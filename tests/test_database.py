from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from sjtu_learning_assistant.database import (
    DATABASE_URL_ENV,
    DatabaseConfigError,
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

    def test_environment_url_overrides_other_sources(self) -> None:
        value = (
            "postgresql+psycopg://user:secret@db.example.com:5432/app"
            "?sslmode=require"
        )
        with patch.dict(os.environ, {DATABASE_URL_ENV: value}):
            self.assertEqual(value, get_database_url())

    def test_remote_url_requires_tls(self) -> None:
        with self.assertRaisesRegex(DatabaseConfigError, "sslmode"):
            validate_database_url(
                "postgresql+psycopg://user:secret@db.example.com:5432/app"
            )

    def test_rejects_non_postgresql_driver(self) -> None:
        with self.assertRaises(DatabaseConfigError):
            validate_database_url("sqlite:///app.db")

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
