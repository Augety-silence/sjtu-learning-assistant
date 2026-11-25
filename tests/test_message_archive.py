from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.agent_runtime.tools import ReadOnlyToolRegistry
from sjtu_learning_assistant.agent_runtime import AgentLoop, AgentPreset
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.mail_client import EmailRecord, fetch_incremental_mail
from sjtu_learning_assistant.mail_sync import MailCheckError
from sjtu_learning_assistant.message_archive import search_archive
from sjtu_learning_assistant.models import Email
from sjtu_learning_assistant.repository import get_sync_state, persist_canvas_data, persist_course_catalog, persist_emails


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_database_engine(default_sqlite_url(self.root / "test.db"))
        bootstrap_sqlite(self.engine)
        self.account = "student@example.com"
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.records = [EmailRecord(source_id=f"{self.account}:INBOX:7:{i}", subject=f"通知 {i}",
            sender_name="老师", sender_address="teacher@example.com", sent_at=now + timedelta(days=i),
            received_at=now, body_preview="前面的摘要", body_text="前言" * 400 + ("考试 最早通知 100%" if i == 1 else "普通正文"),
            is_unread=True, raw_data={"rich_content_fetched": True}) for i in range(1, 152)]
        persist_emails(self.engine, self.records, cursor="legacy")
        store = SettingsStore(self.root / "settings.json")
        store.update({"mail_account": self.account})
        self.service = DashboardService(self.engine, settings_store=store, archive_root=self.root / "archive")

    def tearDown(self):
        self.service.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_page_101_and_full_body_search_survive_reopen(self):
        self.assertEqual(100, len(self.service.messages("email")))
        page = self.service.message_archive("email", offset=100)
        self.assertEqual(151, page["total"])
        self.assertEqual(51, len(page["items"]))
        self.assertEqual(f"{self.account}:INBOX:7:1", page["items"][-1]["source_id"])
        page = self.service.message_archive("email", query="老师 考试 100%")
        self.assertEqual(1, page["total"])
        self.assertIn("考试", page["items"][0]["preview"])
        second = create_database_engine(default_sqlite_url(self.root / "test.db"))
        try:
            self.assertEqual(1, search_archive(second, query="考试", account=self.account)["total"])
        finally:
            second.dispose()

    def test_query_read_only_updates_matches_and_repeat_sync_preserves_read(self):
        self.assertEqual(1, self.service.message_archive_mark_read("email", "考试")["updated"])
        self.assertEqual(150, self.service.message_archive("email")["unread"])
        persist_emails(self.engine, self.records, cursor="repeat")
        self.assertEqual(150, self.service.message_archive("email")["unread"])
        with Session(self.engine) as session:
            self.assertEqual(151, session.scalar(select(func.count()).select_from(Email)))

    def test_account_isolation_and_ai_body_citation(self):
        other = replace(self.records[0], source_id="other@example.com:INBOX:7:1", subject="别人的考试")
        persist_emails(self.engine, [other], cursor="other")
        self.assertEqual(151, self.service.message_archive("email")["total"])
        registry = ReadOnlyToolRegistry(self.engine, mail_account=self.account)
        page, _ = registry._search_messages({"query": "老师 考试", "kind": "email"})
        self.assertEqual(1, page["count"])
        self.assertIn("ref=email:", page["items"][0]["citation"])
        self.assertNotIn(self.account, str(page))
        detail, _ = registry._get_message_detail({"kind": "email", "ref": page["items"][0]["ref"]})
        self.assertIn("考试", detail["body"])
        self.assertIn("citation", detail)

    def test_history_course_search_uses_current_account_catalog(self):
        courses = [{"id": 1, "name": "当前课程"}, {"id": 2, "name": "往期课程"}, {"id": 3, "name": "其他账户课程"}]
        for course in courses:
            persist_canvas_data(self.engine, raw_courses=[course], announcements_by_course={},
                assignments_by_course={str(course["id"]): [{"id": course["id"] * 10, "name": "期末作业", "description": "<h2>提交要求</h2><p>排版要求</p>"}]}, default_scope=course["id"] == 1)
        persist_course_catalog(self.engine, courses[:2])
        page = self.service.message_archive("assignment", query="排版要求")
        self.assertEqual(2, page["total"])
        self.assertEqual({"当前课程", "往期课程"}, {row["source_label"] for row in page["items"]})

    def prefetch(self, question):
        registry = ReadOnlyToolRegistry(self.engine, mail_account=self.account)
        preset = AgentPreset("messages", "消息", "消息", ("search_messages",), "根据检索回答")
        messages, runs = AgentLoop(None, registry, preset)._prefetch(question)
        self.assertEqual(1, len(messages))
        self.assertEqual("ok", runs[0]["status"])
        return json.loads(messages[0]["content"].split("：", 1)[1])["result"]

    def test_history_prefetch_finds_body_match_outside_recent_100(self):
        result = self.prefetch("查找历史邮件中的考试通知")
        self.assertEqual(1, result["count"])
        self.assertEqual("通知 1", result["items"][0]["title"])
        self.assertEqual("email", result["items"][0]["kind"])

    def test_quoted_keyword_keeps_literal_percent(self):
        result = self.prefetch("搜索邮件「100%」")
        self.assertEqual(1, result["count"])
        self.assertEqual("通知 1", result["items"][0]["title"])

    def test_general_recent_mail_prefetch_keeps_source_and_bounded_context(self):
        result = self.prefetch("最近邮件有哪些")
        self.assertEqual(20, result["count"])
        self.assertTrue(result["truncated"])
        self.assertTrue(all(item["kind"] == "email" for item in result["items"]))

    def test_english_email_prefetch_preserves_quoted_query(self):
        result = self.prefetch('Search historical email "100%"')
        self.assertEqual(1, result["count"])
        self.assertEqual("email", result["items"][0]["kind"])

    def test_old_assignment_requirement_prefetch_uses_body(self):
        course = {"id": 2, "name": "往期课程"}
        persist_canvas_data(self.engine, raw_courses=[course], announcements_by_course={},
            assignments_by_course={"2": [{"id": 20, "name": "报告", "description": "<p>排版要求</p>"}]}, default_scope=False)
        persist_course_catalog(self.engine, [course])
        result = self.prefetch("历史作业「排版要求」")
        self.assertEqual(1, result["count"])
        self.assertEqual("assignment", result["items"][0]["kind"])


