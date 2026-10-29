from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platformdirs import user_data_path
from sqlalchemy.engine import make_url
from sjtu_learning_assistant.database import (
    APP_SUPPORT_DIR_ENV,
    DATABASE_URL_ENV,
    DEFAULT_SQLITE_PATH,
    DatabaseConfigError,
    default_sqlite_url,
    get_database_url,
    redact_database_url,
    sqlite_database_path,
    validate_database_url,
)


class DatabaseConfigTests(unittest.TestCase):
    def test_accepts_psycopg_postgresql_url(self) -> None:
        value = (
            "postgresql+psycopg://user:secret@db.example.com:5432/app"
            "?sslmode=require"
        )
        self.assertEqual(value, validate_database_url(value))

    def test_default_is_application_support_sqlite_without_keychain_lookup(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "sjtu_learning_assistant.database.load_keyring_module"
        ) as load_keyring:
            value = get_database_url()
        self.assertEqual(default_sqlite_url(DEFAULT_SQLITE_PATH), value)
        expected_support = (
            user_data_path("SJTU Learning Assistant", appauthor=False)
            if sys.platform == "win32"
            else Path.home()
            / "Library"
            / "Application Support"
            / "SJTU Learning Assistant"
        )
        self.assertEqual(expected_support / "data" / "app.db", DEFAULT_SQLITE_PATH)
        load_keyring.assert_not_called()

    def test_environment_postgres_url_explicitly_overrides_sqlite(self) -> None:
        value = (
            "postgresql+psycopg://user:secret@db.example.com:5432/app"
            "?sslmode=require"
        )
        with patch.dict(os.environ, {DATABASE_URL_ENV: value}):
            self.assertEqual(value, get_database_url())

    def test_environment_sqlite_url_can_use_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            value = default_sqlite_url(Path(temporary) / "test.db")
            with patch.dict(os.environ, {DATABASE_URL_ENV: value}):
                self.assertEqual(value, get_database_url())

    def test_sqlite_uri_filename_is_not_treated_as_recovery_path(self) -> None:
        self.assertIsNone(
            sqlite_database_path("sqlite:///file:app.db?mode=ro&uri=true")
        )
        self.assertEqual(
            Path("app.db"), sqlite_database_path("sqlite:///app.db")
        )

    def test_remote_url_requires_tls(self) -> None:
        with self.assertRaisesRegex(DatabaseConfigError, "sslmode"):
            validate_database_url(
                "postgresql+psycopg://user:secret@db.example.com:5432/app"
            )

    def test_rejects_unknown_driver(self) -> None:
        with self.assertRaises(DatabaseConfigError):
            validate_database_url("mysql://localhost/app")

    def test_redacts_password(self) -> None:
        rendered = redact_database_url(
            "postgresql+psycopg://user:secret@db.example.com:5432/app"
            "?sslmode=require"
        )
        self.assertNotIn("secret", rendered)
        self.assertIn("***", rendered)

    def test_redacts_passwords_in_query_parameters_case_insensitively(self) -> None:
        rendered = redact_database_url(
            "postgresql+psycopg://localhost/app"
            "?Password=query-secret&sslpassword=tls-secret&sslmode=require"
        )

        self.assertNotIn("query-secret", rendered)
        self.assertNotIn("tls-secret", rendered)
        self.assertIn("Password=%2A%2A%2A", rendered)
        self.assertIn("sslpassword=%2A%2A%2A", rendered)
        self.assertIn("sslmode=require", rendered)

    def test_environment_variable_name_is_stable(self) -> None:
        with patch.dict(os.environ, {DATABASE_URL_ENV: "value"}):
            self.assertEqual("value", os.environ[DATABASE_URL_ENV])

    def test_app_support_override_routes_all_persistent_roots_to_temp_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected = str((Path(temporary) / "isolated-support").resolve())
            environment = os.environ.copy()
            environment.pop(DATABASE_URL_ENV, None)
            environment[APP_SUPPORT_DIR_ENV] = expected
            script = """
import json
from sjtu_learning_assistant.database import (
    APP_SUPPORT_DIR,
    DEFAULT_DATABASE_URL,
    DEFAULT_SQLITE_PATH,
)
from sjtu_learning_assistant.local_settings import SETTINGS_PATH
from sjtu_learning_assistant.transcript_service import (
    DEFAULT_COURSE_MEMORY_ROOT,
    DEFAULT_TRANSCRIPT_ROOT,
)
from sync_runner import APP_SUPPORT_DIR as SYNC_APP_SUPPORT_DIR, DEFAULT_LOG_DIR
print(json.dumps({
    "support": str(APP_SUPPORT_DIR),
    "database": str(DEFAULT_SQLITE_PATH),
    "database_url": DEFAULT_DATABASE_URL,
    "settings": str(SETTINGS_PATH),
    "transcripts": str(DEFAULT_TRANSCRIPT_ROOT),
    "memory": str(DEFAULT_COURSE_MEMORY_ROOT),
    "sync_support": str(SYNC_APP_SUPPORT_DIR),
    "logs": str(DEFAULT_LOG_DIR),
}))
"""
            result = subprocess.run(
                [sys.executable, "-c", script],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
        paths = json.loads(result.stdout)
        root = Path(expected)
        self.assertEqual(str(root), paths["support"])
        self.assertEqual(str(root / "data" / "app.db"), paths["database"])
        self.assertEqual(
            root / "data" / "app.db",
            Path(make_url(paths["database_url"]).database),
        )
        self.assertEqual(str(root / "settings.json"), paths["settings"])
        self.assertEqual(str(root / "transcripts" / "v1"), paths["transcripts"])
        self.assertEqual(
            str(root / "transcripts" / "v2" / "course_memory"), paths["memory"]
        )
        self.assertEqual(str(root), paths["sync_support"])
        self.assertEqual(str(root / "logs"), paths["logs"])

    def test_app_support_override_rejects_nonempty_relative_path(self) -> None:
        environment = os.environ.copy()
        environment[APP_SUPPORT_DIR_ENV] = "relative/sandbox"
        environment.pop(DATABASE_URL_ENV, None)
        result = subprocess.run(
            [sys.executable, "-c", "import sjtu_learning_assistant.database"],
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn(APP_SUPPORT_DIR_ENV, result.stderr)

    def test_app_support_override_empty_value_keeps_platform_default(self) -> None:
        environment = os.environ.copy()
        environment[APP_SUPPORT_DIR_ENV] = "  "
        environment.pop(DATABASE_URL_ENV, None)
        script = "from sjtu_learning_assistant.database import APP_SUPPORT_DIR; print(APP_SUPPORT_DIR)"
        empty_result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        environment.pop(APP_SUPPORT_DIR_ENV)
        unset_result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(unset_result.stdout.strip(), empty_result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
