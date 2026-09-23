"""Incremental, path-safe backup of local Canvas and mail files to SJTU Pan."""

from __future__ import annotations

import copy
import hashlib
import os
import stat
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator, Literal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.cloud_storage import (
    CloudConflictError,
    CloudStorageProvider,
    SJTUCloudPanProvider,
    load_user_token,
)
from sjtu_learning_assistant.models import Course, CourseFile, Email, EmailAttachment

BACKUP_ROOT = "SJTU Learning Assistant"
DEFAULT_MULTIPART_THRESHOLD = 8 * 1024 * 1024
MAX_COMPONENT_LENGTH = 120
MAX_FILENAME_LENGTH = 200
MAX_FAILURE_DETAILS = 100


ProgressCallback = Callable[[dict[str, Any]], None]


class BackupError(RuntimeError):
    """A bounded, user-displayable backup error without local or remote secrets."""


@dataclass(frozen=True)
class BackupCandidate:
    key: str
    source: Literal["canvas", "mail"]
    remote_path: tuple[str, ...]
    local_path: str | None = field(repr=False)
    local_root: Path = field(repr=False)
    ready: bool

    def dto(self) -> dict[str, Any]:
        return {
            "id": self.key,
            "source": self.source,
            "remote_path": list(self.remote_path),
            "ready": self.ready,
        }


