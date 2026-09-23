from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop_app import DesktopBridge, DesktopScheduler, main, run_desktop_app, safe_message
from sjtu_learning_assistant.cloud_storage import (
    delete_user_token as delete_cloud_user_token,
    save_user_token as save_cloud_user_token,
)


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class FakeService:
    def health(self):
        return {"status": "ok"}

    def overview(self):
        return {"courses": 0}

    def deadlines(self, hours):
        return [{"hours": hours}]

    def messages(self, kind):
        return [{"kind": kind}]

    def material_tree(self):
        return {"root": {"id": "root", "children": []}}

    def sync_status(self):
        return {"status": "idle"}

    def trigger_sync(self):
        return {"status": "accepted"}

    def download_material(self, source_id):
        return {"source_id": source_id, "status": "downloaded"}

    def move_material(self, source_id, target_node_id):
        return {
            "source_id": source_id,
            "target_node_id": target_node_id,
            "status": "saved",
        }

    def restore_material_auto(self, source_id):
        return {"source_id": source_id, "status": "saved"}

    def open_material(self, source_id):
        return {"source_id": source_id, "status": "opened"}

    def reveal_material(self, source_id):
        return {"source_id": source_id, "status": "revealed"}

    def settings_status(self):
        return {"missing": []}

    def ai_chat(self, messages):
        return {"reply": messages[-1]["content"], "model": "qwen"}

    def open_external(self, url):
        return {"url": url, "status": "opened"}


class FakeLearningService:
    def assignments_list(self, category):
        return {"category": category, "items": []}

    def assignment_detail(self, course_id, assignment_id):
        return {"course_id": course_id, "id": assignment_id}

    def can_submit(self, course_id, assignment_id, submission_type=None):
        return {"can_submit": True, "submission_types": [submission_type]}

    def submit_text(self, course_id, assignment_id, text):
        return {"verified": True, "text": text}

    def submit_url(self, course_id, assignment_id, url):
        return {"verified": True, "url": url}

    def submit_local_file(self, course_id, assignment_id, path):
        return {"verified": True, "path": path}

    def pan_list(self, path, page, page_size):
        return {"remote_path": path, "page": page, "page_size": page_size, "items": []}

    def submit_cloud_file(self, course_id, assignment_id, path):
        return {"verified": True, "remote_path": path}

    def open_external_assignment(self, course_id, assignment_id):
        return {"status": "requires_external_submission"}


