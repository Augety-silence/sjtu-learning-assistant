from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from sjtu_learning_assistant.agent_runtime import AgentLoop, PresetLoader, ReadOnlyToolRegistry
from sjtu_learning_assistant.agent_runtime.presets import AgentPreset, PresetError, UserSkillStore
from sjtu_learning_assistant.ai_workspace import (
    AIFileChangeStore,
    AIWorkspace,
    AIWorkspaceError,
    validate_workspace_root,
)
from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite


class UserSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "agent_skills.json"
        self.store = UserSkillStore(self.path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_save_edit_delete_and_merge_into_presets(self) -> None:
        skill = self.store.save(
            {
                "name": "论文精读",
                "description": "按段落精读英文论文",
                "instructions": "先给结论，再逐段解释术语。",
                "allowed_tools": ["read_workspace_file", "search_ai_attachments", "read_ai_attachment_text"],
                "reference_attachment_ids": [3, 3, 5],
            },
            updated_at="2026-11-03T10:00:00+08:00",
        )
        self.assertRegex(str(skill["id"]), r"^u-[a-z0-9]{10}$")
        self.assertEqual([3, 5], skill["reference_attachment_ids"])
        loader = PresetLoader(user_skills_path=self.path)
        _default, presets = loader.load()
        preset = presets[str(skill["id"])]
        self.assertEqual(("user", "论文精读", (3, 5)), (preset.source, preset.name, preset.reference_attachment_ids))
        dto = preset.dto()
        self.assertEqual("先给结论，再逐段解释术语。", dto["instructions"])
        # 内置技能不向界面回传提示词。
        self.assertNotIn("instructions", presets["general"].dto())
        self.assertEqual("builtin", presets["general"].dto()["source"])

        edited = self.store.save({**skill, "name": "论文精读 v2"}, updated_at="later")
        self.assertEqual(skill["id"], edited["id"])
        self.assertEqual(1, len(self.store.load()))
        with self.assertRaisesRegex(PresetError, "不存在"):
            self.store.save({**skill, "id": "u-missing00"}, updated_at="x")
        self.assertTrue(self.store.delete(skill["id"]))
        self.assertFalse(self.store.delete(skill["id"]))

    def test_invalid_skills_are_rejected_or_skipped_without_breaking_builtins(self) -> None:
        for bad in (
            {"name": "", "instructions": "x"},
            {"name": "a", "instructions": "x", "allowed_tools": ["drop_database"]},
            {"name": "a", "instructions": "x", "reference_attachment_ids": list(range(1, 20))},
            {"name": "a", "instructions": "x", "id": "general"},
            {"name": "a", "instructions": "x", "extra": 1},
        ):
            with self.assertRaises(PresetError):
                self.store.save(bad, updated_at="x")
        self.path.write_text(
            json.dumps({"version": 1, "skills": [{"id": "u-good0000", "name": "好", "instructions": "ok"}, {"id": "bad"}]}),
            encoding="utf-8",
        )
        self.assertEqual(["u-good0000"], [item["id"] for item in self.store.load()])
        self.path.write_text("{broken", encoding="utf-8")
        _default, presets = PresetLoader(user_skills_path=self.path).load()
        self.assertIn("general", presets)


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / "项目"
        self.root.mkdir()
        self.app_data = base / "app-data"
        self.app_data.mkdir()
        self.changes = self.app_data / "changes"
        (self.root / "notes").mkdir()
        (self.root / "notes" / "第一章.md").write_text("# 第一章\n\n旧的小结\n", encoding="utf-8")
        (self.root / ".secret").write_text("hidden", encoding="utf-8")
        self.workspace = AIWorkspace(self.root, changes_dir=self.changes, protected=[self.app_data])
        self.store = AIFileChangeStore(self.changes, protected=[self.app_data])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_root_validation_rejects_home_overlap_and_symlinks(self) -> None:
        with self.assertRaises(AIWorkspaceError):
            validate_workspace_root(str(Path.home()))
        self.changes.mkdir()
        with self.assertRaisesRegex(AIWorkspaceError, "重叠"):
            validate_workspace_root(str(self.changes), protected=[self.app_data])
        with self.assertRaisesRegex(AIWorkspaceError, "重叠"):
            validate_workspace_root(str(self.app_data.parent), protected=[self.app_data])
        # macOS 临时目录位于 /var（指向 /private/var 的系统别名）下，仍可作为工作区。
        self.assertEqual(self.root, validate_workspace_root(str(self.root)))
        link = Path(self.temporary.name) / "link"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("不支持符号链接")
        with self.assertRaisesRegex(AIWorkspaceError, "符号链接"):
            validate_workspace_root(str(link))

    def test_paths_stay_inside_workspace(self) -> None:
        for bad in ("../outside.md", "/etc/passwd", "C:/x.md", ".secret", "notes/../../x.md", ""):
            with self.assertRaises(AIWorkspaceError):
                self.workspace.resolve_readable(bad)
        listed = self.workspace.list()
        paths = [item["path"] for item in listed["items"]]
        self.assertIn("notes/", paths)
        self.assertIn("notes/第一章.md", paths)
        self.assertNotIn(".secret", paths)
        self.assertNotIn(str(self.root), json.dumps(listed, ensure_ascii=False))

    def test_propose_then_apply_with_backup_and_conflict_detection(self) -> None:
        target = self.root / "notes" / "第一章.md"
        change = self.workspace.propose(
            "notes/第一章.md",
            mode="replace",
            edits=[{"find": "旧的小结", "replace": "新的小结：供需曲线"}],
            summary="更新小结",
        )
        # 提议阶段不写文件。
        self.assertEqual("# 第一章\n\n旧的小结\n", target.read_text(encoding="utf-8"))
        self.assertEqual(("modify", "pending", 1, 1), (change["mode"], change["status"], change["added"], change["removed"]))
        self.assertIn("+新的小结：供需曲线", change["diff"])
        applied = self.store.apply(change["change_id"], current_root=str(self.root))
        self.assertEqual("applied", applied["status"])
        self.assertEqual("# 第一章\n\n新的小结：供需曲线\n", target.read_text(encoding="utf-8"))
        backups = list((self.changes / "backups" / change["change_id"]).iterdir())
        self.assertEqual("# 第一章\n\n旧的小结\n", backups[0].read_text(encoding="utf-8"))
        # 已处理的修改不会重复应用。
        self.assertEqual("applied", self.store.apply(change["change_id"], current_root=str(self.root))["status"])

        second = self.workspace.propose("notes/第一章.md", mode="append", content="\n补充\n")
        target.write_text("用户手动改过\n", encoding="utf-8")
        conflict = self.store.apply(second["change_id"], current_root=str(self.root))
        self.assertEqual("conflict", conflict["status"])
        self.assertEqual("用户手动改过\n", target.read_text(encoding="utf-8"))

    def test_create_append_extend_reject_and_limits(self) -> None:
        created = self.workspace.propose("drafts/提纲.md", mode="create", content="# 提纲\n")
        extended = self.workspace.extend(created["change_id"], "\n## 第二部分\n")
        self.assertEqual(created["change_id"], extended["change_id"])
        self.assertEqual("create", extended["mode"])
        applied = self.store.apply(created["change_id"], current_root=str(self.root))
        self.assertEqual("applied", applied["status"])
        self.assertEqual("# 提纲\n\n## 第二部分\n", (self.root / "drafts" / "提纲.md").read_text(encoding="utf-8"))

        rejected = self.workspace.propose("drafts/提纲.md", mode="overwrite", content="全部重写")
        self.assertEqual("rejected", self.store.reject(rejected["change_id"])["status"])
        self.assertEqual("rejected", self.store.apply(rejected["change_id"], current_root=str(self.root))["status"])
        with self.assertRaisesRegex(AIWorkspaceError, "已存在"):
            self.workspace.propose("drafts/提纲.md", mode="create", content="x")
        with self.assertRaisesRegex(AIWorkspaceError, "恰好出现一次"):
            self.workspace.propose("drafts/提纲.md", mode="replace", edits=[{"find": "#", "replace": "*"}])
        with self.assertRaisesRegex(AIWorkspaceError, "文本类文件"):
            self.workspace.propose("drafts/图.png", mode="create", content="x")
        moved = self.workspace.propose("drafts/新.md", mode="create", content="x")
        self.assertEqual(
            "conflict", self.store.apply(moved["change_id"], current_root=str(self.app_data))["status"]
        )


class WorkspaceToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.engine = create_database_engine(default_sqlite_url(base / "app.db"))
        bootstrap_sqlite(self.engine)
        self.root = base / "ws"
        self.root.mkdir()
        (self.root / "讲义.md").write_text("# 讲义\n" + "正文" * 50, encoding="utf-8")
        self.workspace = AIWorkspace(self.root, changes_dir=base / "changes")

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def test_tools_are_declared_only_with_a_workspace(self) -> None:
        tools = ("list_workspace_files", "read_workspace_file", "propose_file_change", "list_courses")
        without = ReadOnlyToolRegistry(self.engine).definitions(tools)
        self.assertEqual(["list_courses"], [item["function"]["name"] for item in without])
        registry = ReadOnlyToolRegistry(self.engine, workspace=self.workspace)
        self.assertEqual(4, len(registry.definitions(tools)))
        listed = registry.execute("list_workspace_files", {}, tools).result
        self.assertEqual(["讲义.md"], [item["path"] for item in listed["items"]])
        read = registry.execute("read_workspace_file", {"path": "讲义.md"}, tools).result
        self.assertEqual(("ready", 1), (read["status"], read["end_page"]))
        self.assertIn("正文正文", read["text"])

    def test_agent_proposes_long_file_in_appends_and_compacts_arguments(self) -> None:
        class Client:
            supports_streaming = False

            def __init__(inner):
                inner.requests = []
                inner.replies = [
                    {"content": "", "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "propose_file_change", "arguments": json.dumps({"path": "笔记.md", "mode": "create", "content": "第一部分" * 500})}},
                    ]},
                    {"content": "", "tool_calls": [
                        {"id": "c2", "type": "function", "function": {"name": "propose_file_change", "arguments": json.dumps({"path": "笔记.md", "mode": "append", "content": "第二部分" * 500})}},
                    ]},
                    {"content": "已生成待确认的笔记。", "tool_calls": []},
                ]

            def chat_completion(inner, messages, **kwargs):
                inner.requests.append(json.dumps(messages, ensure_ascii=False))
                return inner.replies.pop(0)

        preset = AgentPreset("t", "t", "t", ("propose_file_change",), "写文件")
        registry = ReadOnlyToolRegistry(self.engine, workspace=self.workspace)
        client = Client()
        result = AgentLoop(client, registry, preset).run(
            [{"role": "user", "content": "整理成笔记"}], user_text="整理成笔记", max_tokens=100, temperature=0
        )
        self.assertEqual("completed", result.status)
        change_ids = {run["result_summary"]["change_id"] for run in result.tool_runs}
        self.assertEqual(1, len(change_ids))
        self.assertFalse((self.root / "笔记.md").exists())
        change = AIFileChangeStore(Path(self.temporary.name) / "changes").get(change_ids.pop())
        self.assertEqual(("create", 1), (change["mode"], change["added"]))
        # 已保存的大段正文不再随后续请求重复发送。
        self.assertNotIn("第一部分" * 500, client.requests[-1])


if __name__ == "__main__":
    unittest.main()