def safe_path_component(
    value: object,
    *,
    fallback: str = "未命名",
    limit: int = MAX_COMPONENT_LENGTH,
) -> str:
    """Create one bounded remote component; separators and controls never survive."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    cleaned = "".join(
        "_" if character in {"/", "\\"} or ord(character) < 32 or ord(character) == 127 else character
        for character in text
    )
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = fallback
    cleaned = cleaned[:limit].rstrip(" .") or fallback
    if cleaned in {".", ".."}:
        cleaned = fallback
    return cleaned


def stable_filename(value: object, identity: str) -> str:
    """Return a readable filename with a stable identity suffix for collision safety."""
    clean = safe_path_component(value, fallback="文件", limit=MAX_FILENAME_LENGTH)
    suffix = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:12]
    path = Path(clean)
    extension = path.suffix[:32] if path.suffix and path.name != path.suffix else ""
    stem_limit = MAX_FILENAME_LENGTH - len(extension) - len(suffix) - 2
    stem = (path.stem if extension else clean)[: max(stem_limit, 1)].rstrip(" .") or "文件"
    return f"{stem}--{suffix}{extension}"


def _candidate_key(source: str, identity: str) -> str:
    digest = hashlib.sha256(f"{source}:{identity}".encode("utf-8", errors="replace")).hexdigest()
    return digest[:20]


def _month(value: datetime | None) -> str:
    if value is None:
        return "unknown-month"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m")


def _lexical_root(root: Path) -> Path:
    return Path(os.path.abspath(os.fspath(root.expanduser())))


def _relative_local_path(root: Path, raw_path: str | None) -> tuple[Path, tuple[str, ...]]:
    if not raw_path or "\x00" in raw_path:
        raise BackupError("本地备份文件缺失或不安全。")
    lexical_root = _lexical_root(root)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = lexical_root / candidate
    lexical_candidate = Path(os.path.abspath(os.fspath(candidate)))
    try:
        relative = lexical_candidate.relative_to(lexical_root)
    except ValueError:
        raise BackupError("本地备份文件缺失或不安全。") from None
    if not relative.parts:
        raise BackupError("本地备份文件缺失或不安全。")
    return lexical_root, relative.parts


@contextmanager
def open_controlled_file(root: Path, raw_path: str | None) -> Iterator[tuple[BinaryIO, int]]:
    """Open a regular file beneath root using no-follow directory descriptors."""
    lexical_root, parts = _relative_local_path(root, raw_path)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    descriptors: list[int] = []
    stream: BinaryIO | None = None
    try:
        root_mode = lexical_root.lstat().st_mode
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            raise BackupError("本地备份文件缺失或不安全。")
        current_fd = os.open(lexical_root, directory_flags)
        descriptors.append(current_fd)
        for component in parts[:-1]:
            current_fd = os.open(component, directory_flags, dir_fd=current_fd)
            descriptors.append(current_fd)
        file_fd = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=current_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(file_fd)
            raise BackupError("本地备份文件缺失或不安全。")
        stream = os.fdopen(file_fd, "rb")
        yield stream, int(info.st_size)
    except BackupError:
        raise
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, ValueError):
        raise BackupError("本地备份文件缺失或不安全。") from None
    finally:
        if stream is not None:
            stream.close()
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _is_ready(root: Path, raw_path: str | None) -> bool:
    try:
        with open_controlled_file(root, raw_path):
            return True
    except BackupError:
        return False


def preview_counts(candidates: tuple[BackupCandidate, ...]) -> dict[str, int]:
    canvas = sum(candidate.source == "canvas" for candidate in candidates)
    mail = len(candidates) - canvas
    ready = sum(candidate.ready for candidate in candidates)
    return {
        "canvas": canvas,
        "mail": mail,
        "ready": ready,
        "missing_local": len(candidates) - ready,
        "total": len(candidates),
    }


class BackupService:
    """Scans with short-lived sessions and uploads each candidate independently."""

    def __init__(
        self,
        engine: Engine,
        provider: CloudStorageProvider | None,
        *,
        archive_root: Path,
        mail_attachments_root: Path,
        mail_account: str = "",
        multipart_threshold: int = DEFAULT_MULTIPART_THRESHOLD,
    ) -> None:
        if multipart_threshold <= 0:
            raise ValueError("分片上传阈值必须为正数。")
        self.engine = engine
        self.provider = provider
        self.archive_root = Path(archive_root)
        self.mail_attachments_root = Path(mail_attachments_root)
        self.mail_account = safe_path_component(mail_account, fallback="default")
        self.multipart_threshold = multipart_threshold

    def scan(self) -> tuple[BackupCandidate, ...]:
        """Read candidate scalar values, then close the session before filesystem work."""
        with Session(self.engine) as session:
            canvas_rows = session.execute(
                select(
                    CourseFile.source_id,
                    CourseFile.display_name,
                    CourseFile.filename,
                    CourseFile.local_path,
                    Course.term_name,
                    Course.course_code,
                    Course.name,
                )
                .join(Course, Course.id == CourseFile.course_id)
                .where(CourseFile.is_active.is_(True))
                .order_by(CourseFile.source_id)
            ).all()
            mail_rows = session.execute(
                select(
                    EmailAttachment.resource_id,
                    EmailAttachment.filename,
                    EmailAttachment.local_path,
                    Email.source_id,
                    Email.sent_at,
                    Email.received_at,
                    Email.created_at,
                )
                .join(Email, Email.id == EmailAttachment.email_id)
                .order_by(Email.source_id, EmailAttachment.resource_id)
            ).all()

        candidates: list[BackupCandidate] = []
        for row in canvas_rows:
            identity = str(row.source_id)
            filename = stable_filename(row.filename or row.display_name, identity)
            course_label = row.course_code or row.name
            remote_path = (
                BACKUP_ROOT,
                "Canvas",
                safe_path_component(row.term_name, fallback="未分学期"),
                safe_path_component(course_label, fallback="未命名课程"),
                filename,
            )
            candidates.append(
                BackupCandidate(
                    key=_candidate_key("canvas", identity),
                    source="canvas",
                    remote_path=remote_path,
                    local_path=row.local_path,
                    local_root=self.archive_root,
                    ready=_is_ready(self.archive_root, row.local_path),
                )
            )

        for row in mail_rows:
            identity = f"{row.source_id}:{row.resource_id}"
            remote_path = (
                BACKUP_ROOT,
                "Mail",
                self.mail_account,
                _month(row.sent_at or row.received_at or row.created_at),
                stable_filename(row.filename, identity),
            )
            candidates.append(
                BackupCandidate(
                    key=_candidate_key("mail", identity),
                    source="mail",
                    remote_path=remote_path,
                    local_path=row.local_path,
                    local_root=self.mail_attachments_root,
                    ready=_is_ready(self.mail_attachments_root, row.local_path),
                )
            )
        return tuple(candidates)

    def preview(self) -> dict[str, int]:
        return preview_counts(self.scan())

    def _ensure_directory(self, path: tuple[str, ...]) -> None:
        assert self.provider is not None
        ensure = getattr(self.provider, "ensure_directory", None)
        if callable(ensure):
            ensure(path)
            return
        current: list[str] = []
        for component in path:
            current.append(component)
            try:
                self.provider.create_directory(tuple(current))
            except CloudConflictError:
                continue

    def backup(
        self,
        candidates: tuple[BackupCandidate, ...] | None = None,
        *,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if self.provider is None:
            raise BackupError("交大云盘备份服务未配置。")
        selected = candidates if candidates is not None else self.scan()
        started_at = datetime.now(timezone.utc).isoformat()
        result: dict[str, Any] = {
            "started_at": started_at,
            "finished_at": None,
            "uploaded": 0,
            "skipped_existing": 0,
            "skipped_missing_local": 0,
            "failed": 0,
            "failures": [],
        }
        total = len(selected)
        done = 0
        ensured: set[tuple[str, ...]] = set()

        def report(current_name: str | None) -> None:
            if progress_callback is None:
                return
            progress_callback({"done": done, "total": total, "current_name": current_name})

        report(None)
        for candidate in selected:
            if cancel_event is not None and cancel_event.is_set():
                break
            current_name = safe_path_component(
                candidate.remote_path[-1] if candidate.remote_path else "文件",
                fallback="文件",
                limit=MAX_FILENAME_LENGTH,
            )
            report(current_name)
            try:
                if not candidate.ready:
                    result["skipped_missing_local"] += 1
                    continue
                with open_controlled_file(candidate.local_root, candidate.local_path) as (source, size):
                    directory = candidate.remote_path[:-1]
                    if directory not in ensured:
                        self._ensure_directory(directory)
                        ensured.add(directory)
                    exists = self.provider.exists(candidate.remote_path)
                    overwrite = False
                    if exists:
                        remote = self.provider.get_info(candidate.remote_path)
                        if not remote.is_directory and remote.size == size:
                            result["skipped_existing"] += 1
                            continue
                        overwrite = True
                    if size >= self.multipart_threshold:
                        self.provider.multipart_upload(
                            candidate.remote_path,
                            source,
                            overwrite=overwrite,
                        )
                    else:
                        self.provider.simple_upload(
                            candidate.remote_path,
                            source,
                            overwrite=overwrite,
                        )
                    result["uploaded"] += 1
            except BackupError:
                result["skipped_missing_local"] += 1
            except Exception:
                # Provider exceptions may contain credentials or signed URLs. Never
                # copy their text into a bridge DTO.
                result["failed"] += 1
                if len(result["failures"]) < MAX_FAILURE_DETAILS:
                    safe_source = safe_path_component(
                        candidate.source,
                        fallback="unknown",
                        limit=32,
                    )
                    safe_remote_path = "/".join(
                        safe_path_component(part, fallback="未命名")
                        for part in candidate.remote_path
                    )
                    result["failures"].append(
                        {
                            "source": safe_source,
                            "name": current_name,
                            "remote_path": safe_remote_path,
                            "error": "上传失败，请检查云盘凭据或网络连接。",
                        }
                    )
            finally:
                done += 1
                report(current_name)
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        return result


class BackupManager:
    """Thread-safe owner for one daemon backup worker and its provider lifecycle."""

    def __init__(
        self,
        engine: Engine,
        *,
        archive_root: Path,
        mail_attachments_root: Path,
        mail_account: str = "",
        provider_factory: Callable[[], CloudStorageProvider] = SJTUCloudPanProvider,
        token_loader: Callable[[], str | None] | None = None,
        multipart_threshold: int = DEFAULT_MULTIPART_THRESHOLD,
    ) -> None:
        self._engine = engine
        self._archive_root = Path(archive_root)
        self._mail_attachments_root = Path(mail_attachments_root)
        self._mail_account = mail_account
        self._provider_factory = provider_factory
        self._token_loader = token_loader or load_user_token
        self._multipart_threshold = multipart_threshold
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._state: Literal["idle", "running", "finished"] = "idle"
        self._counts = {
            "canvas": 0,
            "mail": 0,
            "ready": 0,
            "missing_local": 0,
            "total": 0,
        }
        self._progress: dict[str, Any] | None = None
        self._last_result: dict[str, Any] | None = None

    def _service(self, provider: CloudStorageProvider | None = None) -> BackupService:
        return BackupService(
            self._engine,
            provider,
            archive_root=self._archive_root,
            mail_attachments_root=self._mail_attachments_root,
            mail_account=self._mail_account,
            multipart_threshold=self._multipart_threshold,
        )

    def _availability(self) -> tuple[bool, str | None]:
        try:
            # Only retain whether a credential exists; the token never enters a DTO or
            # manager state.
            configured = self._token_loader() is not None
        except Exception:
            return False, "无法读取系统 Keychain 中的交大云盘凭据。"
        if not configured:
            return False, "尚未在系统 Keychain 中配置交大云盘 UserToken。"
        return True, None

    def status(self) -> dict[str, Any]:
        available, availability_message = self._availability()
        with self._lock:
            state = self._state
        if state in {"idle", "finished"}:
            try:
                counts = self._service().preview()
            except Exception:
                counts = None
            if counts is not None:
                with self._lock:
                    if self._state == state:
                        self._counts = counts
        with self._lock:
            return {
                "status": self._state,
                "available": available,
                "availability_message": availability_message,
                "counts": dict(self._counts),
                "progress": copy.deepcopy(self._progress),
                "last_result": copy.deepcopy(self._last_result),
            }

    def start(self) -> dict[str, str]:
        with self._lock:
            if self._closed:
                raise BackupError("备份服务已关闭。")
            if self._thread is not None and self._thread.is_alive():
                return {"status": "already_running"}
            self._cancel.clear()
            self._state = "running"
            self._progress = {
                "done": 0,
                "total": self._counts["total"],
                "current_name": None,
            }
            self._thread = threading.Thread(
                target=self._run,
                name="sjtu-cloud-backup",
                daemon=True,
            )
            self._thread.start()
            return {"status": "started"}

    def _update_progress(self, progress: dict[str, Any]) -> None:
        current_name = progress.get("current_name")
        safe_name = (
            safe_path_component(current_name, fallback="文件", limit=MAX_FILENAME_LENGTH)
            if current_name is not None
            else None
        )
        with self._lock:
            self._progress = {
                "done": max(0, int(progress.get("done", 0))),
                "total": max(0, int(progress.get("total", 0))),
                "current_name": safe_name,
            }

    def _fatal_result(self, started_at: str) -> dict[str, Any]:
        with self._lock:
            failed = self._counts["ready"] or 1
        return {
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "uploaded": 0,
            "skipped_existing": 0,
            "skipped_missing_local": 0,
            "failed": failed,
            "failures": [
                {
                    "source": "backup",
                    "name": "云盘备份",
                    "remote_path": BACKUP_ROOT,
                    "error": "备份失败，请检查云盘凭据、网络连接后重试。",
                }
            ],
        }

    def _run(self) -> None:
        provider: CloudStorageProvider | None = None
        started_at = datetime.now(timezone.utc).isoformat()
        result: dict[str, Any] | None = None
        try:
            provider = self._provider_factory()
            service = self._service(provider)
            candidates = service.scan()
            counts = preview_counts(candidates)
            with self._lock:
                self._counts = counts
                self._progress = {"done": 0, "total": len(candidates), "current_name": None}
            result = service.backup(
                candidates,
                cancel_event=self._cancel,
                progress_callback=self._update_progress,
            )
        except Exception:
            # Provider, Keychain and database exceptions can contain sensitive values.
            result = self._fatal_result(started_at)
        finally:
            if provider is not None:
                close = getattr(provider, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            with self._lock:
                if result is not None:
                    self._last_result = result
                self._state = "finished"
                self._thread = None

    def close(self, timeout: float = 10.0) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
