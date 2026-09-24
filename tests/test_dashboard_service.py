from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sjtu_learning_assistant.dashboard_service import (
    DashboardError,
    DashboardService,
    NotFoundError,
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
        self.assertEqual([["/usr/bin/open", str(document.resolve())]], self.commands)

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



if __name__ == "__main__":
    unittest.main()
