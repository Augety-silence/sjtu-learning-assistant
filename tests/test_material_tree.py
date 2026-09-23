from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sjtu_learning_assistant.material_tree import build_material_tree, classify_material, safe_folder_chain
from sjtu_learning_assistant.models import Base, Course, CourseFile, CourseFolder, CourseModule, CourseModuleItem


NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


class MaterialClassificationTests(unittest.TestCase):
    def test_priority_is_module_then_folder_then_filename(self):
        self.assertEqual("assignments", classify_material(module_names=["Homework"], folder_names=["课件"], filename="reading.pdf"))
        self.assertEqual("courseware", classify_material(module_names=[], folder_names=["Lecture 01"], filename="homework.pdf"))
        self.assertEqual("other", classify_material(module_names=[], folder_names=[], filename="reference.pdf"))
        self.assertEqual("other", classify_material(module_names=[], folder_names=[], filename="syllabus.pdf"))
        self.assertEqual(
            "assignments",
            classify_material(
                module_names=[],
                module_item_names=["Lab 1"],
                folder_names=["Lecture"],
                filename="reference.pdf",
            ),
        )

    def test_folder_cycle_is_safe(self):
        first = SimpleNamespace(id=1, parent_folder_id=2)
        second = SimpleNamespace(id=2, parent_folder_id=1)
        chain = safe_folder_chain(1, {1: first, 2: second})
        self.assertEqual(2, len(chain))
        self.assertEqual({1, 2}, {item.id for item in chain})


class MaterialTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite+pysqlite:///{Path(self.temp.name) / 'test.db'}")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_tree_shape_module_priority_and_inactive_filter(self):
        with Session(self.engine) as session:
            course = Course(source_id="course-1", name="自然语言处理", term_name="2026-2027 Fall", raw_data={})
            session.add(course)
            session.flush()
            root = CourseFolder(source_id="folder-root", course_id=course.id, name="course files", parent_folder_id=None, is_active=True, last_seen_at=NOW, raw_data={})
            session.add(root)
            session.flush()
            folder = CourseFolder(source_id="folder-lecture", course_id=course.id, name="课件", parent_folder_id=root.id, position=1, is_active=True, last_seen_at=NOW, raw_data={})
            session.add(folder)
            session.flush()
            document = CourseFile(source_id="file-1", course_id=course.id, folder_id=folder.id, display_name="reading.pdf", filename="reading.pdf", size=12, download_status="cloud_only", cloud_path="SJTU Learning Assistant/Canvas/reading.pdf", cloud_size=12, cloud_backed_up_at=NOW, is_active=True, last_seen_at=NOW, raw_data={})
            hidden = CourseFile(source_id="file-old", course_id=course.id, folder_id=folder.id, display_name="old.pdf", filename="old.pdf", download_status="pending", is_active=False, last_seen_at=NOW, raw_data={})
            session.add_all([document, hidden])
            session.flush()
            module = CourseModule(source_id="module-1", course_id=course.id, name="Homework", is_active=True, last_seen_at=NOW, raw_data={})
            session.add(module)
            session.flush()
            session.add(CourseModuleItem(source_id="item-1", course_id=course.id, module_id=module.id, content_file_id=document.id, title="reading", item_type="File", is_active=True, last_seen_at=NOW, raw_data={}))
            session.commit()
            tree = build_material_tree(session)

        term = tree["root"]["children"][0]
        self.assertEqual("2026-2027 Fall", term["name"])
        course_node = term["children"][0]
        categories = {node["category"]: node for node in course_node["children"]}
        assignment_files = categories["assignments"]["children"][0]["children"]
        self.assertEqual(["file-1"], [item["source_id"] for item in assignment_files])
        cloud_file = assignment_files[0]
        self.assertEqual("cloud_only", cloud_file["download_status"])
        self.assertEqual("cloud", cloud_file["cloud_status"])
        self.assertTrue(cloud_file["cloud_ready"])
        self.assertTrue(cloud_file["can_preview"])
        self.assertTrue(cloud_file["can_open"])
        self.assertNotIn("file-old", str(tree))
        self.assertEqual({"assignments", "courseware", "other"}, set(categories))

    def test_tree_matches_flat_archive_layout_and_merges_legacy_supplementary(self):
        with Session(self.engine) as session:
            course = Course(source_id="course-1", name="自然语言处理", term_name="2026-2027 Fall", raw_data={})
            session.add(course)
            session.flush()
            root = CourseFolder(source_id="root", course_id=course.id, name="course files", is_active=True, last_seen_at=NOW, raw_data={})
            session.add(root)
            session.flush()
            week = CourseFolder(source_id="week", course_id=course.id, name="Week 1", parent_folder_id=root.id, is_active=True, last_seen_at=NOW, raw_data={})
            session.add(week)
            session.flush()
            leaf = CourseFolder(source_id="leaf", course_id=course.id, name="Deep", parent_folder_id=week.id, is_active=True, last_seen_at=NOW, raw_data={})
            session.add(leaf)
            session.flush()
            session.add_all(
                [
                    CourseFile(source_id="assignment", course_id=course.id, folder_id=leaf.id, display_name="homework.pdf", is_active=True, last_seen_at=NOW, raw_data={}),
                    CourseFile(source_id="courseware", course_id=course.id, folder_id=leaf.id, display_name="slides.pdf", ai_category="courseware", is_active=True, last_seen_at=NOW, raw_data={}),
                    CourseFile(source_id="legacy-ai", course_id=course.id, folder_id=leaf.id, display_name="reference.pdf", ai_category="supplementary", is_active=True, last_seen_at=NOW, raw_data={}),
                    CourseFile(source_id="legacy-manual", course_id=course.id, folder_id=leaf.id, display_name="notes.pdf", manual_category="supplementary", manual_folder_id=leaf.id, manual_override=True, is_active=True, last_seen_at=NOW, raw_data={}),
                ]
            )
            session.commit()
            tree = build_material_tree(session)

        course_node = tree["root"]["children"][0]["children"][0]
        categories = {node["category"]: node for node in course_node["children"]}
        assignments = categories["assignments"]["children"]
        self.assertEqual("Week 1", assignments[0]["name"])
        self.assertEqual("Deep", assignments[0]["children"][0]["name"])
        self.assertEqual("assignment", assignments[0]["children"][0]["children"][0]["source_id"])
        self.assertEqual(["courseware"], [item["source_id"] for item in categories["courseware"]["children"]])
        self.assertEqual(
            ["legacy-manual", "legacy-ai"],
            [item["source_id"] for item in categories["other"]["children"]],
        )
        self.assertNotIn("supplementary", {item["id"] for item in tree["categories"]})


if __name__ == "__main__":
    unittest.main()
