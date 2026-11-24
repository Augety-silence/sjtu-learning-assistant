"""单个慢页不挡后续页；真实请求、失败响应和切片预算均有边界。"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from office_fixtures import build_pdf, knowledge_response
from sjtu_learning_assistant.ai_runtime import cancellation_scope, current_cancellation_check
from sjtu_learning_assistant.document_text import DocumentTextError
from sjtu_learning_assistant.knowledge_compiler import KnowledgeAPIError, KnowledgeCompilerError
from sjtu_learning_assistant.knowledge_documents import (
    CompilationDeferred, DocumentCompiler, VISION_MODEL, _rolling_page_results,
)
from sjtu_learning_assistant.knowledge_prompts import PAGE_PROMPT
from sjtu_learning_assistant.course_markdown_sync import CourseMarkdownSync
from sjtu_learning_assistant.local_settings import LocalSettings


class RollingPagesTests(unittest.TestCase):
    def test_finished_page_refills_window_while_first_is_slow(self):
        slow_release, third_started = threading.Event(), threading.Event()
        active, maximum, guard, results = 0, 0, threading.Lock(), []
        def build(number, data):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            try:
                if number == 1:
                    if not slow_release.wait(3):
                        raise AssertionError('慢页没有获准完成')
                if number == 3:
                    third_started.set()
                return number, data
            finally:
                with guard:
                    active -= 1
        thread = threading.Thread(target=lambda: results.extend(_rolling_page_results(
            ((n, str(n)) for n in range(1, 5)), build, 2)))
        thread.start()
        try:
            self.assertTrue(third_started.wait(2), '第二页完成后必须立即派发第三页')
        finally:
            slow_release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual({1, 2, 3, 4}, {n for n, _ in results})
        self.assertLessEqual(maximum, 2)

    def test_real_failure_is_not_hidden_by_slice_deferral(self):
        deferred, release = threading.Event(), threading.Event()
        saved = []
        def build(number, data):
            if number == 1:
                deferred.set()
                raise CompilationDeferred('额度切片')
            deferred.wait(2)
            saved.append(number)
            raise KnowledgeAPIError('真实超时', category='timeout')
        with self.assertRaisesRegex(KnowledgeAPIError, '真实超时'):
            list(_rolling_page_results(((1, None), (2, None)), build, 2))
        self.assertEqual([2], saved)

    def test_cancellation_scope_is_propagated_to_page_workers(self):
        stop = threading.Event()
        stop.set()
        def build(number, data):
            return number, current_cancellation_check()()
        with cancellation_scope(stop.is_set):
            results = list(_rolling_page_results(((1, None), (2, None)), build, 2))
        self.assertTrue(all(cancelled for _, cancelled in results))


class PageCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.source = root / 'source'
        self.target = root / 'target'
        self.source.mkdir()
        self.target.mkdir()
        self.path = build_pdf(self.source / 'slides.pdf', ('First page', 'Second page'))
        self.cancel = threading.Event()

    def test_invalid_paid_page_is_saved_and_restart_never_regenerates(self):
        request = Mock(return_value='## 页面主题\n只有一栏目，不能当完整页。')
        for _ in range(2):
            with self.assertRaisesRegex(DocumentTextError, '结构不完整'):
                DocumentCompiler(request, self.cancel).compile(self.source, self.path, self.target)
        self.assertEqual(1, request.call_count)
        responses = list(self.target.rglob('responses/*.json'))
        self.assertEqual(1, len(responses))
        response = json.loads(responses[0].read_text())
        self.assertEqual(request.return_value, response['response'])
        manifest = json.loads((responses[0].parent.parent / 'manifest.json').read_text())
        self.assertFalse(manifest['pages'])
        self.assertIn(responses[0].relative_to(responses[0].parent.parent).as_posix(), manifest['outputs'])

    def test_parallel_failure_keeps_other_finished_page_and_raw_responses(self):
        started = threading.Barrier(2)
        def runner(model, system, content):
            if system == PAGE_PROMPT:
                number = int(content[0]['text'].split()[1])
                started.wait(3)
                if number == 1:
                    return '## 页面主题\n第一页面真实缺少完整栏目。'
            return knowledge_response(model, system, content)
        with self.assertRaisesRegex(DocumentTextError, '结构不完整'):
            DocumentCompiler(runner, self.cancel, auto_recovery=True, parallel_batches=3).compile(
                self.source, self.path, self.target)
        manifest_path = next(self.target.rglob('manifest.json'))
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual({'2'}, set(manifest['pages']))
        self.assertEqual(2, len(list(self.target.rglob('responses/*.json'))))
        self.assertFalse(list(self.target.rglob('document.md')))

    def test_cancel_after_response_retains_paid_raw_without_publishing_page(self):
        def runner(*args):
            self.cancel.set()
            return knowledge_response(*args)
        with self.assertRaisesRegex(DocumentTextError, '取消'):
            DocumentCompiler(runner, self.cancel).compile(self.source, self.path, self.target)
        self.assertEqual(1, len(list(self.target.rglob('responses/*.json'))))
        self.assertFalse(list(self.target.rglob('pages/*.md')))

    def test_auto_page_timeout_uses_one_request_then_existing_cooldown(self):
        request = Mock(side_effect=KnowledgeAPIError('请求超时', category='timeout'))
        compiler = DocumentCompiler(request, self.cancel, auto_recovery=True)
        with patch.object(self.cancel, 'wait') as waiting:
            with self.assertRaisesRegex(KnowledgeCompilerError, '请求超时'):
                compiler._request(VISION_MODEL, PAGE_PROMPT, [])
            waiting.assert_not_called()
        self.assertEqual(1, request.call_count)

    def test_parallel_slice_is_bounded_and_resumes_all_saved_pages(self):
        request = Mock(side_effect=knowledge_response)
        compiler = DocumentCompiler(request, self.cancel, auto_recovery=True, parallel_batches=3, slice_calls=1)
        with self.assertRaises(CompilationDeferred):
            compiler.compile(self.source, self.path, self.target)
        self.assertEqual(1, request.call_count)
        self.assertEqual(1, len(list(self.target.rglob('pages/*.md'))))
        note = DocumentCompiler(request, self.cancel, auto_recovery=True, parallel_batches=3).compile(
            self.source, self.path, self.target)
        self.assertTrue(note.is_file())
        self.assertEqual(4, request.call_count)

    def test_version_wakeup_cannot_refill_exhausted_failure_count(self):
        settings = LocalSettings(archive_root=str(self.source), ai_enabled=True, ai_key_saved=True,
                                 course_markdown_enabled=True)
        request = Mock(side_effect=RuntimeError('initial failure'))
        worker = CourseMarkdownSync(lambda: settings, lambda: [self.path], request, auto_recovery=True)
        self.addCleanup(worker.close)
        worker.run_once()
        marker = self.source / 'Knowledge Markdown/99_System/Course-Sync.json'
        state = json.loads(marker.read_text())
        item = next(iter(state['items'].values()))
        item.update(attempts=8, auto_paused=False, error='DashboardError', auto_recovery_version='old')
        marker.write_text(json.dumps(state))
        before = request.call_count
        worker.run_once()
        after = next(iter(json.loads(marker.read_text())['items'].values()))
        self.assertEqual(before, request.call_count)
        self.assertEqual(8, after['attempts'])
        self.assertTrue(after['auto_paused'])

    def test_tail_expands_slice_but_many_documents_keep_two_calls(self):
        settings = LocalSettings(archive_root=str(self.source), ai_enabled=True, ai_key_saved=True,
                                 course_markdown_enabled=True)
        paths, slices = [self.path], []
        class CaptureCompiler:
            def __init__(inner, runner, cancel, **kwargs):
                slices.append(kwargs['slice_calls'])
            def compile(inner, source, path, target):
                raise CompilationDeferred('保留检查点')
        worker = CourseMarkdownSync(lambda: settings, lambda: paths, Mock(), auto_recovery=True,
                                    compiler_factory=CaptureCompiler, slice_calls=2, workers=6,
                                    max_documents_per_cycle=10)
        self.addCleanup(worker.close)
        worker.run_once()
        self.assertEqual([6], slices)
        paths.extend(build_pdf(self.source / f'extra-{number}.pdf', ('Page',)) for number in range(2))
        slices.clear()
        worker.run_once()
        self.assertEqual([2, 2, 2], slices)
        state = json.loads((self.source / 'Knowledge Markdown/99_System/Course-Sync.json').read_text())
        self.assertTrue(all(item['attempts'] == 0 for item in state['items'].values()),
                        '正常切片不增加失败次数')
