"""课程身份与范围校验；学期名称不作为课程主键。"""
from __future__ import annotations
import hashlib
import json
import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from sjtu_learning_assistant.models import Course, SyncState


def course_identifier(value: object) -> str:
    if type(value) not in {str, int} or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(value)):
        raise ValueError("课程标识无效。")
    return str(value)


def term_identity(raw: dict, name: str | None = None) -> tuple[str, str]:
    term = raw.get("term") if isinstance(raw.get("term"), dict) else {}
    remote = term.get("id") or raw.get("enrollment_term_id")
    label = str(term.get("name") or name or "未识别学期")[:200]
    if type(remote) in {str, int} and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(remote)):
        return "term:" + str(remote), label
    return ("legacy:" + hashlib.sha256(label.encode()).hexdigest()[:24], label) if name or term.get("name") else ("unknown", label)


def validate_scope(term_id: object = "", course_ids: object = None) -> tuple[str, tuple[str, ...]]:
    if type(term_id) is not str or not re.fullmatch(r"(?:term:[A-Za-z0-9_-]{1,128}|legacy:[a-f0-9]{24}|unknown)?", term_id):
        raise ValueError("学期标识无效。")
    if course_ids is None:
        ids = ()
    elif type(course_ids) not in {list, tuple} or not 1 <= len(course_ids) <= 1000:
        raise ValueError("课程范围必须包含 1 到 1000 门课程。")
    else:
        ids = tuple(dict.fromkeys(course_identifier(item) for item in course_ids))
    return term_id, ids


def select_scope(rows: list[dict], term_id: object = "", course_ids: object = None) -> list[dict]:
    term, ids = validate_scope(term_id, course_ids)
    if not term and not ids:
        raise ValueError("请先选择学期或课程，不自动同步全部历史资料。")
    by_id = {course_identifier(row["id"]): row for row in rows if row.get("id") is not None}
    if any(item not in by_id for item in ids):
        raise ValueError("部分课程不在当前账号目录中，请刷新后重试。")
    selected = [row for key, row in by_id.items() if (not ids or key in ids) and (not term or term_identity(row)[0] == term)]
    if not selected or (ids and len(selected) != len(ids)):
        raise ValueError("课程不属于所选学期或学期目录已变化。")
    return selected


def current_course_ids(session: Session) -> set[int] | None:
    """None 为尚无标记的旧库；空集合表示明确没有当前课程。"""
    state = session.scalar(select(SyncState).where(SyncState.source == "canvas", SyncState.resource == "courses"))
    if state is None:
        return None
    if state.cursor:
        try:
            value = json.loads(state.cursor)
            if type(value) is not dict:
                raise ValueError()
            ids = value["course_ids"]
            if value.get("version") != 1 or type(ids) is not list or len(ids) > 10000:
                raise ValueError()
            sources = [course_identifier(item) for item in ids]
        except (ValueError, TypeError, KeyError, RecursionError):
            return set()
        return set(session.scalars(select(Course.id).where(Course.source_id.in_(sources))))
    if state.last_success_at is None:
        return set()
    return set(session.scalars(select(Course.id).where(Course.updated_at == state.last_success_at)))


def scoped_database_ids(session: Session, term_id: object = "", course_ids: object = None) -> set[int] | None:
    term, ids = validate_scope(term_id, course_ids)
    if not term and not ids:
        return None
    return {row.id for row in session.scalars(select(Course))
            if (not ids or row.source_id in ids)
            and (not term or term_identity(row.raw_data or {}, row.term_name)[0] == term)}


def default_conditions(session: Session, column) -> tuple:
    ids = current_course_ids(session)
    return () if ids is None else (column.in_(ids),)


def knowledge_metadata(engine, archive_root) -> dict[str, dict]:
    """通过数据库原始文件身份关联知识，绝不按文件夹名称猜学期。"""
    from pathlib import Path
    from sjtu_learning_assistant.models import CourseFile
    root = Path(archive_root).resolve()
    result = {}
    with Session(engine) as session:
        for file, course in session.execute(select(CourseFile, Course).join(Course).where(CourseFile.is_active.is_(True), CourseFile.local_path.is_not(None))):
            path = Path(file.local_path)
            if not path.is_absolute():
                path = root / path
            try:
                relative = path.resolve().relative_to(root).as_posix()
            except (ValueError, OSError):
                continue
            term, label = term_identity(course.raw_data or {}, course.term_name)
            result[relative] = {"course_id": course.source_id, "course_name": course.name, "term_id": term, "term_name": label,
                                "course_file_source_id": file.source_id}
    return result
