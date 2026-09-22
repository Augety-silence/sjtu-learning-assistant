from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sjtu_learning_assistant.dashboard_service import DashboardError, DashboardService


class DesktopActionSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "archive"
        self.root.mkdir()
        self.commands: list[list[str]] = []

        def runner(command, **_kwargs):
            self.commands.append(list(command))
            return SimpleNamespace(returncode=0)

        self.service = DashboardService(
            SimpleNamespace(), archive_root=self.root, command_runner=runner
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_reveal_uses_fixed_open_arguments_for_valid_archive_file(self) -> None:
        document = self.root / "课程" / "讲义.pdf"
        document.parent.mkdir()
        document.write_bytes(b"pdf")
        self.service._local_path_for_source_id = lambda _source_id: str(document)

        self.assertEqual("revealed", self.service.reveal_material("42")["status"])
        self.assertEqual(
            [["/usr/bin/open", "-R", str(document.resolve())]], self.commands
        )

    def test_external_open_accepts_only_plain_https_urls(self) -> None:
        self.assertEqual(
            "opened", self.service.open_external("https://example.edu/a")["status"]
        )
        self.assertEqual([["/usr/bin/open", "https://example.edu/a"]], self.commands)
        for unsafe in (
            "http://example.edu",
            "https://user:password@example.edu",
            "file:///tmp/a",
            "javascript:alert(1)",
        ):
            with self.subTest(url=unsafe), self.assertRaises(DashboardError):
                self.service.open_external(unsafe)


class SyncReentryTests(unittest.TestCase):
    @patch("sjtu_learning_assistant.dashboard_service.sys.platform", "linux")
    def test_second_sync_is_rejected_until_first_process_exits(self) -> None:
        release = threading.Event()

        class Process:
            def wait(self):
                release.wait(1)

        service = DashboardService(
            SimpleNamespace(), process_launcher=lambda *_args, **_kwargs: Process()
        )
        self.assertEqual("accepted", service.trigger_sync()["status"])
        self.assertEqual("already_running", service.trigger_sync()["status"])
        release.set()
        deadline = time.monotonic() + 1
        while service._sync_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(service._sync_lock.locked())


if __name__ == "__main__":
    unittest.main()
