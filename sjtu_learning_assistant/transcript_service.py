from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.transcript_pipeline import (
    PIPELINE_VERSION,
    PROMPT_VERSION,
    REDUCE_FALLBACK_WARNING,
    SUMMARY_EMPTY_WARNING,
    TranscriptAIFormatError,
    TranscriptPipeline,
    chunk_cues,
    normalize_cues,
    parse_vtt,
    validate_map,
)
from sjtu_learning_assistant.video_service import parse_remote_source_id

DEFAULT_TRANSCRIPT_ROOT = APP_SUPPORT_DIR / "transcripts" / "v1"
ACTIVE_STATES = frozenset(("queued", "fetching", "saved", "organizing"))
FINAL_STATES = frozenset(("completed", "completed_with_warnings", "partial", "failed", "waiting_for_ai", "waiting_remote", "cancelled", "interrupted"))
LEASE_SECONDS = 900
MANIFEST_SCHEMA_VERSION = 2
ARTIFACT_FILES = {
    "raw_vtt": "raw.vtt",
    "cues": "cues.json",
    "cleaned": "cleaned.md",
    "summary_json": "summary.json",
    "summary": "summary.md",
}
_ID = re.compile(r"^[0-9a-f]{32}$")
_ARTIFACT_ID = re.compile(r"^([0-9a-f]{32}):(raw_vtt|cues|cleaned|summary_json|summary)$")


class TranscriptError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIContext:
    client: Any | None
    model: str
    endpoint_fingerprint: str
    enabled: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process_start_marker(pid: int) -> str | None:
    if type(pid) is not int or pid <= 0:
        return None
    if sys.platform == "win32":
        command = (
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "(Get-Process -Id %d -ErrorAction Stop).StartTime.ToUniversalTime().ToString(" + chr(39) + "o" + chr(39) + ")" % pid,
        )
    else:
        command = ("ps", "-o", "lstart=", "-p", str(pid))
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = " ".join(result.stdout.split())
    return value or None


def _parse_time(value: object) -> datetime | None:
    if type(value) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_error(value: object) -> str:
    text = " ".join(str(value).split())
    text = re.sub(r"/Users/\S+", "[已隐藏]", text)
    text = re.sub(r"(?i)(token|password|secret|api[_-]?key)\s*[=:]\s*\S+", r"\1=[已隐藏]", text)
    return text[:400] or "字幕任务失败，请稍后重试。"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


