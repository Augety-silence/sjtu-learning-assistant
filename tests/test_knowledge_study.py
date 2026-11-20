"""完整讲义契约测试；响应为合成数据，不代表模型事实验收。"""
import json
import unittest

from sjtu_learning_assistant.knowledge_study import (
    StudyCompiler, canonical_citations, cited_pages, source_note, validate_plan, validate_chapter, validate_review, validate_tables,
)
from office_fixtures import knowledge_response


class KnowledgeStudyTests(unittest.TestCase):
    def test_full_chapter_retains_more_than_eight_points_with_bounded_field_errors(self):
        draft = dict(covered_pages=[1], markdown='完整知识正文。' * 20 + '[课件 p.1]',
                     key_points=['知识要点 [课件 p.1]'] * 24, gaps=[])
        self.assertEqual(24, len(validate_chapter(draft, [1])['key_points']))
        draft['key_points'].append('超额要点 [课件 p.1]')
        with self.assertRaisesRegex(ValueError, 'key_points.*1–24.*600'):
            validate_chapter(draft, [1])
        draft['key_points'] = ['有效要点 [课件 p.1]']
        draft['gaps'] = ['x' * 601]
        with self.assertRaisesRegex(ValueError, 'gaps.*0–80.*600'):
            validate_chapter(draft, [1])

    def test_independent_review_model_and_identity_cannot_reuse_author_approval(self):
        from unittest.mock import patch
        import sjtu_learning_assistant.knowledge_study as module
        seen, stored = [], dict()
        def request(model, prompt, data):
            seen.append(model)
            return knowledge_response(model, prompt, data)
        compiler = StudyCompiler(request, stored.get, stored.update)
        key = compiler._review_key('review.json', dict(draft='稿件'))
        with patch.object(module, 'REVIEW_MODEL', 'different-reviewer'):
            self.assertNotEqual(key, compiler._review_key('review.json', dict(draft='稿件')))
        compiler._review('review.json', dict(draft='稿件'))
        self.assertEqual(['qwen3.8-27b'], seen)

    def test_exercise_correction_is_reviewed_and_success_resumes_without_calls(self):
        seen, stored = [], dict()
        def request(model, prompt, data):
            payload = json.loads(data)
            seen.append((prompt, payload))
            if '来源审校员' in prompt and payload.get('kind') == 'teaching_exercises':
                if '错误最优解' in payload['draft']['exercises'][0]['question']:
                    return json.dumps(dict(approved=False, issues=['第1页：题目给定最优解不成立，须改正题设并检查可行性']))
            result = json.loads(knowledge_response(model, prompt, data))
            if '"exercises"' in prompt and '来源审校员' not in prompt:
                result['exercises'][0]['question'] = '已核验条件完整的自编练习题。' if '这是自测题纠错' in prompt else '错误最优解，请证明这个条件。'
            return json.dumps(result)
        compiler = StudyCompiler(request, stored.get, stored.update)
        entries = [dict(page=1, text='原文主题')]
        body, _, report = compiler.compile(entries)
        self.assertTrue(report['exercise_review']['approved'])
        repairs = [row for row in seen if '这是自测题纠错' in row[0]]
        self.assertEqual(1, len(repairs))
        self.assertIn('题目给定最优解不成立', repairs[0][1]['corrections'][0])
        self.assertNotIn('错误最优解', body)
        count = len(seen)
        self.assertEqual(body, compiler.compile(entries)[0])
        self.assertEqual(count, len(seen))

    def test_exercise_repair_failure_is_bounded_and_never_returns_body(self):
        authors, stored = [], dict()
        def request(model, prompt, data):
            if '来源审校员' in prompt and json.loads(data).get('kind') == 'teaching_exercises':
                return json.dumps(dict(approved=False, issues=['第1页题设仍不成立']))
            if '"exercises"' in prompt:
                authors.append(prompt)
            return knowledge_response(model, prompt, data)
        with self.assertRaisesRegex(ValueError, '自测题审校未通过'):
            StudyCompiler(request, stored.get, stored.update).compile([dict(page=1, text='主题')])
        self.assertEqual(2, len(authors))
        self.assertTrue(any('exercises-' in key and '-repair-' in key for key in stored))

    def test_downstream_caches_change_when_reviewed_chapter_points_change(self):
        stored, seen = dict(), []
        def request(model, prompt, data):
            seen.append(prompt)
            return knowledge_response(model, prompt, data)
        compiler = StudyCompiler(request, stored.get, stored.update)
        entries = [dict(page=1, text='主题')]
        compiler.compile(entries)
        chapter_key = compiler.prefix + 'chapter-001-001.json'
        chapter = json.loads(stored[chapter_key])
        chapter['key_points'] = ['修正后的知识与条件 [课件 p.1]']
        stored[chapter_key] = json.dumps(chapter)
        count = len(seen)
        body, _, _ = compiler.compile(entries)
        self.assertIn('修正后的知识与条件', body)
        self.assertGreater(len(seen), count)
        self.assertEqual(2, len([key for key in stored if '/revision-0-0-' in key]))

    def test_summary_page_repair_names_page_and_never_reinjects_rejected_facts(self):
        seen, stored = [], dict()
        old = {'covered_pages': [31, 32], 'markdown': '旧错误数学 9999。' * 20,
               'key_points': ['对偶总结 [课件 p.31]'], 'gaps': [],
               'context_pages': [{'page': 31, 'reason': '课程总结页，列出三个主题，无具体公式或例题'},
                                 {'page': 32, 'reason': '致谢页，无实质内容'}]}
        def request(model, prompt, data):
            seen.append((prompt, json.loads(data)))
            return json.dumps({'covered_pages': [31, 32], 'markdown': '总结三个核心知识主题及其联系。' * 12 + '[课件 p.31]',
                               'key_points': ['对偶总结 [课件 p.31]'], 'gaps': [],
                               'context_pages': [{'page': 32, 'reason': '致谢页，无实质内容'}]})
        compiler = StudyCompiler(request, stored.get, stored.update)
        stored[compiler.prefix + 'chapter.json-retry.json'] = json.dumps(old)
        data = {'chapter': '总结', 'pages': [{'page': 31, 'text': '三个核心知识主题'}, {'page': 32, 'text': 'Thank You'}]}
        with self.assertRaisesRegex(ValueError, '第 31 页'):
            validate_chapter(old, [31, 32])
        result = compiler._saved('chapter.json', '课程作者', data, lambda r: validate_chapter(r, [31, 32]))
        self.assertEqual([32], [row['page'] for row in result['context_pages']])
        self.assertEqual(1, len(seen))
        self.assertIn('第 31 页', seen[0][0])
        self.assertIn('不能因为没有具体公式或例题', seen[0][0])
        self.assertNotIn('9999', seen[0][0])
        self.assertEqual(data, seen[0][1])

    def test_rejected_old_coverage_cache_is_reauthored_from_current_evidence(self):
        stored, calls = {}, []
        def request(model, prompt, data):
            calls.append((prompt, json.loads(data)))
            return json.dumps({'covered_pages': [18], 'markdown': '原图中独立目标列与互补解。' * 20 + '[课件 p.18]', 'key_points': ['互补解 [课件 p.18]'], 'gaps': []})
        compiler = StudyCompiler(request, stored.get, stored.update)
        old = {'covered_pages': [18], 'markdown': '|y4|y5|\n|---|---|\n|9999|-3|0|\n' + '错表。' * 40 + '[课件 p.18]', 'key_points': ['互补解 [课件 p.18]'], 'gaps': []}
        name = compiler.prefix + 'chapter.json'
        stored[name + '-coverage.json'] = json.dumps(old)
        draft = {'covered_pages': [17, 18], 'markdown': '本章完整理论。' * 25 + '[课件 p.17]', 'key_points': ['理论 [课件 p.17]'], 'gaps': []}
        data = {'chapter': '互补解', 'pages': [{'page': 17, 'text': '理论'}, {'page': 18, 'text': '最新原图证据', 'visual_rechecked': True}]}
        result = compiler._coverage(name, data, draft, lambda r: validate_chapter(r, [17, 18]))
        self.assertEqual({17, 18}, cited_pages(result['markdown']))
        self.assertEqual(1, len(calls))
        self.assertNotIn('9999', calls[0][0])
        self.assertEqual([data['pages'][1]], calls[0][1]['pages'])
        self.assertFalse(any('review' in key for key in stored))
        self.assertEqual(result, compiler._coverage(name, data, draft, lambda r: validate_chapter(r, [17, 18])))
        self.assertEqual(1, len(calls))

    def test_coverage_failure_has_page_context_and_cannot_recurse(self):
        stored, calls = {}, []
        def request(model, prompt, data):
            calls.append(prompt)
            return json.dumps({'covered_pages': [18], 'markdown': '仍遗漏来源。' * 25, 'key_points': ['要点 [课件 p.18]'], 'gaps': []})
        compiler = StudyCompiler(request, stored.get, stored.update)
        draft = {'covered_pages': [17, 18], 'markdown': '理论。' * 40 + '[课件 p.17]', 'key_points': ['理论 [课件 p.17]'], 'gaps': []}
        data = {'chapter': '互补解', 'pages': [{'page': 17}, {'page': 18}]}
        with self.assertRaisesRegex(ValueError, r'缺失页 \[18\] 的主题补齐失败'):
            compiler._coverage(compiler.prefix + 'chapter.json', data, draft, lambda r: validate_chapter(r, [17, 18]))
        self.assertEqual(2, len(calls))

    def test_rechecked_evidence_reauthoring_omits_old_wrong_numbers(self):
        seen, stored = [], {}
        def request(model, prompt, data):
            seen.append(json.loads(data))
            return knowledge_response(model, prompt, data)
        compiler = StudyCompiler(request, stored.get, stored.update)
        data = {'chapter': '互补解', 'pages': [{'page': 1, 'text': '最新原图', 'visual_rechecked': True}]}
        compiler._rewrite('source-repair', data, {'markdown': '### 互补解\n旧错数 9999'}, ['第1页表格错列'], [1])
        self.assertNotIn('draft', seen[0])
        self.assertEqual(['### 互补解'], seen[0]['draft_structure'])
        self.assertNotIn('9999', json.dumps(seen[0]))

    def test_review_cache_identity_includes_draft_and_full_source_evidence(self):
        compiler = StudyCompiler(None, None, None)
        first = {'draft': {'markdown': '旧正文'}, 'pages': [{'text': '旧图文'}]}
        key = compiler._review_key('review.json', first)
        self.assertNotEqual(key, compiler._review_key('review.json', {**first, 'draft': {'markdown': '新正文'}}))
        self.assertNotEqual(key, compiler._review_key('review.json', {**first, 'pages': [{'text': '新图文'}]}))

    def test_ragged_table_repair_never_reinjects_failed_numbers_into_system_prompt(self):
        stored, seen = {}, []
        def request(model, prompt, data):
            seen.append((prompt, json.loads(data)))
            return '{"fixed":true}'
        compiler = StudyCompiler(request, stored.get, stored.update)
        stored[compiler.prefix + 'chapter.json'] = '{"fixed":false,"wrong_table":"9999"}'
        def validator(result):
            if not result['fixed']:
                raise ValueError('讲义表格列数与表头不一致')
            return result
        data = {'pages': [{'page': 1, 'text': '完整原图证据'}]}
        compiler._saved('chapter.json', '作者', data, validator)
        self.assertNotIn('9999', seen[0][0])
        self.assertIn('不再提供旧错误数值', seen[0][0])
        self.assertEqual(data, seen[0][1])

    def test_table_requires_independent_coefficient_column_and_respects_math_pipes(self):
        with self.assertRaisesRegex(ValueError, '表格列数'):
            validate_tables('| 基变量 | y4 | y5 | y1 | y2 | y3 | yb |\n|---|---|---|---|---|---|---|\n|Z|1|-3|0|0|5/2|0|30|')
        validate_tables('| 基变量 | Z系数 | y4 | y5 | y1 | y2 | y3 | yb |\n|---|---|---|---|---|---|---|---|\n|Z|1|-3|0|0|5/2|0|30|')
        validate_tables('| 范数 | 值 |\n|---|---|\n| $|x|$ | `a|b` |\n| \\| | 1 |')

    def test_title_transition_is_declared_but_source_review_is_still_required(self):
        draft = {'covered_pages': [24, 25], 'markdown': '完整知识内容。' * 20 + '[课件 p.25]', 'key_points': ['要点 [课件 p.25]'], 'gaps': [], 'context_pages': [{'page': 24, 'reason': '章节过渡页，仅含标题 Examples，无实质内容'}]}
        self.assertEqual(draft, validate_chapter(draft, [24, 25]))
        draft['context_pages'][0]['reason'] = '有定义的过渡页，不再整理'
        with self.assertRaisesRegex(ValueError, '不能排除知识页'):
            validate_chapter(draft, [24, 25])
        draft['context_pages'][0]['reason'] = '课程致谢页，仅 Thank You，无知识内容'
        self.assertEqual(draft, validate_chapter(draft, [24, 25]))

    def test_previously_saved_draft_is_repaired_when_new_validator_rejects_it(self):
        stored, seen = {}, []
        def request(model, prompt, data):
            seen.append(prompt)
            return '{"text":"已修复"}'
        compiler = StudyCompiler(request, stored.get, stored.update)
        stored[compiler.prefix + 'chapter.json'] = '{"text":"旧错列"}'
        def validate(result):
            if result['text'] != '已修复':
                raise ValueError('表格列数错误')
            return result
        result = compiler._saved('chapter.json', '作者', {}, validate)
        self.assertEqual('已修复', result['text'])
        self.assertIn('旧错列', seen[0])
        self.assertIn('表格列数错误', seen[0])
        self.assertEqual(result, json.loads(stored[compiler.prefix + 'chapter.json']))
        self.assertEqual(result, compiler._saved('chapter.json', '作者', {}, validate))
        self.assertEqual(1, len(seen))

    def test_rewrite_uses_new_evidence_and_issue_identity_not_old_failed_draft(self):
        stored, seen = {}, []
        def request(model, prompt, data):
            seen.append((prompt, json.loads(data)))
            return knowledge_response(model, prompt, data)
        compiler = StudyCompiler(request, stored.get, stored.update)
        data = {'chapter': '表格', 'pages': [{'page': 1, 'source_text': 'OCR 漏列', 'text': '新原图独立 Z 列'}]}
        draft = {'markdown': '旧错列表格'}
        revised, review = compiler._rewrite('source-repair', data, draft, ['第1页错列'], [1])
        self.assertTrue(review['approved'])
        self.assertIn('不得原样复写', seen[0][0])
        self.assertIn('审校意见不是事实', seen[0][0])
        self.assertEqual(data['pages'], seen[0][1]['pages'])
        self.assertEqual(draft, seen[0][1]['draft'])
        self.assertEqual(['第1页错列'], seen[0][1]['corrections'])
        self.assertEqual(revised, compiler._rewrite('source-repair', data, draft, ['第1页错列'], [1])[0])
        self.assertEqual(2, len(seen))
        compiler._rewrite('source-repair', data, draft, ['第1页新问题'], [1])
        self.assertEqual(4, len(seen))

    def test_source_repair_is_bounded_and_rejected_repetition_never_publishes(self):
        stored, authors = {}, []
        def request(model, prompt, data):
            if '来源审校员' in prompt:
                return '{"approved":false,"issues":["第1页表格仍错列"]}'
            if 'covered_pages' in prompt:
                authors.append(prompt)
            return knowledge_response(model, prompt, data)
        compiler = StudyCompiler(request, stored.get, stored.update, verify_sources=lambda entries, *_: entries)
        with self.assertRaisesRegex(ValueError, '审校未通过'):
            compiler.compile([{'page': 1, 'source_text': '原文', 'text': '新证据'}])
        self.assertEqual(4, len(authors))  # 初稿、普通纠错、原图后修订、最终一次纠错。
        self.assertFalse(any(name.endswith('document.md') for name in stored))
        self.assertTrue(any('final-repair' in name and '-review-' in name for name in stored))

    def test_source_conflict_rechecks_only_referenced_pages_and_reviews_new_evidence(self):
        stored, verified, seen = {}, [], []
        def request(model, prompt, data):
            payload = json.loads(data)
            seen.append(payload)
            if '来源审校员' in prompt and payload.get('pages') and payload['pages'][0]['text'] != '原图重新复核的证据':
                return '{"approved":false,"issues":["第1页：视觉表格错列，需核对原图"]}'
            return knowledge_response(model, prompt, data)
        def verify(entries, issues, key):
            verified.extend(entry['page'] for entry in entries)
            return [{**entry, 'text': '原图重新复核的证据'} for entry in entries]
        compiler = StudyCompiler(request, stored.get, stored.update, verify_sources=verify)
        entries = [{'page': 1, 'source_text': '原始文字', 'text': '旧视觉错误'}]
        body, _, report = compiler.compile(entries)
        self.assertEqual([1], verified)
        self.assertEqual([1], report['checks'][0]['verified_visual_pages'])
        self.assertTrue(report['checks'][0]['approved'])
        self.assertTrue(any(isinstance(p, dict) and p.get('pages') and p['pages'][0]['text'] == '原图重新复核的证据' for p in seen))
        count = len(seen)
        self.assertEqual(body, compiler.compile(entries)[0])
        self.assertEqual(count, len(seen))
        self.assertEqual([1, 1], verified)  # 回调读取复核缓存；已成功章节不再调用模型。
    def test_source_review_cannot_select_an_unrelated_page(self):
        stored = {}
        def request(model, prompt, data):
            if '来源审校员' in prompt:
                return '{"approved":false,"issues":["第499页需要原图"]}'
            return knowledge_response(model, prompt, data)
        compiler = StudyCompiler(request, stored.get, stored.update, verify_sources=lambda *_: self.fail('不能读取别的来源页'))
        with self.assertRaisesRegex(ValueError, '审校未通过'):
            compiler.compile([{'page': 1, 'source_text': '原文', 'text': '主题'}])
    def test_targeted_coverage_receives_full_missing_page_then_whole_section_requires_review(self):
        stored, calls = {}, []
        def request(model, prompt, data):
            decoded = json.loads(data)
            calls.append((prompt, decoded))
            if '定向补齐任务' in prompt:
                return json.dumps({'covered_pages': [18], 'markdown': '互补解表格与可行性说明。' * 20 + '[课件 p.18]', 'key_points': ['检查可行性 [课件 p.18]'], 'gaps': []})
            return json.dumps({'covered_pages': [17, 18], 'markdown': '互补解理论。' * 25 + '[课件 p.17]', 'key_points': ['理论 [课件 p.17]'], 'gaps': []})
        compiler = StudyCompiler(request, stored.get, stored.update)
        data = {'chapter': '互补解', 'pages': [{'page': 17, 'source_text': '理论'}, {'page': 18, 'source_text': '完整表格 -3 5/2', 'text': '图文证据'}]}
        result = compiler._saved('chapter.json', '', data, lambda r: validate_chapter(r, [17, 18]))
        self.assertEqual(3, len(calls))
        self.assertEqual([data['pages'][1]], calls[-1][1]['pages'])
        self.assertIn('完整表格 -3 5/2', calls[-1][1]['pages'][0]['source_text'])
        self.assertEqual({17, 18}, cited_pages(result['markdown']))
        self.assertFalse(any('review.json' in key for key in stored))
        # 失败草稿 + 已补齐页缓存续跑只组装草稿，不重复模型调用或伪造审校结论。
        stored.pop(compiler.prefix + 'chapter.json')
        restored = compiler._saved('chapter.json', '', data, lambda r: validate_chapter(r, [17, 18]))
        self.assertEqual(result, restored)
        self.assertEqual(3, len(calls))
        self.assertFalse(any('review.json' in key for key in stored))
    def test_formatting_never_saves_a_field_that_cannot_be_revalidated(self):
        draft = {'covered_pages': [1], 'markdown': '完整知识内容。' * 20 + '[课件 p.1]', 'key_points': ['要点 [课件 p.1]'], 'gaps': ['第1页' + '缺少' * 296]}
        with self.assertRaises(ValueError):
            validate_chapter(draft, [1])
    def test_explicit_gap_page_numbers_are_formatted_not_inferred_from_other_numbers(self):
        self.assertIn('[课件 p.5] [课件 p.6]', source_note('第 5 页和第 6 页仅给出问题，缺少模型。'))
        self.assertEqual('编号 02 条件缺失', source_note('编号 02 条件缺失'))
        self.assertIn('[课件 p.18]', source_note('课件 p.18 的表格列对应关系需核对。'))
        draft = {'covered_pages': [25], 'markdown': '完整知识内容。' * 20 + '[课件 p.25]', 'key_points': ['要点 [课件 p.25]'], 'gaps': ['第 2 页条件缺失']}
        with self.assertRaisesRegex(ValueError, '没有有效出处'):
            validate_chapter(draft, [25])

    def test_rejected_format_can_be_revalidated_but_never_skips_source_review(self):
        stored = {}
        compiler = StudyCompiler(lambda *_: self.fail('格式恢复不应再次调用 API'), stored.get, stored.update)
        name = compiler.prefix + 'chapter.json'
        draft = {'covered_pages': [1], 'markdown': '完整知识内容。' * 20 + '[课件 p.1]', 'key_points': ['要点 [课件 p.1]'], 'gaps': ['第 1 页条件缺失']}
        stored[name + '-retry.json'] = json.dumps(draft)
        restored = compiler._saved('chapter.json', '', {}, lambda r: validate_chapter(r, [1]))
        self.assertIn('[课件 p.1]', restored['gaps'][0])
        self.assertIn(name, stored)
        self.assertFalse(any('review.json' in key for key in stored))
    def test_compound_citations_normalize_without_changing_page_numbers(self):
        text = '结论 [课件 p.2, p.5–6]，依据 [课件 p.25、26]'
        self.assertEqual({2, 5, 6, 25, 26}, cited_pages(text))
        self.assertIn('[课件 p.2] [课件 p.5–6]', canonical_citations(text))
        self.assertEqual('[课件 p.2x]', canonical_citations('[课件 p.2x]'))

    def test_cover_page_is_declared_but_knowledge_pages_cannot_disappear(self):
        draft = {'covered_pages': [1, 2], 'markdown': '完整的知识内容。' * 20 + '[课件 p.2]', 'key_points': ['要点 [课件 p.2]'], 'gaps': [], 'context_pages': [{'page': 1, 'reason': '章节封面，仅课程主题'}]}
        self.assertEqual(draft, validate_chapter(draft, [1, 2]))
        draft['context_pages'][0]['reason'] = '此页有定理，省略'
        with self.assertRaisesRegex(ValueError, '不能排除知识页'):
            validate_chapter(draft, [1, 2])
    def test_correction_contains_previous_draft_and_exact_missing_page_without_changing_input(self):
        stored, calls = {}, []
        def request(model, prompt, data):
            calls.append((prompt, json.loads(data)))
            result = json.loads(knowledge_response(model, prompt, data))
            if 'covered_pages' in prompt and '上次结构错误' not in prompt:
                result['markdown'] = result['markdown'].replace('[课件 p.25]', '')
            return json.dumps(result, ensure_ascii=False)
        body, _, _ = StudyCompiler(request, stored.get, stored.update).compile([{'page': n, 'text': '主题', 'source_text': '原始证据'} for n in (1, 25)])
        correction = next((prompt, data) for prompt, data in calls if '上次结构错误' in prompt)
        self.assertIn('缺少 [课件 p.25]', correction[0])
        self.assertIn('之前草稿', correction[0])
        self.assertIn('pages', correction[1])
        self.assertIn('[课件 p.25]', body)
        self.assertTrue(any(name.endswith('-invalid.json') for name in stored))
    def test_resuming_invalid_draft_receives_previous_content_and_specific_correction(self):
        stored, requests = {}, []
        def request(model, prompt, data):
            requests.append((prompt, json.loads(data)))
            return json.dumps({'covered_pages': [18], 'markdown': '单纯形表的互补解不是可行解。' * 20 + '[课件 p.18]', 'key_points': ['互补解要检查可行性 [课件 p.18]'], 'gaps': []})
        compiler = StudyCompiler(request, stored.get, stored.update)
        stored[compiler.prefix + 'chapter.json-retry.json'] = json.dumps({'covered_pages': [18], 'markdown': '遗漏了具体单纯形表。' * 20, 'key_points': ['要点 [课件 p.18]'], 'gaps': []}, ensure_ascii=False)
        data = {'pages': [{'page': 18, 'source_text': '目标行 -3，互补解不可行'}]}
        compiler._saved('chapter.json', '作者', data, lambda r: validate_chapter(r, [18]))
        self.assertEqual(1, len(requests))
        self.assertEqual(data, requests[0][1])
        self.assertIn('遗漏了具体单纯形表', requests[0][0])
        self.assertIn('缺少 [课件 p.18]', requests[0][0])
        self.assertIn('不能仅补引用凑覆盖', requests[0][0])
    def test_non_adjacent_pages_are_grouped_and_body_number_is_not_source(self):
        stored = {}
        requests = []
        def request(model, prompt, data):
            requests.append((prompt, json.loads(data)))
            result = json.loads(knowledge_response(model, prompt, data))
            if 'chapters 1–80' in prompt:
                result['chapters'] = [{'title': '定义与应用', 'pages': [1, 25]}, {'title': '条件', 'pages': [2]}]
            return json.dumps(result, ensure_ascii=False)
        entries = [{'page': n, 'source_text': '原文 02 不表示来源页码', 'text': '## 页面主题\n主题\n## 知识点\n证据'} for n in (1, 2, 25)]
        compiler = StudyCompiler(request, stored.get, stored.update)
        body, plan, report = compiler.compile(entries)
        self.assertEqual([1, 25], plan['chapters'][0]['pages'])
        self.assertIn('[课件 p.25]', body)
        self.assertIn('## 自测答案', body)
        self.assertNotIn('## 第 25 页', body)
        self.assertEqual([1, 2, 25], report['covered_pages'])
        chapter_data = next(data for prompt, data in requests if 'covered_pages' in prompt)
        self.assertEqual([1, 25], [p['page'] for p in chapter_data['pages']])
        self.assertIn('02', chapter_data['pages'][-1]['source_text'])
        count = len(requests)
        self.assertEqual(body, compiler.compile(entries)[0])
        self.assertEqual(count, len(requests))

    def test_failed_source_review_preserves_draft_and_does_not_publish(self):
        stored = {}
        def request(model, prompt, data):
            if '来源审校员' in prompt:
                return '{"approved":false,"issues":["p.1 不等号方向错误"]}'
            return knowledge_response(model, prompt, data)
        compiler = StudyCompiler(request, stored.get, stored.update)
        with self.assertRaisesRegex(ValueError, '审校未通过'):
            compiler.compile([{'page': 1, 'source_text': 'min', 'text': '## 页面主题\n优化'}])
        self.assertTrue(any('rewrite-' in name and '-review-' in name for name in stored))
        self.assertFalse(any(name.endswith('document.md') for name in stored))

    def test_invalid_pages_missing_citations_and_contradictory_review_fail(self):
        plan = {'title': '课程', 'abstract': '简介', 'tags': [], 'synonyms': [], 'goals': ['目标'], 'chapters': [{'title': '主题', 'pages': [True]}]}
        with self.assertRaises(ValueError):
            validate_plan(plan, [1])
        with self.assertRaises(ValueError):
            validate_chapter({'covered_pages': [1], 'markdown': 'x' * 101, 'key_points': ['[课件 p.1]要点'], 'gaps': []}, [1])
        with self.assertRaises(ValueError):
            validate_review({'approved': True, 'issues': ['矛盾']})
