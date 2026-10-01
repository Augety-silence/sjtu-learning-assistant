from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

from sjtu_learning_assistant.diagnostic_bundle import (
    DiagnosticBundleError,
    DiagnosticBundleService,
    MAX_ENTRY_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    ZIP_ENTRY_ALLOWLIST,
    sanitize_text,
)


class DiagnosticBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE sync_runs ("
                    "status TEXT NOT NULL, error TEXT, started_at TEXT NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO sync_runs(status, error, started_at) VALUES "
                    "('success', NULL, '2026-09-30'), "
                    "('failed', 'Bearer unsafe-token timeout', '2026-09-29')"
                )
            )

    def tearDown(self) -> None:
        self.engine.dispose()

    def _service(self, log_path: Path) -> DiagnosticBundleService:
        return DiagnosticBundleService(
            self.engine,
            config_status_provider=lambda: {
                "canvas_token_saved": True,
                "mail_password_saved": False,
                "cloud_token_saved": True,
                "ai_key_saved": False,
                "mail_account": "must-not-export@example.edu",
                "archive_root": "/Users/alice/Documents/course",
            },
            status_provider=lambda: {
                "status": "idle",
                "last_run_status": "failed",
                "last_run_at": "2026-09-29T00:00:00+08:00",
            },
            log_path=log_path,
        )

    def test_sanitizer_redacts_credentials_urls_email_and_user_paths(self) -> None:
        fake_jwt = "eyJ" + "a" * 30 + "." + "b" * 15 + "." + "c" * 15
        source = " ".join(
            (
                "Authorization: Bearer fake-bearer-token",
                "token=fake-token password='fake password' secret=fake-secret",
                "cookie=session=fake-cookie api_key=sk-fake123456789",
                "https://canvas.example.edu/courses/1?access_token=fake#private",
                "student@example.edu",
                "/Users/alice/Documents/private.txt",
                r"C:\Users\Alice\Documents\private.txt",
                fake_jwt,
            )
        )
        result = sanitize_text(source)
        for forbidden in (
            "fake-bearer-token",
            "fake-token",
            "fake password",
            "fake-secret",
            "fake-cookie",
            "sk-fake123456789",
            "access_token=fake",
            "student@example.edu",
            "/Users/alice",
            r"C:\Users\Alice",
            fake_jwt,
        ):
            self.assertNotIn(forbidden, result)
        self.assertIn("https://canvas.example.edu/courses/1?[redacted]#[redacted]", result)

    def test_bundle_uses_exact_allowlist_and_safe_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "sync.jsonl"
            log.write_text(
                '{"event":"failed","token":"fake-token","path":"/Users/alice/private",'
                '"url":"https://canvas.example.edu/api?token=fake",'
                '"email":"student@example.edu"}\n',
                encoding="utf-8",
            )
            destination = root / "bundle.zip"
            result = self._service(log).export(destination)
            self.assertEqual("created", result["status"])
            self.assertEqual("bundle.zip", result["filename"])
            self.assertNotIn(str(root), str(result))

            with zipfile.ZipFile(destination) as bundle:
                self.assertTrue(set(bundle.namelist()).issubset(ZIP_ENTRY_ALLOWLIST))
                self.assertEqual(
                    {
                        "app_info.json",
                        "logs/sync.jsonl",
                        "manifest.json",
                        "status/summary.json",
                    },
                    set(bundle.namelist()),
                )
                self.assertTrue(all(".." not in Path(name).parts for name in bundle.namelist()))
                contents = "\n".join(
                    bundle.read(name).decode("utf-8") for name in bundle.namelist()
                )
                self.assertNotIn("fake-token", contents)
                self.assertNotIn("/Users/alice", contents)
                self.assertNotIn("student@example.edu", contents)
                app_info = json.loads(bundle.read("app_info.json"))
                self.assertEqual("sqlite", app_info["database"]["dialect"])
                self.assertTrue(app_info["database"]["reachable"])
                self.assertEqual(
                    {
                        "ai_configured": False,
                        "canvas_configured": True,
                        "cloud_configured": True,
                        "mail_configured": False,
                    },
                    app_info["config"],
                )
                summary = json.loads(bundle.read("status/summary.json"))
                self.assertEqual(
                    {"failed": 1, "success": 1},
                    summary["database"]["tables"]["sync_runs"]["status_counts"],
                )
                self.assertEqual(
                    {"authentication": 1},
                    summary["database"]["tables"]["sync_runs"]["error_category_counts"],
                )

    def test_log_and_total_size_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "sync.jsonl"
            log.write_text(("safe-log-line\n" * 100_000), encoding="utf-8")
            entries = self._service(log)._build_entries()
            self.assertLessEqual(len(entries["logs/sync.jsonl"]), MAX_ENTRY_BYTES)
            self.assertLessEqual(
                sum(len(payload) for payload in entries.values()),
                MAX_TOTAL_UNCOMPRESSED_BYTES,
            )

    def test_atomic_failure_removes_temporary_file_and_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "bundle.zip"
            service = self._service(root / "missing.log")
            with patch("sjtu_learning_assistant.diagnostic_bundle.os.replace", side_effect=OSError("nope")):
                with self.assertRaises(DiagnosticBundleError) as raised:
                    service.export(destination)
            self.assertEqual("无法生成调试包。", str(raised.exception))
            self.assertFalse(destination.exists())
            self.assertEqual([], list(root.glob(".sjtu-debug-*.tmp")))

    def test_entry_validation_rejects_traversal_and_oversize(self) -> None:
        with self.assertRaises(DiagnosticBundleError):
            DiagnosticBundleService._validate_entry("../secret", b"safe")
        with self.assertRaises(DiagnosticBundleError):
            DiagnosticBundleService._validate_entry(
                "logs/sync.jsonl", b"x" * (MAX_ENTRY_BYTES + 1)
            )


if __name__ == "__main__":
    unittest.main()
