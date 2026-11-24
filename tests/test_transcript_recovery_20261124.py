"""定向字幕恢复使用合成来源；拒绝、付费响应和预约跨重启保留。"""
import copy
import json
import unittest

from sjtu_learning_assistant.transcript_notes import (
    PROMPT_VERSION, SummaryDeferred, _apply_content_patch, compile_summary,
)


RAW = ('WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n老师讲解机会成本。\n\n'
       '00:00:05.000 --> 00:00:09.000\n老师强调比较相关成本。\n')


class RecoveryClient:
    def __init__(self, *, initial_text='投注总数为10。', bad_patch=False, timeout=False):
        self.initial_text = initial_text
        self.bad_patch = bad_patch
        self.timeout = timeout
        self.calls = []
        self.cache = {}

    def chat_completion(self, messages, **kwargs):
        data = json.loads(messages[-1]['content'])
        self.calls.append(data)
        if data.get('content_patch'):
            assert sum('-reservation-' in key for key in self.cache) > 0
            if self.timeout:
                raise TimeoutError('合成修复超时')
            ids = ['不存在的ID'] if self.bad_patch else [data['cues'][-1]['cue_id']]
            value = {'sections': [{'index': 0, 'title': '成本决策',
                                  'text': '老师强调比较相关成本，字幕未提供投注总数。', 'cue_ids': ids}]}
        elif 'task' in data:
            passed = data.get('review_scope') == 'citation-block-v3' and all(
                '为10' not in section['text'] for section in data['draft']['sections'])
            value = {'approved': passed, 'covered_chunks': data['covered_chunks'],
                     'issues': [] if passed else ['投注总数10在当前来源无依据。']}
            if data.get('review_scope') == 'citation-block-v3':
                assert data['check_section_indexes'] == [0]
                assert '其他块引用的事实不可因未在本块出现而拒绝' in messages[0]['content']
        else:
            value = {'covered_chunks': data['covered_chunks'], 'sections': [{
                'title': '成本决策', 'text': self.initial_text, 'cue_ids': [data['cues'][-1]['cue_id']]}]}
        return {'content': json.dumps(value, ensure_ascii=False), 'finish_reason': 'stop'}


