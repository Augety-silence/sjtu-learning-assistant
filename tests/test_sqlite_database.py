from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, insert, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.desktop_database import (
    BUSINESS_TABLES,
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
