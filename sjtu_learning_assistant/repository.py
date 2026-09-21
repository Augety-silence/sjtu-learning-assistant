"""Transactional PostgreSQL repository operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Protocol, TypeVar

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sjtu_learning_assistant.models import (
    Announcement,
    Assignment,
    Course,
    Email,
    SyncRun,
    SyncState,
    UnifiedItem,
)


class CourseLike(Protocol):
    course_id: int | str
    name: str
    course_code: str
    term_name: str


class EmailLike(Protocol):
    source_id: str
    subject: str
    sender_name: str | None
    sender_address: str | None
    sent_at: datetime | None
    received_at: datetime | None
    body_preview: str | None
    is_unread: bool
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class UpsertResult:
    fetched: int
    inserted: int
    updated: int


@dataclass(frozen=True)
class CanvasPersistResult:
    courses: UpsertResult
    announcements: UpsertResult
    assignments: UpsertResult


ModelType = TypeVar("ModelType", Course, Announcement, Assignment, Email)


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def get_sync_state(engine: Engine, source: str, resource: str) -> SyncState | None:
    with Session(engine) as session:
        return session.scalar(
            select(SyncState).where(
                SyncState.source == source,
                SyncState.resource == resource,
            )
        )


def _existing_ids(
    session: Session, model: type[ModelType], source_ids: list[str]
) -> set[str]:
    if not source_ids:
        return set()
    return set(
        session.scalars(
            select(model.source_id).where(model.source_id.in_(source_ids))
        ).all()
    )


def _result(source_ids: list[str], existing_ids: set[str]) -> UpsertResult:
    unique_ids = set(source_ids)
    return UpsertResult(
        fetched=len(source_ids),
        inserted=len(unique_ids - existing_ids),
        updated=len(unique_ids & existing_ids),
    )


def _upsert_sync_state(
    session: Session,
    *,
    source: str,
    resource: str,
    now: datetime,
    cursor: str | None = None,
) -> None:
    statement = insert(SyncState).values(
        source=source,
        resource=resource,
        cursor=cursor,
        last_sync_at=now,
        last_success_at=now,
        status="success",
        last_error=None,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_sync_state_source_resource",
        set_={
            "cursor": cursor,
            "last_sync_at": now,
            "last_success_at": now,
            "status": "success",
            "last_error": None,
            "updated_at": now,
        },
    )
    session.execute(statement)


def _add_sync_run(
    session: Session,
    *,
    source: str,
    resource: str,
    result: UpsertResult,
    now: datetime,
) -> None:
    session.add(
        SyncRun(
            source=source,
            resource=resource,
            started_at=now,
            finished_at=now,
            status="success",
            fetched_count=result.fetched,
            inserted_count=result.inserted,
            updated_count=result.updated,
        )
    )


def _course_rows(raw_courses: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for course in raw_courses:
        source_id = course.get("id")
        if source_id is None:
            continue
        term = course.get("term")
        term_name = term.get("name") if isinstance(term, dict) else None
        rows.append(
            {
                "source_id": str(source_id),
                "name": str(course.get("name") or "（未命名课程）"),
                "course_code": course.get("course_code"),
                "term_name": term_name,
                "source_updated_at": parse_iso_datetime(course.get("updated_at")),
                "raw_data": course,
            }
        )
    return rows


def _upsert_courses(session: Session, rows: list[dict[str, Any]], now: datetime) -> UpsertResult:
    source_ids = [row["source_id"] for row in rows]
    existing_ids = _existing_ids(session, Course, source_ids)
    if rows:
        statement = insert(Course).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[Course.source_id],
            set_={
                "name": statement.excluded.name,
                "course_code": statement.excluded.course_code,
                "term_name": statement.excluded.term_name,
                "source_updated_at": statement.excluded.source_updated_at,
                "raw_data": statement.excluded.raw_data,
                "updated_at": now,
            },
        )
        session.execute(statement)
    return _result(source_ids, existing_ids)


def _record_id_map(
    session: Session, model: type[ModelType], source_ids: list[str]
) -> dict[str, int]:
    if not source_ids:
        return {}
    rows = session.execute(
        select(model.source_id, model.id).where(model.source_id.in_(source_ids))
    ).all()
    return {str(source_id): int(database_id) for source_id, database_id in rows}


def _course_id_map(session: Session, source_ids: list[str]) -> dict[str, int]:
    return _record_id_map(session, Course, source_ids)


def _upsert_announcements(
    session: Session,
    announcements_by_course: dict[str, list[dict[str, Any]]],
    course_ids: dict[str, int],
    now: datetime,
) -> UpsertResult:
    rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    for course_source_id, announcements in announcements_by_course.items():
        database_course_id = course_ids.get(str(course_source_id))
        if database_course_id is None:
            continue
        for announcement in announcements:
            source_id = announcement.get("id")
            if source_id is None:
                continue
            source_id_text = str(source_id)
            posted_at = parse_iso_datetime(announcement.get("posted_at"))
            title = str(announcement.get("title") or "（无标题公告）")
            url = announcement.get("html_url")
            rows.append(
                {
                    "source_id": source_id_text,
                    "course_id": database_course_id,
                    "title": title,
                    "body": announcement.get("message"),
                    "posted_at": posted_at,
                    "source_updated_at": parse_iso_datetime(
                        announcement.get("updated_at")
                    ),
                    "url": url,
                    "raw_data": announcement,
                }
            )
            item_rows.append(
                {
                    "source": "canvas",
                    "item_type": "announcement",
                    "source_id": source_id_text,
                    "course_id": database_course_id,
                    "title": title,
                    "occurred_at": posted_at,
                    "url": url,
                    "priority": 0,
                    "raw_data": announcement,
                }
            )

    source_ids = [row["source_id"] for row in rows]
    existing_ids = _existing_ids(session, Announcement, source_ids)
    if rows:
        statement = insert(Announcement).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[Announcement.source_id],
            set_={
                "course_id": statement.excluded.course_id,
                "title": statement.excluded.title,
                "body": statement.excluded.body,
                "posted_at": statement.excluded.posted_at,
                "source_updated_at": statement.excluded.source_updated_at,
                "url": statement.excluded.url,
                "raw_data": statement.excluded.raw_data,
                "updated_at": now,
            },
        )
        session.execute(statement)
        record_ids = _record_id_map(session, Announcement, source_ids)
        for item_row in item_rows:
            item_row["announcement_id"] = record_ids[item_row["source_id"]]
        _upsert_items(session, item_rows, now)
    return _result(source_ids, existing_ids)


def _upsert_assignments(
    session: Session,
    assignments_by_course: dict[str, list[dict[str, Any]]],
    course_ids: dict[str, int],
    now: datetime,
) -> UpsertResult:
    rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    for course_source_id, assignments in assignments_by_course.items():
        database_course_id = course_ids.get(str(course_source_id))
        if database_course_id is None:
            continue
        for assignment in assignments:
            source_id = assignment.get("id")
            if source_id is None:
                continue
            source_id_text = str(source_id)
            submission = assignment.get("submission")
            submission_state = (
                submission.get("workflow_state")
                if isinstance(submission, dict)
                else None
            )
            due_at = parse_iso_datetime(assignment.get("due_at"))
            occurred_at = parse_iso_datetime(
                assignment.get("updated_at") or assignment.get("created_at")
            )
            name = str(assignment.get("name") or "（无标题作业）")
            url = assignment.get("html_url")
            rows.append(
                {
                    "source_id": source_id_text,
                    "course_id": database_course_id,
                    "name": name,
                    "due_at": due_at,
                    "points_possible": decimal_or_none(
                        assignment.get("points_possible")
                    ),
                    "submission_state": submission_state,
                    "source_updated_at": parse_iso_datetime(
                        assignment.get("updated_at")
                    ),
                    "url": url,
                    "raw_data": assignment,
                }
            )
            item_rows.append(
                {
                    "source": "canvas",
                    "item_type": "assignment",
                    "source_id": source_id_text,
                    "course_id": database_course_id,
                    "title": name,
                    "occurred_at": occurred_at,
                    "due_at": due_at,
                    "url": url,
                    "priority": 1 if due_at else 0,
                    "raw_data": assignment,
                }
            )

    source_ids = [row["source_id"] for row in rows]
    existing_ids = _existing_ids(session, Assignment, source_ids)
    if rows:
        statement = insert(Assignment).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[Assignment.source_id],
            set_={
                "course_id": statement.excluded.course_id,
                "name": statement.excluded.name,
                "due_at": statement.excluded.due_at,
                "points_possible": statement.excluded.points_possible,
                "submission_state": statement.excluded.submission_state,
                "source_updated_at": statement.excluded.source_updated_at,
                "url": statement.excluded.url,
                "raw_data": statement.excluded.raw_data,
                "updated_at": now,
            },
        )
        session.execute(statement)
        record_ids = _record_id_map(session, Assignment, source_ids)
        for item_row in item_rows:
            item_row["assignment_id"] = record_ids[item_row["source_id"]]
        _upsert_items(session, item_rows, now)
    return _result(source_ids, existing_ids)


def _upsert_items(
    session: Session, rows: list[dict[str, Any]], now: datetime
) -> None:
    if not rows:
        return
    statement = insert(UnifiedItem).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="uq_items_source",
        set_={
            "course_id": statement.excluded.course_id,
            "announcement_id": statement.excluded.announcement_id,
            "assignment_id": statement.excluded.assignment_id,
            "email_id": statement.excluded.email_id,
            "course_file_id": statement.excluded.course_file_id,
            "title": statement.excluded.title,
            "sender": statement.excluded.sender,
            "occurred_at": statement.excluded.occurred_at,
            "due_at": statement.excluded.due_at,
            "url": statement.excluded.url,
            "priority": statement.excluded.priority,
            "is_read": statement.excluded.is_read,
            "raw_data": statement.excluded.raw_data,
            "updated_at": now,
        },
    )
    session.execute(statement)


def persist_canvas_data(
    engine: Engine,
    *,
    raw_courses: list[dict[str, Any]],
    announcements_by_course: dict[str, list[dict[str, Any]]],
    assignments_by_course: dict[str, list[dict[str, Any]]],
    announcement_cursors: dict[str, str | None] | None = None,
    assignment_cursors: dict[str, str | None] | None = None,
) -> CanvasPersistResult:
    """Persist Canvas resources, ETag cursors, and unified items atomically."""
    now = datetime.now(timezone.utc)
    announcement_cursors = announcement_cursors or {}
    assignment_cursors = assignment_cursors or {}
    course_rows = _course_rows(raw_courses)
    course_source_ids = [row["source_id"] for row in course_rows]

    with Session(engine) as session, session.begin():
        courses_result = _upsert_courses(session, course_rows, now)
        course_ids = _course_id_map(session, course_source_ids)
        announcements_result = _upsert_announcements(
            session, announcements_by_course, course_ids, now
        )
        assignments_result = _upsert_assignments(
            session, assignments_by_course, course_ids, now
        )

        for resource, result in (
            ("courses", courses_result),
            ("announcements", announcements_result),
            ("assignments", assignments_result),
        ):
            _upsert_sync_state(
                session, source="canvas", resource=resource, now=now
            )
            _add_sync_run(
                session,
                source="canvas",
                resource=resource,
                result=result,
                now=now,
            )

        for course_source_id, cursor in announcement_cursors.items():
            _upsert_sync_state(
                session,
                source="canvas",
                resource=f"course:{course_source_id}:announcements",
                cursor=cursor,
                now=now,
            )
        for course_source_id, cursor in assignment_cursors.items():
            _upsert_sync_state(
                session,
                source="canvas",
                resource=f"course:{course_source_id}:assignments",
                cursor=cursor,
                now=now,
            )

    return CanvasPersistResult(
        courses=courses_result,
        announcements=announcements_result,
        assignments=assignments_result,
    )


def persist_emails(
    engine: Engine,
    emails: Iterable[EmailLike],
    *,
    cursor: str,
) -> UpsertResult:
    """Persist email records, unified items, and UID cursor atomically."""
    records = list(emails)
    now = datetime.now(timezone.utc)
    rows = [
        {
            "source_id": item.source_id,
            "subject": item.subject,
            "sender_name": item.sender_name,
            "sender_address": item.sender_address,
            "sent_at": item.sent_at,
            "received_at": item.received_at,
            "body_preview": item.body_preview,
            "is_unread": item.is_unread,
            "priority": 0,
            "raw_data": item.raw_data,
        }
        for item in records
    ]
    item_rows = [
        {
            "source": "email",
            "item_type": "email",
            "source_id": item.source_id,
            "title": item.subject,
            "sender": item.sender_address or item.sender_name,
            "occurred_at": item.sent_at or item.received_at,
            "priority": 0,
            "is_read": not item.is_unread,
            "raw_data": item.raw_data,
        }
        for item in records
    ]

    with Session(engine) as session, session.begin():
        source_ids = [row["source_id"] for row in rows]
        existing_ids = _existing_ids(session, Email, source_ids)
        if rows:
            statement = insert(Email).values(rows)
            statement = statement.on_conflict_do_update(
                index_elements=[Email.source_id],
                set_={
                    "subject": statement.excluded.subject,
                    "sender_name": statement.excluded.sender_name,
                    "sender_address": statement.excluded.sender_address,
                    "sent_at": statement.excluded.sent_at,
                    "received_at": statement.excluded.received_at,
                    "body_preview": statement.excluded.body_preview,
                    "is_unread": statement.excluded.is_unread,
                    "raw_data": statement.excluded.raw_data,
                    "updated_at": now,
                },
            )
            session.execute(statement)
            record_ids = _record_id_map(session, Email, source_ids)
            for item_row in item_rows:
                item_row["email_id"] = record_ids[item_row["source_id"]]
            _upsert_items(session, item_rows, now)

        result = _result(source_ids, existing_ids)
        _upsert_sync_state(
            session,
            source="email",
            resource="inbox",
            cursor=cursor,
            now=now,
        )
        _add_sync_run(
            session,
            source="email",
            resource="inbox",
            result=result,
            now=now,
        )

    return result


def upsert_courses(engine: Engine, courses: Iterable[CourseLike]) -> UpsertResult:
    """Phase 2A-compatible course upsert entry point."""
    rows = [
        {
            "id": course.course_id,
            "name": course.name,
            "course_code": course.course_code,
            "term": {"name": course.term_name},
        }
        for course in courses
    ]
    result = persist_canvas_data(
        engine,
        raw_courses=rows,
        announcements_by_course={},
        assignments_by_course={},
    )
    return result.courses
