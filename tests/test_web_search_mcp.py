"""检索失败语义、真实协议以及助理隔离的回归验证。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sjtu_learning_assistant.web_search_mcp import search_web
from sjtu_learning_assistant.external_mcp import ExternalMCP
from sjtu_learning_assistant.agent_runtime.tools import ReadOnlyToolRegistry, AgentToolError
from sjtu_learning_assistant.agent_runtime.presets import PresetLoader
from sjtu_learning_assistant.agent_runtime.loop import AgentLoop


class WebSearchTests(unittest.TestCase):
    def request(self, body, status=200):
        return httpx.MockTransport(lambda request: httpx.Response(status, text=body))

    def test_rss_real_urls_deduplicated_and_source_preserved(self):
        rss='<rss><channel><item><title>交大</title><link>https://www.sjtu.edu.cn/</link><description>&lt;b&gt;官网&lt;/b&gt;</description></item><item><title>重复</title><link>https://www.sjtu.edu.cn/</link></item><item><title>无效</title><link>javascript:alert(1)</link></item></channel></rss>'
        data=search_web('上海交通大学',transport=self.request(rss))
        self.assertEqual('Bing RSS',data['provider']);self.assertEqual('ready',data['status'])
        self.assertEqual(1,data['count']);self.assertEqual('官网',data['items'][0]['snippet'])

    def test_empty_is_distinct_from_service_failure(self):
        self.assertEqual('empty',search_web('测试',transport=self.request('<rss><channel/></rss>'))['status'])
        for body,status in (('<html>blocked</html>',200),('<rss',200),('denied',403)):
            with self.assertRaises((ValueError,RuntimeError)):search_web('测试',transport=self.request(body,status))

    def test_argument_boundaries(self):
        for query,count in (('',5),('x'*161,5),('x',0),('x',11),('x',True)):
            with self.assertRaises(ValueError):search_web(query,count)

    def test_ddg_ambiguous_result_is_error_and_registry_rejects(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=ExternalMCP(Path(folder)/'mcp.json')
            manager.import_config({'mcpServers':{'DuckDuckGo':{'command':'fixture'}}})
            with patch.object(manager,'request',return_value=[{'name':'search','description':'search','inputSchema':{'type':'object'},'annotations':{'readOnlyHint':True}}]):
                manager.connect('DuckDuckGo')
            name=manager.definitions()[0]['function']['name']
            response={'content':[{'type':'text','text':'No results were found for your search query. This could be due to DuckDuckGo bot detection.'}]}
            engine=create_engine('sqlite://')
            try:
                registry=ReadOnlyToolRegistry(engine,external_mcp=manager)
                with patch.object(manager,'request',return_value=response):
                    output,_=manager.call(name,{'query':'上海交通大学'})
                    self.assertTrue(output['isError']);self.assertIn('不能证明',output['text'])
                    with self.assertRaises(AgentToolError) as caught:registry.execute(name,{'query':'测试'},(name,))
                    self.assertFalse(caught.exception.retryable)
                    with self.assertRaises(AgentToolError):registry.execute(name,{'query':'测试'},PresetLoader().get('knowledge').allowed_tools)
            finally:engine.dispose()

    def test_explicit_online_query_does_not_prefetch_local_wiki(self):
        class Tools:
            names=('browse_knowledge_wiki',)
            def execute(self,*args):raise AssertionError('纯联网请求不应读取本地Wiki')
        loop=object.__new__(AgentLoop);loop.tools=Tools();loop.preset=PresetLoader().get('general')
        self.assertEqual(([],[]),loop._prefetch('测试联网功能：上海交通大学'))
