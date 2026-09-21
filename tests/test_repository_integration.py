from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.database import create_database_engine
from sjtu_learning_assistant.mail_client import EmailRecord
from sjtu_learning_assistant.models import (
    Announcement,
    Assignment,
    Course,
    Email,
    SyncState,
    UnifiedItem,
)
from sjtu_learning_assistant.repository import persist_canvas_data, persist_emails

TEST_DATABASE_URL = os.environ.get("SJTU_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "需要独立的 SJTU_TEST_DATABASE_URL")
class RepositoryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_database_engine(TEST_DATABASE_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def test_idempotent_cross_table_persistence(self) -> None:
        course = {
            "id": 95040,
            "name": "文本分析与大模型",
            "course_code": "MGTS3605",
            "term": {"name": "2026-2027 Fall"},
        }
        announcement = {
            "id": 1001,
            "title": "课程通知",
            "posted_at": "2026-09-21T01:00:00Z",
            "html_url": "https://oc.sjtu.edu.cn/announcement/1001",
        }
        assignment = {
            "id": 2001,
            "name": "作业一",
            "due_at": "2026-09-28T15:59:00Z",
            "points_possible": 100,
            "html_url": "https://oc.sjtu.edu.cn/assignment/2001",
        }
        for _ in range(2):
            persist_canvas_data(
                self.engine,
                raw_courses=[course],
                announcements_by_course={"95040": [announcement]},
                assignments_by_course={"95040": [assignment]},
                announcement_cursors={"95040": '"announcement-etag"'},
                assignment_cursors={"95040": '"assignment-etag"'},
            )

        email = EmailRecord(
            source_id="student@sjtu.edu.cn:INBOX:123:456",
            subject="测试邮件",
            sender_name="测试发件人",
            sender_address="sender@example.com",
            sent_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
            received_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
            body_preview=None,
            is_unread=True,
            raw_data={"uid": 456, "uid_validity": "123"},
        )
        for _ in range(2):
            persist_emails(self.engine, [email], cursor='{"last_uid":456}')

        with Session(self.engine) as session:
            self.assertEqual(1, session.scalar(select(func.count(Course.id))))
            self.assertEqual(1, session.scalar(select(func.count(Announcement.id))))
            self.assertEqual(1, session.scalar(select(func.count(Assignment.id))))
            self.assertEqual(1, session.scalar(select(func.count(Email.id))))
            self.assertEqual(3, session.scalar(select(func.count(UnifiedItem.id))))
            self.assertEqual(
                1,
                session.scalar(
                    select(func.count(UnifiedItem.id)).where(
                        UnifiedItem.announcement_id.is_not(None)
                    )
                ),
            )
            self.assertEqual(
                1,
                session.scalar(
                    select(func.count(UnifiedItem.id)).where(
                        UnifiedItem.assignment_id.is_not(None)
                    )
                ),
            )
            self.assertEqual(
                1,
                session.scalar(
                    select(func.count(UnifiedItem.id)).where(
                        UnifiedItem.email_id.is_not(None)
                    )
                ),
            )
            state_count = session.scalar(select(func.count(SyncState.id)))
            self.assertGreaterEqual(state_count or 0, 6)


if __name__ == "__main__":
    unittest.main()
