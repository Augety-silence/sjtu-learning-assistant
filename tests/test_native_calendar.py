import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from sjtu_learning_assistant.native_calendar import NativeCalendarService, NativeCalendarError, _revision

class MemoryCalendar:
    generation = 0
    access = "full_access"
    def __init__(self): self.rows = {}; self.writes = 0
    def authorization(self): return self.access
    def request_access(self):
        if self.access == "denied": raise NativeCalendarError("拒绝访问")
        self.access = "full_access"
    def calendars(self): return [{"id":"work","title":"日历","writable":True},{"id":"readonly","title":"订阅","writable":False}]
    def events(self, start, end, ids): return [dict(e) for e in self.rows.values() if not ids or e['calendar_id'] in ids]
    def get(self, event_id, occurrence_start=None):
        if event_id not in self.rows: raise NativeCalendarError("已删除")
        return dict(self.rows[event_id])
    def save(self, data, event_id=None, span="this", occurrence_start=None):
        self.writes += 1
        row = {**data, "id": event_id or str(uuid4()), "writable":True, "recurring":False, "calendar_title":"日历"}
        row["revision"] = _revision(row)
        self.rows[row['id']] = row
        self.generation += 1
        return dict(row)
    def delete(self, event_id, span="this", occurrence_start=None):
        self.rows.pop(event_id); self.writes += 1; self.generation += 1

class NativeCalendarTests(unittest.TestCase):
    def test_alarm_schema_uses_negative_seconds_and_matches_validation(self):
        from sjtu_learning_assistant.native_calendar import CALENDAR_SCHEMAS
        schema = CALENDAR_SCHEMAS['native_calendar_save'][1]['properties']['alarms']
        self.assertEqual(5, schema['maxItems'])
        self.assertEqual(-604800, schema['items']['minimum'])
        self.assertEqual(0, schema['items']['maximum'])
        self.connected()
        row = self.service.call('save', {**self.create, 'alarms': [-1800]})
        self.assertEqual([-1800], row['alarms'])
        for alarms in ([30], [{'minutes': 30}], ['-1800']):
            with self.subTest(alarms=alarms), self.assertRaisesRegex(NativeCalendarError, '秒数数组'):
                self.service.call('save', {**self.create, 'request_id': str(uuid4()), 'alarms': alarms})
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.adapter = MemoryCalendar()
        self.service = NativeCalendarService(Path(self.temp.name)/'calendar.sqlite3', adapter=self.adapter, platform='darwin')
        self.create = dict(request_id=str(uuid4()),calendar_id='work',title='实验讨论',start_at='2026-11-28T10:00:00+08:00',end_at='2026-11-28T11:00:00+08:00',timezone='Asia/Shanghai',alarms=[-900])
    def connected(self): self.service.call('connect')
    def test_access_is_explicit_and_disconnect_blocks_ai_writes(self):
        with self.assertRaises(NativeCalendarError): self.service.call('save',self.create)
        self.connected(); self.service.call('disconnect')
        with self.assertRaises(NativeCalendarError): self.service.call('save',self.create)
        self.assertEqual(0,self.adapter.writes)
    def test_roundtrip_native_change_and_version_conflict(self):
        self.connected(); row=self.service.call('save',self.create)
        changed=self.service.call('save',dict(id=row['id'],revision=row['revision'],request_id=str(uuid4()),title='新标题'))
        self.assertEqual('新标题',changed['title'])
        native=self.adapter.rows[row['id']]; native['title']='系统日历修改'; native['revision']=_revision(native)
        result=self.service.call('events',dict(start_at=self.create['start_at'],end_at=self.create['end_at']))
        self.assertEqual('系统日历修改',result['items'][0]['title'])
        with self.assertRaisesRegex(NativeCalendarError,'其他位置修改'):
            self.service.call('delete',dict(id=row['id'],revision=changed['revision'],request_id=str(uuid4())))
        self.service.call('delete',dict(id=row['id'],revision=native['revision'],request_id=str(uuid4())))
        self.assertFalse(self.adapter.rows)
    def test_idempotence_across_services_and_request_reuse(self):
        self.connected(); row=self.service.call('save',self.create)
        other=NativeCalendarService(self.service.path,adapter=self.adapter,platform='darwin')
        self.assertEqual(row,other.call('save',self.create)); self.assertEqual(1,self.adapter.writes)
        with self.assertRaisesRegex(NativeCalendarError,'不同修改'): other.call('save',{**self.create,'title':'修改'})
    def test_invalid_time_range_timezone_alarm_and_readonly_are_rejected(self):
        self.connected()
        for update in ({'end_at':self.create['start_at']},{'start_at':'2026-11-28T10:00:00'},{'timezone':'invalid'},{'alarms':[100]},{'calendar_id':'readonly'},{'title':''},{'all_day':True},{'title':42},{'request_id':'random'}):
            with self.subTest(update=update), self.assertRaises(NativeCalendarError): self.service.call('save',{**self.create,**update})
        self.assertEqual(0,self.adapter.writes)
    def test_unknown_parameters_and_bounded_queries(self):
        self.connected()
        with self.assertRaises(NativeCalendarError): self.service.call('save',{**self.create,'shell':'rm'})
        with self.assertRaises(NativeCalendarError): self.service.call('events',{'start_at':'2020-01-01T00:00:00Z','end_at':'2026-01-01T00:00:00Z'})
    def test_denied_permission_is_not_recorded_as_connected(self):
        self.adapter.access='denied'
        with self.assertRaises(NativeCalendarError): self.connected()
        self.assertFalse(self.service.call('status')['enabled'])
    def test_windows_status_is_explicit(self):
        s=NativeCalendarService(self.service.path,platform='win32')
        self.assertFalse(s.call('status')['supported'])
        self.assertEqual('win32', s.call('status')['platform'])
        self.assertEqual('local_ics', s.call('status')['fallback'])
        self.assertFalse(s.call('status')['enabled'])
        with self.assertRaisesRegex(NativeCalendarError,'Windows'): s.call('connect')
    def test_all_day_timezone_midnight_and_reminders(self):
        self.connected()
        row=self.service.call('save',{**self.create,'all_day':True,'start_at':'2026-11-28T00:00:00+08:00','end_at':'2026-11-29T00:00:00+08:00'})
        self.assertTrue(row['all_day']); self.assertEqual([-900],row['alarms'])


