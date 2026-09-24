"""Cross-dialect database configuration and engine helpers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from weakref import WeakKeyDictionary

from platformdirs import user_data_path
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from sjtu_learning_assistant.sqlite_recovery import (
    SQLiteRecoveryResult,
    prepare_sqlite_database,
)

DATABASE_URL_ENV = "SJTU_DATABASE_URL"
KEYCHAIN_SERVICE = "SJTU Learning Assistant - PostgreSQL"
KEYCHAIN_ACCOUNT = "database-url"
APP_SUPPORT_DIR = (
    user_data_path("SJTU Learning Assistant", appauthor=False)
    if sys.platform == "win32"
    else Path.home() / "Library" / "Application Support" / "SJTU Learning Assistant"
)
DEFAULT_SQLITE_PATH = APP_SUPPORT_DIR / "data" / "app.db"
SQLITE_BUSY_TIMEOUT_MS = 5000

_ENGINE_RECOVERY_RESULTS: WeakKeyDictionary[Engine, SQLiteRecoveryResult] = WeakKeyDictionary()
_ENGINE_RECOVERY_LOCK = Lock()


class DatabaseConfigError(RuntimeError):
    """Raised when database configuration cannot be loaded or validated."""


@dataclass(frozen=True)
class DatabaseHealth:
    dialect: str
    database: str
    user: str
    server_version: str


def default_sqlite_url(path: Path | None = None) -> str:
    database_path = (path or DEFAULT_SQLITE_PATH).expanduser().resolve()
    return str(URL.create("sqlite+pysqlite", database=str(database_path)))


DEFAULT_DATABASE_URL = default_sqlite_url()


def load_keyring_module():
    try:
        import keyring  # type: ignore[import-not-found]
        from keyring.errors import KeyringError  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DatabaseConfigError(
            "缺少 keyring 依赖。请先运行：python3 -m pip install -r requirements.txt"
        ) from exc
    return keyring, KeyringError


def _validate_postgres_url(parsed: URL, database_url: str) -> str:
    if parsed.drivername != "postgresql+psycopg":
        raise DatabaseConfigError(
            "PostgreSQL 地址必须使用 postgresql+psycopg:// 驱动格式。"
        )
    if not parsed.database:
        raise DatabaseConfigError("数据库地址必须包含数据库名称。")

    host = parsed.host or parsed.query.get("host")
    local_hosts = {None, "localhost", "127.0.0.1", "::1"}
    is_local = host in local_hosts or str(host).startswith("/")
    sslmode = parsed.query.get("sslmode")
    if not is_local and sslmode not in {"require", "verify-ca", "verify-full"}:
        raise DatabaseConfigError(
            "远程 PostgreSQL 必须在连接地址中启用 sslmode=require、"
            "verify-ca 或 verify-full。"
        )
    return database_url


def validate_database_url(value: str) -> str:
    database_url = value.strip()
    try:
        parsed: URL = make_url(database_url)
    except Exception as exc:
        raise DatabaseConfigError("数据库连接地址格式不正确。") from exc

    if parsed.drivername in {"sqlite", "sqlite+pysqlite"}:
        if not parsed.database:
            raise DatabaseConfigError("SQLite 地址必须包含数据库文件路径。")
        return database_url
    if parsed.drivername.startswith("postgresql"):
        return _validate_postgres_url(parsed, database_url)
    raise DatabaseConfigError("数据库地址必须使用 SQLite 或 postgresql+psycopg 驱动。")


def validate_postgres_database_url(value: str) -> str:
    database_url = value.strip()
    try:
        parsed = make_url(database_url)
    except Exception as exc:
        raise DatabaseConfigError("PostgreSQL 连接地址格式不正确。") from exc
    return _validate_postgres_url(parsed, database_url)


def get_database_url() -> str:
    """Use an explicit environment URL, otherwise the per-user SQLite file."""
    from_environment = os.environ.get(DATABASE_URL_ENV)
    if from_environment:
        return validate_database_url(from_environment)
    return DEFAULT_DATABASE_URL


def get_postgres_database_url(*, required: bool = True) -> str | None:
    """Load an explicitly selected PostgreSQL URL without changing the default."""
    from_environment = os.environ.get(DATABASE_URL_ENV)
    if from_environment:
        try:
            parsed = make_url(from_environment.strip())
        except Exception as exc:
            raise DatabaseConfigError("PostgreSQL 连接地址格式不正确。") from exc
        if parsed.drivername.startswith("postgresql"):
            return validate_postgres_database_url(from_environment)

    keyring, KeyringError = load_keyring_module()
    try:
        from_keychain = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except KeyringError as exc:
        raise DatabaseConfigError("无法读取数据库 Keychain 配置。") from exc
    if from_keychain:
        return validate_postgres_database_url(from_keychain)
    if required:
        raise DatabaseConfigError(
            "未找到 PostgreSQL 配置；请先运行 db_manage.py configure，"
            "或显式设置 SJTU_DATABASE_URL。"
        )
    return None


def save_database_url(database_url: str) -> None:
    validated = validate_postgres_database_url(database_url)
    keyring, KeyringError = load_keyring_module()
    try:
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, validated)
    except KeyringError as exc:
        raise DatabaseConfigError("无法保存数据库配置到 Keychain。") from exc


def delete_database_url() -> bool:
    keyring, KeyringError = load_keyring_module()
    try:
        existing = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        if existing is None:
            return False
        keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except KeyringError as exc:
        raise DatabaseConfigError("无法删除数据库 Keychain 配置。") from exc
    return True


def redact_database_url(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)


def is_sqlite_url(database_url: str) -> bool:
    return make_url(database_url).get_backend_name() == "sqlite"


def sqlite_database_path(database_url: str) -> Path | None:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite" or parsed.database in {None, ":memory:"}:
        return None
    return Path(parsed.database).expanduser()


def database_recovery_result(engine: Engine) -> SQLiteRecoveryResult | None:
    with _ENGINE_RECOVERY_LOCK:
        return _ENGINE_RECOVERY_RESULTS.get(engine)


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


def create_database_engine(database_url: str | None = None) -> Engine:
    url = validate_database_url(database_url or get_database_url())
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        path = sqlite_database_path(url)
        recovery_result = None
        if path is not None:
            recovery_result = prepare_sqlite_database(path)
        engine = create_engine(
            url,
            connect_args={"timeout": SQLITE_BUSY_TIMEOUT_MS / 1000, "check_same_thread": False},
        )
        event.listen(engine, "connect", _configure_sqlite_connection)
        if recovery_result is not None:
            with _ENGINE_RECOVERY_LOCK:
                _ENGINE_RECOVERY_RESULTS[engine] = recovery_result
        return engine

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=5,
        max_overflow=5,
        connect_args={
            "connect_timeout": 5,
            "application_name": "sjtu-learning-assistant",
        },
    )


def check_database(engine: Engine) -> DatabaseHealth:
    try:
        with engine.connect() as connection:
            if engine.dialect.name == "sqlite":
                database_rows = connection.exec_driver_sql("PRAGMA database_list").all()
                database = next(
                    (str(row[2]) for row in database_rows if row[1] == "main"),
                    ":memory:",
                )
                version = str(connection.exec_driver_sql("SELECT sqlite_version()").scalar_one())
                return DatabaseHealth(
                    dialect="sqlite",
                    database=database or ":memory:",
                    user="local",
                    server_version=version,
                )

            row = connection.execute(
                text(
                    "SELECT current_database(), current_user, "
                    "current_setting('server_version')"
                )
            ).one()
    except SQLAlchemyError as exc:
        raise DatabaseConfigError("数据库连接失败。") from exc
    return DatabaseHealth(
        dialect="postgresql",
        database=str(row[0]),
        user=str(row[1]),
        server_version=str(row[2]),
    )
