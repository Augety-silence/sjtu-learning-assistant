"""只读 Bing RSS 检索服务；通过标准 MCP stdio 提供，来源明确。"""
from __future__ import annotations
import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
import httpx


def search_web(query: str, max_results: int = 5, *, exact_phrase: str | None = None, transport=None) -> dict:
    if type(query) is not str or not query.strip() or len(query)>160:
        raise ValueError('关键词需要为1至160字。')
    if type(max_results) is not int or not 1<=max_results<=10:
        raise ValueError('结果数量需要为1至10。')
    if exact_phrase is not None and (type(exact_phrase) is not str or not exact_phrase.strip() or len(exact_phrase)>80):
        raise ValueError('精确短语需要为1至80字。')
    query=query.strip()
    quoted=re.findall(r'"([^"\n]{1,80})"|「([^」\n]{1,80})」|“([^”\n]{1,80})”',query)
    phrases=[exact_phrase.strip()] if exact_phrase else [next(p for p in match if p) for match in quoted]
    if not phrases and re.fullmatch(r'[\u4e00-\u9fff]{2,4}',query):
        phrases=[query]
    effective_query=('"'+query+'"') if phrases==[query] else query
    try:
        with httpx.Client(timeout=15,follow_redirects=True,transport=transport) as client:
            response=client.get('https://www.bing.com/search',params={'q':effective_query,'format':'rss'})
            response.raise_for_status()
            if len(response.content)>2*1024*1024:raise ValueError('检索响应过大。')
            document=ET.fromstring(response.content)
        if document.tag!='rss':raise ValueError('检索服务未返回RSS结果，可能暂时受限。')
        items=[];seen=set()
        for item in document.findall('./channel/item'):
            url=(item.findtext('link') or '').strip()
            address=urlparse(url)
            if address.scheme not in ('http','https') or not address.hostname or address.username or address.password or url in seen:continue
            title=(item.findtext('title') or '').strip()
            if not title:continue
            seen.add(url)
            items.append({'title':html.unescape(title)[:400],'url':url,'snippet':html.unescape(re.sub(r'<[^>]*>','',item.findtext('description') or ''))[:1800]})
            if len(items)>=max_results:break
        upstream_count=len(items)
        if phrases:
            def normalize(value):
                return re.sub(r'\s+','',value).casefold()
            items=[item for item in items if all(normalize(phrase) in normalize(item['title']+' '+item['snippet']) for phrase in phrases)]
        status='ready' if items else 'no_relevant_matches' if upstream_count else 'empty'
        return {'provider':'Bing RSS','query':query,'effective_query':effective_query,'status':status,
                'count':len(items),'upstream_count':upstream_count,'discarded_count':upstream_count-len(items),
                'match_policy':'literal_phrase' if phrases else 'unverified', 'items':items,
                'note':('上游返回的条目没有匹配完整短语，已排除无关结果。本轮未取得有效证据，不能推断人物不存在、没有公开资料或就是用户本人。'
                        if status=='no_relevant_matches' else
                        '搜索摘要仅用于定位来源；匹配字串不证明身份。回答事实前读取可信原文核对。空结果不能证明相关资料不存在。')}
    except (httpx.HTTPError,ET.ParseError) as exc:
        raise RuntimeError('Bing检索未完成，请检查网络或稍后重试（'+type(exc).__name__+'）。') from None


def main():
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    server=FastMCP('Bing 联网搜索')
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def search(query: str, max_results: int = 5, exact_phrase: str | None = None) -> dict:
        """使用Bing检索公开网页，返回标题、真实URL和摘要。此服务来源为Bing，不是DuckDuckGo；遇到网络错误不编造结果。"""
        return search_web(query,max_results,exact_phrase=exact_phrase)
    server.run(transport='stdio')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
