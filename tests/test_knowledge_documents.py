from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from office_fixtures import build_pdf, build_pptx
from sjtu_learning_assistant.document_text import DocumentTextError
from sjtu_learning_assistant.knowledge_compiler import KnowledgeCompilerError, KnowledgeCompilerService
from sjtu_learning_assistant.knowledge_documents import DocumentCompiler, page_images, target_lock, _validate_renderable_pptx
from sjtu_learning_assistant.knowledge_sync import main


class KnowledgeDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source, self.target = self.root / "source", self.root / "vault"
        self.source.mkdir()
        self.target.mkdir()
        self.path = build_pdf(self.source / "课件.PDF")
        self.calls = []
        self.cancel = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, model, system, content):
        self.calls.append((model, system, content))
        return "## 知识点\n仅根据本页解读。"

    def converter(self, **kw):
        return DocumentCompiler(self.runner, self.cancel, **kw)

    def test_real_local_pdf_renderer_produces_two_bounded_jpegs(self):
        pages = list(page_images(self.path))
        self.assertEqual([1, 2], [number for number, _ in pages])
        self.assertTrue(all(data.startswith(b"\xff\xd8") and len(data) < 4 * 1024**2 for _, data in pages))

    def test_multimodal_pages_and_standard_markdown_provenance(self):
        note = self.converter().compile(self.source, self.path, self.target)
        self.assertEqual(2, len(self.calls))
        self.assertEqual("qwen3.8-27b", self.calls[0][0])
        self.assertEqual(["text", "image_url"], [part["type"] for part in self.calls[0][2]])
        self.assertIn("原始提取", note.read_text())
        self.assertIn("AI 解读（需复核）", note.read_text())
        self.assertIn("(assets/page-0001.jpg)", note.read_text())
        page = note.parent / "pages/page-0001.md"
        self.assertIn("source_sha256:", page.read_text())
        self.assertIn("page: 1", page.read_text())

    def test_second_run_reuses_pages_without_api_calls(self):
        self.converter().compile(self.source, self.path, self.target)
        resumed = self.converter()
        resumed.compile(self.source, self.path, self.target)
        self.assertEqual((2, 0, 2), (resumed.cached_pages, resumed.calls, len(self.calls)))

    def test_call_budget_and_restart_checkpoint(self):
        compiler = self.converter(max_calls=1)
        with self.assertRaisesRegex(DocumentTextError, "调用上限"):
            compiler.compile(self.source, self.path, self.target)
        self.assertEqual(1, compiler.calls)
        self.assertFalse(list(self.target.rglob("document.md")))
        resumed = self.converter(max_calls=1)
        resumed.compile(self.source, self.path, self.target)
        self.assertEqual((1, 1), (resumed.calls, resumed.cached_pages))

    def test_crash_between_page_files_resumes_without_repeated_api(self):
        replace = os.replace
        def fail_page(source, destination):
            if Path(destination).name == "page-0001.md":
                raise OSError("simulated disk failure")
            return replace(source, destination)
        with patch("sjtu_learning_assistant.knowledge_documents.os.replace", side_effect=fail_page):
            with self.assertRaises(DocumentTextError):
                self.converter().compile(self.source, self.path, self.target)
        self.assertEqual(1, len(self.calls))
        resumed = self.converter()
        resumed.compile(self.source, self.path, self.target)
        self.assertEqual((1, 1, 2), (resumed.cached_pages, resumed.calls, len(self.calls)))

    def test_crash_before_document_replace_recovers_offline(self):
        replace = os.replace
        def fail_document(source, destination):
            if Path(destination).name == "document.md":
                raise OSError("simulated disk failure")
            return replace(source, destination)
        with patch("sjtu_learning_assistant.knowledge_documents.os.replace", side_effect=fail_document):
            with self.assertRaises(DocumentTextError):
                self.converter().compile(self.source, self.path, self.target)
        resumed = self.converter()
        note = resumed.compile(self.source, self.path, self.target)
        self.assertTrue(note.is_file())
        self.assertEqual((2, 0, 2), (resumed.cached_pages, resumed.calls, len(self.calls)))

    def test_manual_note_is_preserved(self):
        note = self.converter().compile(self.source, self.path, self.target)
        note.write_text("用户知识笔记", encoding="utf-8")
        with self.assertRaisesRegex(DocumentTextError, "已被修改"):
            self.converter().compile(self.source, self.path, self.target)
        self.assertEqual("用户知识笔记", note.read_text())
        self.assertEqual(2, len(self.calls))

    def test_changed_source_keeps_old_revision(self):
        first = self.converter().compile(self.source, self.path, self.target)
        build_pdf(self.path, ("Changed",))
        second = self.converter().compile(self.source, self.path, self.target)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())

    def test_cancel_during_api_does_not_commit_page(self):
        def runner(*args):
            self.cancel.set()
            return "结果"
        compiler = DocumentCompiler(runner, self.cancel)
        with self.assertRaisesRegex(DocumentTextError, "取消"):
            compiler.compile(self.source, self.path, self.target)
        self.assertFalse(list(self.target.rglob("page-0001.md")))

    def test_api_failure_is_bounded_and_not_cached(self):
        def runner(*args):
            raise RuntimeError("do not expose confidential request")
        compiler = DocumentCompiler(runner, self.cancel)
        with self.assertRaises(DocumentTextError) as context:
            compiler.compile(self.source, self.path, self.target)
        self.assertNotIn("confidential", str(context.exception))
        self.assertEqual(1, compiler.calls)
        self.assertFalse(list(self.target.rglob("page-0001.md")))

    def test_manifest_path_escape_is_rejected(self):
        note = self.converter().compile(self.source, self.path, self.target)
        manifest = note.parent / "manifest.json"
        payload = json.loads(manifest.read_text())
        payload["outputs"]["../../outside.md"] = "bad"
        manifest.write_text(json.dumps(payload))
        with self.assertRaisesRegex(DocumentTextError, "路径无效"):
            self.converter().compile(self.source, self.path, self.target)

    def test_target_symlink_does_not_write_outside(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / "90_Inbox").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(KnowledgeCompilerError):
            self.converter().compile(self.source, self.path, self.target)
        self.assertEqual([], list(outside.iterdir()))

    def test_pptx_external_resources_blocked_before_office(self):
        path = build_pptx(self.source / "external.pptx", extra={
            "ppt/slides/_rels/slide2.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rExt" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="https://example.invalid/image.png" TargetMode="External"/></Relationships>'
        })
        with self.assertRaisesRegex(DocumentTextError, "外部资源"):
            _validate_renderable_pptx(path)

    def test_lock_rejects_concurrent_writer_and_releases(self):
        with target_lock(self.target):
            with self.assertRaisesRegex(KnowledgeCompilerError, "另一个进程"):
                with target_lock(self.target):
                    pass
        with target_lock(self.target):
            pass

    def test_documents_mode_accepts_pdf_and_isolates_unsupported_ppt(self):
        (self.source / "legacy.ppt").write_bytes(b"unsupported")
        service = KnowledgeCompilerService(ai_runner=self.runner)
        self.assertEqual(2, service.inspect(str(self.source))["document_files"])
        service.start(str(self.source), str(self.target), "documents")
        service._thread.join(10)
        state = service.status()
        self.assertEqual("completed_with_warnings", state["status"])
        self.assertEqual(1, state["counts"]["warnings"])
        self.assertEqual(2, state["counts"]["document_calls"])
        self.assertEqual(1, len(list(self.target.rglob("document.md"))))

    def test_index_links_resolve_and_markdown_rows_survive_resync(self):
        (self.source / "笔记.md").write_text("# 个人笔记", encoding="utf-8")
        service = KnowledgeCompilerService(ai_runner=self.runner)
        for _ in range(2):
            service.start(str(self.source), str(self.target), "documents")
            service._thread.join(10)
        index = self.target / "99_System/File-Index.md"
        content = index.read_text()
        self.assertIn("笔记.md", content)
        import re
        link = re.search(r"\]\(([^)]+document.md)\)", content).group(1)
        self.assertTrue((index.parent / link).is_file())

    def test_cli_default_is_readonly_and_never_loads_key(self):
        with patch("sjtu_learning_assistant.knowledge_sync.get_ai_api_key") as key:
            self.assertEqual(0, main(["--source", str(self.source), "--target", str(self.target)]))
        key.assert_not_called()
        self.assertEqual([], list(self.target.iterdir()))

    def test_cli_watch_budget_counts_failures_across_rounds(self):
        with patch("sjtu_learning_assistant.knowledge_sync.get_ai_api_key", return_value="test"), patch("sjtu_learning_assistant.knowledge_sync.SettingsStore") as settings, patch("sjtu_learning_assistant.knowledge_sync.OpenAIClassificationClient") as client:
            settings.return_value.load.return_value.ai_enabled = True
            client.return_value.chat_completion.side_effect = RuntimeError("request failed")
            self.assertEqual(1, main(["--source", str(self.source), "--target", str(self.target), "--allow-api", "--watch", "30", "--max-calls", "1"]))
            self.assertEqual(1, client.return_value.chat_completion.call_count)


if __name__ == "__main__":
    unittest.main()
