"""Transactional cross-dialect repository operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Protocol, TypeVar

from sqlalchemy import Engine, case, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from sjtu_learning_assistant.models import (
    Announcement,
    Assignment,
    Course,
    CourseFile,
    CourseFolder,
    CourseModule,
    CourseModuleItem,
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
    inserted_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanvasPersistResult:
    courses: UpsertResult
    announcements: UpsertResult
    assignments: UpsertResult
    folders: UpsertResult
    files: UpsertResult
    modules: UpsertResult
    module_items: UpsertResult


ModelType = TypeVar(
    "ModelType",
    Course,
    Announcement,
    Assignment,
    Email,
    CourseFolder,
    CourseFile,
    CourseModule,
    CourseModuleItem,
)


def _dialect_insert(session: Session, model: Any):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as postgresql_insert

        return postgresql_insert(model)
    if dialect == "sqlite":
        return sqlite_insert(model)
    raise RuntimeError(f"不支持的数据库方言：{dialect}")


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


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
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
    inserted_ids = unique_ids - existing_ids
    return UpsertResult(
        fetched=len(source_ids),
        inserted=len(inserted_ids),
        updated=len(unique_ids & existing_ids),
        inserted_source_ids=tuple(
            source_id for source_id in dict.fromkeys(source_ids) if source_id in inserted_ids
        ),
    )


def _upsert_sync_state(
    session: Session,
    *,
    source: str,
    resource: str,
    now: datetime,
    cursor: str | None = None,
) -> None:
    statement = _dialect_insert(session, SyncState).values(
        source=source,
        resource=resource,
        cursor=cursor,
        last_sync_at=now,
        last_success_at=now,
        status="success",
        last_error=None,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[SyncState.source, SyncState.resource],
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
        # All records returned by fetch_active_courses share this exact marker.
        # It lets later desktop archive actions recover the latest active set
        # without adding a schema field or trusting a localised term name.
        timestamped_rows = [
            {**row, "created_at": now, "updated_at": now} for row in rows
        ]
        statement = _dialect_insert(session, Course).values(timestamped_rows)
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
    seen_by_course: dict[int, set[str]] = {}
    for course_source_id, announcements in announcements_by_course.items():
        database_course_id = course_ids.get(str(course_source_id))
        if database_course_id is None:
            continue
        seen = seen_by_course.setdefault(database_course_id, set())
        for announcement in announcements:
            source_id = announcement.get("id")
            if source_id is None:
                continue
            source_id_text = str(source_id)
            seen.add(source_id_text)
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
                    "is_active": True,
                    "last_seen_at": now,
                    "deactivated_at": None,
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
                    "is_active": True,
                    "raw_data": announcement,
                }
            )

    source_ids = [row["source_id"] for row in rows]
    existing_ids = _existing_ids(session, Announcement, source_ids)
    if rows:
        statement = _dialect_insert(session, Announcement).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[Announcement.source_id],
            set_={
                "course_id": statement.excluded.course_id,
                "title": statement.excluded.title,
                "body": statement.excluded.body,
                "posted_at": statement.excluded.posted_at,
                "source_updated_at": statement.excluded.source_updated_at,
                "url": statement.excluded.url,
                "is_active": True,
                "last_seen_at": now,
                "deactivated_at": None,
                "raw_data": statement.excluded.raw_data,
                "updated_at": now,
            },
        )
        session.execute(statement)
        record_ids = _record_id_map(session, Announcement, source_ids)
        for item_row in item_rows:
            item_row["announcement_id"] = record_ids[item_row["source_id"]]
        _upsert_items(session, item_rows, now)

    for database_course_id, seen_ids in seen_by_course.items():
        _deactivate_missing(
            session,
            Announcement,
            course_id=database_course_id,
            seen_source_ids=seen_ids,
            now=now,
        )
        item_conditions = [
            UnifiedItem.source == "canvas",
            UnifiedItem.item_type == "announcement",
            UnifiedItem.course_id == database_course_id,
            UnifiedItem.is_active.is_(True),
        ]
        if seen_ids:
            item_conditions.append(UnifiedItem.source_id.not_in(seen_ids))
        session.execute(
            update(UnifiedItem)
            .where(*item_conditions)
            .values(is_active=False, updated_at=now)
        )
    return _result(source_ids, existing_ids)


def _upsert_assignments(
    session: Session,
    assignments_by_course: dict[str, list[dict[str, Any]]],
    course_ids: dict[str, int],
    now: datetime,
) -> UpsertResult:
    rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    seen_by_course: dict[int, set[str]] = {}
    for course_source_id, assignments in assignments_by_course.items():
        database_course_id = course_ids.get(str(course_source_id))
        if database_course_id is None:
            continue
        seen = seen_by_course.setdefault(database_course_id, set())
        for assignment in assignments:
            source_id = assignment.get("id")
            if source_id is None:
                continue
            source_id_text = str(source_id)
            seen.add(source_id_text)
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
                    "is_active": True,
                    "last_seen_at": now,
                    "deactivated_at": None,
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
                    "is_active": True,
                    "raw_data": assignment,
                }
            )

    source_ids = [row["source_id"] for row in rows]
    existing_ids = _existing_ids(session, Assignment, source_ids)
    if rows:
        statement = _dialect_insert(session, Assignment).values(rows)
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
                "is_active": True,
                "last_seen_at": now,
                "deactivated_at": None,
                "raw_data": statement.excluded.raw_data,
                "updated_at": now,
            },
        )
        session.execute(statement)
        record_ids = _record_id_map(session, Assignment, source_ids)
        for item_row in item_rows:
            item_row["assignment_id"] = record_ids[item_row["source_id"]]
        _upsert_items(session, item_rows, now)

    for database_course_id, seen_ids in seen_by_course.items():
        _deactivate_missing(
            session,
            Assignment,
            course_id=database_course_id,
            seen_source_ids=seen_ids,
            now=now,
        )
        item_conditions = [
            UnifiedItem.source == "canvas",
            UnifiedItem.item_type == "assignment",
            UnifiedItem.course_id == database_course_id,
            UnifiedItem.is_active.is_(True),
        ]
        if seen_ids:
            item_conditions.append(UnifiedItem.source_id.not_in(seen_ids))
        session.execute(
            update(UnifiedItem)
            .where(*item_conditions)
            .values(is_active=False, updated_at=now)
        )
    return _result(source_ids, existing_ids)


def _deactivate_missing(
    session: Session,
    model: Any,
    *,
    course_id: int,
    seen_source_ids: set[str],
    now: datetime,
) -> None:
    conditions = [model.course_id == course_id, model.is_active.is_(True)]
    if seen_source_ids:
        conditions.append(model.source_id.not_in(seen_source_ids))
    session.execute(
        update(model)
        .where(*conditions)
        .values(is_active=False, deactivated_at=now, updated_at=now)
    )


def _upsert_folders(
    session: Session,
    folders_by_course: dict[str, list[dict[str, Any]]],
    course_ids: dict[str, int],
    now: datetime,
) -> UpsertResult:
    rows: list[dict[str, Any]] = []
    parent_sources: dict[str, str | None] = {}
    seen_by_course: dict[int, set[str]] = {}
    for course_source_id, folders in folders_by_course.items():
        database_course_id = course_ids.get(str(course_source_id))
        if database_course_id is None:
            continue
        seen = seen_by_course.setdefault(database_course_id, set())
        for folder in folders:
            source_id = folder.get("id")
            if source_id is None:
                continue
            source_id_text = str(source_id)
            seen.add(source_id_text)
            parent = folder.get("parent_folder_id")
            parent_sources[source_id_text] = str(parent) if parent is not None else None
            rows.append(
                {
                    "source_id": source_id_text,
                    "course_id": database_course_id,
                    "parent_folder_id": None,
                    "name": str(folder.get("name") or "（未命名文件夹）"),
                    "full_name": folder.get("full_name"),
                    "position": int_or_none(folder.get("position")),
                    "files_count": int_or_none(folder.get("files_count")),
                    "folders_count": int_or_none(folder.get("folders_count")),
                    "source_updated_at": parse_iso_datetime(folder.get("updated_at")),
                    "is_active": True,
                    "last_seen_at": now,
                    "deactivated_at": None,
                    "raw_data": folder,
                }
            )

    source_ids = [row["source_id"] for row in rows]
    existing_ids = _existing_ids(session, CourseFolder, source_ids)
    if rows:
        statement = _dialect_insert(session, CourseFolder).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[CourseFolder.source_id],
            set_={
                "course_id": statement.excluded.course_id,
                "parent_folder_id": None,
                "name": statement.excluded.name,
                "full_name": statement.excluded.full_name,
                "position": statement.excluded.position,
                "files_count": statement.excluded.files_count,
                "folders_count": statement.excluded.folders_count,
                "source_updated_at": statement.excluded.source_updated_at,
                "is_active": True,
                "last_seen_at": now,
                "deactivated_at": None,
                "raw_data": statement.excluded.raw_data,
                "updated_at": now,
            },
        )
        session.execute(statement)
        folder_ids = _record_id_map(session, CourseFolder, source_ids)
        for source_id, parent_source_id in parent_sources.items():
            if parent_source_id is None:
                continue
            parent_database_id = folder_ids.get(parent_source_id)
            if parent_database_id is not None:
                session.execute(
                    update(CourseFolder)
                    .where(CourseFolder.id == folder_ids[source_id])
                    .values(parent_folder_id=parent_database_id, updated_at=now)
                )

    for database_course_id, seen_ids in seen_by_course.items():
        _deactivate_missing(
            session,
            CourseFolder,
            course_id=database_course_id,
            seen_source_ids=seen_ids,
            now=now,
        )
    return _result(source_ids, existing_ids)


def _upsert_files(
    session: Session,
    files_by_course: dict[str, list[dict[str, Any]]],
    course_ids: dict[str, int],
    now: datetime,
) -> UpsertResult:
    folder_source_ids = {
        str(file_data["folder_id"])
        for files in files_by_course.values()
        for file_data in files
        if file_data.get("folder_id") is not None
    }
    folder_ids = _record_id_map(session, CourseFolder, list(folder_source_ids))
    rows: list[dict[str, Any]] = []
    item_rows: list[dict[str, Any]] = []
    seen_by_course: dict[int, set[str]] = {}
    for course_source_id, files in files_by_course.items():
        database_course_id = course_ids.get(str(course_source_id))
        if database_course_id is None:
            continue
        seen = seen_by_course.setdefault(database_course_id, set())
        for file_data in files:
            source_id = file_data.get("id")
            if source_id is None:
                continue
            source_id_text = str(source_id)
            seen.add(source_id_text)
            folder_source_id = file_data.get("folder_id")
            updated_at = parse_iso_datetime(
                file_data.get("updated_at") or file_data.get("modified_at")
            )
            display_name = str(
                file_data.get("display_name")
                or file_data.get("filename")
                or "（未命名文件）"
            )
            url = file_data.get("url") or file_data.get("html_url")
            rows.append(
                {
                    "source_id": source_id_text,
                    "course_id": database_course_id,
                    "folder_id": folder_ids.get(str(folder_source_id))
                    if folder_source_id is not None
                    else None,
                    "display_name": display_name,
                    "filename": file_data.get("filename"),
                    "content_type": file_data.get("content-type")
                    or file_data.get("content_type"),
                    "size": int_or_none(file_data.get("size")),
                    "source_updated_at": updated_at,
                    "url": url,
                    "hidden": bool(file_data.get("hidden", False)),
                    "locked": bool(file_data.get("locked", False)),
                    "is_active": True,
                    "last_seen_at": now,
                    "deactivated_at": None,
                    "raw_data": file_data,
                }
            )
            item_rows.append(
                {
                    "source": "canvas",
                    "item_type": "file",
                    "source_id": source_id_text,
                    "course_id": database_course_id,
                    "title": display_name,
                    "occurred_at": updated_at,
                    "url": url,
                    "priority": 0,
                    "is_active": True,
                    "raw_data": file_data,
                }
            )

    source_ids = [row["source_id"] for row in rows]
    existing_ids = _existing_ids(session, CourseFile, source_ids)
    if rows:
        statement = _dialect_insert(session, CourseFile).values(rows)
        metadata_changed = or_(
            CourseFile.source_updated_at.is_distinct_from(
                statement.excluded.source_updated_at
            ),
            CourseFile.size.is_distinct_from(statement.excluded.size),
            CourseFile.display_name.is_distinct_from(statement.excluded.display_name),
            CourseFile.folder_id.is_distinct_from(statement.excluded.folder_id),
        )
        statement = statement.on_conflict_do_update(
            index_elements=[CourseFile.source_id],
            set_={
                "course_id": statement.excluded.course_id,
                "folder_id": statement.excluded.folder_id,
                "display_name": statement.excluded.display_name,
                "filename": statement.excluded.filename,
                "content_type": statement.excluded.content_type,
                "size": statement.excluded.size,
                "source_updated_at": statement.excluded.source_updated_at,
                "url": statement.excluded.url,
                "download_status": case(
                    (metadata_changed, "pending"),
                    else_=CourseFile.download_status,
                ),
                "download_error": case(
                    (metadata_changed, None),
                    else_=CourseFile.download_error,
                ),
                "hidden": statement.excluded.hidden,
                "locked": statement.excluded.locked,
                "is_active": True,
                "last_seen_at": now,
                "deactivated_at": None,
                "raw_data": statement.excluded.raw_data,
                "updated_at": now,
            },
        )
        session.execute(statement)
        record_ids = _record_id_map(session, CourseFile, source_ids)
        for item_row in item_rows:
            item_row["course_file_id"] = record_ids[item_row["source_id"]]
        _upsert_items(session, item_rows, now)

    for database_course_id, seen_ids in seen_by_course.items():
        _deactivate_missing(
            session,
            CourseFile,
            course_id=database_course_id,
            seen_source_ids=seen_ids,
            now=now,
        )
        item_conditions = [
            UnifiedItem.source == "canvas",
            UnifiedItem.item_type == "file",
            UnifiedItem.course_id == database_course_id,
            UnifiedItem.is_active.is_(True),
        ]
        if seen_ids:
            item_conditions.append(UnifiedItem.source_id.not_in(seen_ids))
        session.execute(
            update(UnifiedItem)
            .where(*item_conditions)
            .values(is_active=False, updated_at=now)
        )
    return _result(source_ids, existing_ids)


def _upsert_modules(
    session: Session,
    modules_by_course: dict[str, list[dict[str, Any]]],
    course_ids: dict[str, int],
    now: datetime,
) -> UpsertResult:
    rows: list[dict[str, Any]] = []
    seen_by_course: dict[int, set[str]] = {}
    for course_source_id, modules in modules_by_course.items():
        database_course_id = course_ids.get(str(course_source_id))
        if database_course_id is None:
            continue
        seen = seen_by_course.setdefault(database_course_id, set())
        for module in modules:
            source_id = module.get("id")
            if source_id is None:
                continue
            source_id_text = str(source_id)
            seen.add(source_id_text)
            rows.append(
                {
                    "source_id": source_id_text,
                    "course_id": database_course_id,
                    "name": str(module.get("name") or "（未命名模块）"),
                    "position": int_or_none(module.get("position")),
                    "workflow_state": module.get("state"),
                    "unlock_at": parse_iso_datetime(module.get("unlock_at")),
                    "items_count": int_or_none(module.get("items_count")),
                    "is_active": True,
                    "last_seen_at": now,
                    "deactivated_at": None,
                    "raw_data": module,
                }
            )

    source_ids = [row["source_id"] for row in rows]
    existing_ids = _existing_ids(session, CourseModule, source_ids)
    if rows:
        statement = _dialect_insert(session, CourseModule).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[CourseModule.source_id],
            set_={
                "course_id": statement.excluded.course_id,
                "name": statement.excluded.name,
                "position": statement.excluded.position,
                "workflow_state": statement.excluded.workflow_state,
                "unlock_at": statement.excluded.unlock_at,
                "items_count": statement.excluded.items_count,
                "is_active": True,
                "last_seen_at": now,
                "deactivated_at": None,
                "raw_data": statement.excluded.raw_data,
                "updated_at": now,
            },
        )
        session.execute(statement)

    for database_course_id, seen_ids in seen_by_course.items():
        _deactivate_missing(
            session,
            CourseModule,
            course_id=database_course_id,
            seen_source_ids=seen_ids,
            now=now,
        )
    return _result(source_ids, existing_ids)


def _upsert_module_items(
    session: Session,
    module_items_by_course: dict[str, list[dict[str, Any]]],
    course_ids: dict[str, int],
    now: datetime,
) -> UpsertResult:
    all_items = [item for items in module_items_by_course.values() for item in items]
    module_source_ids = {
        str(item["module_id"])
        for item in all_items
        if item.get("module_id") is not None
    }
    file_source_ids = {
        str(item["content_id"])
        for item in all_items
        if item.get("type") == "File" and item.get("content_id") is not None
    }
    module_ids = _record_id_map(session, CourseModule, list(module_source_ids))
    file_ids = _record_id_map(session, CourseFile, list(file_source_ids))
    rows: list[dict[str, Any]] = []
    seen_by_course: dict[int, set[str]] = {}
    for course_source_id, items in module_items_by_course.items():
        database_course_id = course_ids.get(str(course_source_id))
        if database_course_id is None:
            continue
        seen = seen_by_course.setdefault(database_course_id, set())
        for item in items:
            source_id = item.get("id")
            module_source_id = item.get("module_id")
            if source_id is None or module_source_id is None:
                continue
            database_module_id = module_ids.get(str(module_source_id))
            if database_module_id is None:
                continue
            source_id_text = str(source_id)
            content_id = item.get("content_id")
            item_type = str(item.get("type") or "Unknown")
            seen.add(source_id_text)
            rows.append(
                {
                    "source_id": source_id_text,
                    "course_id": database_course_id,
                    "module_id": database_module_id,
                    "content_file_id": file_ids.get(str(content_id))
                    if item_type == "File" and content_id is not None
                    else None,
                    "content_source_id": str(content_id)
                    if content_id is not None
                    else None,
                    "title": str(item.get("title") or "（未命名模块项）"),
                    "item_type": item_type,
                    "position": int_or_none(item.get("position")),
                    "indent": int_or_none(item.get("indent")),
                    "html_url": item.get("html_url"),
                    "api_url": item.get("url"),
                    "external_url": item.get("external_url"),
                    "is_active": True,
                    "last_seen_at": now,
                    "deactivated_at": None,
                    "raw_data": item,
                }
            )

    source_ids = [row["source_id"] for row in rows]
    existing_ids = _existing_ids(session, CourseModuleItem, source_ids)
    if rows:
        statement = _dialect_insert(session, CourseModuleItem).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[CourseModuleItem.source_id],
            set_={
                "course_id": statement.excluded.course_id,
                "module_id": statement.excluded.module_id,
                "content_file_id": statement.excluded.content_file_id,
                "content_source_id": statement.excluded.content_source_id,
                "title": statement.excluded.title,
                "item_type": statement.excluded.item_type,
                "position": statement.excluded.position,
                "indent": statement.excluded.indent,
                "html_url": statement.excluded.html_url,
                "api_url": statement.excluded.api_url,
                "external_url": statement.excluded.external_url,
                "is_active": True,
                "last_seen_at": now,
                "deactivated_at": None,
                "raw_data": statement.excluded.raw_data,
                "updated_at": now,
            },
        )
        session.execute(statement)

    for database_course_id, seen_ids in seen_by_course.items():
        _deactivate_missing(
            session,
            CourseModuleItem,
            course_id=database_course_id,
            seen_source_ids=seen_ids,
            now=now,
        )
    return _result(source_ids, existing_ids)


def _upsert_items(
    session: Session, rows: list[dict[str, Any]], now: datetime
) -> None:
    if not rows:
        return
    statement = _dialect_insert(session, UnifiedItem).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[UnifiedItem.source, UnifiedItem.item_type, UnifiedItem.source_id],
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
            "is_active": statement.excluded.is_active,
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
    folders_by_course: dict[str, list[dict[str, Any]]] | None = None,
    files_by_course: dict[str, list[dict[str, Any]]] | None = None,
    modules_by_course: dict[str, list[dict[str, Any]]] | None = None,
    module_items_by_course: dict[str, list[dict[str, Any]]] | None = None,
    announcement_cursors: dict[str, str | None] | None = None,
    assignment_cursors: dict[str, str | None] | None = None,
    file_cursors: dict[str, str | None] | None = None,
) -> CanvasPersistResult:
    """Persist Canvas resources, ETag cursors, and unified items atomically."""
    now = datetime.now(timezone.utc)
    folders_by_course = folders_by_course or {}
    files_by_course = files_by_course or {}
    modules_by_course = modules_by_course or {}
    module_items_by_course = module_items_by_course or {}
    announcement_cursors = announcement_cursors or {}
    assignment_cursors = assignment_cursors or {}
    file_cursors = file_cursors or {}
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
        folders_result = _upsert_folders(
            session, folders_by_course, course_ids, now
        )
        files_result = _upsert_files(session, files_by_course, course_ids, now)
        modules_result = _upsert_modules(
            session, modules_by_course, course_ids, now
        )
        module_items_result = _upsert_module_items(
            session, module_items_by_course, course_ids, now
        )

        for resource, result in (
            ("courses", courses_result),
            ("announcements", announcements_result),
            ("assignments", assignments_result),
            ("folders", folders_result),
            ("files", files_result),
            ("modules", modules_result),
            ("module_items", module_items_result),
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
        for course_source_id, cursor in file_cursors.items():
            _upsert_sync_state(
                session,
                source="canvas",
                resource=f"course:{course_source_id}:files",
                cursor=cursor,
                now=now,
            )

    return CanvasPersistResult(
        courses=courses_result,
        announcements=announcements_result,
        assignments=assignments_result,
        folders=folders_result,
        files=files_result,
        modules=modules_result,
        module_items=module_items_result,
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
            statement = _dialect_insert(session, Email).values(rows)
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
