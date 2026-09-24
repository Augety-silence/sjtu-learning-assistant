from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sjtu_learning_assistant.database import (
    create_database_engine,
    database_recovery_result,
    default_sqlite_url,
)
from sjtu_learning_assistant.desktop_database import (
    BUSINESS_TABLES,
    DesktopDatabaseError,
    NonEmptyDatabaseError,
    SCHEMA_VERSION,
    _copy_table,
    bootstrap_sqlite,
    get_schema_version,
    import_postgres_data,
    initialize_desktop_database,
)
from sjtu_learning_assistant.mail_client import EmailRecord
from sjtu_learning_assistant.models import (
    Announcement,
    Assignment,
    Base,
    Course,
    CourseFile,
    CourseFolder,
    CourseModule,
    CourseModuleItem,
    Email,
    NotificationEvent,
    SyncState,
    UnifiedItem,
)
from sjtu_learning_assistant.notifications import (
    NotificationCandidate,
    SqlNotificationEventStore,
)
from sjtu_learning_assistant.repository import persist_canvas_data, persist_emails
from sjtu_learning_assistant.sqlite_recovery import (
    SQLiteRecoveryError,
    check_sqlite_integrity,
    create_sqlite_backup,
    prepare_sqlite_database,
    read_sqlite_schema_version,
    sqlite_backup_paths,
)


COURSE = {
    "id": 95040,
    "name": "文本分析与大模型",
    "course_code": "MGTS3605",
    "term": {"name": "2026-2027 Fall"},
    "updated_at": "2026-09-21T00:00:00Z",
}
ANNOUNCEMENT = {
    "id": 1001,
    "title": "课程通知",
    "posted_at": "2026-09-21T01:00:00Z",
    "html_url": "https://oc.sjtu.edu.cn/announcement/1001",
}
ASSIGNMENT = {
    "id": 2001,
    "name": "作业一",
    "due_at": "2026-09-28T15:59:00Z",
    "points_possible": 100,
}
FOLDER = {"id": 3001, "name": "课件", "files_count": 1, "folders_count": 0}
FILE = {
    "id": 4001,
    "folder_id": 3001,
    "display_name": "lecture-01.pdf",
    "filename": "lecture-01.pdf",
    "size": 1024,
    "updated_at": "2026-09-21T02:00:00Z",
}
MODULE = {"id": 5001, "name": "第一周", "position": 1, "items_count": 1}
MODULE_ITEM = {
    "id": 6001,
    "module_id": 5001,
    "content_id": 4001,
    "title": "第一讲课件",
    "type": "File",
    "position": 1,
}
EMAIL = EmailRecord(
    source_id="mail:123:456",
    subject="测试邮件",
    sender_name="测试发件人",
    sender_address="sender@example.com",
    sent_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
    received_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
    body_preview="摘要",
    is_unread=True,
    raw_data={"uid": 456, "nested": {"ok": True}},
)


def read_alembic_revision(path: Path) -> str | None:
    with sqlite3.connect(path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0]) if row is not None else None


def seed_all(engine) -> None:
    persist_canvas_data(
        engine,
        raw_courses=[COURSE],
        announcements_by_course={"95040": [ANNOUNCEMENT]},
        assignments_by_course={"95040": [ASSIGNMENT]},
        folders_by_course={"95040": [FOLDER]},
        files_by_course={"95040": [FILE]},
        modules_by_course={"95040": [MODULE]},
        module_items_by_course={"95040": [MODULE_ITEM]},
        announcement_cursors={"95040": "announcement-etag"},
        assignment_cursors={"95040": "assignment-etag"},
        file_cursors={"95040": "file-etag"},
    )
    persist_emails(engine, [EMAIL], cursor='{"last_uid":456}')


class SQLiteRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "app.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_value(self, value: str) -> None:
        engine = create_database_engine(default_sqlite_url(self.path))
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE IF NOT EXISTS recovery_probe (value TEXT NOT NULL)"
                )
                connection.exec_driver_sql("DELETE FROM recovery_probe")
                connection.exec_driver_sql(
                    "INSERT INTO recovery_probe (value) VALUES (?)", (value,)
                )
        finally:
            engine.dispose()

    def _read_value(self, path: Path) -> str:
        engine = create_database_engine(default_sqlite_url(path))
        try:
            with engine.connect() as connection:
                return str(
                    connection.exec_driver_sql(
                        "SELECT value FROM recovery_probe"
                    ).scalar_one()
                )
        finally:
            engine.dispose()

    def test_quick_check_rejects_non_database_file(self) -> None:
        self.path.write_bytes(b"not a sqlite database")
        self.assertFalse(check_sqlite_integrity(self.path))

    def test_transient_database_errors_do_not_trigger_quarantine(self) -> None:
        self._write_value("healthy")
        transient = sqlite3.OperationalError("database is locked")
        transient.sqlite_errorcode = sqlite3.SQLITE_BUSY
        with patch(
            "sjtu_learning_assistant.sqlite_recovery.sqlite3.connect",
            side_effect=transient,
        ), self.assertRaisesRegex(SQLiteRecoveryError, "暂时无法检查"):
            prepare_sqlite_database(self.path)

        self.assertTrue(self.path.exists())
        self.assertFalse(tuple(self.path.parent.glob("app.db.corrupt.*")))

    def test_backup_rotation_keeps_three_verified_snapshots(self) -> None:
        for value in ("one", "two", "three", "four"):
            self._write_value(value)
            create_sqlite_backup(self.path)

        backups = sqlite_backup_paths(self.path)
        self.assertEqual(["four", "three", "two"], [self._read_value(path) for path in backups])
        self.assertTrue(all(check_sqlite_integrity(path, full=True) for path in backups))

    def test_corrupt_database_is_quarantined_and_latest_backup_restored(self) -> None:
        self._write_value("recoverable")
        create_sqlite_backup(self.path)
        self.path.write_bytes(b"corrupt")

        engine = create_database_engine(default_sqlite_url(self.path))
        try:
            result = database_recovery_result(engine)
            self.assertIsNotNone(result)
            self.assertEqual("restored", result.status)
            self.assertEqual(1, result.backup_index)
            with engine.connect() as connection:
                self.assertEqual(
                    "recoverable",
                    connection.exec_driver_sql(
                        "SELECT value FROM recovery_probe"
                    ).scalar_one(),
                )
        finally:
            engine.dispose()
        self.assertEqual(1, len(tuple(self.path.parent.glob("app.db.corrupt.*"))))

    def test_corrupt_database_without_backup_is_quarantined_for_rebuild(self) -> None:
        self.path.write_bytes(b"corrupt")
        result = prepare_sqlite_database(self.path)

        self.assertEqual("rebuilt", result.status)
        self.assertFalse(self.path.exists())
        self.assertEqual(1, len(tuple(self.path.parent.glob("app.db.corrupt.*"))))

    def test_initialize_snapshots_database_before_schema_upgrade(self) -> None:
        engine = create_database_engine(default_sqlite_url(self.path))
        try:
            bootstrap_sqlite(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE alembic_version SET version_num='0016'"
                )
                connection.exec_driver_sql(
                    "UPDATE desktop_schema_version SET version='legacy' WHERE id=1"
                )
            initialize_desktop_database(engine, skip_import=True)
        finally:
            engine.dispose()

        backup = sqlite_backup_paths(self.path)[0]
        self.assertEqual("legacy", read_sqlite_schema_version(backup))
        self.assertEqual(SCHEMA_VERSION, read_sqlite_schema_version(self.path))

    def test_concurrent_startup_serializes_single_alembic_upgrade(self) -> None:
        engine = create_database_engine(default_sqlite_url(self.path))
        try:
            bootstrap_sqlite(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE alembic_version SET version_num='0016'"
                )
        finally:
            engine.dispose()

        def initialize_once(_index: int) -> str:
            local_engine = create_database_engine(default_sqlite_url(self.path))
            try:
                return initialize_desktop_database(
                    local_engine, skip_import=True
                ).schema_version
            finally:
                local_engine.dispose()

        with ThreadPoolExecutor(max_workers=2) as executor:
            versions = tuple(executor.map(initialize_once, range(2)))

        self.assertEqual((SCHEMA_VERSION, SCHEMA_VERSION), versions)
        self.assertEqual(SCHEMA_VERSION, read_alembic_revision(self.path))

    def test_failed_schema_upgrade_restores_pre_migration_snapshot(self) -> None:
        engine = create_database_engine(default_sqlite_url(self.path))
        try:
            bootstrap_sqlite(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE alembic_version SET version_num='0016'"
                )
                connection.exec_driver_sql(
                    "UPDATE desktop_schema_version SET version='legacy' WHERE id=1"
                )
            with patch(
                "sjtu_learning_assistant.desktop_database.command.upgrade",
                side_effect=RuntimeError("simulated migration failure"),
            ), self.assertRaisesRegex(DesktopDatabaseError, "已自动恢复"):
                initialize_desktop_database(engine, skip_import=True)
        finally:
            engine.dispose()

        self.assertEqual("legacy", read_sqlite_schema_version(self.path))
        with sqlite3.connect(self.path) as connection:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
        self.assertEqual("0016", revision)
        self.assertTrue(check_sqlite_integrity(self.path, full=True))


class SQLiteIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "app.db"
        self.engine = create_database_engine(default_sqlite_url(self.path))
        bootstrap_sqlite(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def test_schema_pragmas_and_sqlite_primary_keys(self) -> None:
        tables = set(inspect(self.engine).get_table_names())
        self.assertTrue(set(BUSINESS_TABLES).issubset(tables))
        self.assertEqual(SCHEMA_VERSION, get_schema_version(self.engine))
        with self.engine.connect() as connection:
            self.assertEqual(1, connection.exec_driver_sql("PRAGMA foreign_keys").scalar())
            self.assertEqual("wal", connection.exec_driver_sql("PRAGMA journal_mode").scalar())
            self.assertEqual(5000, connection.exec_driver_sql("PRAGMA busy_timeout").scalar())
            pk_type = next(
                column["type"]
                for column in inspect(connection).get_columns("courses")
                if column["name"] == "id"
            )
        self.assertEqual("INTEGER", str(pk_type))
        for table_name in BUSINESS_TABLES:
            with self.subTest(table=table_name):
                columns = inspect(self.engine).get_columns(table_name)
                primary_key = next(column for column in columns if column.get("name") == "id")
                self.assertEqual("INTEGER", str(primary_key.get("type")))

    def test_alembic_revision_is_authoritative_and_matches_orm_schema(self) -> None:
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE desktop_schema_version SET version='stale' WHERE id=1"
            )
        self.assertEqual(SCHEMA_VERSION, get_schema_version(self.engine))
        self.assertEqual(SCHEMA_VERSION, read_alembic_revision(self.path))

        inspector = inspect(self.engine)
        for table in Base.metadata.sorted_tables:
            with self.subTest(table=table.name):
                actual = {
                    str(column["name"])
                    for column in inspector.get_columns(table.name)
                }
                self.assertEqual(set(table.columns.keys()), actual)

    def test_bootstrap_is_idempotent(self) -> None:
        self.assertEqual(SCHEMA_VERSION, bootstrap_sqlite(self.engine))
        self.assertEqual(SCHEMA_VERSION, bootstrap_sqlite(self.engine))
        with self.engine.connect() as connection:
            count = connection.exec_driver_sql(
                "SELECT count(id) FROM desktop_schema_version"
            ).scalar_one()
        self.assertEqual(1, count)

    def test_explicit_sqlite_url_never_reads_postgres_keychain(self) -> None:
        environment = dict(zip(("SJTU_DATABASE_URL",), (default_sqlite_url(self.path),)))
        with patch.dict(os.environ, environment), patch(
            "sjtu_learning_assistant.desktop_database.get_postgres_database_url"
        ) as get_postgres_url:
            result = initialize_desktop_database(self.engine)
        self.assertIsNone(result.import_summary)
        get_postgres_url.assert_not_called()

    def test_all_repository_upserts_are_idempotent_on_sqlite(self) -> None:
        seed_all(self.engine)
        seed_all(self.engine)
        expected = {
            Course: 1,
            Announcement: 1,
            Assignment: 1,
            CourseFolder: 1,
            CourseFile: 1,
            CourseModule: 1,
            CourseModuleItem: 1,
            Email: 1,
            UnifiedItem: 4,
        }
        with Session(self.engine) as session:
            for model, count in expected.items():
                with self.subTest(table=model.__tablename__):
                    self.assertEqual(count, session.scalar(select(func.count(model.id))))
            course = session.scalar(select(Course))
            self.assertEqual(COURSE, course.raw_data)
            self.assertGreaterEqual(session.scalar(select(func.count(SyncState.id))), 10)

    def test_foreign_keys_and_single_source_constraint_are_enforced(self) -> None:
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(
                insert(Assignment).values(
                    source_id="orphan",
                    course_id=999,
                    name="orphan",
                    last_seen_at=datetime.now(timezone.utc),
                    raw_data={},
                )
            )

        seed_all(self.engine)
        with Session(self.engine) as session:
            announcement_id = session.scalar(select(Announcement.id))
            email_id = session.scalar(select(Email.id))
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(
                insert(UnifiedItem).values(
                    source="test",
                    item_type="invalid",
                    source_id="invalid",
                    title="invalid",
                    announcement_id=announcement_id,
                    email_id=email_id,
                    raw_data={},
                )
            )

    def test_notification_upsert_is_idempotent_on_sqlite(self) -> None:
        store = SqlNotificationEventStore(self.engine)
        candidate = NotificationCandidate(
            event_key="new_file:4001",
            event_type="new_file",
            item_id=None,
            title="新文件",
            subtitle="课程",
            body="详情",
        )
        first = store.reserve([candidate], suppressed=True)
        second = store.reserve([candidate], suppressed=True)
        store.record_sent([candidate])
        store.advance_canvas_cursor(42)
        store.advance_canvas_cursor(42)
        self.assertEqual([candidate], first)
        self.assertEqual([], second)
        self.assertEqual(42, store.load_canvas_cursor())
        with Session(self.engine) as session:
            self.assertEqual(1, session.scalar(select(func.count(NotificationEvent.id))))


class SQLiteImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.source = create_database_engine(default_sqlite_url(root / "source.db"))
        self.target = create_database_engine(default_sqlite_url(root / "target.db"))
        bootstrap_sqlite(self.source)
        bootstrap_sqlite(self.target)
        seed_all(self.source)

    def tearDown(self) -> None:
        self.source.dispose()
        self.target.dispose()
        self.temporary.cleanup()

    def test_empty_database_import_preserves_values_and_reports_exact_counts(self) -> None:
        summary = import_postgres_data(self.target, source_engine=self.source)
        with self.source.connect() as source, self.target.connect() as target:
            expected = {
                name: int(
                    source.scalar(
                        select(func.count()).select_from(Base.metadata.tables[name])
                    )
                )
                for name in BUSINESS_TABLES
            }
            actual = {
                name: int(
                    target.scalar(
                        select(func.count()).select_from(Base.metadata.tables[name])
                    )
                )
                for name in BUSINESS_TABLES
            }
        self.assertEqual(expected, dict(summary.rows_by_table))
        self.assertEqual(expected, actual)
        with Session(self.source) as source_session, Session(self.target) as target_session:
            self.assertEqual(COURSE, target_session.scalar(select(Course)).raw_data)
            self.assertEqual(
                source_session.scalar(select(Course.id)),
                target_session.scalar(select(Course.id)),
            )
            imported_email = target_session.scalar(select(Email))
            self.assertEqual(EMAIL.raw_data, imported_email.raw_data)
            self.assertEqual(EMAIL.sent_at.replace(tzinfo=None), imported_email.sent_at)

    def test_nonempty_database_is_refused_without_changes(self) -> None:
        with self.target.begin() as connection:
            connection.execute(
                insert(Course).values(source_id="existing", name="已有", raw_data={})
            )
        with self.assertRaises(NonEmptyDatabaseError):
            import_postgres_data(self.target, source_engine=self.source)
        with Session(self.target) as session:
            self.assertEqual(["existing"], list(session.scalars(select(Course.source_id))))

    def test_import_failure_rolls_back_all_business_rows(self) -> None:
        def fail_after_first(source, target, table_name):
            result = _copy_table(source, target, table_name)
            if table_name == "emails":
                raise RuntimeError("simulated import failure")
            return result

        with self.assertRaisesRegex(RuntimeError, "simulated"):
            import_postgres_data(
                self.target,
                source_engine=self.source,
                copy_table=fail_after_first,
            )
        with self.target.connect() as connection:
            counts = {
                name: int(
                    connection.scalar(
                        select(func.count()).select_from(Base.metadata.tables[name])
                    )
                )
                for name in BUSINESS_TABLES
            }
        self.assertFalse(any(counts.values()))


if __name__ == "__main__":
    unittest.main()
