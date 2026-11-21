from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge, CompiledKnowledgeError
from sjtu_learning_assistant.knowledge_wiki import CourseWiki, validate_wiki


class KnowledgeWikiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.archive = self.root / "archive"
        self.target = self.archive / "Knowledge Markdown"
        self.state = {"version": 1, "source_root": str(self.archive), "items": {}}
        self.marker = self.target / "99_System/Course-Sync.json"
        self.marker.parent.mkdir(parents=True)
        self.add("秋/数据库/课件/一.pdf", "# 关系代数\n\n选择运算对行按条件过滤。")
        self.add("秋/数据库/课件/二.pdf", "# SQL\n\nWHERE 条件对应选择运算。")
        self.add("秋/数据库/课件/未完成.pdf", "# 待处理原文", "pending")
        self.add("春/数据库/课件/一.pdf", "# 事务\n事务具有原子性。")
        self.knowledge = CompiledKnowledge(lambda: SimpleNamespace(archive_root=str(self.archive)), self.root / "projects.json")
        self.calls = []
        self.wiki = CourseWiki(self.knowledge, self.runner)
        self.group = next(group for group in self.wiki.snapshot() if group["term"] == "秋")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, relative, text, status="completed"):
        original = self.archive / relative
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b"source")
        mirror = self.target / "Courses" / (relative + ".md")
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(text, encoding="utf-8")
        self.state["items"][relative] = {"text_ready": True, "status": status, "contract": "course-summary-v1"}
        self.marker.write_text(json.dumps(self.state), encoding="utf-8")

    def runner(self, model, system, content):
        data = json.loads(content)
        ids = [source["id"] for source in data["sources"]]
        self.calls.append((model, data))
        if "draft" in data:
            return json.dumps({"approved": True, "covered_documents": ids, "issues": []})
        return json.dumps({"title": "数据库知识地图", "overview": "过滤与查询的关系 " + f"[source:{ids[0]}]",
            "covered_documents": ids,
            "concepts": [{"name": "选择运算", "aliases": ["行过滤", "Selection"],
                          "markdown": "选择运算与 WHERE 条件的联系。 " + " ".join(f"[source:{identity}]" for identity in ids),
                          "source_ids": ids}], "relations": []}, ensure_ascii=False)

    def test_build_cross_document_concept_and_read_full_page_without_exact_query(self):
        before = {str(path): path.read_bytes() for path in (self.target / "Courses").rglob("*.md")}
        result = self.wiki.refresh(only=self.group["id"])
        self.assertEqual(1, result["built"])
        self.assertEqual(2, len(self.calls))
        self.assertEqual(2, len(self.calls[0][1]["sources"]))
        index = next(item for item in self.wiki.browse()["items"] if item["term"] == "秋")
        self.assertEqual(3, index["document_count"])
        self.assertEqual(2, index["compiled_count"])
        self.assertEqual(["行过滤", "Selection"], index["topics"][0]["aliases"])
        map_page = self.wiki.browse(page_id=index["page_id"])
        self.assertEqual(index["topics"][0]["id"], map_page["topics"][0]["id"])
        self.assertEqual(2, len(map_page["topics"][0]["source_ids"]))
        concept = self.wiki.browse(page_id=index["topics"][0]["id"])
        self.assertIn("WHERE", concept["text"])
        self.assertEqual(2, len(concept["source_ids"]))
        for identity in concept["source_ids"]:
            self.assertTrue(self.knowledge.read(identity)["text"])
        self.assertEqual(before, {str(path): path.read_bytes() for path in (self.target / "Courses").rglob("*.md")})
        self.assertEqual(1, self.wiki.refresh(only=self.group["id"])["cached"])
        self.assertEqual(2, len(self.calls))

    def test_previous_page_becomes_unavailable_when_source_changes_or_disappears(self):
        self.wiki.refresh(only=self.group["id"])
        page_id = next(x for x in self.wiki.browse()["items"] if x["term"] == "秋")["page_id"]
        path = self.target / "Courses/秋/数据库/课件/一.pdf.md"
        path.write_text("# 新的定义\n不能混用旧知识。", encoding="utf-8")
        with self.assertRaises(CompiledKnowledgeError):
            self.wiki.browse(page_id=page_id)
        self.assertEqual(1, self.wiki.refresh(only=self.group["id"])["built"])
        self.assertGreater(len(list((self.target / "Wiki").rglob("index.md"))), 1)
        (self.archive / "秋/数据库/课件/一.pdf").unlink()
        self.assertEqual("pending", next(x for x in self.wiki.browse()["items"] if x["term"] == "秋")["status"])

    def test_manual_wiki_edits_are_not_overwritten_and_not_served_as_verified(self):
        self.wiki.refresh(only=self.group["id"])
        path = next((self.target / "Wiki").rglob("index.md"))
        path.write_text("# 我的人工笔记", encoding="utf-8")
        self.assertEqual(1, self.wiki.refresh(only=self.group["id"])["failed"])
        self.assertEqual(2, len(self.calls))
        self.assertEqual("# 我的人工笔记", path.read_text(encoding="utf-8"))
        self.assertEqual("pending", next(x for x in self.wiki.browse()["items"] if x["term"] == "秋")["status"])

    def test_rejected_review_does_not_publish_and_same_day_failure_does_not_loop(self):
        def rejected(model, system, content):
            if "draft" in json.loads(content):
                return json.dumps({"approved": False, "covered_documents": [], "issues": ["证据不足"]})
            return self.runner(model, system, content)
        self.wiki.runner = rejected
        self.assertEqual(1, self.wiki.refresh(only=self.group["id"])["failed"])
        self.assertFalse((self.target / "Wiki").exists())
        self.wiki.refresh(only=self.group["id"])
        self.assertEqual(1, len(self.calls))

    def test_cancelled_draft_never_publishes_or_requests_review(self):
        cancelled = False
        def runner(*args):
            nonlocal cancelled
            cancelled = True
            return self.runner(*args)
        self.wiki.runner = runner
        self.wiki.refresh(only=self.group["id"], cancelled=lambda: cancelled)
        self.assertFalse((self.target / "Wiki").exists())
        self.assertEqual(1, len(self.calls))

    def test_unknown_citations_missing_coverage_and_unsafe_labels_fail_closed(self):
        ids = {item["id"] for item in self.group["ready"]}
        draft = json.loads(self.runner("deepseek-chat", "", json.dumps({"sources": self.group["ready"]})))
        for mutation in ("unknown", "missing", "unsafe", "unhashable"):
            bad = copy.deepcopy(draft)
            if mutation == "unknown":
                bad["concepts"][0]["markdown"] = "未知 [source:" + "f" * 32 + "]"
            elif mutation == "missing":
                bad["concepts"][0]["source_ids"] = []
            elif mutation == "unsafe":
                bad["concepts"][0]["name"] = "[坏链接](file)"
            else:
                bad["relations"] = [{"from": [], "to": "选择运算", "reason": "不安全"}]
            with self.subTest(mutation=mutation), self.assertRaises(CompiledKnowledgeError):
                validate_wiki(bad, ids)

    def test_wiki_scope_never_combines_same_named_courses_in_different_terms(self):
        self.wiki.refresh()
        groups = self.wiki.browse()["items"]
        self.assertEqual(2, len(groups))
        self.assertEqual({1, 2}, {group["compiled_count"] for group in groups})
        for model, request in self.calls:
            if "draft" not in request:
                self.assertEqual(1, len({source["citation"].split("/")[1] for source in request["sources"]}))

    def test_wiki_rejects_arbitrary_path_and_invalid_scope(self):
        for arguments in ({"page_id": "../../etc/private"}, {"course_id": []}, {"offset": True}):
            with self.subTest(arguments=arguments), self.assertRaises(CompiledKnowledgeError):
                self.wiki.browse(**arguments)

    def test_corrupt_source_index_does_not_leave_readable_unverified_pages(self):
        self.wiki.refresh(only=self.group["id"])
        state = self.wiki._state()
        page = state["courses"][self.group["id"]]["pages"][0]
        identity = page["id"]
        page["source_ids"] = ["f" * 32]
        self.wiki._persist(state)
        course = next(item for item in self.wiki.browse()["items"] if item["id"] == self.group["id"])
        self.assertIsNone(course["page_id"])
        self.assertEqual([], course["topics"])
        with self.assertRaises(CompiledKnowledgeError):
            self.wiki.browse(page_id=identity)

    def test_isolated_api_validation_storage_links_to_real_readonly_md(self):
        from urllib.parse import unquote
        import re
        output = self.root / "isolated-wiki"
        wiki = CourseWiki(self.knowledge, self.runner, output_root=output)
        self.assertEqual(1, wiki.refresh(only=self.group["id"])["built"])
        self.assertFalse((self.target / "Wiki").exists())
        for note in output.rglob("*.md"):
            body = note.read_text(encoding="utf-8")
            for relative in re.findall(r"\]\(([^)]+\.md)\)", body):
                self.assertTrue((note.parent / unquote(relative)).resolve().is_file())


if __name__ == "__main__":
    unittest.main()
