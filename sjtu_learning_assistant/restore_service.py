from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.cloud_archive_service import (
    CloudArchiveError,
    add_event,
    decode_remote_path,
    get_volume_identity,
    hash_local_file_snapshot,
    job_data,
    ProgressThrottle,
    utcnow,
)
from sjtu_learning_assistant.cloud_storage import (
    CloudAuthError,
    CloudNetworkError,
    CloudNotFoundError,
    SJTUCloudPanProvider,
)
from sjtu_learning_assistant.models import (
    ArchiveAuthorizedRoot,
    ArchiveEntry,
    ArchiveJob,
    ArchiveVersion,
)

POLICIES = ("skip", "save_as", "overwrite", "compare")
REPLACE_RETRY_DELAYS_SECONDS = (0.05, 0.1, 0.25)
_MOUNT_POINT_TAG = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
_WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_STEMS = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"{device}{digit}" for device in ("COM", "LPT") for digit in "123456789¹²³"]
)


def _is_link(state: os.stat_result) -> bool:
    """符号链接，或 Windows 目录联接（junction 不带 S_IFLNK，需看 reparse tag）。"""
    return stat.S_ISLNK(state.st_mode) or getattr(state, "st_reparse_tag", 0) == _MOUNT_POINT_TAG


def _discard_reservation(job: Any) -> None:
    """删除本任务先前用 O_EXCL 建立、仍为 0 字节且 inode 未变的占位文件。"""
    plan = job.plan_data if isinstance(job.plan_data, dict) else {}
    reservation = plan.get("reservation_inode")
    if reservation is None or not job.target_abs_path:
        return
    target = Path(job.target_abs_path)
    try:
        state = target.lstat()
        if stat.S_ISREG(state.st_mode) and state.st_size == 0 and str(state.st_ino) == reservation:
            target.unlink()
    except OSError:
        pass


def _replace_with_retry(source: Path, target: Path) -> None:
    """Windows 上刚写完的临时文件常被杀软/索引短暂占用，有限重试 os.replace。"""
    for delay in (*REPLACE_RETRY_DELAYS_SECONDS, None):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if os.name != "nt" or delay is None:
                raise
            time.sleep(delay)


class RestoreError(CloudArchiveError):
    pass


def _absolute_directory(value: str | Path) -> Path:
    raw = os.fspath(value)
    if not raw or "\x00" in raw or len(raw) > 4096:
        raise RestoreError("授权目录无效。")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise RestoreError("授权目录必须是绝对路径。")
    _reject_symlink_chain(path, require_leaf=True)
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise RestoreError("授权目录不存在或无法访问。") from exc
    if not stat.S_ISDIR(mode):
        raise RestoreError("授权根必须是目录。")
    return path.resolve(strict=True)


def _reject_symlink_chain(path: Path, require_leaf: bool = False) -> None:
    if not path.is_absolute():
        raise RestoreError("目标路径必须是绝对路径。")
    parts = path.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        try:
            state = current.lstat()
        except FileNotFoundError:
            if require_leaf:
                raise RestoreError("目标目录不存在。")
            return
        except OSError as exc:
            raise RestoreError("无法检查目标路径。") from exc
        if _is_link(state):
            raise RestoreError("目标路径包含符号链接，已拒绝恢复。")


def _path_hash(path: Path, volume: str) -> str:
    text = unicodedata.normalize("NFC", str(path))
    return hashlib.sha256((volume + "\x00" + text).encode("utf-8")).hexdigest()


def _contained(root: Path, target: Path) -> bool:
    normalized_root = Path(os.path.abspath(os.fspath(root)))
    normalized_target = Path(os.path.abspath(os.fspath(target)))
    try:
        normalized_target.relative_to(normalized_root)
        return True
    except ValueError:
        return False


def _existing_details(path: Path) -> dict[str, Any] | None:
    details, _identity = _existing_snapshot(path)
    return details


