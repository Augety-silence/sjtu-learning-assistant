from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
