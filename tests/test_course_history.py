from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

import sync_data_to_db
from sjtu_learning_assistant.canvas_client import CanvasClient
from sjtu_learning_assistant.canvas_sync import CanvasCheckError, fetch_all_courses, CollectionFetchResult
from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge, CompiledKnowledgeError
from sjtu_learning_assistant.course_history import CourseHistoryError, CourseHistoryService
from sjtu_learning_assistant.course_scope import current_course_ids, select_scope, term_identity, validate_scope
from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.agent_runtime.tools import ReadOnlyToolRegistry
from sjtu_learning_assistant.material_tree import build_material_tree
from sjtu_learning_assistant.models import Course, SyncState
from sjtu_learning_assistant.notifications import NotificationService
from sjtu_learning_assistant.repository import persist_canvas_data, persist_course_catalog


def course(identifier, term):
    return {"id": identifier, "name": "同名课程", "term": {"id": term, "name": "同名学期"}}


class CourseScopeTests(unittest.TestCase):
    def test_ids_not_names_define_term(self):
        self.assertNotEqual(term_identity(course(1, 10))[0], term_identity(course(2, 20))[0])
        self.assertEqual([course(2, 20)], select_scope([course(1, 10), course(2, 20)], "term:20"))

    def test_invalid_or_unowned_scope_is_rejected(self):
        for term, ids in [(True, None), ("../x", None), ("term:10", []), ("", [True]), ("", ["../1"]), ("", None), ("term:10", [2]), ("", [99])]:
            with self.subTest(term=term, ids=ids), self.assertRaises(ValueError):
                select_scope([course(1, 10), course(2, 20)], term, ids)
        self.assertEqual(("term:10", ("1",)), validate_scope("term:10", [1, "1"]))

    def test_both_clients_request_full_available_completed_directory(self):
        for adapter in (fetch_all_courses, lambda client: CanvasClient(client=client).all_courses()):
            requests = []
            def handler(request):
                requests.append(request)
                if request.url.params.get("page") == "2":
                    return httpx.Response(200, json=[course(2, 20)])
                return httpx.Response(200, json=[course(1, 10)], headers={"Link": '<https://oc.sjtu.edu.cn/api/v1/courses?page=2>; rel="next"'})
            with httpx.Client(base_url="https://oc.sjtu.edu.cn", transport=httpx.MockTransport(handler)) as client:
                self.assertEqual(2, len(adapter(client)))
            self.assertEqual(["available", "completed"], requests[0].url.params.get_list("state[]"))
            self.assertNotIn("enrollment_state", requests[0].url.params)
            self.assertEqual(["term"], requests[0].url.params.get_list("include[]"))


class HistoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_database_engine(default_sqlite_url(self.root / "data.db"))
        bootstrap_sqlite(self.engine)
        self.now = datetime.now(timezone.utc)
        self.persist([course(1, 10)])

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def persist(self, rows, default=True, assignments=None):
        return persist_canvas_data(self.engine, raw_courses=rows, announcements_by_course={}, assignments_by_course=assignments or {}, default_scope=default)

    def state(self):
        with Session(self.engine) as session:
            state = session.scalar(select(SyncState).where(SyncState.resource == "courses", SyncState.source == "canvas"))
            return state.cursor, state.last_success_at

    def test_catalog_and_history_preserve_default_marker(self):
        before = self.state()
        persist_course_catalog(self.engine, [course(1, 10), course(2, 20)])
        self.persist([course(2, 20)], False)
        self.assertEqual(before, self.state())
        with Session(self.engine) as session:
            active = current_course_ids(session)
            self.assertEqual({session.scalar(select(Course.id).where(Course.source_id == "1"))}, active)
            self.assertIsNotNone(session.scalar(select(SyncState).where(SyncState.resource == "history:courses")))

    def test_legacy_default_scope_is_frozen_before_catalog_changes_timestamps(self):
        with Session(self.engine) as session, session.begin():
            state = session.scalar(select(SyncState).where(SyncState.resource == "courses"))
            state.cursor = None
        timestamp = self.state()[1]
        persist_course_catalog(self.engine, [course(1, 10), course(2, 20)])
        self.assertEqual(timestamp, self.state()[1])
        self.assertEqual(["1"], json.loads(self.state()[0])["course_ids"])

    def test_malformed_scope_fail_closed(self):
        for cursor in ("[]", '{"version":1,"course_ids":[true]}', "{"):
            with Session(self.engine) as session, session.begin():
                state = session.scalar(select(SyncState).where(SyncState.resource == "courses"))
                state.cursor = cursor
            with Session(self.engine) as session:
                self.assertEqual(set(), current_course_ids(session))

    def test_sparse_restricted_catalog_preserves_term_and_name(self):
        persist_course_catalog(self.engine, [{"id": 1, "access_restricted_by_date": True, "term": {}}])
        with Session(self.engine) as session:
            row = session.scalar(select(Course).where(Course.source_id == "1"))
            self.assertEqual("同名课程", row.name)
            self.assertEqual("term:10", term_identity(row.raw_data, row.term_name)[0])
            self.assertTrue(row.raw_data["access_restricted_by_date"])

    def test_tree_uses_term_identity_and_scope(self):
        persist_course_catalog(self.engine, [course(1, 10), course(2, 20)])
        with Session(self.engine) as session:
            tree = build_material_tree(session)
            self.assertEqual(["term:10", "term:20"], [row["id"] for row in tree["root"]["children"]])
        service = DashboardService(self.engine, archive_root=self.root / "archive")
        try:
            tree = service.material_tree(term_id="term:20")
            self.assertEqual(["term:20"], [row["id"] for row in tree["root"]["children"]])
        finally:
            service.close()

    def test_history_assignments_do_not_pollute_default_deadlines_or_notifications(self):
        assignment = {"id": 51, "name": "历史未交", "due_at": (self.now + timedelta(hours=12)).isoformat()}
        self.persist([course(2, 20)], False, {"2": [assignment]})
        service = DashboardService(self.engine, archive_root=self.root / "archive", now_provider=lambda: self.now)
        try:
            self.assertEqual([], service.deadlines(None))
            self.assertEqual([], service.messages("assignment"))
            self.assertEqual([], service.calendar(self.now.year, self.now.month)["month_events"])
        finally:
            service.close()
        notification = NotificationService(self.engine, sender=Mock(), now=self.now)
        self.assertEqual([], notification._new_item_candidates("assignment", after_id=0))
        self.assertEqual([], notification._due_assignment_candidates())
        registry = ReadOnlyToolRegistry(self.engine, now_provider=lambda: self.now)
        self.assertEqual([], registry.execute("get_deadlines", {}, ("get_deadlines",)).result["items"])

    def service(self, rows=None, synchronize=None):
        return CourseHistoryService(self.engine, fetch_rows=lambda: rows or [course(1, 10), course(2, 20)],
                                    synchronize=synchronize or (lambda rows, download, progress: {"courses": len(rows), "issues": [], "downloaded": 0, "download_failed": 0}),
                                    sync_lock=threading.Lock(), lock_path=self.root / "runtime/sync.lock")

    def test_job_fixed_scope_persists_and_revalidates_owner(self):
        calls = []
        service = self.service(synchronize=lambda rows, download, progress: calls.append(rows) or {"issues": [], "download_failed": 0})
        service.start("term:20", [2])
        service._thread.join(5)
        self.assertEqual([course(2, 20)], calls[0])
        self.assertEqual("completed", service.status()["status"])
        self.assertEqual(["2"], self.service().status()["course_ids"])
        service.start("term:20", [999])
        service._thread.join(5)
        self.assertEqual("failed", service.status()["status"])
        self.assertEqual(1, len(calls))

    def test_busy_refresh_and_start_do_not_overlap(self):
        service = self.service()
        service.sync_lock.acquire()
        with self.assertRaises(CourseHistoryError):
            service.start("term:10")
        with self.assertRaises(CourseHistoryError):
            service.catalog(True)
        service.sync_lock.release()
        self.assertEqual(1, service.catalog()["count"])
        self.assertEqual(2, service.catalog(True)["count"])

    def test_restart_running_job_is_interrupted_not_automatically_replayed(self):
        with Session(self.engine) as session, session.begin():
            session.add(SyncState(source="canvas", resource="course_history_job", cursor=json.dumps({"version": 1, "status": "running"})))
        service = self.service()
        self.assertEqual("interrupted", service.status()["status"])
        self.assertIsNone(service._thread)

    def test_partial_is_not_reported_completed(self):
        service = self.service(synchronize=lambda *args: {"issues": [{"http": 403}], "download_failed": 0})
        service.start("term:20")
        service._thread.join(5)
        self.assertEqual("partial", service.status()["status"])

    def test_ai_course_list_paginates_and_filters_term(self):
        persist_course_catalog(self.engine, [course(1, 10), course(2, 20)])
        registry = ReadOnlyToolRegistry(self.engine)
        result = registry.execute("list_courses", {"limit": 1}, ("list_courses",)).result
        self.assertEqual(2, result["count"])
        self.assertEqual(1, result["next_offset"])
        result = registry.execute("list_courses", {"term_id": "term:20"}, ("list_courses",)).result
        self.assertEqual(["2"], [row["course_id"] for row in result["items"]])

    def test_scoped_sync_retains_forbidden_cached_files_and_uses_exact_download_ids(self):
        persist_canvas_data(self.engine, raw_courses=[course(2, 20)], announcements_by_course={}, assignments_by_course={},
                            files_by_course={"2": [{"id": 22, "filename": "lecture.pptx", "size": 1}]},
                            file_cursors={"2": "old-etag"}, default_scope=False)
        def handler(request):
            if request.url.path.endswith("/files"):
                return httpx.Response(403)
            return httpx.Response(200, json=[])
        factory = lambda *args: httpx.Client(base_url="https://oc.sjtu.edu.cn", transport=httpx.MockTransport(handler), trust_env=False)
        archive = Mock()
        archive.archive_current_term.return_value = SimpleNamespace(downloaded=0, failed=0, unchanged=0, skipped=False, term_name="test", results=[])
        with patch.object(sync_data_to_db, "build_http_client", side_effect=factory), patch.object(sync_data_to_db, "ArchiveService", return_value=archive) as constructor, patch.object(sync_data_to_db, "NotificationService") as notify:
            result = sync_data_to_db.sync_canvas(self.engine, selected_courses=[course(2, 20)], saved_token="fixture", download=True)
        self.assertEqual({"2"}, constructor.call_args.kwargs["active_course_source_ids"])
        self.assertEqual([{"course_id": "2", "resource": "files", "http": 403}], result["issues"])
        notify.assert_not_called()
        with Session(self.engine) as session:
            from sjtu_learning_assistant.models import CourseFile
            self.assertTrue(session.scalar(select(CourseFile).where(CourseFile.source_id == "22")).is_active)
            self.assertEqual("old-etag", session.scalar(select(SyncState).where(SyncState.resource == "course:2:files")).cursor)

    def test_failed_fresh_catalog_and_external_lock_leave_cache_unchanged(self):
        service = self.service()
        before = self.state()
        with patch.object(service, "fetch_rows", side_effect=CanvasCheckError("fixture")):
            with self.assertRaises(CourseHistoryError):
                service.catalog(True)
        self.assertEqual(before, self.state())
        fd = service._runner._acquire_lock()
        try:
            with self.assertRaises(CourseHistoryError):
                service.start("term:20")
            self.assertFalse(service.sync_lock.locked())
        finally:
            service._runner._release_lock(fd)

    def test_knowledge_scope_filters_before_read_and_returns_provenance(self):
        archive = self.root / "archive"
        original = archive / "previous/course/lecture.pptx"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"fixture")
        self.persist([course(2, 20)], False)
        from sjtu_learning_assistant.models import CourseFile
        with Session(self.engine) as session, session.begin():
            c = session.scalar(select(Course).where(Course.source_id == "2"))
            session.add(CourseFile(source_id="22", course_id=c.id, filename="lecture.pptx", display_name="lecture.pptx", is_active=True, local_path=str(original), download_status="downloaded", last_seen_at=self.now, raw_data={}))
        root = archive / "Knowledge Markdown"
        mirror = root / "Courses/previous/course/lecture.pptx.md"
        mirror.parent.mkdir(parents=True)
        mirror.write_text("# 测试讲义\n知识点", encoding="utf-8")
        marker = root / "99_System/Course-Sync.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"version": 1, "source_root": str(archive), "items": {original.relative_to(archive).as_posix(): {"text_ready": True, "status": "completed"}}}), encoding="utf-8")
        knowledge = CompiledKnowledge(lambda: SimpleNamespace(archive_root=str(archive)), self.root / "projects.json", engine=self.engine)
        with patch.object(knowledge, "_text", side_effect=AssertionError("cross-term read")):
            self.assertEqual(0, knowledge.search("知识点", term_id="term:10")["count"])
        result = knowledge.search("知识点", term_id="term:20", course_id="2")
        self.assertEqual("term:20", result["items"][0]["term_id"])
        registry = ReadOnlyToolRegistry(self.engine, compiled_knowledge=knowledge)
        self.assertEqual(1, registry.execute("search_compiled_knowledge", {"term_id": "term:20"}, ("search_compiled_knowledge",)).result["count"])
        with self.assertRaises(CompiledKnowledgeError):
            knowledge.search(term_id="../x")


