from __future__ import annotations

import imaplib
import errno
import json
import os
import socket
import ssl
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import sync_data_to_db
from sqlalchemy import create_engine, text

from sjtu_learning_assistant import database, mail_sync
from sjtu_learning_assistant.canvas_sync import CanvasCheckError
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.diagnostic_bundle import DiagnosticBundleService
from sjtu_learning_assistant.local_settings import LocalSettings
from sjtu_learning_assistant.mail_client import fetch_incremental_mail
from sjtu_learning_assistant.models import Base
from sjtu_learning_assistant.sync_diagnostics import diagnostic_run, diagnostic_stage, record_event


class MailDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.log = self.root / "logs" / "sync-stages.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def events(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_failure_retains_phase_errno_and_no_exception_payload(self):
        secret = "私密邮件正文 fake-credential-value"
        with diagnostic_run(self.log):
            with self.assertRaises(OSError), diagnostic_stage("email", "imap_connect"):
                raise OSError(errno.ECONNREFUSED, secret)
        failed = next(row for row in self.events() if row["stage"] == "imap_connect" and row["event"] == "failed")
        self.assertEqual([{"type": "ConnectionRefusedError", "errno": errno.ECONNREFUSED}], failed["exception_chain"])
        self.assertNotIn(secret, self.log.read_text())
        if os.name != "nt":
            self.assertEqual(0o600, stat.S_IMODE(self.log.stat().st_mode))

    def test_network_tls_and_auth_failures_are_distinguished_without_real_network(self):
        failures = (
            (socket.gaierror(-2, "private DNS name"), "imap_connect"),
            (TimeoutError("private account"), "imap_connect"),
            (ssl.SSLCertVerificationError("private certificate"), "imap_connect"),
            (imaplib.IMAP4.error("private server response"), "imap_login"),
        )
        for error, expected_stage in failures:
            with self.subTest(error=type(error).__name__), diagnostic_run(self.log):
                client = MagicMock()
                client.__enter__.return_value = client
                client.login.side_effect = error
                with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", side_effect=error if expected_stage == "imap_connect" else None, return_value=client):
                    with self.assertRaises(mail_sync.MailCheckError):
                        fetch_incremental_mail("synthetic@example.edu", "fake", None)
                failed = [row for row in self.events() if row["stage"] == expected_stage and row["event"] == "failed"][-1]
                self.assertEqual(type(error).__name__, failed["exception_chain"][0]["type"])
        self.assertNotIn("private", self.log.read_text())

    def test_select_and_search_failures_have_their_own_stage(self):
        for phase in ("imap_select", "imap_search"):
            with self.subTest(phase=phase), diagnostic_run(self.log):
                client = MagicMock()
                client.__enter__.return_value = client
                client.select.return_value = ("NO" if phase == "imap_select" else "OK", [])
                client.response.return_value = ("UIDVALIDITY", [b"1"])
                client.uid.return_value = ("NO", [])
                with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", return_value=client):
                    with self.assertRaises(mail_sync.MailCheckError):
                        fetch_incremental_mail("synthetic@example.edu", "fake", None)
                self.assertTrue(any(row["stage"] == phase and row["event"] == "failed" for row in self.events()))

    def test_logger_io_failure_does_not_stop_operation(self):
        with patch("sjtu_learning_assistant.sync_diagnostics.os.open", side_effect=PermissionError), diagnostic_run(self.log):
            with diagnostic_stage("email", "credentials"):
                record_event("email", "persist", "counts", fetched=0)

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "POSIX 链接测试")
    def test_logger_and_export_do_not_follow_symlinks(self):
        asset = self.root / "user-asset"
        asset.write_text("私密内容", encoding="utf-8")
        self.log.parent.mkdir()
        self.log.symlink_to(asset)
        with diagnostic_run(self.log):
            record_event("email", "sync", "started")
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            service = DiagnosticBundleService(engine, stage_log_path=self.log)
            content, status = service._read_log(self.log, structured=True)
            self.assertIsNone(content)
            self.assertEqual("unsafe_symlink", status)
            self.assertEqual("私密内容", asset.read_text())
        finally:
            engine.dispose()

    def test_rotated_logs_keep_latest_phase_and_safe_permissions(self):
        with patch("sjtu_learning_assistant.sync_diagnostics.MAX_LOG_BYTES", 600), diagnostic_run(self.log):
            for _ in range(20):
                record_event("email", "imap_fetch", "started")
            record_event("email", "persist", "succeeded")
        self.assertEqual("persist", self.events()[-1]["stage"])
        backup = self.log.with_name(self.log.name + ".1")
        self.assertTrue(backup.is_file())
        if os.name != "nt":
            self.assertEqual(0o600, stat.S_IMODE(backup.stat().st_mode))


