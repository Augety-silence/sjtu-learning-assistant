"""SQLite schema bootstrap and safe one-time PostgreSQL data import."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    func,
    inspect,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection

from sjtu_learning_assistant.database import (
    DATABASE_URL_ENV,
    DatabaseConfigError,
    create_database_engine,
    get_postgres_database_url,
)
from sjtu_learning_assistant.models import Base

SCHEMA_VERSION = "0011"
SCHEMA_VERSION_TABLE = "desktop_schema_version"
BUSINESS_TABLES = (
    "courses",
    "emails",
    "email_attachments",
    "sync_runs",
    "sync_state",
    "announcements",
    "assignments",
    "cloud_files",
    "submissions",
    "course_folders",
    "course_files",
    "course_modules",
    "course_module_items",
    "items",
    "notification_events",
)

metadata = MetaData()
schema_version = Table(
    SCHEMA_VERSION_TABLE,
    metadata,
    Column("id", Integer, primary_key=True),
    Column("version", String(32), nullable=False),
    Column("installed_at", DateTime(timezone=True), nullable=False),
    Column("postgres_imported_at", DateTime(timezone=True)),
)


class DesktopDatabaseError(RuntimeError):
    """Raised when SQLite bootstrap or import cannot complete safely."""


class NonEmptyDatabaseError(DesktopDatabaseError):
    """Raised when an import would overwrite existing SQLite business data."""


@dataclass(frozen=True)
class ImportSummary:
    rows_by_table: Mapping[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_table.values())

    def format(self) -> str:
        details = ", ".join(
            f"{table}={self.rows_by_table[table]}" for table in BUSINESS_TABLES
        )
        return f"共导入 {self.total_rows} 行（{details}）"


@dataclass(frozen=True)
class DesktopInitialization:
    schema_version: str
    import_summary: ImportSummary | None = None


def _require_sqlite(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        raise DesktopDatabaseError("桌面数据库操作仅支持 SQLite 目标库。")


def bootstrap_sqlite(engine: Engine) -> str:
    """Create the current metadata schema and persist its bootstrap version."""
    _require_sqlite(engine)
    Base.metadata.create_all(engine)
    metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        # create_all intentionally does not alter existing SQLite tables. Apply the
        # additive desktop migration explicitly and only after inspecting the table.
        email_columns = {
            str(column["name"])
            for column in inspect(connection).get_columns("emails")
        }
        if "body_text" not in email_columns:
            connection.exec_driver_sql("ALTER TABLE emails ADD COLUMN body_text TEXT")
        if "body_html" not in email_columns:
            connection.exec_driver_sql("ALTER TABLE emails ADD COLUMN body_html TEXT")
        file_columns = {
            str(column["name"])
            for column in inspect(connection).get_columns("course_files")
        }
        ai_columns = {
            "ai_category": "VARCHAR(32)",
            "ai_fingerprint": "VARCHAR(64)",
            "ai_model": "VARCHAR(64)",
            "ai_classified_at": "DATETIME",
        }
        for name, sql_type in ai_columns.items():
            if name not in file_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE course_files ADD COLUMN {name} {sql_type}"
                )
        manual_columns = (
            ("manual_category", "VARCHAR(32)"),
            ("manual_folder_id", "INTEGER"),
            ("manual_override", "BOOLEAN NOT NULL DEFAULT 0"),
        )
        for name, sql_type in manual_columns:
            if name not in file_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE course_files ADD COLUMN " + name + " " + sql_type
                )
        existing = connection.scalar(select(schema_version.c.id).where(schema_version.c.id == 1))
        if existing is None:
            connection.execute(
                insert(schema_version).values(
                    id=1,
                    version=SCHEMA_VERSION,
                    installed_at=now,
                    postgres_imported_at=None,
                )
            )
        else:
            connection.execute(
                update(schema_version)
                .where(schema_version.c.id == 1)
                .values(version=SCHEMA_VERSION)
            )
    return SCHEMA_VERSION


def get_schema_version(engine: Engine) -> str | None:
    _require_sqlite(engine)
    if not inspect(engine).has_table(SCHEMA_VERSION_TABLE):
        return None
    with engine.connect() as connection:
        return connection.scalar(
            select(schema_version.c.version).where(schema_version.c.id == 1)
        )


def _business_row_counts(connection: Connection) -> dict[str, int]:
    return {
        table_name: int(
            connection.scalar(select(func.count()).select_from(Base.metadata.tables[table_name]))
            or 0
        )
        for table_name in BUSINESS_TABLES
    }


def _copy_table(
    source: Connection,
    target: Connection,
    table_name: str,
) -> tuple[int, list[tuple[int, int | None]]]:
    table = Base.metadata.tables[table_name]
    rows = [dict(row) for row in source.execute(select(table)).mappings()]
    datetime_columns = tuple(
        column.name for column in table.columns if isinstance(column.type, DateTime)
    )
    for row in rows:
        for column_name in datetime_columns:
            value = row.get(column_name)
            if isinstance(value, datetime) and value.tzinfo is not None:
                # SQLite stores DateTime values without an offset. Normalize to UTC so
                # callers that interpret naive database timestamps as UTC keep the instant.
                row.__setitem__(
                    column_name, value.astimezone(timezone.utc).replace(tzinfo=None)
                )
    parent_links: list[tuple[int, int | None]] = []
    if table_name == "course_folders":
        for row in rows:
            parent_links.append((int(row["id"]), row.get("parent_folder_id")))
            row["parent_folder_id"] = None
    if rows:
        target.execute(insert(table), rows)
    return len(rows), parent_links


def _postgres_import_marker(connection: Connection) -> datetime | None:
    return connection.scalar(
        select(schema_version.c.postgres_imported_at).where(schema_version.c.id == 1)
    )


def import_postgres_data(
    sqlite_engine: Engine,
    *,
    postgres_url: str | None = None,
    source_engine: Engine | None = None,
    copy_table: Callable[
        [Connection, Connection, str], tuple[int, list[tuple[int, int | None]]]
    ] = _copy_table,
) -> ImportSummary:
    """Copy every business table atomically, preserving values and identifiers."""
    _require_sqlite(sqlite_engine)
    bootstrap_sqlite(sqlite_engine)
    owns_source = source_engine is None
    if source_engine is None:
        source_engine = create_database_engine(
            postgres_url or get_postgres_database_url(required=True)
        )
    if source_engine.dialect.name != "postgresql" and owns_source:
        source_engine.dispose()
        raise DesktopDatabaseError("导入源必须是 PostgreSQL。")

    try:
        with source_engine.connect() as source, sqlite_engine.begin() as target:
            counts = _business_row_counts(target)
            nonempty = {name: count for name, count in counts.items() if count}
            if nonempty:
                detail = ", ".join(f"{name}={count}" for name, count in nonempty.items())
                raise NonEmptyDatabaseError(
                    f"SQLite 业务库非空，已拒绝导入（{detail}）。"
                )
            if _postgres_import_marker(target) is not None:
                raise DesktopDatabaseError("该 SQLite 库已经完成过 PostgreSQL 导入。")

            imported: dict[str, int] = {}
            folder_links: list[tuple[int, int | None]] = []
            for table_name in BUSINESS_TABLES:
                count, links = copy_table(source, target, table_name)
                imported[table_name] = count
                folder_links.extend(links)
                if table_name == "course_folders" and folder_links:
                    folders = Base.metadata.tables["course_folders"]
                    for folder_id, parent_id in folder_links:
                        if parent_id is not None:
                            target.execute(
                                update(folders)
                                .where(folders.c.id == folder_id)
                                .values(parent_folder_id=parent_id)
                            )

            target.execute(
                update(schema_version)
                .where(schema_version.c.id == 1)
                .values(postgres_imported_at=datetime.now(timezone.utc))
            )
        return ImportSummary(imported)
    finally:
        if owns_source:
            source_engine.dispose()


def initialize_desktop_database(
    engine: Engine,
    *,
    skip_import: bool = False,
    postgres_url: str | None = None,
) -> DesktopInitialization:
    """Bootstrap SQLite and, when available, perform its one-time safe import."""
    version = bootstrap_sqlite(engine)
    if skip_import:
        return DesktopInitialization(schema_version=version)

    with engine.connect() as connection:
        if _postgres_import_marker(connection) is not None:
            return DesktopInitialization(schema_version=version)
        if any(_business_row_counts(connection).values()):
            return DesktopInitialization(schema_version=version)

    source_url = postgres_url
    # An explicit SQLite URL is commonly used by tests and previews. It must fully
    # override the Keychain-backed legacy database unless import-postgres is invoked.
    if source_url is None and os.environ.get(DATABASE_URL_ENV):
        return DesktopInitialization(schema_version=version)
    if source_url is None:
        try:
            source_url = get_postgres_database_url(required=False)
        except DatabaseConfigError as exc:
            raise DesktopDatabaseError("无法读取 PostgreSQL 导入配置。") from exc
    if not source_url:
        return DesktopInitialization(schema_version=version)
    summary = import_postgres_data(engine, postgres_url=source_url)
    return DesktopInitialization(schema_version=version, import_summary=summary)