def _existing_snapshot(
    path: Path,
) -> tuple[dict[str, Any] | None, tuple[int, int, int, int] | None]:
    try:
        state = path.lstat()
    except FileNotFoundError:
        return (None, None)
    except OSError as exc:
        raise RestoreError("无法检查现有目标文件。") from exc
    mode = state.st_mode
    if _is_link(state):
        raise RestoreError("目标文件是符号链接，已拒绝恢复。")
    if not stat.S_ISREG(mode):
        return ({"kind": "other", "size": None, "mtime_ns": None, "sha256": None}, None)
    size, mtime_ns, digest, identity = hash_local_file_snapshot(path)
    return ({"kind": "file", "size": size, "mtime_ns": mtime_ns, "sha256": digest}, identity)


def _fingerprint(details: Any) -> tuple[Any, ...] | None:
    if not isinstance(details, dict):
        return None
    return (details.get("kind"), details.get("size"), details.get("sha256"))


def _identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        state = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RestoreError("无法检查现有目标文件。") from exc
    if _is_link(state) or not stat.S_ISREG(state.st_mode):
        raise RestoreError("目标已存在且不是普通文件。")
    return (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns)


def _missing_directories(root: Path, parent: Path) -> list[str]:
    missing = []
    current = parent
    while current != root and not current.exists():
        missing.append(str(current))
        current = current.parent
    if current != root and not _contained(root, current):
        raise RestoreError("恢复目标超出授权根。")
    _reject_symlink_chain(current, require_leaf=True)
    missing.reverse()
    return missing


def _safe_leaf(name: str) -> str:
    normalized = unicodedata.normalize("NFC", name)
    if not normalized or normalized in (".", "..") or "/" in normalized or "\\" in normalized or "\x00" in normalized:
        raise RestoreError("恢复文件名无效。")
    if os.name == "nt":
        normalized = "".join(
            "_" if ord(character) < 32 or character in _WINDOWS_INVALID_CHARS else character
            for character in normalized
        ).rstrip(" .")
        if not normalized:
            raise RestoreError("恢复文件名无效。")
        if normalized.split(".", 1)[0].strip().upper() in _WINDOWS_RESERVED_STEMS:
            normalized = "_" + normalized
    return normalized


def _unique_target(target: Path) -> Path:
    if not target.exists():
        return target
    suffix = target.suffix
    stem = target.name[: -len(suffix)] if suffix else target.name
    for number in range(1, 10000):
        candidate = target.with_name(f"{stem} ({number}){suffix}")
        if not candidate.exists():
            return candidate
    raise RestoreError("无法生成不冲突的另存为文件名。")


