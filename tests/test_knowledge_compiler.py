from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sjtu_learning_assistant.knowledge_compiler import (
    KnowledgeCompilerError,
    KnowledgeCompilerService,
)


class KnowledgeCompilerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.target = self.root / "vault"
        self.source.mkdir()
        self.target.mkdir()
        (self.source / "数据库").mkdir()
        (self.source / "数据库" / "第一讲.md").write_text(
            "# 关系模型\n\n函数依赖决定规范化。\n\n![[关系图.png]]\n",
            encoding="utf-8",
        )
        self.calls: list[tuple[str, str, str]] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runner(self, model: str, system: str, content: str) -> str:
        self.calls.append((model, system, content))
        if "原子概念" in system:
            return (
                "## 概念：函数依赖\n"
                "一句话定义。\n\n## 直觉\n用于判断属性决定关系。\n"
            )
        return "# 重构结果\n\n只依据原始材料生成。"

    def test_inspect_reports_markdown_courses_and_images(self) -> None:
        service = KnowledgeCompilerService(ai_runner=self.runner)
        result = service.inspect(str(self.source))
        self.assertEqual(1, result["markdown_files"])
        self.assertEqual(1, result["image_references"])
        self.assertEqual(["数据库"], result["courses"])

    def test_foundation_job_preserves_source_and_builds_vault(self) -> None:
        original = (self.source / "数据库" / "第一讲.md").read_bytes()
        service = KnowledgeCompilerService(ai_runner=self.runner)
        started = service.start(str(self.source), str(self.target), "foundation")
        self.assertEqual("started", started["status"])
        assert service._thread is not None
        service._thread.join(timeout=5)

        status = service.status(str(self.target))
        self.assertEqual("completed", status["status"])
        self.assertEqual(3, status["total_phases"])
        self.assertEqual(1, status["counts"]["markdown_files"])
        self.assertGreaterEqual(status["counts"]["generated_notes"], 3)
        self.assertNotIn("source_root", status)
        self.assertNotIn("target_root", status)
        self.assertEqual(
            original,
            (self.source / "数据库" / "第一讲.md").read_bytes(),
        )
        self.assertTrue((self.target / "90_Inbox" / "数据库" / "第一讲.md").is_file())
        self.assertTrue((self.target / "99_System" / "File-Index.md").is_file())
        self.assertTrue((self.target / "02_Concepts" / "函数依赖.md").is_file())
        self.assertTrue(any(call[0] == "deepseek-chat" for call in self.calls))

    def test_rejects_nonempty_unmanaged_target_and_nested_roots(self) -> None:
        service = KnowledgeCompilerService(ai_runner=self.runner)
        (self.target / "personal.md").write_text("do not overwrite", encoding="utf-8")
        with self.assertRaisesRegex(KnowledgeCompilerError, "输出目录必须为空"):
            service.start(str(self.source), str(self.target), "foundation")

        nested = self.source / "output"
        nested.mkdir()
        with self.assertRaisesRegex(KnowledgeCompilerError, "彼此独立"):
            service.start(str(self.source), str(nested), "foundation")

    def test_persisted_running_state_is_reported_as_interrupted_after_restart(self) -> None:
        service = KnowledgeCompilerService(ai_runner=self.runner)
        service.start(str(self.source), str(self.target), "foundation")
        assert service._thread is not None
        service._thread.join(timeout=5)
        state_path = self.target / "99_System" / "Compiler-State.json"
        state = state_path.read_text(encoding="utf-8").replace(
            '"status": "completed"', '"status": "running"', 1
        )
        state_path.write_text(state, encoding="utf-8")

        restored = KnowledgeCompilerService(ai_runner=self.runner).status(str(self.target))
        self.assertEqual("interrupted", restored["status"])
        self.assertIn("应用退出", restored["error"])


if __name__ == "__main__":
    unittest.main()
