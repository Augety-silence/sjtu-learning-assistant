"""Isolated fake-client tests: no real home backup, keychain, network, or model."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from office_fixtures import knowledge_response
from sjtu_learning_assistant.knowledge_compiler import KnowledgeResponseError
from sjtu_learning_assistant.knowledge_summary import (
    SummaryCompiler, SUMMARY_PROMPT, CHECKPOINT_LENGTH_GUIDANCE, validate_summary,
)
from sjtu_learning_assistant.segment_recovery import (
    AuthorizedSegmentRecovery, RecoveryBlocked, RecoveryBudgetExhausted, RecoveryOutcomeUnknown,
)
from sjtu_learning_assistant.summary_policy import source_chars, summary_budget


class AuthorizedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Path(self.temp.name).resolve() / 'requests.json'
        self.ledger.write_text(json.dumps({'requests': 40, 'limit': 64, 'results': [{'old': True}]}))
        self.stored = {}
        self.calls = []

    def context(self, **kwargs):
        defaults = dict(scope='explicit-operation', max_calls=10, registrations={'source-A': ['part-0']},
                        allow_ai=True, runner=self.request)
        defaults.update(kwargs)
        return AuthorizedSegmentRecovery(self.ledger, **defaults).for_source('source-A')

    def request(self, model, prompt, content):
        self.calls.append((model, prompt, content))
        return knowledge_response(model, prompt, content)

    def compiler(self, context):
        return SummaryCompiler(self.request, self.stored.get, self.stored.update, authorized_recovery=context)

    def invoke(self, compiler, context, content='{}', prompt='来源审校员', model='qwen3.8-27b'):
        with context.node_scope('part-0-any-version'):
            return compiler.request(model, prompt, content)

    def draft(self):
        return json.loads(knowledge_response('deepseek-chat', SUMMARY_PROMPT,
                          json.dumps({'pages': [{'page': 1}, {'page': 2}], 'topic_ids': []})))

    def test_total_reservations_before_timeout_and_all_requests_bounded(self):
        context = self.context(max_calls=1)
        def timeout(*args):
            self.assertEqual(41, json.loads(self.ledger.read_text())['requests'])
            raise TimeoutError('fake timeout')
        context.runner = timeout
        compiler = self.compiler(context)
        with self.assertRaises(TimeoutError):
            self.invoke(compiler, context)
        self.assertEqual(1, context.status()['requests'])
        with self.assertRaises(RecoveryOutcomeUnknown):
            self.invoke(compiler, context, content='{"changed_error":true}')
        context2 = self.context(max_calls=1, runner=timeout)
        with self.assertRaises(RecoveryOutcomeUnknown):
            self.invoke(self.compiler(context2), context2)
        self.assertEqual(41, json.loads(self.ledger.read_text())['requests'])

    def test_shared_ten_total_and_linked_limit_never_reset(self):
        context = self.context(registrations={'source-A': ['part-0'], 'source-B': ['merge-0-0']})
        a = self.compiler(context)
        b_context = context.for_source('source-B')
        b = self.compiler(b_context)
        for i in range(10):
            current, compiler, node = (context, a, 'part-0') if i % 2 == 0 else (b_context, b, 'merge-0-0')
            with current.node_scope(node):
                compiler.request('qwen3.8-27b', '来源审校员', json.dumps({'i': i}))
        with self.assertRaises(RecoveryBudgetExhausted):
            self.invoke(a, context, content='{"i":99}')
        history = json.loads(self.ledger.read_text())
        self.assertEqual((50, 64, [{'old': True}]), (history['requests'], history['limit'], history['results']))
        self.assertEqual(10, len(self.calls))

    def test_linked_existing_limit_checked_before_provider(self):
        self.ledger.write_text(json.dumps({'requests': 64, 'limit': 64, 'results': []}))
        context = self.context()
        with self.assertRaises(RecoveryBudgetExhausted):
            self.invoke(self.compiler(context), context)
        self.assertEqual([], self.calls)

    def test_exact_response_reused_across_restart_without_new_reservation(self):
        context = self.context()
        first = self.invoke(self.compiler(context), context)
        restarted = self.context()
        self.assertEqual(first, self.invoke(self.compiler(restarted), restarted))
        self.assertEqual(1, len(self.calls))
        self.assertEqual(41, json.loads(self.ledger.read_text())['requests'])

    def test_provider_user_exact_cache_is_consulted_before_reservation(self):
        context = self.context(cache_lookup=lambda *args: '{"approved":true,"issues":[]}')
        self.invoke(self.compiler(context), context)
        self.assertEqual(0, context.status()['requests'])
        self.assertEqual([], self.calls)

    def test_unknown_attempt_requires_bounded_explicit_opt_in(self):
        context = self.context(runner=lambda *args: (_ for _ in ()).throw(TimeoutError()))
        with self.assertRaises(TimeoutError):
            self.invoke(self.compiler(context), context)
        with self.assertRaises(RecoveryOutcomeUnknown):
            self.invoke(self.compiler(context), context)
        opted = self.context(retry_unknown=1)
        self.invoke(self.compiler(opted), opted)
        self.assertEqual(2, opted.status()['requests'])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(42, json.loads(self.ledger.read_text())['requests'])

    def test_successful_explicit_unknown_retry_does_not_block_later_node_review(self):
        context = self.context(runner=lambda *args: (_ for _ in ()).throw(TimeoutError()))
        with self.assertRaises(TimeoutError):
            self.invoke(self.compiler(context), context)
        opted = self.context(retry_unknown=1)
        compiler = self.compiler(opted)
        self.invoke(compiler, opted)
        self.invoke(compiler, opted, content='{"next_review":true}')
        self.assertEqual(3, opted.status()['requests'])
        self.assertEqual(2, len(self.calls))
        self.assertEqual(43, json.loads(self.ledger.read_text())['requests'])

    def test_two_generations_per_failed_node_reviews_share_total(self):
        context = self.context()
        compiler = self.compiler(context)
        for i in range(2):
            self.invoke(compiler, context, json.dumps({'pages': [{'page': 1}], 'topic_ids': [], 'repair': i}), SUMMARY_PROMPT, 'deepseek-chat')
        with self.assertRaises(RecoveryBudgetExhausted):
            self.invoke(compiler, context, '{"pages":[{"page":1}],"repair":2}', SUMMARY_PROMPT, 'deepseek-chat')
        self.invoke(compiler, context)
        self.assertEqual(3, context.status()['requests'])

    def test_raw_envelope_saved_before_finish_reason_and_not_truncated(self):
        body = 'x' * 70000
        context = self.context(runner=lambda *args: {'content': body, 'finish_reason': 'length'})
        with self.assertRaises(KnowledgeResponseError) as caught:
            self.invoke(self.compiler(context), context)
        self.assertEqual(body, caught.exception.content)
        history = json.loads(self.ledger.read_text())
        attempt = history['authorized_scopes']['explicit-operation']['attempts'][0]
        raw = json.loads(context._response_path(attempt).read_text())
        self.assertEqual(body, raw['raw']['content'])
        with self.assertRaises(KnowledgeResponseError):
            self.invoke(self.compiler(context), context)
        self.assertEqual(1, context.status()['requests'])

    def test_raw_capture_precedes_client_validation_and_preserves_whitespace(self):
        body = '  {"approved":true,"issues":[]}  \n'
        payload = {'choices': [{'message': {'content': body}, 'finish_reason': 'stop'}], 'usage': {'total_tokens': 7}}
        def capture_request(model, prompt, content, *, raw_response):
            self.calls.append((model, prompt, content))
            raw_response(payload)
            self.assertTrue(any(self.ledger.parent.rglob('response-*.json')))
            raise ValueError('fake client parser error after receiving response')
        capture_request.supports_raw_capture = True
        context = self.context(runner=capture_request)
        self.assertEqual(body, self.invoke(self.compiler(context), context))
        self.assertEqual(body, self.invoke(self.compiler(context), context))
        self.assertEqual(1, len(self.calls))
        attempt = json.loads(self.ledger.read_text())['authorized_scopes']['explicit-operation']['attempts'][0]
        self.assertEqual(payload, json.loads(context._response_path(attempt).read_text())['raw']['provider_response'])

    def test_actual_review_rejection_cannot_be_replaced_by_metadata_or_old_approval(self):
        context = self.context()
        compiler = self.compiler(context)
        data = {'pages': [{'page': 1, 'source_text': '关系模型定义'}, {'page': 2, 'source_text': '条件'}], 'topic_ids': []}
        bad = self.draft()
        bad['markdown'] += '\n无来源的因果断言 [课件 p.1]'
        self.stored[compiler.prefix + 'part-0-hash.json'] = json.dumps(bad, ensure_ascii=False)
        def reviewer(model, prompt, content):
            self.calls.append((model, prompt, content))
            if '来源审校员' in prompt and '无来源的因果断言' in json.loads(content)['draft']['markdown']:
                return '{"approved":false,"issues":["第1页：来源并不支持这个因果关系"]}'
            return knowledge_response(model, prompt, content)
        context.runner = reviewer
        compiler = self.compiler(context)  # Capture the rejecting fake runner before invoking review.
        draft, review = compiler._summary('part-0-hash', data, [1, 2], [])
        self.assertNotIn('无来源的因果断言', draft['markdown'])
        self.assertTrue(review['approved'])
        self.assertEqual(3, context.status()['requests'])
        bad_key = compiler._review_key('part-0-hash-review.json', {**data, 'draft': bad})
        self.assertFalse(json.loads(self.stored[compiler.prefix + bad_key])['approved'])
        self.assertNotEqual(bad_key, compiler._review_key('part-0-hash-review.json', {**data, 'draft': draft}))

    def test_immutable_saved_response_tamper_is_not_replaced(self):
        context = self.context()
        self.invoke(self.compiler(context), context)
        attempt = json.loads(self.ledger.read_text())['authorized_scopes']['explicit-operation']['attempts'][0]
        response_path = context._response_path(attempt)
        response_path.write_text('manual edit')
        with self.assertRaises(RecoveryBlocked):
            self.invoke(self.compiler(context), context)
        self.assertEqual('manual edit', response_path.read_text())
        self.assertEqual(1, len(self.calls))

    def test_scope_source_node_binding_not_prompt_date_error_identity(self):
        context = self.context()
        self.assertTrue(context.registered('part-0-any-version-content-recovery-any-date.json'))
        self.assertFalse(context.registered('part-1-any-version.json'))
        with self.assertRaises(RecoveryBlocked):
            context.for_source('not-authorized')
        with self.assertRaises(RecoveryBlocked):
            self.context(max_calls=9)
        with self.assertRaises(ValueError):
            self.context(max_calls=11)

    def test_offline_never_calls_provider_even_without_saved_response(self):
        context = self.context(offline=True)
        with self.assertRaises(RecoveryBlocked):
            self.invoke(self.compiler(context), context)
        self.assertEqual(0, context.status()['requests'])
        self.assertEqual([], self.calls)

    def test_current_structural_validation_zero_network_no_metadata_filling(self):
        context = self.context(offline=True)
        compiler = self.compiler(context)
        draft = self.draft()
        data = {'pages': [{'page': 1}, {'page': 2}], 'topic_ids': []}
        original = copy.deepcopy(draft)
        result = context.recover(compiler, 'part-0-hash.json', SUMMARY_PROMPT, data,
            lambda value: validate_summary(value, [1, 2]), draft, compiler.prefix + 'part-0-hash.json')
        self.assertEqual(original, result)
        self.assertEqual([], self.calls)
        self.assertEqual(0, context.status()['requests'])

    def test_full_pages_topics_citations_length_are_real_gates(self):
        for key, value in (('covered_pages', [1]), ('covered_topics', ['wrong']),
                           ('topics', [{'name': 'unsupported', 'pages': [3]}]),
                           ('markdown', 'unsupported ' * 7 + '[课件 p.3]'),
                           ('markdown', '正文' * 2600 + '[课件 p.1]')):
            draft = self.draft()
            draft[key] = value
            with self.subTest(key=key, value=str(value)[:50]), self.assertRaises(ValueError):
                validate_summary(draft, [1, 2])

    def test_frozen_slots_restart_changed_error_no_new_allowance(self):
        draft = self.draft()
        draft['covered_pages'] = [1]
        def bad_request(*args):
            self.calls.append(args)
            return json.dumps(draft)
        context = self.context(runner=bad_request)
        compiler = self.compiler(context)
        data = {'pages': [{'page': 1}, {'page': 2}], 'topic_ids': []}
        for prompt in (SUMMARY_PROMPT, SUMMARY_PROMPT + '\nchanged error/date instructions'):
            with self.assertRaises(RecoveryBudgetExhausted):
                context.recover(compiler, 'part-0-hash.json', prompt, data,
                    lambda v: validate_summary(v, [1, 2]), draft, compiler.prefix + 'part-0-hash.json')
        self.assertEqual(2, len(self.calls))
        changed = {**data, 'pages': [{'page': 1, 'source_text': 'changed'}, {'page': 2}]}
        with self.assertRaisesRegex(RecoveryBlocked, 'evidence changed'):
            context.recover(compiler, 'part-0-new-hash.json', SUMMARY_PROMPT, changed,
                lambda v: validate_summary(v, [1, 2]), draft, compiler.prefix + 'part-0-new-hash.json')
        self.assertEqual(2, context.status()['requests'])

    def test_merge_recovers_all_three_accepted_summaries_without_regenerating_pages(self):
        context = self.context(registrations={'source-A': ['merge-0-0']})
        summaries = []
        for page in (1, 2, 3):
            value = json.loads(knowledge_response('deepseek-chat', SUMMARY_PROMPT,
                json.dumps({'pages': [{'page': page}], 'topic_ids': []})))
            value['topics'][0]['name'] = '主题' + str(page)
            value['markdown'] += '\n主题' + str(page) + '的来源条件 [课件 p.' + str(page) + ']'
            summaries.append(value)
        data = {'kind': 'concise_course_summary', 'input_summaries': summaries,
                'topic_ids': ['first', 'middle', 'last'], 'source_budget': summary_budget(50000, legacy=True)}
        old = self.draft()
        old['covered_pages'] = [1, 2, 3]
        old['covered_topics'] = data['topic_ids']
        old['topics'] = [copy.deepcopy(s['topics'][0]) for s in summaries]
        old['markdown'] = '\n'.join(s['markdown'] for s in summaries) + '重复观点' * 1300
        before = copy.deepcopy(summaries)
        def merge_request(model, prompt, content):
            payload = json.loads(content)
            self.calls.append((model, prompt, content))
            self.assertNotIn('pages', payload)
            self.assertEqual(before, payload['input_summaries'])
            if '来源审校员' in prompt:
                self.assertIn('主题3', payload['draft']['markdown'])
                return '{"approved":true,"issues":[]}'
            self.assertEqual(4300, payload['source_budget']['target_chars'])
            result = copy.deepcopy(old)
            result['markdown'] = ('## 课程主线\n主题1定义变量与关系，为主题2约束判断提供前提；主题3将满足条件的关系用于查询。 [课件 p.1]\n'
                '## 核心知识\n主题2要求明确约束，再作可行性判断。 [课件 p.2]\n'
                '## 适用条件与易错点\n主题3只能在前述条件内应用，不能把推导当作原文或省略约束。 [课件 p.3]')
            return json.dumps(result, ensure_ascii=False)
        context.runner = merge_request
        compiler = self.compiler(context)
        self.stored[compiler.prefix + 'merge-0-0-hash.json'] = json.dumps(old, ensure_ascii=False)
        draft, review = compiler._summary('merge-0-0-hash', data, [1, 2, 3], data['topic_ids'])
        self.assertLessEqual(len(draft['markdown']), 4850)
        self.assertEqual([1, 2, 3], draft['covered_pages'])
        self.assertEqual(before, summaries)
        self.assertTrue(review['approved'])
        self.assertEqual(2, context.status()['requests'])

    def test_protected_commit_preserves_manual_markdown(self):
        note = Path(self.temp.name) / 'manual.md'
        note.write_text('manual edits')
        context = self.context(offline=True)
        def commit(values):
            if any(name.endswith('part-0-hash.json') for name in values):
                raise RecoveryBlocked('manual output hash changed')
            self.stored.update(values)
        compiler = SummaryCompiler(self.request, self.stored.get, commit, authorized_recovery=context)
        with self.assertRaises(RecoveryBlocked):
            context.recover(compiler, 'part-0-hash.json', SUMMARY_PROMPT,
                {'pages': [{'page': 1}, {'page': 2}]}, lambda v: validate_summary(v, [1, 2]),
                self.draft(), compiler.prefix + 'part-0-hash.json')
        self.assertEqual('manual edits', note.read_text())
        self.assertEqual([], self.calls)

    def test_only_failed_part_repaired_approved_sibling_reused_old_counters_retained(self):
        context = self.context()
        compiler = self.compiler(context)
        legacy = SummaryCompiler(self.request, self.stored.get, self.stored.update)
        self.assertEqual(legacy.prefix, compiler.prefix)
        entries = [dict(page=n, source_text='原文' * 13000, text='视觉') for n in (1, 2)]
        names = []
        for index, entry in enumerate(entries):
            data = {'kind': 'concise_course_summary', 'pages': [entry], 'topic_ids': [],
                    'source_budget': summary_budget(source_chars(entry['source_text']), legacy=True)}
            identity = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            name = f'part-{index}-{identity}'
            names.append(name)
            draft = json.loads(knowledge_response('deepseek-chat', SUMMARY_PROMPT, json.dumps(data)))
            if index == 1:
                self.stored[compiler.prefix + name + '.json'] = json.dumps(draft, ensure_ascii=False)
                review_key = compiler._review_key(name + '-review.json', {**data, 'draft': draft})
                self.stored[compiler.prefix + review_key] = '{"approved":true,"issues":[]}'
            else:
                draft['topics'][0]['pages'] = [99]
                prompt = SUMMARY_PROMPT + '\n数学记号、符号方向和适用条件须遵循这份原文；只概括已有内容，不自行补证明或改用其他教材约定。' + CHECKPOINT_LENGTH_GUIDANCE
                input_hash = hashlib.sha256((prompt + json.dumps(data, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
                base = compiler.prefix + name + '.json'
                self.stored[base + '-resume-v1-' + input_hash[:12] + '.json'] = json.dumps(
                    {'input': input_hash, 'step': 4, 'draft': json.dumps(draft), 'error': '主题讲义页码无效。'})
                self.stored[base + '-old-automatic.json'] = '{"step":2,"limit":2}'
                self.stored[base + '-old-content.json'] = '{"step":2,"limit":2}'
        before = copy.deepcopy(self.stored)
        body, draft, report = compiler.compile(entries)
        self.assertEqual([1, 2], report['covered_pages'])
        self.assertLessEqual(len(body), 5000)
        for name, raw in before.items():
            self.assertEqual(raw, self.stored[name])
        part_requests = [json.loads(content) for _, prompt, content in self.calls
                         if prompt.startswith(SUMMARY_PROMPT) and 'pages' in json.loads(content)]
        self.assertEqual(1, len(part_requests))
        self.assertEqual([1], [p['page'] for p in part_requests[0]['pages']])
        calls = len(self.calls)
        compiler.compile(entries)
        self.assertEqual(calls, len(self.calls))


if __name__ == '__main__':
    unittest.main()
