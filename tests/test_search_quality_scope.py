"""在线追问不能携带邮件正文；无关 RSS 条目不能变成人物证据。"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
from sjtu_learning_assistant.agent_runtime.loop import AgentLoop
from sjtu_learning_assistant.agent_runtime.presets import AgentPreset
from sjtu_learning_assistant.agent_runtime.tools import AgentToolError
from sjtu_learning_assistant.agent_runtime.turn_scope import resolve_turn_scope
from sjtu_learning_assistant.chat_steering import ChatSteeringInterrupt
from sjtu_learning_assistant.web_search_mcp import search_web

@pytest.mark.parametrize('text',['在线搜索','联网查询','网上查一下','网络搜索','online search','web search'])
def test_online_followup_inherits_user_question_only(text):
    history=[{'role':'user','content':'样例姓名是谁'},{'role':'assistant','content':'私人邮件与手机号：fixture-private-body'},{'role':'user','content':text}]
    scope=resolve_turn_scope(history,text)
    assert scope.web_only and scope.query=='样例姓名是谁' and scope.brief

@pytest.mark.parametrize('text',['在线搜索，不要查本地邮件','只在线搜索样例姓名','仅本地查邮件，不联网'])
def test_negative_scope_phrases(text):
    scope=resolve_turn_scope([],text)
    assert scope.kind==('local' if text.startswith('仅本地') else 'web')

def test_person_question_uses_public_search_and_local_identity_stays_local():
    assert resolve_turn_scope([],'样例姓名是谁').web_only
    assert not resolve_turn_scope([],'我邮件里的联系人是谁').web_only
    assert not resolve_turn_scope([],'我的课程老师是谁').web_only
    assert not resolve_turn_scope([],'我是谁').web_only

def test_explicit_both_sources_remain_available():
    assert resolve_turn_scope([],'在线搜索，同时查本地邮件').kind=='default'
    assert resolve_turn_scope([],'在线搜索并保存到工作区').kind=='default'

def test_online_course_does_not_mean_web_search():
    assert not resolve_turn_scope([],'我今天有哪些在线课程').web_only
    assert not resolve_turn_scope([],'课件作者是谁').web_only

def test_retry_remembers_online_source_and_subject():
    history=[{'role':'user','content':'样例姓名是谁'},{'role':'user','content':'在线搜索'}]
    scope=resolve_turn_scope(history,'再试试')
    assert scope.web_only and scope.query=='样例姓名是谁'

class Registry:
    names=frozenset({'browse_knowledge_wiki','search_messages','mcp_search'})
    web_tool_names=frozenset({'mcp_search'})
    def __init__(self):self.calls=[]
    def definitions(self,allowed):return [{'type':'function','function':{'name':n,'parameters':{'type':'object'}}} for n in allowed]
    def execute(self,name,args,allowed):
        if name not in allowed:raise AgentToolError('未获本轮授权',retryable=False)
        self.calls.append(name)
        result={'text':'fixture-private-body'} if name!='mcp_search' else {'status':'no_relevant_matches','items':[]}
        return SimpleNamespace(name=name,result=result,arguments_summary={},result_summary={})
class Client:
    def __init__(self,replies):self.replies=iter(replies);self.requests=[]
    def chat_completion(self,messages,**kwargs):
        self.requests.append((deepcopy(messages),deepcopy(kwargs)))
        reply=next(self.replies)
        return reply() if callable(reply) else reply
class Control:
    def __init__(self):self.pending=[];self.task_id='fixture';self.ledger=Mock()
    def take_pending(self):pending,self.pending=self.pending,[];return pending
    def has_pending(self):return bool(self.pending)
    def check_request(self):
        if self.pending:raise ChatSteeringInterrupt()
    def record_response(self,value):pass
    def seal_if_idle(self):return not self.pending
    def finish_pending(self):return self.take_pending()
def call(name):return {'content':'','tool_calls':[{'id':'call-'+name,'function':{'name':name,'arguments':{}}}]}
def run(client,registry,history,text,control=None):
    preset=AgentPreset('general','综合助手','',tuple(registry.names),'')
    return AgentLoop(client,registry,preset).run(history,user_text=text,max_tokens=4096,temperature=0,steering_control=control)

def test_online_followup_drops_private_history_and_denies_local_calls():
    registry=Registry();client=Client([call('search_messages'),call('mcp_search'),{'content':'本轮未取得有效公开信息。'}])
    history=[{'role':'system','content':'fixture-private-page-context'},{'role':'user','content':'样例姓名是谁'},{'role':'assistant','content':'fixture-private-body'},{'role':'user','content':'在线搜索'}]
    result=run(client,registry,history,'在线搜索')
    assert result.status=='completed' and registry.calls==['mcp_search']
    assert result.tool_runs[0]['status']=='rejected'
    assert {t['function']['name'] for t in client.requests[0][1]['tools']}=={'mcp_search'}
    for messages,options in client.requests:
        assert 'fixture-private' not in str(messages)
        assert {t['function']['name'] for t in options['tools']}<={'mcp_search'}
        assert options['max_tokens']==1200

def test_late_online_intervention_removes_private_evidence_from_next_request():
    registry=Registry();control=Control()
    def change():
        control.pending=[{'content':'在线搜索','request_id':'web-change'}]
        raise ChatSteeringInterrupt()
    client=Client([change,call('mcp_search'),{'content':'没有有效匹配。'}])
    result=run(client,registry,[{'role':'user','content':'本地测试「样例姓名」'}],'本地测试「样例姓名」',control)
    assert result.status=='completed' and registry.calls==['browse_knowledge_wiki','mcp_search']
    assert 'fixture-private-body' in str(client.requests[0][0])
    assert 'fixture-private-body' not in str(client.requests[1][0])
    assert {t['function']['name'] for t in client.requests[1][1]['tools']}=={'mcp_search'}

def test_no_enabled_public_tool_returns_without_paid_request():
    registry=Registry();registry.web_tool_names=frozenset();client=Client([])
    result=run(client,registry,[{'role':'user','content':'在线搜索样例姓名'}],'在线搜索样例姓名')
    assert '没有已启用' in result.content and not client.requests and not registry.calls

def test_cached_mail_cannot_bypass_changed_source_permission():
    registry=Registry();control=Control()
    def change():
        control.pending=[{'content':'在线搜索','request_id':'web-change'}]
        raise ChatSteeringInterrupt()
    client=Client([call('search_messages'),change,call('search_messages'),call('mcp_search'),{'content':'没有有效匹配。'}])
    result=run(client,registry,[{'role':'user','content':'本地测试「样例姓名」'}],'本地测试「样例姓名」',control)
    assert registry.calls.count('search_messages')==1
    assert result.status=='completed'
    assert all('fixture-private-body' not in str(messages) for messages,options in client.requests[2:])

def test_short_web_reply_does_not_trigger_automatic_continuation():
    registry=Registry();client=Client([{'content':'本轮没有取得有效公开证据。','response_metadata':{'finish_reason':'length'}}])
    result=run(client,registry,[{'role':'user','content':'在线搜索样例姓名'}],'在线搜索样例姓名')
    assert len(client.requests)==1 and result.status!='completed'

def test_full_name_filter_discards_single_character_dictionary_results():
    seen=[]
    rss='<rss><channel><item><title>周：汉字解释</title><link>https://example.test/word</link><description>周的释义</description></item><item><title>周朝</title><link>https://example.test/history</link></item></channel></rss>'
    def transport(request):seen.append(request);return httpx.Response(200,text=rss)
    result=search_web('周济睿',transport=httpx.MockTransport(transport))
    assert seen[0].url.params['q']=='"周济睿"'
    assert result['status']=='no_relevant_matches' and result['count']==0
    assert result['upstream_count']==2 and result['discarded_count']==2
    assert not result['items'] and '不能推断' in result['note']

def test_exact_name_match_may_be_in_snippet_and_is_not_identity_proof():
    rss='<rss><channel><item><title>校内活动</title><link>https://example.test/event</link><description>出席者：周济睿</description></item></channel></rss>'
    result=search_web('"周济睿" 上海交通大学',transport=httpx.MockTransport(lambda r:httpx.Response(200,text=rss)))
    assert result['status']=='ready' and result['count']==1 and '不证明身份' in result['note']

def test_general_query_does_not_use_literal_filter():
    rss='<rss><channel><item><title>Linear programming overview</title><link>https://example.test/lp</link></item></channel></rss>'
    result=search_web('线性规划入门课程',transport=httpx.MockTransport(lambda r:httpx.Response(200,text=rss)))
    assert result['count']==1 and result['match_policy']=='unverified'

def test_failed_web_search_is_not_reissued_with_changed_keywords():
    registry=Registry();attempts=[]
    original=registry.execute
    def execute(name,args,allowed):
        if name=='mcp_search':
            attempts.append(args)
            raise AgentToolError('上游拦截',category='execution',retryable=False)
        return original(name,args,allowed)
    registry.execute=execute
    second=call('mcp_search');second['tool_calls'][0]['function']['arguments']={'query':'新关键词'}
    client=Client([call('mcp_search'),second,{'content':'本轮检索未完成。'}])
    result=run(client,registry,[{'role':'user','content':'在线搜索样例姓名'}],'在线搜索样例姓名')
    assert len(attempts)==1 and result.status=='completed'

@pytest.mark.parametrize('status',['no_relevant_matches','empty'])
def test_empty_short_web_query_does_not_reissue_same_source(status):
    registry=Registry();original=registry.execute
    def execute(name,args,allowed):
        execution=original(name,args,allowed)
        if name=='mcp_search':execution.result['status']=status
        return execution
    registry.execute=execute
    second=call('mcp_search');second['tool_calls'][0]['function']['arguments']={'query':'新关键词'}
    client=Client([call('mcp_search'),second,{'content':'本轮未取得有效公开证据。'}])
    result=run(client,registry,[{'role':'user','content':'在线搜索样例姓名'}],'在线搜索样例姓名')
    assert result.status=='completed' and registry.calls==['mcp_search']
    assert client.requests[1][1]['tool_choice']=='none'
    assert result.tool_runs[-1]['status']=='rejected'

def test_empty_short_web_query_keeps_other_enabled_source():
    registry=Registry();registry.names=registry.names|{'mcp_other_search'}
    registry.web_tool_names=registry.web_tool_names|{'mcp_other_search'}
    client=Client([call('mcp_search'),call('mcp_other_search'),{'content':'本轮未取得有效公开证据。'}])
    result=run(client,registry,[{'role':'user','content':'在线搜索样例姓名'}],'在线搜索样例姓名')
    assert result.status=='completed' and registry.calls==['mcp_search','mcp_other_search']
    assert {t['function']['name'] for t in client.requests[1][1]['tools']}=={'mcp_other_search'}

def test_exact_subject_is_pinned_when_search_tool_supports_it():
    registry=Registry();original=registry.definitions;seen=[];execute=registry.execute
    def definitions(allowed):
        values=original(allowed)
        for value in values:
            if value['function']['name']=='mcp_search':value['function']['parameters']['properties']={'exact_phrase':{'type':'string'}}
        return values
    def capture(name,args,allowed):seen.append(args);return execute(name,args,allowed)
    registry.definitions=definitions;registry.execute=capture
    request=call('mcp_search');request['tool_calls'][0]['function']['arguments']={'query':'样 上海','exact_phrase':'样'}
    client=Client([request,{'content':'未取得有效证据。'}])
    run(client,registry,[{'role':'user','content':'样例姓名是谁'},{'role':'user','content':'在线搜索'}],'在线搜索')
    assert seen[0]['exact_phrase']=='样例姓名'

def test_structured_mcp_items_are_not_counted_as_one_protocol_object():
    from sqlalchemy import create_engine
    from sjtu_learning_assistant.agent_runtime.tools import ReadOnlyToolRegistry
    engine=create_engine('sqlite://');registry=ReadOnlyToolRegistry(engine)
    body={'provider':'Bing RSS','status':'ready','items':[{'title':'a'},{'title':'b'}]}
    registry._handlers['mcp_fixture']=lambda raw:registry._extension_call(lambda:({'structuredContent':body,'isError':False},{'service':'Bing'}))
    try:
        execution=registry.execute('mcp_fixture',{},('mcp_fixture',))
        assert execution.result_summary['count']==2
        assert execution.result['mcp_source']['service']=='Bing'
    finally:engine.dispose()

def test_frozen_search_self_test_still_rejects_failed_evidence():
    from pathlib import Path
    import sjtu_learning_assistant.search_self_test as self_test
    namespace={'__name__':'frozen_search_check'}
    exec(compile(Path(self_test.__file__).read_text(),self_test.__file__,'exec',optimize=1),namespace)
    with pytest.raises(RuntimeError,match='未取得证据'):
        namespace['require'](False,'未取得证据')
