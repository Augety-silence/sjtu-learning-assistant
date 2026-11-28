"""App 身份下的真实模型/真实聊天界面验收；任务账本在独立本机快照。

仅显式运行本模式才进行付费请求。日历默认只读；--schedule 必须指向
用户已授权的单项日程 JSON，写入只允许该时间、课程、日历，且先查重。
不导出模型密钥、学习正文或其他会话；快照保留付费响应以供本机排查。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--html', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--schedule', type=Path)
    parser.add_argument('--request-access', action='store_true')
    parser.add_argument('--ui-stop-only', action='store_true', help='只复测真实界面的首包前停止与切页返回')
    parser.add_argument('--backend-only', action='store_true', help='真实后端停止/连续补充；不自动操作界面')
    args = parser.parse_args(sys.argv[2:])
    if args.ui_stop_only and (args.backend_only or args.schedule):
        parser.error('--ui-stop-only 不与后端模式或日历写入一起运行')
    if not args.html.is_file() or not args.output.is_absolute() or args.output.exists():
        parser.error('HTML 必须存在；output 必须是尚不存在的绝对路径')
    if sys.platform != 'darwin':
        parser.error('本模式只用于 macOS WKWebView 真机验收')
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSApplicationActivationPolicyRegular
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    import webview
    from desktop_app import DesktopBridge
    from .database import DEFAULT_DATABASE_URL
    from .dashboard_service import DashboardService
    from .native_calendar import native_calendar_service, NativeCalendarError, _date

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    # SQLite backup 在数据库自己的读事务中取得一致快照，不修改原任务状态。
    original = make_url(DEFAULT_DATABASE_URL)
    if original.get_backend_name() != 'sqlite':
        parser.error('为防止影响生产任务，本验收要求本机 SQLite 快照')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    snapshot = args.output.parent / ('live-' + str(time.time_ns()) + '.sqlite3')
    with sqlite3.connect('file:' + original.database + '?mode=ro', uri=True) as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
    engine = create_engine('sqlite:///' + str(snapshot))
    service = DashboardService(engine, ai_workspace_config_path=args.output.parent / 'test-workspace.json')
    native = native_calendar_service()
    authorized = json.loads(args.schedule.read_text()) if args.schedule else None
    result = dict(ok=False, native='WKWebView', model='deepseek-chat', isolated_task_ledger=True,
                  scenarios=[], test_tasks=[], created_sessions=0, calendar_writes=0, permission_requests=0)
    calendar_lock = threading.RLock()
    saved = []
    if authorized:
        _date(authorized['start_at']); _date(authorized['end_at'])
        if '建模与优化' not in authorized['title']:
            parser.error('授权日程必须明确课程')
    class CalendarBoundary:
        def call(self, operation, raw=None):
            raw = raw or {}
            if operation in {'delete', 'connect', 'disconnect'}:
                raise NativeCalendarError('本机验收禁止该操作。')
            if operation != 'save':
                return native.call(operation, raw)
            with calendar_lock:
                if not authorized or raw.get('id'):
                    raise NativeCalendarError('本机验收仅允许显式授权的新建日程。')
                if (raw.get('calendar_id') != authorized['calendar_id'] or
                    _date(raw.get('start_at')) != _date(authorized['start_at']) or
                    _date(raw.get('end_at')) != _date(authorized['end_at']) or
                    '建模与优化' not in str(raw.get('title', '')) or raw.get('alarms', [])):
                    raise NativeCalendarError('创建参数与本次用户授权不符；请保持所选日历、20:30–21:00和无提醒。')
                matches = native.call('events', dict(start_at=authorized['start_at'], end_at=authorized['end_at'], calendar_ids=[authorized['calendar_id']]))['items']
                existing = next((e for e in matches if '建模与优化' in e['title'] and
                                 _date(e['start_at']) == _date(authorized['start_at']) and
                                 _date(e['end_at']) == _date(authorized['end_at'])), None)
                if existing:
                    saved[:] = [existing]
                    return existing
                event = native.call('save', raw)
                result['calendar_writes'] += 1
                saved[:] = [event]
                return event
    # 只限制验收副作用；模型、AgentLoop、参数验证、EventKit 都使用正式实现。
    import sjtu_learning_assistant.dashboard_service as dashboard_module
    dashboard_module.native_calendar_service = lambda: CalendarBoundary()
    desktop_bridge = DesktopBridge(service)
    allowed = {'ai_presets', 'ai_workspace_status', 'settings_status', 'ai_chat_sessions',
               'ai_chat_session', 'ai_chat_new', 'ai_chat_stream_start', 'ai_task_status',
               'ai_chat_steer', 'ai_task_cancel', 'ai_mcp_list', 'ai_skill_list', 'ai_skills',
               'ai_skill_packages', 'ai_extensions', 'ai_usage_summary'}
    class Bridge:
        def invoke(self, action, payload=None):
            if action not in allowed:
                return dict(ok=False, error=dict(code='test_not_allowed', message='本机验收不执行此操作。'))
            calls=result.setdefault('bridge_calls',{}).setdefault(action,dict(started=0,finished=0))
            calls['started']+=1
            response = desktop_bridge.invoke(action, payload)
            calls['finished']+=1
            if action == 'ai_chat_new' and response.get('ok'):
                result['created_sessions'] += 1
            if action == 'ai_chat_stream_start' and response.get('ok'):
                identity = response['data']['task']['id']
                result['test_tasks'].append(identity)
            return response
    def ensure_calendar():
        status=native.call('status')
        if args.request_access and status.get('authorization') != 'full_access':
            native.call('connect');result['permission_requests'] += 1
        if authorized:
            status=native.call('status')
            assert status['enabled'] and status['authorization']=='full_access', 'Mac 日历未获完整访问'
            choices=[c for c in native.call('calendars')['items'] if c['title']==authorized['calendar_title'] and c['writable']]
            assert len(choices)==1, '所选日历不存在、只读或同名不唯一'
            authorized['calendar_id']=choices[0]['id']

    if args.backend_only:
        from Foundation import NSDate, NSRunLoop
        from uuid import uuid4
        result.update(native='AppKit + EventKit', backend_only=True)
        done=threading.Event()
        def task_state(identity): return service.ai_task_status(identity)
        def wait_task(identity, predicate, timeout):
            end=time.monotonic()+timeout
            while True:
                snapshot=task_state(identity)
                if predicate(snapshot):return snapshot
                if time.monotonic()>end:raise RuntimeError('真实模型任务等待超时')
                threading.Event().wait(.1)
        def begin(text):
            chat=service.ai_chat_new('deepseek-chat','standard','general')
            started=service.ai_chat_stream_start(chat['id'],text,model='deepseek-chat',depth='standard',preset_id='general')
            identity=started['task']['id'];result['test_tasks'].append(identity)
            return chat['id'],identity
        def native_check():
            try:
                ensure_calendar()
                session_id,b=begin('真机停止测试：不调用工具，逐项解释100个线性规划概念，每项20字以上。')
                wait_task(b,lambda v:any(e['kind'] in ('content_delta','progress_delta') for e in v['events']),90)
                stop=time.monotonic();service.ai_task_cancel(b)
                stopped=wait_task(b,lambda v:v['task']['status']=='cancelled',15)
                elapsed=time.monotonic()-stop;calls=stopped['task']['usage']['model_calls']
                threading.Event().wait(2)
                after=task_state(b)
                assert after['task']['status']=='cancelled' and after['task']['usage']['model_calls']==calls
                assert not any(e['kind'] in ('content_delta','progress_delta') and e['sequence']>stopped['next_sequence'] for e in after['events'])
                paid=[e for e in after['events'] if e['kind']=='paid_response']
                assert paid and any(e['payload']['response'].get('content') for e in paid), '停止前付费片段未保留'
                result['scenarios'].append(dict(name='真实模型输出中停止',ok=True,stop_seconds=round(elapsed,3),model_calls=calls,paid_fragment_saved=True,no_revival=True))
                if authorized:
                    session_id,c=begin('今天晚上8点半到9点，看一下建模与优化的内容')
                    wait_task(c,lambda v:any(e['kind']=='status' for e in v['events']),20)
                    for text in ('给我制定日程','日历设置时间','确认，写入「'+authorized['calendar_title']+'」，'+authorized['start_at']+' 到 '+authorized['end_at']+'，建模与优化学习，无提醒，直接创建，避免重复。'):
                        service.ai_chat_steer(session_id,c,text,str(uuid4()))
                        threading.Event().wait(.2)
                    finished=wait_task(c,lambda v:v['task']['status'] in ('completed','failed','cancelled'),320)
                    assert finished['task']['status']=='completed','真实日历任务未完成'
                    applied=[rid for e in finished['events'] if e['kind']=='steering_applied' for rid in e['payload']['request_ids']]
                    assert len(applied)==len(set(applied))==3
                    rows=native.call('events',dict(start_at=authorized['start_at'],end_at=authorized['end_at'],calendar_ids=[authorized['calendar_id']]))['items']
                    matches=[e for e in rows if '建模与优化' in e['title'] and _date(e['start_at'])==_date(authorized['start_at']) and _date(e['end_at'])==_date(authorized['end_at'])]
                    assert len(matches)==1,'日程未落地或重复'
                    result['calendar']=dict(readback=True,date=_date(authorized['start_at']).date().isoformat(),time='20:30–21:00',calendar_title=authorized['calendar_title'],matches=1)
                    result['scenarios'].append(dict(name='连续三次补充与原生日历创建',ok=True,model_calls=finished['task']['usage']['model_calls'],steering_count=3))
                result['ok']=True
            except Exception as exc:result.update(error_type=type(exc).__name__,error=str(exc)[:250])
            finally:
                for identity in result['test_tasks']:
                    if task_state(identity)['task']['status'] in ('running','queued'):service.ai_task_cancel(identity)
                args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2));done.set()
        threading.Thread(target=native_check,daemon=True).start()
        while not done.is_set():
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.05))
        service.close();engine.dispose()
        return 0 if result['ok'] else 1

    progress_path=args.output.with_suffix('.progress.json')
    def progress(stage):
        result['stage']=stage
        progress_path.write_text(json.dumps(dict(stage=stage, test_tasks=len(result['test_tasks'])),ensure_ascii=False))
    progress('creating_window')
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    window = webview.create_window('学习助手 · 真实模型中断验收', args.html.resolve().as_uri(),
                                   js_api=Bridge(), width=1440, height=900, text_select=True)

    native_evaluate=window.evaluate_js
    def bounded_evaluate(script):
        event=threading.Event();box={}
        progress('JavaScript: '+script[:110])
        def evaluate():
            try:box['value']=native_evaluate(script)
            except Exception as exc:box['error']=exc
            finally:event.set()
        threading.Thread(target=evaluate,daemon=True).start()
        if not event.wait(10):raise RuntimeError('WKWebView JavaScript 未就绪或未返回：'+script[:100])
        if 'error' in box:raise box['error']
        return box.get('value')

    def wait(expression, timeout=20):
        end = time.monotonic() + timeout
        while not bounded_evaluate(expression):
            if time.monotonic() > end:
                raise RuntimeError('界面等待超时：' + expression[:130])
            threading.Event().wait(.1)
    def js_click(selector):
        wait('Boolean(document.querySelector(' + json.dumps(selector) + '))')
        bounded_evaluate('document.querySelector(' + json.dumps(selector) + ').click()')
    def draft(text):
        bounded_evaluate("(() => {const t=document.querySelector('[aria-label=输入问题]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t," + json.dumps(text) + ");t.dispatchEvent(new Event('input',{bubbles:true}));})()")
        wait("document.querySelector('[aria-label=输入问题]').value===" + json.dumps(text))
        threading.Event().wait(.15)
    def send(text, supplement=False):
        draft(text)
        label = '补充指令' if supplement else '发送消息'
        selector = '[aria-label="' + label + '"]'
        wait('Boolean(document.querySelector(' + json.dumps(selector) + ')&&!document.querySelector(' + json.dumps(selector) + ').disabled)')
        js_click(selector)
    def state(identity): return service.ai_task_status(identity)
    def events(identity, kind): return [e for e in state(identity)['events'] if e['kind'] == kind]
    def task_wait(identity, predicate, timeout=50):
        end = time.monotonic() + timeout
        while not predicate(state(identity)):
            if time.monotonic() > end:
                raise RuntimeError('任务等待超时')
            threading.Event().wait(.1)
    def latest_task(expected):
        end=time.monotonic()+15
        while len(result['test_tasks']) < expected:
            assert time.monotonic() < end, '界面未创建测试任务'
            threading.Event().wait(.1)
        return result['test_tasks'][-1]
    def choose_model():
        js_click('[aria-label^="模型："]')
        wait('Boolean(document.querySelector("[data-ai-floating-menu]"))')
        bounded_evaluate("(() => {const b=Array.from(document.querySelectorAll('[data-ai-floating-menu] [role=option]')).find(b=>!b.closest('[inert],[aria-hidden=true]') && b.textContent.includes('DeepSeek Flash'));if(!b)throw new Error('DeepSeek选项缺失');b.click()})()")
        wait("document.querySelector('[aria-label^=\"模型：\"]').getAttribute('aria-expanded')==='false'")
    def new_chat():
        wait("!document.querySelector('.ai-initial-loading') && Array.from(document.querySelectorAll('button')).some(b=>b.textContent.trim()==='新对话'&&!b.disabled)")
        expected=result['created_sessions']+1
        bounded_evaluate("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='新对话').click()")
        end=time.monotonic()+15
        while result['created_sessions'] < expected:
            assert time.monotonic()<end, '新对话请求未返回';threading.Event().wait(.1)
        threading.Event().wait(.2)
        wait("!document.querySelector('.ai-initial-loading') && document.querySelector('.ai-conversation-heading strong').textContent==='新对话'")
        wait("document.querySelector('[aria-label=输入问题]').value===''")
        choose_model()
    def check():
        progress('checking_calendar')
        try:
            status = native.call('status')
            if args.request_access and status.get('authorization') != 'full_access':
                native.call('connect'); result['permission_requests'] += 1
            if authorized:
                status=native.call('status')
                assert status['enabled'] and status['authorization']=='full_access', 'Mac 日历未获完整访问'
                cals=native.call('calendars')['items']
                choices=[c for c in cals if c['title']==authorized['calendar_title'] and c['writable']]
                assert len(choices)==1, '所选日历不存在、只读或同名不唯一'
                authorized['calendar_id']=choices[0]['id']
            wait("Boolean(document.querySelector('[aria-label=\"AI 助手\"]'))")
            bounded_evaluate("window.localStorage.setItem('ai-chat-new-session','true')")
            js_click('[aria-label="AI 助手"]')
            wait("Boolean(document.querySelector('[aria-label=输入问题]')) && !document.querySelector('.ai-initial-loading')")
            end=time.monotonic()+15
            while result['created_sessions']<1:
                assert time.monotonic()<end,'首个新对话未返回';threading.Event().wait(.1)
            wait("document.querySelector('.ai-conversation-heading strong').textContent==='新对话'")
            choose_model()
            if not args.ui_stop_only:
                send('真机中断测试：不调用工具，逐项解释100个线性规划概念，每项20字以上。')
                task_wait_for_created = time.monotonic()+15
                while not result['test_tasks']:
                    assert time.monotonic()<task_wait_for_created;threading.Event().wait(.1)
                a=result['test_tasks'][-1]
                task_wait(a, lambda s:any(e['kind'] in ('content_delta','progress_delta') for e in s['events']),80)
                send('取消列举。不要调用工具，只回复：已收到补充A。',True)
                task_wait(a, lambda s:s['task']['status'] in ('completed','failed','cancelled'),100)
                first=state(a)
                assert first['task']['status']=='completed' and events(a,'steering_applied')
                assert '已收到补充A' in json.dumps(first['task'].get('result'), ensure_ascii=False), '模型没有遵循补充'
                assert len(result['test_tasks'])==1, '补充不得另开任务'
                result['scenarios'].append(dict(name='输出中补充',ok=True,model_calls=first['task']['usage']['model_calls']))
                wait("Boolean(document.querySelector('[aria-label=发送消息]'))")
                new_chat()
            send('真机停止测试：不调用工具，逐项解释100个线性规划概念，每项20字以上。')
            b=latest_task(1 if args.ui_stop_only else 2)
            task_wait(b,lambda s:any(e['kind']=='status' and e['payload'].get('state')=='model_call' for e in s['events']))
            stop=time.monotonic();js_click('[aria-label=停止本次任务]')
            task_wait(b,lambda s:s['task']['status']=='cancelled',15)
            stopped=state(b);elapsed=time.monotonic()-stop
            calls=stopped['task']['usage']['model_calls']
            # 离开/返回界面后也不能恢复已取消任务。
            bounded_evaluate("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='返回工作台').click()")
            js_click('[aria-label="AI 助手"]')
            threading.Event().wait(3)
            after=state(b)
            assert after['task']['status']=='cancelled' and after['task']['usage']['model_calls']==calls
            assert not any(e['kind'] in ('content_delta','progress_delta') and e['sequence']>stopped['next_sequence'] for e in after['events'])
            result['scenarios'].append(dict(name='首包前停止与切页恢复',ok=True,stop_seconds=round(elapsed,3),no_revival=True))
            if authorized:
                wait("Boolean(document.querySelector('[aria-label=发送消息]'))")
                new_chat();send('今天晚上8点半到9点，看一下建模与优化的内容')
                c=latest_task(3)
                task_wait(c,lambda s:any(e['kind']=='status' and e['payload'].get('state')=='model_call' for e in s['events']))
                for text in ('给我制定日程','日历设置时间', '确认，写入「'+authorized['calendar_title']+'」，'+authorized['start_at']+' 到 '+authorized['end_at']+'，建模与优化学习，无提醒；直接创建，避免重复。'):
                    send(text,True)
                task_wait(c,lambda s:s['task']['status'] in ('completed','failed','cancelled'),310)
                final=state(c)
                assert final['task']['status']=='completed', '真实模型任务未完成'
                applied=[rid for e in events(c,'steering_applied') for rid in e['payload']['request_ids']]
                assert len(applied)==len(set(applied))==3
                assert len(result['test_tasks'])==3
                rows=native.call('events',dict(start_at=authorized['start_at'],end_at=authorized['end_at'],calendar_ids=[authorized['calendar_id']]))['items']
                matches=[e for e in rows if '建模与优化' in e['title'] and _date(e['start_at'])==_date(authorized['start_at']) and _date(e['end_at'])==_date(authorized['end_at'])]
                assert len(matches)==1, '日程没有落地或出现重复'
                result['calendar']=dict(readback=True,date=_date(authorized['start_at']).date().isoformat(),time='20:30–21:00',calendar_title=authorized['calendar_title'],matches=len(matches))
                result['scenarios'].append(dict(name='连续三次补充与原生日历创建',ok=True,model_calls=final['task']['usage']['model_calls'],steering_count=len(applied)))
            result['javascript_errors']=bounded_evaluate('window.__chatSteeringFixtureErrors || []')
            assert not result['javascript_errors']
            result['ok']=True
        except Exception as exc:
            result.update(error_type=type(exc).__name__,error=str(exc)[:250])
            try:
                result['ui_controls']=bounded_evaluate("Array.from(document.querySelectorAll('.ai-composer-actions button')).map(b=>({name:b.getAttribute('aria-label'),disabled:b.disabled}))")
            except Exception:pass
        finally:
            for tid in result['test_tasks']:
                try:
                    if state(tid)['task']['status'] in ('running','queued'):service.ai_task_cancel(tid)
                except Exception:pass
            args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
            window.destroy()
    def readiness_timeout():
        if not result['test_tasks'] and not args.output.exists():
            result.update(error_type='RuntimeError',error='原生验收窗口45秒内未开始测试：'+result.get('stage','unknown'))
            args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
            # 尚无测试请求，无付费响应或原生产任务需要终止。
            os._exit(2)
    timer=threading.Timer(45,readiness_timeout);timer.daemon=True;timer.start()
    progress('starting_webview')
    webview.start(check,gui='cocoa')
    timer.cancel()
    service.close();engine.dispose()
    return 0 if result['ok'] else 1
