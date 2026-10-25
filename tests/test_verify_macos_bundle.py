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
    "verify_macos_bundle", ROOT / "scripts" / "verify_macos_bundle.py"
)
assert SPEC is not None and SPEC.loader is not None
bundle_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle_module)


class VerifyMacOSBundleTests(unittest.TestCase):
    def test_root_forbidden_path_does_not_match_every_absolute_path(self) -> None:
        self.assertEqual(b"", bundle_module._forbidden_path_bytes("/"))
        self.assertEqual(b"/Users/example/", bundle_module._forbidden_path_bytes("/Users/example/"))

    def test_invalid_plist_is_reported_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "App.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            (contents / "Resources").mkdir()
            (contents / "MacOS" / "SJTU Learning Assistant").write_bytes(b"app")
            (contents / "Info.plist").write_bytes(b"not a plist")
            stderr = io.StringIO()
            with (
                patch("sys.argv", ["verify_macos_bundle.py", str(app)]),
                redirect_stderr(stderr),
            ):
                result = bundle_module.main()
        self.assertEqual(1, result)
        self.assertIn("Info.plist 无法读取或格式无效", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
