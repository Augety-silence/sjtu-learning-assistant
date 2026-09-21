"""Transactional PostgreSQL repository operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Protocol

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sjtu_learning_assistant.models import Course, SyncRun, SyncState


class CourseLike(Protocol):
    course_id: int | str
    name: str
    course_code: str
    term_name: str


@dataclass(frozen=True)
class UpsertResult:
    fetched: int
    inserted: int
    updated: int


def upsert_courses(engine: Engine, courses: Iterable[CourseLike]) -> UpsertResult:
    """Idempotently persist courses and advance sync state in one transaction."""
    rows = [
        {
            "source_id": str(course.course_id),
            "name": course.name,
            "course_code": course.course_code,
            "term_name": course.term_name,
            "raw_data": {},
        }
        for course in courses
    ]
    now = datetime.now(timezone.utc)

    with Session(engine) as session, session.begin():
        sync_run = SyncRun(
            source="canvas",
            resource="courses",
            status="running",
            fetched_count=len(rows),
        )
        session.add(sync_run)
        session.flush()

        source_ids = [row["source_id"] for row in rows]
        existing_ids = set(
            session.scalars(
                select(Course.source_id).where(Course.source_id.in_(source_ids))
            ).all()
        ) if source_ids else set()

        if rows:
            statement = insert(Course).values(rows)
            statement = statement.on_conflict_do_update(
                index_elements=[Course.source_id],
                set_={
                    "name": statement.excluded.name,
                    "course_code": statement.excluded.course_code,
                    "term_name": statement.excluded.term_name,
                    "updated_at": now,
                },
            )
            session.execute(statement)

        inserted_count = len(set(source_ids) - existing_ids)
        updated_count = len(set(source_ids) & existing_ids)

        state_statement = insert(SyncState).values(
            source="canvas",
            resource="courses",
            last_sync_at=now,
            last_success_at=now,
            status="success",
            last_error=None,
        )
        state_statement = state_statement.on_conflict_do_update(
            constraint="uq_sync_state_source_resource",
            set_={
                "last_sync_at": now,
                "last_success_at": now,
                "status": "success",
                "last_error": None,
                "updated_at": now,
            },
        )
        session.execute(state_statement)

        sync_run.status = "success"
        sync_run.finished_at = now
        sync_run.inserted_count = inserted_count
        sync_run.updated_count = updated_count

    return UpsertResult(
        fetched=len(rows), inserted=inserted_count, updated=updated_count
    )
