from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import re
import os
import stat
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from sqlalchemy import Engine, and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from sjtu_learning_assistant.cloud_storage import SJTUCloudPanProvider
from sjtu_learning_assistant.models import (
    AIManagedFile,
    ArchiveAuthorizedRoot,
    ArchiveEntry,
    ArchiveEvent,
    ArchiveJob,
    ArchiveVersion,
    CourseFile,
    EmailAttachment,
)

REMOTE_ROOT = ("SJTU Learning Assistant", "Archive")
ACTIVE_STATES = ("running", "uploading", "verifying", "downloading")
PROGRESS_MIN_BYTES = 5 * 1024 * 1024
_SENSITIVE_KEY_PARTS = ("token", "cookie", "authorization", "password", "secret")


def redact_public_text(value: str) -> str:
    home = str(Path.home())
    redacted = value.replace(home, "~") if home else value
    redacted = re.sub(
        r"(?i)\b(token|cookie|authorization|set-cookie|password|secret)\b"
        r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1\2<redacted>",
        redacted,
    )
    return re.sub(
        r"(?i)\bbearer\s+[^\s,;]+", "Bearer <redacted>", redacted
    )


def redact_public_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "<redacted>"
                if any(part in str(key).casefold() for part in _SENSITIVE_KEY_PARTS)
                else redact_public_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_public_value(item) for item in value]
    if isinstance(value, str):
        return redact_public_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_public_text(str(value))


class CloudArchiveError(RuntimeError):
    def __str__(self) -> str:
        return redact_public_text(super().__str__())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_source(value: str | Path) -> Path:
    raw = os.fspath(value)
    if not raw or "\x00" in raw or len(raw) > 4096:
        raise CloudArchiveError("本地文件路径无效。")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise CloudArchiveError("归档文件必须使用绝对路径。")
    path = Path(os.path.abspath(os.fspath(path)))
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise CloudArchiveError("归档文件不存在或无法读取。") from exc
    if stat.S_ISLNK(mode):
        raise CloudArchiveError("不允许归档符号链接。")
    if not path.is_file():
        raise CloudArchiveError("归档目标必须是普通文件。")
    return path


def get_volume_identity(path: Path) -> str:
    return f"dev:{os.stat(path, follow_symlinks=False).st_dev}"


def path_identity(path: Path, volume: str | None = None) -> str:
    identity_text = unicodedata.normalize("NFC", str(path))
    payload = (volume or get_volume_identity(path)) + "\x00" + identity_text
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_local_file(path: Path, chunk_size: int = 1048576) -> tuple[int, int, str]:
    before = path.stat()
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as source:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            count += len(chunk)
            digest.update(chunk)
    after = path.stat()
    before_key = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_key = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_key != after_key or count != after.st_size:
        raise CloudArchiveError("文件在归档读取期间发生变化，请重试。")
    return count, after.st_mtime_ns, digest.hexdigest()


