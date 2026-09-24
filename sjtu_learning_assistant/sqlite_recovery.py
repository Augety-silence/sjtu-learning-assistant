"""SQLite integrity checks, rotating snapshots, and startup recovery."""

from __future__ import annotations

import fcntl
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Literal

SQLITE_BACKUP_COUNT = 3
SQLITE_MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60
SQLITE_TIMEOUT_SECONDS = 5.0
_SQLITE_CORRUPTION_CODES = {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}


class SQLiteRecoveryError(RuntimeError):
    """Raised when SQLite maintenance cannot complete safely."""


@dataclass(frozen=True)
class SQLiteRecoveryResult:
    status: Literal["new", "healthy", "restored", "rebuilt"]
    backup_index: int | None = None

    @property
    def requires_notice(self) -> bool:
        return self.status in {"restored", "rebuilt"}

    def user_message(self) -> str:
        if self.status == "restored":
            return (
                "检测到本地数据库损坏，已从最近可用快照恢复。"
                "部分本地状态可能回退，请核对后重新同步。"
            )
        if self.status == "rebuilt":
            return (
                "检测到本地数据库损坏且没有可用快照，已隔离原文件并重建数据库。"
                "请重新同步课程数据；原文件仍保留以便人工恢复。"
            )
        return ""


def sqlite_backup_paths(database_path: Path, *, keep: int = SQLITE_BACKUP_COUNT) -> tuple[Path, ...]:
    if keep < 1:
        raise ValueError("SQLite 备份数量必须至少为 1。")
    return tuple(
        database_path.with_name(f"{database_path.name}.backup-{index}")
        for index in range(1, keep + 1)
    )


def _sqlite_uri(path: Path) -> str:
    return f"{path.expanduser().resolve().as_uri()}?mode=ro"


def _database_sidecars(database_path: Path) -> tuple[Path, Path]:
    return (
        database_path.with_name(f"{database_path.name}-wal"),
        database_path.with_name(f"{database_path.name}-shm"),
    )


@contextmanager
def sqlite_maintenance_lock(database_path: Path) -> Iterator[None]:
    lock_path = database_path.with_name(f"{database_path.name}.maintenance.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(lock_path.parent, 0o700)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _is_corruption_error(error: sqlite3.DatabaseError) -> bool:
    return getattr(error, "sqlite_errorcode", None) in _SQLITE_CORRUPTION_CODES


def _run_integrity_check(database_path: Path, *, full: bool) -> bool:
    path = database_path.expanduser()
    pragma = "PRAGMA integrity_check" if full else "PRAGMA quick_check"
    try:
        with sqlite3.connect(
            _sqlite_uri(path),
            uri=True,
            timeout=SQLITE_TIMEOUT_SECONDS,
        ) as connection:
            rows = connection.execute(pragma).fetchall()
    except sqlite3.DatabaseError as exc:
        if _is_corruption_error(exc):
            return False
        raise SQLiteRecoveryError("暂时无法检查本地数据库，请稍后重试。") from exc
    except OSError as exc:
        raise SQLiteRecoveryError("暂时无法访问本地数据库，请检查磁盘和目录权限。") from exc
    return bool(rows) and all(str(row[0]).lower() == "ok" for row in rows)


def check_sqlite_integrity(database_path: Path, *, full: bool = False) -> bool:
    """Return whether an existing SQLite file passes an integrity check."""
    path = database_path.expanduser()
    if not path.exists() or path.stat().st_size == 0:
        return True
    return _run_integrity_check(path, full=full)


def read_sqlite_schema_version(database_path: Path) -> str | None:
    """Read the desktop schema marker without mutating the database."""
    path = database_path.expanduser()
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with sqlite3.connect(
            _sqlite_uri(path),
            uri=True,
            timeout=SQLITE_TIMEOUT_SECONDS,
        ) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='desktop_schema_version'"
            ).fetchone()
            if table is None:
                return None
            row = connection.execute(
                "SELECT version FROM desktop_schema_version WHERE id=1"
            ).fetchone()
    except (OSError, sqlite3.DatabaseError):
        return None
    return str(row[0]) if row is not None else None


def _copy_with_sqlite_backup(source_path: Path, target_path: Path) -> None:
    target_path.unlink(missing_ok=True)
    try:
        with sqlite3.connect(
            _sqlite_uri(source_path),
            uri=True,
            timeout=SQLITE_TIMEOUT_SECONDS,
        ) as source, sqlite3.connect(target_path, timeout=SQLITE_TIMEOUT_SECONDS) as target:
            source.backup(target)
            target.commit()
        os.chmod(target_path, 0o600)
    except (OSError, sqlite3.DatabaseError) as exc:
        target_path.unlink(missing_ok=True)
        raise SQLiteRecoveryError("无法创建本地数据库安全快照。") from exc


def _rotate_backups(database_path: Path, temporary_path: Path, *, keep: int) -> Path:
    backups = sqlite_backup_paths(database_path, keep=keep)
    for index in range(len(backups) - 1, 0, -1):
        previous = backups[index - 1]
        if previous.exists():
            os.replace(previous, backups[index])
    os.replace(temporary_path, backups[0])
    os.chmod(backups[0], 0o600)
    return backups[0]


