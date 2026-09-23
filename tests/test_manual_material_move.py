from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.archive_service import ArchiveError, ArchiveService
from sjtu_learning_assistant.material_tree import build_material_tree
from sjtu_learning_assistant.models import Base, Course, CourseFile, CourseFolder
from sjtu_learning_assistant.repository import persist_canvas_data

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)
CONTENT = b"temporary test content"


class ManualMaterialMoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "archive"
        self.root.mkdir()
        self.engine = create_engine(
            "sqlite+pysqlite:///" + str(Path(self.temporary.name) / "test.db")
        )
        Base.metadata.create_all(self.engine)
        self.client = httpx.Client(base_url="https://oc.sjtu.edu.cn")
        self.service = ArchiveService(
            self.engine,
            self.client,
            archive_root=self.root,
        )
        with Session(self.engine) as session, session.begin():
            first = Course(
                source_id="course-1",
                name="自然语言处理",
                term_name="2026-2027 Fall",
                raw_data={},
            )
            second = Course(
                source_id="course-2",
                name="数据库",
                term_name="2026-2027 Fall",
                raw_data={},
            )
            session.add_all([first, second])
            session.flush()
            first_root = CourseFolder(
                source_id="root-1",
                course_id=first.id,
                name="course files",
                is_active=True,
                last_seen_at=NOW,
                raw_data={},
            )
            second_root = CourseFolder(
                source_id="root-2",
                course_id=second.id,
                name="course files",
                is_active=True,
                last_seen_at=NOW,
                raw_data={},
            )
            session.add_all([first_root, second_root])
            session.flush()
            first_folder = CourseFolder(
                source_id="week-1",
                course_id=first.id,
                parent_folder_id=first_root.id,
                name="Week 1",
                is_active=True,
                last_seen_at=NOW,
                raw_data={},
            )
            second_folder = CourseFolder(
                source_id="week-2",
                course_id=second.id,
                parent_folder_id=second_root.id,
                name="Week 1",
                is_active=True,
                last_seen_at=NOW,
                raw_data={},
            )
            session.add_all([first_folder, second_folder])
            session.flush()
            self.first_course_id = first.id
            self.first_folder_id = first_folder.id
            self.second_folder_id = second_folder.id
            session.add_all(
                [
                    CourseFile(
                        source_id="file-1",
                        course_id=first.id,
                        folder_id=first_folder.id,
                        display_name="lecture.pdf",
                        filename="lecture.pdf",
                        size=len(CONTENT),
                        source_updated_at=NOW,
                        ai_category="courseware",
                        download_status="pending",
                        is_active=True,
                        last_seen_at=NOW,
                        raw_data={},
                    ),
                    CourseFile(
                        source_id="file-2",
                        course_id=second.id,
                        folder_id=second_folder.id,
                        display_name="database.pdf",
                        filename="database.pdf",
                        size=len(CONTENT),
                        download_status="pending",
                        is_active=True,
                        last_seen_at=NOW,
                        raw_data={},
                    ),
                ]
            )

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self.temporary.cleanup()

    def category_id(self, course: str, category: str) -> str:
        return "category:" + course + ":" + category

    def folder_id(self, course_id: int, category: str, folder_id: int) -> str:
        return "folder:" + str(course_id) + ":" + category + ":" + str(folder_id)

    def record(self) -> CourseFile:
        with Session(self.engine) as session:
            return session.scalar(
                select(CourseFile).where(CourseFile.source_id == "file-1")
            )

    def mark_downloaded(self) -> Path:
        context = self.service._load_contexts(source_id="file-1")[0]
        source = self.service._planned_paths([context])["file-1"]
        source.parent.mkdir(parents=True)
        source.write_bytes(CONTENT)
        with Session(self.engine) as session, session.begin():
            record = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "file-1")
            )
            record.download_status = "downloaded"
            record.local_path = str(source)
            record.downloaded_size = len(CONTENT)
            record.download_sha256 = hashlib.sha256(CONTENT).hexdigest()
            record.downloaded_source_updated_at = NOW
        return source

    def test_rejects_cross_course_and_non_target_nodes(self) -> None:
        for source_id in ("", "file\n1", 1):
            with self.subTest(source_id=source_id), self.assertRaises(ArchiveError):
                self.service.move_file_by_source_id(
                    source_id, self.category_id("course-1", "courseware")
                )
        with self.assertRaisesRegex(ArchiveError, "其他课程"):
            self.service.move_file_by_source_id(
                "file-1", self.category_id("course-2", "courseware")
            )
        for target in (
            "root",
            "course:course-1",
            "file:file-1",
            "category:course-1:../../other",
            "folder:1:courseware:999999",
            "missing",
        ):
            with self.subTest(target=target), self.assertRaises(ArchiveError):
                self.service.move_file_by_source_id("file-1", target)
        record = self.record()
        self.assertFalse(record.manual_override)
        self.assertIsNone(record.manual_category)

    def test_manual_priority_and_undownloaded_target_are_persisted(self) -> None:
        self.service.organize_by_category = False
        category_target = self.category_id("course-1", "assignments")
        result = self.service.move_file_by_source_id("file-1", category_target)
        self.assertEqual("saved", result.status)
        record = self.record()
        self.assertTrue(record.manual_override)
        self.assertEqual("assignments", record.manual_category)
        self.assertIsNone(record.manual_folder_id)
        self.assertEqual("courseware", record.ai_category)

        context = self.service._load_contexts(source_id="file-1")[0]
        self.assertEqual("assignments", context.category)
        self.assertEqual((), context.folder_names)
        planned = self.service._planned_paths([context])["file-1"]
        self.assertIn("课程作业", planned.parts)

        self.service.restore_file_auto("file-1")
        with Session(self.engine) as session, session.begin():
            record = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "file-1")
            )
            record.ai_category = "assignments"
        folder_target = self.folder_id(
            self.first_course_id, "assignments", self.first_folder_id
        )
        self.service.move_file_by_source_id("file-1", folder_target)
        record = self.record()
        self.assertEqual("assignments", record.manual_category)
        self.assertEqual(self.first_folder_id, record.manual_folder_id)
        context = self.service._load_contexts(source_id="file-1")[0]
        self.assertEqual(("Week 1",), context.folder_names)
        tree = build_material_tree(Session(self.engine))
        target_node = ArchiveService._find_tree_node(tree["root"], folder_target)
        self.assertIsNotNone(target_node)
        self.assertEqual("file-1", target_node["children"][0]["source_id"])
        self.assertTrue(target_node["children"][0]["manual_override"])

    def test_downloaded_file_moves_and_updates_fields_atomically(self) -> None:
        source = self.mark_downloaded()
        result = self.service.move_file_by_source_id(
            "file-1", self.category_id("course-1", "assignments")
        )
        self.assertEqual("moved", result.status)
        self.assertFalse(source.exists())
        target = Path(result.local_path)
        self.assertEqual(CONTENT, target.read_bytes())
        self.assertIn("课程作业", target.parts)
        record = self.record()
        self.assertEqual(str(target), record.local_path)
        self.assertTrue(record.manual_override)
        self.assertEqual("assignments", record.manual_category)

    def test_move_failure_rolls_back_file_and_manual_state(self) -> None:
        source = self.mark_downloaded()
        with patch.object(
            self.service,
            "_persist_organized_path",
            side_effect=RuntimeError("database unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                self.service.move_file_by_source_id(
                    "file-1", self.category_id("course-1", "assignments")
                )
        self.assertEqual(CONTENT, source.read_bytes())
        record = self.record()
        self.assertEqual(str(source), record.local_path)
        self.assertFalse(record.manual_override)
        self.assertIsNone(record.manual_category)
        assignments = self.root / "2026-2027 Fall" / "自然语言处理" / "课程作业"
        self.assertFalse(assignments.exists() and any(assignments.rglob("lecture.pdf")))

    def test_restore_auto_moves_downloaded_file_to_ai_target(self) -> None:
        self.mark_downloaded()
        moved = self.service.move_file_by_source_id(
            "file-1", self.category_id("course-1", "assignments")
        )
        manual_path = Path(moved.local_path)
        restored = self.service.restore_file_auto("file-1")
        self.assertEqual("moved", restored.status)
        self.assertFalse(manual_path.exists())
        automatic_path = Path(restored.local_path)
        self.assertEqual(CONTENT, automatic_path.read_bytes())
        self.assertIn("课件", automatic_path.parts)
        record = self.record()
        self.assertFalse(record.manual_override)
        self.assertIsNone(record.manual_category)
        self.assertIsNone(record.manual_folder_id)
        self.assertEqual("courseware", record.ai_category)


    def test_canvas_upsert_preserves_manual_override(self) -> None:
        self.service.move_file_by_source_id(
            "file-1", self.category_id("course-1", "assignments")
        )
        persist_canvas_data(
            self.engine,
            raw_courses=list(
                (
                    dict(
                        id="course-1",
                        name="自然语言处理",
                        term=dict(name="2026-2027 Fall"),
                    ),
                )
            ),
            announcements_by_course=dict(),
            assignments_by_course=dict(),
            files_by_course=dict(
                (
                    (
                        "course-1",
                        list(
                            (
                                dict(
                                    id="file-1",
                                    folder_id="week-1",
                                    display_name="lecture-renamed.pdf",
                                    filename="lecture-renamed.pdf",
                                    size=len(CONTENT),
                                    updated_at=NOW.isoformat(),
                                ),
                            )
                        ),
                    ),
                )
            ),
        )
        record = self.record()
        self.assertTrue(record.manual_override)
        self.assertEqual("assignments", record.manual_category)
        self.assertIsNone(record.manual_folder_id)



if __name__ == "__main__":
    unittest.main()
