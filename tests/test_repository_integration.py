from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from sjtu_learning_assistant.database import create_database_engine
from sjtu_learning_assistant.mail_client import EmailRecord
from sjtu_learning_assistant.models import (
    Announcement,
    Assignment,
    Course,
    CourseFile,
    CourseFolder,
    CourseModule,
    CourseModuleItem,
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
        course_folder = {
            "id": 3001,
            "name": "课件",
            "full_name": "course files/课件",
            "files_count": 1,
            "folders_count": 0,
        }
        course_file = {
            "id": 4001,
            "folder_id": 3001,
            "display_name": "lecture-01.pdf",
            "filename": "lecture-01.pdf",
            "content-type": "application/pdf",
            "size": 1024,
            "updated_at": "2026-09-21T02:00:00Z",
            "url": "https://oc.sjtu.edu.cn/files/4001/download",
        }
        course_module = {
            "id": 5001,
            "name": "第一周",
            "position": 1,
            "items_count": 1,
            "state": "started",
        }
        course_module_item = {
            "id": 6001,
            "module_id": 5001,
            "content_id": 4001,
            "title": "第一讲课件",
            "type": "File",
            "position": 1,
            "html_url": "https://oc.sjtu.edu.cn/courses/95040/modules/items/6001",
        }
        for _ in range(2):
            persist_canvas_data(
                self.engine,
                raw_courses=[course],
                announcements_by_course={"95040": [announcement]},
                assignments_by_course={"95040": [assignment]},
                folders_by_course={"95040": [course_folder]},
                files_by_course={"95040": [course_file]},
                modules_by_course={"95040": [course_module]},
                module_items_by_course={"95040": [course_module_item]},
                announcement_cursors={"95040": '"announcement-etag"'},
                assignment_cursors={"95040": '"assignment-etag"'},
                file_cursors={"95040": '"file-etag"'},
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
            self.assertEqual(1, session.scalar(select(func.count(CourseFolder.id))))
            self.assertEqual(1, session.scalar(select(func.count(CourseFile.id))))
            self.assertEqual(1, session.scalar(select(func.count(CourseModule.id))))
            self.assertEqual(1, session.scalar(select(func.count(CourseModuleItem.id))))
            self.assertEqual(1, session.scalar(select(func.count(Email.id))))
            self.assertEqual(4, session.scalar(select(func.count(UnifiedItem.id))))
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
            self.assertEqual(
                1,
                session.scalar(
                    select(func.count(UnifiedItem.id)).where(
                        UnifiedItem.course_file_id.is_not(None)
                    )
                ),
            )
            state_count = session.scalar(select(func.count(SyncState.id)))
            self.assertGreaterEqual(state_count or 0, 10)

        with Session(self.engine) as session:
            session.execute(
                update(CourseFile)
                .where(CourseFile.source_id == "4001")
                .values(
                    download_status="downloaded",
                    download_attempts=4,
                    download_error=None,
                )
            )
            session.commit()

        persist_canvas_data(
            self.engine,
            raw_courses=(course,),
            announcements_by_course=dict(),
            assignments_by_course=dict(),
            files_by_course=dict((("95040", (course_file,)),)),
        )
        with Session(self.engine) as session:
            unchanged_file = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "4001")
            )
            self.assertEqual("downloaded", unchanged_file.download_status)
            self.assertEqual(4, unchanged_file.download_attempts)

        course_file.update(updated_at="2026-09-22T02:00:00Z")
        persist_canvas_data(
            self.engine,
            raw_courses=(course,),
            announcements_by_course=dict(),
            assignments_by_course=dict(),
            files_by_course=dict((("95040", (course_file,)),)),
        )
        with Session(self.engine) as session:
            changed_file = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "4001")
            )
            self.assertEqual("pending", changed_file.download_status)
            self.assertEqual(4, changed_file.download_attempts)
            self.assertIsNone(changed_file.download_error)

        # Empty top-level maps mean the resource was not fetched (for example HTTP 304),
        # so existing rows must remain active.
        persist_canvas_data(
            self.engine,
            raw_courses=[course],
            announcements_by_course={},
            assignments_by_course={},
        )
        with Session(self.engine) as session:
            self.assertTrue(
                session.scalar(
                    select(CourseFile.is_active).where(CourseFile.source_id == "4001")
                )
            )

        # A present course key with an empty list means a successful empty response;
        # previously seen rows should be retained but marked inactive.
        persist_canvas_data(
            self.engine,
            raw_courses=[course],
            announcements_by_course={"95040": []},
            assignments_by_course={"95040": []},
            folders_by_course={"95040": []},
            files_by_course={"95040": []},
            modules_by_course={"95040": []},
            module_items_by_course={"95040": []},
        )
        with Session(self.engine) as session:
            for model, source_id in (
                (Announcement, "1001"),
                (Assignment, "2001"),
                (CourseFolder, "3001"),
                (CourseFile, "4001"),
                (CourseModule, "5001"),
                (CourseModuleItem, "6001"),
            ):
                record = session.scalar(select(model).where(model.source_id == source_id))
                self.assertIsNotNone(record)
                self.assertFalse(record.is_active)
                self.assertIsNotNone(record.deactivated_at)
            inactive_item_count = session.scalar(
                select(func.count(UnifiedItem.id)).where(
                    UnifiedItem.item_type.in_(("announcement", "assignment", "file")),
                    UnifiedItem.is_active.is_(False),
                )
            )
            self.assertEqual(3, inactive_item_count)


if __name__ == "__main__":
    unittest.main()