def safe_filename(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("/", "_").replace("\\", "_")
    value = "".join(character for character in value if 31 < ord(character) != 127).strip(" .")
    if value in ("", ".", ".."):
        value = "file"
    encoded = value.encode("utf-8")
    if len(encoded) <= 180:
        return value
    suffix = Path(value).suffix[:24]
    prefix = encoded[:140].decode("utf-8", errors="ignore")
    return prefix + "-" + hashlib.sha256(encoded).hexdigest()[:12] + suffix


def deterministic_remote_path(entry_id: str, number: int, digest: str, name: str) -> tuple[str, ...]:
    return REMOTE_ROOT + (entry_id, f"v{number}", digest[:16], safe_filename(name))


def encode_remote_path(parts: tuple[str, ...]) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def decode_remote_path(value: str | None) -> tuple[str, ...]:
    if not value:
        raise CloudArchiveError("归档记录缺少云端路径。")
    try:
        parts = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise CloudArchiveError("归档记录中的云端路径无效。") from exc
    if not isinstance(parts, list):
        raise CloudArchiveError("归档记录中的云端路径无效。")
    if not parts or any(
        not isinstance(part, str) or not part or part in (".", "..") or "/" in part or "\\" in part
        for part in parts
    ):
        raise CloudArchiveError("归档记录中的云端路径无效。")
    return tuple(parts)


def safe_failure(operation: str, exc: BaseException) -> str:
    return f"{operation}失败（{type(exc).__name__}）。"


def add_event(
    session: Session,
    job: ArchiveJob,
    event_type: str,
    status: str,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        ArchiveEvent(
            id=str(uuid.uuid4()),
            job_id=job.id,
            entry_id=job.entry_id,
            version_id=job.version_id,
            event_type=event_type,
            status=status,
            message=redact_public_text(message) if message else None,
            details=redact_public_value(details or {}),
            bytes_done=job.bytes_done,
            bytes_total=job.bytes_total,
        )
    )


def version_data(version: ArchiveVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "version_number": version.version_number,
        "size": version.size,
        "file_type": version.file_type,
        "mtime_ns": version.mtime_ns,
        "archived_at": version.archived_at.isoformat() if version.archived_at else None,
        "sha256": version.sha256,
        "cloud_remote_id": version.cloud_remote_id,
        "cloud_remote_path": list(decode_remote_path(version.cloud_remote_path)) if version.cloud_remote_path else None,
        "cloud_etag": version.cloud_etag,
        "status": version.status,
        "last_error": redact_public_text(version.last_error) if version.last_error else None,
        "retry_count": version.retry_count,
    }


def entry_data(entry: ArchiveEntry, detailed: bool = False) -> dict[str, Any]:
    result = {
        "id": entry.id,
        "filename": entry.filename,
        "original_abs_path": entry.original_abs_path,
        "archive_root_snapshot": entry.archive_root_snapshot,
        "relative_path": entry.relative_path,
        "restore_capability": entry.restore_capability,
        "source_kind": entry.source_kind,
        "status": entry.status,
        "last_error": redact_public_text(entry.last_error) if entry.last_error else None,
        "retry_count": entry.retry_count,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }
    if detailed:
        result["versions"] = [version_data(item) for item in entry.versions]
    return result


def job_retryable(job: ArchiveJob) -> bool:
    if job.kind == "archive":
        if job.status in ("needs_reconcile", "needs_verification"):
            return bool(job.version_id)
        plan = job.plan_data if isinstance(job.plan_data, dict) else {}
        return (
            job.status in ("failed", "interrupted")
            and all(
                key in plan
                for key in ("source_path", "source_size", "source_sha256")
            )
            and bool(job.version_id)
        )
    if job.kind == "restore":
        plan = job.plan_data if isinstance(job.plan_data, dict) else {}
        return (
            job.status in ("failed", "interrupted")
            and bool(job.target_abs_path)
            and bool(job.conflict_policy)
            and isinstance(plan.get("expected"), dict)
        )
    return False


def job_data(job: ArchiveJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "idempotency_key": job.idempotency_key,
        "kind": job.kind,
        "entry_id": job.entry_id,
        "version_id": job.version_id,
        "status": job.status,
        "bytes_total": job.bytes_total,
        "bytes_done": job.bytes_done,
        "conflict_policy": job.conflict_policy,
        "last_error": redact_public_text(job.last_error) if job.last_error else None,
        "attempt_count": job.attempt_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "retryable": job_retryable(job),
    }


class ProgressThrottle:
    def __init__(self, total: int, callback: Callable[[int], None]) -> None:
        self.total = max(0, total)
        self.callback = callback
        self.step = max(PROGRESS_MIN_BYTES, math.ceil(self.total / 100))
        self.last_committed = 0
        self.position = 0

    def update(self, count: int) -> None:
        self.position = min(self.total, max(self.position, max(0, count)))
        next_commit = self.last_committed + self.step
        while next_commit <= self.position and next_commit < self.total:
            self.callback(next_commit)
            self.last_committed = next_commit
            next_commit += self.step


class UploadProgressReader:
    def __init__(self, source: Any, callback: Callable[[int], None]) -> None:
        self.source = source
        self.callback = callback

    def read(self, size: int = -1) -> bytes:
        chunk = self.source.read(size)
        if isinstance(chunk, bytes):
            self.callback(self.source.tell())
        return chunk

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        position = self.source.seek(offset, whence)
        self.callback(position)
        return position

    def tell(self) -> int:
        return self.source.tell()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.source, name)