class TranscriptService:
    def __init__(
        self,
        root: Path = DEFAULT_TRANSCRIPT_ROOT,
        *,
        subtitle_fetcher: Callable[[str], Mapping[str, Any]] | None = None,
        ai_context_provider: Callable[[], AIContext] | None = None,
        pipeline: TranscriptPipeline | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        revealer: Callable[[Path], Any] | None = None,
        autostart_worker: bool = True,
        lease_seconds: int = LEASE_SECONDS,
    ) -> None:
        self.root = Path(root).expanduser()
        self.subtitle_fetcher = subtitle_fetcher
        self.ai_context_provider = ai_context_provider or (lambda: AIContext(None, "", "", False))
        self.pipeline = pipeline or TranscriptPipeline()
        self.sleeper = sleeper
        self.revealer = revealer or self._default_reveal
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lease_seconds = max(30, int(lease_seconds))
        self._owner = dict(
            pid=os.getpid(),
            process_start=_process_start_marker(os.getpid()),
            instance_nonce=uuid.uuid4().hex,
        )
        self._ensure_root()
        self._recover_interrupted()
        if autostart_worker:
            self._thread = threading.Thread(target=self._worker, name="transcript-worker", daemon=True)
            self._thread.start()

    def _ensure_root(self) -> None:
        storage_root = self.root.parent
        storage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if storage_root.is_symlink() or not storage_root.is_dir():
            raise TranscriptError("字幕存储目录不安全。")
        os.chmod(storage_root, 0o700)
        self.root.mkdir(exist_ok=True, mode=0o700)
        if self.root.is_symlink() or not self.root.is_dir():
            raise TranscriptError("字幕存储目录不安全。")
        os.chmod(self.root, 0o700)
        for name in ("videos", "batches"):
            path = self.root / name
            path.mkdir(exist_ok=True, mode=0o700)
            if path.is_symlink() or not path.is_dir():
                raise TranscriptError("字幕存储目录不安全。")
            os.chmod(path, 0o700)

    @contextmanager
    def _batch_lock(self):
        lock_path = self.root / ".batches.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            elif msvcrt is not None:
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            elif msvcrt is not None:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            os.close(descriptor)

    def _lease(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        lease = dict(self._owner)
        lease.update(
            heartbeat_at=now.isoformat(),
            lease_expires_at=(now + timedelta(seconds=self.lease_seconds)).isoformat(),
        )
        return lease

    def _is_self_owner(self, value: object) -> bool:
        return type(value) is dict and all(value.get(key) == expected for key, expected in self._owner.items())

    def _owner_alive(self, value: object) -> bool:
        if type(value) is not dict:
            return False
        expiry = _parse_time(value.get("lease_expires_at"))
        if expiry is None or expiry <= datetime.now(timezone.utc):
            return False
        pid = value.get("pid")
        if type(pid) is not int or pid <= 0:
            return False
        expected_start = value.get("process_start")
        actual_start = _process_start_marker(pid)
        if actual_start is None:
            return False
        if type(expected_start) is not str or not expected_start:
            return False
        return actual_start == expected_start

    def _refresh_owner(self, batch: dict[str, Any]) -> None:
        lease = self._lease()
        batch["owner"] = lease
        for job in batch.get("jobs", list()):
            if job.get("status") in ACTIVE_STATES:
                job["owner"] = dict(lease)


    def _atomic_write(self, path: Path, payload: bytes) -> None:
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink():
            raise TranscriptError("字幕存储路径不安全。")
        os.chmod(parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".transcript-", dir=parent)
        temporary = Path(temporary_name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            if os.name != "nt":
                directory_fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_json(self, path: Path, value: object) -> None:
        self._atomic_write(path, _json_bytes(value))

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            if path.is_symlink() or not path.is_file():
                raise TranscriptError("字幕任务工件不存在或不安全。")
            value = json.loads(path.read_text(encoding="utf-8"))
        except TranscriptError:
            raise
        except Exception as exc:
            raise TranscriptError("字幕任务记录无法读取。") from exc
        if type(value) is not dict:
            raise TranscriptError("字幕任务记录格式无效。")
        return value

    def _batch_path(self, batch_id: str) -> Path:
        if type(batch_id) is not str or _ID.fullmatch(batch_id) is None:
            raise TranscriptError("字幕批次标识不正确。")
        return self.root / "batches" / f"{batch_id}.json"

    def _video_dir(self, source_id: str) -> Path:
        return self.root / "videos" / _sha256(source_id.encode("utf-8"))[:32]

    def _manifest_path(self, source_id: str) -> Path:
        return self._video_dir(source_id) / "manifest.json"

    def _job_video_dir(self, job: Mapping[str, Any]) -> Path:
        source_id = job.get("source_id")
        if type(source_id) is not str:
            raise TranscriptError("字幕任务视频标识不正确。")
        expected = self._video_dir(source_id)
        directory_id = job.get("video_dir") or expected.name
        if type(directory_id) is not str or directory_id != expected.name or _ID.fullmatch(directory_id) is None:
            raise TranscriptError("字幕任务存储路径不安全。")
        path = self.root / "videos" / directory_id
        if path.is_symlink() or not path.is_dir():
            raise TranscriptError("字幕任务存储路径不安全。")
        return path

    def _mark_manifest_interrupted(self, job: Mapping[str, Any]) -> None:
        if type(job.get("video_dir")) is not str:
            return
        try:
            manifest_path = self._job_video_dir(job) / "manifest.json"
            manifest = self._read_json(manifest_path)
        except TranscriptError:
            return
        if manifest.get("status") not in ACTIVE_STATES:
            return
        manifest.update(
            status="interrupted",
            stage="interrupted",
            error="上次运行被中断，可重试并复用已保存工件。",
            updated_at=_now(),
        )
        self._write_json(manifest_path, manifest)

    def _recover_interrupted(self) -> None:
        with self._batch_lock():
            for path in (self.root / "batches").glob("*.json"):
                try:
                    batch = self._read_json(path)
                except TranscriptError:
                    continue
                changed = False
                batch_owner = batch.get("owner")
                for job in batch.get("jobs", list()):
                    if job.get("status") not in ACTIVE_STATES:
                        continue
                    owner = job.get("owner") or batch_owner
                    if self._owner_alive(owner):
                        continue
                    job["status"] = "interrupted"
                    job["stage"] = "interrupted"
                    job["message"] = "上次运行被中断，可重试并复用已保存工件。"
                    self._mark_manifest_interrupted(job)
                    job.pop("owner", None)
                    changed = True
                active_jobs = tuple(job for job in batch.get("jobs", list()) if job.get("status") in ACTIVE_STATES)
                if batch.get("status") in ACTIVE_STATES and not active_jobs:
                    batch["status"] = "interrupted"
                    batch.pop("owner", None)
                    changed = True
                if changed:
                    batch["updated_at"] = _now()
                    self._write_json(path, batch)

    @staticmethod
    def _validate_video(course_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
        source_id = value.get("source_id")
        if type(source_id) is not str:
            raise TranscriptError("视频标识不正确。")
        remote_course_id, _video_id = parse_remote_source_id(source_id)
        if remote_course_id != course_id:
            raise TranscriptError("视频不属于当前课程。")
        title = value.get("title")
        if type(title) is not str or not title.strip() or len(title) > 500:
            raise TranscriptError("视频标题不正确。")
        return {
            "source_id": source_id,
            "title": title.strip(),
            "classroom": str(value.get("classroom") or "")[:120],
            "teaching_class": str(value.get("teaching_class") or "")[:200],
            "recorded_at": str(value.get("recorded_at") or "")[:64],
        }

    def start_batch(self, *, course_id: str | int, course_name: str, videos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        key = str(course_id).strip()
        if not key.isdigit() or not 1 <= len(videos) <= 100:
            raise TranscriptError("课程或视频数量不正确。")
        if type(course_name) is not str or not course_name.strip() or len(course_name) > 300:
            raise TranscriptError("课程名称不正确。")
        clean_videos = [self._validate_video(key, video) for video in videos]
        if len({item["source_id"] for item in clean_videos}) != len(clean_videos):
            raise TranscriptError("视频列表包含重复项目。")
        batch_id = uuid.uuid4().hex
        jobs = [
            {
                "id": uuid.uuid4().hex,
                "batch_id": batch_id,
                "source_id": video["source_id"],
                "title": video["title"],
                "meta": {key: value for key, value in video.items() if key not in {"source_id", "title"}},
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "attempts": 0,
                "message": "等待存储字幕。",
                "reused": False,
                "error": None,
            }
            for video in clean_videos
        ]
        batch = {"schema_version": 1, "id": batch_id, "course_id": key, "course_name": course_name.strip(), "status": "queued", "progress": 0, "created_at": _now(), "updated_at": _now(), "cancel_requested": False, "jobs": jobs}
        self._refresh_owner(batch)
        with self._lock, self._batch_lock():
            self._write_json(self._batch_path(batch_id), batch)
        self._wake.set()
        return self._public_batch(batch)

    def _all_batches(self) -> list[dict[str, Any]]:
        batches = []
        for path in (self.root / "batches").glob("*.json"):
            try:
                batches.append(self._read_json(path))
            except TranscriptError:
                continue
        return sorted(batches, key=lambda item: str(item.get("created_at", "")), reverse=True)

    @staticmethod
    def _public_job(job: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(job)
        result.pop("owner", None)
        result.pop("video_dir", None)
        return result

    def _public_batch(self, batch: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(batch)
        result.pop("cancel_requested", None)
        result.pop("owner", None)
        result.update(jobs=list(map(self._public_job, batch.get("jobs", list()))))
        return result

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public_batch(self._read_json(self._batch_path(batch_id)))

    def jobs(self, course_id: str | int | None = None) -> dict[str, Any]:
        key = None if course_id is None else str(course_id)
        items = [self._public_job(job) for batch in self._all_batches() if key is None or batch.get("course_id") == key for job in batch.get("jobs", [])]
        return {"items": items[:500]}

    def retry(self, job_id: str) -> dict[str, Any]:
        previous_batch, job = self._find_job(job_id)
        video = dict(job.get("meta") or dict())
        video.update(source_id=job["source_id"], title=job["title"])
        manifest_path = self._manifest_path(str(job["source_id"]))
        if manifest_path.is_file() and not manifest_path.is_symlink():
            manifest = self._read_json(manifest_path)
            self._refresh_manifest_versions(manifest, job, self.ai_context_provider())
            self._write_json(manifest_path, manifest)
        return self.start_batch(
            course_id=str(previous_batch["course_id"]),
            course_name=str(previous_batch["course_name"]),
            videos=[video],
        )

    def cancel(self, *, batch_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
        if (batch_id is None) == (job_id is None):
            raise TranscriptError("必须且只能指定一个待取消任务。")
        if job_id is not None:
            batch, job = self._find_job(job_id)
            target_id = str(batch["id"])
            job["cancel_requested"] = True
        else:
            target_id = str(batch_id)
            batch = self._read_json(self._batch_path(target_id))
            batch["cancel_requested"] = True
            for job in batch.get("jobs", []):
                if job.get("status") not in FINAL_STATES:
                    job["cancel_requested"] = True
        batch["updated_at"] = _now()
        self._write_json(self._batch_path(target_id), batch)
        return self._public_batch(batch)

    def _find_job(self, job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if type(job_id) is not str or _ID.fullmatch(job_id) is None:
            raise TranscriptError("字幕任务标识不正确。")
        for batch in self._all_batches():
            for job in batch.get("jobs", []):
                if job.get("id") == job_id:
                    return batch, job
        raise TranscriptError("未找到字幕任务。")

    def _worker(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(1)
            self._wake.clear()
            if self._stop.is_set():
                break
            self.run_pending()

    def _claim_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock, self._batch_lock():
            batch = self._read_json(self._batch_path(batch_id))
            if batch.get("status") != "queued":
                return None
            owner = batch.get("owner")
            if self._owner_alive(owner) and not self._is_self_owner(owner):
                return None
            self._refresh_owner(batch)
            batch["updated_at"] = _now()
            self._write_json(self._batch_path(batch_id), batch)
            return batch

    def run_pending(self) -> None:
        for candidate in reversed(self._all_batches()):
            if candidate.get("status") != "queued":
                continue
            batch_id = candidate.get("id")
            if type(batch_id) is not str:
                continue
            batch = self._claim_batch(batch_id)
            if batch is not None:
                self._process_batch(batch)

    def _save_batch(self, batch: dict[str, Any]) -> None:
        with self._lock:
            jobs = batch.get("jobs", [])
            batch["progress"] = round(sum(int(job.get("progress") or 0) for job in jobs) / max(1, len(jobs)))
            states = {str(job.get("status")) for job in jobs}
            if states and all(state == "cancelled" for state in states):
                batch["status"] = "cancelled"
            elif states and all(state == "completed" for state in states):
                batch["status"] = "completed"
            elif states.issubset(frozenset(("completed", "completed_with_warnings"))):
                batch["status"] = "completed_with_warnings"
            elif any(state == "organizing" for state in states):
                batch["status"] = "organizing"
            elif any(state in {"queued", "fetching", "saved"} for state in states):
                batch["status"] = "fetching"
            elif any(state == "partial" for state in states):
                batch["status"] = "partial"
            elif any(state == "failed" for state in states):
                batch["status"] = "partial" if len(states) > 1 else "failed"
            elif any(state == "interrupted" for state in states):
                batch["status"] = "interrupted"
            elif any(state == "waiting_remote" for state in states):
                batch["status"] = "waiting_remote"
            elif any(state == "waiting_for_ai" for state in states):
                batch["status"] = "waiting_for_ai"
            else:
                batch["status"] = "partial"
            batch["updated_at"] = _now()
            self._refresh_owner(batch)
            with self._batch_lock():
                self._write_json(self._batch_path(str(batch["id"])), batch)

    def _heartbeat_batch(self, batch_id: str) -> bool:
        with self._lock, self._batch_lock():
            try:
                batch = self._read_json(self._batch_path(batch_id))
            except TranscriptError:
                return False
            if batch.get("status") not in ACTIVE_STATES or not self._is_self_owner(batch.get("owner")):
                return False
            self._refresh_owner(batch)
            batch["updated_at"] = _now()
            self._write_json(self._batch_path(batch_id), batch)
            return True

    def _heartbeat_loop(self, batch_id: str, stopped: threading.Event) -> None:
        interval = max(1.0, min(30.0, self.lease_seconds / 3))
        while not stopped.wait(interval):
            if not self._heartbeat_batch(batch_id):
                return

    @contextmanager
    def _lease_heartbeat(self, batch_id: str):
        stopped = threading.Event()
        thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(batch_id, stopped),
            name="transcript-heartbeat",
            daemon=True,
        )
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=2)

    def _process_batch(self, batch: dict[str, Any]) -> None:
        with self._lease_heartbeat(str(batch["id"])):
            self._process_claimed_batch(batch)

    def _process_claimed_batch(self, batch: dict[str, Any]) -> None:
        if batch.get("cancel_requested"):
            for job in batch["jobs"]:
                job["status"] = "cancelled"
                job["progress"] = 100
            self._save_batch(batch)
            return
        batch["status"] = "fetching"
        self._save_batch(batch)
        pending = [job for job in batch["jobs"] if job.get("status") == "queued"]
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="subtitle-fetch") as pool:
            futures = {pool.submit(self._fetch_and_store, batch, job): job for job in pending}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    job["status"] = "failed"
                    job["stage"] = "fetch"
                    job["error"] = _safe_error(exc)
                    job["message"] = "字幕存储失败，可重试。"
                self._save_batch(batch)
        for job in batch["jobs"]:
            if job.get("status") == "saved":
                if batch.get("cancel_requested") or job.get("cancel_requested"):
                    job["status"] = "cancelled"
                    job["progress"] = 100
                else:
                    self._organize(batch, job)
                self._save_batch(batch)
        self._save_batch(batch)

    @staticmethod
    def _model_fingerprint(context: AIContext) -> str:
        return _sha256((context.model + "|" + context.endpoint_fingerprint).encode("utf-8"))

    def _idempotency_key(
        self,
        source_id: object,
        raw_sha256: object,
        context: AIContext,
    ) -> str:
        return _sha256("|".join((
            str(source_id),
            str(raw_sha256 or ""),
            PIPELINE_VERSION,
            PROMPT_VERSION,
            context.model,
            context.endpoint_fingerprint,
        )).encode("utf-8"))

    def _preserve_chunk_context(self, manifest: dict[str, Any]) -> None:
        if not manifest.get("chunks") or type(manifest.get("chunk_context")) is dict:
            return
        pipeline = manifest.get("pipeline")
        ai = manifest.get("ai")
        if type(pipeline) is not dict or type(ai) is not dict:
            manifest["chunk_context"] = dict(invalidated=True)
            return
        model = ai.get("model")
        endpoint = ai.get("endpoint_fingerprint")
        if type(model) is not str or type(endpoint) is not str:
            manifest["chunk_context"] = dict(invalidated=True)
            return
        manifest["chunk_context"] = dict(
            raw_sha256=manifest.get("raw_sha256"),
            pipeline_version=pipeline.get("pipeline_version"),
            prompt_version=pipeline.get("prompt_version"),
            model_fingerprint=ai.get("model_fingerprint") or _sha256((model + "|" + endpoint).encode("utf-8")),
            idempotency_key=manifest.get("idempotency_key"),
        )

    def _current_chunk_context(
        self,
        manifest: Mapping[str, Any],
        job: Mapping[str, Any],
        context: AIContext,
    ) -> dict[str, Any]:
        return dict(
            raw_sha256=manifest.get("raw_sha256"),
            pipeline_version=PIPELINE_VERSION,
            prompt_version=PROMPT_VERSION,
            model_fingerprint=self._model_fingerprint(context),
            idempotency_key=self._idempotency_key(job.get("source_id"), manifest.get("raw_sha256"), context),
        )

    def _chunk_context_matches(
        self,
        manifest: Mapping[str, Any],
        job: Mapping[str, Any],
        context: AIContext,
    ) -> bool:
        value = manifest.get("chunk_context")
        return type(value) is dict and value == self._current_chunk_context(manifest, job, context)

    @staticmethod
    def _hashed_file_valid(path: Path, expected_hash: object) -> bool:
        try:
            return (
                type(expected_hash) is str
                and len(expected_hash) == 64
                and path.is_file()
                and not path.is_symlink()
                and _sha256(path.read_bytes()) == expected_hash
            )
        except OSError:
            return False

    def _validated_cached_chunks(
        self,
        video_dir: Path,
        manifest: Mapping[str, Any],
        job: Mapping[str, Any],
        context: AIContext,
        raw: str,
        require_complete: bool,
    ) -> dict[int, Mapping[str, Any]] | None:
        if not self._chunk_context_matches(manifest, job, context):
            return None
        try:
            cues = normalize_cues(parse_vtt(raw))
            expected_chunks = chunk_cues(cues)
        except ValueError:
            return None
        if _sha256(raw.encode("utf-8")) != manifest.get("raw_sha256"):
            return None
        cues_record = manifest.get("artifacts", dict()).get("cues")
        cues_path = video_dir / "cues.json"
        if (
            type(cues_record) is not dict
            or cues_record.get("path") != "cues.json"
            or not self._hashed_file_valid(cues_path, cues_record.get("sha256"))
        ):
            return None
        try:
            stored_cues = json.loads(cues_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if type(stored_cues) is not list or len(stored_cues) != len(cues):
            return None
        for row, cue in zip(stored_cues, cues):
            if type(row) is not dict:
                return None
            if (
                row.get("start_ms") != cue.start_ms
                or row.get("end_ms") != cue.end_ms
                or row.get("text") != cue.text
                or (row.get("cue_id") is not None and row.get("cue_id") != cue.cue_id)
            ):
                return None
        records = manifest.get("chunks")
        if type(records) is not dict:
            return None
        if require_complete and set(records) != set(str(chunk.index) for chunk in expected_chunks):
            return None
        validated: dict[int, Mapping[str, Any]] = dict()
        for chunk in expected_chunks:
            record = records.get(str(chunk.index))
            if type(record) is not dict:
                if require_complete:
                    return None
                continue
            relative = "chunks/%04d.json" % chunk.index
            path = video_dir / relative
            if record.get("path") != relative or not self._hashed_file_valid(path, record.get("sha256")):
                return None
            try:
                value = self._read_json(path)
                validated[chunk.index] = validate_map(value, chunk.cues)
            except (TranscriptError, TypeError, ValueError):
                return None
        if require_complete and len(validated) != len(expected_chunks):
            return None
        return validated

    def _completed_artifacts_valid(self, video_dir: Path, manifest: Mapping[str, Any]) -> bool:
        records = manifest.get("artifacts")
        if type(records) is not dict:
            return False
        for kind, name in ARTIFACT_FILES.items():
            record = records.get(kind)
            if (
                type(record) is not dict
                or record.get("path") != name
                or not self._hashed_file_valid(video_dir / name, record.get("sha256"))
            ):
                return False
        return True

    def _refresh_manifest_versions(
        self,
        manifest: dict[str, Any],
        job: Mapping[str, Any],
        context: AIContext,
    ) -> None:
        self._preserve_chunk_context(manifest)
        previous_key = manifest.get("idempotency_key")
        if (
            manifest.get("status") in ("completed", "completed_with_warnings")
            and type(previous_key) is str
            and "artifact_idempotency_key" not in manifest
        ):
            manifest["artifact_idempotency_key"] = previous_key
        manifest["ai"] = dict(
            model=context.model,
            endpoint_fingerprint=context.endpoint_fingerprint,
            model_fingerprint=self._model_fingerprint(context),
        )
        manifest["pipeline"] = dict(
            pipeline_version=PIPELINE_VERSION,
            prompt_version=PROMPT_VERSION,
        )
        manifest["idempotency_key"] = self._idempotency_key(
            job.get("source_id"), manifest.get("raw_sha256"), context
        )
        manifest["schema_version"] = MANIFEST_SCHEMA_VERSION
        manifest["updated_at"] = _now()

    def _fetch_and_store(self, batch: dict[str, Any], job: dict[str, Any]) -> None:
        video_dir = self._video_dir(str(job["source_id"]))
        if video_dir.exists() and (video_dir.is_symlink() or not video_dir.is_dir()):
            raise TranscriptError("字幕视频目录不安全。")
        manifest_path = video_dir / "manifest.json"
        if manifest_path.exists() and not manifest_path.is_symlink():
            manifest = self._read_json(manifest_path)
            raw_path = video_dir / "raw.vtt"
            if raw_path.is_symlink():
                raise TranscriptError("字幕工件路径不安全。")
            if (
                manifest.get("source", {}).get("source_id") == job["source_id"]
                and raw_path.is_file()
                and _sha256(raw_path.read_bytes()) == manifest.get("raw_sha256")
            ):
                context = self.ai_context_provider()
                self._refresh_manifest_versions(manifest, job, context)
                expected_key = manifest.get("idempotency_key")
                artifact_key = manifest.get("artifact_idempotency_key")
                can_reuse_complete = (
                    manifest.get("status") in ("completed", "completed_with_warnings")
                    and artifact_key == expected_key
                    and self._completed_artifacts_valid(video_dir, manifest)
                )
                raw = raw_path.read_text(encoding="utf-8")
                offline_chunks = None
                if not can_reuse_complete and manifest.get("status") in ("partial", "interrupted"):
                    offline_chunks = self._validated_cached_chunks(
                        video_dir, manifest, job, context, raw, True
                    )
                self._write_json(manifest_path, manifest)
                job["video_dir"] = video_dir.name
                reused_status = str(manifest.get("status")) if can_reuse_complete else "saved"
                job["status"] = reused_status
                job["stage"] = "reused"
                job["progress"] = 100 if can_reuse_complete else (85 if offline_chunks is not None else 45)
                job["message"] = "已复用相同字幕工件。"
                if reused_status == "completed_with_warnings":
                    job["partial_warning"] = True
                    job["message"] = REDUCE_FALLBACK_WARNING if REDUCE_FALLBACK_WARNING in manifest.get("partial_warnings", list()) else "已复用带提示的字幕工件。"
                job["reused"] = True
                if offline_chunks is not None:
                    job["offline_recovery"] = True
                    job["message"] = "已验证全部分块，正在离线生成最终结果。"
                return
        if self.subtitle_fetcher is None:
            raise TranscriptError("字幕服务不可用。")
        job["status"] = "fetching"
        job["stage"] = "fetch"
        job["progress"] = 10
        result: Mapping[str, Any] | None = None
        for attempt in range(4):
            job["attempts"] = attempt + 1
            try:
                result = self.subtitle_fetcher(str(job["source_id"]))
                break
            except Exception:
                if attempt >= 3:
                    raise TranscriptError("字幕服务暂时不可用，重试后仍失败。") from None
                self.sleeper(min(8.0, 0.25 * (2 ** attempt)))
        if result is None:
            raise TranscriptError("字幕服务未返回结果。")
        status = result.get("status")
        if status == "processing":
            job["status"] = "waiting_remote"
            job["stage"] = "waiting_remote"
            job["progress"] = 10
            job["message"] = "远端字幕仍在生成，请稍后重试。"
            return
        raw = result.get("vtt")
        if status != "ready" or type(raw) is not str or not raw.strip():
            raise TranscriptError(str(result.get("message") or "该录像暂无字幕。"))
        data = raw.encode("utf-8")
        cues_payload = _json_bytes(
            [cue.as_dict() for cue in normalize_cues(parse_vtt(raw))]
        )
        video_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if video_dir.is_symlink() or not video_dir.is_dir():
            raise TranscriptError("字幕视频目录不安全。")
        os.chmod(video_dir, 0o700)
        raw_hash = _sha256(data)
        context = self.ai_context_provider()
        idempotency = _sha256("|".join((str(job["source_id"]), raw_hash, PROMPT_VERSION, context.model, context.endpoint_fingerprint)).encode("utf-8"))
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "source": {"source_id": job["source_id"], "type": "video_space"},
            "course": {"id": batch["course_id"], "name": batch["course_name"]},
            "video": {"title": job["title"], **dict(job.get("meta") or {})},
            "raw_sha256": raw_hash,
            "ai": {"model": context.model, "endpoint_fingerprint": context.endpoint_fingerprint},
            "pipeline": {"prompt_version": PROMPT_VERSION},
            "idempotency_key": idempotency,
            "status": "saved",
            "stage": "raw_saved",
            "progress": 45,
            "retry_count": int(job.get("attempts") or 0) - 1,
            "error": None,
            "artifacts": {},
            "chunks": {},
            "updated_at": _now(),
        }
        self._refresh_manifest_versions(manifest, job, context)
        self._atomic_write(video_dir / "raw.vtt", data)
        self._atomic_write(video_dir / "cues.json", cues_payload)
        (video_dir / "chunks").mkdir(exist_ok=True, mode=0o700)
        os.chmod(video_dir / "chunks", 0o700)
        manifest["artifacts"]["raw_vtt"] = {"path": "raw.vtt", "sha256": raw_hash}
        manifest["artifacts"]["cues"] = {"path": "cues.json", "sha256": _sha256(cues_payload)}
        self._write_json(video_dir / "manifest.json", manifest)
        job["video_dir"] = video_dir.name
        job["status"] = "saved"
        job["stage"] = "raw_saved"
        job["progress"] = 45
        job["message"] = "原始字幕已安全存储。"

    def _organize(self, batch: dict[str, Any], job: dict[str, Any]) -> None:
        video_dir = self._job_video_dir(job)
        manifest_path = video_dir / "manifest.json"
        manifest = self._read_json(manifest_path)
        context = self.ai_context_provider()
        self._refresh_manifest_versions(manifest, job, context)
        raw_path = video_dir / "raw.vtt"
        if not self._hashed_file_valid(raw_path, manifest.get("raw_sha256")):
            raise TranscriptError("原始字幕哈希校验失败。")
        raw = raw_path.read_text(encoding="utf-8")
        offline_recovery = bool(job.pop("offline_recovery", False))
        cached = self._validated_cached_chunks(
            video_dir, manifest, job, context, raw, offline_recovery
        )
        if offline_recovery and cached is None:
            offline_recovery = False
        if cached is None:
            cached = dict()
            manifest["chunks"] = dict()
            manifest["chunk_context"] = self._current_chunk_context(manifest, job, context)
        if not context.enabled or context.client is None:
            if not offline_recovery:
                job["status"] = "waiting_for_ai"
                job["stage"] = "waiting_for_ai"
                job["progress"] = 45
                job["message"] = "原始字幕已保存；启用 AI 后可继续规整。"
                manifest.update(dict(status="waiting_for_ai", stage="waiting_for_ai", progress=45, updated_at=_now()))
                self._write_json(manifest_path, manifest)
                return
        job["status"] = "organizing"
        job["stage"] = "offline_reduce" if offline_recovery else "map_reduce"
        job["progress"] = 85 if offline_recovery else 55
        cached = cached or dict()
        chunks_dir = video_dir / "chunks"
        chunks_dir.mkdir(exist_ok=True, mode=0o700)
        if chunks_dir.is_symlink() or not chunks_dir.is_dir():
            raise TranscriptError("字幕分块目录不安全。")
        os.chmod(chunks_dir, 0o700)
        def on_chunk(index: int, value: dict[str, Any]) -> None:
            payload = _json_bytes(value)
            name = f"{index:04d}.json"
            self._atomic_write(chunks_dir / name, payload)
            cached[index] = value
            manifest.setdefault("chunks", {})
            manifest["chunks"][str(index)] = {"path": f"chunks/{name}", "sha256": _sha256(payload)}
            manifest["chunk_context"] = self._current_chunk_context(manifest, job, context)
            manifest["stage"] = "map"
            manifest["progress"] = min(84, 55 + len(cached) * 3)
            self._write_json(manifest_path, manifest)
            self._save_batch(batch)
        def on_cleaned(markdown: str) -> None:
            payload = markdown.encode("utf-8")
            self._atomic_write(video_dir / "cleaned.md", payload)
            manifest.setdefault("artifacts", dict())
            manifest["artifacts"]["cleaned"] = dict(path="cleaned.md", sha256=_sha256(payload))
            manifest["stage"] = "cleaned_saved"
            manifest["progress"] = max(85, int(manifest.get("progress") or 0))
            manifest["updated_at"] = _now()
            self._write_json(manifest_path, manifest)
            self._save_batch(batch)
        try:
            run_options = dict(
                cached_chunks=cached,
                on_chunk=on_chunk,
                on_cleaned=on_cleaned,
            )
            if offline_recovery:
                run_options["offline_only"] = True
            result = self.pipeline.run(raw, context.client, **run_options)
            artifacts = {
                "cues": ("cues.json", _json_bytes(result.cues)),
                "summary_json": ("summary.json", _json_bytes(result.summary)),
                "summary": ("summary.md", result.summary_markdown.encode("utf-8")),
            }
            for kind, (name, payload) in artifacts.items():
                self._atomic_write(video_dir / name, payload)
                manifest["artifacts"][kind] = {"path": name, "sha256": _sha256(payload)}
            manifest["artifact_idempotency_key"] = manifest.get("idempotency_key")
            if getattr(result, "summary_empty", False):
                manifest.pop("ai_error", None)
                manifest.update(dict(status="partial", stage="summary_empty", progress=100, error=None, partial_warnings=list(result.partial_warnings), updated_at=_now()))
                job.update(dict(status="partial", stage="summary_empty", progress=100, message=SUMMARY_EMPTY_WARNING, error=None, partial_warning=True))
            elif result.partial_warnings:
                manifest.pop("ai_error", None)
                manifest.update(dict(status="completed_with_warnings", stage="completed_with_warnings", progress=100, error=None, partial_warnings=list(result.partial_warnings), updated_at=_now()))
                reduce_diagnostics = getattr(result, "reduce_diagnostics", None)
                if reduce_diagnostics:
                    warning_category = str(reduce_diagnostics.get("category") or "reduce_error")
                    manifest["ai_warning"] = dict(category=warning_category, diagnostics=dict(reduce_diagnostics))
                message = REDUCE_FALLBACK_WARNING if REDUCE_FALLBACK_WARNING in result.partial_warnings else "字幕与经验证的本节要点已生成；部分分块使用原字幕回退。"
                job.update(dict(status="completed_with_warnings", stage="completed_with_warnings", progress=100, message=message, error=None, partial_warning=True))
            else:
                manifest.pop("partial_warnings", None)
                manifest.pop("ai_warning", None)
                manifest.pop("ai_error", None)
                manifest.update(dict(status="completed", stage="completed", progress=100, error=None, updated_at=_now()))
                job.update(dict(status="completed", stage="completed", progress=100, message="字幕已存储并完成 AI 规整。", error=None))
        except Exception as exc:
            manifest.update(dict(status="partial", stage="ai_failed", progress=max(55, int(manifest.get("progress") or 55)), error=_safe_error(exc), updated_at=_now()))
            if isinstance(exc, TranscriptAIFormatError):
                manifest["ai_error"] = dict(category=exc.category, diagnostics=exc.diagnostics)
            job.update(dict(status="partial", stage="ai_failed", progress=manifest["progress"], message="原始字幕已保存；AI 规整失败，可重试。", error=_safe_error(exc)))
        self._write_json(manifest_path, manifest)

    def list_artifacts(self, job_id: str) -> dict[str, Any]:
        _batch, job = self._find_job(job_id)
        video_dir = self._job_video_dir(job)
        manifest = self._read_json(video_dir / "manifest.json")
        items = []
        for kind, record in manifest.get("artifacts", {}).items():
            if kind not in ARTIFACT_FILES or type(record) is not dict:
                continue
            path = video_dir / str(record.get("path"))
            if path.is_file() and not path.is_symlink():
                items.append({"id": f"{job_id}:{kind}", "kind": kind, "label": {"raw_vtt": "原始字幕", "cues": "Cue 数据", "cleaned": "规整字幕", "summary_json": "要点数据", "summary": "本节要点"}[kind], "content_type": "application/json" if kind in {"cues", "summary_json"} else "text/markdown" if kind in {"cleaned", "summary"} else "text/vtt"})
        return {"items": items}

    def _resolve_artifact(self, artifact_id: str) -> tuple[Path, str]:
        if type(artifact_id) is not str:
            raise TranscriptError("工件标识不正确。")
        match = _ARTIFACT_ID.fullmatch(artifact_id)
        if match is None:
            raise TranscriptError("工件标识不正确。")
        job_id, kind = match.groups()
        _batch, job = self._find_job(job_id)
        video_dir = self._job_video_dir(job)
        manifest = self._read_json(video_dir / "manifest.json")
        record = manifest.get("artifacts", {}).get(kind)
        if type(record) is not dict or record.get("path") != ARTIFACT_FILES[kind]:
            raise TranscriptError("字幕工件不存在。")
        path = video_dir / ARTIFACT_FILES[kind]
        try:
            root = self.root.resolve(strict=True)
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            mode = path.lstat().st_mode
        except (OSError, ValueError):
            raise TranscriptError("字幕工件路径不安全。") from None
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise TranscriptError("字幕工件路径不安全。")
        return path, kind

    def read_artifact(self, artifact_id: str) -> dict[str, Any]:
        path, kind = self._resolve_artifact(artifact_id)
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise TranscriptError("字幕工件无法读取。") from exc
        if len(content) > 8_000_000:
            raise TranscriptError("字幕工件超过预览限制。")
        return {"id": artifact_id, "kind": kind, "content": content}

    def reveal_artifact(self, artifact_id: str) -> dict[str, str]:
        path, _kind = self._resolve_artifact(artifact_id)
        self.revealer(path)
        return {"id": artifact_id, "status": "revealed"}

    @staticmethod
    def _default_reveal(path: Path) -> None:
        subprocess.run(("/usr/bin/open", "-R", "--", str(path)), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
