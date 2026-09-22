from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sjtu_learning_assistant.database import (
    DATABASE_URL_ENV,
    DEFAULT_SQLITE_PATH,
    DatabaseConfigError,
    default_sqlite_url,
    get_database_url,
    redact_database_url,
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
        self.assertEqual(
            Path.home()
            / "Library"
            / "Application Support"
            / "SJTU Learning Assistant"
            / "data"
            / "app.db",
            DEFAULT_SQLITE_PATH,
        )
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

    def test_environment_variable_name_is_stable(self) -> None:
        with patch.dict(os.environ, {DATABASE_URL_ENV: "value"}):
            self.assertEqual("value", os.environ[DATABASE_URL_ENV])


if __name__ == "__main__":
    unittest.main()
