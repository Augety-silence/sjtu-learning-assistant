"""自动恢复只接续失败阶段，不清空付费检查点；全部模型均为合成。"""
import hashlib
import json
from datetime import timedelta
from unittest import TestCase
from unittest.mock import Mock

from office_fixtures import knowledge_response
from sjtu_learning_assistant.knowledge_summary import SummaryCompiler, SUMMARY_PROMPT, validate_summary
from sjtu_learning_assistant.knowledge_documents import CompilationDeferred
from sjtu_learning_assistant.transcript_notes import compile_summary, SummaryDeferred
from sjtu_learning_assistant.summary_policy import recovery_delay_seconds
from test_transcript_notes import RAW, NotesClient
import test_course_markdown_sync as course_tests


class BoundedSummaryTests(TestCase):
    def setup_draft(self, topics):
        cache = {}
        compiler = SummaryCompiler(Mock(), cache.get, cache.update, auto_recovery=True)
        data = {'pages': [{'page': 1, 'text': '完整原始来源'}], 'topic_ids': []}
        draft = json.loads(knowledge_response('deepseek-chat', SUMMARY_PROMPT, json.dumps(data)))
        draft['topics'] = topics
        identity = hashlib.sha256((SUMMARY_PROMPT + json.dumps(data, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
        cache[compiler.prefix + 'old.json-resume-v1-' + identity[:12] + '.json'] = json.dumps(
            dict(input=identity, step=4, draft=json.dumps(draft), error='精炼总结缺少完整主题结构。'))
        return cache, compiler, data, draft

    def test_more_than_twenty_real_topics_reuses_paid_draft_without_api(self):
        topics = [dict(name=f'真实主题{n}', pages=[1]) for n in range(24)]
        cache, compiler, data, draft = self.setup_draft(topics)
        self.assertEqual(topics, compiler._saved('old.json', SUMMARY_PROMPT, data, lambda v: validate_summary(v, [1]))['topics'])
        compiler.request.assert_not_called()
        with self.assertRaises(ValueError):
            validate_summary({**draft, 'topics': [dict(name='伪造来源', pages=[99])]}, [1])

    def test_patch_does_not_rewrite_paid_body_and_restart_is_free(self):
        cache, compiler, data, draft = self.setup_draft([])
        compiler.request.return_value = json.dumps({'topics': [{'name': '主题', 'pages': [1]}]})
        validate = lambda v: validate_summary(v, [1])
        result = compiler._saved('old.json', SUMMARY_PROMPT, data, validate)
        self.assertEqual(draft['markdown'], result['markdown'])
        sent = json.loads(compiler.request.call_args.args[2])
        self.assertNotIn('markdown', sent['draft'])
        self.assertIn('pages', sent)
        SummaryCompiler(compiler.request, cache.get, cache.update, auto_recovery=True)._saved('old.json', SUMMARY_PROMPT, data, validate)
        self.assertEqual(1, compiler.request.call_count)

    def test_restart_never_refills_two_failed_patch_requests(self):
        cache, compiler, data, draft = self.setup_draft([])
        request = Mock(return_value='{"topics":[]}')
        for _ in range(4):
            with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
                SummaryCompiler(request, cache.get, cache.update, auto_recovery=True)._saved(
                    'old.json', SUMMARY_PROMPT, data, lambda v: validate_summary(v, [1]))
        self.assertEqual(2, request.call_count)

    def test_timeout_is_reserved_and_deferred_slice_does_not_consume_budget(self):
        cache, compiler, data, _ = self.setup_draft([])
        request = Mock(side_effect=[CompilationDeferred(), TimeoutError(), '{"topics":[]}'])
        for expected in (CompilationDeferred, TimeoutError, ValueError):
            with self.assertRaises(expected):
                SummaryCompiler(request, cache.get, cache.update, auto_recovery=True)._saved(
                    'old.json', SUMMARY_PROMPT, data, lambda v: validate_summary(v, [1]))
        with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
            SummaryCompiler(request, cache.get, cache.update, auto_recovery=True)._saved(
                'old.json', SUMMARY_PROMPT, data, lambda v: validate_summary(v, [1]))
        self.assertEqual(3, request.call_count)

    def test_saved_patch_response_survives_crash_before_validation(self):
        cache, compiler, data, _ = self.setup_draft([])
        compiler.request.return_value = '{"topics":[{"name":"主题","pages":[1]}]}'
        def validate(v):
            if v.get('topics'):
                raise RuntimeError('validation crash')
            return validate_summary(v, [1])
        with self.assertRaises(RuntimeError):
            compiler._saved('old.json', SUMMARY_PROMPT, data, validate)
        SummaryCompiler(compiler.request, cache.get, cache.update, auto_recovery=True)._saved(
            'old.json', SUMMARY_PROMPT, data, lambda v: validate_summary(v, [1]))
        self.assertEqual(1, compiler.request.call_count)


class BoundedTranscriptTests(TestCase):
    def test_old_rejection_gets_one_recheck_not_forced_approval_or_repeated_payment(self):
        cache, client = {}, NotesClient()
        original = client.chat_completion
        calls = []
        def request(messages, **kwargs):
            data = json.loads(messages[-1]['content'])
            calls.append(data)
            if 'task' in data:
                return {'content': json.dumps({'approved': False, 'covered_chunks': data['covered_chunks'], 'issues': ['真实来源矛盾']}), 'finish_reason': 'stop'}
            return original(messages, **kwargs)
        client.chat_completion = request
        for _ in range(4):
            with self.assertRaisesRegex(ValueError, '来源审校未通过'):
                compile_summary(RAW, client, cached=cache.get, commit=lambda k, v: cache.setdefault(k, v), auto_recovery=True)
        self.assertEqual(3, len(calls), '生成1+旧审阅1+新政策复核1；拒绝保持拒绝')

    def test_new_recovery_is_bounded_across_slices_and_restarts(self):
        cache = {}
        client = Mock()
        client.chat_completion.return_value = {'content': '{bad', 'finish_reason': 'stop'}
        for _ in range(8):
            with self.assertRaises((ValueError, SummaryDeferred)):
                compile_summary(RAW, client, cached=cache.get, commit=lambda k, v: cache.setdefault(k, v), max_calls=1, auto_recovery=True)
        self.assertEqual(4, client.chat_completion.call_count, '原稿1+旧修复1+新修复2，不因切片重置')

    def test_success_recovery_preserves_raw_and_uses_cached_review(self):
        cache, client = {}, NotesClient()
        original = client.chat_completion
        calls = []
        def request(messages, **kwargs):
            calls.append(1)
            if len(calls) <= 2:
                return {'content': '{bad', 'finish_reason': 'stop'}
            return original(messages, **kwargs)
        client.chat_completion = request
        body, _ = compile_summary(RAW, client, cached=cache.get, commit=lambda k, v: cache.setdefault(k, v), auto_recovery=True)
        self.assertIn('机会成本', body)
        again, _ = compile_summary(RAW, client, cached=cache.get, commit=lambda k, v: cache.setdefault(k, v), auto_recovery=True)
        self.assertEqual(body, again)
        self.assertEqual(4, len(calls))


class AutoWakeTests(TestCase):
    setUp = course_tests.CourseMarkdownSyncTests.setUp
    tearDown = course_tests.CourseMarkdownSyncTests.tearDown
    new_worker = course_tests.CourseMarkdownSyncTests.new_worker

    def test_network_cooldown_and_due_wake_do_not_run_wiki(self):
        self.worker.auto_recovery = True
        self.worker.max_documents_per_cycle = 10
        self.worker.wiki_rebuilder = Mock()
        self.runner.side_effect = TimeoutError()
        self.worker.run_once()
        self.assertEqual(30, self.worker._wait_seconds())
        self.worker.wiki_rebuilder.assert_not_called()
        before = self.runner.call_count
        self.worker.run_once()
        self.assertEqual(before, self.runner.call_count)
        self.now += timedelta(seconds=31)
        self.runner.side_effect = knowledge_response
        self.worker.run_once()
        self.assertEqual(1, self.worker.status()['completed'])

    def test_network_policy_has_short_bounded_backoff(self):
        self.assertEqual([30, 90, 180, 600, 1800, 1800], [recovery_delay_seconds(n, 'timeout') for n in range(1, 7)])
