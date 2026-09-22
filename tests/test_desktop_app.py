from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from desktop_app import DesktopBridge, DesktopScheduler, main, safe_message


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

    def open_external(self, url):
        return {"url": url, "status": "opened"}


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
