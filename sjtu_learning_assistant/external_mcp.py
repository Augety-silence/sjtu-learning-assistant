"""标准 MCP 客户端：持久化原始配置、发现工具并按服务权限调用。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

LOCK = threading.RLock()
MAX_SERVICES = 16
MAX_TOOLS = 36


class MCPClientError(RuntimeError):
    pass


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.extension-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def command_path(command):
    if Path(command).is_absolute():
        return command
    extra = ([str(Path(os.environ['APPDATA'])/'npm')] if os.environ.get('APPDATA') else []) if os.name == 'nt' else ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin']
    search = os.pathsep.join(filter(None, (os.environ.get('PATH'), str(Path.home()/'.local/bin'), *extra)))
    located = shutil.which(command, path=search)
    if not located:
        raise MCPClientError('找不到服务启动程序，请在配置中填写已安装程序的完整路径。')
    return located


def tool_authorized(row, tool):
    """服务启用、工具勾选与权限判断在注册/筛选/执行时保持一致。"""
    return bool(row.get('enabled') and tool.get('enabled') and
                (row.get('permission') == 'all' or tool.get('read_only') is True))


def normalize_web_result(raw, text, identity, name):
    """兼容远程 JSON 和 stdio 的 Title/URL 搜索格式；业务错误不当成命中。"""
    body = raw.get('structuredContent')
    if not isinstance(body, dict):
        try:
            body = json.loads(text)
        except (TypeError, ValueError):
            body = None
    failed = bool(raw.get('isError'))
    if isinstance(body, dict):
        code = str(body.get('code') or '').lower()
        failed = failed or bool(body.get('error')) or body.get('status') in ('error', 'failed') or code.startswith(
            ('monthly_cap_', 'invalid_api_key', 'unauthorized', 'rate_limit', 'quota_', 'request_limit'))
        if isinstance(body.get('results'), list):
            body = {**body, 'items': body['results'], 'count': len(body['results'])}
    elif re.search(r'search', name, re.I):
        items = []
        for part in re.split(r'(?m)^Title:\s*', text)[1:]:
            url = re.search(r'(?m)^URL:\s*(https?://\S+)', part)
            if not url:
                continue
            content = re.search(r'(?m)^(?:Content|Highlights):\s*', part)
            items.append({'title': part.splitlines()[0].strip(), 'url': url.group(1),
                          'snippet': part[content.end():].strip()[:3000] if content else ''})
        if items:
            body = {'provider': identity, 'status': 'ready', 'items': items, 'count': len(items)}
        if name.startswith('tavily_') and re.match(r'(?:Error|API error|Tavily API error):', text, re.I):
            failed = True
    return body, failed


def tool_name(service_id: str, name: str):
    digest = hashlib.sha256((service_id+'\0'+name).encode()).hexdigest()[:12]
    return 'mcp_' + digest + '_' + re.sub(r'[^a-zA-Z0-9_-]', '_', name)[:46]


class ExternalMCP:
    def __init__(self, path: Path):
        self.path = path

    def load(self):
        try:
            result = json.loads(self.path.read_text(encoding='utf-8'))
            return result if type(result) is dict else {}
        except FileNotFoundError:
            return {}
        except (ValueError, OSError):
            raise MCPClientError('MCP 配置文件无法读取，请恢复配置文件后重试。') from None

    def list(self):
        return {'items': [self.dto(name, row) for name, row in self.load().items()]}

    @staticmethod
    def dto(name, row):
        config = row['config']
        return dict(id=name, name=name, enabled=row.get('enabled', False),
                    permission=row.get('permission', 'read_only'),
                    transport=config.get('type', 'stdio'), command=config.get('command', ''),
                    env_keys=list(config.get('env', {})), url=config.get('url', ''),
                    tools=row.get('tools', []), error=row.get('error', ''))

    def import_config(self, raw):
        if type(raw) is str:
            if len(raw) > 64000:
                raise MCPClientError('MCP 配置不能超过 64KB。')
            try:
                raw = json.loads(raw)
            except ValueError:
                raise MCPClientError('JSON 格式无效，请检查逗号和引号。') from None
        if type(raw) is not dict or type(raw.get('mcpServers')) is not dict or not raw['mcpServers']:
            raise MCPClientError('配置需要包含 mcpServers 对象。')
        clean = {}
        for name, config in raw['mcpServers'].items():
            if type(name) is not str or not name.strip() or len(name) > 64 or type(config) is not dict:
                raise MCPClientError('服务名称或配置无效。')
            if set(config) - {'command','args','env','url','type','transport','headers','timeout'}:
                raise MCPClientError('服务包含不支持的配置字段。')
            transport = config.get('type', config.get('transport', 'streamable-http' if 'url' in config else 'stdio'))
            transport = {'http':'streamable-http','streamableHttp':'streamable-http'}.get(transport, transport)
            if transport not in ('stdio','streamable-http','sse'):
                raise MCPClientError('支持 stdio、Streamable HTTP 和 SSE 服务。')
            row = dict(config, type=transport)
            row.pop('transport', None)
            if transport == 'stdio':
                if type(row.get('command')) is not str or not row['command'].strip() or '\0' in row['command']:
                    raise MCPClientError('请填写启动命令。')
                args = row.get('args', [])
                if type(args) is not list or len(args)>64 or any(type(a) is not str or len(a)>4096 or '\0' in a for a in args):
                    raise MCPClientError('args 必须为字符串数组。')
                row['args'] = args
            else:
                address = row.get('url')
                parsed = urlparse(address) if type(address) is str else None
                if not parsed or not parsed.hostname or parsed.username or parsed.password or not (
                    parsed.scheme == 'https' or parsed.scheme == 'http' and parsed.hostname in ('localhost','127.0.0.1','::1')
                ):
                    raise MCPClientError('远端地址需使用 HTTPS；本机服务可以使用 HTTP。')
            for field in ('env','headers'):
                mapping = row.get(field, {})
                if type(mapping) is not dict or len(mapping)>32 or any(type(k) is not str or type(v) is not str or '\0' in k+v or len(k+v)>8192 for k,v in mapping.items()):
                    raise MCPClientError(f'{field} 必须为字符串键值对象。')
            clean[name] = row
        with LOCK:
            data = self.load()
            if len(set(data)|set(clean))>MAX_SERVICES:
                raise MCPClientError(f'最多保存 {MAX_SERVICES} 个服务。')
            for name, config in clean.items():
                # 更新配置需重新连接，不沿用上一版本工具的授权。
                data[name] = {'config':config,'enabled':False,'permission':'read_only','tools':[],'error':''}
            write_json(self.path, data)
        return self.list()

    async def _request(self, config, method, arguments=None):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamablehttp_client
        transport = config['type']
        with open(os.devnull, 'w') as errlog:
            context = (stdio_client(StdioServerParameters(command=command_path(config['command']), args=config.get('args', []), env=config.get('env', {})), errlog=errlog)
                       if transport == 'stdio' else
                       sse_client(config['url'], headers=config.get('headers', {})) if transport == 'sse' else
                       streamablehttp_client(config['url'], headers=config.get('headers', {})))
            async with context as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    if method == 'list':
                        response = await session.list_tools()
                        if response.nextCursor or len(response.tools)>MAX_TOOLS:
                            raise MCPClientError(f'服务工具超过 {MAX_TOOLS} 个，请使用精简工具配置。')
                        return [t.model_dump(by_alias=True, exclude_none=True) for t in response.tools]
                    response = await session.call_tool(method, arguments=arguments)
                    return response.model_dump(by_alias=True, exclude_none=True)

    def request(self, config, method, arguments=None):
        async def bounded():
            return await asyncio.wait_for(self._request(config,method,arguments), timeout=45)
        try:
            return asyncio.run(bounded())
        except MCPClientError:
            raise
        except (Exception, BaseExceptionGroup) as exc:
            raise MCPClientError('MCP 连接或调用失败（'+type(exc).__name__+'），请检查启动程序、服务地址及网络后重试。') from None

    def connect(self, identity):
        data=self.load()
        if identity not in data:
            raise MCPClientError('服务不存在。')
        config=data[identity]['config']
        tools=self.request(config, 'list')
        normalized=[]
        for t in tools:
            if type(t.get('name')) is not str or type(t.get('inputSchema')) is not dict or t['inputSchema'].get('type')!='object':
                raise MCPClientError('服务返回了无效工具定义。')
            normalized.append(dict(name=t['name'], model_name=tool_name(identity,t['name']), description=str(t.get('description',''))[:3000],
                                   inputSchema=t['inputSchema'], read_only=t.get('annotations',{}).get('readOnlyHint') is True, enabled=True))
        with LOCK:
            latest=self.load()
            if latest.get(identity,{}).get('config') != config:
                raise MCPClientError('连接期间配置发生变化，请重新连接。')
            latest[identity].update(tools=normalized, enabled=True, error='')
            write_json(self.path,latest)
        return self.list()

    def update(self, identity, *, enabled=None, permission=None, tools=None, delete=False):
        with LOCK:
            data=self.load()
            if identity not in data:raise MCPClientError('服务不存在。')
            row=data[identity]
            if delete:
                del data[identity]
            else:
                if enabled is not None:
                    if type(enabled) is not bool:raise MCPClientError('启用状态无效。')
                    if enabled and not row.get('tools'):raise MCPClientError('请先连接并读取工具。')
                    row['enabled']=enabled
                if permission is not None:
                    if permission not in ('read_only','all'):raise MCPClientError('权限类型无效。')
                    row['permission']=permission
                if tools is not None:
                    known={t['name'] for t in row.get('tools',[])}
                    if type(tools) is not list or any(t not in known for t in tools):raise MCPClientError('工具选择无效。')
                    for t in row['tools']:t['enabled']=t['name'] in tools
            write_json(self.path,data)
        return self.list()

    def definitions(self):
        result=[]
        for identity,row in self.load().items():
            if not row.get('enabled'):continue
            for t in row.get('tools',[]):
                if tool_authorized(row, t):
                    result.append({'type':'function','function':dict(name=t['model_name'],description=f"MCP 服务 {identity}：{t['description']}",parameters=t['inputSchema'])})
        if len(result)>MAX_TOOLS:raise MCPClientError(f'已启用工具超过 {MAX_TOOLS} 个，请关闭部分工具。')
        return result

    def web_tool_names(self):
        """在已授权工具中定位网页检索/读取；未声明只读时遵循显式全部授权。"""
        names = []
        for identity, row in self.load().items():
            if not row.get('enabled'):
                continue
            for tool in row.get('tools', []):
                context = identity + ' ' + tool.get('description', '')
                if (tool_authorized(row, tool) and
                    re.search(r'bing|duckduckgo|brave|tavily|exa|jina|网页|联网|互联网|web|internet', context, re.I) and
                    re.search(r'search|fetch|browse|extract|crawl|map|read_url|read_page', tool['name'], re.I)):
                    names.append(tool['model_name'])
        return tuple(names)

    def call(self, model_name, arguments):
        if type(arguments) is not dict:raise MCPClientError('工具参数需要为对象。')
        # 每次执行重新核对状态；关闭服务或降权立即阻止后续调用。
        for identity,row in self.load().items():
            for t in row.get('tools',[]):
                if t['model_name'] != model_name:continue
                if not tool_authorized(row, t):
                    raise MCPClientError('此 MCP 工具已关闭或未获授权。')
                raw=self.request(row['config'],t['name'],arguments)
                text='\n'.join(b.get('text','') for b in raw.get('content',[]) if b.get('type')=='text')
                ambiguous_search = t['name']=='search' and 'No results were found' in text and 'bot detection' in text
                if ambiguous_search:
                    text='DuckDuckGo未返回可验证的搜索结果，上游可能拦截或限流；这不能证明关键词没有匹配。本轮不要反复更换关键词重试，可使用已启用的其他搜索服务。'
                body, failed = normalize_web_result(raw, text, identity, t['name'])
                return {'text':text[:16000],'structuredContent':body, 'isError':failed or ambiguous_search}, {'service':identity,'tool':t['name']}
        raise MCPClientError('MCP 工具不存在，请重新连接服务。')
