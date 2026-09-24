from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sjtu_learning_assistant import (
    ai_keychain,
    canvas_sync,
    credential_store,
    mail_sync,
)
from sjtu_learning_assistant.cloud_storage import sjtu_pan
from sjtu_learning_assistant.dashboard_service import DashboardService


class FakeKeyringError(Exception):
    pass


class FakeKeyring:
    def __init__(self, values=()):
        self.values = dict(values)
        self.get_calls = list()
        self.set_calls = list()
        self.delete_calls = list()

    def get_password(self, service, account):
        self.get_calls.append((service, account))
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.set_calls.append((service, account, value))
        self.values.update((((service, account), value),))

    def delete_password(self, service, account):
        self.delete_calls.append((service, account))
        self.values.pop((service, account), None)


class CredentialMetadataTests(unittest.TestCase):
    @patch.object(credential_store.sys, "platform", "darwin")
    @patch.object(credential_store.subprocess, "run")
    def test_macos_presence_check_never_requests_secret(self, runner):
        runner.return_value = SimpleNamespace(returncode=0)

        self.assertTrue(
            credential_store.keychain_item_exists("service-name", "account-name")
        )

        command = runner.call_args.args[0]
        self.assertEqual(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                "service-name",
                "-a",
                "account-name",
            ],
            command,
        )
        self.assertNotIn("-w", command)

    @patch.object(credential_store.sys, "platform", "darwin")
    @patch.object(credential_store.subprocess, "run")
    def test_macos_presence_check_handles_missing_and_errors(self, runner):
        runner.return_value = SimpleNamespace(returncode=44)
        self.assertFalse(credential_store.keychain_item_exists("service", "account"))

        runner.return_value = SimpleNamespace(returncode=1)
        with self.assertRaises(credential_store.CredentialStoreError):
            credential_store.keychain_item_exists("service", "account")

    def test_specialized_presence_checks_share_metadata_path(self):
        with (
            patch.object(
                ai_keychain, "keychain_item_exists", return_value=True
            ) as ai_exists,
            patch.object(
                sjtu_pan, "keychain_item_exists", return_value=True
            ) as cloud_exists,
        ):
            self.assertTrue(ai_keychain.ai_api_key_saved())
            self.assertTrue(sjtu_pan.user_token_saved())

        ai_exists.assert_called_once_with(
            ai_keychain.AI_KEYCHAIN_SERVICE, ai_keychain.AI_KEYCHAIN_ACCOUNT
        )
        cloud_exists.assert_called_once_with(
            sjtu_pan.KEYCHAIN_SERVICE, sjtu_pan.KEYCHAIN_ACCOUNT
        )


class CanvasCredentialCacheTests(unittest.TestCase):
    def setUp(self):
        canvas_sync.clear_canvas_credential_cache()

    def tearDown(self):
        canvas_sync.clear_canvas_credential_cache()

    def test_default_reads_once_in_process_and_is_thread_safe(self):
        location = (canvas_sync.KEYCHAIN_SERVICE, canvas_sync.KEYCHAIN_ACCOUNT)
        keyring = FakeKeyring(((location, "canvas-token"),))
        with patch.object(
            canvas_sync,
            "load_keyring_module",
            return_value=(keyring, FakeKeyringError),
        ):
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = tuple(
                    executor.map(lambda _index: canvas_sync.get_token(True), range(16))
                )
        self.assertEqual(tuple(("canvas-token", True) for _index in range(16)), results)
        self.assertEqual((location,), tuple(keyring.get_calls))

    def test_save_updates_delete_invalidates_and_input_waits_for_save(self):
        location = (canvas_sync.KEYCHAIN_SERVICE, canvas_sync.KEYCHAIN_ACCOUNT)
        keyring = FakeKeyring()
        with (
            patch.object(
                canvas_sync,
                "load_keyring_module",
                return_value=(keyring, FakeKeyringError),
            ),
            patch.object(canvas_sync.getpass, "getpass", side_effect=("first", "second")),
        ):
            self.assertEqual(("first", False), canvas_sync.get_token(True))
            self.assertEqual(("second", False), canvas_sync.get_token(True))
            canvas_sync.save_token("second")
            self.assertEqual(("second", True), canvas_sync.get_token(True))
            canvas_sync.delete_token()
            keyring.values.update(((location, "replacement"),))
            self.assertEqual(("replacement", True), canvas_sync.get_token(True))
        self.assertEqual(4, len(keyring.get_calls))


