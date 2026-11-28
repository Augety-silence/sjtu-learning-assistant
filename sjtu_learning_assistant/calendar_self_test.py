"""实际 App 身份下的 EventKit / AI / MCP 验收；仅创建并清理自身临时事件。"""
from __future__ import annotations
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4


def main():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from Foundation import NSDate, NSRunLoop
    from sqlalchemy import create_engine
    from sjtu_learning_assistant.native_calendar import EventKitAdapter, native_calendar_service
    from sjtu_learning_assistant.agent_runtime.tools import ReadOnlyToolRegistry
    from sjtu_learning_assistant.mcp_server import MCPServer
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    result = {"ok": False}
    done = threading.Event()
    def check():
        engine = create_engine('sqlite://')
        service = native_calendar_service()
        created = None
        try:
            service.call('connect')
            calendars = service.call('calendars')['items']
            calendar = next(c for c in calendars if c['writable'])
            start = (datetime.now(timezone.utc)+timedelta(days=1)).replace(second=0,microsecond=0)
            payload = dict(request_id=str(uuid4()), calendar_id=calendar['id'], title='学习助手连接验证（自动清理）', start_at=start.isoformat(), end_at=(start+timedelta(minutes=10)).isoformat(),timezone='Asia/Shanghai',alarms=[-1800])
            registry = ReadOnlyToolRegistry(engine,native_calendar=service)
            allowed = tuple(registry.names)
            created = registry.execute('native_calendar_save',payload,allowed).result
            assert service.call('save',payload)['id']==created['id']
            changed = registry.execute('native_calendar_save',dict(id=created['id'],revision=created['revision'],request_id=str(uuid4()),title='学习助手 API 修改验证（自动清理）'),allowed).result
            # 第二个 EventStore 写入，代表其他原生客户端修改；再经 MCP 读取真实存储。
            other = EventKitAdapter()
            external = other.get(changed['id'])
            external['title']='学习助手系统回读验证（自动清理）'
            other.save(external,changed['id'])
            server = MCPServer(registry)
            response = server.handle_request(dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='native_calendar_get',arguments=dict(id=changed['id']))))
            row = response['result']['structuredContent']
            assert row['title']==external['title']
            assert row['alarms']==[-1800]
            registry.execute('native_calendar_delete',dict(id=row['id'],revision=row['revision'],request_id=str(uuid4())),allowed)
            created=None
            listed=service.call('events',dict(start_at=start.isoformat(),end_at=(start+timedelta(minutes=11)).isoformat()))['items']
            assert not any(e['id']==row['id'] for e in listed)
            result.update(ok=True,native_access=True,ai_create_update=True,mcp_native_readback=True,idempotence=True,reminder=True,cleanup=True)
        except Exception as exc:
            result.update(error_type=type(exc).__name__,message=str(exc) if type(exc).__name__=='NativeCalendarError' else '原生日历验收未通过')
        finally:
            if created:
                try:
                    row=service.call('get',dict(id=created['id']))
                    service.call('delete',dict(id=row['id'],revision=row['revision'],request_id=str(uuid4())))
                    result['cleanup']=True
                except Exception:
                    result['cleanup']=False
            engine.dispose()
            done.set()
    threading.Thread(target=check,daemon=True).start()
    limit=time.monotonic()+150
    while not done.is_set() and time.monotonic()<limit:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
    if not done.is_set(): result.update(message='系统授权等待超时')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['ok'] else 1
