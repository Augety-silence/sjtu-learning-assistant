from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_windows_manifest", ROOT / "scripts" / "verify_windows_manifest.py"
)
assert SPEC is not None and SPEC.loader is not None
manifest_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manifest_module)


class VerifyWindowsManifestTests(unittest.TestCase):
    def test_accepts_manifest_bytes_from_resource_fallback_tuple(self) -> None:
        payload = (
            b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1" '
            b'xmlns:trust="urn:schemas-microsoft-com:asm.v3" '
            b'xmlns:ws="http://schemas.microsoft.com/SMI/2016/WindowsSettings">'
            b'<trust:trustInfo><trust:security><trust:requestedPrivileges>'
            b'<trust:requestedExecutionLevel level="asInvoker" uiAccess="false"/>'
            b'</trust:requestedPrivileges></trust:security></trust:trustInfo>'
            b'<application><windowsSettings><ws:longPathAware>true'
            b'</ws:longPathAware></windowsSettings></application>'
            b'<dependency><dependentAssembly><assemblyIdentity '
            b'name="Microsoft.Windows.Common-Controls" version="6.0.0.0"/>'
            b'</dependentAssembly></dependency></assembly>'
        )
        with patch.object(
            manifest_module,
            "read_manifest_from_executable",
            return_value=(1033, payload),
        ):
            manifest_module.verify_long_path_manifest(Path("app.exe"))

    def test_rejects_manifest_without_common_controls_v6(self) -> None:
        payload = (
            b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1" '
            b'xmlns:trust="urn:schemas-microsoft-com:asm.v3" '
            b'xmlns:ws="http://schemas.microsoft.com/SMI/2016/WindowsSettings">'
            b'<trust:trustInfo><trust:security><trust:requestedPrivileges>'
            b'<trust:requestedExecutionLevel level="asInvoker"/>'
            b'</trust:requestedPrivileges></trust:security></trust:trustInfo>'
            b'<application><windowsSettings><ws:longPathAware>true'
            b'</ws:longPathAware></windowsSettings></application></assembly>'
        )
        with patch.object(manifest_module, "read_manifest_from_executable", return_value=payload):
            with self.assertRaisesRegex(RuntimeError, "Common-Controls v6"):
                manifest_module.verify_long_path_manifest(Path("app.exe"))

    @unittest.skipIf(os.name == "nt", "requires a non-Windows platform")
    def test_non_windows_invocation_reports_platform_error(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "app.exe"
            executable.touch()
            with (
                patch("sys.argv", ["verify_windows_manifest.py", str(executable)]),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit),
            ):
                manifest_module.main()
        self.assertIn("只能在 Windows", stderr.getvalue())

    def test_invalid_manifest_is_reported_without_traceback(self) -> None:
        stderr = io.StringIO()
        with (
            patch.object(
                manifest_module,
                "read_manifest_from_executable",
                return_value=b"not xml",
            ),
            redirect_stderr(stderr),
        ):
            result = manifest_module._verify_or_report(Path("app.exe"))
        self.assertEqual(1, result)
        self.assertIn("错误：", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
