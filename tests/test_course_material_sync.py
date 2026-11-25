from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

import sync_data_to_db
from sjtu_learning_assistant.canvas_sync import CanvasCheckError
from sjtu_learning_assistant.course_material_sync import sync_historical_materials
from sjtu_learning_assistant.course_scope import current_course_ids
from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.models import Course, CourseFile, SyncState
from sjtu_learning_assistant.repository import persist_canvas_data
from sjtu_learning_assistant.dashboard_service import DashboardService


def course(identifier, term):
    return {"id": identifier, "name": f"课程{identifier}", "term": {"id": term, "name": f"学期{term}"}}


class HistoricalMaterialSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_database_engine(default_sqlite_url(self.root / "test.db"))
        bootstrap_sqlite(self.engine)
        persist_canvas_data(self.engine, raw_courses=[course(1, 20)], announcements_by_course={}, assignments_by_course={})

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_normal_sync_fills_previous_folders_without_changing_default_scope(self):
        requests = []
        def handler(request):
            requests.append(request)
            path = request.url.path
            if path == "/api/v1/courses":
                if request.url.params.get_list("state[]"):
                    return httpx.Response(200, json=[course(1, 20), course(2, 10)])
                return httpx.Response(200, json=[course(1, 20)])
            if path == "/api/v1/courses/2/files":
                return httpx.Response(200, json=[{"id": 22, "filename": "往期讲义.pptx", "size": 1}], headers={"ETag": "old-course-v1"})
            return httpx.Response(200, json=[])
        factory = lambda *args: httpx.Client(base_url="https://oc.sjtu.edu.cn", transport=httpx.MockTransport(handler), trust_env=False)
        with patch.object(sync_data_to_db, "build_http_client", side_effect=factory), patch.object(sync_data_to_db, "ArchiveService") as archive, patch.object(sync_data_to_db, "NotificationService") as notify:
            result = sync_data_to_db.sync_canvas(self.engine, saved_token="fixture", download=False, notify=False)
        self.assertEqual(1, result["historical_materials"]["courses"])
        archive.assert_not_called()
        notify.assert_not_called()
        self.assertTrue(all(request.method == "GET" for request in requests))
        self.assertTrue(any("/courses/2/assignments" in str(request.url) for request in requests))
        with Session(self.engine) as session:
            self.assertEqual({session.scalar(select(Course.id).where(Course.source_id == "1"))}, current_course_ids(session))
            self.assertIsNotNone(session.scalar(select(CourseFile).where(CourseFile.source_id == "22")))
        service = DashboardService(self.engine, archive_root=self.root / "archive")
        try:
            tree = service.material_tree()
            self.assertEqual({"term:10", "term:20"}, {node["id"] for node in tree["root"]["children"]})
            self.assertIn("往期讲义.pptx", str(tree))
        finally:
            service.close()

    def test_unreadable_files_preserve_cached_files_and_cursor(self):
        persist_canvas_data(self.engine, raw_courses=[course(2, 10)], announcements_by_course={}, assignments_by_course={}, files_by_course={"2": [{"id": 22, "filename": "cached.pptx"}]}, file_cursors={"2": "cached-etag"}, default_scope=False)
        def handler(request):
            if request.url.path == "/api/v1/courses":
                return httpx.Response(200, json=[course(1, 20), course(2, 10)])
            if request.url.path.endswith("/files"):
                return httpx.Response(403)
            return httpx.Response(200, json=[])
        with httpx.Client(base_url="https://oc.sjtu.edu.cn", transport=httpx.MockTransport(handler)) as client:
            result = sync_historical_materials(self.engine, client, {"1"})
        self.assertEqual([{"course_id": "2", "resource": "files", "http": 403}], result["issues"])
        with Session(self.engine) as session:
            self.assertTrue(session.scalar(select(CourseFile).where(CourseFile.source_id == "22")).is_active)
            self.assertEqual("cached-etag", session.scalar(select(SyncState).where(SyncState.resource == "course:2:files")).cursor)

    def test_auth_or_rate_failure_stops_without_losing_completed_course(self):
        for status in (401, 429):
            with self.subTest(status=status):
                def handler(request):
                    if request.url.path == "/api/v1/courses":
                        return httpx.Response(200, json=[course(1, 20), course(2, 10), course(3, 5)])
                    if request.url.path == "/api/v1/courses/2/files":
                        return httpx.Response(200, json=[{"id": 22, "filename": "done.pptx"}])
                    if request.url.path == "/api/v1/courses/3/files":
                        return httpx.Response(status)
                    return httpx.Response(200, json=[])
                with httpx.Client(base_url="https://oc.sjtu.edu.cn", transport=httpx.MockTransport(handler)) as client:
                    with self.assertRaises(CanvasCheckError):
                        sync_historical_materials(self.engine, client, {"1"})
                with Session(self.engine) as session:
                    self.assertTrue(session.scalar(select(CourseFile).where(CourseFile.source_id == "22")).is_active)
                    self.assertIsNone(session.scalar(select(SyncState).where(SyncState.resource == "course:3:files")))
