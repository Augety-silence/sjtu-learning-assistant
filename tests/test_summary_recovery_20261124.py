"""复验已付费定向补丁；合成响应，不读取生产资料或调用模型。"""
import hashlib
import json
import unittest
from unittest.mock import Mock

from sjtu_learning_assistant.knowledge_summary import SUMMARY_PROMPT, SummaryCompiler, validate_summary


class SummaryPatchExtractionTests(unittest.TestCase):
    def setup_state(self, *, error='精炼总结缺少完整主题结构。', markdown=None):
        cache = {}
        request = Mock()
        compiler = SummaryCompiler(request, cache.get, cache.update, auto_recovery=True)
        data = {'pages': [{'page': 1, 'source_text': '来源定义与适用条件'}], 'topic_ids': []}
        draft = dict(title='课程主题', abstract='概念与条件。', tags=['主题'], synonyms=[],
                     covered_pages=[1], covered_topics=[], topics=[],
                     markdown=markdown or '## 课程主线\n从来源定义理解概念关系。\n## 核心知识\n保留原文给出的必要条件和核心结论。\n## 适用条件与易错点\n在所列前提下使用结论，不能脱离条件。 [课件 p.1]')
        if '正文' in error:
            draft['topics'] = [{'name': '课程主题', 'pages': [1]}]
        identity = hashlib.sha256((SUMMARY_PROMPT + json.dumps(data, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
        cache[compiler.prefix + 'old.json-resume-v1-' + identity[:12] + '.json'] = json.dumps(
            dict(input=identity, step=4, draft=json.dumps(draft), error=error))
        return cache, compiler, data, draft, request

    def execute(self, compiler, data):
        return compiler._saved('old.json', SUMMARY_PROMPT, data, lambda value: validate_summary(value, [1]))

    def test_extra_fields_are_ignored_and_original_draft_fields_are_retained(self):
        cache, compiler, data, draft, request = self.setup_state()
        response = dict(topics=[dict(name='课程主题', pages=[1])], title='伪造标题',
                        markdown='新增无来源正文', covered_pages=[99], explanation='多余字段')
        raw = json.dumps(response, ensure_ascii=False)
        request.return_value = raw
        result = self.execute(compiler, data)
        self.assertEqual(response['topics'], result['topics'])
        for field in ('title', 'abstract', 'tags', 'synonyms', 'markdown', 'covered_pages', 'covered_topics'):
            self.assertEqual(draft[field], result[field])
        response_keys = [key for key in cache if '-response-v1-' in key]
        self.assertEqual([raw], [cache[key] for key in response_keys])
        self.assertEqual(result, self.execute(SummaryCompiler(request, cache.get, cache.update, auto_recovery=True), data))
        self.assertEqual(1, request.call_count)

    def test_exhausted_old_extra_field_rejection_is_revalidated_without_new_request(self):
        cache, compiler, data, draft, request = self.setup_state()
        # 用相同的旧补丁请求和缓存身份模拟两次响应都因额外字段被拒绝。
        raw = json.dumps(dict(topics=[dict(name='课程主题', pages=[1])], explanation='额外解释'))
        name = 'old.json-automatic-patch-v2.json'
        error = '精炼总结缺少完整主题结构。'
        repair_data = dict(data, draft={'topics': []}, validation_error=error, repair_kind='automatic-patch-v2')
        prompt = SUMMARY_PROMPT + '\n自动恢复v2：保留已有有效内容，只修复validation_error指出的问题。'
        prompt += '\n只返回字段topics的JSON补丁，不返回或重抄其他字段；主题用真实来源映射，不凭空补页码。'
        identity = hashlib.sha256((prompt + json.dumps(repair_data, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
        base = compiler.prefix + name
        key = base + '-resume-v1-' + identity[:12] + '.json'
        retained = json.dumps(dict(input=identity, step=2, draft=raw, error='定向修复只能返回指定字段。'))
        cache[key] = retained
        result = self.execute(compiler, data)
        self.assertEqual(draft['markdown'], result['markdown'])
        self.assertEqual([1], result['topics'][0]['pages'])
        self.assertEqual(retained, cache[key], '本地复验不重置或改写耗尽次数')
        request.assert_not_called()

    def test_explicit_content_scope_reuses_valid_complete_patch_after_field_merge_fails(self):
        cache, compiler, data, draft, request = self.setup_state()
        draft['markdown'] = '少于60字符'
        # topics 定向阶段的旧完整响应同时补齐了真实正文；普通开关不可偷渡，
        # 显式内容恢复开关可执行完整JSON验证后零请求继续来源审校。
        data = dict(data)
        raw = json.dumps(dict(draft, topics=[dict(name='课程主题', pages=[1])],
                              markdown='## 课程主线\n从来源定义理解概念关系。\n## 核心知识\n保留原文给出的必要条件和核心结论。\n## 适用条件与易错点\n在所列前提下使用结论，不能脱离条件。 [课件 p.1]'))
        state = dict(draft=json.dumps(draft), error='精炼总结缺少完整主题结构。')
        repair_data = dict(data, draft={'topics': []}, validation_error=state['error'], repair_kind='automatic-patch-v2')
        prompt = SUMMARY_PROMPT + '\n自动恢复v2：保留已有有效内容，只修复validation_error指出的问题。'
        prompt += '\n只返回字段topics的JSON补丁，不返回或重抄其他字段；主题用真实来源映射，不凭空补页码。'
        identity = hashlib.sha256((prompt + json.dumps(repair_data, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
        key = compiler.prefix + 'content.json-automatic-patch-v2.json-resume-v1-' + identity[:12] + '.json'
        retained = json.dumps(dict(input=identity, step=2, draft=raw, error='定向修复只能返回指定字段。'))
        cache[key] = retained
        compiler.content_recovery = True
        result = compiler._recover_patch('content.json', SUMMARY_PROMPT, data, lambda value: validate_summary(value, [1]),
                                         state, compiler.prefix + 'content.json')
        self.assertEqual(json.loads(raw), result)
        self.assertEqual(retained, cache[key])
        request.assert_not_called()

    def test_selected_topic_pages_still_require_real_in_range_integers(self):
        for pages in ([99], ['1'], [True], []):
            with self.subTest(pages=pages):
                cache, compiler, data, draft, request = self.setup_state()
                request.return_value = json.dumps(dict(topics=[dict(name='课程主题', pages=pages)], title='额外字段'))
                for _ in range(3):
                    with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
                        self.execute(SummaryCompiler(request, cache.get, cache.update, auto_recovery=True), data)
                self.assertEqual(2, request.call_count)

    def test_missing_patch_field_is_bounded_and_does_not_replace_draft(self):
        cache, compiler, data, draft, request = self.setup_state()
        request.return_value = '{"explanation":"没有topics"}'
        for _ in range(3):
            with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
                self.execute(SummaryCompiler(request, cache.get, cache.update, auto_recovery=True), data)
        self.assertEqual(2, request.call_count)
        self.assertNotIn(compiler.prefix + 'old.json', cache)

    def test_markdown_patch_still_rejects_fake_citations_and_missing_content(self):
        for markdown in ('过短 [课件 p.1]', '错误来源 [课件 p.99]' * 10):
            with self.subTest(markdown=markdown):
                cache, compiler, data, draft, request = self.setup_state(error='精炼总结正文当前5字符', markdown='过短')
                request.return_value = json.dumps(dict(markdown=markdown, covered_pages=[99], topics=[]))
                with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
                    self.execute(compiler, data)
                self.assertEqual(2, request.call_count)

    def test_topic_page_order_and_duplicates_are_normalized_without_guessing_pages(self):
        cache, compiler, data, draft, request = self.setup_state()
        draft['covered_pages'] = [1, 2]
        draft['topics'] = [dict(name='课程主题', pages=[2, 1, 2])]
        self.assertEqual([1, 2], validate_summary(draft, [1, 2])['topics'][0]['pages'])
        self.assertEqual([1, 2], draft['covered_pages'])
        for pages in ([2, 99, 1], [True, 2], ['1', 2]):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                validate_summary(dict(draft, topics=[dict(name='课程主题', pages=pages)]), [1, 2])

    def test_extra_complete_response_cannot_repair_other_invalid_fields(self):
        cache, compiler, data, draft, request = self.setup_state()
        draft['covered_pages'] = []
        # topics 修复无法把完整响应中的 covered_pages 偷渡进旧草稿。
        raw = json.dumps(dict(topics=[dict(name='课程主题', pages=[1])], covered_pages=[1]))
        request.return_value = raw
        with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
            compiler._recover_patch('isolated.json', SUMMARY_PROMPT, data,
                                    lambda value: validate_summary(value, [1]),
                                    dict(draft=json.dumps(draft), error='精炼总结缺少完整主题结构。'),
                                    compiler.prefix + 'isolated.json')
        self.assertEqual(2, request.call_count)


class BoundedContentRecoveryTests(unittest.TestCase):
    def setup_content(self, request, *, enabled=True, merge=False):
        cache = {}
        compiler = SummaryCompiler(request, cache.get, cache.update, auto_recovery=True, content_recovery=enabled)
        draft = dict(title='课程主题', abstract='概念与条件。', tags=['主题'], synonyms=[],
                     covered_pages=[1], covered_topics=[], topics=[dict(name='课程主题', pages=[1])],
                     markdown='## 课程主线\n从来源定义理解概念关系。\n## 核心知识\n保留原文给出的必要条件和核心结论。\n## 适用条件与易错点\n在所列前提下使用结论，不能脱离条件。 [课件 p.1]')
        complete = dict(draft, covered_pages=[1, 2], topics=[dict(name='课程主题', pages=[1, 2])])
        data = {'topic_ids': []}
        if merge:
            data['input_summaries'] = [complete]
        else:
            data['pages'] = [dict(page=1, source_text='来源定义'), dict(page=2, source_text='应用条件')]
        validate = lambda value: validate_summary(value, [1, 2])
        return cache, compiler, data, draft, complete, validate

    def recover(self, compiler, data, draft, validate):
        return compiler._recover_content('existing.json', SUMMARY_PROMPT, data, validate, draft,
                                         compiler.prefix + 'existing.json')

    def test_default_disabled_does_not_add_request(self):
        request = Mock()
        cache, compiler, data, draft, complete, validate = self.setup_content(request, enabled=False)
        with self.assertRaisesRegex(ValueError, '未开启'):
            self.recover(compiler, data, draft, validate)
        request.assert_not_called()
        self.assertEqual({}, cache)

    def test_full_source_recovery_is_saved_and_restarts_do_not_repeat_request(self):
        request = Mock()
        cache, compiler, data, draft, complete, validate = self.setup_content(request)
        request.return_value = json.dumps(complete, ensure_ascii=False)
        result = self.recover(compiler, data, draft, validate)
        self.assertEqual(complete, result)
        sent = json.loads(request.call_args.args[2])
        self.assertEqual(data['pages'], sent['pages'])
        self.assertEqual(draft, sent['draft'])
        self.assertIn('遗漏输入页面', sent['validation_error'])
        self.assertEqual('20261124', sent['content_recovery_stage'])
        self.assertEqual('automatic-patch-v2', sent['repair_kind'])
        self.assertEqual(result, self.recover(
            SummaryCompiler(request, cache.get, cache.update, auto_recovery=True, content_recovery=True), data, draft, validate))
        self.assertEqual(1, request.call_count)

    def test_two_failed_requests_are_lifetime_bounded_and_input_is_frozen(self):
        request = Mock()
        cache, compiler, data, draft, complete, validate = self.setup_content(request, merge=True)
        request.return_value = json.dumps(draft)
        for index in range(4):
            # 下一次运行看到不同失败草稿，也必须沿用首次完整来源/草稿身份。
            current = dict(draft, markdown=draft['markdown'] + ' 后续失败稿' * index)
            with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
                self.recover(SummaryCompiler(request, cache.get, cache.update, auto_recovery=True,
                                             content_recovery=True), data, current, validate)
        self.assertEqual(2, request.call_count)
        for call in request.call_args_list:
            self.assertEqual(data['input_summaries'], json.loads(call.args[2])['input_summaries'])
        states = [json.loads(value) for key, value in cache.items() if 'content-recovery' in key and '-resume-v1-' in key]
        self.assertEqual(1, len(states))
        self.assertEqual(2, states[0]['step'])

    def test_timeout_attempt_is_reserved_before_request_and_never_reissued(self):
        seen = []
        request = Mock()
        cache, compiler, data, draft, complete, validate = self.setup_content(request)
        def timeout(*_):
            states = [json.loads(value) for key, value in cache.items() if 'content-recovery' in key and '-resume-v1-' in key]
            seen.append((states[0]['step'], states[0]['reserved_step']))
            raise TimeoutError('synthetic timeout')
        request.side_effect = timeout
        for _ in range(2):
            with self.assertRaises(TimeoutError):
                self.recover(SummaryCompiler(request, cache.get, cache.update, content_recovery=True), data, draft, validate)
        with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
            self.recover(compiler, data, draft, validate)
        self.assertEqual([(1, 0), (2, 1)], seen)
        self.assertEqual(2, request.call_count)

    def test_unknown_error_does_not_start_new_content_stage(self):
        request = Mock()
        cache, compiler, data, draft, complete, validate = self.setup_content(request)
        with self.assertRaisesRegex(ValueError, '不支持的校验错误'):
            self.recover(compiler, data, draft, lambda _: (_ for _ in ()).throw(ValueError('不支持的校验错误')))
        request.assert_not_called()
        self.assertEqual({}, cache)

    def test_changed_source_cannot_refill_existing_named_stage(self):
        request = Mock()
        cache, compiler, data, draft, complete, validate = self.setup_content(request)
        request.return_value = json.dumps(draft)
        with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
            self.recover(compiler, data, draft, validate)
        changed = dict(data, pages=[dict(page=1, source_text='新来源'), dict(page=2, source_text='新条件')])
        with self.assertRaisesRegex(ValueError, '不重置修复额度'):
            self.recover(compiler, changed, draft, validate)
        self.assertEqual(2, request.call_count)

    def test_short_body_requires_source_content_and_is_not_marked_approved(self):
        request = Mock()
        cache, compiler, data, draft, complete, validate = self.setup_content(request)
        short = dict(complete, markdown='太短 [课件 p.1]')
        request.return_value = json.dumps(short)
        with self.assertRaisesRegex(ValueError, '自动修复额度已用尽'):
            self.recover(compiler, data, short, validate)
        self.assertIn('补齐缺失的核心知识', json.loads(request.call_args_list[0].args[2])['validation_error'])
        self.assertNotIn(compiler.prefix + 'existing.json', cache)


if __name__ == '__main__':
    unittest.main()
