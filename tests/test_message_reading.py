from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from desktop_app import DesktopBridge
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.desktop_database import SCHEMA_VERSION, bootstrap_sqlite, get_schema_version
from sjtu_learning_assistant.mail_client import EmailRecord, _parse_message, fetch_incremental_mail
from sjtu_learning_assistant.models import Email
from sjtu_learning_assistant.repository import persist_canvas_data, persist_emails


class MailBodyTests(unittest.TestCase):
    def test_multipart_prefers_plain_text_and_ignores_attachment(self) -> None:
        raw = (
            b"Subject: =?utf-8?b?5rWL6K+V?=\r\n"
            b"From: Sender <sender@example.com>\r\n"
            b"Content-Type: multipart/mixed; boundary=x\r\n\r\n"
            b"--x\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nplain body\r\n"
            b"--x\r\nContent-Type: text/plain\r\n"
            b"Content-Disposition: attachment; filename=a.txt\r\n\r\n"
            b"secret attachment\r\n--x--\r\n"
        )
        record = _parse_message(
            email_address="student@example.com", uid_validity="1", uid=2,
            metadata=b'FLAGS () INTERNALDATE "22-Sep-2026 12:00:00 +0800"', headers=raw,
        )
        self.assertEqual("plain body", record.body_text)
        self.assertNotIn("attachment", record.body_text or "")

    def test_html_only_is_plain_text_without_hidden_executable_content(self) -> None:
        raw = (
            b"Subject: HTML\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
            b"<h1>Hello</h1><script>alert('x')</script><p>World &amp; all</p>"
        )
        record = _parse_message(
            email_address="student@example.com", uid_validity="1", uid=3,
            metadata=b"FLAGS (\\Seen)", headers=raw,
        )
        self.assertEqual("Hello\nWorld & all", record.body_text)
        self.assertNotIn("script", record.body_text or "")
        self.assertFalse(record.is_unread)

    def test_header_only_input_remains_supported(self) -> None:
        record = _parse_message(
            email_address="student@example.com", uid_validity="1", uid=4,
            metadata=b"FLAGS ()", headers=b"Subject: Header only\r\n\r\n",
        )
        self.assertEqual("Header only", record.subject)
        self.assertIsNone(record.body_text)

    def test_incremental_fetch_uses_body_peek_without_marking_seen(self) -> None:
        raw = b"Subject: Peek\r\nContent-Type: text/plain\r\n\r\nBody"

        class FakeIMAP:
            queries: list[str] = []

            def __init__(self, *_args, **_kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def login(self, *_args): return "OK", []
            def select(self, *_args, **_kwargs): return "OK", []
            def response(self, name): return name, [b"99"]
            def uid(self, command, *args):
                if command == "search": return "OK", [b"7"]
                self.queries.append(str(args[-1]))
                return "OK", [(b'7 (FLAGS () INTERNALDATE "22-Sep-2026 12:00:00 +0800")', raw), b")"]

        with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", FakeIMAP):
            result = fetch_incremental_mail("student@example.com", "not-a-real-secret", None)
        self.assertEqual("Body", result.messages[0].body_text)
        self.assertEqual(["(BODY.PEEK[] FLAGS INTERNALDATE)"], FakeIMAP.queries)


class MessageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(default_sqlite_url(Path(self.temp.name) / "app.db"))
        bootstrap_sqlite(self.engine)
        self.course = {"id": 1, "name": "课程"}
        self.announcement = {
            "id": 11, "title": "公告", "message": "<p>公告正文</p><script>bad()</script>",
            "posted_at": "2026-09-22T01:00:00Z", "html_url": "https://example.edu/a",
        }
        self.assignment = {
            "id": 12, "name": "作业", "description": "<p>作业说明 &amp; 要求</p><style>bad</style>",
            "updated_at": "2026-09-22T02:00:00Z", "html_url": "https://example.edu/b",
        }
        self.email = EmailRecord(
            source_id="mail:1", subject="邮件", sender_name="教师", sender_address="teacher@example.com",
            sent_at=datetime(2026, 9, 22, 3, tzinfo=timezone.utc),
            received_at=datetime(2026, 9, 22, 3, tzinfo=timezone.utc),
            body_preview="邮件正文", is_unread=True, raw_data={"uid": 1}, body_text="邮件正文",
        )
        self.sync()
        self.service = DashboardService(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def sync(self) -> None:
        persist_canvas_data(
            self.engine, raw_courses=[self.course],
            announcements_by_course={"1": [self.announcement]},
            assignments_by_course={"1": [self.assignment]},
        )
        persist_emails(self.engine, [self.email], cursor="cursor")

    def test_messages_have_stable_ids_all_kinds_and_local_unread(self) -> None:
        rows = self.service.messages("all")
        self.assertEqual({"email", "announcement", "assignment"}, {row["kind"] for row in rows})
        self.assertEqual({"mail:1", "11", "12"}, {row["source_id"] for row in rows})
        self.assertTrue(all(row["is_unread"] for row in rows))

    def test_details_are_safe_plain_text_and_do_not_mark_read(self) -> None:
        self.assertEqual("邮件正文", self.service.message_detail("email", "mail:1")["body"])
        self.assertEqual("公告正文", self.service.message_detail("announcement", "11")["body"])
        self.assertEqual("作业说明 & 要求", self.service.message_detail("assignment", "12")["body"])
        self.assertTrue(self.service.message_detail("email", "mail:1")["is_unread"])

    def test_single_and_kind_wide_mark_read_are_local_only_and_sync_idempotent(self) -> None:
        self.assertEqual(1, self.service.message_mark_read("email", ["mail:1"])["updated"])
        self.assertEqual(1, self.service.message_mark_read("announcement", None)["updated"])
        self.assertTrue(self.service.message_detail("assignment", "12")["is_unread"])
        self.assertEqual(1, self.service.message_mark_read("all", None)["updated"])
        self.assertEqual(0, self.service.message_mark_read("all", None)["updated"])
        self.assertFalse(self.service.message_detail("email", "mail:1")["is_unread"])
        self.assertFalse(self.service.message_detail("assignment", "12")["is_unread"])
        self.assertEqual(0, self.service.overview()["unread_emails"])
        with Session(self.engine) as session:
            self.assertTrue(session.scalar(select(Email.is_unread).where(Email.source_id == "mail:1")))
        self.email = EmailRecord(**{**self.email.__dict__, "body_text": "更新正文"})
        self.sync()
        self.assertFalse(self.service.message_detail("email", "mail:1")["is_unread"])
        self.assertEqual("更新正文", self.service.message_detail("email", "mail:1")["body"])


class BridgeMessageValidationTests(unittest.TestCase):
    class Service:
        def messages(self, kind): return [{"kind": kind}]
        def message_detail(self, kind, source_id): return {"kind": kind, "source_id": source_id}
        def message_mark_read(self, kind, ids): return {"kind": kind, "ids": ids}

    def setUp(self) -> None:
        self.bridge = DesktopBridge(self.Service())

    def test_new_actions_are_allowlisted_and_strictly_validated(self) -> None:
        detail = self.bridge.invoke("message_detail", {"kind": "assignment", "source_id": "12"})
        self.assertTrue(detail["ok"])
        marked = self.bridge.invoke("message_mark_read", {"kind": "email", "ids": ["a", "a"]})
        self.assertEqual(["a"], marked["data"]["ids"])
        all_marked = self.bridge.invoke("message_mark_read", {"kind": "announcement"})
        self.assertIsNone(all_marked["data"]["ids"])
        explicit_all = self.bridge.invoke("message_mark_read", {"kind": "announcement", "all": True})
        self.assertIsNone(explicit_all["data"]["ids"])
        invalid_payloads = (
            {"kind": "all", "source_id": "12"},
            {"kind": "email", "ids": []},
            {"kind": "email", "ids": ["a"], "all": True},
            {"kind": "email", "all": "yes"},
            {"kind": "email", "ids": ["a"], "extra": 1},
        )
        actions = ("message_detail", "message_mark_read", "message_mark_read", "message_mark_read", "message_mark_read")
        for action, payload in zip(actions, invalid_payloads):
            with self.subTest(payload=payload):
                self.assertFalse(self.bridge.invoke(action, payload)["ok"])


class LegacySQLiteUpgradeTests(unittest.TestCase):
    def test_bootstrap_adds_body_text_idempotently_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = create_database_engine(default_sqlite_url(Path(directory) / "legacy.db"))
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE emails (id INTEGER PRIMARY KEY, source_id VARCHAR(255) NOT NULL UNIQUE, subject TEXT NOT NULL)"
                )
                connection.exec_driver_sql("INSERT INTO emails (source_id, subject) VALUES ('old', '保留')")
            self.assertEqual(SCHEMA_VERSION, bootstrap_sqlite(engine))
            self.assertEqual(SCHEMA_VERSION, bootstrap_sqlite(engine))
            self.assertIn("body_text", {column["name"] for column in inspect(engine).get_columns("emails")})
            with engine.connect() as connection:
                self.assertEqual("保留", connection.exec_driver_sql("SELECT subject FROM emails WHERE source_id='old'").scalar_one())
            self.assertEqual(SCHEMA_VERSION, get_schema_version(engine))
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
