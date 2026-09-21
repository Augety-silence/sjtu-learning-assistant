from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dashboard_control


class DashboardControlTests(unittest.TestCase):
    def test_plist_is_loopback_keepalive_and_contains_no_secret(self) -> None:
        plist = dashboard_control.build_plist(17655)
        args = plist["ProgramArguments"]
        self.assertEqual("com.sjtu.learningassistant.dashboard", plist["Label"])
        self.assertTrue(plist["RunAtLoad"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual("127.0.0.1", args[args.index("--host") + 1])
        self.assertEqual("17655", args[args.index("--port") + 1])
        serialized = plistlib.dumps(plist).lower()
        for forbidden in (b"token", b"password", b"database_url", b"sjtu_database_url"):
            self.assertNotIn(forbidden, serialized)

    def test_port_collision_is_reported_before_writing_plist(self) -> None:
        with (
            patch.object(dashboard_control.sys, "platform", "darwin"),
            patch.object(dashboard_control, "port_is_available", return_value=False),
            patch.object(Path, "is_file", return_value=True),
            patch.object(dashboard_control.os, "access", return_value=True),
            patch.object(dashboard_control, "write_plist_atomic") as write_plist,
        ):
            self.assertEqual(2, dashboard_control.install(17655))
            write_plist.assert_not_called()

    def test_open_uses_argument_array_without_shell(self) -> None:
        with patch.object(
            dashboard_control.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            self.assertEqual(0, dashboard_control.open_dashboard(17655))
        run.assert_called_once_with(
            ["/usr/bin/open", "http://127.0.0.1:17655/"], check=False
        )

    def test_atomic_plist_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agents = Path(directory)
            plist_path = agents / "dashboard.plist"
            with (
                patch.object(dashboard_control, "LAUNCH_AGENTS_DIR", agents),
                patch.object(dashboard_control, "PLIST_PATH", plist_path),
            ):
                dashboard_control.write_plist_atomic(b"plist")
            self.assertEqual(b"plist", plist_path.read_bytes())
            self.assertEqual(0o600, plist_path.stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