class HistorySyncTests(unittest.TestCase):
    def test_authorization_or_rate_error_aborts_without_persisting(self):
        empty = CollectionFetchResult([], None, False)
        for status in (401, 429, None):
            with self.subTest(status=status), patch.object(sync_data_to_db, "get_sync_state", return_value=None), patch.object(sync_data_to_db, "build_http_client", return_value=httpx.Client(trust_env=False)), patch.object(sync_data_to_db, "fetch_course_announcements_incremental", return_value=empty), patch.object(sync_data_to_db, "fetch_course_assignments_incremental", return_value=empty), patch.object(sync_data_to_db, "fetch_course_files_incremental", side_effect=CanvasCheckError("synthetic", status_code=status)), patch.object(sync_data_to_db, "persist_canvas_data") as persist:
                with self.assertRaises(CanvasCheckError):
                    sync_data_to_db.sync_canvas(object(), selected_courses=[course(1, 10)], download=False, notify=False, saved_token="fixture")
                persist.assert_not_called()

    def test_history_cli_options_require_scope_and_disable_notifications(self):
        self.assertEqual(["2"], sync_data_to_db.parse_args(["--canvas-only", "--term-id", "term:20", "--course-id", "2"]).course_id)
        with self.assertRaises(SystemExit):
            sync_data_to_db.parse_args(["--mail-only", "--term-id", "term:20"])
