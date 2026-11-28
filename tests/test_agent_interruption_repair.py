"""晚到确认、参数纠正、重复输出和工具边界停止的复现。"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sjtu_learning_assistant.agent_runtime.loop import AgentLoop
from sjtu_learning_assistant.agent_runtime.presets import AgentPreset
from sjtu_learning_assistant.agent_runtime.tools import AgentToolError
from sjtu_learning_assistant.agent_runtime.output_guard import has_output_loop
from sjtu_learning_assistant.ai_tasks import AITaskCancelled
from sjtu_learning_assistant.chat_steering import ChatSteeringInterrupt
from sjtu_learning_assistant.native_calendar import _date, CALENDAR_SCHEMAS

class Registry:
    names=frozenset({'list_courses','native_calendar_events','native_calendar_save','native_calendar_calendars'})
    def __init__(self):self.calls=[]
    def definitions(self,allowed):
        return [{'type':'function','function':{'name':n,'parameters':deepcopy(CALENDAR_SCHEMAS[n][1]) if n in CALENDAR_SCHEMAS else {}}} for n in allowed]
    def execute(self,n,args,allowed):
        self.calls.append((n,args))
        if n=='native_calendar_events' and '+' not in args.get('start_at',''):
            raise AgentToolError('时间必须含 +08:00',category='execution')
        result={'items':[{'id':'canonical-id','title':'个人','writable':True}]} if n=='native_calendar_calendars' else {'saved':n=='native_calendar_save'}
        return SimpleNamespace(name=n,result=result,arguments_summary={},result_summary={'status':'ok'})

class Client:
    def __init__(self,replies):self.replies,self.requests=iter(replies),[]
    def chat_completion(self,messages,**options):
        self.requests.append((deepcopy(messages),deepcopy(options)))
        value=next(self.replies)
        return value() if callable(value) else value

class Control:
    def __init__(self):
        self.pending=[];self.responses=[];self.ledger=Mock();self.task_id='test';self.cancelled=False
    def has_pending(self):return bool(self.pending)
    def check_request(self):
        if self.cancelled:raise AITaskCancelled('已停止')
        if self.pending:raise ChatSteeringInterrupt('补充')
    def take_pending(self):
        result,self.pending=self.pending,[]
        return result
    def record_response(self,value):self.responses.append(value)
    def seal_if_idle(self):return not self.pending
    def finish_pending(self):return self.take_pending()

def tool(n,args=None):return {'content':'','tool_calls':[{'id':n,'function':{'name':n,'arguments':args or {}}}]}
def run(client,registry=None,control=None,steps=6,callback=None):
    registry=registry or Registry()
    return AgentLoop(client,registry,AgentPreset('general','测试','',tuple(registry.names),''),max_steps=steps).run(
        [{'role':'user','content':'测试'}],user_text='测试',max_tokens=500,temperature=0,steering_control=control,event_callback=callback)

def test_calendar_error_near_last_round_can_repair_then_write():
    registry=Registry();client=Client([tool('list_courses'),tool('native_calendar_events',{'start_at':'2026-11-28T20:30:00'}),
        tool('native_calendar_events',{'start_at':'2026-11-28T20:30:00+08:00'}),tool('native_calendar_save'),{'content':'日程已创建'}])
    result=run(client,registry,steps=3)
    assert result.status=='completed' and result.steps<=3
    assert registry.calls[-1][0]=='native_calendar_save'
    assert client.requests[2][1]['tools'] and 'next_action' in client.requests[2][0][-1]['content']

def test_late_confirmation_restores_tools_without_stale_summary():
    control=Control()
    def interrupt():
        control.pending=[{'content':'确认，创建日程','request_id':'one'}];raise ChatSteeringInterrupt()
    client=Client([tool('list_courses'),interrupt,tool('native_calendar_save'),{'content':'已创建'}])
    assert run(client,control=control,steps=2).status=='completed'
    assert not client.requests[1][1]['tools'] and client.requests[2][1]['tools']
    assert not any('本轮最后一次请求' in m.get('content','') for m in client.requests[2][0])
    assert any('确认，创建日程' in m.get('content','') for m in client.requests[2][0])

def test_successful_read_reused_but_writes_not_cached():
    registry=Registry();client=Client([tool('list_courses'),tool('list_courses'),tool('native_calendar_save'),tool('native_calendar_save'),{'content':'完成'}])
    result=run(client,registry)
    assert [n for n,a in registry.calls].count('list_courses')==1
    assert [n for n,a in registry.calls].count('native_calendar_save')==2
    assert result.tool_runs[1]['reused']

def test_looping_truncation_not_continued_and_paid_fragment_saved():
    loop=('让我重新核对日历列表中的正确标识，之前写错的标识不能再次用于写入操作。\n')*45
    events=[];control=Control();client=Client([{'content':loop,'response_metadata':{'finish_reason':'length'}},{'content':'操作尚未完成。'}])
    assert run(client,control=control,callback=lambda k,v:events.append((k,v))).status=='completed'
    assert len(client.requests)==2 and not any(v.get('state')=='continuing' for k,v in events)
    assert control.responses[0]['content']==loop
    assert not any(loop in m.get('content','') for m in client.requests[1][0])

def test_second_loop_stops_without_third_request():
    loop=('这个标识需要仔细核对，不能重复使用不存在的日历标识进行创建操作。\n')*30
    client=Client([{'content':loop},{'content':loop}])
    assert run(client).status=='max_steps' and len(client.requests)==2

def test_stream_loop_interrupted_before_full_reply():
    class Streaming:
        def chat_completion_stream(self,messages,on_delta,**options):
            line='让我重新核对日历列表中的正确标识，不能凭空猜测不存在的目标日历。\n'
            for i in range(100):self.emitted=i;on_delta('content',line)
            return {'content':line*100}
    client=Streaming();run(client)
    assert client.emitted<30

def test_cancel_between_tools_prevents_second_write():
    control=Control();registry=Registry();original=registry.execute
    def execute(*args):
        result=original(*args);control.cancelled=True;return result
    registry.execute=execute;call=tool('list_courses');call['tool_calls']+=tool('native_calendar_save')['tool_calls']
    with pytest.raises(AITaskCancelled):run(Client([call]),registry,control)
    assert [n for n,a in registry.calls]==['list_courses']

def test_datetime_schema_explicit_and_naive_rejected():
    for n in ('native_calendar_events','native_calendar_save'):
        schema=CALENDAR_SCHEMAS[n][1]['properties']['start_at']
        assert schema['format']=='date-time' and '+08:00' in schema['description']
    with pytest.raises(Exception,match='时区'):_date('2026-11-28T20:30:00')

def test_normal_code_and_nonrepeating_narrative_accepted():
    assert not has_output_loop('```python\n'+'print("example")\n'*500+'```')
    assert not has_output_loop('\n'.join(f'第{i}项：使用唯一的变量名称 x{i} 解释约束及不同场景。' for i in range(100)))

def test_calendar_id_schema_bound_to_writable_native_ids():
    client=Client([tool('native_calendar_calendars'),{'content':'读取完成'}]);run(client)
    save=next(d for d in client.requests[1][1]['tools'] if d['function']['name']=='native_calendar_save')
    assert save['function']['parameters']['properties']['calendar_id']['enum']==['canonical-id']


def test_early_supplement_also_reserves_bounded_action_recovery():
    control=Control();control.pending=[{'content':'创建日程，已确认','request_id':'early'}]
    client=Client([tool('list_courses',{'limit':i+1}) for i in range(6)]+[tool('native_calendar_save'),{'content':'已创建'}])
    registry=Registry();result=run(client,registry,control)
    assert result.status=='completed' and registry.calls[-1][0]=='native_calendar_save'
    assert len(client.requests)==8 and result.steps<=6
