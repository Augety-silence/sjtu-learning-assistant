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
from sjtu_learning_assistant.knowledge_documents import DocumentCompiler, page_images, target_lock, _validate_renderable_pptx, preferred_visual_output, SOURCE_RECHECK_PROMPT, digest, VISION_MODEL
from sjtu_learning_assistant.knowledge_prompts import LECTURE_PROMPT
from sjtu_learning_assistant.knowledge_study import PROMPT_VERSION
from sjtu_learning_assistant.knowledge_sync import main


class KnowledgeDocumentTests(unittest.TestCase):
    def test_only_registered_current_contract_visual_recheck_can_replace_page_evidence(self):
        prefix = 'study/' + digest((LECTURE_PROMPT + PROMPT_VERSION).encode())[:12] + '/'
        suffix = '-visual-' + digest(SOURCE_RECHECK_PROMPT.encode())[:12] + '-page-0018.md'
        expected = prefix + 'chapter-003-001' + suffix
        outputs = {expected: 'verified-hash', 'study/old/chapter' + suffix: 'old-hash', prefix + 'chapter-visual-old-page-0018.md': 'old-prompt'}
        self.assertEqual(expected, preferred_visual_output(outputs, 18))
        self.assertIsNone(preferred_visual_output(outputs, 19))
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
        self.assertEqual(8, len(self.calls))
        self.assertEqual("qwen3.8-27b", self.calls[0][0])
        self.assertEqual(["text", "image_url"], [part["type"] for part in self.calls[0][2]])
        self.assertTrue(self.calls[0][2][1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertNotIn("原始提取", note.read_text(encoding="utf-8"))
        self.assertIn("学习目标与建议顺序", note.read_text(encoding="utf-8"))
        self.assertIn("自测答案", note.read_text(encoding="utf-8"))
        self.assertIn("contract: course-study-v3", note.read_text(encoding="utf-8"))
        self.assertNotIn("AI 解读（需复核）", note.read_text(encoding="utf-8"))
        self.assertIn("(pages/page-0001.md)", note.read_text(encoding="utf-8"))
        page = note.parent / "pages/page-0001.md"
        self.assertIn("source_sha256:", page.read_text(encoding="utf-8"))
        self.assertIn("page: 1", page.read_text(encoding="utf-8"))

    def test_second_run_reuses_pages_without_api_calls(self):
        self.converter().compile(self.source, self.path, self.target)
        resumed = self.converter()
        resumed.compile(self.source, self.path, self.target)
        self.assertEqual((2, 0, 8), (resumed.cached_pages, resumed.calls, len(self.calls)))

    def test_source_conflict_uses_registered_page_image_and_preserves_old_visual(self):
        def runner(model, system, content):
            self.calls.append((model, system, content))
            if '这是来源矛盾的原图复核' in system:
                self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
                return knowledge_response(model, system, content) + '\n原图复核完成'
            if '来源审校员' in system:
                payload = json.loads(content)
                if payload.get('pages') and '原图复核完成' not in payload['pages'][0]['text']:
                    return '{"approved":false,"issues":["第1页：原图与视觉解读矛盾"]}'
            return knowledge_response(model, system, content)
        note = DocumentCompiler(runner, self.cancel).compile(self.source, self.path, self.target)
        self.assertIn('最新图文复核', note.read_text())
        self.assertNotIn('原图复核完成', (note.parent / 'pages/page-0001.md').read_text())
        count = len(self.calls)
        resumed = DocumentCompiler(runner, self.cancel)
        resumed.compile(self.source, self.path, self.target)
        self.assertEqual(0, resumed.calls)
        self.assertEqual(count, len(self.calls))
        checks = json.loads((note.parent / 'review.json').read_text())['checks']
        self.assertIn(1, checks[0]['verified_visual_pages'])

    def test_lecture_has_original_text_to_cross_check_visual_interpretation(self):
        self.converter().compile(self.source, self.path, self.target)
        pages = next(json.loads(content)['pages'] for _, system, content in self.calls if 'covered_pages' in system and type(json.loads(content)) is dict)
        self.assertEqual([1, 2], [entry['page'] for entry in pages])
        self.assertTrue(all(entry['source_text'].strip() and entry['text'].strip() for entry in pages))

    def test_lecture_prompt_upgrade_reuses_pages_and_preserves_prior_lecture(self):
        from sjtu_learning_assistant.knowledge_documents import LECTURE_PROMPT
        note = self.converter().compile(self.source, self.path, self.target)
        original_lectures = tuple(note.parent.rglob('chapter-*.json'))
        with patch('sjtu_learning_assistant.knowledge_documents.LECTURE_PROMPT', LECTURE_PROMPT + '\n改进核对要求。'):
            resumed = self.converter()
            resumed.compile(self.source, self.path, self.target)
        self.assertEqual((2, 6), (resumed.cached_pages, resumed.calls))
        self.assertTrue(all(path.is_file() for path in original_lectures))
        self.assertEqual(4, len(tuple(note.parent.rglob('chapter-*.json'))))

    def test_call_budget_and_restart_checkpoint(self):
        compiler = self.converter(max_calls=1)
        with self.assertRaisesRegex(DocumentTextError, "调用上限"):
            compiler.compile(self.source, self.path, self.target)
        self.assertEqual(1, compiler.calls)
        self.assertFalse(list(self.target.rglob("document.md")))
        resumed = self.converter(max_calls=7)
        resumed.compile(self.source, self.path, self.target)
        self.assertEqual((7, 1), (resumed.calls, resumed.cached_pages))

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
        self.assertEqual((1, 7, 8), (resumed.cached_pages, resumed.calls, len(self.calls)))

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
        self.assertEqual((2, 0, 8), (resumed.cached_pages, resumed.calls, len(self.calls)))

    def test_manual_note_is_preserved(self):
        note = self.converter().compile(self.source, self.path, self.target)
        note.write_text("用户知识笔记", encoding="utf-8")
        with self.assertRaisesRegex(DocumentTextError, "已被修改"):
            self.converter().compile(self.source, self.path, self.target)
        self.assertEqual("用户知识笔记", note.read_text(encoding="utf-8"))
        self.assertEqual(8, len(self.calls))

    def test_incomplete_lecture_is_not_published_and_resumes_from_saved_pages(self):
        def runner(model, system, content):
            if 'chapters 1–80' in system:
                result = json.loads(knowledge_response(model, system, content))
                result['chapters'][0]['pages'] = [1]
                return json.dumps(result)
            return knowledge_response(model, system, content)
        converter = DocumentCompiler(runner, self.cancel)
        with self.assertRaisesRegex(DocumentTextError, '覆盖不完整'):
            converter.compile(self.source, self.path, self.target)
        self.assertEqual(2, len(list(self.target.rglob('page-*.md'))))
        self.assertFalse(list(self.target.rglob('document.md')))
        resumed = self.converter()
        note = resumed.compile(self.source, self.path, self.target)
        self.assertEqual((2, 6), (resumed.cached_pages, resumed.calls))
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
        self.assertEqual(9, converter.calls)

    def test_authentication_error_is_not_retried_or_hidden(self):
        def runner(*_):
            raise KnowledgeAPIError('AI 认证或访问权限失败。', category='authentication')
        converter = DocumentCompiler(runner, self.cancel)
        with self.assertRaisesRegex(KnowledgeCompilerError, '认证'):
            converter.compile(self.source, self.path, self.target)
        self.assertEqual(1, converter.calls)

    def test_text_timeout_uses_one_bounded_qwen_retry_and_records_actual_model(self):
        attempts = []
        def runner(model, *_):
            attempts.append(model)
            if model == 'deepseek-chat':
                raise KnowledgeAPIError('请求超时。', category='timeout')
            return '完整响应'
        converter = DocumentCompiler(runner, self.cancel)
        with patch.object(self.cancel, 'wait', return_value=False):
            self.assertEqual('完整响应', converter._request('deepseek-chat', '作者', '证据'))
        self.assertEqual(['deepseek-chat', VISION_MODEL], attempts)
        self.assertEqual(2, converter.calls)
        self.assertEqual([dict(requested_model='deepseek-chat', used_model=VISION_MODEL, category='timeout')], converter.model_fallbacks)

    def test_auth_and_quota_do_not_switch_models_and_fallback_failure_is_bounded(self):
        for category, expected in (('authentication', ['deepseek-chat']),
                                   ('rate_limit_or_quota', ['deepseek-chat', 'deepseek-chat']),
                                   ('network', ['deepseek-chat', VISION_MODEL])):
            attempts = []
            def runner(model, *_):
                attempts.append(model)
                raise KnowledgeAPIError('服务不可用。', category=category)
            converter = DocumentCompiler(runner, self.cancel)
            with self.subTest(category=category), patch.object(self.cancel, 'wait', return_value=False):
                with self.assertRaises(KnowledgeCompilerError):
                    converter._request('deepseek-chat', '作者', '证据')
                self.assertEqual(expected, attempts)
                self.assertEqual(len(expected), converter.calls)

    def test_cancel_and_explicit_validation_budget_stop_before_fallback(self):
        for cancelled, budget in ((True, 0), (False, 1)):
            attempts = []
            def runner(model, *_):
                attempts.append(model)
                raise KnowledgeAPIError('请求超时。', category='timeout')
            converter = DocumentCompiler(runner, self.cancel, max_calls=budget)
            with self.subTest(cancelled=cancelled), patch.object(self.cancel, 'wait', return_value=cancelled):
                with self.assertRaises(DocumentTextError):
                    converter._request('deepseek-chat', '作者', '证据')
                self.assertEqual(['deepseek-chat'], attempts)
                self.assertEqual([], converter.model_fallbacks)

    def test_fallback_provenance_persists_across_zero_call_restore(self):
        failed = False
        def runner(model, system, content):
            nonlocal failed
            if model == 'deepseek-chat' and not failed:
                failed = True
                raise KnowledgeAPIError('服务端错误。', category='upstream_service')
            return knowledge_response(model, system, content)
        converter = DocumentCompiler(runner, self.cancel)
        with patch.object(self.cancel, 'wait', return_value=False):
            note = converter.compile(self.source, self.path, self.target)
        report = json.loads((note.parent / 'review.json').read_text())
        self.assertEqual(converter.model_fallbacks, report['model_fallbacks'])
        self.assertEqual(1, len(report['model_fallbacks']))
        restored = DocumentCompiler(lambda *_: self.fail('不得重复调用'), self.cancel)
        self.assertEqual(note, restored.compile(self.source, self.path, self.target))
        self.assertEqual(converter.model_fallbacks, restored.model_fallbacks)
        self.assertEqual(0, restored.calls)

    def test_revision_coverage_policy_changes_course_mirror_fingerprint(self):
        import sjtu_learning_assistant.knowledge_documents as module
        before = module.compilation_fingerprint('same-source')
        with patch.object(module, 'REVISION_COVERAGE_POLICY', 'different-policy'):
            self.assertNotEqual(before, module.compilation_fingerprint('same-source'))
        with patch.object(module, 'REVISION_COVERAGE_GUIDANCE', 'different-guidance'):
            self.assertNotEqual(before, module.compilation_fingerprint('same-source'))
        with patch.object(module, 'EXERCISE_REVIEW_GUIDANCE', 'different-exercise-review'):
            self.assertNotEqual(before, module.compilation_fingerprint('same-source'))

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
        self.assertEqual(8, state["counts"]["document_calls"])
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
