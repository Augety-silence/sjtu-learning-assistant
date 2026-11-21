from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from sjtu_learning_assistant.markdown_progress import course_progress, transcript_progress


class MarkdownProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_basic_ready_with_ai_failed_and_restart_snapshot(self):
        source = self.root / "archive"
        path = source / "term" / "lecture.pdf"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"source")
        target = source / "Knowledge Markdown"
        mirror = target / "Courses/term/lecture.pdf.md"
        mirror.parent.mkdir(parents=True)
        mirror.write_text("basic text")
        self.write(target / "99_System/Course-Sync.json", dict(version=1, source_root=str(source), items={"term/lecture.pdf": dict(text_ready=True, mirror_hash="a" * 64, status="failed", error="请求超时")}))
        result = course_progress(source, [path, path])
        self.assertEqual((1, 1, 0, 0, 0, 1), tuple(result[k] for k in ("total", "completed", "pending", "failed", "ai_completed", "ai_failed")))
        self.assertEqual("timeout", result["issues"][0]["code"])
        self.assertEqual(result, course_progress(source, [path]))
        mirror.unlink()
        self.assertEqual(1, course_progress(source, [path])["failed"])

    def test_missing_manifest_pending_and_unsafe_paths_never_completed(self):
        path = self.root / "lecture.pdf"
        path.write_bytes(b"source")
        result = course_progress(self.root, [path])
        self.assertEqual(1, result["pending"])
        outside = self.root / "../outside.pdf"
        self.assertEqual(1, course_progress(self.root, [outside])["failed"])
        target = self.root / "Knowledge Markdown"
        self.write(target / "99_System/Course-Sync.json", dict(version=1, source_root="wrong root", items={}))
        self.assertEqual("manifest", course_progress(self.root, [path])["issues"][0]["code"])

    def test_transcripts_basic_and_ai_are_independent_and_no_body_exposed(self):
        directory = self.root / "videos" / ("a" * 32)
        records = {}
        for kind, filename in (("lesson", "lesson.md"), ("original", "original.md")):
            file = directory / filename
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("敏感字幕正文")
            records[kind] = dict(path=filename, sha256=hashlib.sha256(file.read_bytes()).hexdigest())
        self.write(directory / "manifest.json", dict(video=dict(title="第三堂"), status="completed", artifacts=records, lesson_notes=dict(status="pending", error_code="timeout")))
        result = transcript_progress(self.root)
        self.assertEqual((1, 1, 0, 0, 0, 1), tuple(result[k] for k in ("total", "completed", "pending", "failed", "ai_completed", "ai_failed")))
        self.assertNotIn("敏感字幕正文", json.dumps(result, ensure_ascii=False))
        self.assertEqual("timeout", result["issues"][0]["code"])

    def test_malformed_and_symlinked_manifests_do_not_claim_completion(self):
        directory = self.root / "videos" / ("b" * 32)
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text("not json")
        self.assertEqual(1, transcript_progress(self.root)["failed"])
        (directory / "manifest.json").unlink()
        (directory / "manifest.json").symlink_to(self.root / "not-authorized")
        self.assertEqual(1, transcript_progress(self.root)["failed"])

    def test_dashboard_bridge_snapshot_does_not_start_workers(self):
        from desktop_app import DesktopBridge
        from sjtu_learning_assistant.dashboard_service import DashboardService
        from sjtu_learning_assistant.local_settings import LocalSettings
        from datetime import datetime, timezone
        service = DashboardService.__new__(DashboardService)
        service._effective_settings = lambda: LocalSettings(archive_root=str(self.root))
        service._course_markdown_paths = lambda: []
        service.local_projects_path = self.root / "projects.json"
        service.now = lambda: datetime.now(timezone.utc)
        service.start_course_markdown_sync = Mock()
        service.start_transcript_markdown_sync = Mock()
        bridge = DesktopBridge(service)
        self.assertTrue(bridge.invoke("markdown_sync_progress", {})["ok"])
        self.assertFalse(bridge.invoke("markdown_sync_progress", {"unexpected": True})["ok"])
        service.start_course_markdown_sync.assert_not_called()
        service.start_transcript_markdown_sync.assert_not_called()
