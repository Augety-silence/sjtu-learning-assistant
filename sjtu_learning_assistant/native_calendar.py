"""macOS EventKit 本机日历。桌面 API 与 stdio MCP 共用授权、版本和幂等记录。"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from platformdirs import user_data_path


class NativeCalendarError(RuntimeError):
    pass


def _date(value):
    if not isinstance(value, str) or len(value) > 80:
        raise NativeCalendarError("请输入带时区的 ISO 日期时间。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed
    except ValueError:
        raise NativeCalendarError("请输入带时区的 ISO 日期时间。") from None


def _revision(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


class EventKitAdapter:
    def __init__(self):
        import EventKit
        import Foundation
        self.ek = EventKit
        self.f = Foundation
        self.store = EventKit.EKEventStore.alloc().init()
        self.generation = 0
        self._observer = Foundation.NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            EventKit.EKEventStoreChangedNotification, self.store, None, self._changed)

    def _changed(self, notification):
        self.generation += 1

    def authorization(self):
        status = int(self.ek.EKEventStore.authorizationStatusForEntityType_(self.ek.EKEntityTypeEvent))
        if status == int(self.ek.EKAuthorizationStatusFullAccess):
            return "full_access"
        if status == int(self.ek.EKAuthorizationStatusWriteOnly):
            return "write_only"
        return {0: "not_determined", 1: "restricted", 2: "denied"}.get(status, "unknown")

    def request_access(self):
        done = threading.Event()
        outcome = []
        def completed(granted, error):
            outcome.append(bool(granted))
            done.set()
        if hasattr(self.store, "requestFullAccessToEventsWithCompletion_"):
            self.store.requestFullAccessToEventsWithCompletion_(completed)
        else:
            self.store.requestAccessToEntityType_completion_(self.ek.EKEntityTypeEvent, completed)
        if not done.wait(120):
            raise NativeCalendarError("等待系统日历授权超时，请检查 macOS 权限弹窗后重试。")
        if not outcome[0]:
            raise NativeCalendarError("未获得日历读写权限，请在系统设置的隐私与安全性中允许日历完整访问。")

    def calendars(self):
        return [{"id": str(c.calendarIdentifier()), "title": str(c.title()), "writable": bool(c.allowsContentModifications()), "source": str(c.source().title())}
                for c in self.store.calendarsForEntityType_(self.ek.EKEntityTypeEvent)]

    def _nsdate(self, value):
        return self.f.NSDate.dateWithTimeIntervalSince1970_(_date(value).timestamp())

    def _row(self, e):
        def iso(d):
            return datetime.fromtimestamp(float(d.timeIntervalSince1970()), timezone.utc).isoformat()
        row = {"id": str(e.eventIdentifier()), "calendar_id": str(e.calendar().calendarIdentifier()),
               "title": str(e.title() or "无标题日程"), "start_at": iso(e.startDate()), "end_at": iso(e.endDate()),
               "all_day": bool(e.isAllDay()), "timezone": str(e.timeZone().name()) if e.timeZone() else str(self.f.NSTimeZone.localTimeZone().name()),
               "location": str(e.location() or ""), "notes": str(e.notes() or ""),
               "calendar_title": str(e.calendar().title()), "writable": bool(e.calendar().allowsContentModifications()),
               "recurring": bool(e.hasRecurrenceRules()),
               "alarms": [float(a.relativeOffset()) for a in e.alarms() or [] if a.absoluteDate() is None]}
        row["revision"] = _revision(row)
        return row

    def events(self, start, end, calendar_ids):
        calendars = [c for c in self.store.calendarsForEntityType_(self.ek.EKEntityTypeEvent) if not calendar_ids or str(c.calendarIdentifier()) in calendar_ids]
        predicate = self.store.predicateForEventsWithStartDate_endDate_calendars_(self._nsdate(start), self._nsdate(end), calendars)
        return [self._row(e) for e in self.store.eventsMatchingPredicate_(predicate)]

    def _event(self, event_id, occurrence_start=None):
        e = self.store.eventWithIdentifier_(event_id)
        if e and occurrence_start and e.hasRecurrenceRules():
            target = _date(occurrence_start).timestamp()
            predicate = self.store.predicateForEventsWithStartDate_endDate_calendars_(self.f.NSDate.dateWithTimeIntervalSince1970_(target-1), self.f.NSDate.dateWithTimeIntervalSince1970_(target+1), [e.calendar()])
            e = next((item for item in self.store.eventsMatchingPredicate_(predicate) if str(item.eventIdentifier()) == event_id and abs(float(item.startDate().timeIntervalSince1970())-target)<1), None)
        return e

    def get(self, event_id, occurrence_start=None):
        e = self._event(event_id, occurrence_start)
        if not e:
            raise NativeCalendarError("日程已删除或标识已改变，请重新读取日历。")
        return self._row(e)

    def save(self, data, event_id=None, span="this", occurrence_start=None):
        e = self._event(event_id, occurrence_start) if event_id else self.ek.EKEvent.eventWithEventStore_(self.store)
        if not e:
            raise NativeCalendarError("日程已删除，请刷新后重试。")
        calendar = self.store.calendarWithIdentifier_(data["calendar_id"])
        if not calendar or not calendar.allowsContentModifications():
            raise NativeCalendarError("该日历只读或已删除。")
        if not event_id and data.get("request_id"):
            e.setURL_(self.f.NSURL.URLWithString_("sjtu-calendar://request/"+data["request_id"]))
        e.setCalendar_(calendar)
        e.setTitle_(data["title"])
        e.setStartDate_(self._nsdate(data["start_at"]))
        e.setEndDate_(self._nsdate(data["end_at"]))
        e.setAllDay_(data["all_day"])
        e.setTimeZone_(self.f.NSTimeZone.timeZoneWithName_(data["timezone"]))
        e.setLocation_(data["location"])
        e.setNotes_(data["notes"])
        e.setAlarms_([self.ek.EKAlarm.alarmWithRelativeOffset_(v) for v in data["alarms"]])
        ok, error = self.store.saveEvent_span_commit_error_(e, self.ek.EKSpanFutureEvents if span == "future" else self.ek.EKSpanThisEvent, True, None)
        if not ok:
            raise NativeCalendarError("系统日历保存失败，请检查日历账户连接。")
        return self._row(e)

    def find_request(self, data, request_id):
        predicate = self.store.predicateForEventsWithStartDate_endDate_calendars_(self._nsdate(data["start_at"]), self._nsdate(data["end_at"]), [self.store.calendarWithIdentifier_(data["calendar_id"])])
        marker = "sjtu-calendar://request/"+request_id
        return next((self._row(e) for e in self.store.eventsMatchingPredicate_(predicate) if e.URL() and str(e.URL().absoluteString()) == marker), None)

    def delete(self, event_id, span="this", occurrence_start=None):
        e = self._event(event_id, occurrence_start)
        if not e:
            return
        ok, error = self.store.removeEvent_span_commit_error_(e, self.ek.EKSpanFutureEvents if span == "future" else self.ek.EKSpanThisEvent, True, None)
        if not ok:
            raise NativeCalendarError("系统日历删除失败。")


class NativeCalendarService:
    def __init__(self, path: Path | None = None, adapter=None, platform=None):
        self.platform = platform or sys.platform
        self.path = path or user_data_path("sjtu-learning-assistant") / "native_calendar.sqlite3"
        self.adapter = adapter
        self.lock = threading.RLock()

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO config VALUES (1,0)")
        db.commit()
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _enabled(self):
        with self._db() as db:
            return bool(db.execute("SELECT enabled FROM config WHERE id=1").fetchone()[0])

    def _adapter(self):
        if self.platform != "darwin":
            raise NativeCalendarError("原生日历目前支持 macOS，Windows 接入待定。")
        if self.adapter is None:
            try:
                self.adapter = EventKitAdapter()
            except ImportError:
                raise NativeCalendarError("原生日历组件未安装，请使用包含 EventKit 的新版应用。") from None
        return self.adapter

    def _require(self):
        adapter = self._adapter()
        if not self._enabled() or adapter.authorization() != "full_access":
            raise NativeCalendarError("请先在日程页面连接 Mac 日历并授权完整访问。")
        return adapter

    def call(self, operation, raw=None):
        raw = {} if raw is None else raw
        if not isinstance(raw, dict):
            raise NativeCalendarError("日历参数必须是对象。")
        allowed = {
            "status": set(), "connect": set(), "disconnect": set(), "calendars": set(),
            "events": {"start_at", "end_at", "calendar_ids"}, "get": {"id", "occurrence_start"},
            "save": {"id", "revision", "request_id", "calendar_id", "title", "start_at", "end_at", "all_day", "timezone", "location", "notes", "alarms", "span", "occurrence_start"},
            "delete": {"id", "revision", "span", "request_id", "occurrence_start"},
        }
        if operation not in allowed or set(raw) - allowed[operation]:
            raise NativeCalendarError("不支持的日历操作或参数。")
        if "occurrence_start" in raw:
            _date(raw["occurrence_start"])
        with self.lock:
            if operation == "status":
                if self.platform != "darwin":
                    return {"supported": False, "enabled": False, "authorization": "unsupported", "platform": self.platform, "fallback": "local_ics"}
                adapter = self._adapter()
                return {"supported": True, "enabled": self._enabled(), "authorization": adapter.authorization(), "generation": adapter.generation, "refresh_seconds": 5}
            if operation == "disconnect":
                with self._db() as db:
                    db.execute("UPDATE config SET enabled=0 WHERE id=1")
                return {"enabled": False}
            if operation == "connect":
                adapter = self._adapter()
                if adapter.authorization() != "full_access":
                    adapter.request_access()
                with self._db() as db:
                    db.execute("UPDATE config SET enabled=1 WHERE id=1")
                return self.call("status")
            adapter = self._require()
            if operation == "calendars":
                return {"items": adapter.calendars()}
            if operation == "events":
                start, end = _date(raw.get("start_at")), _date(raw.get("end_at"))
                if end <= start or (end-start).total_seconds() > 366 * 86400:
                    raise NativeCalendarError("查询范围须大于零且不超过一年。")
                ids = raw.get("calendar_ids", [])
                if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(i, str) or len(i)>512 for i in ids):
                    raise NativeCalendarError("日历标识无效。")
                known_ids = {c["id"] for c in adapter.calendars()}
                if any(i not in known_ids for i in ids):
                    raise NativeCalendarError("日历标识已变化，请重新读取日历列表。")
                rows = sorted(adapter.events(raw["start_at"], raw["end_at"], ids), key=lambda e: e["start_at"])
                return {"items": rows[:2000], "truncated": len(rows)>2000, "generation": adapter.generation}
            if operation == "get":
                return adapter.get(self._id(raw), raw.get("occurrence_start"))
            return self._write(adapter, operation, raw)

    @staticmethod
    def _id(raw):
        value = raw.get("id")
        if not isinstance(value, str) or not value or len(value)>512:
            raise NativeCalendarError("日程标识无效。")
        return value

    def _write(self, adapter, operation, raw):
        request = raw.get("request_id")
        try:
            UUID(request)
        except (ValueError, TypeError, AttributeError):
            raise NativeCalendarError("写入需要唯一 UUID request_id，重试时复用同一个值。") from None
        fingerprint = _revision({"operation": operation, "payload": raw})
        # 跨 MCP 与桌面进程序列化；事务记录响应，重复请求不再次写入。
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT fingerprint,result FROM requests WHERE id=?", (request,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise NativeCalendarError("同一个 request_id 不能用于不同修改。")
                return json.loads(prior[1])
            span = raw.get("span", "this")
            if span not in {"this", "future"}:
                raise NativeCalendarError("重复日程范围只能为 this 或 future。")
            event_id = raw.get("id")
            previous = None
            if event_id or operation == "delete":
                event_id = self._id(raw)
                previous = adapter.get(event_id, raw.get("occurrence_start"))
                if previous["recurring"] and not raw.get("occurrence_start"):
                    raise NativeCalendarError("修改重复日程需要 occurrence_start 指定本次开始时间。")
                if raw.get("revision") != previous["revision"]:
                    raise NativeCalendarError("日程已在其他位置修改，请重新读取并确认变更后重试。")
                if not previous["writable"]:
                    raise NativeCalendarError("该日历不允许修改。")
            if operation == "delete":
                adapter.delete(event_id, span, raw.get("occurrence_start"))
                result = {"deleted": True, "id": event_id}
            else:
                data = {key: raw.get(key, previous.get(key) if previous else default) for key, default in {
                    "calendar_id": None, "title": None, "start_at": None, "end_at": None,
                    "all_day": False, "timezone": "Asia/Shanghai", "location": "", "notes": "", "alarms": []}.items()}
                for key, limit in {"calendar_id":512,"title":200,"location":1000,"notes":10000}.items():
                    if not isinstance(data[key], str) or len(data[key])>limit:
                        raise NativeCalendarError("日程标题、日历或备注无效。")
                if not data["title"].strip():
                    raise NativeCalendarError("日程标题不能为空。")
                start, end = _date(data["start_at"]), _date(data["end_at"])
                if end<=start:
                    raise NativeCalendarError("结束时间必须晚于开始时间。")
                try:
                    ZoneInfo(data["timezone"])
                except (KeyError, ValueError, TypeError):
                    raise NativeCalendarError("请使用有效 IANA 时区，如 Asia/Shanghai。") from None
                if type(data["all_day"]) is not bool:
                    raise NativeCalendarError("全天日程参数无效。")
                if data["all_day"] and (start.astimezone(ZoneInfo(data["timezone"])).time().isoformat() != "00:00:00" or end.astimezone(ZoneInfo(data["timezone"])).time().isoformat() != "00:00:00"):
                    raise NativeCalendarError("全天日程应使用当地午夜起止，结束日期不包含在内。")
                alarms = data["alarms"]
                if not isinstance(alarms, list) or len(alarms)>5 or any(type(a) not in (int,float) or not -604800<=a<=0 for a in alarms):
                    raise NativeCalendarError("alarms 必须是秒数数组，使用非正数，最多五项；提前30分钟为 [-1800]，无提醒为 []，范围 [-604800, 0]。")
                if not any(c["id"] == data["calendar_id"] and c["writable"] for c in adapter.calendars()):
                    raise NativeCalendarError("目标日历只读或不存在。")
                data["request_id"] = request
                recovered = adapter.find_request(data, request) if not event_id and hasattr(adapter, "find_request") else None
                result = recovered or adapter.save(data, event_id, span, raw.get("occurrence_start"))
            db.execute("INSERT INTO requests VALUES (?,?,?)", (request, fingerprint, json.dumps(result, ensure_ascii=False)))
            return result


_shared = None
_shared_lock = threading.Lock()
def native_calendar_service():
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = NativeCalendarService()
        return _shared


CALENDAR_SCHEMAS = {
    "native_calendar_status": ("读取 Mac 原生日历连接状态，不弹出授权请求。", {"type":"object","properties":{},"additionalProperties":False}),
    "native_calendar_calendars": ("列出已授权的 Mac 日历及只读状态。", {"type":"object","properties":{},"additionalProperties":False}),
    "native_calendar_events": ("实时读取 Mac 日历事件，与系统日历双向共享；查询范围最长一年。", {"type":"object","properties":{"start_at":{"type":"string"},"end_at":{"type":"string"},"calendar_ids":{"type":"array","items":{"type":"string"}}},"required":["start_at","end_at"],"additionalProperties":False}),
    "native_calendar_get": ("读取指定系统日程与最新 revision，修改前必须读取。重复日程需提供 occurrence_start 指定本次开始时间。", {"type":"object","properties":{"id":{"type":"string"},"occurrence_start":{"type":"string"}},"required":["id"],"additionalProperties":False}),
    "native_calendar_save": ("按用户明确指示创建或更新已授权 Mac 日程。写后返回原生事件与 revision。更新需要 id 与最新 revision；request_id 使用 UUID，重试复用。重复日程需要 occurrence_start，默认仅本次，future 修改此后全部需用户明确指定。不会邀请参会人。", {"type":"object","properties":{**{k:{"type":"string"} for k in ("id","revision","request_id","calendar_id","title","start_at","end_at","timezone","location","notes","occurrence_start")},"all_day":{"type":"boolean"},"alarms":{"type":"array","description":"相对开始时间的秒数数组，必须为非正数。提前30分钟 [-1800]，提前15分钟 [-900]，开始时 [0]，无提醒 []。最多5项，最早提前7天。不得传提醒对象、字符串或正分钟数。","maxItems":5,"items":{"type":"number","minimum":-604800,"maximum":0}},"span":{"type":"string","enum":["this","future"]}},"required":["request_id"],"additionalProperties":False}),
    "native_calendar_delete": ("按用户明确指示删除系统日程；需要最新 revision 与唯一 UUID request_id。默认仅本次；future 需要用户指定。", {"type":"object","properties":{**{k:{"type":"string"} for k in ("id","revision","request_id","occurrence_start")},"span":{"type":"string","enum":["this","future"]}},"required":["id","revision","request_id"],"additionalProperties":False}),
}
