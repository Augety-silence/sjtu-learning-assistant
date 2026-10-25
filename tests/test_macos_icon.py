from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_macos_icon", ROOT / "scripts" / "generate_macos_icon.py"
)
assert SPEC is not None and SPEC.loader is not None
icon_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(icon_module)


class MacOSIconTest(unittest.TestCase):
    def test_app_icon_uses_transparent_macos_safe_area(self) -> None:
        icon = icon_module.render_app_icon()
        self.assertEqual((1024, 1024), icon.size)
        self.assertEqual("RGBA", icon.mode)
        alpha_box = icon.getchannel("A").getbbox()
        self.assertIsNotNone(alpha_box)
        assert alpha_box is not None
        self.assertGreaterEqual(alpha_box[0], 99)
        self.assertGreaterEqual(alpha_box[1], 99)
        self.assertLessEqual(alpha_box[2], 925)
        self.assertLessEqual(alpha_box[3], 925)

    def test_missing_iconutil_does_not_modify_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            icon_path = Path(directory) / "app-icon.png"
            logo_path = Path(directory) / "app-logo.png"
            with (
                patch.object(icon_module, "APP_ICON_PNG", icon_path),
                patch.object(icon_module, "LOGO_OUTPUT", logo_path),
                patch.object(icon_module.shutil, "which", return_value=None),
                patch.object(icon_module, "render_app_icon") as render_icon,
            ):
                with self.assertRaisesRegex(SystemExit, "iconutil"):
                    icon_module.main()
            render_icon.assert_not_called()
            self.assertFalse(icon_path.exists())
            self.assertFalse(logo_path.exists())

    def test_iconutil_failure_is_reported_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_icon = root / "app-icon.png"
            logo = root / "app-logo.png"
            with (
                patch.object(icon_module, "APP_ICON_PNG", app_icon),
                patch.object(icon_module, "LOGO_OUTPUT", logo),
                patch.object(icon_module, "ICONSET", root / "icons.iconset"),
                patch.object(icon_module, "OUTPUT", root / "app.icns"),
                patch.object(icon_module.shutil, "which", return_value="/usr/bin/iconutil"),
                patch.object(icon_module, "render_app_icon", return_value=Image.new("RGBA", (8, 8))),
                patch.object(icon_module, "render_dashboard_logo", return_value=Image.new("RGBA", (8, 8))),
                patch.object(
                    icon_module.subprocess,
                    "run",
                    side_effect=subprocess.CalledProcessError(1, "iconutil"),
                ),
            ):
                with self.assertRaisesRegex(SystemExit, "iconutil 生成"):
                    icon_module.main()


if __name__ == "__main__":
    unittest.main()