class IMAPHistoryTests(unittest.TestCase):
    def fake(self, count=151, fail_uid=None):
        class IMAP:
            fetched = []
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def login(self, *args): pass
            def select(self, *args, **kwargs): return "OK", []
            def response(self, name): return name, [b"7"]
            def uid(self, action, *args):
                if action == "search":
                    first, last = map(int, args[-1].split(":"))
                    return "OK", [" ".join(str(i) for i in range(1, count + 1) if first <= i <= last).encode()]
                uid = int(args[0])
                if uid == fail_uid:
                    raise __import__("imaplib").IMAP4.abort("synthetic interrupt")
                self.fetched.append(uid)
                assert args[-1] == "(BODY.PEEK[] FLAGS INTERNALDATE)"
                return "OK", [(b"FLAGS ()", f"Subject: Notice {uid}\r\nContent-Type: text/plain\r\n\r\nBody {uid}".encode()), b")"]
        return IMAP

    def test_full_history_in_batches_then_incremental_without_duplicates(self):
        batches = []
        fake = self.fake()
        with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", fake):
            result = fetch_incremental_mail("a@example.com", "fake", None, initial_limit=100, on_batch=batches.append)
        self.assertEqual([100, 51], [len(x.messages) for x in batches])
        self.assertEqual(set(range(1, 152)), set(fake.fetched))
        self.assertTrue(json.loads(result.cursor)["history_complete"])
        more = self.fake(153)
        with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", more):
            fetch_incremental_mail("a@example.com", "fake", result.cursor, on_batch=batches.append)
        self.assertEqual([152, 153], more.fetched)

    def test_interrupt_resumes_committed_history_and_legacy_cursor(self):
        batches = []
        fake = self.fake(fail_uid=30)
        legacy = json.dumps({"last_uid": 151, "uid_validity": "7"})
        with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", fake):
            with self.assertRaises(MailCheckError):
                fetch_incremental_mail("a@example.com", "fake", legacy, on_batch=batches.append)
        self.assertEqual(1, len(batches))
        self.assertEqual(52, json.loads(batches[-1].cursor)["history_before"])
        retry = self.fake()
        with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", retry):
            result = fetch_incremental_mail("a@example.com", "fake", batches[-1].cursor, on_batch=batches.append)
        self.assertEqual(list(range(1, 52)), retry.fetched)
        self.assertTrue(json.loads(result.cursor)["history_complete"])

    def test_account_switch_does_not_reuse_other_uid_cursor(self):
        batches = []
        fake = self.fake(5)
        cursor = json.dumps({"account": "other@example.com", "uid_validity": "7", "last_uid": 99, "history_complete": True})
        with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", fake):
            fetch_incremental_mail("a@example.com", "fake", cursor, on_batch=batches.append)
        self.assertEqual(list(range(1, 6)), fake.fetched)

    def test_incremental_interruption_never_skips_uncommitted_new_uid(self):
        batches = []
        cursor = json.dumps({"account": "a@example.com", "uid_validity": "7", "last_uid": 1, "history_complete": True})
        fake = self.fake(9, fail_uid=6)
        with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", fake):
            with self.assertRaises(MailCheckError):
                fetch_incremental_mail("a@example.com", "fake", cursor, initial_limit=3, on_batch=batches.append)
        self.assertEqual(4, json.loads(batches[-1].cursor)["last_uid"])
