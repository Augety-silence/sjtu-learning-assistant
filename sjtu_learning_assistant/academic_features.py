"""Reusable Canvas academic features with server-side authorization gates.

The service intentionally keeps exports inside a backend-owned temporary directory and
never accepts an output path from a caller.  Privileged submission reads and writes
resolve the current user's Canvas enrollment on every operation; caller-provided role
claims and stale local data are never accepted for authorization.
"""
from __future__ import annotations

import csv
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from sjtu_learning_assistant.canvas_client import CanvasError, CanvasNetworkError
from sjtu_learning_assistant.models import Course

T = TypeVar("T")
_STAFF_ROLES = frozenset({"teacher", "ta"})
_ROLE_ALIASES = {
    "teacherenrollment": "teacher",
    "teacher": "teacher",
    "instructor": "teacher",
    "教师": "teacher",
    "taenrollment": "ta",
    "ta": "ta",
    "teachingassistant": "ta",
    "助教": "ta",
    "studentenrollment": "student",
    "student": "student",
    "学生": "student",
    "observerenrollment": "observer",
    "observer": "observer",
    "designerenrollment": "designer",
    "designer": "designer",
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SECRET = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+|bearer\s+|(?:access_)?token\s*[=:]\s*)"
    r"[^\s,;&]+"
)
_QUERY_SECRET = re.compile(r"(?i)([?&](?:access_)?token=)[^&#\s]+")


class AcademicFeatureError(RuntimeError):
    """A safe-to-display academic service error."""


class AcademicPermissionError(AcademicFeatureError):
    """The current Canvas enrollment is not allowed to perform the operation."""


class AcademicValidationError(AcademicFeatureError):
    """Caller input is invalid."""


class AcademicVerificationError(AcademicFeatureError):
    """Canvas accepted a write but the subsequent read did not confirm it."""


class AcademicOutcomeUncertain(AcademicFeatureError):
    """A write may have succeeded and must not be retried automatically."""


@dataclass(frozen=True)
class CourseCapabilities:
    course_id: str
    course_name: str
    roles: tuple[str, ...]
    role_source: str
    can_view_calendar: bool
    can_view_members: bool
    can_view_submissions: bool
    can_manage_grades: bool
    can_comment_submissions: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CalendarAggregation:
    month_start: str
    month_end: str
    month_events: tuple[dict[str, Any], ...]
    upcoming_start: str
    upcoming_end: str
    upcoming_events: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ExportArtifact:
    path: Path
    filename: str
    content_type: str
    row_count: int

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


@dataclass(frozen=True)
class SubmissionWriteResult:
    submission: dict[str, Any]
    grade_verified: bool
    comment_verified: bool
    recovered_after_uncertain_write: bool = False



