from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class ProductionImportBoundaryTests(unittest.TestCase):
    def test_production_modules_do_not_import_cli_test_scripts(self) -> None:
        script = textwrap.dedent(
            """
            import importlib
            import sys

            modules = (
                "sjtu_learning_assistant.archive_service",
                "sjtu_learning_assistant.credential_store",
                "sjtu_learning_assistant.dashboard_service",
                "sjtu_learning_assistant.desktop_learning_service",
                "sjtu_learning_assistant.mail_client",
                "sync_courses_to_db",
                "sync_data_to_db",
            )
            for module in modules:
                importlib.import_module(module)

            forbidden = {"test_canvas", "test_mail"}.intersection(sys.modules)
            if forbidden:
                raise SystemExit(f"production imports loaded CLI test scripts: {sorted(forbidden)}")
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=False,
            cwd=Path(__file__).resolve().parents[1],
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
