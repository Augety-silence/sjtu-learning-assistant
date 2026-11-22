"""断点修复回归：模型响应为合成数据；此测试不触碰生产资料。"""
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from office_fixtures import knowledge_response
from sjtu_learning_assistant.knowledge_documents import CompilationDeferred, DocumentCompiler
from sjtu_learning_assistant.knowledge_summary import SummaryCompiler, SUMMARY_PROMPT, CHECKPOINT_LENGTH_GUIDANCE, validate_summary
from sjtu_learning_assistant.knowledge_compiler import KnowledgeResponseError
from sjtu_learning_assistant.transcript_service import AIContext, TranscriptService
from sjtu_learning_assistant.transcript_notes import compile_summary, SummaryDeferred
from test_transcript_notes import NotesClient, RAW, SOURCE


class CheckpointRecoveryTests(unittest.TestCase):
    def test_two_call_slices_reach_repair_then_review_and_resume_free(self):
        cache, calls = {}, []
        def runner(model, prompt, payload):
            calls.append((model, json.loads(payload)))
            value = json.loads(knowledge_response(model, prompt, payload))
            if model == 'deepseek-chat' and len(calls) <= 2:
                value['markdown'] = '超长重复' * 1500
            return json.dumps(value, ensure_ascii=False)
        entries = [dict(page=1, source_text='原始定义', text='证据')]
        def compiler():
            document = DocumentCompiler(runner, threading.Event(), slice_calls=2)
            return SummaryCompiler(document._request, cache.get, cache.update)
        with self.assertRaises(CompilationDeferred):
            compiler().compile(entries)
        body, _, report = compiler().compile(entries)
        self.assertEqual(4, len(calls))
        self.assertEqual(160, calls[2][1]['target_chars'])
        self.assertIn('draft_markdown', calls[2][1])
        self.assertTrue(report['checks'][0]['approved'])
        self.assertEqual(body, compiler().compile(entries)[0])
        self.assertEqual(4, len(calls), '恢复已验收稿不得新增付费请求')

    def test_malformed_json_is_saved_and_not_regenerated_after_restart(self):
        cache, calls = {}, []
        def request(model, prompt, payload):
            calls.append(json.loads(payload))
            if len(calls) <= 2:
                return '{broken JSON'
            return knowledge_response(model, prompt, payload)
        entries = [dict(page=1, text='完整证据')]
        for _ in range(3):
            document = DocumentCompiler(request, threading.Event(), slice_calls=1)
            with self.assertRaises(CompilationDeferred):
                SummaryCompiler(document._request, cache.get, cache.update).compile(entries)
        document = DocumentCompiler(request, threading.Event(), slice_calls=1)
        SummaryCompiler(document._request, cache.get, cache.update).compile(entries)
        self.assertEqual(4, len(calls))
        self.assertEqual([None, 1, 2], [data.get('repair_attempt') for data in calls[:3]])

    def test_exhausted_invalid_stage_costs_nothing_on_automatic_retry(self):
        cache, calls = {}, []
        def request(model, prompt, payload):
            calls.append(payload)
            return '{invalid'
        entries = [dict(page=1, text='完整证据')]
        for _ in range(2):
            document = DocumentCompiler(request, threading.Event(), slice_calls=2)
            try:
                SummaryCompiler(document._request, cache.get, cache.update).compile(entries)
            except (CompilationDeferred, ValueError):
                pass
        self.assertEqual(4, len(calls))
        with self.assertRaises(ValueError):
            document = DocumentCompiler(request, threading.Event(), slice_calls=2)
            SummaryCompiler(document._request, cache.get, cache.update).compile(entries)
        self.assertEqual(4, len(calls))

    def test_legacy_invalid_and_retry_go_directly_to_targeted_repair(self):
        cache, calls = {}, []
        def request(model, prompt, payload):
            calls.append(json.loads(payload))
            return json.dumps(dict(markdown='accepted'))
        compiler = SummaryCompiler(request, cache.get, cache.update)
        for suffix in ('-invalid.json', '-retry.json'):
            cache[compiler.prefix + 'legacy.json' + suffix] = json.dumps(dict(markdown='invalid'))
        def validate(value):
            if value.get('markdown') != 'accepted':
                raise ValueError('格式错误')
            return value
        compiler._saved('legacy.json', SUMMARY_PROMPT, dict(pages=[]), validate)
        self.assertEqual(1, len(calls))
        self.assertEqual(2, calls[0]['repair_attempt'])

    def test_truncated_paid_response_is_checkpointed_before_validation(self):
        cache, calls = {}, []
        def request(model, prompt, payload):
            calls.append(json.loads(payload))
            raise KnowledgeResponseError('响应截断', content='{"markdown":"looks valid"}')
        for _ in range(4):
            document = DocumentCompiler(request, threading.Event(), slice_calls=1)
            with self.assertRaises((CompilationDeferred, ValueError)):
                SummaryCompiler(document._request, cache.get, cache.update)._saved(
                    'cut.json', SUMMARY_PROMPT, {}, lambda value: value)
        self.assertEqual(4, len(calls))
        # 模拟保存响应后、推进修复步前崩溃；恢复仍不能把完整JSON的截断稿误当成功。
        for key in list(cache):
            if '-resume-v1-' in key:
                cache.pop(key)
        with self.assertRaises(ValueError):
            document = DocumentCompiler(request, threading.Event(), slice_calls=1)
            SummaryCompiler(document._request, cache.get, cache.update)._saved(
                'cut.json', SUMMARY_PROMPT, {}, lambda value: value)
        self.assertEqual(4, len(calls))
        saved = [json.loads(value) for key, value in cache.items() if '-failed-response-' in key]
        self.assertEqual(4, len(saved))
        self.assertEqual('{"markdown":"looks valid"}', saved[0]['content'])

    def test_response_saved_before_validation_crash_avoids_new_api(self):
        cache, request = {}, Mock(return_value='{"markdown":"accepted"}')
        compiler = SummaryCompiler(request, cache.get, cache.update)
        with self.assertRaises(RuntimeError):
            compiler._saved('crash.json', SUMMARY_PROMPT, {}, Mock(side_effect=RuntimeError('crash')))
        self.assertEqual(1, request.call_count)
        SummaryCompiler(request, cache.get, cache.update)._saved('crash.json', SUMMARY_PROMPT, {}, lambda value: value)
        self.assertEqual(1, request.call_count)

    def test_changed_repair_contract_does_not_reuse_other_input_response(self):
        cache, calls = {}, []
        def request(model, prompt, payload):
            calls.append(prompt)
            return '{invalid' if '旧规则' in prompt else '{"markdown":"accepted"}'
        compiler = SummaryCompiler(request, cache.get, cache.update)
        with self.assertRaises(ValueError):
            compiler._saved('identity.json', SUMMARY_PROMPT + '旧规则', {}, lambda value: value)
        self.assertEqual(4, len(calls))
        result = compiler._saved('identity.json', SUMMARY_PROMPT + '新规则', {}, lambda value: value)
        self.assertEqual('accepted', result['markdown'])
        self.assertEqual(5, len(calls))

    def test_length_contract_upgrade_reuses_latest_failed_draft_and_ratio(self):
        cache, calls = {}, []
        data = {'pages': [{'page': 1}], 'topic_ids': [], 'source_budget': {'target_chars': 2600}}
        def request(model, prompt, payload):
            calls.append(json.loads(payload))
            result = json.loads(knowledge_response(model, prompt, payload))
            if CHECKPOINT_LENGTH_GUIDANCE not in prompt:
                result['markdown'] = '重复' * 3000
            return json.dumps(result, ensure_ascii=False)
        compiler = SummaryCompiler(request, cache.get, cache.update)
        with self.assertRaises(ValueError):
            compiler._saved('length.json', SUMMARY_PROMPT, data, lambda value: validate_summary(value, [1]))
        self.assertEqual(4, len(calls))
        compiler._saved('length.json', SUMMARY_PROMPT + CHECKPOINT_LENGTH_GUIDANCE, data,
                        lambda value: validate_summary(value, [1]))
        self.assertEqual(5, len(calls))
        self.assertEqual(2, calls[-1]['repair_attempt'])
        self.assertEqual({'current_markdown_chars': 6000, 'target_markdown_chars': 2600,
                          'retain_fraction': 0.43}, calls[-1]['compression_plan'])
        compiler._saved('length.json', SUMMARY_PROMPT + CHECKPOINT_LENGTH_GUIDANCE, data,
                        lambda value: validate_summary(value, [1]))
        self.assertEqual(5, len(calls))

    def test_legacy_overlong_draft_only_compresses_text_and_reuses_completion(self):
        cache, calls = {}, []
        def request(model, prompt, payload):
            calls.append(json.loads(payload))
            return knowledge_response(model, prompt, payload)
        compiler = SummaryCompiler(request, cache.get, cache.update)
        data = {'pages': [{'page': 1}], 'topic_ids': [], 'source_budget': {'target_chars': 2600}}
        draft = json.loads(knowledge_response('deepseek-chat', SUMMARY_PROMPT, json.dumps(data)))
        draft['markdown'] = '重复' * 3000
        cache[compiler.prefix + 'compact.json-retry.json'] = json.dumps(draft)
        result = compiler._save_summary('compact.json', SUMMARY_PROMPT, data, [1], [])
        self.assertEqual(1, len(calls))
        self.assertNotIn('pages', calls[0])
        self.assertEqual(2600, calls[0]['target_chars'])
        self.assertEqual(draft['topics'], result['topics'])
        self.assertEqual(draft['covered_pages'], result['covered_pages'])
        compiler._save_summary('compact.json', SUMMARY_PROMPT, data, [1], [])
        self.assertEqual(1, len(calls))

    def test_prefetch_ready_and_legacy_saved_both_generate_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'transcripts'
            client = NotesClient()
            service = TranscriptService(root, autostart_worker=False, enable_phase1=False,
                subtitle_fetcher=lambda _: dict(status='ready', vtt=RAW),
                ai_context_provider=lambda: AIContext(client, 'deepseek-chat', 'test', True))
            try:
                service.prefetch(course_id='12', course_name='课程', videos=[dict(source_id=SOURCE, title='课堂')])
                service.run_pending(source_only=True)
                directory = service._video_dir(SOURCE)
                self.assertEqual('raw_ready', service._read_json(directory / 'manifest.json')['status'])
                # 模拟旧1.8.16仅成功批次但录像仍saved的情况。
                manifest = service._read_json(directory / 'manifest.json')
                manifest['status'] = 'saved'
                service._write_json(directory / 'manifest.json', manifest)
                raw_hash = hashlib.sha256((directory / 'raw.vtt').read_bytes()).hexdigest()
            finally:
                service.close()
            service = TranscriptService(root, autostart_worker=False, enable_phase1=False,
                ai_context_provider=lambda: AIContext(client, 'deepseek-chat', 'test', True))
            try:
                self.assertEqual('raw_ready', service._read_json(directory / 'manifest.json')['status'])
                service._backfill_next_lesson()
                self.assertEqual('completed', service._read_json(directory / 'manifest.json')['lesson_notes']['status'])
                before = len(client.calls)
                service._next_notes_scan = 0
                service._backfill_next_lesson()
                self.assertEqual(before, len(client.calls))
                self.assertEqual(raw_hash, hashlib.sha256((directory / 'raw.vtt').read_bytes()).hexdigest())
            finally:
                service.close()

    def test_empty_source_is_not_short_interval_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            fetch = Mock(return_value=dict(status='empty', message='该录像暂无字幕。'))
            service = TranscriptService(Path(temporary) / 'transcripts', autostart_worker=False, subtitle_fetcher=fetch)
            try:
                service.prefetch(course_id='12', course_name='课程', videos=[dict(source_id=SOURCE, title='课堂')])
                service.run_pending(source_only=True)
                job = service._all_batches()[0]['jobs'][0]
                self.assertEqual('source_empty', job['fetch_error_code'])
                self.assertEqual('failed', job['status'])
                service.run_pending(source_only=True)
                self.assertEqual(1, fetch.call_count)
                self.assertEqual(1, service.prefetch(course_id='12', course_name='课程',
                    videos=[dict(source_id=SOURCE, title='课堂')], retry=True))
            finally:
                service.close()

    def test_subtitle_bad_responses_survive_slices_without_repeat_payment(self):
        cache, client = {}, NotesClient()
        original = client.chat_completion
        invocations = []
        def call(*args, **kwargs):
            invocations.append(1)
            if len(invocations) == 1:
                return {'content': '{bad JSON', 'finish_reason': 'stop'}
            return original(*args, **kwargs)
        client.chat_completion = call
        options = dict(cached=cache.get, commit=lambda key, value: cache.setdefault(key, value), max_calls=1)
        with self.assertRaises(SummaryDeferred):
            compile_summary(RAW, client, **options)
        with self.assertRaises(SummaryDeferred):
            compile_summary(RAW, client, **options)
        body, report = compile_summary(RAW, client, **options)
        self.assertEqual(3, len(invocations))
        self.assertEqual(1, report['calls'])
        self.assertEqual(body, compile_summary(RAW, client, **options)[0])
        self.assertEqual(3, len(invocations))

    def test_subtitle_truncated_adapter_response_is_checkpointed(self):
        cache, client = {}, NotesClient()
        client.chat_completion = Mock(side_effect=KnowledgeResponseError('响应截断', content='{"partial":true}'))
        options = dict(cached=cache.get, commit=lambda key, value: cache.setdefault(key, value), max_calls=1)
        with self.assertRaises(SummaryDeferred):
            compile_summary(RAW, client, **options)
        with self.assertRaises(ValueError):
            compile_summary(RAW, client, **options)
        with self.assertRaises(ValueError):
            compile_summary(RAW, client, **options)
        self.assertEqual(2, client.chat_completion.call_count)
        self.assertEqual(2, len(cache))
