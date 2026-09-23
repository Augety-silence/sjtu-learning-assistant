from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
