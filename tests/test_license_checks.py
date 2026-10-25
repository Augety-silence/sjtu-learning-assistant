from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_licenses", ROOT / "scripts" / "check_licenses.py"
)
assert SPEC is not None and SPEC.loader is not None
license_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(license_module)


class LicenseChecksTests(unittest.TestCase):
    def test_permissive_or_branch_matches_complete_branch_only(self) -> None:
        self.assertTrue(license_module.has_permissive_or_branch("GPL-3.0-only OR MIT"))
        self.assertTrue(license_module.has_permissive_or_branch("GPL OR BSD 3-Clause"))
        self.assertTrue(license_module.has_permissive_or_branch("(MIT OR GPL-3.0)"))
        self.assertFalse(license_module.has_permissive_or_branch("GPL OR Limited License"))
        self.assertFalse(license_module.has_permissive_or_branch("GPL OR miscellaneous terms"))

    def test_non_distributable_license_markers_are_rejected(self) -> None:
        distribution = unittest.mock.Mock()
        distribution.metadata = {"Name": "private-package", "License": "UNLICENSED"}
        with patch.object(license_module, "dependency_closure", return_value={"private-package": distribution}):
            errors = license_module.check_group("runtime", {"private-package"}, allow_pyinstaller=False)
        self.assertTrue(any("不可分发许可证" in error for error in errors))

    def test_windows_package_mapping_is_scanned_once(self) -> None:
        with (
            patch.object(
                license_module,
                "requirement_names",
                side_effect=[{"runtime"}, {"psycopg"}, set(), {"windows-only"}],
            ),
            patch.object(license_module.importlib.metadata, "packages_distributions", return_value={"windows_only": ["windows-only"]}) as scan,
            patch.object(license_module, "check_group", return_value=[]),
        ):
            self.assertEqual(0, license_module.main())
        scan.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
