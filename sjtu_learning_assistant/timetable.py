"""Safe timetable providers, parsers, preview tokens and local persistence."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.models import (
    CanonicalCourse,
    TimetableCourse,
    TimetableImportAudit,
    TimetableImportRun,
    TimetableSession,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_IMPORT_BYTES = 10 * 1024 * 1024
PREVIEW_TTL_SECONDS = 10 * 60


class TimetableError(RuntimeError):
    pass


class TimetableProvider(ABC):
    @abstractmethod
    def status(self) -> dict[str, Any]: ...


class SJTUEducationAPIProvider(TimetableProvider):
    """Disabled by design until an approved jAccount application is configured."""

    provider = "sjtu_education_api"

    def status(self) -> dict[str, Any]:
        return {
            "state": "awaiting_configuration",
            "provider": self.provider,
            "lastSyncedAt": None,
            "message": "等待开放平台配置",
            "supportsOAuth": False,
            "hasLocalData": False,
        }


@dataclass(frozen=True)
class ParsedCourse:
    source_id: str
    code: str | None
    name: str
    term: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class ParsedSession:
    course_source_id: str
    source_id: str
    week: int | None
    day: int | None
    period: int | None
    duration: int | None
    start_at: datetime
    finish_at: datetime
    classroom: str | None
    raw: dict[str, Any]


def _text(value: object, limit: int = 512) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return None
    result = str(value).strip()
    return result[:limit] if result else None


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _datetime(value: object, date: object = None) -> datetime:
    text = _text(value, 64)
    if not text:
        raise TimetableError("课表时间字段缺失。")
    if date and re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        text = f"{date}T{text}"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise TimetableError("课表时间格式无效。") from exc
    return (
        result.replace(tzinfo=SHANGHAI)
        if result.tzinfo is None
        else result.astimezone(SHANGHAI)
    )


def _lesson_rows(payload: object) -> list[Mapping[str, Any]]:
    value = payload.get("data", payload) if isinstance(payload, Mapping) else payload
    value = (
        value.get("lessons", value.get("items"))
        if isinstance(value, Mapping)
        else value
    )
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise TimetableError("JSON 不是可识别的 SJTU lessons 官方响应结构。")
    return value


def parse_sjtu_lessons(
    payload: object,
) -> tuple[list[ParsedCourse], list[ParsedSession], list[str]]:
    """Keep each official weekly occurrence; do not infer recurring rules."""
    courses: list[ParsedCourse] = []
    sessions: list[ParsedSession] = []
    warnings: list[str] = []
    for index, lesson in enumerate(_lesson_rows(payload)):
        course_data = (
            lesson.get("course")
            if isinstance(lesson.get("course"), Mapping)
            else lesson
        )
        source_id = _text(
            lesson.get("id") or lesson.get("lessonId") or course_data.get("id"), 255
        )
        name = _text(course_data.get("name") or lesson.get("courseName"))
        code = _text(course_data.get("code") or lesson.get("courseCode"), 128)
        if not source_id or not name:
            warnings.append(f"第 {index + 1} 门课程缺少标识或名称，已跳过。")
            continue
        courses.append(
            ParsedCourse(
                source_id, code, name, _text(lesson.get("term"), 128), dict(lesson)
            )
        )
        occurrences = lesson.get("schedules", lesson.get("sessions", []))
        if not isinstance(occurrences, list):
            warnings.append(f"课程 {name} 的上课安排格式无效，已跳过。")
            continue
        for occurrence in occurrences:
            if not isinstance(occurrence, Mapping):
                continue
            start = _datetime(
                occurrence.get("startAt") or occurrence.get("start"),
                occurrence.get("date"),
            )
            finish = _datetime(
                occurrence.get("finishAt")
                or occurrence.get("endAt")
                or occurrence.get("end"),
                occurrence.get("date"),
            )
            if finish <= start:
                raise TimetableError("课表结束时间必须晚于开始时间。")
            stable = _text(occurrence.get("id") or occurrence.get("sourceId"), 512)
            if not stable:
                identity = "|".join(
                    (
                        source_id,
                        start.isoformat(),
                        finish.isoformat(),
                        _text(occurrence.get("classroom") or occurrence.get("location"))
                        or "",
                    )
                )
                stable = hashlib.sha256(identity.encode()).hexdigest()
            sessions.append(
                ParsedSession(
                    source_id,
                    stable,
                    _integer(occurrence.get("week")),
                    _integer(occurrence.get("day") or occurrence.get("weekday")),
                    _integer(occurrence.get("period")),
                    _integer(occurrence.get("duration")),
                    start,
                    finish,
                    _text(occurrence.get("classroom") or occurrence.get("location")),
                    dict(occurrence),
                )
            )
    if not courses:
        raise TimetableError("文件中没有可导入的课程。")
    return courses, sessions, warnings


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _ics_datetime(value: str) -> datetime:
    utc = value.endswith("Z")
    raw = value[:-1] if utc else value
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            return (
                datetime.strptime(raw, fmt)
                .replace(tzinfo=timezone.utc if utc else SHANGHAI)
                .astimezone(SHANGHAI)
            )
        except ValueError:
            pass
    raise TimetableError("ICS 日期时间格式无效。")


def parse_ics(text: str) -> tuple[list[ParsedCourse], list[ParsedSession], list[str]]:
    if "BEGIN:VCALENDAR" not in text:
        raise TimetableError("文件不是有效的 ICS 日历。")
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None:
            key, separator, value = line.partition(":")
            key = key.split(";", 1)[0].upper()
            if separator and key in {"UID", "SUMMARY", "DTSTART", "DTEND", "LOCATION"}:
                current[key] = value.replace("\\,", ",").replace("\\n", "\n")
    courses: dict[str, ParsedCourse] = {}
    sessions: list[ParsedSession] = []
    warnings: list[str] = []
    for index, event in enumerate(events):
        uid, summary = _text(event.get("UID"), 512), _text(event.get("SUMMARY"))
        if not uid or not summary or not event.get("DTSTART") or not event.get("DTEND"):
            warnings.append(f"第 {index + 1} 个 VEVENT 缺少字段，已跳过。")
            continue
        course_id = (
            "ics:" + hashlib.sha256(summary.casefold().encode()).hexdigest()[:24]
        )
        courses.setdefault(
            course_id, ParsedCourse(course_id, None, summary, None, dict(event))
        )
        start, finish = _ics_datetime(event["DTSTART"]), _ics_datetime(event["DTEND"])
        if finish <= start:
            raise TimetableError("ICS 结束时间必须晚于开始时间。")
        sessions.append(
            ParsedSession(
                course_id,
                uid,
                None,
                start.isoweekday(),
                None,
                None,
                start,
                finish,
                _text(event.get("LOCATION")),
                dict(event),
            )
        )
    if not courses:
        raise TimetableError("ICS 中没有可导入的 VEVENT。")
    return list(courses.values()), sessions, warnings


def normalize_course_name(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())


class TimetableService:
    def __init__(self, engine: Engine, sample_path: Path | None = None) -> None:
        self.engine = engine
        self.sample_path = sample_path
        self._previews: dict[str, tuple[Any, ...]] = {}
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        result = SJTUEducationAPIProvider().status()
        with Session(self.engine) as session:
            result["hasLocalData"] = bool(
                session.scalar(select(func.count(TimetableSession.id)))
            )
            latest = session.scalar(select(func.max(TimetableImportRun.created_at)))
            result["lastSyncedAt"] = latest.isoformat() if latest else None
        return result

    def _preview_bytes(self, data: bytes, suffix: str) -> dict[str, Any]:
        if not data or len(data) > MAX_IMPORT_BYTES:
            raise TimetableError("文件为空或超过 10 MiB 限制。")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise TimetableError("导入文件必须是 UTF-8 文本。") from exc
        if suffix == ".json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise TimetableError("JSON 格式无效。") from exc
            courses, sessions, warnings = parse_sjtu_lessons(payload)
            format_name = "sjtu-lessons-json"
        elif suffix == ".ics":
            courses, sessions, warnings = parse_ics(text)
            format_name = "ics"
        else:
            raise TimetableError("仅支持 .json 和 .ics 文件。")
        preview_id = uuid.uuid4().hex
        with self._lock:
            now = monotonic()
            self._previews = {
                key: value for key, value in self._previews.items() if value[0] > now
            }
            self._previews[preview_id] = (
                now + PREVIEW_TTL_SECONDS,
                format_name,
                hashlib.sha256(data).hexdigest(),
                courses,
                sessions,
                warnings,
            )
        return {
            "previewId": preview_id,
            "format": format_name,
            "courses": len(courses),
            "sessions": len(sessions),
            "warnings": warnings,
        }

    def preview_local_file(self, path: str) -> dict[str, Any]:
        if type(path) is not str or not path or len(path) > 4096 or "\x00" in path:
            raise TimetableError("本地文件路径无效。")
        candidate = Path(path)
        if (
            not candidate.is_absolute()
            or candidate.is_symlink()
            or not candidate.is_file()
        ):
            raise TimetableError("请选择普通本地文件。")
        size = candidate.stat().st_size
        if size <= 0 or size > MAX_IMPORT_BYTES:
            raise TimetableError("文件为空或超过 10 MiB 限制。")
        return self._preview_bytes(candidate.read_bytes(), candidate.suffix.lower())

    def load_bundled_sample(self) -> dict[str, Any]:
        if self.sample_path is None:
            raise TimetableError("未提供内置示例。")
        return self._preview_bytes(self.sample_path.read_bytes(), ".json")

    @staticmethod
    def _canonical(session: Session, code: str | None, name: str) -> CanonicalCourse:
        normalized = normalize_course_name(name)
        exact = (
            session.scalar(
                select(CanonicalCourse).where(
                    CanonicalCourse.course_code == code,
                    CanonicalCourse.normalized_name == normalized,
                )
            )
            if code
            else None
        )
        if exact:
            return exact
        record = CanonicalCourse(
            course_code=code,
            normalized_name=normalized,
            display_name=name,
            mapping_status="mapped" if code else "pending",
        )
        session.add(record)
        session.flush()
        return record

    def commit_preview(self, preview_id: str) -> dict[str, Any]:
        if (
            type(preview_id) is not str
            or re.fullmatch(r"[0-9a-f]{32}", preview_id) is None
        ):
            raise TimetableError("预览标识无效。")
        with self._lock:
            preview = self._previews.pop(preview_id, None)
        if preview is None or preview[0] <= monotonic():
            raise TimetableError("预览已过期，请重新选择文件。")
        _, format_name, digest, courses, sessions, warnings = preview
        imported_courses = imported_sessions = updated_sessions = 0
        with Session(self.engine) as db, db.begin():
            run = TimetableImportRun(
                provider="local",
                source_format=format_name,
                source_digest=digest,
                status="committed",
                warnings=warnings,
            )
            db.add(run)
            db.flush()
            by_source: dict[str, TimetableCourse] = {}
            for item in courses:
                record = db.scalar(
                    select(TimetableCourse).where(
                        TimetableCourse.source == "local",
                        TimetableCourse.source_id == item.source_id,
                    )
                )
                action = "updated"
                if record is None:
                    canonical = self._canonical(db, item.code, item.name)
                    record = TimetableCourse(
                        source="local",
                        source_id=item.source_id,
                        canonical_course_id=canonical.id
                        if canonical.mapping_status == "mapped"
                        else None,
                        course_code=item.code,
                        course_name=item.name,
                        term=item.term,
                        mapping_status=canonical.mapping_status,
                        raw_data=item.raw,
                    )
                    db.add(record)
                    db.flush()
                    imported_courses += 1
                    action = "inserted"
                else:
                    (
                        record.course_code,
                        record.course_name,
                        record.term,
                        record.raw_data,
                    ) = item.code, item.name, item.term, item.raw
                by_source[item.source_id] = record
                db.add(
                    TimetableImportAudit(
                        import_run_id=run.id,
                        entity_type="course",
                        source_id=item.source_id,
                        action=action,
                        details={},
                    )
                )
            for item in sessions:
                course = by_source[item.course_source_id]
                record = db.scalar(
                    select(TimetableSession).where(
                        TimetableSession.timetable_course_id == course.id,
                        TimetableSession.source_id == item.source_id,
                    )
                )
                values = {
                    "week": item.week,
                    "day": item.day,
                    "period": item.period,
                    "duration": item.duration,
                    "start_at": item.start_at,
                    "finish_at": item.finish_at,
                    "classroom": item.classroom,
                    "raw_data": item.raw,
                }
                if record is None:
                    record = TimetableSession(
                        timetable_course_id=course.id,
                        source_id=item.source_id,
                        **values,
                    )
                    db.add(record)
                    imported_sessions += 1
                    action = "inserted"
                else:

                    def differs(
                        key: str, value: Any, current_record: TimetableSession = record
                    ) -> bool:
                        current = getattr(current_record, key)
                        if isinstance(current, datetime) and isinstance(
                            value, datetime
                        ):
                            if current.tzinfo is None:
                                current = current.replace(tzinfo=SHANGHAI)
                            return current.astimezone(timezone.utc) != value.astimezone(
                                timezone.utc
                            )
                        return current != value

                    changed = any(differs(key, value) for key, value in values.items())
                    for key, value in values.items():
                        setattr(record, key, value)
                    updated_sessions += int(changed)
                    action = "updated" if changed else "unchanged"
                db.add(
                    TimetableImportAudit(
                        import_run_id=run.id,
                        entity_type="session",
                        source_id=item.source_id,
                        action=action,
                        details={},
                    )
                )
            run.imported_courses, run.imported_sessions, run.updated_sessions = (
                imported_courses,
                imported_sessions,
                updated_sessions,
            )
        return {
            "status": "committed",
            "importedCourses": imported_courses,
            "importedSessions": imported_sessions,
            "updatedSessions": updated_sessions,
        }

    def schedule(self, start: datetime, end: datetime) -> dict[str, Any]:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise TimetableError("课表时间范围无效。")
        with Session(self.engine) as db:
            rows = db.execute(
                select(TimetableSession, TimetableCourse)
                .join(TimetableCourse)
                .where(
                    TimetableSession.start_at < end, TimetableSession.finish_at > start
                )
                .order_by(TimetableSession.start_at)
            ).all()
            events = [
                {
                    "id": f"timetable:{item.id}",
                    "title": course.course_name,
                    "courseName": course.course_name,
                    "startAt": item.start_at.isoformat(),
                    "endAt": item.finish_at.isoformat(),
                    "location": item.classroom,
                    "periodLabel": f"第{item.period}节" if item.period else None,
                    "eventType": "course",
                    "source": course.source,
                    "canonicalCourseId": course.canonical_course_id,
                }
                for item, course in rows
            ]
        return {"events": events}
