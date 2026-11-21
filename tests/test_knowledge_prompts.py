import json
import unittest
from office_fixtures import knowledge_response
from sjtu_learning_assistant.knowledge_prompts import lecture_markdown, page_markdown


class KnowledgePromptTests(unittest.TestCase):
    def result(self):
        return json.loads(knowledge_response('deepseek-chat', 'covered_pages', '[{"page":1},{"page":2}]'))

    def test_citations_and_column_order_required(self):
        result = self.result()
        self.assertIn('知识点', lecture_markdown(json.dumps(result), [1, 2]))
        result['markdown'] = result['markdown'].replace('[第 2 页]', '')
        with self.assertRaisesRegex(ValueError, '来源引用'):
            lecture_markdown(json.dumps(result), [1, 2])

    def test_citation_ranges_are_accepted_without_invented_pages(self):
        result = self.result()
        result['markdown'] = result['markdown'].replace('[第 1 页] [第 2 页]', '[第 1–2 页]')
        self.assertIn('1–2', lecture_markdown(json.dumps(result), [1, 2]))
        result['markdown'] += '[第 3 页]'
        with self.assertRaises(ValueError):
            lecture_markdown(json.dumps(result), [1, 2])

    def test_model_refusal_or_plain_summary_not_cached_as_page(self):
        with self.assertRaisesRegex(ValueError, '结构不完整'):
            page_markdown('我无法访问图片，请自行阅读课件。')
        self.assertIn('页面主题', page_markdown(knowledge_response('qwen', '', [])))
