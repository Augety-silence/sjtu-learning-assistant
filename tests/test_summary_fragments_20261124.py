"""封底短分段复用付费稿并强制来源复核，最终正文仍严格验证。"""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import Mock

from sjtu_learning_assistant.knowledge_summary import SUMMARY_PROMPT, SummaryCompiler, validate_summary


class FragmentSummaryTests(unittest.TestCase):
    def draft(self):
        return dict(title='课件封底', abstract='学院标识。', tags=[], synonyms=[],
                    markdown='本页只含学院标识和建筑背景，没有教学正文。 [课件 p.27]',
                    covered_pages=[27], covered_topics=[], topics=[])

    def test_short_fragment_is_allowed_but_final_summary_is_rejected(self):
        draft = self.draft()
        self.assertIs(draft, validate_summary(draft, [27], fragment=True))
        with self.assertRaisesRegex(ValueError, '少于60字符'):
            validate_summary(deepcopy(draft), [27])
        draft['markdown'] *= 3
        with self.assertRaisesRegex(ValueError, '完整主题结构'):
            validate_summary(draft, [27])

    def test_fragment_still_rejects_missing_pages_false_pages_and_bad_citations(self):
        for changes in (dict(covered_pages=[]), dict(covered_pages=[True]),
                        dict(covered_pages=[27.0]), dict(markdown='封底。 [课件 p.28]'),
                        dict(topics=[dict(name='伪来源', pages=[28])]), dict(markdown='')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_summary(dict(self.draft(), **changes), [27], fragment=True)

    def test_uncited_non_teaching_fragment_is_allowed_only_before_full_review(self):
        draft = self.draft()
        draft['markdown'] = '本页为封底，无实质教学内容。'
        self.assertIs(draft, validate_summary(draft, [27], fragment=True))
        draft['topics'] = [dict(name='教学主题', pages=[27])]
        with self.assertRaisesRegex(ValueError, '有效来源引用'):
            validate_summary(draft, [27], fragment=True)

    def test_exhausted_short_paid_draft_is_revalidated_without_new_generation(self):
        cache, request = {}, Mock()
        compiler = SummaryCompiler(request, cache.get, cache.update, auto_recovery=True)
        data = dict(pages=[dict(page=27, text='学院标识，无正文。')], topic_ids=[])
        identity = hashlib.sha256((SUMMARY_PROMPT + json.dumps(data, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
        key = compiler.prefix + 'part.json-resume-v1-' + identity[:12] + '.json'
        retained = json.dumps(dict(input=identity, step=4, draft=json.dumps(self.draft()), error='正文少于60字符'))
        cache[key] = retained
        result = compiler._save_summary('part.json', SUMMARY_PROMPT, data, [27], [], fragment=True)
        self.assertEqual(self.draft(), result)
        self.assertEqual(retained, cache[key])
        request.assert_not_called()

    def test_short_fragment_is_fully_reviewed_even_at_zero_sampling(self):
        compiler = SummaryCompiler(Mock(), {}.get, Mock(), review_percent=0)
        compiler._save_summary = Mock(return_value=self.draft())
        compiler._review = Mock(return_value=dict(approved=True, issues=[]))
        draft, review = compiler._summary('part-end', dict(pages=[dict(page=27, text='学院标识')]),
                                          [27], [], always_review=False, fragment=True)
        self.assertTrue(review['approved'])
        self.assertEqual(1, compiler.reviewed_parts)
        compiler._review.assert_called_once()

    def test_real_teaching_omission_is_not_accepted_as_a_short_fragment(self):
        compiler = SummaryCompiler(Mock(), {}.get, Mock(), review_percent=0)
        compiler._save_summary = Mock(return_value=self.draft())
        compiler._review = Mock(return_value=dict(approved=False, issues=['第27页：遗漏成本分类公式。']))
        with self.assertRaisesRegex(ValueError, '来源审校未通过'):
            compiler._summary('part-teaching', dict(pages=[dict(page=27, text='成本分类公式。')]),
                              [27], [], always_review=False, fragment=True)
        self.assertEqual(2, compiler._review.call_count)
