from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine

from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge, CompiledKnowledgeError
from sjtu_learning_assistant.agent_runtime.tools import AgentToolError, ReadOnlyToolRegistry
from sjtu_learning_assistant.mcp_server import MCPServer


class CompiledKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.archive = self.root / "archive"
        self.target = self.archive / "Knowledge Markdown"
        self.registry = self.root / "local-projects.json"
        self.relative = "学期/数据库/课件/第一讲.pptx"
        original = self.archive / self.relative
        original.parent.mkdir(parents=True)
        original.write_bytes(b"source")
        self.mirror = self.target / "Courses" / (self.relative + ".md")
        self.mirror.parent.mkdir(parents=True)
        self.body = "# 数据库第一讲\n\n## 第 2 页\n\n关系代数与选择运算\n" + "长正文\n" * 3000
        self.mirror.write_text(self.body, encoding="utf-8")
        self.marker = self.target / "99_System/Course-Sync.json"
        self.marker.parent.mkdir()
        self.marker.write_text(json.dumps({"version": 1, "source_root": str(self.archive), "items": {self.relative: {"text_ready": True, "status": "pending"}}}), encoding="utf-8")
        self.service = CompiledKnowledge(lambda: SimpleNamespace(archive_root=str(self.archive)), self.registry)

    def tearDown(self):
        self.temp.cleanup()

    def test_automatic_catalog_and_unicode_body_search(self):
        sources = self.service.sources()
        self.assertEqual(1, sources["count"])
        result = self.service.search("关系代数")
        self.assertEqual(1, result["count"])
        item = result["items"][0]
        self.assertEqual("pending", item["status"])
        self.assertIn("关系代数", item["snippet"])
        page = self.service.read(item["id"], item["match_offset"])
        self.assertEqual("## 第 2 页", page["section"])
        self.assertIn(self.relative, page["citation"])

    def test_pagination_round_trip_has_no_missing_characters(self):
        doc = self.service.search()["items"][0]
        parts, offset = [], 0
        while True:
            page = self.service.read(doc["id"], offset, 500)
            parts.append(page["text"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        self.assertEqual(self.body, "".join(parts))

    def test_unregistered_markdown_and_revision_duplicates_are_not_catalogued(self):
        (self.target / "Courses/unregistered.md").write_text("not authorized")
        revisions = self.target / "90_Inbox/Documents/revision"
        revisions.mkdir(parents=True)
        (revisions / "index.md").write_text("duplicate")
        self.assertEqual(1, self.service.sources()["count"])

    def test_user_edited_markdown_is_read_but_never_overwritten(self):
        self.mirror.write_text("# 人工补充\n我的知识点", encoding="utf-8")
        self.assertEqual(1, self.service.search("我的知识点")["count"])
        self.assertEqual("# 人工补充\n我的知识点", self.mirror.read_text())

    def test_missing_original_and_new_files_update_without_restart(self):
        (self.archive / self.relative).unlink()
        self.assertEqual(0, self.service.sources()["count"])
        (self.archive / self.relative).write_bytes(b"restored")
        self.assertEqual(1, self.service.sources()["count"])

    @unittest.skipIf(os.name == "nt", "符号链接权限在 Windows 真机单独验收")
    def test_symlinks_and_manifest_traversal_fail_closed(self):
        self.mirror.unlink()
        outside = self.root / "outside.md"
        outside.write_text("private material")
        self.mirror.symlink_to(outside)
        self.assertEqual(0, self.service.sources()["count"])
        self.marker.write_text(json.dumps({"version": 1, "source_root": str(self.archive), "items": {"../../outside": {"text_ready": True}}}))
        self.assertEqual(0, self.service.sources()["count"])

    def test_manual_project_exposes_output_not_source_or_system(self):
        target, source = self.root / "vault", self.root / "materials"
        target.mkdir()
        source.mkdir()
        (target / "知识点.md").write_text("# 手动输出\n函数依赖", encoding="utf-8")
        (source / "原文.md").write_text("must not return")
        (target / "99_System").mkdir()
        (target / "99_System/private.md").write_text("not content")
        self.registry.write_text(json.dumps({"version": 1, "projects": [{"id": "a" * 24, "name": "手动课程", "target_root": str(target), "source_root": str(source)}]}))
        result = self.service.search(source_id="project-" + "a" * 24)
        self.assertEqual(["知识点.md"], [item["title"] for item in result["items"]])
        self.assertEqual(1, self.service.search("函数依赖")["count"])

    def test_bad_arguments_and_nonexistent_document_are_rejected(self):
        for values in ({"offset": True}, {"limit": 0}, {"query": "x" * 161}, {"source_id": "unknown"}):
            with self.assertRaises(CompiledKnowledgeError):
                self.service.search(**values)
        for value in ("../outside", "f" * 32, 123):
            with self.assertRaises(CompiledKnowledgeError):
                self.service.read(value)

    def test_corrupt_index_reports_issue_without_modification(self):
        raw = "[" * 5000
        self.marker.write_text(raw)
        result = self.service.sources()
        self.assertEqual(0, result["count"])
        self.assertTrue(result["issues"])
        self.assertEqual(raw, self.marker.read_text())

    def test_open_path_is_only_registered_document_or_source(self):
        doc = self.service.search()["items"][0]
        self.assertEqual(self.mirror, self.service.open_path(doc["source_id"], doc["id"]))
        with self.assertRaises(CompiledKnowledgeError):
            self.service.open_path("arbitrary", doc["id"])

    def test_mcp_search_and_read_reuse_library_and_redact_credentials(self):
        self.mirror.write_text("# 知识\n关系代数\ntoken=TestSecret123", encoding="utf-8")
        engine = create_engine("sqlite://")
        try:
            tools = ReadOnlyToolRegistry(engine, compiled_knowledge=self.service)
            server = MCPServer(tools)
            names = {item["name"] for item in server._list_tools()["tools"]}
            self.assertTrue({"list_compiled_knowledge", "search_compiled_knowledge", "read_compiled_knowledge"}.issubset(names))
            result = server._call_tool({"name": "search_compiled_knowledge", "arguments": {"query": "关系代数"}})["structuredContent"]
            self.assertEqual(1, result["count"])
            doc = result["items"][0]
            content = server._call_tool({"name": "read_compiled_knowledge", "arguments": {"document_id": doc["id"]}})["structuredContent"]
            self.assertIn("关系代数", content["text"])
            self.assertNotIn("TestSecret123", content["text"])
            self.assertNotIn(str(self.root), json.dumps(content))
            with self.assertRaises(AgentToolError):
                tools.execute("search_compiled_knowledge", {"path": str(self.mirror)}, tuple(tools.names))
        finally:
            engine.dispose()

    def test_list_pagination(self):
        result = self.service.search(limit=1)
        self.assertIsNone(result["next_offset"])
        self.assertEqual([], self.service.search(offset=10)["items"])

    def test_unicode_search_cursor_indexes_original_text(self):
        self.mirror.write_text("İİİ\n知识点", encoding="utf-8")
        doc = self.service.search("知识点")["items"][0]
        self.assertEqual(4, doc["match_offset"])
        self.assertTrue(self.service.read(doc["id"], doc["match_offset"])["text"].startswith("知识点"))

    @unittest.skipIf(os.name == "nt", "FIFO 为 POSIX 专项")
    def test_fifo_does_not_block_or_leak_as_markdown(self):
        self.mirror.unlink()
        os.mkfifo(self.mirror)
        self.assertEqual(0, self.service.sources()["count"])

    def test_bridge_accepts_only_fixed_readonly_fields(self):
        from desktop_app import DesktopBridge
        from tests.test_desktop_app import FakeService
        service = FakeService()
        service.compiled_knowledge_search = self.service.search
        service.compiled_knowledge_read = self.service.read
        bridge = DesktopBridge(service)
        result = bridge.invoke("compiled_knowledge_search", {"query": "关系代数"})
        self.assertTrue(result["ok"])
        doc = result["data"]["items"][0]
        self.assertTrue(bridge.invoke("compiled_knowledge_read", {"document_id": doc["id"]})["ok"])
        for action, payload in (("compiled_knowledge_read", {}), ("compiled_knowledge_search", {"path": "/etc/private"}), ("compiled_knowledge_open", {})):
            self.assertFalse(bridge.invoke(action, payload)["ok"])

    def test_ai_search_result_budget_never_skips_unreturned_documents(self):
        items = [{"id": str(index), "relative_path": "长" * 1024, "snippet": "文" * 300} for index in range(20)]
        library = SimpleNamespace(search=lambda **_args: {"items": items, "count": 100, "next_offset": 30, "truncated": False})
        engine = create_engine("sqlite://")
        try:
            tools = ReadOnlyToolRegistry(engine, compiled_knowledge=library)
            result = tools.execute("search_compiled_knowledge", {"offset": 10}, tuple(tools.names)).result
            self.assertLess(len(result["items"]), 20)
            self.assertEqual(10 + len(result["items"]), result["next_offset"])
            self.assertEqual(100, result["count"])
            self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 12000)
        finally:
            engine.dispose()

    def test_ai_sources_have_pagination_without_changing_ui_catalog(self):
        engine = create_engine("sqlite://")
        try:
            tools = ReadOnlyToolRegistry(engine, compiled_knowledge=self.service)
            result = tools.execute("list_compiled_knowledge", {"offset": 1}, tuple(tools.names)).result
            self.assertEqual([], result["items"])
            self.assertEqual(1, result["document_count"])
            self.assertEqual(1, self.service.sources()["count"])
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
