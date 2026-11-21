from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine

from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge, CompiledKnowledgeError, _safe
from sjtu_learning_assistant.local_settings import validate_archive_root, SettingsError
from sjtu_learning_assistant.path_safety import is_macos_system_alias
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
        self.mirror.write_text(self.body, encoding="utf-8", newline="\n")
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

    def test_search_keeps_later_body_hits_not_only_table_of_contents(self):
        body = "# 目录\n评分细则见第26页\n" + "背景材料\n" * 3000 + "# 第26页\n评分细则：作业20%，期末55%\n"
        self.mirror.write_text(body, encoding="utf-8")
        result = self.service.search("评分")
        item = result["items"][0]
        self.assertEqual("body", item["match_kind"])
        self.assertEqual(2, item["match_count"])
        self.assertEqual([body.index("评分"), body.rindex("评分")], [x["match_offset"] for x in item["passages"]])
        self.assertIn("期末55%", self.service.read(item["id"], item["passages"][-1]["match_offset"])["text"])

    def test_many_matches_keep_bounded_early_and_late_passages(self):
        body = ("评分细则\n" + "空" * 1000 + "\n") * 100
        self.mirror.write_text(body, encoding="utf-8")
        item = self.service.search("评分")["items"][0]
        self.assertEqual(100, item["match_count"])
        self.assertEqual(6, len(item["passages"]))
        self.assertEqual(body.rindex("评分"), item["passages"][-1]["match_offset"])

    def test_filename_hit_is_explicitly_not_body_evidence(self):
        item = self.service.search(".pptx")["items"][0]
        self.assertEqual("metadata", item["match_kind"])
        self.assertEqual([], item["passages"])
        self.assertEqual(0, item["match_count"])

    def test_body_search_course_and_term_do_not_mix_same_name_courses(self):
        self.service.metadata = lambda: {self.relative: {"course_id": "course-1", "term_id": "term:30"}}
        self.assertEqual(1, self.service.search("关系", course_id="course-1", term_id="term:30")["count"])
        self.assertEqual(0, self.service.search("关系", course_id="course-2")["count"])
        self.assertEqual(0, self.service.search("关系", course_id="course-1", term_id="term:29")["count"])

    def test_model_space_separated_synonyms_are_literal_or_terms(self):
        result = self.service.search("课程大纲 关系代数 评分")
        self.assertEqual(1, result["count"])
        self.assertEqual(["课程大纲", "关系代数", "评分"], result["query_terms"])
        self.assertEqual("any_literal_term", result["query_mode"])
        self.assertEqual(0, self.service.search(".* [abc]")["count"])
        with self.assertRaises(CompiledKnowledgeError):
            self.service.search(" ".join(str(i) for i in range(11)))

    def test_late_passages_report_explicit_source_page_not_toc_reference(self):
        body = "# 目录\n评分在第26页\n" + "背景\n" * 1000 + "# 第 26 页\n\n## 原始提取\n评分：期末55%\n"
        self.mirror.write_text(body, encoding="utf-8")
        passages = self.service.search("评分")["items"][0]["passages"]
        self.assertNotIn("source_page", passages[0])
        self.assertEqual(26, passages[-1]["source_page"])

    @unittest.skipUnless(sys.platform == "darwin", "macOS 固定系统目录别名专项")
    def test_macos_default_temp_directory_is_readable_without_resolving_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(str(root), validate_archive_root(str(root)))
            self.assertEqual(root, _safe(root))
            archive = root / "archive"
            source = archive / self.relative
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source")
            mirror = archive / "Knowledge Markdown" / "Courses" / (self.relative + ".md")
            mirror.parent.mkdir(parents=True)
            mirror.write_text("# 完整讲义\n关系代数", encoding="utf-8")
            marker = archive / "Knowledge Markdown/99_System/Course-Sync.json"
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"version": 1, "source_root": str(archive), "items": {self.relative: {"text_ready": True, "status": "completed"}}}), encoding="utf-8")
            service = CompiledKnowledge(lambda: SimpleNamespace(archive_root=str(archive)), root / "projects.json")
            result = service.search("关系代数")
            self.assertEqual(1, result["count"])
            self.assertIn("关系代数", service.read(result["items"][0]["id"])["text"])

    @unittest.skipIf(os.name == "nt", "符号链接权限在 Windows 真机单独验收")
    def test_alias_exception_never_allows_user_parent_or_root_symlinks(self):
        real = self.root / "real-directory"
        real.mkdir()
        link = self.root / "user-alias"
        link.symlink_to(real, target_is_directory=True)
        for root in (link, link / "child"):
            with self.subTest(root=root), self.assertRaises(CompiledKnowledgeError):
                _safe(root)
            with self.assertRaises(SettingsError):
                validate_archive_root(str(root))

    def test_only_exact_macos_alias_with_expected_target_is_recognized(self):
        with patch("sjtu_learning_assistant.path_safety.sys.platform", "darwin"):
            for alias, expected in (("/var", "/private/var"), ("/tmp", "/private/tmp")):
                with self.subTest(alias=alias), patch.object(Path, "resolve", return_value=Path(expected)):
                    self.assertTrue(is_macos_system_alias(Path(alias)))
                    self.assertFalse(is_macos_system_alias(Path(alias) / "user-link"))
                with patch.object(Path, "resolve", return_value=Path("/different-target")):
                    self.assertFalse(is_macos_system_alias(Path(alias)))
            for error in (OSError(), RuntimeError()):
                with patch.object(Path, "resolve", side_effect=error):
                    self.assertFalse(is_macos_system_alias(Path("/var")))
        with patch("sjtu_learning_assistant.path_safety.sys.platform", "linux"), patch.object(Path, "resolve", return_value=Path("/private/var")):
            self.assertFalse(is_macos_system_alias(Path("/var")))

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

    def test_folder_catalog_is_not_limited_to_document_page(self):
        state = json.loads(self.marker.read_text())
        for index in range(25):
            relative = f'学期/数据库/第{index:02}讲/课件.pdf'
            original = self.archive / relative
            original.parent.mkdir(parents=True)
            original.write_bytes(b'source')
            mirror = self.target / 'Courses' / (relative + '.md')
            mirror.parent.mkdir(parents=True)
            mirror.write_text('# 讲义', encoding='utf-8')
            state['items'][relative] = {'text_ready': True, 'status': 'completed'}
        self.marker.write_text(json.dumps(state), encoding='utf-8')
        result = self.service.search(limit=1)
        self.assertEqual(1, len(result['items']))
        self.assertEqual(26, next(x['count'] for x in result['folders'] if x['path'] == '学期/数据库'))
        result = self.service.search(folder='学期/数据库/第24讲')
        self.assertEqual(1, result['count'])
        self.assertEqual('学期/数据库/第24讲', result['items'][0]['folder'])
        with self.assertRaises(CompiledKnowledgeError):
            self.service.search(folder='../private')

    def test_crlf_heading_and_pagination_preserve_original_offsets_and_bytes(self):
        body = "# 数据库\r\n\r\n## 第 2 页\r\nİİİ\r\n关系代数\r\n" + "长正文\r\n" * 200
        payload = body.encode("utf-8")
        self.mirror.write_bytes(payload)
        item = self.service.search("关系代数")["items"][0]
        self.assertEqual(body.index("关系代数"), item["match_offset"])
        self.assertEqual("## 第 2 页", self.service.read(item["id"], item["match_offset"])["section"])
        parts, offset = [], 0
        while True:
            page = self.service.read(item["id"], offset, 500)
            parts.append(page["text"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        self.assertEqual(body, "".join(parts))
        self.assertEqual(payload, self.mirror.read_bytes())

    def test_non_utf8_markdown_is_rejected_without_modification(self):
        payload = b"# Heading\n\xffprivate"
        self.mirror.write_bytes(payload)
        result = self.service.search("private")
        self.assertEqual(0, result["count"])
        self.assertIn("知识文件不是 UTF-8 Markdown。", result["issues"])
        self.assertEqual(payload, self.mirror.read_bytes())

    def test_unregistered_markdown_and_revision_duplicates_are_not_catalogued(self):
        (self.target / "Courses/unregistered.md").write_text("not authorized")
        revisions = self.target / "90_Inbox/Documents/revision"
        revisions.mkdir(parents=True)
        (revisions / "index.md").write_text("duplicate")
        self.assertEqual(1, self.service.sources()["count"])

    def test_user_edited_markdown_is_read_but_never_overwritten(self):
        self.mirror.write_text("# 人工补充\n我的知识点", encoding="utf-8")
        self.assertEqual(1, self.service.search("我的知识点")["count"])
        self.assertEqual("# 人工补充\n我的知识点", self.mirror.read_text(encoding="utf-8"))

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
        for values in ({"offset": True}, {"limit": 0}, {"query": "x" * 161}, {"query": "| , ;"}, {"source_id": "unknown"}):
            with self.assertRaises(CompiledKnowledgeError):
                self.service.search(**values)
        for value in ("../outside", "f" * 32, 123):
            with self.assertRaises(CompiledKnowledgeError):
                self.service.read(value)

    def test_project_document_pages_and_old_revisions_are_not_flattened(self):
        target = self.root / 'vault'
        for revision, contract in [('old', 'slide-md-v1'), ('new', 'course-lecture-v2')]:
            directory = target / '90_Inbox/Documents/source-id' / revision
            (directory / 'pages').mkdir(parents=True)
            (directory / 'document.md').write_text('# 讲义', encoding='utf-8')
            (directory / 'pages/page-0001.md').write_text('# 单页', encoding='utf-8')
            (directory / 'manifest.json').write_text(json.dumps({'source': '学期/课程/第一讲.pptx', 'status': 'completed', 'contract': contract}), encoding='utf-8')
        self.registry.write_text(json.dumps({'version': 1, 'projects': [{'id': 'a' * 24, 'name': '项目', 'target_root': str(target)}]}), encoding='utf-8')
        result = self.service.search(source_id='project-' + 'a' * 24)
        self.assertEqual(1, result['count'])
        self.assertEqual('学期/课程', result['items'][0]['folder'])
        self.assertIn('/new/', result['items'][0]['relative_path'])

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
        self.mirror.write_text("İİİ\n知识点", encoding="utf-8", newline="\n")
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