class DiagnosticBundleV2Tests(unittest.TestCase):
    def test_pack_contains_source_history_times_logs_and_missing_log_explanation(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        try:
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO sync_runs(source,resource,status,error,started_at,fetched_count,inserted_count,updated_count) VALUES ('email','run','failed',:error,'2026-11-14 07:32:00',0,0,0)"), {"error": "IMAP 连接超时。私密邮件正文"})
                for _ in range(110):
                    connection.execute(text("INSERT INTO sync_runs(source,resource,status,started_at,fetched_count,inserted_count,updated_count) VALUES ('canvas','courses','success','2026-11-14 07:33:00',0,0,0)"))
                connection.execute(text("INSERT INTO sync_state(source,resource,status,cursor,last_error) VALUES ('email','inbox','failed',:cursor,:error)"), {"cursor": "private cursor", "error": "尚未保存邮箱密码。私密邮件正文"})
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                desktop = root / "desktop.log"
                desktop.write_text('2026-11-14 15:32:00,000 ERROR desktop_app: 邮件主题 私密邮件正文\nTraceback (most recent call last):\n  File "/Users/another/project/mail_client.py", line 523, in fetch_incremental_mail\n    password = "private password"\nTimeoutError: 私密邮件正文\n', encoding="utf-8")
                log = root / "sync-stages.jsonl"
                with diagnostic_run(log):
                    record_event("email", "imap_connect", "started")
                destination = root / "bundle.zip"
                DiagnosticBundleService(engine, log_path=root / "missing.jsonl", desktop_log_path=desktop, stage_log_path=log).export(destination)
                with zipfile.ZipFile(destination) as bundle:
                    manifest = json.loads(bundle.read("manifest.json"))
                    details = json.loads(bundle.read("status/sync_details.json"))
                    self.assertEqual(2, manifest["schema_version"])
                    self.assertEqual(sorted(bundle.namelist()), manifest["entries"])
                    self.assertEqual("missing", manifest["log_status"]["logs/sync.jsonl"])
                    self.assertIn("logs/desktop.log", bundle.namelist())
                    self.assertIn("logs/sync-stages.jsonl", bundle.namelist())
                    self.assertEqual("email", details["recent_by_source"]["email"][0]["source"])
                    self.assertEqual("2026-11-14 07:32:00", details["recent_failures"][0]["started_at"])
                    self.assertEqual("timeout", details["recent_failures"][0]["error"]["code"])
                    self.assertEqual("mail_credentials_missing", details["states"][0]["last_error"]["code"])
                    raw = "\n".join(bundle.read(name).decode() for name in bundle.namelist())
                    for private in ("私密邮件正文", "邮件主题", "private password", "private cursor", "/Users/another"):
                        self.assertNotIn(private, raw)
                    self.assertIn("mail_client.py", raw)
                    self.assertIn("TimeoutError", raw)
        finally:
            engine.dispose()

    def test_stage_log_import_filters_unknown_payload_and_handles_deep_json(self):
        raw = json.dumps({"event": "failed", "stage": "imap_login", "source": "email", "response": "私密邮件正文", "password": "private", "exception_chain": [{"type": "error", "args": "private"}]})
        result = DiagnosticBundleService._stage_metadata("[" * 5000 + "0" + "]" * 5000 + "\n" + raw)
        self.assertNotIn("私密邮件正文", result)
        self.assertNotIn("private", result)
        self.assertEqual("imap_login", json.loads(result)["stage"])


class MailPortabilityTests(unittest.TestCase):
    def test_launcher_failure_has_diagnostics_and_releases_in_process_lock(self):
        with tempfile.TemporaryDirectory() as directory, patch("sjtu_learning_assistant.sync_diagnostics.DEFAULT_STAGE_LOG_PATH", Path(directory) / "stages.jsonl"):
            service = DashboardService(SimpleNamespace(), process_launcher=MagicMock(side_effect=FileNotFoundError("private local path")))
            service._fallback_sync_command = lambda: ["synthetic-app"]
            with self.assertRaisesRegex(Exception, "无法启动同步"):
                service.trigger_sync()
            self.assertFalse(service._sync_lock.locked())
            self.assertIsNotNone(service._last_sync_failed_at)
            rows = [json.loads(line) for line in (Path(directory) / "stages.jsonl").read_text().splitlines()]
            self.assertTrue(any(row["stage"] == "launch" and row["event"] == "failed" for row in rows))
            self.assertNotIn("private", json.dumps(rows))

    def test_mail_tls_context_works_without_system_ca_and_keeps_verification(self):
        empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(0, empty.cert_store_stats()["x509_ca"])
        with patch.object(mail_sync.ssl, "create_default_context", return_value=empty):
            context = mail_sync.create_mail_tls_context()
        self.assertGreater(context.cert_store_stats()["x509_ca"], 0)
        self.assertTrue(context.check_hostname)
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)

    def test_frozen_sync_uses_current_app_not_development_python_or_project(self):
        settings = LocalSettings(archive_root="/synthetic/another-user/archive", mail_account="synthetic@example.edu")
        service = DashboardService(SimpleNamespace(), settings_store=SimpleNamespace(resolve=lambda **kwargs: settings))
        for platform, executable in (("darwin", "/synthetic/Other App.app/Contents/MacOS/app"), ("win32", r"D:\Apps\SJTU\app.exe")):
            with self.subTest(platform=platform), patch("sys.frozen", True, create=True), patch("sys.platform", platform), patch("sys.executable", executable):
                command = service._fallback_sync_command()
                self.assertEqual([executable, "--background-sync"], command[:2])
                self.assertNotIn("launchd_control.py", str(command))
                self.assertNotIn(".venv", str(command))
                self.assertNotIn("bytedance", str(command))
                self.assertIn(settings.mail_account, command)

    def test_user_profile_resolution_is_runtime_not_build_machine(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.platform", "darwin"), patch.object(Path, "home", return_value=Path("/synthetic/other-user")):
            self.assertEqual(Path("/synthetic/other-user/Library/Application Support/SJTU Learning Assistant"), database._default_app_support_dir())
        with patch.dict(os.environ, {}, clear=True), patch("sys.platform", "win32"), patch.object(database, "user_data_path", return_value=Path("/synthetic/redirected-appdata")):
            self.assertEqual(Path("/synthetic/redirected-appdata"), database._default_app_support_dir())
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SJTU_APP_SUPPORT_DIR": directory}):
            self.assertEqual(Path(directory).resolve(), database._default_app_support_dir())

    def test_background_credentials_never_prompt_when_missing_or_unreadable(self):
        class FakeKeyringError(Exception):
            pass
        for failure in (None, FakeKeyringError("private backend payload")):
            keyring = MagicMock()
            keyring.get_password.return_value = None
            keyring.get_password.side_effect = failure
            mail_sync.clear_mail_credential_cache()
            with self.subTest(failure=bool(failure)), patch.object(mail_sync, "load_keyring_module", return_value=(keyring, FakeKeyringError)), patch.object(mail_sync.getpass, "getpass") as prompt:
                with self.assertRaises(mail_sync.MailCheckError) as raised:
                    mail_sync.get_password("synthetic@example.edu", True, interactive=False)
                self.assertNotIn("private", str(raised.exception))
                prompt.assert_not_called()
                keyring.get_password.assert_called_once_with(mail_sync.KEYCHAIN_SERVICE, "synthetic@example.edu")

    def test_canvas_failure_does_not_skip_mail_but_keeps_failure_exit_code(self):
        engine = SimpleNamespace(dialect=SimpleNamespace(name="synthetic"), dispose=MagicMock())
        settings = LocalSettings(archive_root="/synthetic/archive", auto_download_current_term=False)
        with tempfile.TemporaryDirectory() as directory, patch("sjtu_learning_assistant.sync_diagnostics.DEFAULT_STAGE_LOG_PATH", Path(directory) / "sync-stages.jsonl"), patch.object(sync_data_to_db, "create_database_engine", return_value=engine), patch.object(sync_data_to_db, "check_database", return_value=SimpleNamespace(database="synthetic", server_version="test")), patch.object(sync_data_to_db.SettingsStore, "resolve", return_value=settings), patch.object(sync_data_to_db, "sync_canvas", side_effect=CanvasCheckError("synthetic network error")) as canvas, patch.object(sync_data_to_db, "sync_mail") as mail, patch.object(sync_data_to_db, "_record_failure") as failure:
            self.assertEqual(1, sync_data_to_db.main(["--email", "synthetic@example.edu", "--no-download"]))
            events = [json.loads(line) for line in (Path(directory) / "sync-stages.jsonl").read_text().splitlines()]
        canvas.assert_called_once()
        mail.assert_called_once_with(engine, "synthetic@example.edu", 100)
        self.assertEqual("canvas", failure.call_args.args[1])
        self.assertTrue(any(row["source"] == "email" and row["event"] == "succeeded" for row in events))
        self.assertEqual(1, events[-1]["exit_code"])
        engine.dispose.assert_called_once()


if __name__ == "__main__":
    unittest.main()