class RestoreService:
    def __init__(
        self,
        engine: Engine,
        provider_factory: Callable[[], Any] = SJTUCloudPanProvider,
        chunk_size: int = 65536,
        mark_interrupted: bool = True,
    ) -> None:
        self.engine = engine
        self.provider_factory = provider_factory
        self.chunk_size = chunk_size
        if mark_interrupted:
            self.mark_interrupted_jobs()

    def mark_interrupted_jobs(self) -> int:
        with Session(self.engine) as session:
            jobs = list(
                session.scalars(
                    select(ArchiveJob).where(
                        ArchiveJob.kind == "restore",
                        ArchiveJob.status.in_(("running", "verifying", "downloading")),
                    )
                )
            )
            for job in jobs:
                previous = job.status
                job.status = "interrupted"
                job.finished_at = utcnow()
                job.last_error = "应用上次退出时恢复任务尚未完成。"
                _discard_reservation(job)
                add_event(session, job, "interrupted", "interrupted", details={"previous_status": previous})
            session.commit()
            return len(jobs)

    def authorize_root(self, picker_path: str | Path, source: str = "native_picker") -> dict[str, Any]:
        if source != "native_picker":
            raise RestoreError("恢复授权只能来自原生目录选择器。")
        path = _absolute_directory(picker_path)
        volume = get_volume_identity(path)
        digest = _path_hash(path, volume)
        with Session(self.engine) as session:
            root = session.scalar(select(ArchiveAuthorizedRoot).where(ArchiveAuthorizedRoot.path_hash == digest))
            if root is None:
                root = ArchiveAuthorizedRoot(
                    id=str(uuid.uuid4()),
                    path_hash=digest,
                    absolute_path=str(path),
                    volume_identity=volume,
                    source=source,
                    is_active=True,
                    last_used_at=utcnow(),
                )
                session.add(root)
            else:
                root.absolute_path = str(path)
                root.volume_identity = volume
                root.source = "native_picker"
                root.is_active = True
                root.last_used_at = utcnow()
            for entry in session.scalars(
                select(ArchiveEntry).where(ArchiveEntry.original_abs_path.is_not(None))
            ):
                if _contained(path, Path(entry.original_abs_path)):
                    entry.restore_capability = "original_path"
            session.commit()
            return {"id": root.id, "path": root.absolute_path, "source": root.source}

    def _load_version(self, session: Session, entry_id: str, version_id: str | None) -> tuple[ArchiveEntry, ArchiveVersion]:
        entry = session.get(ArchiveEntry, entry_id)
        if entry is None:
            raise RestoreError("归档条目不存在。")
        statement = select(ArchiveVersion).where(ArchiveVersion.entry_id == entry_id)
        if version_id:
            statement = statement.where(ArchiveVersion.id == version_id)
        else:
            statement = statement.order_by(ArchiveVersion.version_number.desc()).limit(1)
        version = session.scalar(statement)
        if version is None or version.status != "archived":
            raise RestoreError("没有可恢复的归档版本。")
        if not version.cloud_remote_path:
            raise RestoreError("归档版本没有可用的云端位置。")
        return entry, version

    def plan(
        self,
        entry_id: str,
        version_id: str | None = None,
        mode: str = "original",
        authorized_root_id: str | None = None,
    ) -> dict[str, Any]:
        if mode not in ("original", "choose_location", "save_as"):
            raise RestoreError("恢复模式无效。")
        with Session(self.engine) as session:
            entry, version = self._load_version(session, entry_id, version_id)
            root = None
            if mode == "original":
                if not entry.original_abs_path:
                    raise RestoreError("旧归档没有可信原路径，必须选择恢复位置。")
                target = Path(entry.original_abs_path)
                if not target.is_absolute():
                    raise RestoreError("归档原路径无效。")
                roots = session.scalars(
                    select(ArchiveAuthorizedRoot).where(
                        ArchiveAuthorizedRoot.is_active.is_(True),
                        ArchiveAuthorizedRoot.source.in_(("native_picker", "both")),
                    )
                )
                for candidate in roots:
                    candidate_path = Path(candidate.absolute_path)
                    if _contained(candidate_path, target):
                        root = candidate
                        break
                if root is None:
                    raise RestoreError("原路径尚未获得恢复授权。")
            else:
                if not authorized_root_id:
                    raise RestoreError("必须先通过原生目录选择器授权恢复位置。")
                root = session.get(ArchiveAuthorizedRoot, authorized_root_id)
                if root is None or not root.is_active or root.source not in ("native_picker", "both"):
                    raise RestoreError("恢复位置授权无效。")
                target = Path(root.absolute_path) / _safe_leaf(entry.filename)
            root_path = _absolute_directory(root.absolute_path)
            target = Path(os.path.abspath(os.fspath(target)))
            if not _contained(root_path, target):
                raise RestoreError("恢复目标超出授权根。")
            _reject_symlink_chain(target)
            missing = _missing_directories(root_path, target.parent)
            existing = _existing_details(target)
            comparison = None
            if existing is not None:
                comparison = {
                    "size_matches": existing.get("size") == version.size,
                    "mtime_matches": existing.get("mtime_ns") == version.mtime_ns,
                    "hash_matches": existing.get("sha256") == version.sha256,
                }
            key_data = f"restore\x00{version.id}\x00{target}"
            idempotency_key = "restore:" + hashlib.sha256(key_data.encode("utf-8")).hexdigest()
            job = session.scalar(select(ArchiveJob).where(ArchiveJob.idempotency_key == idempotency_key))
            if job is not None and job.status in ("running", "downloading", "verifying"):
                # 不能改写进行中任务的 plan_data/target：会丢掉 reservation_inode（崩溃后
                # 无法清理 0 字节占位），并让任务面板显示的目标与实际写入位置不一致。
                raise RestoreError("该文件正在恢复到同一位置，请等待当前任务完成后再制定计划。")
            plan_data = {
                "mode": mode,
                "root_path_hash": root.path_hash,
                "target": str(target),
                "missing_directories": missing,
                "existing": existing,
                "comparison": comparison,
                "expected": {"size": version.size, "mtime_ns": version.mtime_ns, "sha256": version.sha256},
            }
            if job is None or job.status in ("completed", "skipped", "compare", "failed", "interrupted"):
                idempotency_key = idempotency_key + ":" + str(uuid.uuid4()) if job is not None else idempotency_key
                job = ArchiveJob(
                    id=str(uuid.uuid4()),
                    idempotency_key=idempotency_key,
                    kind="restore",
                    entry_id=entry.id,
                    version_id=version.id,
                    authorized_root_id=root.id,
                    status="planned",
                    bytes_total=version.size,
                    bytes_done=0,
                    target_abs_path=str(target),
                    plan_data=plan_data,
                )
                session.add(job)
                session.flush()
                add_event(session, job, "restore_planned", "planned", details=plan_data)
            else:
                job.plan_data = plan_data
                job.target_abs_path = str(target)
            session.commit()
            return {
                "job": job_data(job),
                "target": str(target),
                "missing_directories": missing,
                "existing": existing,
                "comparison": comparison,
                "expected": plan_data["expected"],
                "requires_directory_confirmation": bool(missing),
            }

    def _validate_plan_target(self, session: Session, job: ArchiveJob) -> tuple[Path, Path, ArchiveVersion]:
        root = session.get(ArchiveAuthorizedRoot, job.authorized_root_id)
        version = session.get(ArchiveVersion, job.version_id)
        if root is None or not root.is_active or version is None or not job.target_abs_path:
            raise RestoreError("恢复计划已失效。")
        root_path = _absolute_directory(root.absolute_path)
        if _path_hash(root_path, get_volume_identity(root_path)) != root.path_hash:
            raise RestoreError("授权根已变化，请重新选择。")
        target = Path(job.target_abs_path)
        if not target.is_absolute():
            raise RestoreError("恢复目标必须是绝对路径。")
        target = Path(os.path.abspath(os.fspath(target)))
        if not _contained(root_path, target):
            raise RestoreError("恢复目标超出授权根。")
        _reject_symlink_chain(target)
        return root_path, target, version

    @staticmethod
    def _check_writable(root: Path, parent: Path) -> None:
        existing = parent
        while not existing.exists() and existing != root:
            existing = existing.parent
        mode = existing.stat().st_mode
        # Windows 目录的只读属性被系统忽略（Documents/Desktop 等定制文件夹常带此属性），不能据此判不可写。
        if (os.name != "nt" and mode & 0o222 == 0) or not os.access(existing, os.W_OK):
            raise RestoreError("恢复目标不可写。")
        statvfs = getattr(os, "statvfs", None)
        flags = getattr(statvfs(existing), "f_flag", 0) if statvfs else 0
        readonly = getattr(os, "ST_RDONLY", 1)
        if flags & readonly:
            raise RestoreError("恢复目标所在卷为只读。")

    @staticmethod
    def _create_directories(root: Path, parent: Path) -> None:
        missing = _missing_directories(root, parent)
        for value in missing:
            path = Path(value)
            try:
                path.mkdir()
            except PermissionError as exc:
                raise RestoreError("恢复目标不可写。") from exc
            _reject_symlink_chain(path, require_leaf=True)

    def execute(self, job_id: str, conflict_policy: str, confirm_create_dirs: bool = False) -> dict[str, Any]:
        if conflict_policy not in POLICIES:
            raise RestoreError("冲突策略无效。")
        with Session(self.engine) as session:
            job = session.get(ArchiveJob, job_id)
            if job is None or job.kind != "restore" or job.status != "planned":
                raise RestoreError("恢复计划不存在或已执行。")
            root, target, version = self._validate_plan_target(session, job)
            missing = _missing_directories(root, target.parent)
            if missing and not confirm_create_dirs:
                raise RestoreError("创建父目录前必须由用户确认。")
            self._check_writable(root, target.parent)
            existing, existing_identity = _existing_snapshot(target)
            planned = job.plan_data.get("existing") if isinstance(job.plan_data, dict) else None
            if conflict_policy == "overwrite" and _fingerprint(existing) != _fingerprint(planned):
                # 用户依据计划里的指纹确认覆盖；目标此后被修改/新建时不能静默销毁新内容。
                raise RestoreError("目标文件在制定计划后已变化，请重新检查后再覆盖。")
            job.conflict_policy = conflict_policy
            if existing is not None and conflict_policy == "skip":
                job.status = "skipped"
                job.finished_at = utcnow()
                add_event(session, job, "conflict_skipped", "skipped", details={"existing": existing})
                session.commit()
                return job_data(job)
            if conflict_policy == "compare":
                job.status = "compare"
                job.finished_at = utcnow()
                add_event(session, job, "conflict_compared", "compare", details={"existing": existing})
                session.commit()
                result = job_data(job)
                result["comparison"] = {"existing": existing, "expected": job.plan_data.get("expected")}
                return result
            if existing is not None and conflict_policy == "save_as":
                target = _unique_target(target)
                job.target_abs_path = str(target)
                job.plan_data = dict(job.plan_data, target=str(target))
            elif existing is not None and conflict_policy != "overwrite":
                raise RestoreError("目标文件已存在，需要选择冲突策略。")
            if existing and existing.get("kind") != "file":
                raise RestoreError("目标已存在且不是普通文件。")
            self._create_directories(root, target.parent)
            reservation_inode = None
            overwrite_identity = existing_identity if conflict_policy == "overwrite" else None
            if conflict_policy != "overwrite":
                try:
                    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError as exc:
                    raise RestoreError("恢复目标刚刚被其他文件占用，请重新制定计划。") from exc
                except PermissionError as exc:
                    raise RestoreError("恢复目标不可写。") from exc
                else:
                    os.close(descriptor)
                    reservation_inode = target.lstat().st_ino
                    job.plan_data = dict(job.plan_data, reservation_inode=str(reservation_inode))
            job.status = "downloading"
            job.started_at = utcnow()
            job.attempt_count += 1
            job.last_error = None
            add_event(session, job, "download_started", "downloading")
            session.commit()
            remote_path = decode_remote_path(version.cloud_remote_path)
            expected_size = version.size
            expected_hash = version.sha256
        return self._download(
            job_id,
            root,
            target,
            remote_path,
            expected_size,
            expected_hash,
            reservation_inode,
            overwrite_identity,
            conflict_policy == "overwrite",
        )

    def _record_progress(self, job_id: str, count: int) -> None:
        with Session(self.engine) as session:
            job = session.get(ArchiveJob, job_id)
            if job is not None and job.status == "downloading":
                job.bytes_done = min(job.bytes_total, max(0, count))
                add_event(session, job, "download_progress", "downloading")
                session.commit()

    def retry(self, job_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            job = session.get(ArchiveJob, job_id)
            if job is None or job.kind != "restore":
                raise RestoreError("该任务不是可重试的恢复任务。")
            if job.status not in ("failed", "interrupted"):
                return job_data(job)
            if not isinstance(job.plan_data, dict) or not job.target_abs_path:
                raise RestoreError("恢复任务缺少持久化计划，不能重试。")
            if not job.conflict_policy:
                raise RestoreError("恢复任务没有已确认的冲突策略，不能重试。")
            root, target, version = self._validate_plan_target(session, job)
            expected = job.plan_data.get("expected")
            if not isinstance(expected, dict):
                raise RestoreError("恢复任务缺少可安全重建的持久化计划，不能重试。")
            if (
                expected.get("size") != version.size
                or expected.get("sha256") != version.sha256
                or job.plan_data.get("target") != str(target)
            ):
                raise RestoreError("恢复任务持久化计划已变化，不能重试。")
            _missing_directories(root, target.parent)
            _discard_reservation(job)
            _existing_details(target)
            job.status = "planned"
            job.finished_at = None
            job.last_error = None
            add_event(session, job, "retry_planned", "planned")
            policy = job.conflict_policy
            session.commit()
        try:
            return self.execute(job_id, policy, confirm_create_dirs=True)
        except Exception as exc:
            # execute 在下载前的复核（覆盖指纹、占位冲突、可写性等）失败时不会改状态；
            # 不回写的话任务会永久停在 planned，任务面板丢失错误与重试入口。
            self._mark_retry_rejected(job_id, exc)
            raise

    def _mark_retry_rejected(self, job_id: str, exc: Exception) -> None:
        message = str(exc) if isinstance(exc, CloudArchiveError) else f"恢复失败（{type(exc).__name__}）。"
        with Session(self.engine) as session:
            job = session.get(ArchiveJob, job_id)
            if job is None or job.status != "planned":
                return
            job.status = "failed"
            job.finished_at = utcnow()
            job.last_error = message[:500]
            add_event(session, job, "retry_rejected", "failed", job.last_error)
            session.commit()

    def _download(
        self,
        job_id: str,
        root: Path,
        target: Path,
        remote_path: tuple[str, ...],
        expected_size: int,
        expected_hash: str,
        reservation_inode: int | None,
        overwrite_identity: tuple[int, int, int, int] | None = None,
        verify_overwrite_identity: bool = False,
    ) -> dict[str, Any]:
        provider = None
        temporary: Path | None = None
        overwrite_snapshot: Path | None = None
        restored_target_written = False
        restored_target_identity: tuple[int, int, int, int] | None = None
        count = 0
        try:
            _reject_symlink_chain(target)
            if not _contained(root, target):
                raise RestoreError("恢复目标超出授权根。")
            descriptor, temporary_name = tempfile.mkstemp(prefix=".sjtu-restore-", dir=target.parent)
            temporary = Path(temporary_name)
            digest = hashlib.sha256()
            progress = ProgressThrottle(
                expected_size, lambda value: self._record_progress(job_id, value)
            )
            with os.fdopen(descriptor, "wb") as output:
                provider = self.provider_factory()
                for chunk in provider.download_stream(remote_path, chunk_size=self.chunk_size):
                    if not isinstance(chunk, bytes):
                        raise RestoreError("云端下载返回了无效数据。")
                    output.write(chunk)
                    digest.update(chunk)
                    count += len(chunk)
                    if count > expected_size:
                        raise RestoreError("下载文件大小校验失败。")
                    progress.update(count)
                output.flush()
                os.fsync(output.fileno())
            with Session(self.engine) as session:
                job = session.get(ArchiveJob, job_id)
                if job is None:
                    raise RestoreError("恢复任务不存在。")
                job.status = "verifying"
                add_event(session, job, "download_verifying", "verifying")
                session.commit()
            if count != expected_size or digest.hexdigest() != expected_hash:
                raise RestoreError("下载文件完整性校验失败。")
            _reject_symlink_chain(target)
            if reservation_inode is not None and target.lstat().st_ino != reservation_inode:
                raise RestoreError("恢复目标在下载期间发生变化，已拒绝覆盖。")
            if verify_overwrite_identity and _identity(target) != overwrite_identity:
                raise RestoreError("恢复目标在下载期间发生变化，已拒绝覆盖。")
            if verify_overwrite_identity and overwrite_identity is not None:
                snapshot_descriptor, snapshot_name = tempfile.mkstemp(
                    prefix=".sjtu-restore-overwrite-", dir=target.parent
                )
                os.close(snapshot_descriptor)
                snapshot_path = Path(snapshot_name)
                snapshot_path.unlink()
                _replace_with_retry(target, snapshot_path)
                # 只有原文件确实移入快照后才进入回滚路径：Windows 上目标被 Word 等占用时
                # 这一步就会失败，此时原文件仍在原位，不能再误报“回滚失败/保留快照”。
                overwrite_snapshot = snapshot_path
            _replace_with_retry(temporary, target)
            temporary = None
            restored_target_written = True
            restored_target_identity = _identity(target)
            if os.name != "nt":
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            with Session(self.engine) as session:
                job = session.get(ArchiveJob, job_id)
                if job is None:
                    raise RestoreError("恢复任务不存在。")
                job.status = "completed"
                job.bytes_done = expected_size
                job.finished_at = utcnow()
                add_event(session, job, "restore_completed", "completed")
                session.commit()
                result = job_data(job)
                result["target"] = str(target)
            if overwrite_snapshot is not None:
                snapshot_path, overwrite_snapshot = overwrite_snapshot, None
                try:
                    snapshot_path.unlink(missing_ok=True)
                except OSError:
                    # 恢复已提交为 completed；快照被杀软/索引短暂占用时只残留一个副本，
                    # 不能进入 except 把新文件回滚并改写成 failed。
                    pass
            return result
        except Exception as exc:
            rollback_failed = False
            snapshot_name = overwrite_snapshot.name if overwrite_snapshot is not None else ""
            if overwrite_snapshot is not None:
                try:
                    if restored_target_written:
                        if _identity(target) != restored_target_identity:
                            rollback_failed = True
                        else:
                            target.unlink(missing_ok=True)
                    if not rollback_failed:
                        _replace_with_retry(overwrite_snapshot, target)
                        overwrite_snapshot = None
                except (OSError, RestoreError):
                    # _identity 把 lstat 失败包装成 RestoreError；漏捕会跳过下方 failed 回写，
                    # 任务停在 verifying，前端会一直轮询。
                    rollback_failed = True
            # 任务面板只读 last_error：保留可操作的已脱敏原因，而不是只显示异常类名。
            if rollback_failed:
                # 只给出快照文件名（随机名，不含路径），否则用户在 Windows 上找不到无扩展名的副本。
                public_error = (
                    "恢复失败，且原文件自动回滚失败；原文件保留在目标目录的隐藏快照“"
                    + snapshot_name
                    + "”中，请勿删除，核对后可手动改回原文件名。"
                )
            elif isinstance(exc, CloudArchiveError):
                public_error = str(exc)
            elif isinstance(exc, CloudAuthError):
                # 与归档上传一致：凭据失效需引导用户更新 UserToken，而不是笼统的“恢复失败”。
                public_error = "交大云盘凭据无效或已过期，请在“系统设置 → 交大云盘”更新 UserToken 后重试。"
            elif isinstance(exc, CloudNetworkError):
                public_error = "无法连接交大云盘，请检查网络后重试。"
            elif isinstance(exc, CloudNotFoundError):
                # 云端归档对象已被删除/移动时重试必然失败，需明确告知而不是“可稍后重试”。
                public_error = "云端归档文件不存在或已被移除，无法恢复该版本。"
            elif isinstance(exc, PermissionError):
                public_error = "目标文件正被其他程序占用或没有写入权限，请关闭相关程序后重试。"
            else:
                public_error = f"恢复失败（{type(exc).__name__}）。"
            with Session(self.engine) as session:
                job = session.get(ArchiveJob, job_id)
                if job is not None:
                    job.status = "failed"
                    job.bytes_done = min(job.bytes_total, max(job.bytes_done, count))
                    job.finished_at = utcnow()
                    job.last_error = public_error[:500]
                    add_event(session, job, "restore_failed", "failed", job.last_error)
                    session.commit()
            if rollback_failed:
                raise RestoreError(public_error) from exc
            if isinstance(exc, RestoreError):
                raise
            if isinstance(exc, (CloudAuthError, CloudNetworkError, CloudNotFoundError, PermissionError)):
                raise RestoreError(public_error) from exc
            raise RestoreError("恢复下载失败，可稍后重新制定计划。") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            if reservation_inode is not None:
                try:
                    state = target.lstat()
                    if state.st_ino == reservation_inode and state.st_size == 0:
                        target.unlink()
                except OSError:
                    pass
            close = getattr(provider, "close", None) if provider is not None else None
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
