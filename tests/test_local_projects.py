from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sjtu_learning_assistant.knowledge_compiler import KnowledgeCompilerService
from sjtu_learning_assistant.local_projects import LocalProjectError, LocalProjectService


class LocalProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "课程素材"
        self.target = self.root / "课程 Vault"
        self.registry = self.root / "state" / "local-projects.json"
        self.source.mkdir()
        self.target.mkdir()
        (self.source / "第一讲.md").write_text("# 第一讲\n\n![[图.png]]\n", encoding="utf-8")
        self.compiler = KnowledgeCompilerService(
            ai_runner=lambda _model, _system, _content: "# result"
        )
        self.service = LocalProjectService(self.registry, self.compiler)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_add_list_and_duplicate_update_are_persistent(self) -> None:
        added = self.service.add(str(self.source), str(self.target))
        self.assertEqual("课程素材", added["name"])
        self.assertEqual(1, added["markdown_files"])
        self.assertEqual(1, added["image_references"])
        self.assertEqual("idle", added["compile_status"])
        self.assertTrue(added["available"])

        duplicated = self.service.add(str(self.source), str(self.target))
        self.assertEqual(added["id"], duplicated["id"])
        restored = LocalProjectService(self.registry, self.compiler).list()
        self.assertEqual(1, len(restored["items"]))
        self.assertEqual(0o600, self.registry.stat().st_mode & 0o777)

    def test_refresh_updates_inventory_and_remove_never_deletes_project_files(self) -> None:
        project = self.service.add(str(self.source), str(self.target))
        (self.source / "第二讲.md").write_text("# 第二讲\n", encoding="utf-8")

        refreshed = self.service.refresh(project["id"])
        self.assertEqual(2, refreshed["markdown_files"])

        result = self.service.remove(project["id"])
        self.assertTrue(result["removed"])
        self.assertEqual([], self.service.list()["items"])
        self.assertTrue(self.source.is_dir())
        self.assertTrue(self.target.is_dir())
        self.assertTrue((self.source / "第一讲.md").is_file())

    def test_missing_folder_is_reported_without_dropping_registry(self) -> None:
        project = self.service.add(str(self.source), str(self.target))
        moved = self.root / "已移动"
        self.source.rename(moved)

        listed = self.service.list()["items"][0]
        self.assertEqual(project["id"], listed["id"])
        self.assertFalse(listed["available"])
        self.assertIn("已移动", listed["issue"])

    def test_corrupt_registry_fails_closed(self) -> None:
        self.registry.parent.mkdir()
        self.registry.write_text(json.dumps({"version": 99, "projects": []}), encoding="utf-8")
        with self.assertRaisesRegex(LocalProjectError, "格式不正确"):
            self.service.list()


if __name__ == "__main__":
    unittest.main()
