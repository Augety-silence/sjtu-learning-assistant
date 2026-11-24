"""停滞恢复回归：真实本地渲染，模型请求均为合成，不碰生产资料。"""
import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import httpx

from office_fixtures import build_pdf, knowledge_response
from sjtu_learning_assistant.ai_classifier import ClassificationInput, OpenAIClassificationClient
from sjtu_learning_assistant.knowledge_documents import page_images, _PDFIUM_LOCK
from sjtu_learning_assistant.knowledge_study import canonical_citations, cited_pages
from sjtu_learning_assistant.knowledge_summary import SUMMARY_PROMPT, SummaryCompiler, validate_summary
from sjtu_learning_assistant.markdown_progress import transcript_progress
from sjtu_learning_assistant.document_text import DocumentTextError
from sjtu_learning_assistant.course_markdown_sync import RECOVERY_VERSION
import test_course_markdown_sync as course_tests


class RendererRecoveryTests(unittest.TestCase):
    def test_parallel_pdfium_render_and_yield_releases_native_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            pdf = build_pdf(Path(directory) / 'slides.pdf')
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: list(page_images(pdf)), range(12)))
            self.assertTrue(all(len(pages) == 2 for pages in results))
            self.assertTrue(all(data.startswith(b'\xff\xd8') for pages in results for _, data in pages))
            iterator = page_images(pdf)
            next(iterator)
            acquired = threading.Event()
            def another_thread():
                with _PDFIUM_LOCK:
                    acquired.set()
            worker = threading.Thread(target=another_thread)
            worker.start()
            try:
                self.assertTrue(acquired.wait(2), '等待 API 时不可占用 PDFium 锁')
            finally:
                iterator.close()
                worker.join(2)


