"""夜审落地回归：只使用临时文件与假输入，不访问账户或模型。"""

from __future__ import annotations

import os
import logging
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import desktop_app
from sjtu_learning_assistant.academic_features import AcademicFeatureService, _numeric_grade
from sjtu_learning_assistant.canvas_client import CanvasProtocolError, normalize_base_url
from sjtu_learning_assistant.agent_runtime.tools import _clip, _redact_body
from sjtu_learning_assistant.backup_service import BackupError, _controlled_json
from sjtu_learning_assistant.cloud_archive_service import CloudArchiveError, redact_public_text
from sjtu_learning_assistant.diagnostic_bundle import sanitize_text
from sjtu_learning_assistant.local_settings import SettingsError, SettingsStore, validate_ai_base_url
from sjtu_learning_assistant.transcript_pipeline import parse_vtt
from sjtu_learning_assistant.update_service import InvalidVersionError, SemanticVersion, UnsafeUpdateURLError, validate_update_url


class NightlyLandingTests(unittest.TestCase):
    def test_vtt_control_filter_preserves_words_and_numbers(self):
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello\tworld 1&#9;2 3&#10;4 &amp; a\x00b\x07c\x1bd\x7fe\x9bf\n"
        self.assertEqual("hello world 1 2 3 4 & abcdef", parse_vtt(vtt)[0].text)

    def test_vtt_three_digit_hours_tag_and_math_text(self):
        vtt = "WEBVTT\n\n100:00:00.000 --> 100:00:02.000\n甲<100:00:01.000>乙 p < 0.05 且 t > 2\n"
        self.assertEqual("甲乙 p < 0.05 且 t > 2", parse_vtt(vtt)[0].text)

    def test_log_formatter_sanitizes_parameters_traceback_and_rotated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "desktop.log"
            handler = desktop_app._PrivateDesktopLogHandler(target, maxBytes=180, backupCount=3)
            handler.setFormatter(desktop_app._SafeDesktopFormatter("%(levelname)s %(message)s"))
            logger = logging.getLogger("sjtu.nightly.regression")
            old_level, old_propagate = logger.level, logger.propagate
            logger.setLevel(logging.WARNING)
            logger.propagate = False
            logger.addHandler(handler)
            try:
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
                    response = client.get("https://example.test/api", params={"access_token": "TAIL-LEAK"})
                for _ in range(6):
                    logger.warning("假的请求回归 %s", response.request.url)
                try:
                    raise ValueError("password=TAIL-LEAK")
                except ValueError:
                    logger.exception("假的异常回归")
            finally:
                logger.removeHandler(handler)
                handler.close()
                logger.setLevel(old_level)
                logger.propagate = old_propagate
            logs = list(Path(directory).glob("desktop.log*"))
            self.assertGreater(len(logs), 1)
            for log in logs:
                self.assertNotIn("TAIL-LEAK", log.read_text(encoding="utf-8"))
                if os.name != "nt":
                    self.assertEqual(0o600, stat.S_IMODE(log.stat().st_mode))

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "POSIX 链接回归")
    def test_log_handler_does_not_follow_symlink_or_break_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory) / "user-asset.txt"
            external.write_text("用户资产", encoding="utf-8")
            target = Path(directory) / "desktop.log"
            target.symlink_to(external)
            handler = desktop_app._PrivateDesktopLogHandler(target, delay=True)
            handler.setFormatter(desktop_app._SafeDesktopFormatter())
            handler.emit(logging.LogRecord("test", logging.ERROR, "fake.py", 1, "token=TAIL-LEAK", (), None))
            handler.close()
            self.assertEqual("用户资产", external.read_text(encoding="utf-8"))

    def test_grade_extreme_numbers_are_not_unhandled_overflows(self):
        self.assertIsNone(AcademicFeatureService._finite_number(10**1000))
        self.assertIsNone(_numeric_grade({"score": 10**1000}))

    def test_canvas_invalid_url_configuration_is_controlled(self):
        for url in ("https://example.com:abc", "https://example.com:99999", "https://xn--/"):
            with self.subTest(url=url), self.assertRaises(CanvasProtocolError):
                normalize_base_url(url)

    def test_cloud_scalar_redaction_consumes_escaped_quotes(self):
        for text in (r'token="a\"TAIL-LEAK" keep=ok', r"password='a\'TAIL-LEAK' keep=ok"):
            with self.subTest(text=text):
                self.assertNotIn("TAIL-LEAK", redact_public_text(text))
                self.assertIn("keep=ok", redact_public_text(text))
                self.assertNotIn("TAIL-LEAK", str(CloudArchiveError(text)))

    def test_agent_scalar_redaction_handles_truncated_and_escaped_values(self):
        for text in (r'token="a\"TAIL-LEAK" keep=ok', r"password='a\'TAIL-LEAK' keep=ok", 'password="TAIL-LEAK', "secret='TAIL-LEAK"):
            with self.subTest(text=text):
                for result in (_clip(text, 1000), _redact_body(text, 1000)[0]):
                    self.assertNotIn("TAIL-LEAK", result)
                    if "keep=ok" in text:
                        self.assertIn("keep=ok", result)
        self.assertIn("普通正文", _redact_body('password="TAIL-LEAK\n普通正文', 1000)[0])

    def test_agent_url_redaction_handles_userinfo_and_missing_host(self):
        for text in ("http://u:ab/c@TAIL-LEAK@host/x", "http://:TAIL-LEAK@host", "http://u:TAIL-LEAK@", "http://u:TAIL-LEAK@\n正文"):
            with self.subTest(text=text):
                self.assertNotIn("TAIL-LEAK", _clip(text, 1000))
                self.assertNotIn("TAIL-LEAK", _redact_body(text, 1000)[0])
        self.assertEqual("https://example.com/normal", _clip("https://example.com/normal", 1000))

    def test_agent_redaction_keeps_even_backslash_terminator(self):
        text = 'password="value' + "\\" * 2 + '" keep=ok'
        self.assertIn("keep=ok", _redact_body(text, 1000)[0])

    def test_settings_invalid_ports_and_hosts_are_controlled(self):
        for url in ("https://example.com:abc/v1", "https://example.com:99999/v1", "https://bad host/v1", "https://xn--/v1"):
            with self.subTest(url=url), self.assertRaises(SettingsError):
                validate_ai_base_url(url)
        self.assertEqual("https://example.com:8443/v1", validate_ai_base_url("https://example.com:8443/v1"))

    @unittest.skipUnless(hasattr(os, "fchmod"), "POSIX 文件权限回归")
    def test_corrupt_settings_quarantine_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "settings.json"
            target.write_text("{bad", encoding="utf-8")
            target.chmod(0o644)
            settings = SettingsStore(target).load(environ={})
            self.assertFalse(settings.auto_download_current_term)
            self.assertEqual(0o600, stat.S_IMODE(target.with_name("settings.json.corrupt").stat().st_mode))

    @unittest.skipUnless(hasattr(os, "symlink") and hasattr(os, "fchmod"), "POSIX 链接回归")
    def test_corrupt_settings_symlink_does_not_chmod_external_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory) / "user-asset.txt"
            external.write_text("{bad", encoding="utf-8")
            external.chmod(0o644)
            target = Path(directory) / "settings.json"
            target.symlink_to(external)
            settings = SettingsStore(target).load(environ={})
            self.assertFalse(settings.auto_download_current_term)
            self.assertEqual(0o644, stat.S_IMODE(external.stat().st_mode))
            self.assertEqual("{bad", external.read_text(encoding="utf-8"))
            self.assertTrue(target.with_name("settings.json.corrupt").is_symlink())

    @unittest.skipUnless(hasattr(os, "fchmod"), "POSIX 文件权限回归")
    def test_quarantine_permission_failure_still_persists_safe_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "settings.json"
            target.write_text("{bad", encoding="utf-8")
            with patch("sjtu_learning_assistant.local_settings.os.fchmod", side_effect=PermissionError):
                settings = SettingsStore(target).load(environ={})
            self.assertFalse(settings.auto_download_current_term)
            self.assertTrue(target.with_name("settings.json.corrupt").exists())
            self.assertFalse(SettingsStore(target).load(environ={}).auto_download_current_term)
            # save 的权限动作也失败时，当前 load 仍不得打开自动下载。

    def test_backup_deep_json_and_large_integer_are_controlled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "manifest.json"
            for payload in ("[" * 50_000 + "0" + "]" * 50_000, '{"n":' + "9" * 10_000 + "}"):
                path.write_text(payload, encoding="utf-8")
                with self.subTest(size=len(payload)), self.assertRaises(BackupError):
                    _controlled_json(root, "manifest.json")

    def test_version_oversized_numeric_components_are_controlled(self):
        for value in ("9" * 10_000 + ".1.1", "1." + "9" * 10_000 + ".1", "1.1." + "9" * 10_000):
            with self.subTest(size=len(value)), self.assertRaises(InvalidVersionError):
                SemanticVersion.parse(value)

    def test_update_urls_reject_control_characters_before_network(self):
        url = "https://github.com/Augety-silence/sjtu-learning-assistant/releases/download/v1.8.7/app.zip"
        for char in ("\r", "\n", "\t", "\x01", "\x7f"):
            with self.subTest(char=repr(char)), self.assertRaises(UnsafeUpdateURLError):
                validate_update_url(url.replace("github", "git" + char + "hub"))
        self.assertEqual(url, validate_update_url(url))

    def test_diagnostic_long_url_punctuation_is_linear_and_preserved(self):
        text = "https://example.com/path?token=TAIL-LEAK" + ")" * 100_000
        start = time.monotonic()
        result = sanitize_text(text)
        self.assertLess(time.monotonic() - start, 5)
        self.assertNotIn("TAIL-LEAK", result)
        self.assertTrue(result.endswith(")" * 100_000))


if __name__ == "__main__":
    unittest.main()
