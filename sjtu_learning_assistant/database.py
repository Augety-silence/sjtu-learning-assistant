"""PostgreSQL configuration and engine helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

DATABASE_URL_ENV = "SJTU_DATABASE_URL"
KEYCHAIN_SERVICE = "SJTU Learning Assistant - PostgreSQL"
KEYCHAIN_ACCOUNT = "database-url"
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg:///sjtu_learning_assistant?host=/tmp&connect_timeout=5"
)


class DatabaseConfigError(RuntimeError):
    """Raised when database configuration cannot be loaded or validated."""


@dataclass(frozen=True)
class DatabaseHealth:
    database: str
    user: str
    server_version: str


def load_keyring_module():
    try:
        import keyring  # type: ignore[import-not-found]
        from keyring.errors import KeyringError  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DatabaseConfigError(
            "缺少 keyring 依赖。请先运行：python3 -m pip install -r requirements.txt"
        ) from exc
    return keyring, KeyringError


def validate_database_url(value: str) -> str:
    database_url = value.strip()
    try:
        parsed: URL = make_url(database_url)
    except Exception as exc:
        raise DatabaseConfigError("数据库连接地址格式不正确。") from exc
    if parsed.drivername != "postgresql+psycopg":
        raise DatabaseConfigError(
            "数据库地址必须使用 postgresql+psycopg:// 驱动格式。"
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


def get_database_url() -> str:
    """Load URL from deployment secret, Keychain, then safe local default."""
    from_environment = os.environ.get(DATABASE_URL_ENV)
    if from_environment:
        return validate_database_url(from_environment)

    keyring, KeyringError = load_keyring_module()
    try:
        from_keychain = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except KeyringError as exc:
        raise DatabaseConfigError(f"无法读取数据库 Keychain 配置：{exc}") from exc
    if from_keychain:
        return validate_database_url(from_keychain)
    return DEFAULT_DATABASE_URL


def save_database_url(database_url: str) -> None:
    validated = validate_database_url(database_url)
    keyring, KeyringError = load_keyring_module()
    try:
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, validated)
    except KeyringError as exc:
        raise DatabaseConfigError(f"无法保存数据库配置到 Keychain：{exc}") from exc


def delete_database_url() -> bool:
    keyring, KeyringError = load_keyring_module()
    try:
        existing = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        if existing is None:
            return False
        keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except KeyringError as exc:
        raise DatabaseConfigError(f"无法删除数据库 Keychain 配置：{exc}") from exc
    return True


def redact_database_url(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)


def create_database_engine(database_url: str | None = None) -> Engine:
    url = validate_database_url(database_url or get_database_url())
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
            row = connection.execute(
                text(
                    "SELECT current_database(), current_user, "
                    "current_setting('server_version')"
                )
            ).one()
    except SQLAlchemyError as exc:
        raise DatabaseConfigError(f"PostgreSQL 连接失败：{exc}") from exc
    return DatabaseHealth(
        database=str(row[0]), user=str(row[1]), server_version=str(row[2])
    )