class CloudArchiveService:
    def __init__(
        self,
        engine: Engine,
        provider_factory: Callable[[], Any] = SJTUCloudPanProvider,
        archive_root: Path | None = None,
        multipart_threshold: int = 8388608,
        after_upload_hook: Callable[[ArchiveJob, Any], None] | None = None,
        mark_interrupted: bool = True,
    ) -> None:
        self.engine = engine
        self.provider_factory = provider_factory
        self.archive_root = (
            Path(os.path.abspath(os.fspath(archive_root.expanduser()))) if archive_root else None
        )
        self.multipart_threshold = multipart_threshold
        self.after_upload_hook = after_upload_hook
        if mark_interrupted:
            self.mark_interrupted_jobs()

    def mark_interrupted_jobs(self) -> int:
        with Session(self.engine) as session:
            jobs = list(session.scalars(select(ArchiveJob).where(ArchiveJob.status.in_(ACTIVE_STATES))))
            for job in jobs:
                previous = job.status
                job.status = "interrupted"
                job.finished_at = utcnow()
                job.last_error = "应用上次退出时任务尚未完成。"
                add_event(session, job, "interrupted", "interrupted", details={"previous_status": previous})
            session.commit()
            return len(jobs)

    def _root_snapshot(self, path: Path) -> tuple[str | None, str | None]:
        if self.archive_root is None:
            return None, None
        try:
            relative = path.relative_to(self.archive_root)
        except ValueError:
            return str(self.archive_root), None
        return str(self.archive_root), unicodedata.normalize("NFC", str(relative))

    @staticmethod
    def _original_path_authorized(session: Session, path: Path) -> bool:
        roots = session.scalars(
            select(ArchiveAuthorizedRoot).where(
                ArchiveAuthorizedRoot.is_active.is_(True),
                ArchiveAuthorizedRoot.source.in_(("native_picker", "both")),
            )
        )
        for root in roots:
            root_path = Path(os.path.abspath(root.absolute_path))
            try:
                path.relative_to(root_path)
                return True
            except ValueError:
                continue
        return False

    def archive_file(self, source_path: str | Path, idempotency_key: str | None = None) -> dict[str, Any]:
        path = normalize_source(source_path)
        volume = get_volume_identity(path)
        identity = path_identity(path, volume)
        size, mtime_ns, digest = hash_local_file(path)
        root_snapshot, relative = self._root_snapshot(path)
        with Session(self.engine) as session:
            original_path_authorized = self._original_path_authorized(session, path)
            entry = session.scalar(select(ArchiveEntry).where(ArchiveEntry.path_identity == identity))
            if entry is None:
                entry = ArchiveEntry(
                    id=str(uuid.uuid4()),
                    path_identity=identity,
                    volume_identity=volume,
                    original_abs_path=str(path),
                    archive_root_snapshot=root_snapshot,
                    relative_path=relative,
                    filename=unicodedata.normalize("NFC", path.name),
                    restore_capability="original_path" if original_path_authorized else "choose_location",
                    source_kind="user_file",
                    status="active",
                )
                session.add(entry)
                session.flush()
            else:
                entry.original_abs_path = str(path)
                entry.archive_root_snapshot = root_snapshot
                entry.relative_path = relative
                entry.filename = unicodedata.normalize("NFC", path.name)
                entry.restore_capability = (
                    "original_path" if original_path_authorized else "choose_location"
                )
            version = session.scalar(
                select(ArchiveVersion).where(
                    ArchiveVersion.entry_id == entry.id,
                    ArchiveVersion.sha256 == digest,
                )
            )
            if version is None:
                number = session.scalar(
                    select(func.max(ArchiveVersion.version_number)).where(ArchiveVersion.entry_id == entry.id)
                )
                number = int(number or 0) + 1
                remote_path = deterministic_remote_path(entry.id, number, digest, path.name)
                version = ArchiveVersion(
                    id=str(uuid.uuid4()),
                    entry_id=entry.id,
                    version_number=number,
                    size=size,
                    file_type=mimetypes.guess_type(path.name)[0],
                    mtime_ns=mtime_ns,
                    sha256=digest,
                    cloud_remote_path=encode_remote_path(remote_path),
                    status="pending",
                )
                session.add(version)
                session.flush()
            key = idempotency_key or f"archive:{entry.id}:{digest}"
            if not key or len(key) > 128:
                raise CloudArchiveError("幂等键无效。")
            job = session.scalar(select(ArchiveJob).where(ArchiveJob.idempotency_key == key))
            if job is None:
                job = ArchiveJob(
                    id=str(uuid.uuid4()),
                    idempotency_key=key,
                    kind="archive",
                    entry_id=entry.id,
                    version_id=version.id,
                    status="pending",
                    bytes_total=size,
                    bytes_done=0,
                    plan_data={
                        "source_path": str(path),
                        "source_volume_identity": volume,
                        "source_size": size,
                        "source_mtime_ns": mtime_ns,
                        "source_sha256": digest,
                    },
                )
                session.add(job)
                session.flush()
                add_event(session, job, "created", "pending")
            elif job.entry_id != entry.id or job.version_id != version.id:
                raise CloudArchiveError("幂等键已用于其他归档内容。")
            session.commit()
            job_id = job.id
            archived = version.status == "archived"
            completed = job.status == "completed"
        if archived or completed:
            return self._finish_duplicate(job_id)
        return self._upload(job_id, path)

    def _finish_duplicate(self, job_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            job = session.get(ArchiveJob, job_id)
            if job is None:
                raise CloudArchiveError("归档任务不存在。")
            if job.status != "completed":
                job.status = "completed"
                job.bytes_done = job.bytes_total
                job.finished_at = utcnow()
                add_event(session, job, "duplicate_skipped", "completed", "相同内容已归档，未重复上传。")
                session.commit()
            result = job_data(job)
            result["deduplicated"] = True
            return result

    @staticmethod
    def _ensure_directories(provider: Any, remote_path: tuple[str, ...]) -> None:
        ensure = getattr(provider, "ensure_directory", None)
        if callable(ensure):
            ensure(remote_path[:-1])
            return
        for index in range(1, len(remote_path)):
            current = remote_path[:index]
            try:
                provider.create_directory(current)
            except Exception:
                if not provider.exists(current):
                    raise

    def _upload(self, job_id: str, path: Path) -> dict[str, Any]:
        provider = None
        uploaded = False
        progress: ProgressThrottle | None = None
        try:
            with Session(self.engine) as session:
                job = session.get(ArchiveJob, job_id)
                version = session.get(ArchiveVersion, job.version_id) if job else None
                if job is None or version is None:
                    raise CloudArchiveError("归档任务记录不完整。")
                actual_size, _actual_mtime, actual_hash = hash_local_file(path)
                if actual_size != version.size or actual_hash != version.sha256:
                    raise CloudArchiveError("本地文件内容已变化，请创建新的归档版本。")
                remote_path = decode_remote_path(version.cloud_remote_path)
                expected_size = version.size
                job.status = "uploading"
                job.attempt_count += 1
                job.started_at = utcnow()
                job.last_error = None
                version.status = "uploading"
                add_event(session, job, "upload_started", "uploading")
                session.commit()
            provider = self.provider_factory()
            self._ensure_directories(provider, remote_path)
            if provider.exists(remote_path):
                return self._reconcile_remote(job_id, provider, remote_path)
            progress = ProgressThrottle(expected_size, lambda count: self._record_progress(job_id, count))
            with path.open("rb") as source:
                tracked = UploadProgressReader(source, progress.update)
                if path.stat().st_size >= self.multipart_threshold:
                    item = provider.multipart_upload(remote_path, tracked, overwrite=False)
                else:
                    item = provider.simple_upload(remote_path, tracked, overwrite=False)
            uploaded = True
            with Session(self.engine) as session:
                job = session.get(ArchiveJob, job_id)
                if job is None:
                    raise CloudArchiveError("归档任务不存在。")
                if self.after_upload_hook is not None:
                    self.after_upload_hook(job, item)
                self._complete_upload(session, job, item, remote_path, provider)
                session.commit()
                result = job_data(job)
                result["deduplicated"] = False
                return result
        except Exception as exc:
            bytes_done = progress.position if progress is not None else None
            if uploaded:
                self._mark_reconcile(job_id, safe_failure("保存上传状态", exc), bytes_done)
                raise CloudArchiveError("文件已上传，但本地状态保存失败；请执行 reconcile。") from exc
            self._mark_failed(job_id, safe_failure("上传", exc), bytes_done)
            if isinstance(exc, CloudArchiveError):
                raise
            raise CloudArchiveError("归档上传失败，可稍后重试。") from exc
        finally:
            close = getattr(provider, "close", None) if provider is not None else None
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _record_progress(self, job_id: str, count: int) -> None:
        with Session(self.engine) as session:
            job = session.get(ArchiveJob, job_id)
            if job is not None and job.status == "uploading":
                job.bytes_done = min(job.bytes_total, max(0, count))
                add_event(session, job, "upload_progress", "uploading")
                session.commit()

    @staticmethod
    def _remote_id(item: Any, remote_path: tuple[str, ...]) -> str:
        metadata = getattr(item, "metadata", None)
        if isinstance(metadata, dict):
            for key in ("id", "remote_id", "file_id"):
                if metadata.get(key):
                    return str(metadata.get(key))[:512]
        return "/".join(remote_path)[:512]

    def _complete_upload(self, session: Session, job: ArchiveJob, item: Any, remote_path: tuple[str, ...], provider: Any) -> None:
        version = session.get(ArchiveVersion, job.version_id)
        entry = session.get(ArchiveEntry, job.entry_id)
        if version is None or entry is None:
            raise CloudArchiveError("归档任务记录不完整。")
        version.cloud_provider = type(provider).__name__
        version.cloud_remote_id = self._remote_id(item, remote_path)
        version.cloud_etag = getattr(item, "etag", None)
        version.status = "archived"
        version.archived_at = utcnow()
        version.last_error = None
        entry.status = "archived"
        entry.last_error = None
        job.status = "completed"
        job.bytes_done = job.bytes_total
        job.finished_at = utcnow()
        job.last_error = None
        add_event(session, job, "upload_completed", "completed")

    @staticmethod
    def _strong_remote_match(version: ArchiveVersion, item: Any) -> bool:
        metadata = getattr(item, "metadata", None)
        candidates: list[str] = []
        if isinstance(metadata, Mapping):
            for key in ("sha256", "content_sha256", "checksum_sha256"):
                value = metadata.get(key)
                if isinstance(value, str):
                    candidates.append(value)
        etag = getattr(item, "etag", None)
        if isinstance(etag, str):
            candidates.append(etag)
            if version.cloud_etag and etag.strip('"') == version.cloud_etag.strip('"'):
                return True
        expected = version.sha256.casefold()
        return any(value.strip('"').casefold() == expected for value in candidates)

    def _reconcile_remote(self, job_id: str, provider: Any, remote_path: tuple[str, ...]) -> dict[str, Any]:
        item = provider.get_info(remote_path)
        with Session(self.engine) as session:
            job = session.get(ArchiveJob, job_id)
            version = session.get(ArchiveVersion, job.version_id) if job else None
            if job is None or version is None:
                raise CloudArchiveError("归档任务记录不完整。")
            if getattr(item, "size", None) not in (None, version.size):
                raise CloudArchiveError("确定性云端路径已存在不同大小的对象，拒绝覆盖。")
            if not self._strong_remote_match(version, item):
                job.status = "needs_verification"
                job.finished_at = None
                job.last_error = "云端对象仅通过大小检查，仍需强校验。"
                version.status = "needs_verification"
                version.last_error = job.last_error
                add_event(
                    session,
                    job,
                    "verification_required",
                    "needs_verification",
                    job.last_error,
                )
                session.commit()
                result = job_data(job)
                result["reconciled"] = False
                return result
            self._complete_upload(session, job, item, remote_path, provider)
            add_event(session, job, "reconciled", "completed", "已通过云端强校验恢复本地状态。")
            session.commit()
            result = job_data(job)
            result["reconciled"] = True
            return result

    def _mark_failed(
        self, job_id: str, message: str, bytes_done: int | None = None
    ) -> None:
        try:
            with Session(self.engine) as session:
                job = session.get(ArchiveJob, job_id)
                if job is None:
                    return
                version = session.get(ArchiveVersion, job.version_id) if job.version_id else None
                entry = session.get(ArchiveEntry, job.entry_id) if job.entry_id else None
                job.status = "failed"
                if bytes_done is not None:
                    job.bytes_done = min(job.bytes_total, max(job.bytes_done, bytes_done))
                job.last_error = message
                job.finished_at = utcnow()
                if version is not None:
                    version.status = "failed"
                    version.last_error = message
                    version.retry_count += 1
                if entry is not None:
                    entry.status = "failed"
                    entry.last_error = message
                    entry.retry_count += 1
                add_event(session, job, "failed", "failed", message)
                session.commit()
        except Exception:
            return

    def _mark_reconcile(
        self, job_id: str, message: str, bytes_done: int | None = None
    ) -> None:
        with Session(self.engine) as session:
            job = session.get(ArchiveJob, job_id)
            if job is None:
                return
            version = session.get(ArchiveVersion, job.version_id) if job.version_id else None
            job.status = "needs_reconcile"
            if bytes_done is not None:
                job.bytes_done = min(job.bytes_total, max(job.bytes_done, bytes_done))
            job.last_error = message
            if version is not None:
                version.status = "needs_reconcile"
                version.last_error = message
            add_event(session, job, "db_update_failed", "needs_reconcile", message)
            session.commit()

    def retry(self, job_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            job = session.get(ArchiveJob, job_id)
            version = session.get(ArchiveVersion, job.version_id) if job else None
            if job is None or job.kind != "archive" or version is None:
                raise CloudArchiveError("该任务不是可重试的归档任务。")
            status = job.status
            if status not in (
                "failed", "interrupted", "needs_reconcile", "needs_verification"
            ):
                return job_data(job)
            if status in ("needs_reconcile", "needs_verification"):
                path = None
            else:
                plan = job.plan_data if isinstance(job.plan_data, dict) else {}
                source_path = plan.get("source_path")
                source_size = plan.get("source_size")
                source_sha256 = plan.get("source_sha256")
                if (
                    not isinstance(source_path, str)
                    or type(source_size) is not int
                    or not isinstance(source_sha256, str)
                ):
                    raise CloudArchiveError(
                        "该归档任务缺少可安全重建的持久化源信息，不能重试。"
                    )
                path = normalize_source(source_path)
                actual_size, _mtime_ns, actual_sha256 = hash_local_file(path)
                if (
                    actual_size != source_size
                    or actual_size != version.size
                    or actual_sha256 != source_sha256
                    or actual_sha256 != version.sha256
                ):
                    raise CloudArchiveError("归档源已变化，不能重试原任务。")
        if status in ("needs_reconcile", "needs_verification"):
            items = self.reconcile(job_id)["items"]
            if not items:
                raise CloudArchiveError("该归档任务当前无法对账重试。")
            return items[0]
        assert path is not None
        return self._upload(job_id, path)

    def reconcile(self, job_id: str | None = None) -> dict[str, Any]:
        with Session(self.engine) as session:
            statement = select(ArchiveJob).where(
                ArchiveJob.status.in_(("needs_reconcile", "needs_verification"))
            )
            if job_id is not None:
                statement = statement.where(ArchiveJob.id == job_id)
            identifiers = [item.id for item in session.scalars(statement)]
        results = []
        for identifier in identifiers:
            provider = None
            try:
                with Session(self.engine) as session:
                    job = session.get(ArchiveJob, identifier)
                    version = session.get(ArchiveVersion, job.version_id) if job else None
                    if version is None:
                        raise CloudArchiveError("缺少可对账的版本。")
                    remote_path = decode_remote_path(version.cloud_remote_path)
                provider = self.provider_factory()
                if not provider.exists(remote_path):
                    self._mark_failed(identifier, "云端对象不存在，需要重新上传。")
                    results.append({"job_id": identifier, "status": "failed"})
                else:
                    results.append(self._reconcile_remote(identifier, provider, remote_path))
            except Exception as exc:
                self._mark_failed(identifier, safe_failure("对账", exc))
                results.append({"job_id": identifier, "status": "failed"})
            finally:
                close = getattr(provider, "close", None) if provider is not None else None
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
        return {"items": results, "count": len(results)}

    @staticmethod
    def _decode_cursor(cursor: str) -> int:
        if not cursor or len(cursor) > 128:
            raise CloudArchiveError("归档列表游标无效。")
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = int(base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
        except (ValueError, UnicodeError, TypeError) as exc:
            raise CloudArchiveError("归档列表游标无效。") from exc
        if value < 0:
            raise CloudArchiveError("归档列表游标无效。")
        return value

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")

    @staticmethod
    def _list_item(entry: ArchiveEntry, version: ArchiveVersion | None) -> dict[str, Any]:
        result = entry_data(entry)
        result["entry_status"] = result["status"]
        result.update({
            "size": version.size if version else None,
            "size_bytes": version.size if version else None,
            "version_status": version.status if version else entry.status,
            "cloud_path": (
                list(decode_remote_path(version.cloud_remote_path))
                if version and version.cloud_remote_path else None
            ),
            "archived_at": (
                version.archived_at.isoformat()
                if version and version.archived_at else None
            ),
            "sha256": version.sha256 if version else None,
            "status": version.status if version else entry.status,
            "current_version_id": version.id if version else None,
            "current_version_number": version.version_number if version else None,
        })
        return result

    def archive_list(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
        sort: str = "updated_desc",
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 500 or offset < 0:
            raise CloudArchiveError("归档列表分页参数无效。")
        if cursor is not None:
            if offset:
                raise CloudArchiveError("归档列表游标不能与非零偏移量同时使用。")
            offset = self._decode_cursor(cursor)
        query = (query or "").strip()
        if len(query) > 256 or (status is not None and len(status) > 32):
            raise CloudArchiveError("归档列表查询参数无效。")
        latest = (
            select(
                ArchiveVersion.entry_id.label("entry_id"),
                func.max(ArchiveVersion.version_number).label("version_number"),
            )
            .group_by(ArchiveVersion.entry_id)
            .subquery()
        )
        statement = (
            select(ArchiveEntry, ArchiveVersion)
            .outerjoin(latest, latest.c.entry_id == ArchiveEntry.id)
            .outerjoin(
                ArchiveVersion,
                and_(
                    ArchiveVersion.entry_id == latest.c.entry_id,
                    ArchiveVersion.version_number == latest.c.version_number,
                ),
            )
        )
        if query:
            pattern = f"%{query.casefold()}%"
            statement = statement.where(
                or_(
                    func.lower(ArchiveEntry.filename).like(pattern),
                    func.lower(func.coalesce(ArchiveEntry.relative_path, "")).like(pattern),
                )
            )
        if status:
            statement = statement.where(ArchiveVersion.status == status)
        sort_columns = {
            "updated_desc": (ArchiveEntry.updated_at.desc(),),
            "updated_asc": (ArchiveEntry.updated_at.asc(),),
            "archived_desc": (ArchiveVersion.archived_at.desc(),),
            "archived_asc": (ArchiveVersion.archived_at.asc(),),
            "name_asc": (ArchiveEntry.filename.asc(),),
            "name_desc": (ArchiveEntry.filename.desc(),),
            "size_asc": (ArchiveVersion.size.asc(),),
            "size_desc": (ArchiveVersion.size.desc(),),
        }
        aliases = {
            "-updated_at": "updated_desc", "updated_at": "updated_asc",
            "-archived_at": "archived_desc", "archived_at": "archived_asc",
            "filename": "name_asc", "-filename": "name_desc",
            "size": "size_asc", "-size": "size_desc",
        }
        sort = aliases.get(sort, sort)
        if sort not in sort_columns:
            raise CloudArchiveError("归档列表排序参数无效。")
        with Session(self.engine) as session:
            total = session.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            ) or 0
            page = statement.order_by(
                *sort_columns[sort], ArchiveEntry.id.asc()
            ).offset(offset).limit(limit)
            rows = session.execute(page).all()
            next_offset = offset + len(rows)
            return {
                "items": [self._list_item(entry, version) for entry, version in rows],
                "limit": limit,
                "offset": offset,
                "total": int(total),
                "next_cursor": (
                    self._encode_cursor(next_offset) if next_offset < total else None
                ),
            }

    def archive_detail(self, entry_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            entry = session.scalar(
                select(ArchiveEntry).options(selectinload(ArchiveEntry.versions)).where(ArchiveEntry.id == entry_id)
            )
            if entry is None:
                raise CloudArchiveError("归档条目不存在。")
            return entry_data(entry, detailed=True)

    def archive_jobs(self, limit: int = 100, status: str | None = None) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise CloudArchiveError("任务列表分页参数无效。")
        statement = select(ArchiveJob).order_by(ArchiveJob.created_at.desc()).limit(limit)
        if status:
            statement = statement.where(ArchiveJob.status == status)
        with Session(self.engine) as session:
            return {"items": [job_data(row) for row in session.scalars(statement)]}

    def archive_job_events(self, job_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(ArchiveEvent).where(ArchiveEvent.job_id == job_id).order_by(ArchiveEvent.created_at, ArchiveEvent.id)
            )
            return {"items": [{
                "id": row.id,
                "job_id": row.job_id,
                "event_type": row.event_type,
                "status": row.status,
                "message": redact_public_text(row.message) if row.message else None,
                "details": redact_public_value(row.details),
                "bytes_done": row.bytes_done,
                "bytes_total": row.bytes_total,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            } for row in rows]}

    def map_legacy_records(self) -> dict[str, int]:
        counts = {"course_file": 0, "email_attachment": 0, "ai_managed_file": 0}
        with Session(self.engine) as session:
            records = []
            for item in session.scalars(select(CourseFile)):
                records.append(("course_file", item, item.filename or item.display_name, item.download_sha256, item.cloud_path, "choose_location"))
            for item in session.scalars(select(EmailAttachment)):
                records.append(("email_attachment", item, item.filename, item.sha256, item.cloud_path, "choose_location"))
            for item in session.scalars(select(AIManagedFile)):
                records.append(("ai_managed_file", item, item.name, item.sha256, item.cloud_path, "managed_location"))
            for kind, source, name, known_hash, cloud_path, capability in records:
                record_id = str(source.id)
                exists = session.scalar(
                    select(ArchiveEntry.id).where(
                        ArchiveEntry.source_kind == kind,
                        ArchiveEntry.source_record_id == record_id,
                    )
                )
                if exists:
                    continue
                identity = hashlib.sha256(f"legacy\x00{kind}\x00{record_id}".encode()).hexdigest()
                entry = ArchiveEntry(
                    id=str(uuid.uuid4()),
                    path_identity=identity,
                    volume_identity="legacy",
                    original_abs_path=None,
                    archive_root_snapshot=None,
                    relative_path=getattr(source, "controlled_relpath", None),
                    filename=name,
                    restore_capability=capability,
                    source_kind=kind,
                    source_record_id=record_id,
                    status="legacy",
                )
                session.add(entry)
                session.flush()
                digest = known_hash if isinstance(known_hash, str) and len(known_hash) == 64 else hashlib.sha256(f"unverified\x00{kind}\x00{record_id}".encode()).hexdigest()
                size = max(0, int(getattr(source, "size", None) or getattr(source, "cloud_size", None) or 0))
                encoded_path = None
                if cloud_path:
                    parts = tuple(part for part in cloud_path.split("/") if part)
                    if parts and all(part not in (".", "..") and "\\" not in part for part in parts):
                        encoded_path = encode_remote_path(parts)
                session.add(
                    ArchiveVersion(
                        id=str(uuid.uuid4()),
                        entry_id=entry.id,
                        version_number=1,
                        size=size,
                        file_type=mimetypes.guess_type(name)[0],
                        mtime_ns=0,
                        sha256=digest,
                        cloud_remote_path=encoded_path,
                        status="archived" if encoded_path and known_hash else "unavailable",
                        archived_at=getattr(source, "cloud_backed_up_at", None) or getattr(source, "cloud_uploaded_at", None),
                    )
                )
                counts[kind] += 1
            session.commit()
        return counts