def _create_sqlite_backup_locked(
    path: Path,
    *,
    keep: int,
    minimum_interval_seconds: float,
) -> Path | None:
    newest = sqlite_backup_paths(path, keep=keep)[0]
    if (
        minimum_interval_seconds > 0
        and newest.exists()
        and datetime.now(timezone.utc).timestamp() - newest.stat().st_mtime
        < minimum_interval_seconds
    ):
        return newest
    if not check_sqlite_integrity(path):
        raise SQLiteRecoveryError("本地数据库完整性校验失败，未创建快照。")
    temporary_path = path.with_name(f"{path.name}.backup.tmp.{os.getpid()}")
    _copy_with_sqlite_backup(path, temporary_path)
    if not check_sqlite_integrity(temporary_path, full=True):
        temporary_path.unlink(missing_ok=True)
        raise SQLiteRecoveryError("新建的本地数据库快照未通过完整性校验。")
    return _rotate_backups(path, temporary_path, keep=keep)


def create_sqlite_backup(
    database_path: Path,
    *,
    keep: int = SQLITE_BACKUP_COUNT,
    minimum_interval_seconds: float = 0,
) -> Path | None:
    """Create a verified online backup and rotate older snapshots."""
    path = database_path.expanduser()
    if not path.exists() or path.stat().st_size == 0:
        return None
    with sqlite_maintenance_lock(path):
        return _create_sqlite_backup_locked(
            path,
            keep=keep,
            minimum_interval_seconds=minimum_interval_seconds,
        )


@contextmanager
def sqlite_migration_guard(
    database_path: Path,
) -> Iterator[Callable[[], bool]]:
    """Serialize schema changes and expose a lock-safe snapshot operation."""
    path = database_path.expanduser()
    with sqlite_maintenance_lock(path):
        def create_backup() -> bool:
            if not path.exists() or path.stat().st_size == 0:
                return False
            return (
                _create_sqlite_backup_locked(
                    path,
                    keep=SQLITE_BACKUP_COUNT,
                    minimum_interval_seconds=0,
                )
                is not None
            )

        yield create_backup


def _restore_from_backup_locked(database_path: Path, backup_path: Path) -> bool:
    if not backup_path.is_file() or not check_sqlite_integrity(backup_path, full=True):
        return False
    temporary_path = database_path.with_name(f"{database_path.name}.restore.tmp.{os.getpid()}")
    try:
        _copy_with_sqlite_backup(backup_path, temporary_path)
        if not check_sqlite_integrity(temporary_path, full=True):
            return False
        for sidecar in _database_sidecars(database_path):
            sidecar.unlink(missing_ok=True)
        os.replace(temporary_path, database_path)
        os.chmod(database_path, 0o600)
        return True
    finally:
        temporary_path.unlink(missing_ok=True)


def restore_latest_sqlite_backup(
    database_path: Path,
    *,
    keep: int = SQLITE_BACKUP_COUNT,
) -> int | None:
    """Restore the newest valid snapshot and return its one-based index."""
    path = database_path.expanduser()
    with sqlite_maintenance_lock(path):
        for index, backup_path in enumerate(sqlite_backup_paths(path, keep=keep), start=1):
            if _restore_from_backup_locked(path, backup_path):
                return index
    return None


def _quarantine_database_family(database_path: Path) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for path in (database_path, *_database_sidecars(database_path)):
        if path.exists():
            quarantined = path.with_name(f"{path.name}.corrupt.{timestamp}")
            os.replace(path, quarantined)
            os.chmod(quarantined, 0o600)


def prepare_sqlite_database(database_path: Path) -> SQLiteRecoveryResult:
    """Validate a SQLite file before SQLAlchemy opens it and recover if needed."""
    path = database_path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    with sqlite_maintenance_lock(path):
        if not path.exists() or path.stat().st_size == 0:
            return SQLiteRecoveryResult(status="new")
        if check_sqlite_integrity(path):
            return SQLiteRecoveryResult(status="healthy")
        try:
            _quarantine_database_family(path)
        except OSError as exc:
            raise SQLiteRecoveryError("无法隔离损坏的本地数据库。") from exc
        for index, backup_path in enumerate(sqlite_backup_paths(path), start=1):
            if _restore_from_backup_locked(path, backup_path):
                return SQLiteRecoveryResult(status="restored", backup_index=index)
        return SQLiteRecoveryResult(status="rebuilt")


def maintain_sqlite_database(database_path: Path) -> Path | None:
    """Run a daily integrity check and create a rotating snapshot when due."""
    path = database_path.expanduser()
    newest = sqlite_backup_paths(path)[0]
    if (
        newest.exists()
        and datetime.now(timezone.utc).timestamp() - newest.stat().st_mtime
        < SQLITE_MAINTENANCE_INTERVAL_SECONDS
    ):
        return newest
    if not check_sqlite_integrity(path, full=True):
        raise SQLiteRecoveryError("本地数据库完整性校验失败，请重新启动应用以恢复。")
    return create_sqlite_backup(path)
