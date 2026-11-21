"""同一学期课件／字幕共用导航，身份冲突与真实读取边界保持隔离。"""
import hashlib
import json
import unittest

from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledgeError
from sjtu_learning_assistant.knowledge_navigation import navigation_scopes
from tests import test_compiled_knowledge as fixtures


class KnowledgeNavigationTests(unittest.TestCase):
    tearDown = fixtures.CompiledKnowledgeTests.tearDown

    def setUp(self):
        fixtures.CompiledKnowledgeTests.setUp(self)
        self.service.metadata = lambda: {self.relative: {"term_id": "term:30", "course_id": "12"}}
        identity = "sjtu-video:12:99"
        directory = self.service.transcript_root / "videos" / hashlib.sha256(identity.encode()).hexdigest()[:32]
        directory.mkdir(parents=True)
        body = "# 当堂老师讲了什么\n关系代数\n\n# 原始字幕\n老师解释选择运算。"
        (directory / "lesson.md").write_text(body, encoding="utf-8")
        (directory / "raw.vtt").write_text("WEBVTT\n", encoding="utf-8")
        (directory / "manifest.json").write_text(json.dumps({"source": {"source_id": identity},
            "course": {"name": "数据库"}, "video": {"title": "第一讲字幕"},
            "lesson_notes": {"status": "completed"}, "artifacts": {"lesson": {
                "sha256": hashlib.sha256(body.encode()).hexdigest()}}}), encoding="utf-8")
        self.service.transcript_metadata = lambda: {"12": {"term_id": "term:30", "term_name": "学期", "course_name": "数据库"}}

    def test_unified_root_and_course_query_include_both_original_documents(self):
        result = self.service.search(limit=1)
        root, = (item for item in result["folders"] if item["path"] == "学期")
        self.assertEqual(2, root["count"])
        self.assertTrue(root["source_id"].startswith("automatic-learning:"))
        scoped = self.service.search(source_id=root["source_id"], folder="学期/数据库")
        self.assertEqual(2, scoped["count"])
        self.assertEqual({"automatic-courses", "automatic-transcripts"}, {d["source_id"] for d in scoped["items"]})
        self.assertEqual(1, self.service.search(source_id="automatic-courses")["count"])
        self.assertEqual(1, self.service.search(source_id="automatic-transcripts")["count"])
        query = self.service.search("选择", source_id=root["source_id"], folder="学期/数据库")
        self.assertEqual(2, query["count"])
        for doc in query["items"]:
            read = self.service.read(doc["id"])
            self.assertEqual(doc["source_id"], read["source_id"])
            self.assertIn("选择", read["text"])
            self.assertEqual(doc["id"], hashlib.sha256((doc["source_id"] + "\0" + doc["relative_path"]).encode()).hexdigest()[:32])
            self.assertTrue(self.service.open_path(doc["source_id"], doc["id"]).is_file())
            with self.assertRaises(CompiledKnowledgeError):
                self.service.open_path(root["source_id"], doc["id"])
        with self.assertRaises(CompiledKnowledgeError):
            self.service.search(source_id="automatic-learning:" + "0" * 24)

    def test_conflicting_real_term_or_course_identity_never_merge(self):
        for metadata in ({"term_id": "term:29", "course_id": "12"}, {"term_id": "term:30", "course_id": "13"}):
            with self.subTest(metadata=metadata):
                self.service.metadata = lambda: {self.relative: metadata}
                roots = [f for f in self.service.search()["folders"] if f["path"] == "学期"]
                self.assertEqual(2, len(roots))
                self.assertEqual({"automatic-courses", "automatic-transcripts"}, {f["source_id"] for f in roots})

    def test_missing_identity_remains_unassigned_but_visible_under_existing_path(self):
        self.service.metadata = lambda: {}
        result = self.service.search()
        root, = (item for item in result["folders"] if item["path"] == "学期")
        self.assertEqual(2, root["count"])
        scoped = self.service.search(source_id=root["source_id"], course_id="12", term_id="term:30")
        self.assertEqual(1, scoped["count"])
        orphan = next(d for d in result["items"] if d["source_id"] == "automatic-courses")
        self.assertNotIn("term_id", orphan)
        self.assertNotIn("course_id", orphan)

    def test_project_sources_and_unknown_only_catalog_keep_original_scopes(self):
        base = dict(folder="学期/数据库/课件", term_id="term:30", course_id="12")
        docs = [dict(base, id="a", source_id="automatic-courses"), dict(base, id="b", source_id="automatic-transcripts"), dict(base, id="c", source_id="project")]
        self.assertEqual({"a", "b"}, set(navigation_scopes(docs)))
        self.assertEqual({}, navigation_scopes([dict(folder="学期/数据库/课件", id="d", source_id="automatic-courses")]))


if __name__ == "__main__":
    unittest.main()
