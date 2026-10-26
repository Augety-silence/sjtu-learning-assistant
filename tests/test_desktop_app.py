from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop_app import (
    DesktopBridge,
    DesktopScheduler,
    _enable_macos_window_material,
    _initialize_desktop_window,
    _install_native_file_drop_bridge,
    _macos_dragged_file_paths,
    _native_file_drop_paths,
    _notify_database_recovery,
    _report_startup_error,
    main,
    run_desktop_app,
    safe_message,
)
from sjtu_learning_assistant.diagnostic_bundle import DiagnosticBundleError
from sjtu_learning_assistant.database import DatabaseConfigError
from sjtu_learning_assistant.desktop_database import DesktopDatabaseError
from sjtu_learning_assistant.cloud_storage import (
    delete_user_token as delete_cloud_user_token,
    save_user_token as save_cloud_user_token,
)


class DesktopAppKeyringBackendTests(unittest.TestCase):
    def _import_with_platform(self, backend=None, platform_name="darwin"):
        environment = os.environ.copy()
        if backend is None:
            environment.pop("PYTHON_KEYRING_BACKEND", None)
        else:
            environment.__setitem__("PYTHON_KEYRING_BACKEND", backend)
        script = (
            "import os; "
            "import desktop_app; "
            f"backend = {backend!r}; "
            "os.environ.pop(\"PYTHON_KEYRING_BACKEND\", None); "
            "backend is None or os.environ.__setitem__(\"PYTHON_KEYRING_BACKEND\", backend); "
            f"desktop_app._configure_keyring_backend({platform_name!r}); "
            "print(os.environ.get(\"PYTHON_KEYRING_BACKEND\"))"
        )
        return subprocess.run(
            (sys.executable, "-c", script),
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        ).stdout.strip()

    def test_darwin_sets_native_keyring_backend_by_default(self):
        self.assertEqual(
            "keyring.backends.macOS.Keyring",
            self._import_with_platform(),
        )

    def test_windows_sets_native_keyring_backend_by_default(self):
        self.assertEqual(
            "keyring.backends.Windows.WinVaultKeyring",
            self._import_with_platform(platform_name="win32"),
        )

    def test_darwin_preserves_existing_keyring_backend(self):
        self.assertEqual(
            "custom.backend.Keyring",
            self._import_with_platform(backend="custom.backend.Keyring"),
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

    def ai_attachment_ingest(self, path):
        return {"id": 7, "name": Path(path).name, "status": "local"}

    def local_projects_list(self):
        return {"items": []}

    def local_projects_add(self, source_root, target_root):
        return {
            "id": "0123456789abcdef01234567",
            "source_root": source_root,
            "target_root": target_root,
        }

    def local_projects_refresh(self, project_id):
        return {"id": project_id, "status": "refreshed"}

    def local_projects_remove(self, project_id):
        return {"project_id": project_id, "removed": True}

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
        self.remove_local_values = []

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

    def start(self, *, remove_local):
        self.starts += 1
        self.remove_local_values.append(remove_local)
        return {"status": "started" if self.starts == 1 else "already_running"}


class FakeDiagnosticBundle:
    def __init__(self, error=None):
        self.destinations = []
        self.error = error

    def export(self, destination):
        self.destinations.append(destination)
        if self.error is not None:
            raise self.error
        return {
            "status": "created",
            "filename": "debug.zip",
            "size": 123,
            "entry_count": 3,
        }


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

    def test_local_project_actions_are_allowlisted_and_strict(self):
        listed = self.bridge.invoke("local_projects_list", {})
        self.assertEqual([], listed["data"]["items"])

        added = self.bridge.invoke(
            "local_projects_add",
            {
                "source_root": "/Users/student/Courses",
                "target_root": "/Users/student/Vault",
            },
        )
        self.assertTrue(added["ok"])
        self.assertEqual(
            "/Users/student/Courses", added["data"]["source_root"]
        )

        project_id = "0123456789abcdef01234567"
        refreshed = self.bridge.invoke(
            "local_projects_refresh", {"project_id": project_id}
        )
        self.assertEqual("refreshed", refreshed["data"]["status"])
        removed = self.bridge.invoke(
            "local_projects_remove", {"project_id": project_id}
        )
        self.assertTrue(removed["data"]["removed"])

        for action, payload in (
            ("local_projects_list", {"unexpected": True}),
            ("local_projects_add", {"source_root": "/tmp/source"}),
            ("local_projects_refresh", {"project_id": ""}),
            ("local_projects_remove", {"project_id": project_id, "extra": True}),
        ):
            with self.subTest(action=action):
                self.assertEqual(
                    "operation_failed",
                    self.bridge.invoke(action, payload)["error"]["code"],
                )

    def test_debug_bundle_export_is_allowlisted_and_does_not_return_path(self):
        bundle = FakeDiagnosticBundle()
        bridge = DesktopBridge(
            FakeService(),
            save_file_picker=lambda default_name: f"/Users/alice/Desktop/{default_name}",
            diagnostic_bundle=bundle,
        )
        response = bridge.invoke("debug_bundle_export", {})
        self.assertTrue(response["ok"])
        self.assertEqual("created", response["data"]["status"])
        self.assertEqual("debug.zip", response["data"]["filename"])
        self.assertNotIn("/Users/alice", str(response))
        self.assertEqual(1, len(bundle.destinations))
        self.assertRegex(bundle.destinations[0], r"sjtu-learning-assistant-debug-\d{8}_\d{6}\.zip$")

    def test_debug_bundle_cancel_is_safe_and_does_not_write(self):
        bundle = FakeDiagnosticBundle()
        bridge = DesktopBridge(
            FakeService(),
            save_file_picker=lambda _default_name: None,
            diagnostic_bundle=bundle,
        )
        self.assertEqual(
            {"ok": True, "data": {"status": "cancelled"}},
            bridge.invoke("debug_bundle_export"),
        )
        self.assertEqual([], bundle.destinations)

    def test_debug_bundle_error_is_sanitized(self):
        bundle = FakeDiagnosticBundle(
            DiagnosticBundleError(
                "token=fake-token [ph_EMAIL_4_ph] /Users/alice/private"
            )
        )
        bridge = DesktopBridge(
            FakeService(),
            save_file_picker=lambda _default_name: "/tmp/debug.zip",
            diagnostic_bundle=bundle,
        )
        response = bridge.invoke("debug_bundle_export")
        self.assertEqual("operation_failed", response["error"]["code"])
        self.assertNotIn("fake-token", str(response))
        self.assertNotIn("[ph_EMAIL_4_ph]", str(response))
        self.assertNotIn("/Users/alice", str(response))

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
        self.assertEqual("all", bridge.invoke("assignments_list", {})["data"]["category"])
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

    def test_backup_actions_require_explicit_local_retention_choice(self):
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
        self.assertEqual(
            "started",
            bridge.invoke("backup_start", {"remove_local": False})["data"]["status"],
        )
        self.assertEqual(
            "already_running",
            bridge.invoke("backup_start", {"remove_local": True})["data"]["status"],
        )
        self.assertEqual([False, True], manager.remove_local_values)
        self.assertEqual(
            "operation_failed",
            bridge.invoke("backup_status", {"unexpected": True})["error"]["code"],
        )
        for payload in (
            {},
            {"remove_local": 1},
            {"remove_local": "false"},
            {"remove_local": False, "unexpected": True},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    "operation_failed",
                    bridge.invoke("backup_start", payload)["error"]["code"],
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

    def test_ai_attachment_ingest_is_strictly_allowlisted(self):
        absolute_path = str(Path(tempfile.gettempdir()) / "lecture-notes.md")
        result = self.bridge.invoke(
            "ai_attachment_ingest", {"path": absolute_path}
        )
        self.assertTrue(result["ok"])
        self.assertEqual("lecture-notes.md", result["data"]["name"])
        self.assertNotIn("path", result["data"])
        for payload in (
            {},
            {"path": "relative.txt"},
            {"path": absolute_path, "extra": True},
            {"path": 42},
            {"path": absolute_path + "\x00"},
            {"path": r"\\server\share\lecture-notes.md"},
            {"path": r"\\?\C:\lecture-notes.md"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    "operation_failed",
                    self.bridge.invoke("ai_attachment_ingest", payload)["error"]["code"],
                )

        if sys.platform != "win32":
            posix_path_with_backslash = "/\\lecture-notes.md"
            result = self.bridge.invoke(
                "ai_attachment_ingest", {"path": posix_path_with_backslash}
            )
            self.assertTrue(result["ok"])
            self.assertEqual("\\lecture-notes.md", result["data"]["name"])

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
    def test_database_configuration_error_is_reported_at_startup(self):
        webview = SimpleNamespace()
        with (
            patch("desktop_app.STATIC_INDEX", SimpleNamespace(is_file=lambda: True)),
            patch.dict("sys.modules", {"webview": webview}),
            patch("desktop_app._install_macos_native_file_drop_hook"),
            patch(
                "desktop_app.create_database_engine",
                side_effect=DatabaseConfigError("database configuration invalid"),
            ),
            patch("desktop_app._report_startup_error") as report,
        ):
            self.assertEqual(2, run_desktop_app())

        report.assert_called_once()
        self.assertIn("database configuration invalid", report.call_args.args[0])

    def test_desktop_database_initialization_error_is_reported_and_engine_closed(self):
        engine = Mock()
        engine.dialect.name = "sqlite"
        webview = SimpleNamespace()
        with (
            patch("desktop_app.STATIC_INDEX", SimpleNamespace(is_file=lambda: True)),
            patch.dict("sys.modules", {"webview": webview}),
            patch("desktop_app._install_macos_native_file_drop_hook"),
            patch("desktop_app.create_database_engine", return_value=engine),
            patch(
                "desktop_app.initialize_desktop_database",
                side_effect=DesktopDatabaseError("database initialization failed"),
            ),
            patch("desktop_app._report_startup_error") as report,
        ):
            self.assertEqual(2, run_desktop_app())

        report.assert_called_once()
        self.assertIn("database initialization failed", report.call_args.args[0])
        engine.dispose.assert_called_once_with()

    def test_macos_windowed_startup_error_uses_native_dialog(self):
        with (
            patch("desktop_app.sys.platform", "darwin"),
            patch("desktop_app.sys.stderr", None),
            patch("desktop_app.subprocess.run") as runner,
        ):
            _report_startup_error("界面启动失败")

        arguments = runner.call_args.args[0]
        self.assertEqual("/usr/bin/osascript", arguments[0])
        self.assertEqual("--", arguments[-2])
        self.assertEqual("界面启动失败", arguments[-1])
        runner.assert_called_once_with(
            arguments,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_database_recovery_sends_user_notification(self):
        sender = Mock()
        recovery = Mock(requires_notice=True)
        recovery.user_message.return_value = "数据库已从快照恢复。"
        with patch("desktop_app.database_recovery_result", return_value=recovery):
            _notify_database_recovery(Mock(), sender=sender)

        sender.send.assert_called_once_with(
            "SJTU Learning Assistant",
            "本地数据库已自动恢复",
            "数据库已从快照恢复。",
        )

    def test_macos_material_keeps_nonzero_dynamic_window_background(self):
        native = Mock()
        window = SimpleNamespace(native=native)
        material_base = Mock()
        material_color = object()
        material_base.colorWithAlphaComponent_.return_value = material_color
        appkit = SimpleNamespace(
            NSColor=SimpleNamespace(windowBackgroundColor=Mock(return_value=material_base)),
            NSWindowTitleHidden=object(),
        )
        app_helper = SimpleNamespace(callAfter=lambda callback: callback())

        with (
            patch("desktop_app.sys.platform", "darwin"),
            patch.dict(
                "sys.modules",
                AppKit=appkit,
                PyObjCTools=SimpleNamespace(AppHelper=app_helper),
            ),
        ):
            _enable_macos_window_material(window)

        material_base.colorWithAlphaComponent_.assert_called_once_with(0.08)
        native.setOpaque_.assert_called_once_with(False)
        native.setBackgroundColor_.assert_called_once_with(material_color)
        native.setHasShadow_.assert_called_once_with(True)
        native.setTitlebarAppearsTransparent_.assert_called_once_with(True)
        native.setTitleVisibility_.assert_called_once_with(appkit.NSWindowTitleHidden)
        native.setMovableByWindowBackground_.assert_called_once_with(False)

    @unittest.skipIf(sys.platform == "win32", "macOS native drop adapter uses POSIX file URLs")
    def test_native_drop_bridge_forwards_only_real_unique_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.txt"
            second = root / "second.pdf"
            first.write_text("first", encoding="utf-8")
            second.write_bytes(b"%PDF")
            event = {
                "dataTransfer": {
                    "files": [
                        {"name": first.name, "pywebviewFullPath": str(first)},
                        {"name": first.name, "pywebviewFullPath": str(first)},
                        {"name": second.name, "pywebviewFullPath": str(second)},
                        {"name": "missing", "pywebviewFullPath": str(root / "missing")},
                    ]
                }
            }
            self.assertEqual(
                (str(first), str(second)), _native_file_drop_paths(event)
            )
            event_without_joined_paths = {
                "dataTransfer": {"files": [{"name": "download-display-name"}]}
            }
            self.assertEqual(
                (str(first), str(second)),
                _native_file_drop_paths(
                    event_without_joined_paths,
                    [
                        ("download-display-name", str(first)),
                        (second.name, str(second)),
                    ],
                ),
            )

            class NativeURL:
                def __init__(self, value):
                    self.value = value

                def filePathURL(self):
                    return self

                def absoluteString(self):
                    return "file://" + self.value.replace(" ", "%20")

            pasteboard = SimpleNamespace(
                readObjectsForClasses_options_=lambda _classes, _options: [
                    NativeURL(str(first)),
                    NativeURL(str(second)),
                ]
            )
            sender = SimpleNamespace(
                draggingSource=lambda: None,
                draggingPasteboard=lambda: pasteboard,
            )
            self.assertEqual(
                (str(first), str(second)),
                _macos_dragged_file_paths(
                    sender,
                    url_class=object(),
                    file_urls_only_key=object(),
                ),
            )

            localhost_url = SimpleNamespace(
                filePathURL=lambda: SimpleNamespace(
                    absoluteString=lambda: "file://localhost" + str(first).replace(" ", "%20")
                )
            )
            localhost_sender = SimpleNamespace(
                draggingSource=lambda: None,
                draggingPasteboard=lambda: SimpleNamespace(
                    readObjectsForClasses_options_=lambda _classes, _options: [localhost_url]
                ),
            )
            self.assertEqual(
                (str(first),),
                _macos_dragged_file_paths(
                    localhost_sender, url_class=object(), file_urls_only_key=object()
                ),
            )

            class DropEvent:
                callback = None

                def __iadd__(self, callback):
                    self.callback = callback
                    return self

            drop = DropEvent()
            document = SimpleNamespace(events=SimpleNamespace(drop=drop))
            window = SimpleNamespace(
                events=SimpleNamespace(loaded=SimpleNamespace(wait=lambda _timeout: True)),
                dom=SimpleNamespace(document=document),
                evaluate_js=Mock(),
            )
            _install_native_file_drop_bridge(window)
            self.assertIsNotNone(drop.callback)
            drop.callback(event)
            script = window.evaluate_js.call_args.args[0]
            self.assertIn("sjtu-native-file-drop", script)
            self.assertIn(json.dumps(str(first), ensure_ascii=False), script)
            self.assertIn(json.dumps(str(second), ensure_ascii=False), script)

    def test_native_drop_ignores_path_probe_permission_error(self):
        event = {"dataTransfer": {"files": [{"pywebviewFullPath": "/restricted/file.pdf"}]}}
        with patch.object(Path, "is_file", side_effect=PermissionError("denied")):
            self.assertEqual((), _native_file_drop_paths(event))

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
            patch("desktop_app.sys.platform", "darwin"),
            patch.dict("sys.modules", {"webview": webview}),
            patch("desktop_app.STATIC_INDEX", static_index),
            patch("desktop_app.create_database_engine", return_value=engine),
            patch("desktop_app.DashboardService", return_value=dashboard),
            patch("desktop_app.DesktopLearningService", return_value=learning),
            patch("desktop_app.BackupManager", return_value=backup_manager) as manager_factory,
            patch("desktop_app.DesktopScheduler", return_value=scheduler),
        ):
            self.assertEqual(0, run_desktop_app())

        create_window_call = webview.create_window.call_args
        self.assertEqual(
            ("SJTU 学习助手", "file:///index.html"), create_window_call.args
        )
        self.assertEqual(1280, create_window_call.kwargs["width"])
        self.assertEqual(820, create_window_call.kwargs["height"])
        self.assertEqual((960, 640), create_window_call.kwargs["min_size"])
        self.assertEqual("#EAF1FB", create_window_call.kwargs["background_color"])
        self.assertTrue(create_window_call.kwargs["transparent"])
        self.assertTrue(create_window_call.kwargs["vibrancy"])
        self.assertEqual(_initialize_desktop_window, webview.start.call_args.args[0])
        self.assertEqual((webview.create_window.return_value,), webview.start.call_args.args[1])
        start_kwargs = webview.start.call_args.kwargs
        self.assertFalse(start_kwargs["debug"])
        self.assertFalse(start_kwargs["private_mode"])
        self.assertTrue(start_kwargs["storage_path"].endswith("webview"))
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


class DesktopMcpStdioTests(unittest.TestCase):
    @patch("desktop_app.sys.frozen", True, create=True)
    @patch("desktop_app.sys.argv", ["app", "--mcp-stdio"])
    def test_windowed_mcp_mode_requires_stdio_streams(self):
        with (
            patch("desktop_app.sys.stdin", None),
            patch("desktop_app.sys.stdout", None),
            patch("desktop_app._report_startup_error") as report,
            patch("desktop_app.create_database_engine") as create_engine,
        ):
            self.assertEqual(2, main())
        report.assert_called_once()
        self.assertIn("标准输入", report.call_args.args[0])
        create_engine.assert_not_called()


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
        first_call_failed = threading.Event()
        second_call_completed = threading.Event()
        calls = []

        def task():
            calls.append("called")
            if len(calls) == 1:
                first_call_failed.set()
                raise RuntimeError("ignored")
            second_call_completed.set()

        scheduler = DesktopScheduler(task, interval_seconds=0.01)
        worker = None
        first_observed = False
        second_observed = False
        try:
            scheduler.start()
            worker = scheduler._thread
            scheduler.start()
            self.assertIs(scheduler._thread, worker)
            first_observed = first_call_failed.wait(timeout=5)
            second_observed = second_call_completed.wait(timeout=5)
        finally:
            scheduler.stop()
            worker = scheduler._thread if worker is None else worker
            if worker is not None:
                worker.join(timeout=5)

        self.assertTrue(first_observed, "first task call did not fail within the watchdog")
        self.assertTrue(second_observed, "scheduler did not retry after the first task failure")
        self.assertGreaterEqual(len(calls), 2)
        self.assertIsNotNone(worker)
        self.assertFalse(worker.is_alive(), "scheduler worker remained alive after stop and join")


if __name__ == "__main__":
    unittest.main()
