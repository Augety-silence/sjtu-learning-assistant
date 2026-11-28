"""实际 App 身份下的 EventKit / AI / MCP 验收；仅创建并清理自身临时事件。"""
from __future__ import annotations
import json
import argparse
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4


def read_only_check(service, now):
    """Read calendar state only; never request access or mutate events."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    status = service.call('status')
    result = dict(ok=False, read_only=True, calendar_writes=0,
                  permission_requests=0, event_titles_exported=False,
                  date=start.date().isoformat(),
                  status={k: status.get(k) for k in ('supported', 'enabled', 'authorization')})
    if not status.get('enabled') or status.get('authorization') != 'full_access':
        return result
    events = service.call('events', dict(start_at=start.isoformat(), end_at=end.isoformat()))
    evening = []
    for event in events['items']:
        a = datetime.fromisoformat(event['start_at']).astimezone(now.tzinfo)
        b = datetime.fromisoformat(event['end_at']).astimezone(now.tzinfo)
        if not event['all_day'] and b > start.replace(hour=18) and a < end:
            evening.append([a.strftime('%H:%M'), b.strftime('%H:%M')])
    result.update(ok=True, today_events=len(events['items']), evening_events=len(evening),
                  evening_time_windows=evening, truncated=events.get('truncated', False))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only', action='store_true', help='只读当前日程，不申请权限或创建事件')
    parser.add_argument('--request-access', action='store_true', help='经用户明确授权，先唤起系统日历授权；不创建事件')
    parser.add_argument('--output', help='将只读摘要保存到一个尚不存在的绝对路径')
    args = parser.parse_args(sys.argv[2:])
    if args.output and (not args.read_only or not os.path.isabs(args.output)):
        parser.error('--output requires --read-only and an absolute path')
    if args.request_access and not args.read_only:
        parser.error('--request-access requires --read-only')
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
            if args.read_only:
                requested = False
                if args.request_access:
                    requested = service.call('status')['authorization'] != 'full_access'
                    service.call('connect')
                result.update(read_only_check(service, datetime.now().astimezone()))
                result['permission_requests'] = int(requested)
                return
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
    if args.output:
        # Exclusive creation prevents a diagnostic from replacing user files.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['ok'] else 1
