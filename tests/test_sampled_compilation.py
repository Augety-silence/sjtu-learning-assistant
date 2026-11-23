"""抽样审阅、共享缓存与分段并发回归；不调用真实模型。"""
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from office_fixtures import knowledge_response
from sjtu_learning_assistant.ai_runtime import cancellation_scope, current_cancellation_check
from sjtu_learning_assistant.compilation_cache import CompilationResponseCache
from sjtu_learning_assistant.knowledge_documents import DocumentCompiler
from sjtu_learning_assistant.knowledge_summary import SummaryCompiler
from sjtu_learning_assistant.summary_policy import sampled_review, cache_friendly_data
from sjtu_learning_assistant.transcript_notes import compile_summary
from test_transcript_notes import NotesClient


class SampledCompilationTests(unittest.TestCase):
    def test_stable_sampling_and_nested_rates(self):
        identities = [str(i) for i in range(1000)]
        selected = [sampled_review(i, 20) for i in identities]
        self.assertEqual(selected, [sampled_review(i, 20) for i in identities])
        self.assertTrue(150 < sum(selected) < 250)
        self.assertTrue(all(not sampled_review(i, 0) and sampled_review(i, 100) for i in identities))
        self.assertTrue(all(not sampled_review(i, 20) or sampled_review(i, 40) for i in identities))

    def test_unsampled_summary_does_not_forge_review_cache(self):
        store, calls = {}, []
        def request(model, system, content):
            calls.append(system)
            return knowledge_response(model, system, content)
        entries = [{'page': 1, 'source_text': '真实定义'}]
        compiler = SummaryCompiler(request, store.get, store.update, review_percent=0)
        body, _, report = compiler.compile(entries)
        self.assertEqual(1, report['reviews_sampled_out'])
        self.assertFalse(any('-review' in key for key in store))
        count = len(calls)
        self.assertEqual(body, compiler.compile(entries)[0])
        self.assertEqual(count, len(calls))
        SummaryCompiler(request, store.get, store.update, review_percent=100).compile(entries)
        self.assertEqual(count + 1, len(calls), '提升抽样比例只补审，不重新生成')

    def test_parallel_parts_overlap_merge_always_reviewed_and_cancel_scope_passed(self):
        store, calls, guard = {}, [], threading.Lock()
        barrier = threading.Barrier(3)
        inherited = lambda: False
        def request(model, system, content):
            data = json.loads(content)
            if 'pages' in data and 'draft' not in data:
                self.assertIs(inherited, current_cancellation_check())
                barrier.wait(timeout=4)
            with guard:
                calls.append((model, data))
            return knowledge_response(model, system, content)
        compiler = SummaryCompiler(request, store.get, store.update, review_percent=0, parallel_batches=3)
        entries = [{'page': i, 'source_text': '原文' * 15000} for i in (1, 2, 3)]
        with cancellation_scope(inherited):
            _, _, report = compiler.compile(entries)
        self.assertEqual(3, report['reviews_sampled_out'])
        self.assertEqual(1, report['reviews_selected'])
        self.assertEqual([1, 2, 3], report['checks'][-1]['pages'])
        self.assertEqual(5, len(calls), '三次生成+一次合并+一次审阅')
        before = len(calls)
        compiler.compile(entries)
        self.assertEqual(before, len(calls))

    def test_parallel_call_slice_is_atomic(self):
        runner = Mock(return_value='完整响应')
        compiler = DocumentCompiler(runner, threading.Event(), slice_calls=2)
        def invoke(_):
            try:
                return compiler._request('deepseek-chat', '测试', '输入')
            except Exception:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(invoke, range(50)))
        self.assertEqual(2, runner.call_count)
        self.assertEqual(2, compiler.calls)

    def test_transcript_keeps_all_source_blocks_and_audits_cited_blocks(self):
        raw = 'WEBVTT\n\n' + '\n\n'.join(
            f'00:{i // 60:02}:{i % 60:02}.000 --> 00:{(i+1) // 60:02}:{(i+1) % 60:02}.000\n第{i}段。' + '重要内容。' * 15
            for i in range(120))
        client = NotesClient()
        _, report = compile_summary(raw, client, review_percent=0)
        generated = [d for d in client.calls if 'cues' in d and 'task' not in d]
        self.assertEqual(120, sum(len(d['cues']) for d in generated))
        self.assertEqual(1, len(report['reviewed_chunks']))
        self.assertEqual(report['covered_chunks'][-1], report['reviewed_chunks'][0])
        self.assertTrue(report['reviews_sampled_out'])
        self.assertEqual(sorted(report['covered_chunks']), sorted(report['reviewed_chunks'] + report['reviews_sampled_out']))

    def test_input_order_keeps_sources_ahead_of_draft(self):
        data = {'draft': 'D', 'repair': 'R', 'source_budget': {'target_chars': 1}, 'pages': ['原文']}
        ordered = cache_friendly_data(data)
        self.assertEqual('pages', next(iter(ordered)))
        self.assertEqual(data, ordered)

    def test_shared_response_cache_persists_scope_isolated_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cache.sqlite3'
            cache = CompilationResponseCache(path)
            key = cache.identity(('deepseek-chat', 'endpoint', 'key-hash'), 'prompt', '{"b":2,"a":1}', 100)
            self.assertEqual(key, cache.identity(('deepseek-chat', 'endpoint', 'key-hash'), 'prompt', '{"a":1,"b":2}', 100))
            for scope in [('qwen', 'endpoint', 'key-hash'), ('deepseek-chat', 'other', 'key-hash'), ('deepseek-chat', 'endpoint', 'other-key')]:
                self.assertNotEqual(key, cache.identity(scope, 'prompt', '{"a":1,"b":2}', 100))
            compute = Mock(return_value='{"markdown":"完整正文"}')
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: cache.get_or_compute(key, compute), range(4)))
            self.assertEqual(1, compute.call_count)
            self.assertEqual(1, len(set(results)))
            self.assertEqual(results[0], CompilationResponseCache(path).get(key))
            with sqlite3.connect(path) as db:
                db.execute('UPDATE responses SET content=? WHERE id=?', ('{"markdown":"篡改"}', key))
            self.assertIsNone(cache.get(key))

    def test_cache_does_not_store_failure_invalid_or_expired_responses(self):
        with tempfile.TemporaryDirectory() as temp:
            now = [1000]
            cache = CompilationResponseCache(Path(temp) / 'cache.sqlite3', clock=lambda: now[0], ttl=10, limit=2)
            for i in range(3):
                cache.store(str(i), '{"ok":true}')
            self.assertIsNone(cache.get('0'))
            cache.store('invalid', 'broken')
            self.assertIsNone(cache.get('invalid'))
            key = 'a' * 64
            with self.assertRaises(RuntimeError):
                cache.get_or_compute(key, Mock(side_effect=RuntimeError('截断')))
            self.assertIsNone(cache.get(key))
            now[0] += 20
            self.assertIsNone(cache.get('2'))

    def test_desktop_compilation_runner_reuses_response_and_rejects_truncation(self):
        from sjtu_learning_assistant.dashboard_service import DashboardService
        from sjtu_learning_assistant.knowledge_compiler import KnowledgeResponseError
        from sjtu_learning_assistant.knowledge_summary import SUMMARY_PROMPT
        class FakeClient:
            calls = 0
            truncated = False
            def __init__(self, **kwargs):
                pass
            def chat_completion(self, messages, **kwargs):
                type(self).calls += 1
                return {'content': '{"markdown":"已保存的完整响应"}',
                        'finish_reason': 'length' if self.truncated else 'stop'}
        with tempfile.TemporaryDirectory() as temp, patch(
                'sjtu_learning_assistant.dashboard_service.OpenAIClassificationClient', FakeClient):
            service = DashboardService.__new__(DashboardService)
            service._client_lock = threading.RLock()
            service._effective_settings = lambda: SimpleNamespace(ai_enabled=True, ai_key_saved=True, ai_base_url='https://example.invalid/v1')
            service.ai_key_loader = lambda: 'test-key'
            service.ai_client_factory = FakeClient
            service.local_projects_path = Path(temp) / 'projects.json'
            service.engine = object()
            result = service._run_knowledge_compiler_ai('deepseek-chat', SUMMARY_PROMPT, '{"pages":[]}')
            self.assertEqual(result, service._run_knowledge_compiler_ai('deepseek-chat', SUMMARY_PROMPT, '{"pages":[]}'))
            self.assertEqual(1, FakeClient.calls)
            FakeClient.truncated = True
            for _ in range(2):
                with self.assertRaises(KnowledgeResponseError):
                    service._run_knowledge_compiler_ai('deepseek-chat', SUMMARY_PROMPT, '{"pages":[1]}')
            self.assertEqual(3, FakeClient.calls, '截断响应不得作为完整响应缓存')


if __name__ == '__main__':
    unittest.main()
