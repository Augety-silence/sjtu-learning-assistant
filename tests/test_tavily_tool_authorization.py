"""Tavily 未声明 readOnlyHint 时，显式工具授权必须贯通到在线会话。"""
from dataclasses import replace

import pytest
from sqlalchemy import create_engine

from sjtu_learning_assistant.agent_runtime.loop import AgentLoop
from sjtu_learning_assistant.agent_runtime.presets import PresetLoader
from sjtu_learning_assistant.agent_runtime.tools import AgentToolError, ReadOnlyToolRegistry
from sjtu_learning_assistant.external_mcp import ExternalMCP, MCPClientError
from sjtu_learning_assistant.external_mcp import normalize_web_result
from sjtu_learning_assistant.agent_runtime.loop import with_search_links


@pytest.fixture
def manager(tmp_path, monkeypatch):
    store = ExternalMCP(tmp_path / 'mcp.json')
    store.import_config({'mcpServers': {'tavily': {'command': 'npx', 'args': ['tavily-mcp']}}})
    tools = [dict(name='tavily_' + action, description=description,
                  inputSchema={'type': 'object', 'properties': {'query': {'type': 'string'}}})
             for action, description in (
                 ('search', 'Search the web for current information.'),
                 ('extract', 'Extract content from URLs.'),
                 ('crawl', 'Crawl a website.'),
                 ('map', "Map a website's structure."),
                 ('research', 'Research multiple web sources.'))]
    calls = []

    def request(config, method, arguments=None):
        if method == 'list':
            return tools
        calls.append((method, arguments))
        return {'content': [{'type': 'text', 'text': '{"results": []}'}]}

    monkeypatch.setattr(store, 'request', request)
    store.connect('tavily')
    store.observed_calls = calls
    return store


def test_all_permission_exposes_selected_tools_without_readonly_annotation(manager):
    assert not manager.web_tool_names()
    assert not manager.definitions()
    manager.update('tavily', permission='all')
    names = set(manager.web_tool_names())
    assert len(names) == 5
    assert names == {item['function']['name'] for item in manager.definitions()}
    name = next(name for name in names if name.endswith('_tavily_search'))
    manager.call(name, {'query': '上海交通大学'})
    assert manager.observed_calls == [('tavily_search', {'query': '上海交通大学'})]


@pytest.mark.parametrize('change', (
    {'enabled': False}, {'permission': 'read_only'}, {'tools': ['tavily_extract']},
))
def test_revocation_blocks_discovery_and_existing_registry_execution(manager, change):
    manager.update('tavily', permission='all')
    engine = create_engine('sqlite://')
    try:
        registry = ReadOnlyToolRegistry(engine, external_mcp=manager)
        name = next(name for name in registry.web_tool_names if name.endswith('_tavily_search'))
        manager.update('tavily', **change)
        assert name not in manager.web_tool_names()
        assert name not in {d['function']['name'] for d in manager.definitions()}
        with pytest.raises(AgentToolError, match='关闭或未获授权'):
            registry.execute(name, {'query': '上海交通大学'}, (name,))
        assert manager.observed_calls == []
    finally:
        engine.dispose()


def test_stdio_tavily_results_are_counted_and_keep_source_urls():
    text = ('Detailed Results:\n\nTitle: 上海交通大学\nID: one\nURL: https://www.sjtu.edu.cn/\nContent: 官方主页\n\n'
            'Title: 安泰学院\nID: two\nURL: https://acem.sjtu.edu.cn/\nContent: 学院主页')
    body, error = normalize_web_result({}, text, 'tavily', 'tavily_search')
    assert not error and body['count'] == 2
    assert body['items'][0]['url'] == 'https://www.sjtu.edu.cn/'
    assert body['items'][1]['snippet'] == '学院主页'
    engine = create_engine('sqlite://')
    try:
        registry = ReadOnlyToolRegistry(engine)
        registry._handlers['mcp_search'] = lambda raw: registry._extension_call(
            lambda: ({'structuredContent': body}, {'service': 'tavily'}))
        execution = registry.execute('mcp_search', {}, ('mcp_search',))
        assert execution.result_summary['count'] == 2
        assert len(execution.result_summary['preview']['items']) == 2
    finally:
        engine.dispose()


@pytest.mark.parametrize('body', (
    {'code': 'monthly_cap_reached_bonus_eligible'}, {'error': {'message': 'quota'}},
    {'status': 'failed'},
))
def test_business_error_is_not_success_even_when_mcp_error_flag_is_false(body):
    import json
    value, failed = normalize_web_result({'isError': False}, json.dumps(body), 'tavily', 'tavily_search')
    assert failed and value == body


def test_json_tavily_results_use_actual_count():
    body, error = normalize_web_result({'structuredContent': {'results': [
        {'title': 'a', 'url': 'https://example.test/a'},
        {'title': 'b', 'url': 'https://example.test/b'}]}}, '', 'tavily', 'tavily_search')
    assert not error and body['count'] == 2 and len(body['items']) == 2


def test_source_fallback_uses_actual_public_links_and_deduplicates():
    sources = [{'title': 'SJTU', 'url': 'https://www.sjtu.edu.cn/'},
               {'title': 'again', 'url': 'https://www.sjtu.edu.cn/'},
               {'title': 'local', 'url': 'file:///private/local.txt'},
               {'title': 'credential', 'url': 'https://name:secret@example.test/'}]
    content = with_search_links('本轮找到官网。', sources)
    assert content.count('https://www.sjtu.edu.cn/') == 1
    assert 'file:///' not in content and 'name:secret' not in content
    assert with_search_links(content, sources) == content


def test_readonly_mode_still_requires_server_readonly_hint(manager):
    manager.update('tavily', permission='all')
    name = manager.web_tool_names()[0]
    manager.update('tavily', permission='read_only')
    with pytest.raises(MCPClientError, match='关闭或未获授权'):
        manager.call(name, {'query': '上海交通大学'})


def test_web_turn_reaches_model_and_tavily_with_all_permission(manager):
    manager.update('tavily', permission='all')
    engine = create_engine('sqlite://')
    try:
        registry = ReadOnlyToolRegistry(engine, external_mcp=manager)
        name = next(name for name in registry.web_tool_names if name.endswith('_tavily_search'))
        preset = PresetLoader().get('general')
        preset = replace(preset, allowed_tools=(*preset.allowed_tools, *registry.web_tool_names))
        requests = []

        class Client:
            def chat_completion(self, messages, **kwargs):
                requests.append(kwargs)
                if len(requests) == 1:
                    return {
                        'content': '', 'tool_calls': [{'id': 'tavily-test', 'type': 'function',
                        'function': {'name': name, 'arguments': '{"query":"上海交通大学"}'}}]
                    }
                return {'content': '本轮未取得有效公开证据。'}

        loop = AgentLoop(Client(), registry, preset, max_steps=3)
        result = loop.run([{'role': 'user', 'content': '在线搜索上海交通大学'}],
                          user_text='在线搜索上海交通大学', max_tokens=1200, temperature=0)
        assert result.status == 'completed'
        assert '没有已启用' not in result.content
        assert manager.observed_calls == [('tavily_search', {'query': '上海交通大学'})]
        assert len(requests) == 2
        assert all(d['function']['name'].startswith('mcp_') for d in requests[0]['tools'])
    finally:
        engine.dispose()
