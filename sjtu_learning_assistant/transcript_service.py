from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import re
import socket
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
    resolve_product_status,
)
from sjtu_learning_assistant.course_memory import (
    CourseMemoryStore,
    TrainingSampleQualityGate,
)
from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.transcript_pipeline import (
    PIPELINE_VERSION,
    PROMPT_VERSION,
    AUDIT_FALLBACK_WARNING,
    LEARNING_PRODUCTS_INCOMPLETE_WARNING,
    MAP_FALLBACK_WARNING,
    REDUCE_FALLBACK_WARNING,
    SUMMARY_EMPTY_WARNING,
    TranscriptAIFormatError,
    TranscriptPipeline,
    TranscriptPipelineCancelled,
    chunk_cues,
    normalize_cues,
    parse_vtt,
    validate_map,
)
from sjtu_learning_assistant.video_service import parse_remote_source_id
from sjtu_learning_assistant.transcript_playback import build_learning_segments, corrected_source_cues, cues_to_vtt

DEFAULT_TRANSCRIPT_ROOT = APP_SUPPORT_DIR / "transcripts" / "v1"
DEFAULT_COURSE_MEMORY_ROOT = APP_SUPPORT_DIR / "transcripts" / "v2" / "course_memory"
ACTIVE_STATES = frozenset(("queued", "fetching", "saved", "organizing"))
FINAL_STATES = frozenset(("completed", "completed_with_warnings", "partial", "failed", "waiting_for_ai", "cancelled", "interrupted"))
# Overall ``processing_state`` reported by ``learning_segments``, listed in
# evaluation priority order (the first matching rule wins):
#
# | value                 | meaning                                                                   |
# |-----------------------|---------------------------------------------------------------------------|
# | not_started           | no job and no stored subtitle for this recording                          |
# | cancelled             | latest job cancelled (also overrides a stale active/organizing status)    |
# | processing            | latest job queued/fetching/saved/organizing                               |
# | waiting_source        | no validated subtitle stored yet: remote still generating, remote checks |
# |                       | paused, or the fetch ended without a usable subtitle                      |
# | ai_unconfigured       | waiting_for_ai, or the stored draft was produced without AI               |
# | completed             | V1 clean and (when Phase1 is enabled) verified Phase1 quality passed      |
# | finished_needs_review | V1 clean and verified schema-valid Phase1 with only content uncertainty   |
# | recovering            | technical failure with a real pending automatic retry (cooldown or due)   |
# | recovery_exhausted    | technical failure of an auto-recoverable kind, no automatic retry left    |
# | failed                | technical failure that automatic recovery never applies to               |
#
# Only ``completed`` and ``finished_needs_review`` set ``processing_done``;
# only ``recovering`` sets ``auto_retryable`` and promises ``retry_after``
# (an active remote wait keeps its re-check time in ``retry_after``).
# Manual ``retryable`` is independent of this state.
PROCESSING_STATES = (
    "not_started",
    "cancelled",
    "processing",
    "waiting_source",
    "ai_unconfigured",
    "completed",
    "finished_needs_review",
    "recovering",
    "recovery_exhausted",
    "failed",
)
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


class TranscriptError(RuntimeError):
    pass


class _PlaybackYield(BaseException):
    """Keep saved work and let the foreground recording run next."""


class _TaskCircuitOpen(TranscriptError):
    category = "upstream_service"


class _TaskAIClient:
    def __init__(self, client: Any, service: "TranscriptService", batch: dict, job: dict):
        self.client, self.service, self.batch, self.job = client, service, batch, job
        self._task_started = time.monotonic()
        self._task_budget_seconds = service._bounded_env_float("SJTU_AI_TASK_BUDGET_SECONDS", 7200.0, 60.0, 14400.0)
        self._failure_limit = service._bounded_env_int("SJTU_AI_FAILURE_CIRCUIT_THRESHOLD", 3, 1, 20)
        self._consecutive_failures = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def chat_completion(self, *args: Any, **kwargs: Any) -> Any:
        max_attempts = self.service._bounded_env_int("SJTU_AI_RETRY_ATTEMPTS", 2, 1, 3)
        if self._consecutive_failures >= self._failure_limit:
            exc = _TaskCircuitOpen("AI 连续失败，已暂停本节后续请求。")
            self.service._record_ai_diagnostic(self.job, max_attempts, self._task_started, exc)
            self.service._save_batch(self.batch)
            raise exc
        deadline = self._task_started + self._task_budget_seconds
        for attempt in range(max_attempts):
            self.service._check_cancel(self.batch, self.job)
            self.service._yield_for_foreground(self.job)
            if time.monotonic() >= deadline:
                exc = TranscriptError("AI 任务达到配置的时间预算。")
                self.service._record_ai_diagnostic(self.job, attempt + 1, self._task_started, exc)
                self.service._save_batch(self.batch)
                raise exc
            attempt_started = time.monotonic()
            try:
                result = self.client.chat_completion(*args, **kwargs)
                self.service._check_cancel(self.batch, self.job)
                self.service._record_ai_diagnostic(self.job, attempt + 1, attempt_started, None)
                self._consecutive_failures = 0
                return result
            except Exception as exc:
                self.service._record_ai_diagnostic(self.job, attempt + 1, attempt_started, exc)
                self._consecutive_failures += 1
                self.service._save_batch(self.batch)
                category = str(getattr(exc, "category", ""))
                transient = isinstance(exc, (ConnectionError, TimeoutError)) or category in {
                    "network", "connection", "timeout", "rate_limit", "service_unavailable", "upstream_service",
                }
                if self._consecutive_failures >= self._failure_limit:
                    raise _TaskCircuitOpen("AI 连续失败，已暂停本节后续请求。") from None
                if not transient or attempt + 1 >= max_attempts:
                    raise
                delay = min(1.0, 0.25 * 2 ** attempt, max(0.0, deadline - time.monotonic()))
                if delay > 0:
                    self.service.sleeper(delay)