class MailCredentialCacheTests(unittest.TestCase):
    def setUp(self):
        mail_sync.clear_mail_credential_cache()

    def tearDown(self):
        mail_sync.clear_mail_credential_cache()

    def test_default_reads_once_and_accounts_are_isolated(self):
        first = "student-one"
        second = "student-two"
        service = mail_sync.KEYCHAIN_SERVICE
        keyring = FakeKeyring(
            (((service, first), "first-password"), ((service, second), "second-password"))
        )
        with patch.object(
            mail_sync,
            "load_keyring_module",
            return_value=(keyring, FakeKeyringError),
        ):
            self.assertEqual(("first-password", True), mail_sync.get_password(first, True))
            self.assertEqual(("first-password", True), mail_sync.get_password(first, True))
            self.assertEqual(("second-password", True), mail_sync.get_password(second, True))
            self.assertEqual(("second-password", True), mail_sync.get_password(second, True))
        self.assertEqual(((service, first), (service, second)), tuple(keyring.get_calls))

    def test_save_updates_only_account_and_delete_invalidates_it(self):
        first = "student-one"
        second = "student-two"
        service = mail_sync.KEYCHAIN_SERVICE
        keyring = FakeKeyring(
            (((service, first), "first-old"), ((service, second), "second-password"))
        )
        with patch.object(
            mail_sync,
            "load_keyring_module",
            return_value=(keyring, FakeKeyringError),
        ):
            mail_sync.get_password(first, True)
            mail_sync.get_password(second, True)
            mail_sync.save_password(first, "first-new")
            self.assertEqual(("first-new", True), mail_sync.get_password(first, True))
            self.assertEqual(("second-password", True), mail_sync.get_password(second, True))
            mail_sync.delete_password(first)
            keyring.values.update((((service, first), "first-replacement"),))
            self.assertEqual(("first-replacement", True), mail_sync.get_password(first, True))
            self.assertEqual(("second-password", True), mail_sync.get_password(second, True))
        self.assertEqual(4, len(keyring.get_calls))


class AICredentialCacheTests(unittest.TestCase):
    def setUp(self):
        ai_keychain.clear_ai_credential_cache()

    def tearDown(self):
        ai_keychain.clear_ai_credential_cache()

    def test_default_reads_once_save_updates_and_delete_invalidates(self):
        service = ai_keychain.AI_KEYCHAIN_SERVICE
        account = ai_keychain.AI_KEYCHAIN_ACCOUNT
        location = (service, account)
        keyring = FakeKeyring(((location, "old-ai-key"),))
        with patch.object(ai_keychain, "load_keyring_module", return_value=keyring):
            self.assertEqual("old-ai-key", ai_keychain.get_ai_api_key())
            self.assertEqual("old-ai-key", ai_keychain.get_ai_api_key())
            ai_keychain.save_ai_api_key(" new-ai-key ")
            self.assertEqual("new-ai-key", ai_keychain.get_ai_api_key())
            ai_keychain.delete_ai_api_key()
            keyring.values.update(((location, "replacement-ai-key"),))
            self.assertEqual("replacement-ai-key", ai_keychain.get_ai_api_key())
        self.assertEqual(2, len(keyring.get_calls))
        self.assertEqual(((service, account, "new-ai-key"),), tuple(keyring.set_calls))
        self.assertEqual((location,), tuple(keyring.delete_calls))

    def test_explicit_fake_keyring_bypasses_default_cache(self):
        location = (ai_keychain.AI_KEYCHAIN_SERVICE, ai_keychain.AI_KEYCHAIN_ACCOUNT)
        default = FakeKeyring(((location, "default-key"),))
        injected = FakeKeyring(((location, "injected-key"),))
        with patch.object(ai_keychain, "load_keyring_module", return_value=default):
            self.assertEqual("default-key", ai_keychain.get_ai_api_key())
            self.assertEqual(
                "injected-key", ai_keychain.get_ai_api_key(keyring_module=injected)
            )
            ai_keychain.save_ai_api_key("injected-new", keyring_module=injected)
            self.assertEqual("default-key", ai_keychain.get_ai_api_key())
        self.assertEqual((location,), tuple(default.get_calls))
        self.assertEqual((location,), tuple(injected.get_calls))


class CredentialStoreCacheInvalidationTests(unittest.TestCase):
    def test_canvas_writes_clear_runtime_cache(self):
        keyring = FakeKeyring(
            (
                (
                    (
                        credential_store.CANVAS_SERVICE,
                        credential_store.CANVAS_ACCOUNT,
                    ),
                    "old-token",
                ),
            )
        )
        with (
            patch.object(credential_store, "_keyring", return_value=keyring),
            patch.object(canvas_sync, "clear_canvas_credential_cache") as clear_cache,
        ):
            credential_store.save_canvas_token("new-token")
            credential_store.delete_canvas_token()
        self.assertEqual(2, clear_cache.call_count)

    def test_mail_writes_clear_runtime_cache(self):
        account = "student@example.com"
        keyring = FakeKeyring({(credential_store.MAIL_SERVICE, account): "old-password"})
        with (
            patch.object(credential_store, "_keyring", return_value=keyring),
            patch.object(mail_sync, "clear_mail_credential_cache") as clear_cache,
        ):
            credential_store.save_mail_password(account, "new-password")
            credential_store.delete_mail_password(account)
        self.assertEqual(2, clear_cache.call_count)


class CredentialStatusTests(unittest.TestCase):
    def test_settings_status_uses_metadata_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "archive"
            root.mkdir()
            service = DashboardService(SimpleNamespace(), archive_root=root)
            with (
                patch(
                    "sjtu_learning_assistant.dashboard_service.canvas_token_saved",
                    return_value=True,
                ) as canvas_saved,
                patch(
                    "sjtu_learning_assistant.dashboard_service.mail_password_saved",
                    return_value=False,
                ) as mail_saved,
                patch(
                    "sjtu_learning_assistant.dashboard_service.user_token_saved",
                    return_value=True,
                ) as cloud_saved,
            ):
                status = service.settings_status()

        self.assertTrue(status["canvas_token_saved"])
        self.assertFalse(status["mail_password_saved"])
        self.assertTrue(status["cloud_token_saved"])
        canvas_saved.assert_called_once_with()
        mail_saved.assert_called_once()
        cloud_saved.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