class TranscriptRecoveryTests(unittest.TestCase):
    def options(self, client, **extra):
        return dict(cached=client.cache.get, commit=lambda key, value: client.cache.setdefault(key, value),
                    auto_recovery=True, content_recovery=True, **extra)

    def test_cross_block_rejection_gets_independent_scoped_review_and_restart_is_free(self):
        client = RecoveryClient(initial_text='老师强调比较相关成本。')
        body, report = compile_summary(RAW, client, **self.options(client))
        saved = copy.deepcopy(client.cache)
        again, repeat = compile_summary(RAW, client, **self.options(client))
        self.assertEqual(body, again)
        self.assertEqual(4, len(client.calls))
        self.assertEqual(0, repeat['calls'])
        self.assertEqual(saved, client.cache)
        self.assertEqual(PROMPT_VERSION, report['prompt_version'])
        self.assertEqual([0], report['reviewed_chunks'])

    def test_real_missing_fact_is_patched_then_reviewed_and_all_responses_remain(self):
        client = RecoveryClient()
        body, report = compile_summary(RAW, client, **self.options(client))
        self.assertNotIn('为10', body)
        self.assertEqual([0], report['covered_chunks'])
        self.assertLessEqual(report['summary_chars'], 5000)
        self.assertEqual(6, len(client.calls))
        self.assertEqual(1, sum(bool(data.get('content_patch')) for data in client.calls))
        response_keys = [key for key in client.cache if key.endswith('-response-v1')]
        self.assertEqual(6, len(response_keys))
        again, repeat = compile_summary(RAW, client, **self.options(client))
        self.assertEqual(body, again)
        self.assertEqual(0, repeat['calls'])
        self.assertEqual(6, len(client.calls))

    def test_forged_patch_ids_never_pass_and_two_slots_are_not_refilled(self):
        client = RecoveryClient(bad_patch=True)
        for _ in range(4):
            with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
                compile_summary(RAW, client, **self.options(client))
        self.assertEqual(2, sum(bool(data.get('content_patch')) for data in client.calls))
        self.assertEqual(6, len(client.calls))
        self.assertEqual(2, sum('-reservation-' in key for key in client.cache))

    def test_unknown_timeout_responses_are_reserved_before_call_and_not_paid_again(self):
        client = RecoveryClient(timeout=True)
        for _ in range(2):
            with self.assertRaises(TimeoutError):
                compile_summary(RAW, client, **self.options(client))
        with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
            compile_summary(RAW, client, **self.options(client))
        self.assertEqual(2, sum(bool(data.get('content_patch')) for data in client.calls))
        self.assertEqual(6, len(client.calls))

    def test_one_call_slices_resume_saved_patch_and_review_without_duplicate_payment(self):
        client = RecoveryClient()
        for _ in range(8):
            try:
                body, report = compile_summary(RAW, client, **self.options(client, max_calls=1))
                break
            except SummaryDeferred:
                pass
        else:
            self.fail('合成修复应在有界切片中完成')
        self.assertNotIn('为10', body)
        self.assertEqual(6, len(client.calls))
        self.assertEqual(1, report['calls'])

    def test_patch_preserves_other_sections_and_coverage_but_rejects_forged_or_overlong_content(self):
        draft = {'covered_chunks': [0, 1], 'sections': [
            {'title': '第一块', 'text': '旧稿', 'evidence': [{'cue_id': 'a', 'quote': '第一块原话'}]},
            {'title': '第二块', 'text': '保留原稿', 'evidence': [{'cue_id': 'b', 'quote': '第二块原话'}]}]}
        allowed = {'a': '第一块原话', 'b': '第二块原话'}
        timing = {'a': 0, 'b': 1000}
        patch = {'covered_chunks': [99], 'sections': [{'index': 0, 'title': '第一块', 'text': '修复稿', 'cue_ids': ['a']}]}
        result = _apply_content_patch(draft, patch, {'a'}, [0, 1], allowed, timing)
        self.assertEqual([0, 1], result['covered_chunks'])
        self.assertEqual(draft['sections'][1], result['sections'][1])
        self.assertEqual('旧稿', draft['sections'][0]['text'])
        for section in ({'index': 1, 'title': '错块', 'text': '错块', 'cue_ids': ['a']},
                        {'index': 0, 'title': '错ID', 'text': '错ID', 'cue_ids': ['x']},
                        {'index': 0, 'title': '错类型', 'text': '错类型', 'cue_ids': [{}]},
                        {'index': 0, 'title': '过长', 'text': '字' * 5001, 'cue_ids': ['a']}):
            with self.subTest(section=section), self.assertRaises(ValueError):
                _apply_content_patch(draft, {'sections': [section]}, {'a'}, [0, 1], allowed, timing)

    def test_mixed_section_keeps_existing_other_block_refs_but_cannot_add_unseen_refs(self):
        draft = {'covered_chunks': [0, 1], 'sections': [{
            'title': '跨块主题', 'text': '已有事实',
            'evidence': [{'cue_id': 'a', 'quote': '甲原话'}, {'cue_id': 'b', 'quote': '乙原话'}]}]}
        allowed, timing = {'a': '甲原话', 'b': '乙原话', 'c': '丙原话'}, {'a': 0, 'b': 1000, 'c': 2000}
        patch = {'sections': [{'index': 0, 'title': '跨块主题', 'text': '修复甲且保留乙事实', 'cue_ids': ['a', 'b']}]}
        result = _apply_content_patch(draft, patch, {'a'}, [0, 1], allowed, timing)
        self.assertEqual(['a', 'b'], [e['cue_id'] for e in result['sections'][0]['evidence']])
        patch['sections'][0]['cue_ids'].append('c')
        with self.assertRaises(ValueError):
            _apply_content_patch(draft, patch, {'a'}, [0, 1], allowed, timing)


if __name__ == '__main__':
    unittest.main()
