from __future__ import annotations

import re
import unittest
from pathlib import Path

from sjtu_learning_assistant.seed_glossary import DOMAIN_IDS


ROOT = Path(__file__).resolve().parents[1]


class PackagingResourceTests(unittest.TestCase):
    def test_desktop_specs_bundle_seed_glossary_directory(self) -> None:
        expected = '(str(ROOT / "resources" / "seed_glossary"), "resources/seed_glossary")'
        for relative in ("packaging/desktop.spec", "packaging/windows.spec"):
            with self.subTest(spec=relative):
                self.assertIn(expected, (ROOT / relative).read_text(encoding="utf-8"))

    def test_windows_bundle_check_covers_every_seed_pack(self) -> None:
        script = (ROOT / "scripts/build_windows_app.ps1").read_text(encoding="utf-8")
        self.assertIn('_internal\\resources\\seed_glossary', script)
        checked = set(re.findall(r'^\s+"([a-z_]+\.json)",?$', script, re.MULTILINE))
        expected = {f"{domain_id}.json" for domain_id in DOMAIN_IDS}
        self.assertEqual(expected, checked)
        self.assertEqual(
            expected,
            {path.name for path in (ROOT / "resources/seed_glossary").glob("*.json")},
        )


if __name__ == "__main__":
    unittest.main()
