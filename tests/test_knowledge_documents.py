from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from office_fixtures import build_pdf, build_pptx, knowledge_response
from sjtu_learning_assistant.document_text import DocumentTextError
from sjtu_learning_assistant.knowledge_compiler import KnowledgeCompilerError, KnowledgeCompilerService, KnowledgeAPIError
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
        return knowledge_response(model, system, content)

    def converter(self, **kw):
        return DocumentCompiler(self.runner, self.cancel, **kw)

    def test_real_local_pdf_renderer_produces_two_bounded_jpegs(self):
        pages = list(page_images(self.path))
        self.assertEqual([1, 2], [number for number, _ in pages])
        self.assertTrue(all(data.startswith(b"\xff\xd8") and len(data) < 4 * 1024**2 for _, data in pages))

    def test_both_app_specs_include_pdfium_license_metadata(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("desktop.spec", "windows.spec"):
            self.assertIn('*copy_metadata("pypdfium2")', (root / "packaging" / filename).read_text(encoding="utf-8"))

    def test_multimodal_pages_and_standard_markdown_provenance(self):
        note = self.converter().compile(self.source, self.path, self.target)
        self.assertEqual(3, len(self.calls))
        self.assertEqual("qwen3.8-27b", self.calls[0][0])
        self.assertEqual(["text", "image_url"], [part["type"] for part in self.calls[0][2]])
        self.assertTrue(self.calls[0][2][1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertIn("原始提取", note.read_text(encoding="utf-8"))
        self.assertIn("讲义脉络", note.read_text(encoding="utf-8"))
        self.assertNotIn("AI 解读（需复核）", note.read_text(encoding="utf-8"))
        self.assertIn("(assets/page-0001.jpg)", note.read_text(encoding="utf-8"))
        page = note.parent / "pages/page-0001.md"
        self.assertIn("source_sha256:", page.read_text(encoding="utf-8"))
        self.assertIn("page: 1", page.read_text(encoding="utf-8"))

    def test_second_run_reuses_pages_without_api_calls(self):
        self.converter().compile(self.source, self.path, self.target)
        resumed = self.converter()
        resumed.compile(self.source, self.path, self.target)
        self.assertEqual((2, 0, 3), (resumed.cached_pages, resumed.calls, len(self.calls)))

    def test_lecture_has_original_text_to_cross_check_visual_interpretation(self):
        self.converter().compile(self.source, self.path, self.target)
        pages = json.loads(self.calls[-1][2])
        self.assertEqual([1, 2], [entry['page'] for entry in pages])
        self.assertTrue(all(entry['source_text'].strip() and entry['text'].strip() for entry in pages))

    def test_lecture_prompt_upgrade_reuses_pages_and_preserves_prior_lecture(self):
        from sjtu_learning_assistant.knowledge_documents import LECTURE_PROMPT
        note = self.converter().compile(self.source, self.path, self.target)
        original_lectures = tuple(note.parent.rglob('pages-*.md'))
        with patch('sjtu_learning_assistant.knowledge_documents.LECTURE_PROMPT', LECTURE_PROMPT + '\n改进核对要求。'):
            resumed = self.converter()
            resumed.compile(self.source, self.path, self.target)
        self.assertEqual((2, 1), (resumed.cached_pages, resumed.calls))
        self.assertTrue(all(path.is_file() for path in original_lectures))
        self.assertEqual(2, len(tuple(note.parent.rglob('pages-*.md'))))

    def test_call_budget_and_restart_checkpoint(self):
        compiler = self.converter(max_calls=1)
        with self.assertRaisesRegex(DocumentTextError, "调用上限"):
            compiler.compile(self.source, self.path, self.target)
        self.assertEqual(1, compiler.calls)
        self.assertFalse(list(self.target.rglob("document.md")))
        resumed = self.converter(max_calls=2)
        resumed.compile(self.source, self.path, self.target)
        self.assertEqual((2, 1), (resumed.calls, resumed.cached_pages))

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
        self.assertEqual((1, 2, 3), (resumed.cached_pages, resumed.calls, len(self.calls)))

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
        self.assertEqual((2, 0, 3), (resumed.cached_pages, resumed.calls, len(self.calls)))

    def test_manual_note_is_preserved(self):
        note = self.converter().compile(self.source, self.path, self.target)
        note.write_text("用户知识笔记", encoding="utf-8")
        with self.assertRaisesRegex(DocumentTextError, "已被修改"):
            self.converter().compile(self.source, self.path, self.target)
        self.assertEqual("用户知识笔记", note.read_text(encoding="utf-8"))
        self.assertEqual(3, len(self.calls))

    def test_incomplete_lecture_is_not_published_and_resumes_from_saved_pages(self):
        def runner(model, system, content):
            if model == 'deepseek-chat':
                return '{"covered_pages":[1],"markdown":"too short"}'
            return knowledge_response(model, system, content)
        converter = DocumentCompiler(runner, self.cancel)
        with self.assertRaisesRegex(DocumentTextError, '覆盖不完整'):
            converter.compile(self.source, self.path, self.target)
        self.assertEqual(2, len(list(self.target.rglob('page-*.md'))))
        self.assertFalse(list(self.target.rglob('document.md')))
        resumed = self.converter()
        note = resumed.compile(self.source, self.path, self.target)
        self.assertEqual((2, 1), (resumed.cached_pages, resumed.calls))
        self.assertIn('编译覆盖：2/2 页', note.read_text(encoding='utf-8'))

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

    def test_transient_api_error_retries_once_and_counts_every_attempt(self):
        attempts = []
        def runner(model, system, content):
            attempts.append(model)
            if len(attempts) == 1:
                raise KnowledgeAPIError('AI 服务暂时不可用。', category='upstream_service')
            return knowledge_response(model, system, content)
        converter = DocumentCompiler(runner, self.cancel)
        with patch.object(self.cancel, 'wait', return_value=False):
            note = converter.compile(self.source, self.path, self.target)
        self.assertTrue(note.is_file())
        self.assertEqual(4, converter.calls)

    def test_authentication_error_is_not_retried_or_hidden(self):
        def runner(*_):
            raise KnowledgeAPIError('AI 认证或访问权限失败。', category='authentication')
        converter = DocumentCompiler(runner, self.cancel)
        with self.assertRaisesRegex(KnowledgeCompilerError, '认证'):
            converter.compile(self.source, self.path, self.target)
        self.assertEqual(1, converter.calls)

    def test_manifest_path_escape_is_rejected(self):
        note = self.converter().compile(self.source, self.path, self.target)
        manifest = note.parent / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["outputs"]["../../outside.md"] = "bad"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
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
        self.assertEqual(3, state["counts"]["document_calls"])
        self.assertEqual(1, len(list(self.target.rglob("document.md"))))

    def test_index_links_resolve_and_markdown_rows_survive_resync(self):
        (self.source / "笔记.md").write_text("# 个人笔记", encoding="utf-8")
        service = KnowledgeCompilerService(ai_runner=self.runner)
        for _ in range(2):
            service.start(str(self.source), str(self.target), "documents")
            service._thread.join(10)
        index = self.target / "99_System/File-Index.md"
        content = index.read_text(encoding="utf-8")
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
