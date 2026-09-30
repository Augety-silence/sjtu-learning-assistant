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

from sjtu_learning_assistant.course_learning_orchestrator import (
    PIPELINE_VERSION as COURSE_PIPELINE_VERSION,
    PROMPT_VERSION as COURSE_PROMPT_VERSION,
    CourseLearningOrchestrator,
    OpenAIClassificationSemanticAdapter,
)
from sjtu_learning_assistant.course_learning_schemas import (
    CriticDecision,
    OrchestratorResult,
)
from sjtu_learning_assistant.course_memory import (
    CourseMemoryStore,
    TrainingSampleQualityGate,
)
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
DEFAULT_COURSE_MEMORY_ROOT = APP_SUPPORT_DIR / "transcripts" / "v2" / "course_memory"
ACTIVE_STATES = frozenset(("queued", "fetching", "saved", "organizing"))
FINAL_STATES = frozenset(("completed", "completed_with_warnings", "partial", "failed", "waiting_for_ai", "waiting_remote", "cancelled", "interrupted"))
LEASE_SECONDS = 900
MANIFEST_SCHEMA_VERSION = 2
V2_MANIFEST_SCHEMA_VERSION = 1
V2_JSON_PREVIEW_LIMIT = 2 * 1024 * 1024
V2_TEXT_PREVIEW_LIMIT = 8 * 1024 * 1024
ARTIFACT_FILES = {
    "raw_vtt": "raw.vtt",
    "cues": "cues.json",
    "cleaned": "cleaned.md",
    "summary_json": "summary.json",
    "summary": "summary.md",
}
V2_ARTIFACT_FILES = {
    "semantic_chunks": "semantic_chunks.json",
    "corrected_json": "corrected.json",
    "corrected": "corrected.md",
    "correction_diff": "correction_diff.json",
    "uncertain": "uncertain.json",
    "quality": "quality.json",
    "course_memory_version": "course_memory_version.json",
    "pipeline_events": "pipeline_events.json",
    "training_examples": "training_examples.jsonl",
}
V2_ARTIFACT_LABELS = {
    "semantic_chunks": "语义分块",
    "corrected_json": "纠错数据",
    "corrected": "纠错字幕",
    "correction_diff": "纠错差异",
    "uncertain": "待确认片段",
    "quality": "质量报告",
    "course_memory_version": "课程记忆版本",
    "pipeline_events": "流水线事件",
    "training_examples": "合格训练样本",
}
V2_JSON_KINDS = frozenset(set(V2_ARTIFACT_FILES) - {"corrected", "training_examples"})
_ID = re.compile(r"^[0-9a-f]{32}$")
_OPAQUE_ID = re.compile(r"^[0-9a-f]{32}$")
_ARTIFACT_ID = re.compile(r"^([0-9a-f]{32}):(raw_vtt|cues|cleaned|summary_json|summary)$")
_V2_ARTIFACT_ID = re.compile(r"^([0-9a-f]{32}):v2:([0-9a-f]{32})$")
_PHASE1_THREAD_LOCKS: dict[str, threading.RLock] = {}
_PHASE1_THREAD_LOCKS_GUARD = threading.Lock()


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
        orchestrator: Any | None = None,
        memory_root: Path = DEFAULT_COURSE_MEMORY_ROOT,
        memory_store_factory: Callable[[Path, str], Any] | None = None,
        enable_phase1: bool | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        revealer: Callable[[Path], Any] | None = None,
        autostart_worker: bool = True,
        lease_seconds: int = LEASE_SECONDS,
    ) -> None:
        self.root = Path(root).expanduser()
        self.enable_phase1 = (
            self.root == DEFAULT_TRANSCRIPT_ROOT or orchestrator is not None
            if enable_phase1 is None
            else bool(enable_phase1)
        )
        self.subtitle_fetcher = subtitle_fetcher
        self.ai_context_provider = ai_context_provider or (lambda: AIContext(None, "", "", False))
        self.pipeline = pipeline or TranscriptPipeline()
        self.orchestrator = orchestrator
        if self.orchestrator is not None and hasattr(self.orchestrator, "persist_raw"):
            self.orchestrator.persist_raw = False
        self.memory_root = Path(memory_root).expanduser()
        self.memory_store_factory = memory_store_factory or (
            lambda root, course_id: CourseMemoryStore(root, course_id)
        )
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
        with self._lock, self._batch_lock():
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

    @staticmethod
    def _merge_cancel_requests(
        batch: dict[str, Any], persisted: Mapping[str, Any]
    ) -> None:
        if persisted.get("cancel_requested"):
            batch["cancel_requested"] = True
        persisted_jobs = {
            str(item.get("id")): item
            for item in persisted.get("jobs", [])
            if isinstance(item, Mapping)
        }
        for job in batch.get("jobs", []):
            persisted_job = persisted_jobs.get(str(job.get("id")))
            if persisted_job is not None and persisted_job.get("cancel_requested"):
                job["cancel_requested"] = True

    def _refresh_cancel_requested(
        self, batch: dict[str, Any], job: dict[str, Any]
    ) -> bool:
        with self._lock, self._batch_lock():
            persisted = self._read_json(self._batch_path(str(batch["id"])))
            self._merge_cancel_requests(batch, persisted)
        return bool(batch.get("cancel_requested") or job.get("cancel_requested"))

    def _save_batch(self, batch: dict[str, Any]) -> None:
        with self._lock, self._batch_lock():
            path = self._batch_path(str(batch["id"]))
            if path.is_file() and not path.is_symlink():
                self._merge_cancel_requests(batch, self._read_json(path))
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
            self._write_json(path, batch)

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
                if self._refresh_cancel_requested(batch, job):
                    job["status"] = "cancelled"
                    job["progress"] = 100
                else:
                    self._organize(batch, job)
                self._save_batch(batch)
        for job in batch["jobs"]:
            if job.get("status") not in {"completed", "completed_with_warnings"}:
                continue
            video_dir = self._job_video_dir(job)
            manifest = self._read_json(video_dir / "manifest.json")
            if not self._completed_artifacts_valid(video_dir, manifest):
                continue
            if self._refresh_cancel_requested(batch, job):
                job["status"] = "cancelled"
                job["stage"] = "cancelled"
                job["progress"] = 100
                job["message"] = "字幕任务已取消；已生成的旧版字幕仍可使用。"
            else:
                raw = (video_dir / "raw.vtt").read_text(encoding="utf-8")
                self._run_phase1_safely(
                    batch, job, video_dir, raw, self.ai_context_provider()
                )
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
    def _hashed_file_valid(path: Path, expected_hash: object, expected_size: object = None) -> bool:
        try:
            return (
                type(expected_hash) is str
                and len(expected_hash) == 64
                and (expected_size is None or (type(expected_size) is int and expected_size >= 0))
                and path.is_file()
                and not path.is_symlink()
                and (expected_size is None or path.stat().st_size == expected_size)
                and _sha256(path.read_bytes()) == expected_hash
            )
        except OSError:
            return False

    @staticmethod
    def _phase1_value(result: object, name: str, default: object = None) -> object:
        if isinstance(result, Mapping):
            return result.get(name, default)
        return getattr(result, name, default)

    @staticmethod
    def _phase1_plain(value: object) -> object:
        converter = getattr(value, "to_dict", None)
        if callable(converter):
            return converter()
        if isinstance(value, Mapping):
            return {str(key): TranscriptService._phase1_plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [TranscriptService._phase1_plain(item) for item in value]
        if hasattr(value, "value") and type(getattr(value, "value")) is str:
            return getattr(value, "value")
        return value

    @staticmethod
    def _phase1_markdown(result: object) -> str:
        text = str(TranscriptService._phase1_value(result, "corrected_transcript", "") or "")
        return "# 课程语境纠错字幕\n\n" + text.rstrip() + "\n"

    @staticmethod
    def _phase1_versions(orchestrator: object) -> tuple[str, str, str]:
        return (
            str(getattr(orchestrator, "pipeline_version", COURSE_PIPELINE_VERSION))[:256],
            str(getattr(orchestrator, "prompt_version", COURSE_PROMPT_VERSION))[:256],
            str(getattr(orchestrator, "model", "unknown"))[:256],
        )

    @staticmethod
    def _phase1_seed_revision(orchestrator: object) -> str:
        status = getattr(orchestrator, "seed_glossary_status", None)
        if isinstance(status, Mapping):
            revision = status.get("revision")
            if type(revision) is str and revision:
                return revision[:1024]
        return "unavailable"

    @staticmethod
    def _phase1_cache_key(
        raw_hash: str,
        memory_version: str | None,
        pipeline_version: str,
        prompt_version: str,
        model_version: str,
        seed_glossary_revision: str,
    ) -> str:
        value = {
            "raw_hash": raw_hash,
            "course_memory_version": memory_version,
            "pipeline_version": pipeline_version,
            "prompt_version": prompt_version,
            "model_version": model_version,
            "seed_glossary_revision": seed_glossary_revision,
        }
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return _sha256(payload)

    @staticmethod
    def _safe_v2_record(record: object, kind: str) -> bool:
        if type(record) is not dict or set(record) != {"id", "path", "sha256", "size"}:
            return False
        return bool(
            record.get("path") == V2_ARTIFACT_FILES[kind]
            and type(record.get("id")) is str
            and _OPAQUE_ID.fullmatch(str(record.get("id")))
            and type(record.get("sha256")) is str
            and len(str(record.get("sha256"))) == 64
            and type(record.get("size")) is int
            and int(record.get("size")) >= 0
        )

    def _v2_dir(self, video_dir: Path) -> Path:
        return video_dir / "v2"

    def _v2_manifest(self, video_dir: Path) -> dict[str, Any] | None:
        path = self._v2_dir(video_dir) / "manifest.json"
        if not path.exists() or path.is_symlink():
            return None
        try:
            manifest = self._read_json(path)
        except TranscriptError:
            return None
        return manifest if manifest.get("schema_version") == V2_MANIFEST_SCHEMA_VERSION else None

    def _v2_artifacts_valid(
        self,
        video_dir: Path,
        manifest: Mapping[str, Any],
        *,
        expected_cache_key: str | None = None,
    ) -> bool:
        if expected_cache_key is not None and manifest.get("cache_key") != expected_cache_key:
            return False
        records = manifest.get("artifacts")
        if type(records) is not dict:
            return False
        required = set(V2_ARTIFACT_FILES) - {"training_examples"}
        if not required.issubset(records) or set(records) - set(V2_ARTIFACT_FILES):
            return False
        v2_dir = self._v2_dir(video_dir)
        if v2_dir.is_symlink() or not v2_dir.is_dir():
            return False
        for kind, record in records.items():
            if kind not in V2_ARTIFACT_FILES or not self._safe_v2_record(record, kind):
                return False
            if not self._hashed_file_valid(
                v2_dir / V2_ARTIFACT_FILES[kind], record.get("sha256"), record.get("size")
            ):
                return False
        return True

    def _phase1_training_examples(self, result: object) -> list[dict[str, Any]]:
        chunks = list(self._phase1_value(result, "chunks", ()) or ())
        corrections = list(self._phase1_value(result, "corrections", ()) or ())
        critics = list(self._phase1_value(result, "critics", ()) or ())
        gate = TrainingSampleQualityGate()
        rows: list[dict[str, Any]] = []
        for chunk, correction, critic in zip(chunks, corrections, critics):
            decision = self._phase1_value(critic, "decision")
            decision_value = getattr(decision, "value", decision)
            critic_confidence = self._phase1_value(critic, "confidence", 0.0)
            correction_confidence = self._phase1_value(correction, "confidence", 0.0)
            changes = list(self._phase1_value(correction, "changes", ()) or ())
            source = str(self._phase1_value(chunk, "current_text", "") or "")
            corrected = str(self._phase1_value(correction, "corrected_text", "") or "")
            evidence = list(dict.fromkeys(
                str(item)
                for change in changes
                if float(self._phase1_value(change, "confidence", 0.0) or 0.0) >= 0.9
                for item in (self._phase1_value(change, "evidence", ()) or ())
                if type(item) is str and item
            ))
            if (
                decision_value != CriticDecision.PASS.value
                or type(critic_confidence) not in (int, float)
                or type(correction_confidence) not in (int, float)
                or min(float(critic_confidence), float(correction_confidence)) < 0.9
                or source == corrected
                or not evidence
            ):
                continue
            sample = {
                "sample_type": "sft",
                "task_type": "subtitle_correction",
                "system": "只做有证据的最小必要字幕纠错",
                "input": source,
                "output": corrected,
                "chosen": None,
                "rejected": None,
            }
            confidence = min(float(critic_confidence), float(correction_confidence))
            try:
                checked = gate.validate(
                    sample,
                    source="high_confidence_correction",
                    confidence=confidence,
                    verification="LIKELY",
                    evidence=evidence,
                )
            except Exception:
                continue
            rows.append({
                "id": _sha256((source + "\0" + corrected).encode("utf-8"))[:32],
                "sample": checked,
                "source": "high_confidence_correction",
                "confidence": confidence,
                "verification": "LIKELY",
                "evidence": evidence,
            })
        return rows

    def _phase1_payloads(self, result: object) -> dict[str, bytes]:
        chunks = self._phase1_plain(self._phase1_value(result, "chunks", ()))
        corrections = self._phase1_plain(self._phase1_value(result, "corrections", ()))
        quality = self._phase1_plain(self._phase1_value(result, "quality", {}))
        uncertain = self._phase1_plain(self._phase1_value(result, "uncertain", ()))
        events = self._phase1_plain(self._phase1_value(result, "events", ()))
        memory_version = self._phase1_value(result, "memory_version")
        changes = []
        for correction in list(self._phase1_value(result, "corrections", ()) or ()):
            changes.extend(self._phase1_plain(self._phase1_value(correction, "changes", ())) or [])
        payloads = {
            "semantic_chunks": _json_bytes(chunks),
            "corrected_json": _json_bytes({
                "status": self._phase1_value(result, "status", "completed"),
                "corrected_transcript": self._phase1_value(result, "corrected_transcript", ""),
                "corrections": corrections,
            }),
            "corrected": self._phase1_markdown(result).encode("utf-8"),
            "correction_diff": _json_bytes(changes),
            "uncertain": _json_bytes(uncertain),
            "quality": _json_bytes(quality),
            "course_memory_version": _json_bytes({"version": memory_version}),
            "pipeline_events": _json_bytes(events),
        }
        training = self._phase1_training_examples(result)
        if training:
            payloads["training_examples"] = b"".join(
                (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                for row in training
            )
        return payloads

    def _phase1_orchestrator(self, context: AIContext) -> Any:
        if self.orchestrator is not None:
            return self.orchestrator
        if not context.enabled or context.client is None:
            raise TranscriptError("Phase1 未运行：AI 未配置。")
        adapter = OpenAIClassificationSemanticAdapter(context.client)
        adapter.model = context.model or adapter.model
        return CourseLearningOrchestrator(adapter, persist_raw=False)

    def _run_phase1(
        self,
        batch: Mapping[str, Any],
        job: dict[str, Any],
        video_dir: Path,
        raw: str,
        context: AIContext,
    ) -> None:
        raw_path = video_dir / "raw.vtt"
        cues_path = video_dir / "cues.json"
        raw_hash = _sha256(raw.encode("utf-8"))
        if not self._hashed_file_valid(raw_path, raw_hash) or cues_path.is_symlink() or not cues_path.is_file():
            raise TranscriptError("Phase1 输入工件校验失败。")
        course_id = str(batch.get("course_id") or "")
        _remote_course_id, video_id = parse_remote_source_id(str(job.get("source_id")))
        v2_dir = self._v2_dir(video_dir)
        if v2_dir.exists() and (v2_dir.is_symlink() or not v2_dir.is_dir()):
            raise TranscriptError("Phase1 工件目录不安全。")
        memory_repo = self.memory_store_factory(self.memory_root, course_id)
        current_memory_version = getattr(memory_repo, "current_version", None)
        orchestrator = self._phase1_orchestrator(context)
        pipeline_version, prompt_version, model_version = self._phase1_versions(orchestrator)
        seed_glossary_revision = self._phase1_seed_revision(orchestrator)
        expected_key = self._phase1_cache_key(
            raw_hash,
            current_memory_version,
            pipeline_version,
            prompt_version,
            model_version,
            seed_glossary_revision,
        )
        cached_manifest = self._v2_manifest(video_dir)
        if (
            cached_manifest is not None
            and cached_manifest.get("status") in {"completed", "completed_with_warnings"}
            and cached_manifest.get("source_id") == str(job.get("source_id"))
            and cached_manifest.get("raw_sha256") == raw_hash
            and cached_manifest.get("course_id") == course_id
            and cached_manifest.get("video_id") == video_id
            and cached_manifest.get("course_memory_version") == current_memory_version
            and cached_manifest.get("seed_glossary_revision") == seed_glossary_revision
            and self._v2_artifacts_valid(video_dir, cached_manifest, expected_cache_key=expected_key)
        ):
            job.update(
                phase1_status=str(cached_manifest.get("status") or "completed"),
                pipeline_status=str(cached_manifest.get("status") or "completed"),
                quality=cached_manifest.get("quality"),
                phase1_reused=True,
            )
            return
        result = orchestrator.orchestrate(
            course_id=course_id,
            video_id=video_id,
            raw_vtt=raw,
            memory_repo=memory_repo,
        )
        if isinstance(result, Mapping):
            result = OrchestratorResult.from_dict(dict(result))
        result_raw_hash = self._phase1_value(result, "raw_hash", raw_hash)
        if result_raw_hash and result_raw_hash != raw_hash:
            raise TranscriptError("Phase1 原始字幕哈希不一致。")
        output_memory_version = self._phase1_value(result, "memory_version", current_memory_version)
        result_cache_key = self._phase1_cache_key(
            raw_hash,
            output_memory_version if type(output_memory_version) is str else None,
            pipeline_version,
            prompt_version,
            model_version,
            seed_glossary_revision,
        )
        payloads = self._phase1_payloads(result)
        v2_dir = self._v2_dir(video_dir)
        if v2_dir.exists() and (v2_dir.is_symlink() or not v2_dir.is_dir()):
            raise TranscriptError("Phase1 工件目录不安全。")
        v2_dir.mkdir(exist_ok=True, mode=0o700)
        os.chmod(v2_dir, 0o700)
        records: dict[str, dict[str, Any]] = {}
        for kind, payload in payloads.items():
            name = V2_ARTIFACT_FILES[kind]
            path = v2_dir / name
            if path.exists() and path.is_symlink():
                raise TranscriptError("Phase1 工件路径不安全。")
            self._atomic_write(path, payload)
            records[kind] = {
                "id": uuid.uuid4().hex,
                "path": name,
                "sha256": _sha256(payload),
                "size": len(payload),
            }
        stale_training = v2_dir / V2_ARTIFACT_FILES["training_examples"]
        if "training_examples" not in records and stale_training.exists():
            if stale_training.is_symlink():
                raise TranscriptError("Phase1 工件路径不安全。")
            stale_training.unlink()
        status = str(self._phase1_value(result, "status", "completed"))
        quality = self._phase1_plain(self._phase1_value(result, "quality", {}))
        manifest = {
            "schema_version": V2_MANIFEST_SCHEMA_VERSION,
            "source_id": str(job.get("source_id")),
            "course_id": course_id,
            "video_id": video_id,
            "raw_sha256": raw_hash,
            "course_memory_version": output_memory_version,
            "pipeline_version": pipeline_version,
            "prompt_version": prompt_version,
            "model_version": model_version,
            "seed_glossary_revision": seed_glossary_revision,
            "cache_key": result_cache_key,
            "status": status,
            "quality": quality,
            "warnings": self._phase1_plain(self._phase1_value(result, "warnings", ())),
            "artifacts": records,
            "updated_at": _now(),
        }
        self._write_json(v2_dir / "manifest.json", manifest)
        job.update(
            phase1_status=status,
            pipeline_status=status,
            quality=quality,
            phase1_reused=False,
        )
        if status != "completed":
            job["partial_warning"] = True
            job["message"] = str(job.get("message") or "") + " Phase1 已降级，请查看质量报告。"

    @contextmanager
    def _phase1_course_lock(self, course_id: str):
        lock_root = self.memory_root / ".phase1-locks"
        lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if lock_root.is_symlink() or not lock_root.is_dir():
            raise TranscriptError("Phase1 锁目录不安全。")
        os.chmod(lock_root, 0o700)
        lock_path = lock_root / f"{_sha256(course_id.encode('utf-8'))}.lock"
        key = str(lock_path.resolve())
        with _PHASE1_THREAD_LOCKS_GUARD:
            thread_lock = _PHASE1_THREAD_LOCKS.setdefault(key, threading.RLock())
        with thread_lock:
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

    def _run_phase1_safely(
        self,
        batch: Mapping[str, Any],
        job: dict[str, Any],
        video_dir: Path,
        raw: str,
        context: AIContext,
    ) -> None:
        if not self.enable_phase1:
            return
        try:
            course_id = str(batch.get("course_id") or "")
            with self._phase1_course_lock(course_id):
                self._run_phase1(batch, job, video_dir, raw, context)
        except Exception as exc:
            warning = _safe_error(exc)
            job.update(
                phase1_status="partial",
                pipeline_status="partial",
                phase1_reused=False,
                phase1_warning=warning,
                partial_warning=True,
            )
            job["message"] = str(job.get("message") or "") + " Phase1 未完成，旧版字幕与摘要仍可用。"
            v2_dir = self._v2_dir(video_dir)
            if not v2_dir.exists():
                v2_dir.mkdir(exist_ok=True, mode=0o700)
                os.chmod(v2_dir, 0o700)
            if not v2_dir.is_symlink() and v2_dir.is_dir():
                self._write_json(v2_dir / "manifest.json", {
                    "schema_version": V2_MANIFEST_SCHEMA_VERSION,
                    "source_id": str(job.get("source_id")),
                    "course_id": str(batch.get("course_id") or ""),
                    "video_id": parse_remote_source_id(str(job.get("source_id")))[1],
                    "raw_sha256": _sha256(raw.encode("utf-8")),
                    "course_memory_version": None,
                    "pipeline_version": COURSE_PIPELINE_VERSION,
                    "prompt_version": COURSE_PROMPT_VERSION,
                    "model_version": context.model,
                    "seed_glossary_revision": self._phase1_seed_revision(
                        self.orchestrator
                    )
                    if self.orchestrator is not None
                    else "unavailable",
                    "cache_key": None,
                    "status": "partial",
                    "quality": None,
                    "warnings": [warning],
                    "artifacts": {},
                    "updated_at": _now(),
                })

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

    def _v2_manifest_matches_v1(
        self,
        batch: Mapping[str, Any],
        job: Mapping[str, Any],
        video_dir: Path,
        manifest: Mapping[str, Any],
    ) -> bool:
        try:
            v1_manifest = self._read_json(video_dir / "manifest.json")
            source_id = str(job.get("source_id") or "")
            remote_course_id, video_id = parse_remote_source_id(source_id)
        except (TranscriptError, ValueError):
            return False
        raw_hash = v1_manifest.get("raw_sha256")
        v1_source = v1_manifest.get("source")
        v1_course = v1_manifest.get("course")
        return bool(
            type(raw_hash) is str
            and isinstance(v1_source, Mapping)
            and v1_source.get("source_id") == source_id
            and isinstance(v1_course, Mapping)
            and str(v1_course.get("id") or "") == str(batch.get("course_id") or "")
            and remote_course_id == str(batch.get("course_id") or "")
            and manifest.get("source_id") == source_id
            and manifest.get("course_id") == remote_course_id
            and manifest.get("video_id") == video_id
            and manifest.get("raw_sha256") == raw_hash
            and self._hashed_file_valid(video_dir / "raw.vtt", raw_hash)
        )

    def _v2_artifact_items(
        self,
        job_id: str,
        batch: Mapping[str, Any],
        job: Mapping[str, Any],
        video_dir: Path,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        manifest = self._v2_manifest(video_dir)
        if manifest is not None and not self._v2_manifest_matches_v1(
            batch, job, video_dir, manifest
        ):
            manifest = None
        records = manifest.get("artifacts") if type(manifest) is dict else {}
        if type(records) is not dict:
            records = {}
        items: list[dict[str, Any]] = []
        for kind, name in V2_ARTIFACT_FILES.items():
            record = records.get(kind)
            available = bool(
                self._safe_v2_record(record, kind)
                and self._hashed_file_valid(
                    self._v2_dir(video_dir) / name,
                    record.get("sha256"),
                    record.get("size"),
                )
            )
            item: dict[str, Any] = {
                "id": f"{job_id}:v2:{record.get('id')}" if available else None,
                "kind": kind,
                "label": V2_ARTIFACT_LABELS[kind],
                "content_type": (
                    "application/x-ndjson"
                    if kind == "training_examples"
                    else "text/markdown"
                    if kind == "corrected"
                    else "application/json"
                ),
                "available": available,
                "version": "v2",
            }
            if available:
                item["size"] = int(record.get("size"))
                item["sha256"] = str(record.get("sha256"))
            items.append(item)
        return items, manifest

    def list_v2_artifacts(self, job_id: str) -> dict[str, Any]:
        batch, job = self._find_job(job_id)
        video_dir = self._job_video_dir(job)
        items, manifest = self._v2_artifact_items(job_id, batch, job, video_dir)
        available = bool(manifest is not None and any(item["available"] for item in items))
        return {
            "available": available,
            "status": manifest.get("status") if manifest is not None else None,
            "pipeline_status": manifest.get("status") if manifest is not None else None,
            "quality": manifest.get("quality") if manifest is not None else None,
            "warnings": list(manifest.get("warnings") or []) if manifest is not None else [],
            "items": items,
        }

    def list_artifacts(self, job_id: str) -> dict[str, Any]:
        batch, job = self._find_job(job_id)
        video_dir = self._job_video_dir(job)
        manifest = self._read_json(video_dir / "manifest.json")
        items = []
        for kind, record in manifest.get("artifacts", {}).items():
            if kind not in ARTIFACT_FILES or type(record) is not dict:
                continue
            path = video_dir / str(record.get("path"))
            if path.is_file() and not path.is_symlink():
                items.append({"id": f"{job_id}:{kind}", "kind": kind, "label": {"raw_vtt": "原始字幕", "cues": "Cue 数据", "cleaned": "规整字幕", "summary_json": "要点数据", "summary": "本节要点"}[kind], "content_type": "application/json" if kind in {"cues", "summary_json"} else "text/markdown" if kind in {"cleaned", "summary"} else "text/vtt", "available": True, "version": "v1"})
        v2_items, v2_manifest = self._v2_artifact_items(
            job_id, batch, job, video_dir
        )
        items.extend(v2_items)
        return {
            "items": items,
            "v2": {
                "available": bool(v2_manifest is not None and any(item["available"] for item in v2_items)),
                "status": v2_manifest.get("status") if v2_manifest is not None else None,
                "pipeline_status": v2_manifest.get("status") if v2_manifest is not None else None,
                "quality": v2_manifest.get("quality") if v2_manifest is not None else None,
                "warnings": list(v2_manifest.get("warnings") or []) if v2_manifest is not None else [],
            },
        }

    def _resolve_v2_artifact(self, artifact_id: str) -> tuple[Path, str, Mapping[str, Any]]:
        match = _V2_ARTIFACT_ID.fullmatch(artifact_id)
        if match is None:
            raise TranscriptError("Phase1 工件标识不正确。")
        job_id, opaque_id = match.groups()
        batch, job = self._find_job(job_id)
        video_dir = self._job_video_dir(job)
        manifest = self._v2_manifest(video_dir)
        if manifest is None or not self._v2_manifest_matches_v1(
            batch, job, video_dir, manifest
        ):
            raise TranscriptError("Phase1 工件不存在。")
        records = manifest.get("artifacts")
        if type(records) is not dict:
            raise TranscriptError("Phase1 工件不存在。")
        found = [
            (kind, record)
            for kind, record in records.items()
            if kind in V2_ARTIFACT_FILES
            and type(record) is dict
            and record.get("id") == opaque_id
        ]
        if len(found) != 1:
            raise TranscriptError("Phase1 工件不存在。")
        kind, record = found[0]
        if not self._safe_v2_record(record, kind):
            raise TranscriptError("Phase1 工件记录无效。")
        path = self._v2_dir(video_dir) / V2_ARTIFACT_FILES[kind]
        try:
            root = self._v2_dir(video_dir).resolve(strict=True)
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            mode = path.lstat().st_mode
        except (OSError, ValueError):
            raise TranscriptError("Phase1 工件路径不安全。") from None
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise TranscriptError("Phase1 工件路径不安全。")
        if not self._hashed_file_valid(path, record.get("sha256"), record.get("size")):
            raise TranscriptError("Phase1 工件完整性校验失败。")
        return path, kind, record

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
        is_v2 = type(artifact_id) is str and _V2_ARTIFACT_ID.fullmatch(artifact_id) is not None
        if is_v2:
            path, kind, record = self._resolve_v2_artifact(artifact_id)
            limit = V2_JSON_PREVIEW_LIMIT if kind in V2_JSON_KINDS or kind == "training_examples" else V2_TEXT_PREVIEW_LIMIT
        else:
            path, kind = self._resolve_artifact(artifact_id)
            record = None
            limit = V2_TEXT_PREVIEW_LIMIT
        try:
            if path.stat().st_size > limit:
                raise TranscriptError("字幕工件超过预览限制。")
            content = path.read_text(encoding="utf-8")
        except TranscriptError:
            raise
        except (OSError, UnicodeError) as exc:
            raise TranscriptError("字幕工件无法读取。") from exc
        if len(content.encode("utf-8")) > limit:
            raise TranscriptError("字幕工件超过预览限制。")
        parsed: object | None = None
        if is_v2 and kind in V2_JSON_KINDS:
            try:
                parsed = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                raise TranscriptError("Phase1 JSON 工件格式无效。") from None
            if type(parsed) not in (dict, list):
                raise TranscriptError("Phase1 JSON 工件格式无效。")
        elif is_v2 and kind == "training_examples":
            rows: list[dict[str, Any]] = []
            try:
                for line in content.splitlines():
                    value = json.loads(line)
                    if type(value) is not dict:
                        raise ValueError
                    rows.append(value)
            except (json.JSONDecodeError, ValueError, TypeError):
                raise TranscriptError("Phase1 JSONL 工件格式无效。") from None
            parsed = rows
        result = {"id": artifact_id, "kind": kind, "content": content}
        if is_v2:
            result.update(
                version="v2",
                content_type=(
                    "application/x-ndjson"
                    if kind == "training_examples"
                    else "text/markdown"
                    if kind == "corrected"
                    else "application/json"
                ),
                size=int(record.get("size")) if record is not None else len(content.encode("utf-8")),
                sha256=str(record.get("sha256")) if record is not None else _sha256(content.encode("utf-8")),
            )
            if parsed is not None:
                result["data"] = parsed
        return result

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
