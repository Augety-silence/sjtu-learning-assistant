"""Native persistent, consent-gated full-lecture ASR; no summary/KB rebuild.

Callbacks must resolve cached original text before reporting missing subtitles.
The worker lock is held for its whole lifetime (including HTTP response drain),
so another App/process cannot reclaim a slow paid request on lease expiry.
"""
from __future__ import annotations
import errno
import hashlib
import json
import os
import re
import stat
import threading
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from sjtu_learning_assistant.ai_runtime import SharedAIRuntime
from sjtu_learning_assistant.asr_service import get_asr_api_key
from sjtu_learning_assistant.local_settings import validate_asr_model, validate_ai_base_url
from urllib.parse import urlsplit
from sjtu_learning_assistant.recording_audio import (
    MAX_CHUNK_BYTES, MAX_CHUNK_SECONDS, RecordingAudioError, file_metadata,
    prepare_recording_audio,
)

ENDPOINT = "https://api.siliconflow.cn/v1/audio/transcriptions"
DEFAULT_MODEL = "XingChenAGI/XingChenASR-V3.2-Ultra"
MAX_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 16_000_000
RUNNABLE = {"queued", "asr_completed"}

if os.name == "nt":
    import msvcrt as _windows_locks
    _posix_locks = None
else:
    import fcntl as _posix_locks
    _windows_locks = None


def _lock_fd(fd, *, blocking=True):
    """Exclusive cross-process companion-file lock; never a Windows no-op."""
    if _windows_locks is None:
        flags = _posix_locks.LOCK_EX | (0 if blocking else _posix_locks.LOCK_NB)
        _posix_locks.flock(fd, flags)
        return
    # Windows CRT locks an actual byte range; the companion must have byte 0.
    if os.fstat(fd).st_size == 0:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            os.write(fd, b"\0")
            os.fsync(fd)
        except OSError as exc:
            # Another process can initialize and lock byte 0 between fstat
            # and write. Continue to lock it; never replace/truncate the file.
            if exc.errno not in (errno.EACCES, errno.EAGAIN) or os.fstat(fd).st_size < 1:
                raise
    while True:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            _windows_locks.locking(fd, _windows_locks.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) and getattr(exc, "winerror", None) not in (32, 33, 36):
                raise
            if not blocking:
                raise BlockingIOError(errno.EAGAIN, "recording lock held") from None
            time.sleep(0.01)


def _unlock_fd(fd):
    if _windows_locks is None:
        _posix_locks.flock(fd, _posix_locks.LOCK_UN)
    else:
        os.lseek(fd, 0, os.SEEK_SET)
        _windows_locks.locking(fd, _windows_locks.LK_UNLCK, 1)