def redact_error_message(message: object) -> str:
    """Remove common credential forms and control characters from a short message."""
    value = _CONTROL.sub(" ", str(message or "")).strip()
    value = _SECRET.sub(lambda match: f"{match.group(1)}[REDACTED]", value)
    value = _QUERY_SECRET.sub(lambda match: f"{match.group(1)}[REDACTED]", value)
    return value[:300] or "Canvas 学术功能请求失败。"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _records(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise AcademicFeatureError("Canvas API 返回了无法识别的列表数据。")
    if not all(isinstance(item, Mapping) for item in value):
        raise AcademicFeatureError("Canvas API 返回的列表包含无效项目。")
    return [dict(item) for item in value]


def _canvas_id(value: int | str, label: str) -> str:
    if isinstance(value, bool):
        raise AcademicValidationError(f"{label}格式无效。")
    text = str(value).strip()
    if not text.isdigit() or int(text) <= 0:
        raise AcademicValidationError(f"{label}格式无效。")
    return text


def _role(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[\s_-]+", "", value).casefold()
    return _ROLE_ALIASES.get(normalized)


def _enrollment_roles(payload: Mapping[str, Any]) -> tuple[str, ...]:
    roles: set[str] = set()
    enrollments = payload.get("enrollments")
    if isinstance(enrollments, (list, tuple)):
        for enrollment_value in enrollments:
            enrollment = _mapping(enrollment_value)
            for key in ("type", "role", "role_name"):
                canonical = _role(enrollment.get(key))
                if canonical:
                    roles.add(canonical)
    for key in ("enrollment_type", "enrollment_role", "role"):
        canonical = _role(payload.get(key))
        if canonical:
            roles.add(canonical)
    return tuple(sorted(roles))


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _event_time(event: Mapping[str, Any]) -> datetime | None:
    assignment = _mapping(event.get("assignment"))
    for value in (
        event.get("end_at") or event.get("endAt"),
        event.get("start_at") or event.get("startAt"),
        assignment.get("due_at"),
        event.get("created_at"),
    ):
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _event_identity(event: Mapping[str, Any]) -> tuple[str, str, str]:
    assignment_id = _mapping(event.get("assignment")).get("id")
    if assignment_id is not None and not isinstance(assignment_id, bool):
        return ("assignment", str(assignment_id), "")
    event_id = event.get("id")
    if event_id is not None and not isinstance(event_id, bool):
        return ("event", str(event_id), str(event.get("context_code") or ""))
    return (
        "fallback",
        str(event.get("context_code") or ""),
        f"{event.get('title') or ''}|{event.get('start_at') or event.get('end_at') or ''}",
    )


def _numeric_grade(submission: Mapping[str, Any]) -> float | None:
    for key in ("score", "grade", "posted_grade"):
        value = submission.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _display_grade(submission: Mapping[str, Any]) -> str:
    for key in ("grade", "score", "posted_grade"):
        value = submission.get(key)
        if value is not None and not isinstance(value, (dict, list, tuple)):
            return str(value)
    return ""


def _csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    # Prevent spreadsheet formula execution while preserving genuine numeric cells.
    if isinstance(value, str) and text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


class AcademicFeatureService:
    """CanvasHelper-like read/export/grading features for desktop backends.

    ``export_root`` is backend configuration, not request input.  Public export methods
    accept only a plain filename and reject path separators and traversal.
    """

    def __init__(
        self,
        canvas: Any,
        *,
        engine: Engine | None = None,
        export_root: str | Path | None = None,
    ) -> None:
        self.canvas = canvas
        self.engine = engine
        if export_root is None:
            root = Path(tempfile.mkdtemp(prefix="sjtu-learning-academic-"))
        else:
            root = Path(export_root).expanduser()
            root.mkdir(parents=True, exist_ok=True)
        self.export_root = root.resolve()
        try:
            self.export_root.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def _read_call(operation: Callable[[], T], message: str) -> T:
        try:
            return operation()
        except AcademicFeatureError:
            raise
        except CanvasError as exc:
            raise AcademicFeatureError(redact_error_message(exc)) from exc
        except Exception as exc:
            raise AcademicFeatureError(message) from exc

    def _get_object(self, path: str, *, params: Any = None) -> dict[str, Any]:
        method = getattr(self.canvas, "_get_object", None)
        if not callable(method):
            raise AcademicFeatureError("Canvas 客户端不支持所需的对象读取接口。")
        value = self._read_call(lambda: method(path, params=params), "Canvas 对象读取失败。")
        if not isinstance(value, Mapping):
            raise AcademicFeatureError("Canvas API 返回了无法识别的对象数据。")
        return dict(value)

    def _paginate(self, path: str, *, params: Any = None) -> list[dict[str, Any]]:
        method = getattr(self.canvas, "_paginate", None)
        if not callable(method):
            raise AcademicFeatureError("Canvas 客户端不支持所需的分页接口。")
        value = self._read_call(lambda: method(path, params=params), "Canvas 列表读取失败。")
        return _records(value)

    def _local_course_payload(self, course_id: str) -> dict[str, Any] | None:
        if self.engine is None:
            return None
        try:
            with Session(self.engine) as session:
                record = session.scalar(select(Course).where(Course.source_id == course_id))
                if record is None:
                    return None
                payload = dict(_mapping(record.raw_data))
                payload.setdefault("id", record.source_id)
                payload.setdefault("name", record.name)
                return payload
        except SQLAlchemyError:
            return None

    def _canvas_course_payload(self, course_id: str) -> dict[str, Any]:
        direct: dict[str, Any] | None = None
        course_method = getattr(self.canvas, "course", None)
        if callable(course_method):
            value = self._read_call(
                lambda: course_method(course_id), "Canvas 课程权限信息读取失败。"
            )
            if isinstance(value, Mapping):
                direct = dict(value)
                if _enrollment_roles(direct):
                    return direct

        courses_method = getattr(self.canvas, "courses", None)
        if callable(courses_method):
            values = self._read_call(courses_method, "Canvas 课程权限信息读取失败。")
            for course in _records(values):
                if str(course.get("id")) == course_id:
                    return course
        if direct is not None:
            return direct
        raise AcademicFeatureError("无法从 Canvas 确认课程权限。")

    @staticmethod
    def _capabilities(
        course_id: str,
        roles: Sequence[str],
        source: str,
        course_name: object = None,
    ) -> CourseCapabilities:
        role_set = set(roles)
        staff = bool(role_set & _STAFF_ROLES) and source == "canvas"
        clean_name = " ".join(str(course_name or "").split()).strip()[:300]
        return CourseCapabilities(
            course_id=course_id,
            course_name=clean_name or f"课程 {course_id}",
            roles=tuple(sorted(role_set)),
            role_source=source,
            can_view_calendar=bool(role_set),
            can_view_members=bool(role_set),
            can_view_submissions=staff,
            can_manage_grades=staff,
            can_comment_submissions=staff,
        )

    def course_capabilities(self, course_id: int | str) -> CourseCapabilities:
        """Resolve roles from Canvas, falling back to local raw_data for display only."""
        normalized_id = _canvas_id(course_id, "课程 ID")
        try:
            payload = self._canvas_course_payload(normalized_id)
        except AcademicFeatureError:
            local = self._local_course_payload(normalized_id)
            if local is None:
                raise
            return self._capabilities(
                normalized_id,
                _enrollment_roles(local),
                "local_cache",
                local.get("name"),
            )
        roles = _enrollment_roles(payload)
        local = self._local_course_payload(normalized_id)
        if roles:
            return self._capabilities(
                normalized_id,
                roles,
                "canvas",
                payload.get("name") or (local or {}).get("name"),
            )
        if local is not None and _enrollment_roles(local):
            return self._capabilities(
                normalized_id,
                _enrollment_roles(local),
                "local_cache",
                local.get("name"),
            )
        return self._capabilities(normalized_id, (), "canvas", payload.get("name"))

    get_course_capabilities = course_capabilities

    def list_course_capabilities(self) -> list[CourseCapabilities]:
        courses_method = getattr(self.canvas, "courses", None)
        if not callable(courses_method):
            raise AcademicFeatureError("Canvas 客户端不支持课程列表读取。")
        courses = _records(
            self._read_call(courses_method, "Canvas 课程权限信息读取失败。")
        )
        result: list[CourseCapabilities] = []
        for course in courses:
            course_id = course.get("id")
            if course_id is None or isinstance(course_id, bool):
                continue
            normalized_id = _canvas_id(course_id, "课程 ID")
            result.append(
                self._capabilities(
                    normalized_id,
                    _enrollment_roles(course),
                    "canvas",
                    course.get("name"),
                )
            )
        return result

    def _require_staff(self, course_id: int | str) -> str:
        normalized_id = _canvas_id(course_id, "课程 ID")
        # Local raw_data is deliberately excluded from this authorization decision.
        payload = self._canvas_course_payload(normalized_id)
        if not set(_enrollment_roles(payload)) & _STAFF_ROLES:
            raise AcademicPermissionError("仅课程教师或助教可执行此操作。")
        return normalized_id

    def _course_ids(self, course_ids: Iterable[int | str] | None) -> list[str]:
        if course_ids is not None:
            result = [_canvas_id(item, "课程 ID") for item in course_ids]
        else:
            courses_method = getattr(self.canvas, "courses", None)
            if not callable(courses_method):
                raise AcademicFeatureError("Canvas 客户端不支持课程列表读取。")
            courses = _records(
                self._read_call(courses_method, "Canvas 课程列表读取失败。")
            )
            result = [
                _canvas_id(item["id"], "课程 ID")
                for item in courses
                if item.get("id") is not None and not isinstance(item.get("id"), bool)
            ]
        return list(dict.fromkeys(result))

    def _calendar_batch(
        self, context_codes: list[str], start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        public = getattr(self.canvas, "calendar_events", None)
        if callable(public):
            value = self._read_call(
                lambda: public(context_codes, _iso(start), _iso(end)),
                "Canvas 日历读取失败。",
            )
            return _records(value)
        params: list[tuple[str, str]] = [
            ("type", "assignment"),
            ("start_date", _iso(start)),
            ("end_date", _iso(end)),
            ("per_page", "100"),
        ]
        params.extend(("context_codes[]", code) for code in context_codes)
        return self._paginate("/api/v1/calendar_events", params=params)

    def events_between(
        self,
        start: datetime,
        end: datetime,
        *,
        course_ids: Iterable[int | str] | None = None,
    ) -> list[dict[str, Any]]:
        if start.tzinfo is None or end.tzinfo is None:
            raise AcademicValidationError("日历时间必须包含时区。")
        if end <= start:
            raise AcademicValidationError("日历结束时间必须晚于开始时间。")
        try:
            ids = self._course_ids(course_ids)
        except AcademicFeatureError:
            ids = []
        raw: list[dict[str, Any]] = []
        if ids:
            try:
                for offset in range(0, len(ids), 10):
                    contexts = [f"course_{item}" for item in ids[offset : offset + 10]]
                    raw.extend(self._calendar_batch(contexts, start, end))
            except AcademicFeatureError:
                raw = []

        deduplicated: dict[tuple[str, str, str], dict[str, Any]] = {}
        for event in raw:
            if event.get("eventType") != "course":
                assignment = _mapping(event.get("assignment"))
                moment_text = event.get("start_at") or event.get("end_at") or assignment.get("due_at")
                event.setdefault("courseName", event.get("title"))
                event.setdefault("startAt", moment_text)
                event.setdefault("endAt", event.get("end_at") or moment_text)
                event.setdefault("location", event.get("location_name"))
                event.setdefault("periodLabel", None)
                event.setdefault("eventType", "canvas")
                event.setdefault("source", "canvas")
                event.setdefault("canonicalCourseId", None)
            moment = _event_time(event)
            if moment is not None:
                comparable_start = start.astimezone(moment.tzinfo)
                comparable_end = end.astimezone(moment.tzinfo)
                if moment < comparable_start or moment >= comparable_end:
                    continue
            deduplicated.setdefault(_event_identity(event), event)
        return sorted(
            deduplicated.values(),
            key=lambda event: (
                _event_time(event) or datetime.max.replace(tzinfo=timezone.utc),
                str(event.get("title") or ""),
            ),
        )

    def month_events(
        self,
        year: int,
        month: int,
        *,
        course_ids: Iterable[int | str] | None = None,
        timezone_info: tzinfo = timezone.utc,
    ) -> list[dict[str, Any]]:
        try:
            start = datetime(year, month, 1, tzinfo=timezone_info)
        except (TypeError, ValueError) as exc:
            raise AcademicValidationError("年月格式无效。") from exc
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone_info)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone_info)
        return self.events_between(start, end, course_ids=course_ids)

    def upcoming_events(
        self,
        *,
        now: datetime | None = None,
        days: int = 7,
        course_ids: Iterable[int | str] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 31:
            raise AcademicValidationError("提醒天数必须在 1 到 31 之间。")
        start = now or datetime.now(timezone.utc)
        if start.tzinfo is None:
            raise AcademicValidationError("当前时间必须包含时区。")
        return self.events_between(start, start + timedelta(days=days), course_ids=course_ids)

    def calendar_overview(
        self,
        year: int,
        month: int,
        *,
        now: datetime | None = None,
        course_ids: Iterable[int | str] | None = None,
        timezone_info: tzinfo = timezone.utc,
    ) -> CalendarAggregation:
        current = now or datetime.now(timezone_info)
        if current.tzinfo is None:
            raise AcademicValidationError("当前时间必须包含时区。")
        try:
            month_start = datetime(year, month, 1, tzinfo=timezone_info)
            month_end = (
                datetime(year + 1, 1, 1, tzinfo=timezone_info)
                if month == 12
                else datetime(year, month + 1, 1, tzinfo=timezone_info)
            )
        except (TypeError, ValueError) as exc:
            raise AcademicValidationError("年月格式无效。") from exc
        month_items = self.events_between(month_start, month_end, course_ids=course_ids)
        upcoming_end = current + timedelta(days=7)
        upcoming_items = self.events_between(current, upcoming_end, course_ids=course_ids)
        return CalendarAggregation(
            _iso(month_start),
            _iso(month_end),
            tuple(month_items),
            _iso(current),
            _iso(upcoming_end),
            tuple(upcoming_items),
        )

    @staticmethod
    def _member_roles(member: Mapping[str, Any]) -> tuple[str, ...]:
        return _enrollment_roles(member)

    def _list_members_raw(self, course_id: str) -> list[dict[str, Any]]:
        public = getattr(self.canvas, "course_users", None)
        if callable(public):
            value = self._read_call(
                lambda: public(course_id), "Canvas 课程成员读取失败。"
            )
            return _records(value)
        return self._paginate(
            f"/api/v1/courses/{course_id}/users",
            params=[("per_page", "100"), ("include[]", "enrollments")],
        )

    @staticmethod
    def _normalize_role_filter(roles: str | Iterable[str] | None) -> set[str] | None:
        if roles is None:
            return None
        values = [roles] if isinstance(roles, str) else list(roles)
        normalized: set[str] = set()
        for value in values:
            canonical = _role(value)
            if canonical is None:
                raise AcademicValidationError(f"不支持的课程角色：{value}")
            normalized.add(canonical)
        return normalized

    def list_members(
        self,
        course_id: int | str,
        *,
        roles: str | Iterable[str] | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_id = _canvas_id(course_id, "课程 ID")
        role_filter = self._normalize_role_filter(roles)
        needle = (query or "").strip().casefold()
        members: list[dict[str, Any]] = []
        for member in self._list_members_raw(normalized_id):
            member_roles = self._member_roles(member)
            if role_filter is not None and not role_filter.intersection(member_roles):
                continue
            if needle:
                haystack = " ".join(
                    str(member.get(key) or "")
                    for key in ("id", "name", "sortable_name", "short_name", "login_id", "email")
                ).casefold()
                if needle not in haystack:
                    continue
            result = dict(member)
            result["academic_roles"] = list(member_roles)
            members.append(result)
        return sorted(members, key=lambda item: (str(item.get("sortable_name") or item.get("name") or "").casefold(), str(item.get("id") or "")))

    def _assignments(self, course_id: str) -> list[dict[str, Any]]:
        public = getattr(self.canvas, "assignments", None)
        if callable(public):
            return _records(
                self._read_call(
                    lambda: public(course_id), "Canvas 作业列表读取失败。"
                )
            )
        return self._paginate(
            f"/api/v1/courses/{course_id}/assignments",
            params=[("per_page", "100"), ("include[]", "score_statistics")],
        )

    def _user_submissions(
        self, course_id: str, student_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        if not student_ids:
            return []
        result: list[dict[str, Any]] = []
        public = getattr(self.canvas, "user_submissions", None)
        for offset in range(0, len(student_ids), 50):
            batch = list(student_ids[offset : offset + 50])
            if callable(public):
                value = self._read_call(
                    lambda batch=batch: public(course_id, batch),
                    "Canvas 成绩读取失败。",
                )
                result.extend(_records(value))
                continue
            params: list[tuple[str, str]] = [
                ("grouped", "true"),
                ("per_page", "100"),
            ]
            params.extend(("student_ids[]", item) for item in batch)
            result.extend(
                self._paginate(
                    f"/api/v1/courses/{course_id}/students/submissions",
                    params=params,
                )
            )
        return result

    @staticmethod
    def compute_grade_statistics(
        assignments: Sequence[Mapping[str, Any]],
        students: Sequence[Mapping[str, Any]],
        grouped_submissions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        assignment_by_id = {
            str(item.get("id")): item
            for item in assignments
            if item.get("id") is not None and not isinstance(item.get("id"), bool)
        }
        submissions_by_student: dict[str, dict[str, dict[str, Any]]] = {}
        for group in grouped_submissions:
            user_id = group.get("user_id")
            if user_id is None or isinstance(user_id, bool):
                continue
            per_assignment = submissions_by_student.setdefault(str(user_id), {})
            submissions = group.get("submissions")
            if not isinstance(submissions, (list, tuple)):
                continue
            for raw_submission in submissions:
                submission = _mapping(raw_submission)
                assignment_id = submission.get("assignment_id")
                if assignment_id is not None and not isinstance(assignment_id, bool):
                    per_assignment[str(assignment_id)] = dict(submission)

        rows: list[dict[str, Any]] = []
        totals: list[float] = []
        graded_cells = 0
        for student in students:
            user_id = student.get("id")
            if user_id is None or isinstance(user_id, bool):
                continue
            submissions = submissions_by_student.get(str(user_id), {})
            grade_cells: dict[str, str] = {}
            total_score = 0.0
            student_graded = 0
            for assignment_id in assignment_by_id:
                submission = submissions.get(assignment_id, {})
                grade_cells[assignment_id] = _display_grade(submission)
                numeric = _numeric_grade(submission)
                if numeric is not None:
                    total_score += numeric
                    student_graded += 1
                    graded_cells += 1
            totals.append(total_score)
            rows.append(
                {
                    "user_id": user_id,
                    "name": str(student.get("name") or ""),
                    "login_id": str(student.get("login_id") or ""),
                    "grades": grade_cells,
                    "total_score": total_score,
                    "graded_assignments": student_graded,
                }
            )

        assignment_statistics: list[dict[str, Any]] = []
        for assignment_id, assignment in assignment_by_id.items():
            grades = [
                numeric
                for submissions in submissions_by_student.values()
                if (numeric := _numeric_grade(submissions.get(assignment_id, {}))) is not None
            ]
            assignment_statistics.append(
                {
                    "assignment_id": assignment.get("id"),
                    "name": str(assignment.get("name") or ""),
                    "points_possible": assignment.get("points_possible"),
                    "total_students": len(rows),
                    "graded_count": len(grades),
                    "ungraded_count": max(0, len(rows) - len(grades)),
                    "minimum": min(grades) if grades else None,
                    "maximum": max(grades) if grades else None,
                    "mean": mean(grades) if grades else None,
                    "median": median(grades) if grades else None,
                }
            )
        total_possible = sum(
            number
            for assignment in assignment_by_id.values()
            if (number := AcademicFeatureService._finite_number(assignment.get("points_possible")))
            is not None
        )
        return {
            "assignment_count": len(assignment_by_id),
            "student_count": len(rows),
            "graded_count": graded_cells,
            "total_possible": total_possible,
            "assignments": assignment_statistics,
            "student_totals": {
                "values": totals,
                "minimum": min(totals) if totals else None,
                "maximum": max(totals) if totals else None,
                "mean": mean(totals) if totals else None,
                "median": median(totals) if totals else None,
            },
            "rows": rows,
        }

    @staticmethod
    def _finite_number(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def gradebook(self, course_id: int | str) -> dict[str, Any]:
        normalized_id = self._require_staff(course_id)
        assignments = self._assignments(normalized_id)
        students = [
            member
            for member in self._list_members_raw(normalized_id)
            if "student" in self._member_roles(member)
        ]
        student_ids = [str(member["id"]) for member in students if member.get("id") is not None]
        submissions = self._user_submissions(normalized_id, student_ids)
        statistics = self.compute_grade_statistics(assignments, students, submissions)
        return {
            "course_id": normalized_id,
            "assignments": assignments,
            "students": students,
            "submissions": submissions,
            "statistics": statistics,
            "rows": statistics["rows"],
        }

    grade_statistics = gradebook

    @staticmethod
    def _safe_filename(filename: str | None, default_stem: str) -> str:
        value = (filename or f"{default_stem}.csv").strip()
        if not value or value in {".", ".."} or Path(value).name != value or "/" in value or "\\" in value:
            raise AcademicValidationError("导出文件名无效；不允许传入路径。")
        if _CONTROL.search(value):
            raise AcademicValidationError("导出文件名包含无效字符。")
        if not value.casefold().endswith(".csv"):
            value = f"{value}.csv"
        if len(value) > 180:
            raise AcademicValidationError("导出文件名过长。")
        return value

    def _write_csv(
        self, filename: str, rows: Iterable[Sequence[object]]
    ) -> ExportArtifact:
        target = (self.export_root / filename).resolve()
        if target.parent != self.export_root:
            raise AcademicValidationError("导出位置不在应用临时目录内。")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}-", suffix=".tmp", dir=self.export_root
        )
        temporary = Path(temporary_name)
        row_count = 0
        try:
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                for row in rows:
                    writer.writerow([_csv_cell(value) for value in row])
                    row_count += 1
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except Exception as exc:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            if isinstance(exc, AcademicFeatureError):
                raise
            raise AcademicFeatureError("CSV 导出失败。") from exc
        return ExportArtifact(target, target.name, "text/csv; charset=utf-8", row_count)

    def export_grades_csv(
        self, course_id: int | str, *, filename: str | None = None
    ) -> ExportArtifact:
        book = self.gradebook(course_id)
        assignments = book["assignments"]
        rows: list[list[object]] = [
            ["学生 ID", "姓名", "登录名"]
            + [str(item.get("name") or item.get("id") or "") for item in assignments]
            + ["总分"]
        ]
        assignment_ids = [str(item.get("id")) for item in assignments]
        for student in book["rows"]:
            grades = _mapping(student.get("grades"))
            rows.append(
                [student.get("user_id"), student.get("name"), student.get("login_id")]
                + [grades.get(item, "") for item in assignment_ids]
                + [student.get("total_score")]
            )
        return self._write_csv(
            self._safe_filename(filename, f"course-{book['course_id']}-grades"), rows
        )

    export_grade_csv = export_grades_csv

    def export_members_csv(
        self,
        course_id: int | str,
        *,
        user_ids: Iterable[int | str] | None = None,
        roles: str | Iterable[str] | None = None,
        query: str | None = None,
        filename: str | None = None,
    ) -> ExportArtifact:
        # Export contains private enrollment data and therefore requires a fresh
        # server-side staff authorization check; renderer visibility is not authority.
        self._require_staff(course_id)
        members = self.list_members(course_id, roles=roles, query=query)
        if user_ids is not None:
            selected = {_canvas_id(item, "用户 ID") for item in user_ids}
            members = [item for item in members if str(item.get("id")) in selected]
        rows: list[list[object]] = [
            ["用户 ID", "姓名", "邮箱", "登录名", "排序名", "简称", "加入时间", "课程角色"]
        ]
        rows.extend(
            [
                item.get("id"),
                item.get("name"),
                item.get("email"),
                item.get("login_id"),
                item.get("sortable_name"),
                item.get("short_name"),
                item.get("created_at"),
                ",".join(item.get("academic_roles") or ()),
            ]
            for item in members
        )
        normalized_id = _canvas_id(course_id, "课程 ID")
        return self._write_csv(
            self._safe_filename(filename, f"course-{normalized_id}-members"), rows
        )

    def _list_submissions_raw(
        self, course_id: str, assignment_id: str
    ) -> list[dict[str, Any]]:
        public = getattr(self.canvas, "assignment_submissions", None)
        if callable(public):
            return _records(
                self._read_call(
                    lambda: public(course_id, assignment_id),
                    "Canvas 提交列表读取失败。",
                )
            )
        return self._paginate(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions",
            params=[("per_page", "100"), ("include[]", "submission_comments")],
        )

    def _submission_detail_raw(
        self, course_id: str, assignment_id: str, student_id: str
    ) -> dict[str, Any]:
        public = getattr(self.canvas, "submission_for_user", None)
        if callable(public):
            value = self._read_call(
                lambda: public(course_id, assignment_id, student_id),
                "Canvas 提交详情读取失败。",
            )
            if not isinstance(value, Mapping):
                raise AcademicFeatureError("Canvas API 返回了无法识别的提交数据。")
            return dict(value)
        return self._get_object(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{student_id}",
            params=[("include[]", "submission_comments")],
        )

    def list_submissions(
        self, course_id: int | str, assignment_id: int | str
    ) -> list[dict[str, Any]]:
        normalized_course = self._require_staff(course_id)
        normalized_assignment = _canvas_id(assignment_id, "作业 ID")
        return self._list_submissions_raw(normalized_course, normalized_assignment)

    list_assignment_submissions = list_submissions

    def submission_detail(
        self, course_id: int | str, assignment_id: int | str, student_id: int | str
    ) -> dict[str, Any]:
        normalized_course = self._require_staff(course_id)
        return self._submission_detail_raw(
            normalized_course,
            _canvas_id(assignment_id, "作业 ID"),
            _canvas_id(student_id, "学生 ID"),
        )

    get_submission_detail = submission_detail

    def _put_submission(
        self,
        course_id: str,
        assignment_id: str,
        student_id: str,
        data: list[tuple[str, str]],
    ) -> dict[str, Any]:
        public = getattr(self.canvas, "update_user_submission", None)
        if callable(public):
            value = public(course_id, assignment_id, student_id, data)
            if not isinstance(value, Mapping):
                raise AcademicFeatureError("Canvas API 返回了无法识别的写入结果。")
            return dict(value)
        request = getattr(self.canvas, "_request", None)
        json_reader = getattr(self.canvas, "_json", None)
        if not callable(request) or not callable(json_reader):
            raise AcademicFeatureError("Canvas 客户端不支持提交评分写入。")
        response = request(
            "PUT",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{student_id}",
            data=data,
            uncertain_on_error=True,
        )
        value = json_reader(response, dict)
        return dict(value)

    @staticmethod
    def _grade_matches(submission: Mapping[str, Any], expected: str) -> bool:
        observed_values = [
            submission.get("posted_grade"),
            submission.get("grade"),
            submission.get("score"),
        ]
        if expected == "":
            return all(value in (None, "") for value in observed_values)
        try:
            expected_number = Decimal(expected)
        except InvalidOperation:
            return any(
                isinstance(value, str) and value.strip().casefold() == expected.casefold()
                for value in observed_values
            )
        for value in observed_values:
            if value is None or isinstance(value, bool):
                continue
            try:
                if Decimal(str(value)) == expected_number:
                    return True
            except InvalidOperation:
                continue
        return False

    @staticmethod
    def _comments(submission: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        comments = submission.get("submission_comments")
        if not isinstance(comments, (list, tuple)):
            return []
        return [item for item in comments if isinstance(item, Mapping)]

    @classmethod
    def _comment_snapshot(cls, submission: Mapping[str, Any], text: str) -> tuple[set[str], int]:
        matching = [
            item
            for item in cls._comments(submission)
            if str(item.get("comment") or item.get("text_comment") or "").strip() == text
        ]
        identifiers = {
            str(item["id"])
            for item in matching
            if item.get("id") is not None and not isinstance(item.get("id"), bool)
        }
        return identifiers, len(matching)

    @classmethod
    def _new_comment_matches(
        cls,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        text: str,
    ) -> bool:
        old_ids, old_count = cls._comment_snapshot(before, text)
        matching = [
            item
            for item in cls._comments(after)
            if str(item.get("comment") or item.get("text_comment") or "").strip() == text
        ]
        if any(
            item.get("id") is not None
            and not isinstance(item.get("id"), bool)
            and str(item["id"]) not in old_ids
            for item in matching
        ):
            return True
        return len(matching) > old_count

    @staticmethod
    def _validated_grade(value: str | int | float | Decimal | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            raise AcademicValidationError("评分格式无效。")
        text = str(value).strip()
        if len(text) > 64 or _CONTROL.search(text):
            raise AcademicValidationError("评分格式无效。")
        if not text:
            return ""
        try:
            numeric = Decimal(text)
        except InvalidOperation:
            return text
        if not numeric.is_finite() or numeric < 0:
            raise AcademicValidationError("评分必须是非负有限数值。")
        return text

    def update_submission(
        self,
        course_id: int | str,
        assignment_id: int | str,
        student_id: int | str,
        *,
        grade: str | int | float | Decimal | None = None,
        comment: str | None = None,
        set_grade: bool = False,
    ) -> SubmissionWriteResult:
        """Write once and verify with GET; an uncertain PUT is never retried.

        ``set_grade`` distinguishes comment-only writes from explicitly clearing a grade
        with ``grade=None``.  ``grade_submission`` sets it automatically.
        """
        normalized_course = self._require_staff(course_id)
        normalized_assignment = _canvas_id(assignment_id, "作业 ID")
        normalized_student = _canvas_id(student_id, "学生 ID")
        if comment is not None:
            comment = comment.strip()
            if not comment or len(comment) > 10_000 or _CONTROL.search(comment):
                raise AcademicValidationError("评论内容无效。")
        if not set_grade and comment is None:
            raise AcademicValidationError("评分和评论不能同时为空。")
        expected_grade = self._validated_grade(grade) if set_grade else None
        before = self._submission_detail_raw(
            normalized_course, normalized_assignment, normalized_student
        )
        data: list[tuple[str, str]] = []
        if set_grade:
            data.append(("submission[posted_grade]", expected_grade or ""))
        if comment is not None:
            data.append(("comment[text_comment]", comment))

        uncertain = False
        try:
            self._put_submission(
                normalized_course, normalized_assignment, normalized_student, data
            )
        except CanvasNetworkError as exc:
            if not exc.operation_uncertain:
                raise AcademicFeatureError(redact_error_message(exc)) from exc
            uncertain = True
        except CanvasError as exc:
            raise AcademicFeatureError(redact_error_message(exc)) from exc
        except AcademicFeatureError:
            raise
        except Exception as exc:
            raise AcademicFeatureError("Canvas 提交写入失败。") from exc

        try:
            observed = self._submission_detail_raw(
                normalized_course, normalized_assignment, normalized_student
            )
        except AcademicFeatureError as exc:
            if uncertain:
                raise AcademicOutcomeUncertain(
                    "写入请求后网络中断，且无法读取服务器结果；为避免重复写入，未自动重试。"
                ) from exc
            raise AcademicVerificationError("Canvas 写入后无法读取验证结果。") from exc

        grade_verified = expected_grade is None or self._grade_matches(
            observed, expected_grade
        )
        comment_verified = comment is None or self._new_comment_matches(
            before, observed, comment
        )
        if not grade_verified or not comment_verified:
            error_type = AcademicOutcomeUncertain if uncertain else AcademicVerificationError
            raise error_type(
                "Canvas 写入结果未通过读取验证；为避免重复写入，未自动重试。"
            )
        return SubmissionWriteResult(
            observed,
            grade_verified=grade_verified,
            comment_verified=comment_verified,
            recovered_after_uncertain_write=uncertain,
        )

    def grade_submission(
        self,
        course_id: int | str,
        assignment_id: int | str,
        student_id: int | str,
        grade: str | int | float | Decimal | None,
    ) -> SubmissionWriteResult:
        return self.update_submission(
            course_id,
            assignment_id,
            student_id,
            grade=grade,
            set_grade=True,
        )

    def comment_submission(
        self,
        course_id: int | str,
        assignment_id: int | str,
        student_id: int | str,
        comment: str,
    ) -> SubmissionWriteResult:
        return self.update_submission(
            course_id, assignment_id, student_id, comment=comment
        )

    def grade_and_comment_submission(
        self,
        course_id: int | str,
        assignment_id: int | str,
        student_id: int | str,
        grade: str | int | float | Decimal | None,
        comment: str,
    ) -> SubmissionWriteResult:
        return self.update_submission(
            course_id,
            assignment_id,
            student_id,
            grade=grade,
            comment=comment,
            set_grade=True,
        )

    # Stable, descriptive aliases for desktop bridges and other backend callers.
    get_role_and_capabilities = course_capabilities
    get_month_events = month_events
    get_upcoming_events = upcoming_events
    aggregate_calendar = calendar_overview
    get_gradebook = gradebook
    export_gradebook_csv = export_grades_csv
    filter_members = list_members
    export_course_members_csv = export_members_csv
    teacher_list_submissions = list_submissions
    teacher_submission_detail = submission_detail
    score_submission = grade_submission
    add_submission_comment = comment_submission


# Backward-friendly spelling for callers that use the plural feature-set name.
AcademicFeaturesService = AcademicFeatureService
