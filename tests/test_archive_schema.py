from __future__ import annotations

import importlib.util
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from sqlalchemy import create_engine, inspect

from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.models import CourseFile


class ArchiveSchemaTests(unittest.TestCase):
    def test_orm_and_0006_migration_contain_download_state(self) -> None:
        columns = CourseFile.__table__.columns
        for name in (
            "download_status",
            "download_attempts",
            "downloaded_at",
            "downloaded_size",
            "download_sha256",
            "download_error",
            "downloaded_source_updated_at",
        ):
            self.assertIn(name, columns)

        migration_path = (
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "0006_add_course_file_download_fields.py"
        )
        spec = importlib.util.spec_from_file_location("migration_0006", migration_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual("0006", module.revision)
        self.assertEqual("0005", module.down_revision)
        attempts = columns.get("download_attempts")
        self.assertFalse(attempts.nullable)
        self.assertEqual("0", str(attempts.server_default.arg))


    def test_orm_and_0009_migration_contain_manual_override(self) -> None:
        columns = CourseFile.__table__.columns
        for name in ("manual_category", "manual_folder_id", "manual_override"):
            self.assertIn(name, columns)

        migration_path = (
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "0009_add_manual_file_classification.py"
        )
        spec = importlib.util.spec_from_file_location("migration_0009", migration_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual("0009", module.revision)
        self.assertEqual("0008", module.down_revision)
        self.assertFalse(columns.get("manual_override").nullable)



    def test_sqlite_bootstrap_adds_manual_columns_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(
                "sqlite+pysqlite:///" + str(Path(directory) / "legacy.db")
            )
            try:
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        "CREATE TABLE emails (id INTEGER PRIMARY KEY)"
                    )
                    connection.exec_driver_sql(
                        "CREATE TABLE course_files (id INTEGER PRIMARY KEY)"
                    )
                with patch(
                    "sjtu_learning_assistant.desktop_database.Base.metadata.create_all"
                ):
                    self.assertEqual("0010", bootstrap_sqlite(engine))
                    self.assertEqual("0010", bootstrap_sqlite(engine))
                columns = tuple(
                    column["name"]
                    for column in inspect(engine).get_columns("course_files")
                )
                for name in (
                    "manual_category",
                    "manual_folder_id",
                    "manual_override",
                ):
                    self.assertIn(name, columns)
            finally:
                engine.dispose()



if __name__ == "__main__":
    unittest.main()