class RecordingASRError(RuntimeError):
    """Only constant diagnostic codes escape the native service."""


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_path(path: Path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise RecordingASRError("unsafe_storage_path")


def _read(path: Path, limit=MAX_RESPONSE_BYTES) -> bytes:
    _safe_path(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise RecordingASRError("invalid_checkpoint_file")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise RecordingASRError("checkpoint_size_limit")
        return data


def _atomic(path: Path, data: bytes, *, immutable=False):
    _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_path(path.parent)
    if immutable and path.exists():
        if _read(path, max(MAX_RESPONSE_BYTES, len(data))) != data:
            raise RecordingASRError("existing_checkpoint_conflict")
        return
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            try:
                # Exclusive creation is atomic even against external editors.
                os.link(temporary, path)
            except FileExistsError:
                if _read(path, max(MAX_RESPONSE_BYTES, len(data))) != data:
                    raise RecordingASRError("existing_checkpoint_conflict")
        else:
            # Windows scanners/readers can briefly hold a delete-denying
            # handle. Retry only this local rename, never a paid ASR request.
            for attempt in range(20):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError as exc:
                    if os.name != "nt" or attempt == 19 or getattr(exc, "winerror", None) not in (5, 32, 33):
                        raise
                    time.sleep(.01)
        # Windows cannot open/fsync a directory using the CRT. File fsync
        # above is mandatory on both platforms; POSIX also flushes the rename.
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_write(path, value, **kwargs):
    _atomic(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode(), **kwargs)


def _clean(value, default=""):
    text = str(value if value is not None else default)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    # Discovery metadata must never export authenticated URLs/query keys.
    text = re.sub(r"https?://\S+", "[remote media]", text)
    return text[:200]


def _record(value):
    if not isinstance(value, dict):
        raise RecordingASRError("invalid_discovery_record")
    source_id = value.get("source_id")
    if not isinstance(source_id, str) or not re.fullmatch(r"sjtu-video:[1-9][0-9]{0,18}:[1-9][0-9]{0,18}", source_id):
        raise RecordingASRError("invalid_recording_source_id")
    result = {key: _clean(value.get(key)) for key in ("course_id", "course_name", "title", "term_name")}
    result["source_id"] = source_id
    result["course_id"] = source_id.split(":")[1]
    result["title"] = result["title"] or "课程录像"
    return result


def _has_text(value):
    if not isinstance(value, dict):
        raise RecordingASRError("subtitle_check_failed")
    if any(isinstance(value.get(k), str) and value[k].strip() for k in ("cached_text", "source_text", "raw_text", "text")):
        return True
    raw = value.get("vtt")
    if isinstance(raw, str) and raw.strip():
        from sjtu_learning_assistant.transcript_notes import parse_vtt
        try:
            if any(cue.text.strip() for cue in parse_vtt(raw)):
                return True
        except Exception:
            raise RecordingASRError("subtitle_validation_failed") from None
    if value.get("status") not in {"empty", "processing", "missing"}:
        raise RecordingASRError("subtitle_check_inconclusive")
    return False


def _request_audio(*, path, api_key, endpoint, model):
    """Return raw HTTP result without JSON/status validation; no automatic retries."""
    endpoint = validate_ai_base_url(endpoint)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as audio:
        if not stat.S_ISREG(os.fstat(audio.fileno()).st_mode):
            raise RecordingASRError("unsafe_audio_file")
        with httpx.Client(timeout=httpx.Timeout(1200, connect=20, read=1200, write=120),
                          follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", endpoint,
                               headers={"Authorization": "Bearer " + api_key},
                               files={"file": ("lecture.m4a", audio, "audio/mp4")},
                               data={"model": model}) as response:
                pieces = []
                size = 0
                for piece in response.iter_bytes():
                    size += len(piece)
                    if size > MAX_RESPONSE_BYTES:
                        raise RecordingASRError("response_size_limit_outcome_unknown")
                    pieces.append(piece)
                return response.status_code, b"".join(pieces)


class RecordingASRService:
    def __init__(self, *, settings, discover_sources, fetch_subtitles, fetch_playback,
                 root: Path, markdown_root, on_updated=None, extractor=None,
                 request_runner=None, key_provider=None, runtime_factory=None):
        self.settings, self.discover_sources = settings, discover_sources
        self.fetch_subtitles, self.fetch_playback = fetch_subtitles, fetch_playback
        self.root = Path(root).absolute()
        self.markdown_root, self.on_updated = markdown_root, on_updated
        self.extractor = extractor or prepare_recording_audio
        self.request_runner, self.key_provider = request_runner or _request_audio, key_provider or get_asr_api_key
        self.runtime_factory = runtime_factory or SharedAIRuntime
        self._guard = threading.RLock()
        self._stop, self._wake = threading.Event(), threading.Event()
        self._thread = None
        self._owns_worker = False
        self._closed = threading.Event()
        self._drain_error = None
        _safe_path(self.root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "jobs").mkdir(exist_ok=True, mode=0o700)
        with self._locked():
            if not (self.root / "manifest.json").exists():
                self._put_manifest({"version": 1, "consent": False, "paused": True,
                                    "scan_requested": False, "account_blocked": False,
                                    "scan_error": None, "errors": 0, "discovered": 0,
                                    "authorized_markdown_roots": []})
            manifest = self._manifest()
            enabled, paused = self._settings_state()
            if not enabled or paused:
                manifest["paused"] = True
                self._put_manifest(manifest)
        if manifest["consent"] and not manifest["paused"]:
            self._ensure_worker()

    def _settings_state(self):
        try:
            settings = self.settings()
            return (getattr(settings, "recording_asr_enabled", True) is True,
                    getattr(settings, "recording_asr_paused", False) is not False)
        except Exception:
            return False, True

    def _require_enabled(self):
        enabled, paused = self._settings_state()
        if not enabled:
            raise RecordingASRError("recording_asr_disabled_in_settings")
        if paused:
            raise RecordingASRError("recording_asr_paused_in_settings")
        if self._stop.is_set():
            raise RecordingASRError("service_closed")

    def _pin_markdown_root(self, job, *, locked=False):
        # Register only the actual configured callback, never a job-supplied
        # path. Previously authorized roots survive legitimate archive changes.
        configured = Path(self.markdown_root()).absolute()
        _safe_path(configured)
        if configured.name != "Knowledge Markdown":
            raise RecordingASRError("invalid_configured_markdown_root")
        selected = Path(job.get("markdown_root") or configured)
        if not selected.is_absolute() or selected.name != "Knowledge Markdown":
            raise RecordingASRError("unauthorized_markdown_root")
        _safe_path(selected)
        def authorize():
            manifest = self._manifest()
            roots = manifest.get("authorized_markdown_roots", [])
            if not isinstance(roots, list) or any(not isinstance(root, str) for root in roots):
                raise RecordingASRError("invalid_markdown_root_registry")
            if str(configured) not in roots:
                roots = [*roots, str(configured)]
                manifest["authorized_markdown_roots"] = roots
                self._put_manifest(manifest)
            if str(selected) not in roots:
                raise RecordingASRError("unauthorized_markdown_root")
            job["markdown_root"] = str(selected)
        if locked:
            authorize()
        else:
            with self._locked():
                authorize()
        return selected


    def settings_changed(self):
        # Settings changes never reset counters, change an active destination,
        # revoke retained responses, or implicitly restart the paid queue.
        return self.pause()

    def notify(self, *, settings_changed=False):
        if settings_changed:
            return self.settings_changed()
        enabled, paused = self._settings_state()
        if not enabled or paused:
            self.pause()
        if not self._stop.is_set():
            return self.scan()
        return self.status()

    @contextmanager
    def _locked(self):
        with self._guard:
            path = self.root / "state.lock"
            _safe_path(path)
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            held = False
            try:
                _lock_fd(fd)
                held = True
                yield
            finally:
                try:
                    if held:
                        _unlock_fd(fd)
                finally:
                    os.close(fd)

    def _manifest(self):
        return json.loads(_read(self.root / "manifest.json", 1_000_000))

    def _put_manifest(self, value):
        _json_write(self.root / "manifest.json", value)

    def _directory(self, source_id):
        return self.root / "jobs" / _hash(source_id.encode())[:16]

    def _save(self, job):
        job["updated_at"] = time.time()
        with self._locked():
            _json_write(self._directory(job["source_id"]) / "job.json", job)
        if self.on_updated:
            try:
                self.on_updated()
            except Exception:
                pass

    def _load_jobs(self):
        jobs = []
        for path in sorted((self.root / "jobs").glob("*/job.json")):
            try:
                job = json.loads(_read(path, 2_000_000))
                _record(job)
                if (not isinstance(job.get("state"), str) or type(job.get("attempts")) is not int
                        or not 0 <= job["attempts"] <= MAX_ATTEMPTS or type(job.get("request_count")) is not int
                        or job["request_count"] < 0 or not isinstance(job.get("chunks"), list)
                        or len(job["chunks"]) > 8 or any(not isinstance(c, dict) for c in job["chunks"])):
                    raise RecordingASRError("invalid_job_checkpoint_schema")
                if path.parent != self._directory(job["source_id"]):
                    raise RecordingASRError("job_identity_conflict")
                jobs.append(job)
            except Exception:
                # Never overwrite a corrupt checkpoint or pretend it completed.
                jobs.append({"source_id": "", "state": "checkpoint_error", "error": "unreadable_job_checkpoint", "attempts": 0})
        return jobs

    def _ensure_worker(self):
        with self._guard:
            if self._stop.is_set():
                raise RecordingASRError("service_closed")
            if self._thread is None or not self._thread.is_alive():
                self._closed.clear()
                self._thread = threading.Thread(target=self._loop, name="recording-asr", daemon=False)
                self._thread.start()
        self._wake.set()

    def scan(self):
        with self._locked():
            manifest = self._manifest()
            manifest["scan_requested"] = True
            self._put_manifest(manifest)
        self._ensure_worker()
        return self.status()

    def start(self, consent: bool):
        if consent is not True:
            raise RecordingASRError("explicit_asr_upload_consent_required")
        self._require_enabled()
        with self._locked():
            manifest = self._manifest()
            manifest.update(consent=True, paused=False, scan_requested=True)
            self._put_manifest(manifest)
        self._ensure_worker()
        return self.status()

    def pause(self):
        with self._locked():
            manifest = self._manifest()
            manifest["paused"] = True
            self._put_manifest(manifest)
        self._wake.set()
        return self.status()

    def resume(self):
        self._require_enabled()
        with self._locked():
            manifest = self._manifest()
            if not manifest["consent"]:
                raise RecordingASRError("explicit_asr_upload_consent_required")
            manifest["paused"] = False
            self._put_manifest(manifest)
        self._ensure_worker()
        return self.status()

    def _cancelled(self):
        with self._locked():
            manifest = self._manifest()
        enabled, paused = self._settings_state()
        return self._stop.is_set() or not enabled or paused or manifest["paused"] or not manifest["consent"] or manifest["account_blocked"]

    @staticmethod
    def _preview(job):
        keys = ("source_id", "course_id", "course_name", "title", "term_name", "state", "error",
                "attempts", "request_count", "text_chars", "markdown_relative", "updated_at")
        result = {k: job.get(k) for k in keys}
        chunks = job.get("chunks", [])
        result["chunk_count"] = len(chunks)
        result["completed_chunks"] = sum(c.get("state") == "completed" for c in chunks)
        result["outcome_unknown"] = any(c.get("state") == "outcome_unknown" for c in chunks)
        result["can_retry"] = job.get("state") not in {"completed", "skipped_source_text", "transcribing", "extracting", "checking_source"}
        result["attempt_budget_remaining"] = max(0, MAX_ATTEMPTS - int(job.get("attempts", 0)))
        return result

    def jobs(self, limit=100):
        limit = max(0, min(1000, int(limit)))
        with self._locked():
            jobs = self._load_jobs()
        return [self._preview(j) for j in sorted(jobs, key=lambda j: j.get("updated_at", 0), reverse=True)[:limit]]

    def status(self):
        with self._locked():
            manifest = self._manifest()
            jobs = self._load_jobs()
        counts = dict(Counter(j["state"] for j in jobs))
        counts["total"] = len(jobs)
        counts["requests"] = sum(j.get("request_count", 0) for j in jobs)
        active = next((self._preview(j) for j in jobs if j["state"] in {"checking_source", "extracting", "transcribing", "publishing"}), None)
        error = manifest.get("scan_error") or next((j.get("error") for j in jobs if j.get("error")), None)
        enabled, settings_paused = self._settings_state()
        draining = self._stop.is_set() and self._thread is not None and self._thread.is_alive()
        state = ("draining" if draining else "closed" if self._stop.is_set() else "disabled" if not enabled
                 else "blocked_account" if manifest["account_blocked"] else "paused" if manifest["paused"] or settings_paused
                 else "awaiting_consent" if not manifest["consent"] else "discovering" if manifest.get("scanning")
                 else "running" if active or any(j["state"] in RUNNABLE for j in jobs)
                 else "error" if error else "idle")
        return {"state": state, "consent": manifest["consent"], "enabled": enabled,
                "paused": manifest["paused"] or settings_paused, "draining": draining,
                "safe_to_exit": self._stop.is_set() and not draining and self._drain_error is None,
                "drain_error": self._drain_error,
                "account_blocked": manifest["account_blocked"], "counts": counts,
                "active": active, "jobs_preview": [self._preview(j) for j in jobs[:10]],
                "error": error, "discovery_error": manifest.get("scan_error"),
                "discovered": manifest.get("discovered", 0), "scan_requested": manifest.get("scan_requested", False)}

    def busy(self):
        state = self.status()
        return state["active"] is not None or state["state"] in {"running", "discovering"}

    def retry(self, source_id, confirm_unknown=False):
        _record({"source_id": source_id})
        with self._locked():
            path = self._directory(source_id) / "job.json"
            job = json.loads(_read(path))
            if job["state"] in {"extracting", "checking_source", "transcribing", "publishing", "importing"}:
                raise RecordingASRError("job_busy")
            if job["state"] in {"completed", "skipped_source_text"}:
                return self._preview(job)
            for chunk in job.get("chunks", []):
                self._restore_saved_response(job, chunk)
            unknown = [c for c in job.get("chunks", []) if c.get("state") == "outcome_unknown"]
            if unknown and confirm_unknown is not True:
                raise RecordingASRError("confirm_unknown_may_repeat_charge_required")
            chunks = job.get("chunks", [])
            needs_network = not chunks or any(c.get("state") != "completed" and not (
                c.get("state") == "response_saved" and c.get("history", [{}])[-1].get("http_status") == 200
            ) for c in chunks)
            if needs_network and (job.get("attempts", 0) >= MAX_ATTEMPTS or
                    any(c.get("attempts", 0) >= MAX_ATTEMPTS for c in job.get("chunks", []) if c.get("state") != "completed")):
                raise RecordingASRError("lifetime_attempt_budget_exhausted")
            for chunk in job.get("chunks", []):
                if chunk.get("state") in {"failed", "outcome_unknown"}:
                    chunk["state"] = "pending"
            job.update(state="queued", error=None, retry_round=needs_network,
                       unknown_confirmed_at=time.time() if unknown else None)
            _json_write(path, job)
            manifest = self._manifest()
            # Explicit retry after recharge only; start/resume/scan never clear 402.
            if job.get("http_status") == 402:
                manifest["account_blocked"] = False
            self._put_manifest(manifest)
        self._ensure_worker()
        return self.status()

    def close(self, timeout=2):
        # Non-daemon thread + retained flock protect successful paid response
        # drain. Native Quit/deployment must call wait_closed(timeout=None).
        self._stop.set()
        self._wake.set()
        safe = self.wait_closed(timeout=timeout)
        draining = self._thread is not None and self._thread.is_alive()
        return {"state": "draining" if draining else "closed" if safe else "drain_error",
                "draining": draining, "safe_to_exit": safe, "error": self._drain_error}

    def wait_closed(self, timeout=None):
        thread = self._thread
        if thread is threading.current_thread():
            return False
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                return False
        self._closed.set()
        return self._drain_error is None

    def _loop(self):
        path = self.root / "worker.lock"
        _safe_path(path)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        held = False
        try:
            while not self._stop.is_set():
                try:
                    _lock_fd(fd, blocking=False)
                    held = True
                    break
                except BlockingIOError:
                    self._wake.wait(0.2)
                    self._wake.clear()
            else:
                return
            self._owns_worker = True
            self._recover()
            while not self._stop.is_set():
                self._wake.clear()
                with self._locked():
                    manifest = self._manifest()
                    scan = manifest.get("scan_requested")
                    if scan:
                        manifest.update(scan_requested=False, scanning=True)
                        self._put_manifest(manifest)
                if scan:
                    self._discover()
                if not self._cancelled():
                    with self._locked():
                        job = next((j for j in self._load_jobs() if j["state"] in RUNNABLE), None)
                    if job:
                        with self._locked():
                            latest = json.loads(_read(self._directory(job["source_id"]) / "job.json"))
                            if latest["state"] not in RUNNABLE:
                                continue
                            job = latest
                            try:
                                self._pin_markdown_root(job, locked=True)
                            except Exception as exc:
                                job.update(state="failed", error=self._error_code(exc))
                                _json_write(self._directory(job["source_id"]) / "job.json", job)
                                continue
                            job.update(state="checking_source", claim={"pid": os.getpid(), "started_at": time.time()})

                            _json_write(self._directory(job["source_id"]) / "job.json", job)
                        try:
                            self._process(job)
                        except Exception as exc:
                            job.update(state="publication_error" if self._all_paid(job) else "failed",
                                       error=self._error_code(exc))
                            self._save(job)
                        continue
                self._wake.wait(0.25)
        except Exception:
            self._drain_error = "worker_storage_error"
            with self._locked():
                manifest = self._manifest()
                manifest["scan_error"] = "worker_storage_error"
                self._put_manifest(manifest)
        finally:
            self._owns_worker = False
            try:
                if held:
                    _unlock_fd(fd)
            finally:
                os.close(fd)
                self._closed.set()

    @staticmethod
    def _error_code(exc):
        if isinstance(exc, (RecordingASRError, RecordingAudioError)):
            value = str(exc)
            if re.fullmatch(r"[a-z_0-9]{1,100}", value):
                return value
        return "local_or_source_operation_failed"

    def _restore_saved_response(self, job, chunk):
        if chunk.get("state") not in {"sending", "outcome_unknown"} or not chunk.get("history"):
            return
        attempt = chunk["history"][-1]
        response = self._directory(job["source_id"]) / attempt["response_file"]
        sidecar = response.with_name(response.name + ".json")
        if response.exists() and sidecar.exists():
            saved = json.loads(_read(sidecar))
            if (saved.get("attempt") == attempt["attempt"] and saved.get("response_file") == attempt["response_file"]
                    and saved.get("http_status") == 200 and saved.get("response_sha256") == _hash(_read(response))):
                attempt.update(saved)
                chunk["state"] = "response_saved"

    def _recover(self):
        with self._locked():
            jobs = self._load_jobs()
            manifest = self._manifest()
            manifest["scanning"] = False
            if any(j.get("state") == "blocked_account" for j in jobs):
                manifest["account_blocked"] = True
            self._put_manifest(manifest)
        for job in jobs:
            if not job["source_id"]:
                continue
            try:
                dirty = False
                for chunk in job.get("chunks", []):
                    if chunk.get("state") == "sending":
                        attempt = chunk["history"][-1]
                        response = self._directory(job["source_id"]) / attempt["response_file"]
                        sidecar = response.with_name(response.name + ".json")
                        if sidecar.exists():
                            saved = json.loads(_read(sidecar))
                            if saved.get("attempt") == attempt["attempt"] and saved.get("response_file") == attempt["response_file"]:
                                attempt.update(saved)
                        if response.exists() and attempt.get("http_status") is not None:
                            chunk["state"] = "response_saved"
                        else:
                            chunk["state"] = "outcome_unknown"
                            attempt["outcome"] = "outcome_unknown"
                        dirty = True
                if job["state"] in {"transcribing", "extracting", "checking_source", "publishing", "importing"} or dirty:
                    unknown = any(c.get("state") == "outcome_unknown" for c in job.get("chunks", []))
                    job.update(state="outcome_unknown" if unknown else "queued",
                               error="request_outcome_unknown" if unknown else None)
                    self._save(job)
            except Exception:
                for chunk in job.get("chunks", []):
                    if chunk.get("state") == "sending":
                        chunk["state"] = "outcome_unknown"
                job.update(state="outcome_unknown", error="checkpoint_recovery_error")
                self._save(job)

    def _discover(self):
        errors, found, seen = [], 0, set()
        try:
            for raw in self.discover_sources():
                if self._stop.is_set():
                    break
                try:
                    record = _record(raw)
                    source_id = record["source_id"]
                    if source_id in seen:
                        continue
                    seen.add(source_id)
                    found += 1
                    with self._locked():
                        path = self._directory(source_id) / "job.json"
                        if path.exists():
                            # Discovery never resets counters, failures or edited artifacts.
                            continue
                        job = {**record, "state": "queued", "attempts": 0, "request_count": 0,
                               "chunks": [], "error": None, "created_at": time.time(), "updated_at": time.time()}
                        if any(isinstance(raw.get(k), str) and raw[k].strip() for k in ("cached_text", "source_text", "raw_text")):
                            job["state"] = "skipped_source_text"
                        _json_write(path, job)
                except Exception:
                    errors.append("recording_discovery_error")
        except Exception:
            errors.append("source_discovery_failed")
        with self._locked():
            manifest = self._manifest()
            manifest.update(scanning=False, discovered=found, scanned_at=time.time(),
                            scan_error=errors[0] if errors else None,
                            errors=manifest.get("errors", 0) + len(errors))
            self._put_manifest(manifest)

    @staticmethod
    def _all_paid(job):
        chunks = job.get("chunks", [])
        return bool(chunks) and all(c.get("state") == "completed" for c in chunks)

    def _validate_audio(self, directory, chunk):
        filename = chunk["file"]
        if Path(filename).name != filename or filename in {".", ".."}:
            raise RecordingASRError("unsafe_chunk_path")
        metadata = file_metadata(directory / filename, chunk["duration_seconds"],
                                 max_bytes=MAX_CHUNK_BYTES, max_seconds=MAX_CHUNK_SECONDS)
        if metadata["sha256"] != chunk["sha256"] or metadata["bytes"] != chunk["bytes"]:
            raise RecordingASRError("retained_audio_hash_mismatch")

    def _process(self, job):
        directory = self._directory(job["source_id"])
        if not job.get("markdown_root"):
            self._pin_markdown_root(job)
            self._save(job)
        # Drain retained success even if source checks/network/keys are unavailable.
        for chunk in job.get("chunks", []):
            if chunk.get("state") == "response_saved":
                self._consume_response(job, chunk)
                if chunk["state"] != "completed":
                    return
        if self._all_paid(job):
            self._publish(job)
            return
        if any(c.get("state") == "outcome_unknown" for c in job.get("chunks", [])):
            job.update(state="outcome_unknown", error="request_outcome_unknown")
            self._save(job)
            return
        if self._cancelled():
            job.update(state="queued", error=None)
            self._save(job)
            return
        job["state"] = "checking_source"
        self._save(job)
        if _has_text(self.fetch_subtitles(job["source_id"])):
            job.update(state="skipped_source_text", error=None)
            self._save(job)
            return
        if not job.get("chunks"):
            job["state"] = "extracting"
            self._save(job)
            playback = self.fetch_playback(job["source_id"])
            prepared = self.extractor(playback, directory)
            chunks = prepared["chunks"]
            if not 1 <= len(chunks) <= 8:
                raise RecordingASRError("chunk_count_limit")
            job["audio"] = prepared["audio"]
            job["chunks"] = [{**c, "index": i, "attempts": 0, "history": [], "state": "pending"} for i, c in enumerate(chunks)]
            for chunk in job["chunks"]:
                self._validate_audio(directory, chunk)
            self._save(job)
        if self._cancelled():
            job.update(state="queued", error=None)
            self._save(job)
            return
        key = self.key_provider()
        if not key:
            raise RecordingASRError("independent_asr_key_required")
        if not job.get("model"):
            settings = self.settings()
            job["model"] = validate_asr_model(getattr(settings, "recording_asr_model", None) or getattr(settings, "asr_model", None) or DEFAULT_MODEL)
        endpoint = validate_ai_base_url(getattr(self.settings(), "asr_endpoint", None) or ENDPOINT)
        job["asr_endpoint"] = endpoint
        runtime = self.runtime_factory(self.root.parent / "ai-runtime.sqlite3",
                                       "asr:" + _hash((urlsplit(endpoint).netloc.lower() + ":" + key).encode()),

                                       rpm=10, tpm=300000, max_inflight=1)
        round_started = False
        for chunk in job["chunks"]:
            if chunk["state"] == "completed":
                continue
            if self._cancelled():
                job.update(state="queued", error=None)
                self._save(job)
                return
            # Remote/cached source may appear while extracting or waiting on quota.
            if _has_text(self.fetch_subtitles(job["source_id"])):
                job.update(state="skipped_source_text", error=None)
                self._save(job)
                return
            self._validate_audio(directory, chunk)
            if chunk["attempts"] >= MAX_ATTEMPTS:
                raise RecordingASRError("lifetime_attempt_budget_exhausted")
            usage = {}
            body = {"model": job["model"], "max_tokens": min(299000, max(8192, int(chunk["duration_seconds"] * 60))), "messages": []}
            try:
                with runtime.request(body, usage, cancelled=self._cancelled):
                    if self._cancelled():
                        job.update(state="queued", error=None)
                        self._save(job)
                        return
                    # Recheck right before the paid POST, after quota reservation.
                    if _has_text(self.fetch_subtitles(job["source_id"])):
                        job.update(state="skipped_source_text", error=None)
                        self._save(job)
                        return
                    if not round_started and (not job["attempts"] or job.get("retry_round")):
                        if job["attempts"] >= MAX_ATTEMPTS:
                            raise RecordingASRError("lifetime_attempt_budget_exhausted")
                        job["attempts"] += 1
                        job["retry_round"] = False
                    round_started = True
                    chunk["attempts"] += 1
                    job["request_count"] += 1
                    attempt = {"attempt": chunk["attempts"], "started_at": time.time(), "outcome": "sending",
                               "response_file": f"chunk-{chunk['index']:03d}-attempt-{chunk['attempts']}.response"}
                    chunk["history"].append(attempt)
                    chunk["state"] = "sending"
                    job.update(state="transcribing", error=None, claim={"pid": os.getpid(), "started_at": time.time()})
                    self._save(job)  # Durable lifetime budget BEFORE any network.
                    try:
                        status, raw = self.request_runner(path=directory / chunk["file"], api_key=key,
                                                          endpoint=endpoint, model=job["model"])
                        if not isinstance(raw, bytes):
                            raise RecordingASRError("raw_response_not_bytes")
                        # The paid body is saved BEFORE HTTP, JSON, schema or publication validation.
                        _atomic(directory / attempt["response_file"], raw, immutable=True)
                        attempt.update(http_status=int(status), response_sha256=_hash(raw), outcome="response_saved")
                        # Sidecar closes crash window between body save and job update.
                        _json_write(directory / (attempt["response_file"] + ".json"), attempt, immutable=True)
                        chunk["state"] = "response_saved"
                        self._save(job)
                    except Exception:
                        chunk["state"] = "outcome_unknown"
                        attempt["outcome"] = "outcome_unknown"
                        job.update(state="outcome_unknown", error="request_outcome_unknown")
                        self._save(job)
                        return
                    self._consume_response(job, chunk)
                    if status == 429:
                        raise httpx.HTTPStatusError("asr_rate_limited", request=httpx.Request("POST", endpoint),
                                                    response=httpx.Response(429))
            except Exception:
                if chunk["state"] in {"failed", "outcome_unknown"}:
                    return
                if chunk["state"] == "pending" and self._cancelled():
                    job.update(state="queued", error=None)
                    self._save(job)
                    return
                raise
            if chunk["state"] != "completed":
                return
        self._publish(job)

    def _consume_response(self, job, chunk):
        directory = self._directory(job["source_id"])
        attempt = chunk["history"][-1]
        raw = _read(directory / attempt["response_file"])
        if _hash(raw) != attempt["response_sha256"]:
            raise RecordingASRError("paid_response_hash_mismatch")
        status = attempt["http_status"]
        job["http_status"] = status
        if status != 200:
            chunk["state"] = "failed"
            attempt["outcome"] = "http_" + str(status)
            job.update(state="blocked_account" if status == 402 else "failed", error="asr_http_" + str(status))
            self._save(job)
            if status == 402:
                with self._locked():
                    manifest = self._manifest()
                    manifest["account_blocked"] = True
                    self._put_manifest(manifest)
            return
        try:
            response = json.loads(raw)
            text = response.get("text") if isinstance(response, dict) else None
            if not isinstance(text, str) or not text.strip() or len(text) > 1_000_000:
                raise ValueError()
        except (ValueError, UnicodeError):
            chunk["state"] = "failed"
            attempt["outcome"] = "invalid_paid_response"
            job.update(state="failed", error="invalid_paid_response")
            self._save(job)
            return
        result_file = f"chunk-{chunk['index']:03d}-attempt-{chunk['attempts']}.text.json"
        _json_write(directory / result_file, {"text": text, "model": job["model"]}, immutable=True)
        chunk.update(state="completed", result_file=result_file, text_sha256=_hash(text.encode()), text_chars=len(text))
        attempt["outcome"] = "completed"
        self._save(job)

    def _publish(self, job):
        directory = self._directory(job["source_id"])
        pieces = []
        for chunk in job["chunks"]:
            result = json.loads(_read(directory / chunk["result_file"]))
            text = result["text"]
            if _hash(text.encode()) != chunk["text_sha256"]:
                raise RecordingASRError("retained_text_hash_mismatch")
            pieces.append(text)
        text = "\n".join(pieces)
        _json_write(directory / "asr-result.json", {"text": text, "model": job["model"], "source_id": job["source_id"]}, immutable=True)
        job.update(state="publishing", text_chars=len(text), text_sha256=_hash(text.encode()))
        self._save(job)
        root = self._pin_markdown_root(job)
        self._save(job)
        def leaf(value):
            return re.sub(r"[^\w一-鿿 .-]", "_", _clean(value)).strip(" .")[:90] or "课程"
        relative = Path("Recordings ASR") / leaf(job.get("term_name")) / (leaf(job.get("course_name")) + "-" + job["course_id"]) / (leaf(job["title"]) + "-" + job["source_id"].split(":")[-1] + ".md")
        # Once published, future metadata changes cannot orphan/overwrite edits.
        if job.get("markdown_relative"):
            relative = Path(job["markdown_relative"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "Recordings ASR":
            raise RecordingASRError("unsafe_markdown_path")
        destination = root / relative
        _safe_path(destination)
        title = _clean(job["title"]).replace("#", "")
        metadata = {"source_type": "machine_transcript", "source_id": job["source_id"],
                    "course_id": job["course_id"], "course_name": job.get("course_name"),
                    "asr_model": job["model"], "timestamps": "none", "timed": False,
                    "text_sha256": job["text_sha256"], "text_chars": len(text),
                    "duration_seconds": job.get("audio", {}).get("duration_seconds")}
        # No 5000-character summary cap and no invented timing/corrections.
        content = ("---\n" + "\n".join(k + ": " + json.dumps(v, ensure_ascii=False) for k, v in metadata.items()) +
                   "\n---\n\n# " + title + "\n\n机器转写原始全文（无时间戳 / no timestamps）；未经校对，不作为同步字幕。\n\n" + text + "\n").encode()
        if destination.exists() and _hash(_read(destination)) != _hash(content):
            job.update(state="user_edit_protected", error="markdown_user_edit_protected")
            self._save(job)
            return
        try:
            # Never replace an existing Markdown file, including an editor race.
            _atomic(destination, content, immutable=True)
        except RecordingASRError as exc:
            if str(exc) != "existing_checkpoint_conflict":
                raise
            job.update(state="user_edit_protected", error="markdown_user_edit_protected")
            self._save(job)
            return
        job.update(state="completed", error=None, markdown_relative=relative.as_posix(),
                   markdown_sha256=_hash(content), completed_at=time.time())
        self._save(job)

    def import_completed_checkpoint(self, path):
        """Import a generic completed single-audio checkpoint without ASR/key access.

        Identity/model/length/audio/response integrity come from the manifest,
        never a hardcoded course. Original files and lifetime counts are kept.
        """
        source = Path(path).absolute()
        original_bytes = _read(source / "job.json")
        original = json.loads(original_bytes)
        if not isinstance(original, dict):
            raise RecordingASRError("recovery_identity_mismatch")
        try:
            record = _record({**original, "course_name": original.get("course_name") or original.get("course"),
                              "title": original.get("title") or (str(original.get("recorded_date") or "") + " 课程录像")})
            count = original["asr_attempts"]
            model = validate_asr_model(original["asr_model"])
            if type(count) is not int or not 1 <= count <= MAX_ATTEMPTS or original.get("asr_http_status") != 200:
                raise ValueError()
            if type(original.get("text_chars")) is not int or not 0 < original["text_chars"] <= 1_000_000:
                raise ValueError()
            if original.get("recording_id") is not None and str(original["recording_id"]) != record["source_id"].split(":")[-1]:
                raise ValueError()
        except Exception:
            raise RecordingASRError("recovery_identity_mismatch") from None
        def filename(field, default):
            value = original.get(field) or default
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", value) or value in {".", ".."}:
                raise RecordingASRError("unsafe_recovery_filename")
            return value
        audio_file = filename("audio_file", "source-audio.m4a")
        result_file = filename("asr_result_file", "asr-result.json")
        response_file = filename("asr_response_file", f"asr-attempt-{count}.response")
        names = {audio_file: "audio_sha256", result_file: "asr_result_sha256", response_file: "asr_response_sha256"}
        if len(names) != 3 or not audio_file.endswith(".m4a"):
            raise RecordingASRError("unsafe_recovery_filename")
        payloads = {}
        for name, field in names.items():
            expected = original.get(field)
            if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise RecordingASRError("recovery_manifest_hash_missing")
            data = _read(source / name, MAX_CHUNK_BYTES if name == audio_file else MAX_RESPONSE_BYTES)
            if _hash(data) != expected:
                raise RecordingASRError("recovery_original_hash_mismatch")
            payloads[name] = data
        audio = file_metadata(source / audio_file, original["duration_seconds"],
                              max_bytes=MAX_CHUNK_BYTES, max_seconds=MAX_CHUNK_SECONDS)
        if audio["bytes"] != original.get("audio_bytes"):
            raise RecordingASRError("recovery_audio_size_mismatch")
        result = json.loads(payloads[result_file])
        paid = json.loads(payloads[response_file])
        text = result.get("text") if isinstance(result, dict) else None
        if (not isinstance(paid, dict) or not isinstance(text, str) or not text.strip()
                or text != paid.get("text") or len(text) != original["text_chars"] or result.get("model") != model
                or result.get("source_id", record["source_id"]) != record["source_id"]
                or result.get("audio_sha256", audio["sha256"]) != audio["sha256"]):
            raise RecordingASRError("recovery_text_mismatch")
        history = []
        priors = original.get("asr_attempt_history", [])
        if not isinstance(priors, list) or len(priors) != count - 1:
            raise RecordingASRError("recovery_attempt_history_mismatch")
        for number, prior in enumerate(priors, 1):
            if not isinstance(prior, dict) or prior.get("attempt") != number:
                raise RecordingASRError("recovery_attempt_history_mismatch")
            name = prior.get("response_file")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", name) or name in payloads:
                raise RecordingASRError("recovery_attempt_history_mismatch")
            data = _read(source / name)
            expected = prior.get("response_sha256")
            if expected is not None and expected != _hash(data):
                raise RecordingASRError("recovery_original_hash_mismatch")
            payloads[name] = data
            status = prior.get("http_status")
            if status is not None and (type(status) is not int or not 100 <= status <= 599):
                raise RecordingASRError("recovery_attempt_history_mismatch")
            history.append({"attempt": number, "http_status": status, "response_file": name,
                            "response_sha256": _hash(data), "outcome": "http_" + str(status) if status else "outcome_unknown"})
        history.append({"attempt": count, "http_status": 200, "response_file": response_file,
                        "response_sha256": original["asr_response_sha256"], "outcome": "response_saved"})
        directory = self._directory(record["source_id"])
        with self._locked():
            existing = json.loads(_read(directory / "job.json")) if (directory / "job.json").exists() else None
            if existing and existing["state"] in {"transcribing", "checking_source", "extracting", "publishing", "importing"}:
                raise RecordingASRError("job_busy")
            reuse = bool(existing and existing.get("chunks"))
            if reuse and (existing.get("import_proof") != original["asr_response_sha256"]
                          or existing.get("model") != model or existing.get("audio", {}).get("sha256") != audio["sha256"]):
                raise RecordingASRError("existing_paid_job_import_conflict")
            job = existing or {**record, "created_at": time.time(), "attempts": count, "request_count": count}
            self._pin_markdown_root(job, locked=True)
            if reuse and job["state"] == "completed":
                return self._preview(job)
            for name, data in payloads.items():
                destination = directory / ("imported-asr-result.json" if name == result_file else name)
                _atomic(destination, data, immutable=True)
            _atomic(directory / "imported-job.json", original_bytes, immutable=True)
            if reuse:
                # Local republish retains every paid chunk/history/counter. The
                # durable importing claim excludes worker/parallel import races.
                job.update(state="importing", error=None)
            else:
                job.update(state="importing", model=model, audio=audio,
                           attempts=max(count, job.get("attempts", 0)), request_count=max(count, job.get("request_count", 0)),
                           error=None, import_proof=original["asr_response_sha256"],
                           chunks=[{**audio, "index": 0, "attempts": count, "history": history, "state": "response_saved"}])
            _json_write(directory / "job.json", job)
        try:
            for chunk in job["chunks"]:
                # Reconstruct missing local text from a retained paid response;
                # immutable writes still preserve edits/corrupt originals.
                self._consume_response(job, chunk)
                if chunk["state"] != "completed":
                    return self._preview(job)
            self._publish(job)
        except Exception as exc:
            job.update(state="publication_error", error=self._error_code(exc))
            self._save(job)
        return self._preview(job)