class CalendarAgentContractTests(unittest.TestCase):
    setUp = NativeCalendarTests.setUp
    connected = NativeCalendarTests.connected
    def test_ai_and_mcp_share_native_authorization_and_write_annotations(self):
        from sqlalchemy import create_engine
        from sjtu_learning_assistant.agent_runtime.tools import ReadOnlyToolRegistry, AgentToolError
        from sjtu_learning_assistant.mcp_server import MCPServer
        engine=create_engine('sqlite://'); self.addCleanup(engine.dispose)
        registry=ReadOnlyToolRegistry(engine,native_calendar=self.service)
        tools=tuple(registry.names)
        with self.assertRaises(AgentToolError): registry.execute('native_calendar_save',self.create,tools)
        self.connected()
        result=registry.execute('native_calendar_save',self.create,tools).result
        server=MCPServer(registry)
        listed=server.handle_request({'jsonrpc':'2.0','id':1,'method':'tools/list'})['result']['tools']
        definitions={t['name']:t for t in listed}
        self.assertFalse(definitions['native_calendar_save']['annotations']['readOnlyHint'])
        self.assertTrue(definitions['native_calendar_events']['annotations']['readOnlyHint'])
        response=server.handle_request({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'native_calendar_get','arguments':{'id':result['id']}}})['result']
        self.assertEqual(result['title'],response['structuredContent']['title'])
        with self.assertRaises(AgentToolError): registry.execute('native_calendar_delete',{'id':result['id']},('native_calendar_events',))

if __name__ == '__main__': unittest.main()
