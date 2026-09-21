from __future__ import annotations

import fcntl
import json
import os
import plistlib
import signal
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import launchd_control
from sync_runner import SyncRunner


class FakeProcess:
    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code

    def wait(self) -> int:
        return self.exit_code

    def poll(self) -> int:
        return self.exit_code

    def send_signal(self, _signum: int) -> None:
        return None


class SequencedPopen:
    def __init__(self, exit_codes: list[int]) -> None:
        self.exit_codes = list(exit_codes)
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: tuple[str, ...]) -> FakeProcess:
        self.commands.append(command)
        return FakeProcess(self.exit_codes.pop(0))


class SyncRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.lock_path = self.root / "run" / "sync.lock"
        self.log_path = self.root / "logs" / "sync.jsonl"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def read_events(self) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
        ]

    def make_runner(
        self,
        exit_codes: list[int],
        *,
        sleep_calls: list[float] | None = None,
        command: tuple[str, ...] = ("python", "sync_data_to_db.py"),
    ) -> tuple[SyncRunner, SequencedPopen]:
        popen = SequencedPopen(exit_codes)
        sleeps = sleep_calls if sleep_calls is not None else []
        runner = SyncRunner(
            command,
            lock_path=self.lock_path,
            log_path=self.log_path,
            sleeper=sleeps.append,
            popen_factory=popen,
        )
        return runner, popen

    def test_success_runs_once_and_logs_required_fields(self) -> None:
        runner, popen = self.make_runner([0])
        self.assertEqual(0, runner.run())
        self.assertEqual(1, len(popen.commands))
        events = self.read_events()
        self.assertEqual(
            ["run_started", "attempt_started", "attempt_finished", "run_finished"],
            [event["event"] for event in events],
        )
        for event in events:
            self.assertEqual(
                {
                    "utc_time",
                    "local_time",
                    "run_id",
                    "attempt",
                    "event",
                    "exit_code",
                    "duration_ms",
                },
                set(event),
            )
            self.assertTrue(str(event["utc_time"]).endswith("Z"))
            self.assertIn("+08:00", str(event["local_time"]))

    def test_lock_conflict_logs_skip_and_returns_success(self) -> None:
        self.lock_path.parent.mkdir(parents=True)
        lock_fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            runner, popen = self.make_runner([0])
            self.assertEqual(0, runner.run())
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        self.assertEqual([], popen.commands)
        self.assertEqual("skip_locked", self.read_events()[0]["event"])
        self.assertEqual(0, self.read_events()[0]["exit_code"])

    def test_failure_retries_at_five_and_thirty_seconds(self) -> None:
        sleeps: list[float] = []
        runner, popen = self.make_runner([1, 2, 0], sleep_calls=sleeps)
        self.assertEqual(0, runner.run())
        self.assertEqual([5, 30], sleeps)
        self.assertEqual(3, len(popen.commands))
        finished = [
            event for event in self.read_events() if event["event"] == "attempt_finished"
        ]
        self.assertEqual([1, 2, 0], [event["exit_code"] for event in finished])

    def test_exit_130_is_not_retried(self) -> None:
        sleeps: list[float] = []
        runner, popen = self.make_runner([130, 0], sleep_calls=sleeps)
        self.assertEqual(130, runner.run())
        self.assertEqual([], sleeps)
        self.assertEqual(1, len(popen.commands))

    def test_signal_is_forwarded_to_running_child(self) -> None:
        runner, _ = self.make_runner([0])

        class SignalledProcess(FakeProcess):
            received: list[int] = []

            def poll(self) -> None:
                return None

            def wait(self) -> int:
                runner._handle_signal(signal.SIGTERM, None)
                return -signal.SIGTERM

            def send_signal(self, signum: int) -> None:
                self.received.append(signum)

        process = SignalledProcess(0)
        runner.popen_factory = lambda _command: process
        self.assertEqual(143, runner.run())
        self.assertEqual([signal.SIGTERM], process.received)

    def test_persistent_log_never_contains_command_secrets(self) -> None:
        secret = "super-secret-token"
        database_url = "postgresql://user:password@example.invalid/db"
        runner, _ = self.make_runner(
            [1, 1, 1],
            command=("python", "sync.py", "--token", secret, database_url),
        )
        self.assertEqual(1, runner.run())
        content = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, content)
        self.assertNotIn("password", content)
        self.assertNotIn("postgresql://", content)


class LaunchdPlistTests(unittest.TestCase):
    @staticmethod
    def options(**overrides: object) -> Namespace:
        values: dict[str, object] = {
            "canvas_only": False,
            "mail_only": False,
            "email": "name&tag@example.com",
            "initial_mail_limit": 321,
        }
        values.update(overrides)
        return Namespace(**values)

    def test_plist_contains_required_schedule_paths_and_arguments(self) -> None:
        plist = launchd_control.build_plist(self.options(mail_only=True))
        self.assertEqual(launchd_control.LABEL, plist["Label"])
        self.assertEqual(900, plist["StartInterval"])
        self.assertIs(True, plist["RunAtLoad"])
        self.assertEqual(
            {"TZ": "Asia/Shanghai", "PYTHONUNBUFFERED": "1"},
            plist["EnvironmentVariables"],
        )
        arguments = plist["ProgramArguments"]
        self.assertEqual(str(launchd_control.VENV_PYTHON), arguments[0])
        self.assertEqual(str(launchd_control.CONTROL_SCRIPT), arguments[1])
        self.assertIn("--mail-only", arguments)
        self.assertIn("321", arguments)
        self.assertIn("Application Support", plist["StandardOutPath"])
        self.assertIn("Application Support", plist["StandardErrorPath"])

    def test_plistlib_round_trip_escapes_special_argument_characters(self) -> None:
        source = launchd_control.build_plist(self.options())
        payload = plistlib.dumps(source)
        self.assertIn(b"name&amp;tag@example.com", payload)
        self.assertEqual(source, plistlib.loads(payload))

    def test_install_rejects_ambiguous_or_interactive_configuration(self) -> None:
        parser = launchd_control.build_parser()
        with self.assertRaises(SystemExit):
            args = parser.parse_args(["install", "--canvas-only", "--mail-only"])
            launchd_control.validate_sync_options(parser, args, non_interactive=True)
        with self.assertRaises(SystemExit):
            args = parser.parse_args(["install"])
            launchd_control.validate_sync_options(parser, args, non_interactive=True)


if __name__ == "__main__":
    unittest.main()