class FakeBackupManager:
    def __init__(self):
        self.starts = 0

    def status(self):
        return {
            "status": "idle",
            "available": True,
            "availability_message": None,
            "counts": {
                "canvas": 1,
                "mail": 2,
                "ready": 2,
                "missing_local": 1,
                "total": 3,
            },
            "progress": None,
            "last_result": None,
        }

    def start(self):
        self.starts += 1
        return {"status": "started" if self.starts == 1 else "already_running"}


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = DesktopBridge(FakeService())

    def test_only_invoke_is_public_bridge_method(self):
        methods = [name for name in dir(self.bridge) if not name.startswith("_")]
        self.assertEqual(["invoke"], methods)

    def test_allowlisted_actions_and_parameter_validation(self):
        self.assertEqual(168, self.bridge.invoke("deadlines", {"window": "7d"})["data"]["items"][0]["hours"])
        self.assertEqual("not_allowed", self.bridge.invoke("__dict__")["error"]["code"])
        self.assertEqual("operation_failed", self.bridge.invoke("messages", {"kind": "secret"})["error"]["code"])
        self.assertEqual("operation_failed", self.bridge.invoke("material_open", {"source_id": ""})["error"]["code"])
        chat = self.bridge.invoke("ai_chat", {"messages": [{"role": "user", "content": "你好"}]})
        self.assertEqual("你好", chat["data"]["reply"])
        self.assertEqual("operation_failed", self.bridge.invoke("ai_chat", {})["error"]["code"])
        moved = self.bridge.invoke(
            "material_move",
            {"source_id": "file-1", "target_node_id": "category:course-1:other"},
        )
        self.assertEqual("category:course-1:other", moved["data"]["target_node_id"])
        self.assertEqual(
            "operation_failed",
            self.bridge.invoke("material_move", {"source_id": "file-1"})["error"]["code"],
        )
        self.assertEqual(
            "operation_failed",
            self.bridge.invoke(
                "material_move",
                {"source_id": "file-1", "target_node_id": 1},
            )["error"]["code"],
        )

    def test_assignment_action_names_match_public_contract(self):
        bridge = DesktopBridge(FakeService(), FakeLearningService())
        expected = {
            "assignments_list",
            "detail",
            "can_submit",
            "submit_text",
            "submit_url",
            "pick_local_file",
            "submit_local_file",
            "pan_list",
            "submit_cloud_file",
            "open_external_assignment",
        }
        self.assertTrue(expected.issubset(bridge._handlers))
        self.assertFalse({f"assignment_{name}" for name in expected} & set(bridge._handlers))

    def test_assignment_actions_validate_payloads_and_keep_fake_compatibility(self):
        bridge = DesktopBridge(FakeService(), FakeLearningService())
        self.assertEqual("today", bridge.invoke("assignments_list", {"category": "today"})["data"]["category"])
        self.assertTrue(bridge.invoke("submit_text", {"course_id": 1, "assignment_id": 2, "text": "答案"})["data"]["verified"])
        self.assertEqual("operation_failed", bridge.invoke("submit_text", {"course_id": "1", "assignment_id": 2, "text": "答案"})["error"]["code"])
        self.assertEqual("operation_failed", bridge.invoke("submit_url", {"course_id": 1, "assignment_id": 2, "url": "file:///tmp/a"})["error"]["code"])
        self.assertEqual("operation_failed", bridge.invoke("pan_list", {"remote_path": "../secret"})["error"]["code"])
        valid_pan = bridge.invoke("pan_list", {"remote_path": ["课程", "作业"], "page": 2, "page_size": 20})
        self.assertEqual("课程/作业", valid_pan["data"]["remote_path"])
        for invalid_path in ([".."], ["课程/作业"], [1], "课程/作业", [""], ["课程", "a\\b"]):
            with self.subTest(invalid_path=invalid_path):
                self.assertEqual(
                    "operation_failed",
                    bridge.invoke("pan_list", {"remote_path": invalid_path})["error"]["code"],
                )
        cloud = bridge.invoke(
            "submit_cloud_file",
            {"course_id": 1, "assignment_id": 2, "remote_path": ["课程", "report.pdf"]},
        )
        self.assertEqual("课程/report.pdf", cloud["data"]["remote_path"])
        self.assertEqual("operation_failed", self.bridge.invoke("assignments_list", {})["error"]["code"])
        self.assertEqual("ok", self.bridge.invoke("health")["data"]["status"])

    def test_backup_actions_are_allowlisted_require_empty_payload_and_report_start_state(self):
        manager = FakeBackupManager()
        bridge = DesktopBridge(FakeService(), backup_manager=manager)
        self.assertEqual(
            {
                "backup_status",
                "backup_start",
                "backup_token_save",
                "backup_token_delete",
            },
            {name for name in bridge._handlers if name.startswith("backup_")},
        )
        status = bridge.invoke("backup_status", {})
        self.assertTrue(status["ok"])
        self.assertEqual(1, status["data"]["counts"]["missing_local"])
        self.assertEqual(
            {
                "status",
                "available",
                "availability_message",
                "counts",
                "progress",
                "last_result",
            },
            set(status["data"]),
        )
        self.assertEqual("started", bridge.invoke("backup_start")["data"]["status"])
        self.assertEqual("already_running", bridge.invoke("backup_start", {})["data"]["status"])
        for action in ("backup_status", "backup_start"):
            self.assertEqual(
                "operation_failed",
                bridge.invoke(action, {"unexpected": True})["error"]["code"],
            )
        self.assertEqual("operation_failed", self.bridge.invoke("backup_status")["error"]["code"])

    def test_backup_token_actions_use_keychain_without_echoing_secret(self):
        keyring = FakeKeyring()
        bridge = DesktopBridge(FakeService())

        def save(value):
            save_cloud_user_token(value, keyring_module=keyring)

        def delete():
            delete_cloud_user_token(keyring_module=keyring)

        secret = "pan-user-token-private-value"
        with (
            patch("desktop_app.save_user_token", side_effect=save) as save_mock,
            patch("desktop_app.delete_user_token", side_effect=delete) as delete_mock,
        ):
            saved = bridge.invoke("backup_token_save", {"token": f"  {secret}  "})
            self.assertEqual({"ok": True, "data": {"configured": True}}, saved)
            self.assertNotIn(secret, str(saved))
            self.assertEqual([secret], list(keyring.values.values()))
            save_mock.assert_called_once_with(f"  {secret}  ")

            for payload in ({}, {"token": ""}, {"token": 42}, {"token": secret, "extra": True}):
                with self.subTest(payload=payload):
                    response = bridge.invoke("backup_token_save", payload)
                    self.assertEqual("operation_failed", response["error"]["code"])
                    self.assertNotIn(secret, str(response))

            deleted = bridge.invoke("backup_token_delete", {})
            deleted_again = bridge.invoke("backup_token_delete")
            self.assertEqual({"ok": True, "data": {"configured": False}}, deleted)
            self.assertEqual(deleted, deleted_again)
            self.assertEqual({}, keyring.values)
            self.assertEqual(2, delete_mock.call_count)
            self.assertEqual(
                "operation_failed",
                bridge.invoke("backup_token_delete", {"token": secret})["error"]["code"],
            )

    def test_local_file_must_come_from_native_picker(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "answer.txt"
            selected.write_text("answer")
            bridge = DesktopBridge(FakeService(), FakeLearningService(), file_picker=lambda: str(selected))
            picked = bridge.invoke("pick_local_file")["data"]
            self.assertEqual("answer.txt", picked["name"])
            payload = {"course_id": 1, "assignment_id": 2, "path": picked["path"]}
            self.assertTrue(bridge.invoke("submit_local_file", payload)["data"]["verified"])
            self.assertEqual("operation_failed", bridge.invoke("submit_local_file", payload)["error"]["code"])

    def test_errors_are_sanitized_or_hidden(self):
        text = safe_message("postgresql://user:password@db/token=placeholder /Users/example/private/file")
        self.assertNotIn("password", text)
        self.assertNotIn("abcdef", text)
        self.assertNotIn("/Users/alice", text)

        self.bridge._handlers["explode"] = lambda _payload: (_ for _ in ()).throw(RuntimeError("token=top-secret"))
        response = self.bridge.invoke("explode")
        self.assertEqual("internal_error", response["error"]["code"])
        self.assertNotIn("top-secret", str(response))

    def test_safe_message_sanitizes_values_crossing_original_truncation_boundary(self):
        cases = (
            (
                "credential URL",
                "postgresql://alice:boundary-password",
                "@db.local/private",
                ("postgresql://alice", "boundary-password"),
            ),
            (
                "token",
                "token=",
                "boundary-token-value",
                ("token", "boundary-token-value"),
            ),
            (
                "local absolute path",
                "/Users",
                "/alice/Secrets/file.txt",
                ("/Users", "alice/Secrets"),
            ),
        )

        for label, before_boundary, after_boundary, sensitive_fragments in cases:
            with self.subTest(label=label):
                prefix = "x".ljust(399 - len(before_boundary), "x") + " "
                value = prefix + before_boundary + after_boundary
                self.assertEqual(400, len(prefix + before_boundary))
                self.assertGreater(len(value), 400)

                text = safe_message(value)

                self.assertLessEqual(len(text), 400)
                for fragment in sensitive_fragments:
                    self.assertNotIn(fragment, text)
                self.assertIn("[已隐藏]", text)


class DesktopStartupLifecycleTests(unittest.TestCase):
    def test_startup_does_not_require_credentials_and_closes_services(self):
        engine = Mock()
        engine.dialect.name = "postgresql"
        dashboard = Mock()
        learning = Mock()
        scheduler = Mock()
        backup_manager = Mock()
        dashboard.settings_store.load.return_value = SimpleNamespace(mail_account="student")
        webview = SimpleNamespace(
            OPEN_DIALOG=1,
            FOLDER_DIALOG=2,
            windows=[],
            create_window=Mock(),
            start=Mock(),
        )
        static_index = SimpleNamespace(is_file=lambda: True, as_uri=lambda: "file:///index.html")
        with (
            patch.dict("sys.modules", {"webview": webview}),
            patch("desktop_app.STATIC_INDEX", static_index),
            patch("desktop_app.create_database_engine", return_value=engine),
            patch("desktop_app.DashboardService", return_value=dashboard),
            patch("desktop_app.DesktopLearningService", return_value=learning),
            patch("desktop_app.BackupManager", return_value=backup_manager) as manager_factory,
            patch("desktop_app.DesktopScheduler", return_value=scheduler),
        ):
            self.assertEqual(0, run_desktop_app())

        webview.start.assert_called_once_with(debug=False)
        manager_factory.assert_called_once_with(
            engine,
            archive_root=dashboard.archive_root,
            mail_attachments_root=dashboard.mail_attachments_root,
            mail_account="student",
        )
        backup_manager.close.assert_called_once_with()
        dashboard.close.assert_called_once_with()
        learning.close.assert_called_once_with()
        engine.dispose.assert_called_once_with()


class DesktopBackgroundSyncTests(unittest.TestCase):
    @patch("desktop_app.sys.frozen", True, create=True)
    @patch(
        "desktop_app.sys.argv",
        ["app", "--background-sync", "--email", "student-id"],
    )
    def test_forwards_selected_background_sync_options(self):
        with patch("sync_data_to_db.main", return_value=0) as sync_main:
            self.assertEqual(0, main())
        sync_main.assert_called_once_with(["--email", "student-id"])


class DesktopSchedulerTests(unittest.TestCase):
    def test_scheduler_repeats_survives_task_error_and_stops(self):
        called = threading.Event()
        count = 0

        def task():
            nonlocal count
            count += 1
            called.set()
            if count == 1:
                raise RuntimeError("ignored")

        scheduler = DesktopScheduler(task, interval_seconds=0.01)
        scheduler.start()
        scheduler.start()
        self.assertTrue(called.wait(0.3))
        time.sleep(0.04)
        scheduler.stop()
        stopped_count = count
        time.sleep(0.03)
        self.assertGreaterEqual(stopped_count, 2)
        self.assertEqual(stopped_count, count)


if __name__ == "__main__":
    unittest.main()
