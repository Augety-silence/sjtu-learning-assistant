"""验证后台同步卡住后的退出、诊断和可重试状态。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import httpx
from sqlalchemy import create_engine

import sync_data_to_db
from sjtu_learning_assistant import canvas_sync, sync_process
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.diagnostic_bundle import DiagnosticBundleService
from sjtu_learning_assistant.models import Base
from sjtu_learning_assistant.sync_diagnostics import diagnostic_run


class CanvasBackgroundCredentialTests(unittest.TestCase):
    def test_missing_and_unreadable_credentials_never_wait_for_input(self):
        for error in (None, canvas_sync.CanvasCheckError("private backend payload")):
            with (
                self.subTest(error=bool(error)),
                patch.object(canvas_sync, "get_saved_token", return_value=None, side_effect=error),
                patch.object(canvas_sync.sys, "stdin", object()),
                patch.object(canvas_sync.getpass, "getpass") as prompt,
            ):
                with self.assertRaises(canvas_sync.CanvasCheckError) as caught:
                    canvas_sync.get_token(True, interactive=False)
                self.assertNotIn("private", str(caught.exception))
                prompt.assert_not_called()

    def test_saved_token_works_without_interactive_input(self):
        with patch.object(canvas_sync, "get_saved_token", return_value="fixture"):
            self.assertEqual(("fixture", True), canvas_sync.get_token(True, interactive=False))

    def test_cli_still_allows_explicit_interactive_input(self):
        with patch.object(canvas_sync.getpass, "getpass", return_value="fixture"):
            self.assertEqual(("fixture", False), canvas_sync.get_token(False))


class CanvasStageDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name) / "stages.jsonl"
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_course_resources_and_persistence_are_visible_without_user_content(self):
        def respond(request):
            payload = [{"id": 10}] if request.url.path.endswith("/modules") else []
            return httpx.Response(200, json=payload)

        client = httpx.Client(base_url="https://oc.sjtu.edu.cn", transport=httpx.MockTransport(respond))
        with patch.object(sync_data_to_db, "build_http_client", return_value=client), diagnostic_run(self.log):
            result = sync_data_to_db.sync_canvas(
                self.engine, saved_token="private-token", notify=False, download=False,
                selected_courses=[{"id": 1, "name": "私密课程名", "course_code": "fixture"}],
            )
        self.assertEqual(1, result["courses"])
        stages = {row["stage"] for row in self.events() if row["event"] == "succeeded"}
        self.assertTrue({"announcements", "assignments", "files", "folders", "modules", "module_items", "http_request", "persist"} <= stages)
        exported = DiagnosticBundleService._stage_metadata(self.log.read_text())
        self.assertIn('"stage": "persist"', exported)
        for private in ("private-token", "私密课程名", "Authorization", "/api/v1"):
            self.assertNotIn(private, exported)

    def test_missing_token_is_logged_before_any_http_client_is_created(self):
        with (
            patch.object(canvas_sync, "get_saved_token", return_value=None),
            patch.object(canvas_sync.getpass, "getpass") as prompt,
            patch.object(sync_data_to_db, "build_http_client") as build,
            diagnostic_run(self.log),
        ):
            with self.assertRaises(canvas_sync.CanvasCheckError):
                sync_data_to_db.sync_canvas(self.engine, notify=False, download=False)
        prompt.assert_not_called()
        build.assert_not_called()
        self.assertTrue(any(row["stage"] == "credentials" and row["event"] == "failed" for row in self.events()))

    def test_client_initialization_and_transport_failures_have_distinct_stages(self):
        with patch.object(canvas_sync.httpx, "Client", side_effect=OSError(13, "private path")), diagnostic_run(self.log):
            with self.assertRaises(OSError):
                canvas_sync.build_http_client("https://oc.sjtu.edu.cn", "fixture", 20)
        failed = [row for row in self.events() if row["event"] == "failed"]
        self.assertEqual("http_client", failed[0]["stage"])
        self.assertEqual(13, failed[0]["exception_chain"][0]["errno"])
        self.assertNotIn("private path", self.log.read_text())

        def fail(request):
            raise httpx.ConnectTimeout("private request payload", request=request)

        with httpx.Client(base_url="https://oc.sjtu.edu.cn", transport=httpx.MockTransport(fail)) as client, diagnostic_run(self.log):
            with self.assertRaises(canvas_sync.CanvasCheckError):
                canvas_sync.fetch_active_courses(client)
        failed = [row for row in self.events() if row["event"] == "failed"]
        self.assertEqual("http_request", failed[-1]["stage"])
        self.assertEqual("ConnectTimeout", failed[-1]["exception_chain"][0]["type"])
        self.assertNotIn("private request payload", self.log.read_text())


class SyncWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name) / "stages.jsonl"
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.service = DashboardService(self.engine)
        self.service._notify_course_markdown_sync = MagicMock()
        self.service._sync_lock.acquire()
        self.log_patch = patch("sjtu_learning_assistant.sync_diagnostics.DEFAULT_STAGE_LOG_PATH", self.log)
        self.log_patch.start()

    def tearDown(self):
        self.log_patch.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def test_real_stalled_process_exits_and_failure_survives_new_service(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            with patch("sjtu_learning_assistant.dashboard_service.DEFAULT_SYNC_TIMEOUT_SECONDS", 0.05):
                self.service._release_sync_when_done(process)
            self.assertIsNotNone(process.poll())
            self.assertFalse(self.service._sync_lock.locked())
            self.assertIsNotNone(self.service._last_sync_failed_at)
            self.service._notify_course_markdown_sync.assert_not_called()
            status = DashboardService(self.engine).sync_status()
            self.assertEqual("idle", status["status"])
            self.assertEqual("failed", status["last_run_status"])
            self.assertIn('"event":"timed_out"', self.log.read_text())
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    def test_force_kill_preserves_lock_until_child_is_reaped(self):
        process = MagicMock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 900), subprocess.TimeoutExpired("fixture", 5), -9]
        with patch("sjtu_learning_assistant.dashboard_service.stop_sync_process") as stop:
            stop.side_effect = lambda *_args, **_kwargs: self.assertTrue(self.service._sync_lock.locked())
            self.service._release_sync_when_done(process)
        self.assertEqual([call(process), call(process, force=True)], stop.call_args_list)
        self.assertFalse(self.service._sync_lock.locked())
        self.assertEqual("failed", self.service.sync_status()["last_run_status"])

    def test_unreaped_process_does_not_allow_a_second_writer(self):
        process = MagicMock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("fixture", 5)
        with patch("sjtu_learning_assistant.dashboard_service.stop_sync_process"):
            self.service._release_sync_when_done(process)
        self.assertTrue(self.service._sync_lock.locked())
        self.assertEqual("already_running", self.service.trigger_sync()["status"])
        self.assertIsNotNone(self.service._last_sync_failed_at)

    def test_normal_exit_resets_failure_and_notifies_material_sync(self):
        self.service._last_sync_failed_at = self.service.now()
        process = MagicMock()
        process.wait.return_value = 0
        self.service._release_sync_when_done(process)
        self.assertFalse(self.service._sync_lock.locked())
        self.assertIsNone(self.service._last_sync_failed_at)
        self.service._notify_course_markdown_sync.assert_called_once_with()

    def test_nonzero_exit_still_notifies_already_committed_materials(self):
        process = MagicMock()
        process.wait.return_value = 1
        self.service._release_sync_when_done(process)
        self.assertFalse(self.service._sync_lock.locked())
        self.assertIsNotNone(self.service._last_sync_failed_at)
        self.service._notify_course_markdown_sync.assert_called_once_with()

    def test_windows_cleanup_targets_owned_process_tree(self):
        process = SimpleNamespace(pid=1234, poll=lambda: None)
        with patch.object(sync_process.sys, "platform", "win32"), patch.object(sync_process.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            sync_process.stop_sync_process(process)
        self.assertEqual(["taskkill.exe", "/PID", "1234", "/T", "/F"], run.call_args.args[0])
        self.assertEqual(5, run.call_args.kwargs["timeout"])
        self.assertEqual(subprocess.DEVNULL, run.call_args.kwargs["stdin"])

    def test_launch_disables_stdin_and_uses_isolated_process_session(self):
        self.service._sync_lock.release()
        self.service._fallback_sync_command = lambda: ["fixture"]
        self.service.process_launcher = MagicMock(return_value=MagicMock())
        with patch("sjtu_learning_assistant.dashboard_service.threading.Thread"):
            self.assertEqual("accepted", self.service.trigger_sync()["status"])
        options = self.service.process_launcher.call_args.kwargs
        self.assertEqual(subprocess.DEVNULL, options["stdin"])
        self.assertTrue(options["start_new_session"])


if __name__ == "__main__":
    unittest.main()