class CitationRecoveryTests(unittest.TestCase):
    def test_explicit_short_citations_are_normalized_not_guessed(self):
        self.assertEqual('[课件 p.4] [课件 p.5–6] [课件 p.8] [课件 p.10]',
                         canonical_citations('[p.4] [pp.5–6] [p.8, p.10]'))
        self.assertEqual({4, 5, 6, 8, 10}, cited_pages('[p.4] [pp.5–6] [p.8, p.10]'))
        self.assertEqual('第4章 [4] [p.4x]', canonical_citations('第4章 [4] [p.4x]'))
        for bad in ('[p.0]', '[p.501]', '[pp.6–4]'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cited_pages(bad)

    def test_old_exhausted_citation_draft_recovers_without_api(self):
        cache = {}
        def no_api(*_):
            self.fail('已有有效草稿不应重新付费生成')
        compiler = SummaryCompiler(no_api, cache.get, cache.update)
        data = dict(pages=[dict(page=1)], topic_ids=[])
        draft = json.loads(knowledge_response('deepseek-chat', SUMMARY_PROMPT, json.dumps(data)))
        draft['markdown'] = draft['markdown'].replace('[课件 p.', '[p.')
        identity = hashlib.sha256((SUMMARY_PROMPT + json.dumps(data, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
        cache[compiler.prefix + 'old.json-resume-v1-' + identity[:12] + '.json'] = json.dumps(
            dict(input=identity, step=4, draft=json.dumps(draft), error='精炼总结缺少有效来源引用。'))
        result = compiler._saved('old.json', SUMMARY_PROMPT, data, lambda value: validate_summary(value, [1]))
        self.assertIn('[课件 p.1]', result['markdown'])
        invalid = dict(result, markdown=result['markdown'].replace('[课件 p.1]', '[p.2]'))
        with self.assertRaises(ValueError):
            validate_summary(invalid, [1])


class StructureRecoveryTests(unittest.TestCase):
    def test_structure_stage_is_bounded_and_restart_does_not_reset_paid_budget(self):
        cache, calls = {}, []
        data = dict(pages=[dict(page=1)], topic_ids=[])
        def request(model, prompt, payload):
            calls.append(json.loads(payload))
            draft = json.loads(knowledge_response(model, prompt, payload))
            draft['topics'] = []
            return json.dumps(draft)
        for _ in range(3):
            with self.assertRaisesRegex(ValueError, '主题结构'):
                SummaryCompiler(request, cache.get, cache.update)._saved(
                    'structure.json', SUMMARY_PROMPT, data, lambda value: validate_summary(value, [1]))
        self.assertEqual(6, len(calls), '原4请求 + 独立修复最多2请求，重启不重付费')
        self.assertEqual('structure-v1', calls[4]['repair_kind'])

    def test_structure_only_repair_success_is_reused(self):
        cache, calls = {}, []
        data = dict(pages=[dict(page=1)], topic_ids=[])
        def request(model, prompt, payload):
            value = json.loads(payload)
            calls.append(value)
            result = json.loads(knowledge_response(model, prompt, payload))
            if value.get('repair_kind') != 'structure-v1':
                result['topics'] = []
            return json.dumps(result)
        validator = lambda value: validate_summary(value, [1])
        result = SummaryCompiler(request, cache.get, cache.update)._saved('structure.json', SUMMARY_PROMPT, data, validator)
        self.assertEqual(5, len(calls))
        self.assertEqual(result, SummaryCompiler(request, cache.get, cache.update)._saved('structure.json', SUMMARY_PROMPT, data, validator))
        self.assertEqual(5, len(calls))


class ClassificationRecoveryTests(unittest.TestCase):
    def test_fast_background_classifier_does_not_change_reasoner_chat(self):
        seen, usage = [], []
        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            content = '{"classifications":[{"id":"1","category":"courseware"}]}' if len(seen) == 1 else '聊天回复'
            return httpx.Response(200, json=dict(usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15), choices=[dict(message=dict(content=content), finish_reason='stop')]), request=request)
        client = OpenAIClassificationClient(api_key='fake-key', model='deepseek-reasoner',
            transport=httpx.MockTransport(handler), limiter=lambda: None,
            usage_recorder=lambda model, counts: usage.append((model, counts)))
        try:
            self.assertEqual({'1': 'courseware'}, client.classify_many((ClassificationInput('1', '课程', 'slides.pdf'),)))
            client.chat_completion([dict(role='user', content='问题')], system_prompt=False)
        finally:
            client.close()
        self.assertEqual('deepseek-chat', seen[0]['model'])
        self.assertEqual(2048, seen[0]['max_tokens'])
        self.assertEqual('deepseek-reasoner', seen[1]['model'])
        self.assertEqual(['deepseek-chat', 'deepseek-reasoner'], [model for model, _ in usage])


class ProgressRecoveryTests(unittest.TestCase):
    def test_old_and_new_empty_subtitles_are_unavailable_not_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'batches').mkdir()
            jobs = []
            for index in range(4):
                source = f'sjtu-video:12:{index}'
                folder = root / 'videos' / hashlib.sha256(source.encode()).hexdigest()[:32]
                folder.mkdir(parents=True)
                (folder / '.lesson.lock').touch()
                job = dict(source_id=source, status='failed')
                if index == 0:
                    job['error'] = '该录像暂无字幕。'
                elif index == 1:
                    job['fetch_error_code'] = 'source_empty'
                elif index == 2:
                    job['error'] = '真实抓取失败'
                else:
                    continue
                jobs.append(job)
            (root / 'batches' / 'batch.json').write_text(json.dumps(dict(source_only=True, jobs=jobs)), encoding='utf-8')
            result = transcript_progress(root)
            self.assertEqual((4, 2, 1, 1), tuple(result[key] for key in ('total', 'unavailable', 'failed', 'pending')))


class RetryRecoveryTests(unittest.TestCase):
    # 只复用 fixture，避免再次收集继承的全套测试。
    setUp = course_tests.CourseMarkdownSyncTests.setUp
    tearDown = course_tests.CourseMarkdownSyncTests.tearDown
    new_worker = course_tests.CourseMarkdownSyncTests.new_worker

    def test_known_error_wakes_once_without_resetting_network_backoff(self):
        self.runner.side_effect = DocumentTextError('PdfiumError: render')
        self.worker.run_once()
        marker = self.target / '99_System/Course-Sync.json'
        state = json.loads(marker.read_text(encoding='utf-8'))
        item = state['items'][self.path.relative_to(self.source).as_posix()]
        item.update(attempts=6, retry_after=(self.now + timedelta(hours=6)).isoformat())
        marker.write_text(json.dumps(state), encoding='utf-8')
        before = self.runner.call_count
        self.worker.run_once()
        item = json.loads(marker.read_text(encoding='utf-8'))['items'][self.path.relative_to(self.source).as_posix()]
        self.assertEqual(RECOVERY_VERSION, item['recovery_version'])
        self.assertEqual(7, item['attempts'])
        self.assertGreater(self.runner.call_count, before)
        before = self.runner.call_count
        self.worker.run_once()
        self.assertEqual(before, self.runner.call_count, '恢复标记不得每轮清除退避')
        state = json.loads(marker.read_text(encoding='utf-8'))
        state['items'][self.path.relative_to(self.source).as_posix()]['error'] = '请求超时'
        marker.write_text(json.dumps(state), encoding='utf-8')
        self.worker.run_once()
        self.assertEqual(before, self.runner.call_count)
