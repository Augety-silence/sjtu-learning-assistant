"""精炼总结的硬长度、全输入覆盖、审校和增量恢复；合成测试非真实API。"""
import json
import unittest
from office_fixtures import knowledge_response
from sjtu_learning_assistant.knowledge_summary import SummaryCompiler, SUMMARY_PROMPT, validate_summary


class KnowledgeSummaryTests(unittest.TestCase):
    def draft(self):
        return json.loads(knowledge_response('deepseek-chat', SUMMARY_PROMPT,
                          json.dumps({'pages': [{'page': 1}, {'page': 2}], 'topic_ids': []})))

    def test_length_is_hard_gate_not_clipping(self):
        draft = self.draft()
        draft['markdown'] += '无关重复内容' * 500
        with self.assertRaisesRegex(ValueError, '不能截断'):
            validate_summary(draft, [1, 2])

    def test_merge_cannot_drop_later_input_topics_or_pages(self):
        draft = self.draft()
        with self.assertRaisesRegex(ValueError, '遗漏输入主题'):
            validate_summary(draft, [1, 2], ['first', 'last'])
        draft['covered_topics'] = ['first', 'last']
        self.assertIs(draft, validate_summary(draft, [1, 2], ['first', 'last']))
        draft['topics'][0]['pages'] = [1]
        with self.assertRaisesRegex(ValueError, '遗漏页面'):
            validate_summary(draft, [1, 2], ['first', 'last'])

    def test_all_pages_batched_then_reviewed_and_zero_call_resume(self):
        stored, seen = {}, []
        def request(model, prompt, data):
            seen.append((model, prompt, data))
            return knowledge_response(model, prompt, data)
        entries = [dict(page=n, source_text='原文' * 10_000, text='视觉') for n in range(1, 7)]
        compiler = SummaryCompiler(request, stored.get, stored.update)
        body, plan, report = compiler.compile(entries)
        self.assertLessEqual(len(body), 2000)
        self.assertEqual(list(range(1, 7)), report['covered_pages'])
        self.assertTrue(all(row['approved'] for row in report['checks']))
        self.assertTrue(any('input_summaries' in json.loads(data) for _, prompt, data in seen if prompt.startswith(SUMMARY_PROMPT)))
        count = len(seen)
        self.assertEqual((body, plan, report), compiler.compile(entries))
        self.assertEqual(count, len(seen))

    def test_long_response_repaired_and_long_bad_cache_revalidated(self):
        stored, calls = {}, []
        def request(model, prompt, data):
            calls.append(prompt)
            result = json.loads(knowledge_response(model, prompt, data))
            if len(calls) == 1:
                result['markdown'] = '重复' * 2000
            return json.dumps(result, ensure_ascii=False)
        compiler = SummaryCompiler(request, stored.get, stored.update)
        entries = [dict(page=1, text='定义')]
        body = compiler.compile(entries)[0]
        self.assertLessEqual(len(body), 2000)
        self.assertIn('不能截断', calls[1])
        key = next(n for n in stored if n.endswith('.json') and '-review' not in n and '-invalid' not in n and '-retry' not in n)
        draft = json.loads(stored[key])
        draft['markdown'] = '重复' * 2000
        stored[key] = json.dumps(draft)
        self.assertEqual(body, compiler.compile(entries)[0])

    def test_rejected_review_never_publishes(self):
        def request(model, prompt, data):
            if '来源审校员' in prompt:
                return '{"approved":false,"issues":["p.1：结论不成立"]}'
            return knowledge_response(model, prompt, data)
        with self.assertRaisesRegex(ValueError, '审校未通过'):
            SummaryCompiler(request, {}.get, lambda _: None).compile([dict(page=1, text='定义')])
