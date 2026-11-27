"""Narrow zero-cost formatting/local-candidate regressions; fake requests only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from office_fixtures import knowledge_response
from sjtu_learning_assistant.knowledge_summary import SummaryCompiler, SUMMARY_PROMPT, validate_summary
from sjtu_learning_assistant.segment_recovery import (
    AuthorizedSegmentRecovery, RecoveryBlocked, remove_strong_emphasis, _sha, _wire,
)


class LocalCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Path(self.temp.name).resolve() / 'requests.json'
        self.ledger.write_text(json.dumps({'requests': 46, 'limit': 64, 'results': []}))
        self.stored, self.calls = {}, []
        self.data = {'pages': [{'page': 1, 'source_text': '关系模型定义'}, {'page': 2, 'source_text': '约束条件'}], 'topic_ids': []}
        self.good = json.loads(knowledge_response('deepseek-chat', SUMMARY_PROMPT, json.dumps(self.data)))
        self.bad = copy.deepcopy(self.good)
        self.bad['topics'][0]['pages'] = [3]
        self.input_hash = _sha(_wire(AuthorizedSegmentRecovery._source_input(self.data)))
    def request(self, model, prompt, content):
        self.calls.append((model, prompt, content))
        return knowledge_response(model, prompt, content)
    def context(self, **kwargs):
        return AuthorizedSegmentRecovery(self.ledger, scope='existing-user-operation', max_calls=10,
            registrations={'source-A': ['part-0']}, allow_ai=True, runner=self.request,
            **kwargs).for_source('source-A')
    def compiler(self, context):
        return SummaryCompiler(self.request, self.stored.get, self.stored.update, authorized_recovery=context)
    def local(self, **kwargs):
        return {'source-A': {'part-0': {'input_hash': kwargs.get('input_hash', self.input_hash),
                                      'candidate': copy.deepcopy(kwargs.get('candidate', self.good))}}}

    def test_paired_strong_removal_preserves_all_code_math_escaped_and_unmatched_text(self):
        protected = ('`**inline**`\n```python\n**fenced**\n```\n~~~\n__tilde__\n~~~\n'
                     '$x ** y$ $$__formula__$$ \\(a ** b\\) \\[__equation__\\]')
        text = '**facts** __conditions__ [课件 p.1] ' + protected + '\n\\**escaped** unmatched **'
        expected = 'facts conditions [课件 p.1] ' + protected + '\n\\**escaped** unmatched **'
        self.assertEqual(expected, remove_strong_emphasis(text))

    def test_saved_slot_formatting_recovery_after_two_generations_no_new_call_or_raw_mutation(self):
        context = self.context()
        compiler = self.compiler(context)
        long = copy.deepcopy(self.good)
        long['markdown'] += '\n' + '事实条件' * 1050 + '**强调**' * 100
        self.assertGreater(len(long['markdown']), 4850)
        self.assertLessEqual(len(remove_strong_emphasis(long['markdown'])), 4850)
        for i in range(2):
            with context.node_scope('part-0-hash'):
                compiler.request('deepseek-chat', SUMMARY_PROMPT, json.dumps({**self.data, 'attempt': i}))
        key = context._key(compiler, 'part-0-hash.json')
        self.stored[key + '-slot-0-response.txt'] = json.dumps(self.bad, ensure_ascii=False)
        self.stored[key + '-slot-1-response.txt'] = json.dumps(long, ensure_ascii=False)
        original_raw = self.stored[key + '-slot-1-response.txt']
        before = context.status()['requests']
        result = context.recover(compiler, 'part-0-hash.json', SUMMARY_PROMPT, self.data,
            lambda v: validate_summary(v, [1, 2]), self.bad, compiler.prefix + 'part-0-hash.json')
        self.assertLessEqual(len(result['markdown']), 4850)
        self.assertEqual(before, context.status()['requests'])
        self.assertEqual(original_raw, self.stored[key + '-slot-1-response.txt'])
        self.assertEqual(2, len(self.calls))
        records = [json.loads(raw) for name, raw in self.stored.items() if '-candidate-' in name]
        self.assertTrue(any(r['derivation'] == 'remove-paired-strong-markers-only' for r in records))

    def test_local_candidate_hash_binding_copy_provenance_and_real_source_review(self):
        candidates = self.local()
        context = self.context(local_candidates=candidates)
        candidates['source-A']['part-0']['candidate']['markdown'] = 'caller mutation'
        compiler = self.compiler(context)
        self.stored[compiler.prefix + 'part-0-hash.json'] = json.dumps(self.bad)
        draft, review = compiler._summary('part-0-hash', self.data, [1, 2], [])
        self.assertEqual(self.good, draft)
        self.assertTrue(review['approved'])
        self.assertEqual(1, len(self.calls))
        self.assertIn('来源审校员', self.calls[0][1])
        records = [json.loads(raw) for name, raw in self.stored.items() if '-candidate-' in name]
        self.assertTrue(any(r['origin'] == 'explicit-local-candidate' and r['requires_source_review'] for r in records))
        self.assertEqual(1, context.status()['requests'])

    def test_wrong_hash_unregistered_source_and_approval_flags_rejected(self):
        context = self.context(local_candidates=self.local(input_hash='0' * 64))
        compiler = self.compiler(context)
        with self.assertRaisesRegex(RecoveryBlocked, 'input hash differs'):
            context.recover(compiler, 'part-0-hash.json', SUMMARY_PROMPT, self.data,
                lambda v: validate_summary(v, [1, 2]), self.bad, compiler.prefix + 'part-0-hash.json')
        for bad_mapping in ({'unregistered': self.local()['source-A']},
                            {'source-A': {'part-1': self.local()['source-A']['part-0']}}):
            with self.assertRaises(RecoveryBlocked):
                self.context(local_candidates=bad_mapping)
        flagged = self.local()
        flagged['source-A']['part-0']['approved_by_human'] = True
        with self.assertRaises(RecoveryBlocked):
            self.context(local_candidates=flagged)
        self.assertEqual([], self.calls)

    def test_invalid_or_rejected_local_candidate_never_requests_replacement_generation(self):
        context = self.context(local_candidates=self.local(candidate=self.bad))
        compiler = self.compiler(context)
        with self.assertRaisesRegex(RecoveryBlocked, 'local candidate rejected'):
            context.recover(compiler, 'part-0-hash.json', SUMMARY_PROMPT, self.data,
                lambda v: validate_summary(v, [1, 2]), self.bad, compiler.prefix + 'part-0-hash.json')
        self.assertEqual([], self.calls)
        context = self.context(local_candidates=self.local())
        compiler = self.compiler(context)
        context.reject(compiler, 'part-0-hash', self.good, ['第1页：事实错误'])
        with self.assertRaisesRegex(RecoveryBlocked, 'Source review rejected'):
            context.recover(compiler, 'part-0-hash.json', SUMMARY_PROMPT, self.data,
                lambda v: validate_summary(v, [1, 2]), self.bad, compiler.prefix + 'part-0-hash.json')
        self.assertEqual([], self.calls)

    def test_formatting_cannot_repair_bad_citations_or_change_formula_content(self):
        context = self.context(offline=True)
        compiler = self.compiler(context)
        long = copy.deepcopy(self.good)
        long['markdown'] = '事实条件' * 1050 + '**强调**' * 100 + ' [课件 p.3] $a ** b$'
        with self.assertRaises(RecoveryBlocked):
            context.recover(compiler, 'part-0-hash.json', SUMMARY_PROMPT, self.data,
                lambda v: validate_summary(v, [1, 2]), long, compiler.prefix + 'part-0-hash.json')
        self.assertEqual('事实条件' * 1050 + '强调' * 100 + ' [课件 p.3] $a ** b$', remove_strong_emphasis(long['markdown']))
        self.assertEqual([], self.calls)


if __name__ == '__main__':
    unittest.main()
