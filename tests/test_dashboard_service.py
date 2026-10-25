from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.orm import Session

from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.dashboard_service import (
    DashboardError,
    DashboardService,
    NotFoundError,
    _desktop_open_command,
)
from sjtu_learning_assistant.canvas_sync import CanvasCheckError
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.models import Assignment, Course, SyncState


class DashboardOverviewTests(unittest.TestCase):
    def test_overview_returns_five_nearest_deadlines_without_seven_day_cutoff(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)
            engine = create_database_engine(
                default_sqlite_url(Path(directory) / "dashboard.db")
            )
            bootstrap_sqlite(engine)
            with Session(engine) as session, session.begin():
                course = Course(
                    source_id="course-1",
                    name="测试课程",
                    course_code="TEST001",
                    term_name="2026 秋",
                    raw_data={},
                )
                session.add(course)
                session.flush()
                for days in (1, 3, 6, 8, 12, 20):
                    session.add(
                        Assignment(
                            source_id=f"deadline-{days}",
                            course_id=course.id,
                            name=f"第 {days} 天截止",
                            due_at=now + timedelta(days=days),
                            is_active=True,
                            last_seen_at=now,
                            raw_data={},
                        )
                    )
                session.add(
                    Assignment(
                        source_id="already-submitted",
                        course_id=course.id,
                        name="已提交作业",
                        due_at=now + timedelta(hours=12),
                        submission_state="submitted",
                        is_active=True,
                        last_seen_at=now,
                        raw_data={},
                    )
                )

            service = DashboardService(engine, now_provider=lambda: now)
            overview = service.overview()
            engine.dispose()

        self.assertEqual(3, overview["upcoming_deadlines"])
        self.assertEqual(
            [
                "deadline-1",
                "deadline-3",
                "deadline-6",
                "deadline-8",
                "deadline-12",
            ],
            [item["source_id"] for item in overview["deadlines"]],
        )

    def test_deadlines_rejects_invalid_service_level_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = create_database_engine(
                default_sqlite_url(Path(directory) / "dashboard.db")
            )
            bootstrap_sqlite(engine)
            service = DashboardService(engine)
            for hours, limit in ((-1, 10), (8761, 10), (True, 10), (24, 0), (24, 501)):
                with self.subTest(hours=hours, limit=limit):
                    with self.assertRaises(DashboardError):
                        service.deadlines(hours, limit=limit)
            engine.dispose()


class DashboardCalendarTests(unittest.TestCase):
    def test_calendar_reads_assignments_landed_by_canvas_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)
            engine = create_database_engine(
                default_sqlite_url(Path(directory) / "calendar.db")
            )
            bootstrap_sqlite(engine)
            with Session(engine) as session, session.begin():
                course = Course(
                    source_id="123",
                    name="本地同步课程",
                    course_code="LOCAL001",
                    term_name="2026 秋",
                    raw_data={},
                )
                other_course = Course(
                    source_id="456",
                    name="其他课程",
                    course_code="OTHER001",
                    term_name="2026 秋",
                    raw_data={},
                )
                session.add_all([course, other_course])
                session.flush()
                session.add_all(
                    [
                        Assignment(
                            source_id="assignment-1",
                            course_id=course.id,
                            name="数据库中的作业",
                            due_at=now + timedelta(days=2),
                            submission_state="unsubmitted",
                            url="https://oc.sjtu.edu.cn/courses/123/assignments/1",
                            is_active=True,
                            last_seen_at=now,
                            raw_data={
                                "created_at": "2026-10-01T01:00:00Z",
                                "unlock_at": "2026-10-02T01:00:00Z",
                            },
                        ),
                        Assignment(
                            source_id="assignment-2",
                            course_id=other_course.id,
                            name="应被课程筛选排除",
                            due_at=now + timedelta(days=3),
                            is_active=True,
                            last_seen_at=now,
                            raw_data={},
                        ),
                    ]
                )
                session.add(
                    SyncState(
                        source="canvas",
                        resource="courses",
                        last_sync_at=now,
                        last_success_at=now,
                        status="success",
                    )
                )

            service = DashboardService(engine, now_provider=lambda: now)
            calendar = service.calendar(2026, 10, course_ids=[123])
            sync_status = service.sync_status()
            engine.dispose()

        self.assertEqual(
            ["assignment-1"],
            [item["id"] for item in calendar["month_events"]],
        )
        self.assertEqual(
            ["assignment-1"],
            [item["id"] for item in calendar["upcoming_events"]],
        )
        self.assertEqual("course_123", calendar["month_events"][0]["context_code"])
        self.assertEqual(
            "2026-10-10T16:00:00+08:00", sync_status["last_canvas_success_at"]
        )


class DashboardFileSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "archive"
        self.root.mkdir()
        self.commands: list[list[str]] = []
        self.service = DashboardService(
            SimpleNamespace(),
            archive_root=self.root,
            command_runner=self.command_runner,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def command_runner(self, command, **_kwargs):
        self.commands.append(list(command))
        return SimpleNamespace(returncode=0)

    def set_local_path(self, path: Path | str | None) -> None:
        self.service._local_path_for_source_id = lambda _source_id: (
            str(path) if path is not None else None
        )

    def test_open_uses_argument_array_without_shell(self) -> None:
        document = self.root / "课程" / "讲义.pdf"
        document.parent.mkdir()
        document.write_bytes(b"pdf")
        self.set_local_path(document)
        result = self.service.open_material("42")
        self.assertEqual("opened", result["status"])
        self.assertEqual(
            [_desktop_open_command(document.resolve())], self.commands
        )

    def test_rejects_outside_missing_directory_and_symlink(self) -> None:
        outside = Path(self.temp.name) / "outside.pdf"
        outside.write_bytes(b"outside")
        self.set_local_path(outside)
        with self.assertRaises(NotFoundError):
            self.service.open_material("42")

        directory = self.root / "folder"
        directory.mkdir()
        self.set_local_path(directory)
        with self.assertRaises(DashboardError):
            self.service.open_material("42")

        target = self.root / "real.pdf"
        target.write_bytes(b"real")
        link = self.root / "link.pdf"
        link.symlink_to(target)
        self.set_local_path(link)
        with self.assertRaisesRegex(DashboardError, "symlink"):
            self.service.open_material("42")
        self.assertEqual([], self.commands)

    def test_missing_source_id_is_not_found(self) -> None:
        self.set_local_path(None)
        with self.assertRaises(NotFoundError):
            self.service.open_material("missing")


class DashboardSettingsTests(unittest.TestCase):
    def test_settings_status_never_loads_or_reads_keychain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "archive"
            root.mkdir()
            service = DashboardService(SimpleNamespace(), archive_root=root)
            with (
                patch("sjtu_learning_assistant.canvas_sync.load_keyring_module") as canvas_keyring,
                patch("sjtu_learning_assistant.mail_sync.load_keyring_module") as mail_keyring,
            ):
                status = service.settings_status()
        canvas_keyring.assert_not_called()
        mail_keyring.assert_not_called()
        self.assertEqual(str(root), status["archive_root"])
        self.assertEqual(
            {
                "archive_root_ready",
                "archive_root",
                "credential_storage_name",
                "mail_account",
                "canvas_token_saved",
                "mail_password_saved",
                "cloud_token_saved",
                "credential_status_error",
                "auto_download_current_term",
                "organize_by_category",
                "ai_enabled",
                "ai_base_url",
                "ai_model",
                "ai_key_saved",
                "ai_chat_send_shortcut",
                "ai_reply_language",
                "ai_attachment_context_budget",
                "ai_auto_open_activity",
                "ai_code_line_numbers",
                "theme_mode",
            },
            set(status),
        )

    def test_invalid_saved_canvas_token_has_actionable_non_retryable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "archive"
            root.mkdir()
            service = DashboardService(SimpleNamespace(), archive_root=root)
            with (
                patch("sjtu_learning_assistant.canvas_sync.get_saved_token", return_value="bad\u200btoken"),
                patch(
                    "sjtu_learning_assistant.canvas_sync.build_http_client",
                    side_effect=CanvasCheckError("Canvas Access Token 含有无效字符，请重新复制。"),
                ),
            ):
                with self.assertRaises(DashboardError) as context:
                    service._get_canvas_client()
        self.assertIn("重新复制", str(context.exception))
        self.assertFalse(context.exception.retryable)

    def test_archive_root_changes_update_cloud_archive_service(self) -> None:
        class Store:
            def __init__(inner_self, root):
                inner_self.root = str(root)

            def resolve(inner_self, **_kwargs):
                return SimpleNamespace(archive_root=inner_self.root)

            def update(inner_self, payload):
                inner_self.root = payload["archive_root"]
                return SimpleNamespace(archive_root=inner_self.root)

        with tempfile.TemporaryDirectory() as directory:
            initial = Path(directory) / "initial"
            updated = Path(directory) / "updated"
            picked = Path(directory) / "picked"
            initial.mkdir()
            updated.mkdir()
            picked.mkdir()
            store = Store(initial)
            service = DashboardService(
                SimpleNamespace(),
                settings_store=store,
                folder_picker=lambda: str(picked),
            )
            service.settings_status = lambda: {}

            service.update_settings({"archive_root": str(updated)})
            self.assertEqual(Path(os.path.abspath(updated)), service.cloud_archive.archive_root)
            service.pick_archive_root()

        self.assertEqual(
            Path(os.path.abspath(picked)), service.cloud_archive.archive_root
        )


class DashboardActionTests(unittest.TestCase):
    def test_download_calls_existing_archive_service(self) -> None:
        archive = SimpleNamespace(
            download_file_by_source_id=lambda source_id: SimpleNamespace(
                source_id=source_id,
                status="downloaded",
                size=8,
                error=None,
            ),
            canvas_client=SimpleNamespace(close=lambda: None),
        )
        service = DashboardService(
            SimpleNamespace(), archive_service_factory=lambda: archive
        )
        self.assertEqual(
            {"source_id": "old-term-file", "status": "downloaded", "size": 8},
            service.download_material("old-term-file"),
        )

    @patch("sjtu_learning_assistant.dashboard_service.sys.platform", "linux")
    def test_sync_fallback_launches_single_instance_runner_without_waiting(self) -> None:
        launches: list[tuple[list[str], dict[str, object]]] = []

        def launcher(command, **kwargs):
            launches.append((list(command), kwargs))
            return SimpleNamespace()

        settings = SimpleNamespace(
            resolve=lambda **_overrides: SimpleNamespace(
                archive_root="/tmp/archive",
                auto_download_current_term=True,
                organize_by_category=True,
                mail_account="",
            )
        )
        service = DashboardService(
            SimpleNamespace(), settings_store=settings, process_launcher=launcher
        )
        result = service.trigger_sync()
        self.assertEqual({"status": "accepted", "mode": "sync_runner"}, result)
        self.assertEqual(1, len(launches))
        self.assertIn("run-once", launches[0][0])
        self.assertIn("--canvas-only", launches[0][0])
        self.assertTrue(launches[0][1]["start_new_session"])



class DashboardTranscriptPhase1Tests(unittest.TestCase):
    def test_v2_read_only_facade_preserves_absent_contract(self) -> None:
        class FakeTranscript:
            def list_v2_artifacts(self, job_id):
                return {"available": False, "status": None, "items": [], "job_id": job_id}

            def read_artifact(self, artifact_id):
                return {"id": artifact_id, "kind": "quality", "content": "{}", "data": {}}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "archive"
            root.mkdir()
            service = DashboardService(
                SimpleNamespace(),
                archive_root=root,
                transcript_service=FakeTranscript(),
            )
            job_id = "a" * 32
            opaque_id = "b" * 32
            absent = service.transcript_v2_artifacts(job_id)
            self.assertFalse(absent["available"])
            read = service.transcript_v2_artifact_read(f"{job_id}:v2:{opaque_id}")
            self.assertEqual({}, read["data"])
            with self.assertRaises(DashboardError):
                service.transcript_v2_artifact_read(f"{job_id}:quality")


if __name__ == "__main__":
    unittest.main()
