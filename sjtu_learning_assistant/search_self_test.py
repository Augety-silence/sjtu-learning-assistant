"""显式真机检索验收：独立账本、合成旧邮件回复、真实模型及只读搜索。"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time


def require(condition, message):
    # PyInstaller optimize=1 会移除 assert；验收条件必须在发行二进制中执行。
    if not condition:
        raise RuntimeError(message)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(sys.argv[2:])
    if sys.platform!='darwin' or not args.output.is_absolute() or args.output.exists():
        parser.error('仅在Mac运行；output必须是尚不存在的绝对路径')
    if not args.query.strip() or len(args.query)>80:
        parser.error('query需要为1至80字')
    from AppKit import NSApplication,NSApplicationActivationPolicyAccessory
    from Foundation import NSDate,NSRunLoop
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session
    from .database import DEFAULT_DATABASE_URL,APP_SUPPORT_DIR
    from .dashboard_service import DashboardService
    from .external_mcp import ExternalMCP,write_json
    from .models import AIChatMessage
    app=NSApplication.sharedApplication();app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    work=args.output.parent/('search-'+str(time.time_ns()));work.mkdir(mode=0o700)
    original=make_url(DEFAULT_DATABASE_URL)
    if original.get_backend_name()!='sqlite':parser.error('必须使用本机SQLite快照')
    snapshot=work/'chat.sqlite3'
    with sqlite3.connect('file:'+original.database+'?mode=ro',uri=True) as source,sqlite3.connect(snapshot) as dest:source.backup(dest)
    engine=create_engine('sqlite:///'+str(snapshot))
    service=DashboardService(engine,agent_skills_path=work/'skills.json',ai_workspace_config_path=work/'workspace.json')
    original_mcp=ExternalMCP(APP_SUPPORT_DIR/'external-mcp.json')
    data=original_mcp.load()
    for identity,row in data.items():
        is_web=any(word in identity.casefold() for word in ('bing','duckduckgo','联网','搜索'))
        row['enabled']=bool(is_web and row.get('enabled'))
        row['permission']='read_only'
        if 'bing' in identity.casefold() and row['config'].get('args',[])[:1]==['--web-search-mcp']:
            row['config']['command']=sys.executable
    write_json(work/'external-mcp.json',data)
    result=dict(ok=False,model='deepseek-chat',query=args.query,isolated_task_ledger=True,private_canary_exported=False,requests=[],calendar_writes=0)
    canary='fixture-private-email-'+str(time.time_ns())
    original_factory=service.ai_client_factory
    def factory(**kwargs):
        client=original_factory(**kwargs)
        for name in ('chat_completion','chat_completion_stream'):
            real=getattr(client,name)
            def checked(messages,_real=real,**options):
                require(canary not in str(messages),'旧邮件内容进入联网模型请求')
                names=[item['function']['name'] for item in (options.get('tools') or [])]
                require(all(name.startswith('mcp_') for name in names),'联网请求开放了本地工具')
                result['requests'].append(dict(tools=names,max_tokens=options.get('max_tokens'),private_canary_present=False))
                return _real(messages,**options)
            setattr(client,name,checked)
        return client
    service.ai_client_factory=factory
    done=threading.Event();identity=None
    def check():
        nonlocal identity
        try:
            manager=service._external_mcp()
            for name,row in manager.load().items():
                if row.get('enabled'):manager.connect(name)
            session=service.ai_chat_new('deepseek-chat','standard','general')
            with Session(engine) as db,db.begin():
                db.add(AIChatMessage(session_id=session['id'],role='user',content=args.query+'是谁',sequence=1))
                db.add(AIChatMessage(session_id=session['id'],role='assistant',content='旧邮件片段（合成验收数据）：'+canary,sequence=2))
            started=time.monotonic()
            task=service.ai_chat_stream_start(session['id'],'在线搜索',model='deepseek-chat',depth='standard',preset_id='general')
            identity=task['task']['id']
            end=time.monotonic()+300
            while True:
                state=service.ai_task_status(identity)
                if state['task']['status'] in ('completed','failed','cancelled'):break
                if time.monotonic()>end:raise RuntimeError('真实检索任务等待超时')
                threading.Event().wait(.2)
            require(state['task']['status']=='completed','真实模型检索未完成：'+str(state['task'].get('error') or state['task']['status'])[:160])
            chat_result=state['task']['result'].get('chat_result',{})
            assistant=chat_result.get('assistant_message',{})
            require(assistant.get('role')=='assistant' and assistant.get('content'),'任务未保存有效助手回复')
            reply=assistant['content']
            require(canary not in reply,'旧邮件内容出现在联网回答')
            runs=chat_result.get('trace',{}).get('tool_runs',[])
            require(runs and all(r['tool_name'].startswith('mcp_') and r['phase']=='model' for r in runs),'出现本地预检索或未调用搜索服务')
            empty_sources={r['tool_name'] for r in runs if r['status']=='ok' and r.get('result_summary',{}).get('count')==0}
            require(all(sum(r['tool_name']==name and r['status']=='ok' for r in runs)==1 for name in empty_sources),'无有效匹配后仍重复请求同一搜索来源')
            result.update(ok=True,seconds=round(time.monotonic()-started,2),model_calls=state['task']['usage']['model_calls'],reply=reply,
                          local_tools_called=0,reply_chars=len(reply),automatic_continuations=sum(e['kind']=='status' and e['payload'].get('state')=='continuing' for e in state['events']),
                          tool_results=[dict(name=r['tool_name'],status=r['status'],count=r.get('result_summary',{}).get('count')) for r in runs])
        except Exception as exc:
            result.update(error_type=type(exc).__name__,error=str(exc)[:200])
        finally:
            if identity and service.ai_task_status(identity)['task']['status'] in ('running','queued'):service.ai_task_cancel(identity)
            fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
            done.set()
    threading.Thread(target=check,daemon=True).start()
    while not done.is_set():NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.05))
    service.close();engine.dispose()
    return 0 if result['ok'] else 1