@dataclass(frozen=True)
class AIContext:
    client: Any | None
    model: str
    endpoint_fingerprint: str
    enabled: bool
    json_mode: bool = False


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
            "(Get-Process -Id %d -ErrorAction Stop).StartTime.ToUniversalTime().ToString('o')" % pid,
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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
    text = re.sub(r"(?i)\b[A-Z]:[\\/]Users[\\/]\S+", "[已隐藏]", text)
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
        now_provider: Callable[[], datetime] | None = None,
        playback_retry_cooldown_seconds: float = 30.0,
        playback_auto_retry_limit: int = 3,
    ) -> None:
        self.root = Path(root).expanduser()
        self.enable_phase1 = (
            self.root == DEFAULT_TRANSCRIPT_ROOT if enable_phase1 is None else bool(enable_phase1)
        ) or orchestrator is not None
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
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._foreground_source: str | None = None
        self._playback: dict[str, dict[str, float | int]] = {}
        self._playback_seq: dict[str, int] = {}
        self.playback_retry_cooldown_seconds = max(1.0, min(3600.0, float(playback_retry_cooldown_seconds)))
        self.playback_auto_retry_limit = max(0, min(5, int(playback_auto_retry_limit)))
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

    @staticmethod
    def _bounded_env_float(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

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
        except BaseException:
            os.close(descriptor)
            raise
        try:
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
            for delay in (0.05, 0.1, 0.25, 0.5, None):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if os.name != "nt" or delay is None:
                        raise
                    time.sleep(delay)
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
                    if batch.get("origin") == "playback":
                        # A process restart is not an automatic retry. Keep
                        # the interrupted job visible for an explicit retry.
                        job["auto_retryable"] = False
                        job["retryable"] = True
                        job["next_retry_at"] = None
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

    def start_batch(
        self,
        *,
        course_id: str | int,
        course_name: str,
        videos: Sequence[Mapping[str, Any]],
        force_phase1: bool = False,
        automatic_retry_count: int = 0,
        retry_failed_v1: bool = False,
        _batch_lock_held: bool = False,
        _batch_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
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
                "force_phase1": bool(force_phase1),
                # Regenerate V1 map chunks that previously fell back, without
                # necessarily bypassing a valid Phase1 review cache.
                "retry_failed_v1": bool(force_phase1 or retry_failed_v1),
                "automatic_retry_count": max(0, min(self.playback_auto_retry_limit, int(automatic_retry_count))),
            }
            for video in clean_videos
        ]
        batch = {"schema_version": 1, "id": batch_id, "course_id": key, "course_name": course_name.strip(), "status": "queued", "progress": 0, "created_at": _now(), "updated_at": _now(), "cancel_requested": False, "jobs": jobs}
        if _batch_metadata:
            # Playback retry identity must be present in the first durable
            # write so a restart can distinguish a child from a new retry.
            batch.update(dict(_batch_metadata))
        self._refresh_owner(batch)
        if _batch_lock_held:
            # Internal caller already holds both locks while atomically
            # reserving a playback retry against concurrent cancellation.
            with self._lock:
                self._write_json(self._batch_path(batch_id), batch)
        else:
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
        result.pop("force_phase1", None)
        result.pop("retry_failed_v1", None)
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
            force_phase1=True,
            automatic_retry_count=0,
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
            root_job_ids = {str(job["id"]) for job in batch.get("jobs", []) if isinstance(job.get("id"), str)} if job_id is None else {str(job_id)}
            self._cancel_playback_retry_descendants(
                root_batch_ids={target_id} if job_id is None else set(),
                root_job_ids=root_job_ids,
            )
        self._wake.set()
        return self._public_batch(batch)

    def _cancel_playback_retry_descendants(self, *, root_batch_ids: set[str], root_job_ids: set[str]) -> None:
        """Cancel only persisted automatic playback retries of the target lineage."""
        known_batch_ids = set(root_batch_ids)
        known_job_ids = set(root_job_ids)
        batches = self._all_batches()
        changed = True
        while changed:
            changed = False
            for descendant in batches:
                descendant_id = descendant.get("id")
                if not isinstance(descendant_id, str) or descendant_id in known_batch_ids:
                    continue
                if descendant.get("origin") != "playback":
                    continue
                parent_batch_id = descendant.get("retry_parent_batch_id")
                parent_job_id = descendant.get("retry_parent_job_id")
                if parent_batch_id not in known_batch_ids and parent_job_id not in known_job_ids:
                    continue
                descendant["cancel_requested"] = True
                for child_job in descendant.get("jobs", []):
                    child_job["cancel_requested"] = True
                    if child_job.get("status") in {"queued", "waiting_remote"}:
                        child_job.update(
                            status="cancelled",
                            stage="cancelled",
                            progress=100,
                            message="任务已取消。",
                        )
                jobs = descendant.get("jobs", [])
                if jobs and all(job.get("status") == "cancelled" for job in jobs):
                    descendant["status"] = "cancelled"
                descendant["updated_at"] = _now()
                self._write_json(self._batch_path(descendant_id), descendant)
                known_batch_ids.add(descendant_id)
                known_job_ids.update(str(job["id"]) for job in jobs if isinstance(job.get("id"), str))
                changed = True

    def _merge_cancel(self, batch: dict[str, Any], persisted: Mapping[str, Any]) -> None:
        batch["cancel_requested"] = bool(batch.get("cancel_requested") or persisted.get("cancel_requested"))
        by_id = {job.get("id"): job for job in persisted.get("jobs", [])}
        for job in batch.get("jobs", []):
            if by_id.get(job.get("id"), {}).get("cancel_requested"):
                job["cancel_requested"] = True

    def _check_cancel(self, batch: dict[str, Any], job: Mapping[str, Any]) -> None:
        with self._lock:
            self._merge_cancel(batch, self._read_json(self._batch_path(str(batch["id"]))))
            if batch.get("cancel_requested") or job.get("cancel_requested"):
                raise TranscriptPipelineCancelled()

    def _yield_for_foreground(self, job: Mapping[str, Any]) -> None:
        source = self._foreground_source
        if source is None or source == job.get("source_id"):
            return
        if any(
            item.get("source_id") == source and item.get("status") in ACTIVE_STATES
            for batch in self._all_batches() for item in batch.get("jobs", [])
        ):
            raise _PlaybackYield()

    def _task_context(self, context: AIContext, batch: dict, job: dict) -> AIContext:
        if isinstance(context.client, _TaskAIClient):
            return context
        client = _TaskAIClient(context.client, self, batch, job) if context.client is not None else None
        return AIContext(client, context.model, context.endpoint_fingerprint, context.enabled, context.json_mode)

    def update_playback(self, source_id: str, position_seconds: float, playback_rate: float = 1, seq: int | None = None) -> dict[str, Any]:
        parse_remote_source_id(source_id)
        if (
            type(position_seconds) not in {int, float} or not math.isfinite(position_seconds)
            or not 0 <= position_seconds <= 86400
            or type(playback_rate) not in {int, float} or not math.isfinite(playback_rate)
            or not 0.25 <= playback_rate <= 4
        ):
            raise TranscriptError("播放位置或速度不正确。")
        with self._lock:
            if seq is not None and (type(seq) is not int or not 0 <= seq <= 9_007_199_254_740_991):
                raise TranscriptError("播放更新序号不正确。")
            previous_seq = self._playback_seq.get(source_id, -1)
            if seq is not None and seq <= previous_seq:
                current = dict(self._playback.get(source_id, {}))
                return {
                    "source_id": source_id,
                    "position_seconds": current.get("position_seconds", float(position_seconds)),
                    "playback_rate": current.get("playback_rate", float(playback_rate)),
                    "lookahead_seconds": 180 * current.get("playback_rate", float(playback_rate)),
                    "seq": seq,
                    "stale": True,
                }
            self._foreground_source = source_id
            self._playback[source_id] = {"position_seconds": float(position_seconds), "playback_rate": float(playback_rate), "seq": seq if seq is not None else previous_seq + 1}
            self._playback_seq[source_id] = seq if seq is not None else previous_seq + 1
        self._wake.set()
        return {"source_id": source_id, "position_seconds": position_seconds, "playback_rate": playback_rate, "lookahead_seconds": 180 * playback_rate, "seq": self._playback_seq[source_id], "stale": False}

    def prepare_for_playback(
        self, *, course_id: str | int, course_name: str, video: Mapping[str, Any],
        position_seconds: float = 0, playback_rate: float = 1,
    ) -> dict[str, Any]:
        clean = self._validate_video(str(course_id), video)
        self.update_playback(clean["source_id"], position_seconds, playback_rate)
        context = self.ai_context_provider()
        prepare_key = self._idempotency_key(clean["source_id"], "playback", context)
        auto_retry_count = 0
        retry_source = None
        retry_batch_id = None
        with self._lock:
            matching = [
                (batch, job) for batch in self._all_batches() for job in batch.get("jobs", [])
                if job.get("source_id") == clean["source_id"]
            ]
            for batch, job in matching:
                if not batch.get("cancel_requested") and not job.get("cancel_requested") and (
                    job.get("status") in ACTIVE_STATES or job.get("status") == "waiting_remote"
                ):
                    if batch.get("retry_parent_job_id") and batch.get("retry_parent_batch_id"):
                        with self._batch_lock():
                            self._create_playback_retry_locked(
                                str(batch["retry_parent_batch_id"]),
                                str(batch["retry_parent_job_id"]),
                                expected_prepare_key=prepare_key,
                                require_retryable=False,
                            )
                    return self._public_batch(batch)
            latest_match = matching[0] if matching else None
            if latest_match is not None:
                latest_batch, latest_job = latest_match
                if (
                    latest_batch.get("origin") == "playback"
                    and latest_batch.get("prepare_key") == prepare_key
                    and latest_job.get("status") == "interrupted"
                ):
                    with self._batch_lock():
                        interrupted = self._preserve_interrupted_playback_locked(
                            str(latest_batch["id"]),
                            str(latest_job["id"]),
                            prepare_key=prepare_key,
                        )
                    if interrupted is not None:
                        return self._public_batch(interrupted)
            for batch, job in matching:
                if latest_match is not None and (batch is not latest_match[0] or job is not latest_match[1]):
                    continue
                if batch.get("origin") == "playback" and batch.get("prepare_key") == prepare_key and job.get("status") in {"partial", "failed", "waiting_for_ai"}:
                    retry_at = _parse_time(job.get("next_retry_at"))
                    retry_count = int(job.get("automatic_retry_count") or 0)
                    if (
                        job.get("auto_retryable") is not True
                        or retry_count >= self.playback_auto_retry_limit
                        or retry_at is None
                        or retry_at > self.now_provider()
                    ):
                        return self._public_batch(batch)
                    if job.get("retryable", True):
                        retry_source = job
                        retry_batch_id = batch.get("id")
                        auto_retry_count = retry_count + 1
                        break
            for batch, job in matching:
                if retry_source is not None:
                    break
                if latest_match is not None and (batch is not latest_match[0] or job is not latest_match[1]):
                    continue
                if job.get("status") in {"completed", "completed_with_warnings"}:
                    path = self._manifest_path(clean["source_id"])
                    if path.is_file() and not path.is_symlink():
                        manifest = self._read_json(path)
                        if manifest.get("artifact_idempotency_key") == self._idempotency_key(clean["source_id"], manifest.get("raw_sha256"), context) and self._completed_artifacts_valid(path.parent, manifest):
                            if not self._phase1_needs_upgrade(path.parent, manifest):
                                return self._public_batch(batch)
                            if job.get("auto_retryable") is not True:
                                return self._public_batch(batch)
                            retry_source = job
                            retry_batch_id = batch.get("id")
                            auto_retry_count = int(job.get("automatic_retry_count") or 0) + 1
                            if auto_retry_count > self.playback_auto_retry_limit:
                                return self._public_batch(batch)
                            retry_at = _parse_time(job.get("next_retry_at"))
                            if retry_at is None or retry_at > self.now_provider():
                                return self._public_batch(batch)
                            break
            if retry_source is not None and type(retry_batch_id) is str:
                with self._batch_lock():
                    retried = self._create_playback_retry_locked(
                        retry_batch_id,
                        str(retry_source.get("id")),
                        expected_prepare_key=prepare_key,
                        require_retryable=False,
                    )
                    if retried is not None:
                        return self._public_batch(retried)
                    return self._public_batch(self._read_json(self._batch_path(retry_batch_id)))
            started = self.start_batch(
                course_id=course_id, course_name=course_name, videos=[clean],
                force_phase1=False,
                automatic_retry_count=auto_retry_count,
                retry_failed_v1=False,
                _batch_metadata={"origin": "playback", "prepare_key": prepare_key},
            )
            return started

    def _preserve_interrupted_playback_locked(
        self,
        batch_id: str,
        job_id: str,
        *,
        prepare_key: str,
    ) -> dict[str, Any] | None:
        """Keep a restarted playback job interrupted until a user retries it."""
        batch = self._read_json(self._batch_path(batch_id))
        job = next((item for item in batch.get("jobs", []) if item.get("id") == job_id), None)
        if (
            batch.get("origin") != "playback"
            or batch.get("prepare_key") != prepare_key
            or job is None
            or job.get("status") != "interrupted"
        ):
            return None
        parent_batch_id = batch.get("retry_parent_batch_id")
        parent_job_id = batch.get("retry_parent_job_id")
        if isinstance(parent_batch_id, str) and isinstance(parent_job_id, str):
            # If the process exited between child and parent writes, reconcile
            # the durable child identity without resuming it automatically.
            self._create_playback_retry_locked(
                parent_batch_id,
                parent_job_id,
                expected_prepare_key=prepare_key,
                require_retryable=False,
            )
            batch = self._read_json(self._batch_path(batch_id))
            job = next((item for item in batch.get("jobs", []) if item.get("id") == job_id), None)
            if job is None or job.get("status") != "interrupted":
                return None
        job["auto_retryable"] = False
        job["retryable"] = True
        job["next_retry_at"] = None
        batch["updated_at"] = _now()
        self._write_json(self._batch_path(batch_id), batch)
        return batch

    def _phase1_needs_upgrade(self, video_dir: Path, manifest: Mapping[str, Any]) -> bool:
        if not self.enable_phase1:
            return False
        if self._v1_has_unresolved_failure(manifest):
            return True
        raw_hash = manifest.get("raw_sha256")
        if type(raw_hash) is not str or not raw_hash:
            return True
        v2_manifest = self._v2_manifest(video_dir)
        if v2_manifest is None or v2_manifest.get("raw_sha256") != raw_hash or not self._v2_artifacts_valid(video_dir, v2_manifest):
            return True
        quality = v2_manifest.get("quality")
        if (
            type(quality) is dict
            and quality.get("schema_pass") is True
            and quality.get("passed") is not True
            and (
                (type(quality.get("uncertain_rate")) in (int, float) and quality.get("uncertain_rate", 0) > 0)
                or (type(quality.get("critic_pass_rate")) in (int, float) and quality.get("critic_pass_rate", 1) < 1)
            )
        ):
            # Content uncertainty is a completed review decision. Replaying the
            # same input automatically cannot resolve it; leave manual retry available.
            return False
        return type(quality) is not dict or quality.get("passed") is not True

    @staticmethod
    def _v1_has_unresolved_failure(manifest: Mapping[str, Any]) -> bool:
        return bool(
            manifest.get("status") in {"partial", "failed", "interrupted", "completed_with_warnings"}
            or manifest.get("partial_warnings")
        )

    def _v1_recovery_diagnostic(self, video_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any] | None:
        if not self._v1_has_unresolved_failure(manifest):
            return None

        failed_stages: list[dict[str, Any]] = []

        def add(stage: str, code: str, auto_retryable: bool = True) -> None:
            if not any(item["stage"] == stage for item in failed_stages):
                failed_stages.append({"stage": stage, "code": code, "auto_retryable": auto_retryable})

        chunks = manifest.get("chunks") if type(manifest.get("chunks")) is dict else {}
        for index, record in chunks.items():
            if type(index) is not str or not index.isdigit() or type(record) is not dict:
                continue
            relative = "chunks/%04d.json" % int(index)
            if record.get("path") != relative or not self._hashed_file_valid(video_dir / relative, record.get("sha256")):
                continue
            try:
                chunk = self._read_json(video_dir / relative)
            except TranscriptError:
                continue
            if chunk.get("partial_warning") == MAP_FALLBACK_WARNING:
                add("map", "v1_map_fallback")

        summary_record = (manifest.get("artifacts") or {}).get("summary_json") if type(manifest.get("artifacts")) is dict else None
        summary: dict[str, Any] = {}
        if (
            type(summary_record) is dict
            and summary_record.get("path") == "summary.json"
            and self._hashed_file_valid(video_dir / "summary.json", summary_record.get("sha256"))
        ):
            try:
                summary = self._read_json(video_dir / "summary.json")
            except TranscriptError:
                summary = {}
        review = summary.get("review") if type(summary.get("review")) is dict else {}
        guide_diag = review.get("guide_generation_diagnostics") if type(review.get("guide_generation_diagnostics")) is dict else {}
        guide_code = str(guide_diag.get("violation_code") or guide_diag.get("category") or "")
        if guide_code in {"schema", "validation", "response_format", "truncated", "empty"}:
            add("guide_generation", "v1_guide_schema")
        ledger = review.get("coverage_ledger") if type(review.get("coverage_ledger")) is list else []
        for row in ledger:
            if type(row) is not dict:
                continue
            diag = row.get("diagnostics") if type(row.get("diagnostics")) is dict else {}
            code = str(diag.get("violation_code") or diag.get("category") or "")
            if row.get("status") != "audited" and code in {"schema", "validation", "response_format", "truncated", "empty"}:
                add("audit", "v1_audit_schema")
        if not any(item["stage"] == "audit" for item in failed_stages) and (
            review.get("status") == "completed_with_warnings"
            or AUDIT_FALLBACK_WARNING in (manifest.get("partial_warnings") or [])
        ):
            add("audit", "v1_audit_incomplete")
        if not any(item["stage"] == "guide_generation" for item in failed_stages) and guide_diag:
            add("guide_generation", "v1_guide_incomplete")
        if not failed_stages:
            add("v1", "v1_incomplete", auto_retryable=False)
        result = dict(failed_stages[0])
        result["failed_stages"] = failed_stages
        result["auto_retryable"] = any(item["auto_retryable"] for item in failed_stages)
        return result

    def _stored_learning_input(self, source_id: str) -> tuple[Path, dict, str] | None:
        parse_remote_source_id(source_id)
        video_dir = self._video_dir(source_id)
        path = video_dir / "manifest.json"
        if not path.is_file() or path.is_symlink():
            return None
        manifest = self._read_json(path)
        record = manifest.get("artifacts", {}).get("raw_vtt", {})
        if manifest.get("source", {}).get("source_id") != source_id or record.get("path") != "raw.vtt" or record.get("sha256") != manifest.get("raw_sha256") or not self._hashed_file_valid(video_dir / "raw.vtt", record.get("sha256")):
            return None
        raw = (video_dir / "raw.vtt").read_text(encoding="utf-8")
        return video_dir, manifest, raw

    def _corrected_cues(self, video_dir: Path, manifest: Mapping[str, Any], raw: str) -> list[Any] | None:
        v2 = self._v2_manifest(video_dir)
        if not v2 or v2.get("raw_sha256") != manifest.get("raw_sha256") or not self._v2_artifacts_valid(video_dir, v2):
            return None
        v2_dir = self._v2_dir(video_dir)
        quality = self._read_json(v2_dir / "quality.json")
        if quality.get("passed") is not True:
            return None
        corrected = self._read_json(v2_dir / "corrected.json")
        chunks = json.loads((v2_dir / "semantic_chunks.json").read_text(encoding="utf-8"))
        if type(chunks) is not list or type(corrected.get("corrections")) is not list:
            return None
        try:
            return corrected_source_cues(normalize_cues(parse_vtt(raw)), chunks, corrected["corrections"])
        except (ValueError, KeyError, TypeError):
            return None

    def corrected_subtitles(self, source_id: str) -> dict[str, Any] | None:
        stored = self._stored_learning_input(source_id)
        if stored is None:
            return None
        video_dir, manifest, raw = stored
        cues = self._corrected_cues(video_dir, manifest, raw)
        if cues is None:
            return None
        return {"status": "ready", "message": "已加载通过质量检查的校正字幕。", "vtt": cues_to_vtt(cues), "cue_count": len(cues), "content_type": "text/vtt; charset=utf-8", "source_version": "corrected"}

    def learning_segments(self, source_id: str) -> dict[str, Any]:
        parse_remote_source_id(source_id)
        latest = next((
            (batch, job) for batch in self._all_batches() for job in batch.get("jobs", [])
            if job.get("source_id") == source_id
        ), None)
        job = latest[1] if latest is not None else {}
        is_processing = bool(latest is not None and not latest[0].get("cancel_requested") and not job.get("cancel_requested") and job.get("status") in ACTIVE_STATES | {"waiting_remote"})
        video_dir: Path | None = None
        def dto_fields(manifest: Mapping[str, Any] | None = None, source_hash: str | None = None, version: str = "original") -> dict[str, Any]:
            manifest = manifest or {}
            diagnosis = job.get("diagnostic") if type(job.get("diagnostic")) is dict else {}
            quality = manifest.get("quality") if type(manifest.get("quality")) is dict else (
                job.get("quality") if type(job.get("quality")) is dict else {}
            )
            v1_diagnostic = self._v1_recovery_diagnostic(video_dir, manifest) if video_dir is not None else None
            v1_unresolved = self._v1_has_unresolved_failure(manifest)
            content_uncertain = self._phase1_content_uncertain(quality) and not v1_unresolved
            failed = (
                job.get("status") in {"partial", "failed", "interrupted"}
                or v1_unresolved
                or job.get("phase1_status") not in (None, "completed")
                or (self.enable_phase1 and bool(quality) and quality.get("passed") is not True)
            )
            phase1_done = bool(not is_processing and job.get("phase1_status") in {"completed", "completed_with_warnings", "partial"})
            retry_at = job.get("next_retry_at")
            revision_material = "|".join((source_hash or "", str(manifest.get("learning_source_sha256") or ""), version))
            error_code = (
                v1_diagnostic.get("code") if v1_diagnostic else
                "content_uncertain" if content_uncertain else
                diagnosis.get("code") or ("task_failed" if failed else None)
            )
            error_stage = (
                v1_diagnostic.get("stage") if v1_diagnostic else
                "needs_review" if content_uncertain and not is_processing else
                job.get("stage") or manifest.get("stage")
            )
            error_message = (
                self._learning_error_message({**job, "diagnostic": {"code": error_code}}, manifest)
                if failed else None
            )
            state = self._processing_state(
                latest[0] if latest is not None else {}, job, is_processing, video_dir, manifest, v1_unresolved,
            )
            return {
                "processing_state": state,
                "stage": error_stage,
                "error_code": error_code,
                "error_message": error_message,
                "retryable": bool(failed and (v1_unresolved or job.get("retryable", job.get("status") != "cancelled"))),
                # Only a real pending automatic retry counts; a V1 diagnostic
                # code alone cannot outlive the job's retry limit or cancellation.
                "auto_retryable": state == "recovering",
                "failed_stages": v1_diagnostic.get("failed_stages", []) if v1_diagnostic else [],
                "processing_done": state in {"completed", "finished_needs_review"},
                "phase1_processing_done": phase1_done,
                # Only a real pending recovery or an active remote re-check
                # promises a next time; paused/exhausted states promise none.
                "retry_after": retry_at if type(retry_at) is str and (
                    state == "recovering" or (state == "waiting_source" and job.get("status") == "waiting_remote")
                ) else None,
                "updated_at": job.get("updated_at") or manifest.get("updated_at"),
                "source_raw_sha256": source_hash,
                "learning_revision": _sha256(revision_material.encode("utf-8")),
                "completed_segments": 0,
                "failed_segments": int(bool(failed)),
            }
        empty = {"segments": [], "available_ranges": [], "status": "processing" if is_processing else "empty", "message": self._learning_message(job, {}, is_processing, False, True), "source_version": "original", "is_processing": is_processing, **dto_fields()}
        stored = self._stored_learning_input(source_id)
        if stored is None:
            return empty
        video_dir, manifest, raw = stored
        cues = normalize_cues(parse_vtt(raw))
        corrected = self._corrected_cues(video_dir, manifest, raw)
        learning_hash = _sha256(_json_bytes([cue.as_dict() for cue in corrected])) if corrected is not None else None
        synchronized = manifest.get("learning_source") == "corrected" and manifest.get("learning_source_sha256") == learning_hash
        unchanged_verified = corrected is not None and all(cue.text == old.text for cue, old in zip(corrected, cues))
        if synchronized:
            cues = corrected
        mapped = []
        for index, record in manifest.get("chunks", {}).items():
            if not str(index).isdigit() or record.get("path") != f"chunks/{int(index):04d}.json":
                continue
            path = video_dir / record["path"]
            if self._hashed_file_valid(path, record.get("sha256")):
                try:
                    mapped.append(validate_map(self._read_json(path), cues))
                except (ValueError, TranscriptError):
                    continue
        summary = None
        record = manifest.get("artifacts", {}).get("summary_json", {})
        if manifest.get("artifact_learning_source_sha256") == manifest.get("learning_source_sha256") and record.get("path") == "summary.json" and self._hashed_file_valid(video_dir / "summary.json", record.get("sha256")):
            summary = self._read_json(video_dir / "summary.json")
        provisional = not (synchronized or unchanged_verified)
        result = build_learning_segments(cues, mapped, summary, provisional=provisional)
        source_hash = str(manifest.get("raw_sha256") or "") or None
        fields = dto_fields(manifest, source_hash, "corrected" if synchronized or unchanged_verified else "original")
        segment_count = len(result.get("segments", []))
        completed_segments, failed_segments = self._quality_segment_counts(video_dir)
        fields["completed_segments"] = completed_segments
        fields["failed_segments"] = failed_segments
        return {
            **result, "status": "ready" if result["segments"] else ("processing" if is_processing else "empty"),
            "message": self._learning_message(job, manifest, is_processing, bool(result["segments"]), provisional),
            "source_version": "corrected" if synchronized or unchanged_verified else "original",
            "is_processing": is_processing,
            **fields,
        }

    def _quality_segment_counts(self, video_dir: Path) -> tuple[int, int]:
        v2_dir = self._v2_dir(video_dir)
        quality_path = v2_dir / V2_ARTIFACT_FILES["quality"]
        chunks_path = v2_dir / V2_ARTIFACT_FILES["semantic_chunks"]
        uncertain_path = v2_dir / V2_ARTIFACT_FILES["uncertain"]
        try:
            v2 = self._v2_manifest(video_dir)
            if v2 is None or not self._v2_artifacts_valid(video_dir, v2):
                return 0, 0
            quality = self._read_json(quality_path)
            chunks = self._read_json(chunks_path)
            uncertain = self._read_json(uncertain_path)
            if type(chunks) is not list or type(uncertain) is not list:
                return 0, 0
            total = len(chunks)
            rate = quality.get("critic_pass_rate")
            passed = total if quality.get("passed") is True else (
                max(0, min(total, round(float(rate) * total))) if type(rate) in (int, float) else 0
            )
            failed = max(len(uncertain), total - passed)
            return passed, min(total, failed)
        except (OSError, ValueError, TypeError, TranscriptError):
            return 0, 0

    def _automatic_retry_pending(self, batch: Mapping[str, Any], job: Mapping[str, Any]) -> bool:
        """Whether prepare/scheduler will really start another automatic retry.

        Mirrors the guards of ``prepare_for_playback`` and
        ``_schedule_due_playback_retries``: a diagnostic code alone is never
        enough once the retry limit is reached, the retry was consumed, or the
        job was cancelled.
        """
        status = job.get("status")
        return bool(
            job.get("auto_retryable") is True
            and job.get("retryable", True) is not False
            and int(job.get("automatic_retry_count") or 0) < self.playback_auto_retry_limit
            and _parse_time(job.get("next_retry_at")) is not None
            and job.get("auto_retry_scheduled") is not True
            and not job.get("cancel_requested")
            and not (batch.get("cancel_requested") and status not in FINAL_STATES)
            and status in FINAL_STATES - {"cancelled"}
            # Partial/failed jobs are only re-run automatically for playback
            # batches; completed-with-warnings upgrades are taken by prepare.
            and (batch.get("origin") == "playback" or status in {"completed", "completed_with_warnings"})
        )

    def _automatic_retry_exhausted(self, job: Mapping[str, Any], v1_diagnostic: Mapping[str, Any] | None = None) -> bool:
        recoverable_kind = bool(
            job.get("auto_retryable") is True
            or job.get("auto_retry_exhausted") is True
            or (v1_diagnostic is not None and v1_diagnostic.get("auto_retryable"))
        )
        return recoverable_kind and int(job.get("automatic_retry_count") or 0) >= self.playback_auto_retry_limit

    def _verified_phase1_quality(self, video_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any] | None:
        """Phase1 quality bound to the stored raw subtitle with intact artifacts, else None."""
        raw_hash = manifest.get("raw_sha256")
        v2 = self._v2_manifest(video_dir)
        if (
            type(raw_hash) is not str or not raw_hash or v2 is None
            or v2.get("raw_sha256") != raw_hash or not self._v2_artifacts_valid(video_dir, v2)
        ):
            return None
        try:
            quality = self._read_json(self._v2_dir(video_dir) / V2_ARTIFACT_FILES["quality"])
        except TranscriptError:
            return None
        return quality if type(quality) is dict and quality == v2.get("quality") else None

    def _processing_state(
        self,
        batch: Mapping[str, Any],
        job: Mapping[str, Any],
        is_processing: bool,
        video_dir: Path | None,
        manifest: Mapping[str, Any],
        v1_unresolved: bool,
    ) -> str:
        """Single overall state for one recording; see ``PROCESSING_STATES``."""
        status = job.get("status")
        if not job and video_dir is None:
            return "not_started"
        # A batch-level cancel only targets jobs that were still unfinished.
        cancel_requested = bool(
            job.get("cancel_requested") or (batch.get("cancel_requested") and status not in FINAL_STATES)
        )
        if status == "cancelled" or (cancel_requested and status in ACTIVE_STATES | {"waiting_remote"}):
            # A stale worker/organizing status cannot outlive an explicit cancel.
            return "cancelled"
        if status == "waiting_remote":
            return "waiting_source"
        if is_processing:
            return "processing"
        if video_dir is None:
            # No validated subtitle stored: never "completed"; a remote that is
            # still generating (or whose checks paused) is a source wait.
            if status == "waiting_for_ai":
                return "ai_unconfigured"
            if self._automatic_retry_pending(batch, job):
                return "recovering"
            if job.get("stage") != "remote_retry_exhausted" and self._automatic_retry_exhausted(job):
                return "recovery_exhausted"
            return "waiting_source"
        if status == "waiting_for_ai" or (
            manifest.get("ai_execution_mode") == "deterministic"
            and manifest.get("status") in {"completed", "completed_with_warnings"}
            and status not in {"partial", "failed", "interrupted"}
        ):
            # A non-AI draft is never a completed (or exhausted) AI result. A
            # later AI attempt that failed technically is reported as a failure.
            return "ai_unconfigured"
        v1_clean = bool(
            not v1_unresolved
            and manifest.get("status") == "completed"
            and self._completed_artifacts_valid(video_dir, manifest)
            and status not in {"partial", "failed", "interrupted"}
        )
        phase1_technical = bool(job.get("review_status") == "failed" or job.get("phase1_warning"))
        if v1_clean and not phase1_technical:
            if not self.enable_phase1:
                return "completed"
            else:
                quality = self._verified_phase1_quality(video_dir, manifest)
                if quality is not None and quality.get("passed") is True and quality.get("schema_pass") is not False:
                    return "completed"
                if quality is not None and self._phase1_content_uncertain(quality):
                    return "finished_needs_review"
        if cancel_requested:
            return "cancelled"
        if self._automatic_retry_pending(batch, job):
            return "recovering"
        if self._automatic_retry_exhausted(job, self._v1_recovery_diagnostic(video_dir, manifest)):
            return "recovery_exhausted"
        return "failed"

    @staticmethod
    def _phase1_content_uncertain(quality: Mapping[str, Any]) -> bool:
        return bool(
            quality.get("schema_pass") is True
            and quality.get("passed") is not True
            and (
                (type(quality.get("uncertain_rate")) in (int, float) and quality.get("uncertain_rate", 0) > 0)
                or (type(quality.get("critic_pass_rate")) in (int, float) and quality.get("critic_pass_rate", 1) < 1)
            )
        )

    @classmethod
    def _automatic_retry_forces_phase1(cls, job: Mapping[str, Any]) -> bool:
        """Whether an automatic retry must bypass the Phase1 review cache.

        Only a Phase1 technical failure (exception, schema failure or other
        incomplete review) is replayed. A schema-valid review whose content is
        merely uncertain is a finished decision: the retry exists to recover a
        failed V1 stage, so the stored partial quality artifacts are reused.
        """
        if job.get("phase1_status") == "completed":
            return False
        quality = job.get("quality")
        technical_failure = job.get("review_status") == "failed" or bool(job.get("phase1_warning"))
        if not technical_failure and type(quality) is dict and cls._phase1_content_uncertain(quality):
            return False
        return True

    @staticmethod
    def _learning_error_message(job: Mapping[str, Any], manifest: Mapping[str, Any]) -> str:
        code = ((job.get("diagnostic") or {}).get("code") if type(job.get("diagnostic")) is dict else None)
        if code in {"network", "timeout", "rate_limit", "upstream_service"}:
            return "AI 服务暂时不可用；已保存原字幕，可在冷却后重试。"
        if code == "content_uncertain":
            return "校对已结束，部分内容证据不足，需要人工核对。"
        if code == "v1_map_fallback":
            return "字幕分块校验未通过，已保留回退内容；失败阶段可自动重试。"
        if code == "v1_guide_schema":
            return "学习讲义未通过结构校验；已保留可用内容，失败阶段可自动重试。"
        if code in {"v1_audit_schema", "v1_audit_incomplete"}:
            return "讲义审校未完成；已保留可用内容，失败阶段可自动重试。"
        if code == "v1_incomplete":
            return "字幕整理仍有未完成阶段；已保留可用内容，可手动重试。"
        if job.get("stage") == "remote_retry_exhausted":
            return "远端字幕仍未就绪，自动检查已暂停。"
        if job.get("status") == "cancelled":
            return "整理已取消；已保存的内容仍可使用。"
        return "整理未完成；已保留可用内容，可重试。"

    @staticmethod
    def _diagnostic_code(exc: BaseException | None) -> str:
        if exc is None:
            return "ok"
        category = str(getattr(exc, "category", ""))
        allowed = {"network", "connection", "timeout", "rate_limit", "rate_limit_or_quota", "service_unavailable", "upstream_service", "authentication", "endpoint_or_model", "request_parameters", "response_format", "validation", "schema", "format", "unsupported_tools", "client_error"}
        if category in allowed:
            return category
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, (ConnectionError, socket.gaierror, OSError)):
            return "network"
        return "task_error"

    def _record_ai_diagnostic(self, job: dict[str, Any], attempt: int, started: float, exc: BaseException | None) -> None:
        cumulative_attempt = int(job.get("ai_attempt_count") or 0) + 1
        job["ai_attempt_count"] = min(1_000_000, cumulative_attempt)
        diagnosis: dict[str, Any] = {
            "stage": str(job.get("stage") or "ai"),
            "code": self._diagnostic_code(exc),
            "attempt": max(1, min(1_000_000, cumulative_attempt)),
            "elapsed_ms": max(0, min(3_600_000, int((time.monotonic() - started) * 1000))),
            "retryable": bool(exc is not None and self._diagnostic_code(exc) in {"network", "connection", "timeout", "rate_limit", "rate_limit_or_quota", "service_unavailable", "upstream_service", "response_format", "validation", "schema", "format"}),
        }
        status_code = getattr(exc, "status_code", None) if exc is not None else None
        if type(status_code) is int and 100 <= status_code <= 599:
            diagnosis["http_status"] = status_code
        error_code = getattr(exc, "error_code", None) if exc is not None else None
        if type(error_code) is str and re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", error_code):
            diagnosis["code"] = error_code.lower()
        exception_type = getattr(exc, "exception_type", None) if exc is not None else None
        if type(exception_type) is str and re.fullmatch(r"[A-Za-z0-9_]{1,80}", exception_type):
            diagnosis["exception_type"] = exception_type
        if exc is None:
            if not isinstance(job.get("diagnostic"), dict):
                job["diagnostic"] = diagnosis
        else:
            job["diagnostic"] = diagnosis
            job["retryable"] = diagnosis["retryable"]

    @staticmethod
    def _learning_message(job: Mapping[str, Any], manifest: Mapping[str, Any], processing: bool, has_content: bool, provisional: bool) -> str:
        if processing:
            if job.get("status") == "waiting_remote":
                return "远端字幕仍在生成，将自动再次检查。"
            return "已显示可用片段，正在提前整理后续内容。" if has_content else "正在准备同步整理内容。"
        if job.get("status") == "cancelled" or job.get("cancel_requested"):
            return "整理已取消；已保存的内容仍可使用。"
        if job.get("status") in {"partial", "failed", "interrupted"} or manifest.get("status") in {"partial", "failed", "interrupted"}:
            return "整理未完成；已保留可用内容，可重试。" if has_content else "整理未完成，暂无可用的同步内容，可重试。"
        if TranscriptService._v1_has_unresolved_failure(manifest):
            return "字幕基础整理仍有失败阶段；已保留可用内容，可自动或手动重试失败阶段。"
        quality = manifest.get("quality") if type(manifest.get("quality")) is dict else (
            job.get("quality") if type(job.get("quality")) is dict else {}
        )
        if TranscriptService._phase1_content_uncertain(quality):
            return "校对已结束，部分内容证据不足，需要人工核对；系统不会对相同输入自动重复请求。"
        if job.get("phase1_status") not in (None, "completed") or job.get("review_status") == "failed":
            return "基础内容可读；深度校对已结束但未通过，可重试失败阶段。"
        if manifest.get("ai_execution_mode") == "deterministic":
            return "已保存原字幕初稿；AI 尚未启用或未连接。" if has_content else "已保存基础字幕初稿，尚未提取出可验证的同步内容。"
        if has_content:
            return "已提供原字幕初稿；校对尚未完成。" if provisional else "校对完成，同步整理已就绪。"
        return "尚未生成可验证的同步内容。" if job else "还没有此录像的同步整理内容。"

    def _chunk_priority(self, source_id: str, chunk: Any) -> float:
        playback = self._playback.get(source_id, {"position_seconds": 0, "playback_rate": 1})
        position = playback["position_seconds"] * 1000
        start, end = chunk.cues[0].start_ms, chunk.cues[-1].end_ms
        if start <= position <= end:
            return 0
        if end < position:
            return 1e12 + position - end
        return start - position

    def _pipeline_control_options(self, batch: dict, job: dict, *, json_mode: bool | None = None) -> dict[str, Any]:
        def should_cancel() -> bool:
            try:
                self._check_cancel(batch, job)
            except TranscriptPipelineCancelled:
                return True
            return False
        options = {
            "chunk_priority": lambda chunk: self._chunk_priority(str(job["source_id"]), chunk),
            "should_cancel": should_cancel,
        }
        if json_mode is not None:
            options["json_mode"] = bool(json_mode)
        parameters = inspect.signature(self.pipeline.run).parameters
        return options if any(value.kind == inspect.Parameter.VAR_KEYWORD for value in parameters.values()) else {key: value for key, value in options.items() if key in parameters}

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
            try:
                self.run_pending()
            except Exception:
                # A malformed batch or unavailable storage must not kill the only worker.
                continue

    def _claim_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock, self._batch_lock():
            batch = self._read_json(self._batch_path(batch_id))
            if batch.get("status") not in {"queued", "waiting_remote"}:
                return None
            if batch.get("status") == "waiting_remote":
                due = False
                for job in batch.get("jobs", []):
                    retry_at = _parse_time(job.get("next_retry_at"))
                    if job.get("status") == "waiting_remote" and (
                        batch.get("cancel_requested") or job.get("cancel_requested")
                        or retry_at is None or retry_at <= self.now_provider()
                    ):
                        job.update(status="queued", stage="queued")
                        due = True
                if not due:
                    return None
                batch["status"] = "queued"
            owner = batch.get("owner")
            if self._owner_alive(owner) and not self._is_self_owner(owner):
                return None
            self._refresh_owner(batch)
            batch["updated_at"] = _now()
            self._write_json(self._batch_path(batch_id), batch)
            return batch

    def run_pending(self) -> None:
        self._schedule_due_playback_retries()
        while not self._stop.is_set():
            candidates = list(reversed(self._all_batches()))
            candidates.sort(key=lambda batch: not any(job.get("source_id") == self._foreground_source for job in batch.get("jobs", [])))
            batch = None
            for candidate in candidates:
                if candidate.get("status") in {"queued", "waiting_remote"} and type(candidate.get("id")) is str:
                    try:
                        batch = self._claim_batch(candidate["id"])
                    except TranscriptError:
                        continue
                    if batch is not None:
                        break
            if batch is None:
                return
            try:
                self._process_batch(batch)
            except Exception as exc:
                for job in batch.get("jobs", []):
                    if job.get("status") in ACTIVE_STATES:
                        job.update(status="partial", stage="worker_failed", error=_safe_error(exc), message="任务暂时未完成，可重试并复用已保存内容。")
                self._save_batch(batch)

    def _create_playback_retry_locked(
        self,
        parent_batch_id: str,
        parent_job_id: str,
        *,
        expected_prepare_key: str | None,
        require_retryable: bool = True,
    ) -> dict[str, Any] | None:
        """Create or reconcile one retry while both service and batch locks are held."""
        parent = self._read_json(self._batch_path(parent_batch_id))
        parent_job = next(
            (job for job in parent.get("jobs", []) if job.get("id") == parent_job_id),
            None,
        )
        now = self.now_provider()
        due = _parse_time(parent_job.get("next_retry_at")) if parent_job else None
        retry_count = int(parent_job.get("automatic_retry_count") or 0) if parent_job else 0
        if (
            parent.get("origin") != "playback"
            or parent.get("prepare_key") != expected_prepare_key
            or parent.get("cancel_requested")
            or parent_job is None
            or parent_job.get("cancel_requested")
            or parent_job.get("status") not in FINAL_STATES - {"cancelled"}
            or parent_job.get("auto_retryable") is not True
            or (
                not parent_job.get("retryable")
                if require_retryable
                else (
                    parent_job.get("status") in {"partial", "failed", "waiting_for_ai"}
                    and not parent_job.get("retryable", True)
                )
            )
            or parent_job.get("auto_retry_scheduled") is True
            or retry_count >= self.playback_auto_retry_limit
            or due is None
            or due > now
        ):
            return None
        next_retry_count = retry_count + 1
        child = next(
            (
                batch for batch in self._all_batches()
                if batch.get("origin") == "playback"
                and batch.get("retry_parent_batch_id") == parent_batch_id
                and batch.get("retry_parent_job_id") == parent_job_id
                and batch.get("retry_count") == next_retry_count
            ),
            None,
        )
        if child is not None:
            parent_job["auto_retry_scheduled"] = True
            parent["updated_at"] = _now()
            self._write_json(self._batch_path(parent_batch_id), parent)
            return child

        video = dict(parent_job.get("meta") or {})
        video.update(source_id=parent_job["source_id"], title=parent_job.get("title") or "课程录像")
        started = self.start_batch(
            course_id=str(parent["course_id"]),
            course_name=str(parent["course_name"]),
            videos=[video],
            force_phase1=self._automatic_retry_forces_phase1(parent_job),
            automatic_retry_count=next_retry_count,
            retry_failed_v1=True,
            _batch_lock_held=True,
            _batch_metadata={
                "origin": "playback",
                "prepare_key": parent.get("prepare_key"),
                "retry_parent_job_id": parent_job_id,
                "retry_parent_batch_id": parent_batch_id,
                "retry_count": next_retry_count,
            },
        )
        parent_job["auto_retry_scheduled"] = True
        parent["updated_at"] = _now()
        self._write_json(self._batch_path(parent_batch_id), parent)
        return self._read_json(self._batch_path(started["id"]))

    def _schedule_due_playback_retries(self) -> None:
        now = self.now_provider()
        for candidate in self._all_batches():
            if candidate.get("origin") != "playback" or type(candidate.get("id")) is not str:
                continue
            for item in candidate.get("jobs", []):
                due = _parse_time(item.get("next_retry_at"))
                count = int(item.get("automatic_retry_count") or 0)
                if (
                    due is None or due > now or not item.get("retryable")
                    or item.get("auto_retryable") is False
                    or item.get("auto_retry_scheduled") is True
                    or item.get("status") not in FINAL_STATES
                    or count >= self.playback_auto_retry_limit
                    or item.get("status") == "cancelled"
                    or item.get("cancel_requested")
                ):
                    continue
                try:
                    with self._lock, self._batch_lock():
                        self._create_playback_retry_locked(
                            candidate["id"],
                            str(item.get("id")),
                            expected_prepare_key=candidate.get("prepare_key"),
                        )
                except TranscriptError:
                    continue
                except (KeyError, TypeError, ValueError):
                    continue

    def _save_batch(self, batch: dict[str, Any]) -> None:
        with self._lock, self._batch_lock():
            self._merge_cancel(batch, self._read_json(self._batch_path(str(batch["id"]))))
            jobs = batch.get("jobs", [])
            for job in jobs:
                job["updated_at"] = _now()
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
            elif any(job.get("stage") == "paused_for_playback" for job in jobs):
                batch["status"] = "queued"
            elif any(state in {"queued", "fetching", "saved"} for state in states):
                batch["status"] = "fetching"
            elif any(state == "waiting_remote" for state in states):
                batch["status"] = "waiting_remote"
            elif any(state == "partial" for state in states):
                batch["status"] = "partial"
            elif any(state == "failed" for state in states):
                batch["status"] = "partial" if len(states) > 1 else "failed"
            elif any(state == "interrupted" for state in states):
                batch["status"] = "interrupted"
            elif any(state == "waiting_for_ai" for state in states):
                batch["status"] = "waiting_for_ai"
            else:
                batch["status"] = "partial"
            batch["updated_at"] = _now()
            self._refresh_owner(batch)
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

    def _job_has_usable_subtitle(self, job: Mapping[str, Any]) -> bool:
        source_id = job.get("source_id")
        if type(source_id) is not str:
            return False
        try:
            video_dir = self._video_dir(source_id)
            manifest = self._read_json(video_dir / "manifest.json")
            context = self.ai_context_provider()
        except Exception:
            return False
        source = manifest.get("source")
        pipeline = manifest.get("pipeline")
        ai = manifest.get("ai")
        if (
            manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
            or type(source) is not dict
            or source.get("source_id") != source_id
            or type(pipeline) is not dict
            or pipeline.get("pipeline_version") != PIPELINE_VERSION
            or pipeline.get("prompt_version") != PROMPT_VERSION
            or type(ai) is not dict
            or ai.get("model") != context.model
            or ai.get("endpoint_fingerprint") != context.endpoint_fingerprint
            or manifest.get("idempotency_key")
            != self._idempotency_key(source_id, manifest.get("raw_sha256"), context)
        ):
            return False
        records = manifest.get("artifacts")
        record = records.get("raw_vtt") if type(records) is dict else None
        raw_path = video_dir / "raw.vtt"
        if (
            type(record) is not dict
            or record.get("path") != "raw.vtt"
            or record.get("sha256") != manifest.get("raw_sha256")
            or not self._hashed_file_valid(raw_path, record.get("sha256"))
        ):
            return False
        try:
            return bool(normalize_cues(parse_vtt(raw_path.read_text(encoding="utf-8"))))
        except (OSError, UnicodeError, ValueError):
            return False

    def _process_claimed_batch(self, batch: dict[str, Any]) -> None:
        if batch.get("cancel_requested"):
            for job in batch["jobs"]:
                if job.get("status") not in FINAL_STATES:
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
                except TranscriptPipelineCancelled:
                    job.update(status="cancelled", stage="cancelled", progress=100, message="整理已取消，已保存内容仍可使用。")
                except _PlaybackYield:
                    job.update(status="saved", stage="paused_for_playback", message="已保存进度，优先准备当前播放录像。")
                except Exception as exc:
                    has_subtitle = self._job_has_usable_subtitle(job)
                    job["status"] = resolve_product_status(has_subtitle, False, True)
                    job["stage"] = "fetch"
                    job["error"] = _safe_error(exc)
                    self._schedule_retry(job, exc)
                    job["message"] = (
                        "基础字幕已保存；后续存储失败，可重试。"
                        if has_subtitle else "字幕存储失败，可重试。"
                    )
                self._save_batch(batch)
                if job.get("status") == "saved":
                    try:
                        self._organize(batch, job)
                    except TranscriptPipelineCancelled:
                        job.update(status="cancelled", stage="cancelled", progress=100, message="整理已取消，已保存内容仍可使用。")
                    except _PlaybackYield:
                        job.update(status="saved", stage="paused_for_playback", message="已保存进度，优先准备当前播放录像。")
                        self._save_batch(batch)
                        continue
                    except Exception as exc:
                        job.update(status="partial", stage="organize_failed", error=_safe_error(exc), message="已保留字幕，整理暂时失败，可重试。")
                        self._schedule_retry(job, exc)
                    self._save_batch(batch)
        remaining_saved = [job for job in batch["jobs"] if job.get("status") == "saved"]
        remaining_saved.sort(key=lambda item: item.get("source_id") != self._foreground_source)
        for job in remaining_saved:
            if batch.get("cancel_requested") or job.get("cancel_requested"):
                job.update(status="cancelled", stage="cancelled", progress=100, message="整理已取消，已保存内容仍可使用。")
                continue
            try:
                self._organize(batch, job)
            except TranscriptPipelineCancelled:
                job.update(status="cancelled", stage="cancelled", progress=100, message="整理已取消，已保存内容仍可使用。")
            except _PlaybackYield:
                job.update(status="saved", stage="paused_for_playback", message="已保存进度，优先准备当前播放录像。")
            except Exception as exc:
                job.update(status="partial", stage="organize_failed", error=_safe_error(exc), message="已保留字幕，整理暂时失败，可重试。")
                self._schedule_retry(job, exc)
            self._save_batch(batch)
        self._save_batch(batch)

    def _schedule_retry(self, job: dict[str, Any], exc: BaseException | None) -> None:
        code = self._diagnostic_code(exc)
        auto_retryable = code in {"network", "connection", "timeout", "rate_limit", "rate_limit_or_quota", "service_unavailable", "upstream_service", "response_format", "validation", "schema", "format"}
        manual_retryable = auto_retryable or code == "task_error"
        job["retryable"] = manual_retryable
        job["auto_retryable"] = auto_retryable
        retry_count = int(job.get("automatic_retry_count") or 0)
        self._enforce_auto_retry_limit(job)
        if not job["auto_retryable"]:
            job.pop("next_retry_at", None)
            return
        delay = min(3600.0, self.playback_retry_cooldown_seconds * (2 ** retry_count))
        job["next_retry_at"] = (self.now_provider() + timedelta(seconds=delay)).isoformat()

    def _enforce_auto_retry_limit(self, job: dict[str, Any]) -> None:
        """Cap a recoverable job's ``auto_retryable`` by its real retry budget.

        The stored job (and every public job/batch payload) must not promise a
        further automatic recovery once ``automatic_retry_count`` reached the
        limit, whatever the failing stage's error code says. The recoverable
        kind is kept as ``auto_retry_exhausted`` so the UI can say so.
        """
        if job.get("auto_retryable") is True and int(job.get("automatic_retry_count") or 0) >= self.playback_auto_retry_limit:
            job["auto_retryable"] = False
            job["auto_retry_exhausted"] = True
            job.pop("next_retry_at", None)
        else:
            job.pop("auto_retry_exhausted", None)

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
            "ai" if context.enabled and context.client is not None else "deterministic",
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
            learning_source_sha256=manifest.get("learning_source_sha256"),
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
    def _phase1_cache_key(
        raw_hash: str,
        memory_version: str | None,
        pipeline_version: str,
        prompt_version: str,
        model_version: str,
        endpoint_fingerprint: str,
    ) -> str:
        value = {
            "raw_hash": raw_hash,
            "course_memory_version": memory_version,
            "pipeline_version": pipeline_version,
            "prompt_version": prompt_version,
            "model_version": model_version,
            "endpoint_fingerprint": endpoint_fingerprint,
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
        adapter = OpenAIClassificationSemanticAdapter(context.client, json_mode=context.json_mode)
        adapter.model = context.model or adapter.model
        return CourseLearningOrchestrator(adapter, persist_raw=False)

    def _run_phase1(
        self,
        batch: Mapping[str, Any],
        job: dict[str, Any],
        video_dir: Path,
        raw: str,
        context: AIContext,
        v1_manifest: Mapping[str, Any] | None = None,
    ) -> None:
        context = self._task_context(context, batch, job)
        self._check_cancel(batch, job)
        self._yield_for_foreground(job)
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
        if self.orchestrator is not None:
            pipeline_version, prompt_version, model_version = self._phase1_versions(self.orchestrator)
        else:
            pipeline_version, prompt_version, model_version = (
                COURSE_PIPELINE_VERSION,
                COURSE_PROMPT_VERSION,
                context.model or "unknown",
            )
        expected_key = self._phase1_cache_key(
            raw_hash,
            current_memory_version,
            pipeline_version,
            prompt_version,
            model_version,
            context.endpoint_fingerprint,
        )
        cached_manifest = self._v2_manifest(video_dir)
        if (
            not bool(job.get("force_phase1"))
            and cached_manifest is not None
            and cached_manifest.get("raw_sha256") == raw_hash
            and cached_manifest.get("course_id") == course_id
            and cached_manifest.get("video_id") == video_id
            and cached_manifest.get("course_memory_version") == current_memory_version
            and cached_manifest.get("endpoint_fingerprint") == context.endpoint_fingerprint
            and self._v2_artifacts_valid(video_dir, cached_manifest, expected_cache_key=expected_key)
        ):
            job.update(
                phase1_status=str(cached_manifest.get("status") or "completed"),
                pipeline_status=str(cached_manifest.get("status") or "completed"),
                quality=cached_manifest.get("quality"),
                phase1_reused=True,
                review_status=(
                    "completed"
                    if str(cached_manifest.get("status") or "completed") == "completed"
                    else "needs_review"
                ),
            )
            # A reused partial review must project the same retry state as a
            # fresh one; otherwise a V1-only recovery hides manual retry.
            self._apply_phase1_outcome(
                job, video_dir, str(cached_manifest.get("status") or "completed"),
                cached_manifest.get("quality"), v1_manifest,
            )
            return
        orchestrator = self._phase1_orchestrator(context)
        job.update(
            status="organizing",
            stage="reviewing",
            review_status="running",
            review_completed=0,
            review_total=0,
        )
        self._save_batch(batch)

        def report_progress(completed: int, total: int, _stage: str) -> None:
            self._check_cancel(batch, job)
            self._yield_for_foreground(job)
            safe_total = max(0, int(total))
            safe_completed = max(0, min(int(completed), safe_total)) if safe_total else 0
            job.update(
                review_status="running",
                review_completed=safe_completed,
                review_total=safe_total,
                message=(
                    f"AI 校对中 · 已处理 {safe_completed} / {safe_total}"
                    if safe_total
                    else "正在准备 AI 校对…"
                ),
            )
            self._save_batch(batch)

        run_options = dict(
            course_id=course_id,
            video_id=video_id,
            raw_vtt=raw,
            memory_repo=memory_repo,
            progress_callback=report_progress,
            force_refresh=bool(job.get("force_phase1")),
            endpoint_fingerprint=context.endpoint_fingerprint,
        )
        signature = inspect.signature(orchestrator.orchestrate).parameters
        if not any(value.kind == inspect.Parameter.VAR_KEYWORD for value in signature.values()):
            run_options = {key: value for key, value in run_options.items() if key in signature}
        result = orchestrator.orchestrate(**run_options)
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
            context.endpoint_fingerprint,
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
            "course_id": course_id,
            "video_id": video_id,
            "raw_sha256": raw_hash,
            "course_memory_version": output_memory_version,
            "pipeline_version": pipeline_version,
            "prompt_version": prompt_version,
            "model_version": model_version,
            "endpoint_fingerprint": context.endpoint_fingerprint,
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
            review_status="completed" if status == "completed" else "needs_review",
        )
        if int(job.get("review_total") or 0) > 0:
            job["review_completed"] = int(job["review_total"])
        if status != "completed":
            job["partial_warning"] = True
            job["message"] = str(job.get("message") or "") + " Phase1 已降级，请查看质量报告。"
        self._apply_phase1_outcome(job, video_dir, status, quality, v1_manifest)

    def _apply_phase1_outcome(
        self,
        job: dict[str, Any],
        video_dir: Path,
        status: str,
        quality: object,
        v1_manifest: Mapping[str, Any] | None,
    ) -> None:
        """Retry/diagnostic bookkeeping shared by fresh and cached Phase1 results."""
        if status != "completed" or type(quality) is not dict or quality.get("passed") is not True:
            job["retryable"] = True
            stored_v1_manifest = v1_manifest
            if stored_v1_manifest is None:
                v1_manifest_path = self._manifest_path(str(job.get("source_id")))
                try:
                    stored_v1_manifest = self._read_json(v1_manifest_path) if v1_manifest_path.is_file() and not v1_manifest_path.is_symlink() else {}
                except TranscriptError:
                    stored_v1_manifest = {}
            v1_diagnostic = self._v1_recovery_diagnostic(video_dir, stored_v1_manifest)
            v1_unresolved = self._v1_has_unresolved_failure(stored_v1_manifest)
            content_uncertain = (self._phase1_content_uncertain(quality) if type(quality) is dict else False) and not v1_unresolved
            auto_retryable = bool(v1_diagnostic.get("auto_retryable")) if v1_diagnostic else not content_uncertain
            job["auto_retryable"] = auto_retryable
            # The V1 diagnostic says *whether* the stage is recoverable; the
            # job's retry budget decides whether another attempt will happen.
            self._enforce_auto_retry_limit(job)
            job["diagnostic"] = {
                "stage": v1_diagnostic.get("stage") if v1_diagnostic else ("needs_review" if content_uncertain else "phase1_quality"),
                "code": v1_diagnostic.get("code") if v1_diagnostic else ("content_uncertain" if content_uncertain else ("schema_incomplete" if type(quality) is dict and quality.get("schema_pass") is False else "quality_incomplete")),
                "attempt": int(job.get("automatic_retry_count") or 0) + 1,
                "elapsed_ms": 0,
                "retryable": True,
            }
            if job["auto_retryable"]:
                delay = min(3600.0, self.playback_retry_cooldown_seconds * (2 ** int(job.get("automatic_retry_count") or 0)))
                job["next_retry_at"] = (self.now_provider() + timedelta(seconds=delay)).isoformat()
            else:
                job.pop("next_retry_at", None)
        else:
            job["retryable"] = False
            job["auto_retryable"] = False
            job.pop("auto_retry_exhausted", None)
            job.pop("next_retry_at", None)

    def _run_phase1_safely(
        self,
        batch: dict[str, Any],
        job: dict[str, Any],
        video_dir: Path,
        raw: str,
        context: AIContext,
        v1_manifest: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.enable_phase1:
            return
        final_state = (job.get("status"), job.get("stage"), job.get("progress"))
        phase1_succeeded = False
        try:
            self._run_phase1(batch, job, video_dir, raw, context, v1_manifest)
            phase1_succeeded = True
        except (TranscriptPipelineCancelled, _PlaybackYield):
            raise
        except Exception as exc:
            warning = _safe_error(exc)
            self._record_ai_diagnostic(job, int(job.get("automatic_retry_count") or 0) + 1, time.monotonic(), exc)
            self._schedule_retry(job, exc)
            job.update(
                phase1_status="partial",
                pipeline_status="partial",
                phase1_reused=False,
                phase1_warning=warning,
                partial_warning=True,
                review_status="failed",
            )
            job["message"] = str(job.get("message") or "") + " Phase1 未完成，旧版字幕与摘要仍可用。"
            v2_dir = self._v2_dir(video_dir)
            if not v2_dir.exists():
                v2_dir.mkdir(exist_ok=True, mode=0o700)
                os.chmod(v2_dir, 0o700)
            if not v2_dir.is_symlink() and v2_dir.is_dir():
                self._write_json(v2_dir / "manifest.json", {
                    "schema_version": V2_MANIFEST_SCHEMA_VERSION,
                    "course_id": str(batch.get("course_id") or ""),
                    "video_id": parse_remote_source_id(str(job.get("source_id")))[1],
                    "raw_sha256": _sha256(raw.encode("utf-8")),
                    "course_memory_version": None,
                    "pipeline_version": COURSE_PIPELINE_VERSION,
                    "prompt_version": COURSE_PROMPT_VERSION,
                    "model_version": context.model,
                    "cache_key": None,
                    "status": "partial",
                    "quality": None,
                    "warnings": [warning],
                    "artifacts": {},
                    "updated_at": _now(),
                })
        finally:
            if job.get("stage") == "reviewing":
                job.update(status=final_state[0], stage=final_state[1], progress=final_state[2])
                self._save_batch(batch)
            quality = job.get("quality")
            if (
                phase1_succeeded
                and job.get("phase1_status") == "completed"
                and type(quality) is dict
                and quality.get("passed") is True
                and type(v1_manifest) is dict
                and not self._v1_has_unresolved_failure(v1_manifest)
            ):
                # A fully successful Phase1 pass may follow transient request
                # failures in the same job. Clear those only when V1 is clean.
                job.pop("diagnostic", None)
                job.pop("partial_warning", None)
                job.pop("phase1_warning", None)
                job["retryable"] = False
                job["auto_retryable"] = False
                job.pop("auto_retry_exhausted", None)
                job.pop("next_retry_at", None)

    def _validated_cached_chunks(
        self,
        video_dir: Path,
        manifest: Mapping[str, Any],
        job: Mapping[str, Any],
        context: AIContext,
        raw: str,
        require_complete: bool,
        source_cues: Sequence[Any] | None = None,
    ) -> dict[int, Mapping[str, Any]] | None:
        if not self._chunk_context_matches(manifest, job, context):
            return None
        try:
            cues = list(source_cues) if source_cues is not None else normalize_cues(parse_vtt(raw))
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
                normalized = validate_map(value, chunk.cues)
                if (bool(job.get("force_phase1")) or bool(job.get("retry_failed_v1"))) and normalized.get("partial_warning"):
                    # Explicit retry should reuse successful map checkpoints but
                    # regenerate chunks that previously fell back to raw cues.
                    continue
                validated[chunk.index] = normalized
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
        self._check_cancel(batch, job)
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
                can_skip_organize = (
                    can_reuse_complete
                    and (not bool(job.get("force_phase1")) or not self.enable_phase1)
                    and not self._phase1_needs_upgrade(video_dir, manifest)
                )
                raw = raw_path.read_bytes().decode("utf-8")
                offline_chunks = None
                if not can_reuse_complete and manifest.get("status") in ("partial", "interrupted"):
                    offline_chunks = self._validated_cached_chunks(
                        video_dir, manifest, job, context, raw, True
                    )
                self._write_json(manifest_path, manifest)
                job["video_dir"] = video_dir.name
                reused_status = str(manifest.get("status")) if can_skip_organize else "saved"
                job["status"] = reused_status
                job["stage"] = "reused" if can_skip_organize else "saved"
                job["progress"] = 100 if can_skip_organize else (85 if offline_chunks is not None else 45)
                job["message"] = "已复用相同字幕工件。"
                if reused_status == "completed_with_warnings":
                    job["partial_warning"] = True
                    job["message"] = REDUCE_FALLBACK_WARNING if REDUCE_FALLBACK_WARNING in manifest.get("partial_warnings", list()) else "已复用带提示的字幕工件。"
                job["reused"] = True
                if offline_chunks is not None:
                    job["offline_recovery"] = True
                    job["message"] = "已验证全部分块，正在离线生成最终结果。"
                if can_skip_organize and self.enable_phase1:
                    phase_manifest = self._v2_manifest(video_dir) or {}
                    phase_status = str(phase_manifest.get("status") or "completed")
                    phase_quality = phase_manifest.get("quality")
                    phase_quality = phase_quality if type(phase_quality) is dict else {}
                    job.update(
                        phase1_status=phase_status,
                        pipeline_status=phase_status,
                        quality=phase_quality,
                        phase1_reused=True,
                        review_status="completed" if phase_quality.get("passed") is True else "needs_review",
                    )
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
            polls = int(job.get("remote_poll_attempts") or 0) + 1
            job["remote_poll_attempts"] = polls
            if polls > 8:
                job.update(status="failed", stage="remote_retry_exhausted", error="远端字幕长时间未就绪。", message="字幕仍未就绪，自动检查已暂停，可稍后重试。")
                return
            job["status"] = "waiting_remote"
            job["stage"] = "waiting_remote"
            job["progress"] = 10
            job["next_retry_at"] = (self.now_provider() + timedelta(seconds=min(300, 30 * 2 ** (polls - 1)))).isoformat()
            job["message"] = "远端字幕仍在生成，将自动再次检查。"
            return
        job.pop("next_retry_at", None)
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
        self._check_cancel(batch, job)
        self._yield_for_foreground(job)
        video_dir = self._job_video_dir(job)
        manifest_path = video_dir / "manifest.json"
        manifest = self._read_json(manifest_path)
        context = self._task_context(self.ai_context_provider(), batch, job)
        self._refresh_manifest_versions(manifest, job, context)
        raw_path = video_dir / "raw.vtt"
        if not self._hashed_file_valid(raw_path, manifest.get("raw_sha256")):
            raise TranscriptError("原始字幕哈希校验失败。")
        raw = raw_path.read_bytes().decode("utf-8")
        source_cues = None
        if (
            self.enable_phase1
            and source_cues is None
            and bool(job.get("force_phase1"))
            and manifest.get("status") == "completed"
            and not manifest.get("partial_warnings")
            and manifest.get("artifact_idempotency_key") == manifest.get("idempotency_key")
            and self._completed_artifacts_valid(video_dir, manifest)
        ):
            self._write_json(manifest_path, manifest)
            self._run_phase1_safely(batch, job, video_dir, raw, context, manifest)
            # The explicit refresh has now consumed its one-shot cache bypass.
            # Corrected-cue synchronization may re-enter _organize and must not
            # recursively trigger the same refresh.
            job["force_phase1"] = False
            job["retry_failed_v1"] = False
            self._synchronize_corrected_learning(batch, job, video_dir, raw, context)
            current_manifest = self._read_json(manifest_path)
            state = current_manifest.get("status")
            if state in {"completed", "completed_with_warnings"}:
                job.update(status=state, stage=str(current_manifest.get("stage") or state), progress=100)
            return
        source_cues = self._corrected_cues(video_dir, manifest, raw)
        if source_cues is not None and any(cue.text != original.text for cue, original in zip(source_cues, normalize_cues(parse_vtt(raw)))):
            manifest.update(learning_source="corrected", learning_source_sha256=_sha256(_json_bytes([cue.as_dict() for cue in source_cues])))
        else:
            source_cues = None
            manifest.pop("learning_source_sha256", None)
            manifest["learning_source"] = "original"
        offline_recovery = bool(job.pop("offline_recovery", False))
        cached = self._validated_cached_chunks(
            video_dir, manifest, job, context, raw, offline_recovery, source_cues=source_cues
        )
        if offline_recovery and cached is None:
            offline_recovery = False
        if cached is None:
            cached = dict()
            manifest["chunks"] = dict()
            manifest["chunk_context"] = self._current_chunk_context(manifest, job, context)
        if source_cues is not None:
            cue_payload = _json_bytes([cue.as_dict() for cue in source_cues])
            self._atomic_write(video_dir / "cues.json", cue_payload)
            manifest["artifacts"]["cues"] = {"path": "cues.json", "sha256": _sha256(cue_payload)}
        deterministic_only = not context.enabled or context.client is None
        job["status"] = "organizing"
        job["stage"] = "deterministic_handout" if deterministic_only else ("offline_reduce" if offline_recovery else "map_reduce")
        job["progress"] = 85 if offline_recovery else 55
        manifest.update(status="organizing", stage=job["stage"], updated_at=_now())
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
            job.update(stage="map", progress=manifest["progress"])
            self._write_json(manifest_path, manifest)
            self._save_batch(batch)
            self._check_cancel(batch, job)
            self._yield_for_foreground(job)
        def on_cleaned(markdown: str) -> None:
            payload = markdown.encode("utf-8")
            self._atomic_write(video_dir / "cleaned.md", payload)
            manifest.setdefault("artifacts", dict())
            manifest["artifacts"]["cleaned"] = dict(path="cleaned.md", sha256=_sha256(payload))
            manifest["stage"] = "cleaned_saved"
            manifest["progress"] = max(85, int(manifest.get("progress") or 0))
            job.update(stage="cleaned_saved", progress=manifest["progress"])
            manifest["updated_at"] = _now()
            self._write_json(manifest_path, manifest)
            self._save_batch(batch)
        try:
            run_options = dict(
                cached_chunks=cached,
                on_chunk=on_chunk,
                on_cleaned=on_cleaned,
            )
            run_options.update(self._pipeline_control_options(batch, job, json_mode=context.json_mode))
            if source_cues is not None:
                run_options["source_cues"] = source_cues
            if offline_recovery:
                run_options["offline_only"] = True
            elif deterministic_only:
                run_options["deterministic_only"] = True
            result = self.pipeline.run(raw, context.client, **run_options)
            if source_cues is not None and result.cues != [cue.as_dict() for cue in source_cues]:
                raise TranscriptError("校正讲义的 Cue 对齐验证失败。")
            manifest["ai_execution_mode"] = "deterministic" if deterministic_only else "ai"
            artifacts = {
                "cues": ("cues.json", _json_bytes(result.cues)),
                "summary_json": ("summary.json", _json_bytes(result.summary)),
                "summary": ("summary.md", result.summary_markdown.encode("utf-8")),
            }
            for kind, (name, payload) in artifacts.items():
                self._atomic_write(video_dir / name, payload)
                manifest["artifacts"][kind] = {"path": name, "sha256": _sha256(payload)}
            manifest["artifact_idempotency_key"] = manifest.get("idempotency_key")
            manifest["artifact_learning_source_sha256"] = manifest.get("learning_source_sha256")
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
                if LEARNING_PRODUCTS_INCOMPLETE_WARNING in result.partial_warnings:
                    message = LEARNING_PRODUCTS_INCOMPLETE_WARNING
                elif REDUCE_FALLBACK_WARNING in result.partial_warnings:
                    message = REDUCE_FALLBACK_WARNING
                else:
                    message = "字幕与经验证的本节要点已生成；部分分块使用原字幕回退。"
                job.update(dict(status="completed_with_warnings", stage="completed_with_warnings", progress=100, message=message, error=None, partial_warning=True))
            else:
                manifest.pop("partial_warnings", None)
                manifest.pop("ai_warning", None)
                manifest.pop("ai_error", None)
                manifest.update(dict(status="completed", stage="completed", progress=100, error=None, updated_at=_now()))
                job.update(dict(status="completed", stage="completed", progress=100, message="字幕已存储并完成 AI 规整。", error=None))
                # This V1 result supersedes any diagnostic from an earlier
                # attempt. Phase1 below may set a fresh diagnostic if needed.
                job.pop("diagnostic", None)
                job.pop("partial_warning", None)
                job["retryable"] = False
                job["auto_retryable"] = False
                job.pop("auto_retry_exhausted", None)
                job.pop("next_retry_at", None)
        except (TranscriptPipelineCancelled, _PlaybackYield):
            manifest.update(status="partial", stage="paused", updated_at=_now())
            self._write_json(manifest_path, manifest)
            raise
        except Exception as exc:
            manifest.update(dict(status="partial", stage="ai_failed", progress=max(55, int(manifest.get("progress") or 55)), error=_safe_error(exc), updated_at=_now()))
            if isinstance(exc, TranscriptAIFormatError):
                manifest["ai_error"] = dict(category=exc.category, diagnostics=exc.diagnostics)
            job.update(dict(status="partial", stage="ai_failed", progress=manifest["progress"], message="原始字幕已保存；AI 规整失败，可重试。", error=_safe_error(exc)))
        if (
            manifest.get("status") in {"completed", "completed_with_warnings"}
            and self._completed_artifacts_valid(video_dir, manifest)
        ):
            if source_cues is None:
                self._run_phase1_safely(batch, job, video_dir, raw, context, manifest)
                job["force_phase1"] = False
                job["retry_failed_v1"] = False
        self._write_json(manifest_path, manifest)
        if source_cues is None:
            self._synchronize_corrected_learning(batch, job, video_dir, raw, context)

    def _synchronize_corrected_learning(self, batch: dict, job: dict, video_dir: Path, raw: str, context: AIContext) -> None:
        manifest = self._read_json(video_dir / "manifest.json")
        corrected = self._corrected_cues(video_dir, manifest, raw)
        if not context.enabled or corrected is None:
            job["learning_sync"] = "original_provisional"
            return
        original = normalize_cues(parse_vtt(raw))
        if all(cue.text == old.text for cue, old in zip(corrected, original)):
            job["learning_sync"] = "no_text_change"
            return
        source_hash = _sha256(_json_bytes([cue.as_dict() for cue in corrected]))
        if manifest.get("status") in {"completed", "completed_with_warnings"} and manifest.get("artifact_learning_source_sha256") == source_hash and self._completed_artifacts_valid(video_dir, manifest):
            job["learning_sync"] = "corrected"
            return
        parameters = inspect.signature(self.pipeline.run).parameters
        if "source_cues" not in parameters and not any(value.kind == inspect.Parameter.VAR_KEYWORD for value in parameters.values()):
            job["learning_sync"] = "alignment_not_supported"
            return
        self._organize(batch, job)
        job["learning_sync"] = "corrected" if job.get("status") in {"completed", "completed_with_warnings"} else "sync_incomplete"

    def _v2_artifact_items(self, job_id: str, video_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        manifest = self._v2_manifest(video_dir)
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
        _batch, job = self._find_job(job_id)
        video_dir = self._job_video_dir(job)
        items, manifest = self._v2_artifact_items(job_id, video_dir)
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
        _batch, job = self._find_job(job_id)
        video_dir = self._job_video_dir(job)
        manifest = self._read_json(video_dir / "manifest.json")
        items = []
        for kind, record in manifest.get("artifacts", {}).items():
            if kind not in ARTIFACT_FILES or type(record) is not dict:
                continue
            path = video_dir / str(record.get("path"))
            if path.is_file() and not path.is_symlink():
                items.append({"id": f"{job_id}:{kind}", "kind": kind, "label": {"raw_vtt": "原始字幕", "cues": "Cue 数据", "cleaned": "规整字幕", "summary_json": "要点数据", "summary": "本节要点"}[kind], "content_type": "application/json" if kind in {"cues", "summary_json"} else "text/markdown" if kind in {"cleaned", "summary"} else "text/vtt", "available": True, "version": "v1"})
        v2_items, v2_manifest = self._v2_artifact_items(job_id, video_dir)
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
        _batch, job = self._find_job(job_id)
        video_dir = self._job_video_dir(job)
        manifest = self._v2_manifest(video_dir)
        if manifest is None:
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
                for line in content.split("\n"):
                    if not line.strip():
                        continue
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
        if sys.platform == "win32":
            # explorer.exe returns exit code 1 even on success; ignore it.
            subprocess.run(["explorer.exe", f"/select,{path}"], check=False)
            return
        subprocess.run(("/usr/bin/open", "-R", "--", str(path)), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
