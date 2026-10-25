from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "scan_secrets", ROOT / "scripts" / "scan_secrets.py"
)
assert SPEC is not None and SPEC.loader is not None
scanner_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner_module)


class ScanSecretsTests(unittest.TestCase):
    def test_scanner_covers_github_fine_grained_and_slack_tokens(self) -> None:
        values = (
            "github_pat_" + "a" * 45,
            "xoxb-" + "a" * 25,
        )
        for value in values:
            with self.subTest(prefix=value.split("_")[0].split("-")[0]):
                self.assertTrue(any(pattern.search(value) for pattern in scanner_module.PATTERNS))

    def test_scanner_reports_count_of_skipped_non_utf8_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "binary.dat"
            source.write_bytes(b"\xff\xfe")
            stderr = io.StringIO()
            with (
                patch.object(scanner_module, "ROOT", root),
                patch.object(scanner_module, "candidates", return_value=[source]),
                redirect_stderr(stderr),
            ):
                self.assertEqual(0, scanner_module.main())
            self.assertIn("1 个非 UTF-8 文件未扫描", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
