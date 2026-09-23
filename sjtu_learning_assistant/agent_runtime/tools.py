"""固定 ORM 查询实现的结构化只读学习工具；不存在 SQL 字符串入口。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import Engine, func, or_, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.material_tree import build_material_tree
from sjtu_learning_assistant.models import (
    Announcement,
    Assignment,
    Course,
    CourseFile,
    CourseFolder,
    Email,
    UnifiedItem,
)

TOOL_RESULT_CHAR_LIMIT = 12_000
DONE_STATES = {"submitted", "graded", "pending_review"}
MESSAGE_KINDS = frozenset({"email", "announcement", "assignment"})
FILE_CATEGORIES = frozenset({"assignments", "courseware", "supplementary", "other"})
SENSITIVE_TEXT = (
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9_.~+/=-]+"),
    re.compile(r"(?i)\b(token|password|secret|api[_ -]?key)\s*[=:]\s*[^\s,;]+"),
    re.compile(r"(?:/Users|/home)/[^\s]+"),
    re.compile(r"\b[A-Za-z]:\\[^\s]+"),
    re.compile(r"\w+://[^\s/:]+:[^\s]+@[^\s]+"),
)


class AgentToolError(RuntimeError):
    """安全、可展示的工具参数或执行错误。"""


@dataclass(frozen=True)
class ToolExecution:
    name: str
    result: dict[str, Any]
    arguments_summary: dict[str, Any]
    result_summary: dict[str, Any]


def _bounded_text(value: object, name: str, *, limit: int, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if type(value) is not str or len(value) > limit or "\x00" in value:
        raise AgentToolError(f"{name}格式无效。")
    clean = value.strip()
    if required and not clean:
        raise AgentToolError(f"{name}不能为空。")
    return clean


def _bounded_limit(value: object, *, maximum: int = 50, default: int = 20) -> int:
    if value is None:
        return default
    if type(value) is not int or not 1 <= value <= maximum:
        raise AgentToolError(f"limit 必须为 1 到 {maximum} 的整数。")
    return value


def _only(arguments: object, allowed: set[str]) -> Mapping[str, Any]:
    if type(arguments) is not dict or len(arguments) > 12 or set(arguments) - allowed:
        raise AgentToolError("工具参数字段无效。")
    return arguments


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="seconds")


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    for pattern in SENSITIVE_TEXT:
        text = pattern.sub("[已隐藏]", text)
    return text[:limit] + ("…" if len(text) > limit else "")


def _bounded_payload(value: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= TOOL_RESULT_CHAR_LIMIT:
        return value
    items = value.get("items")
    if isinstance(items, list):
        kept: list[Any] = []
        for item in items:
            candidate = {**value, "items": kept + [item], "truncated": True}
            if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) > TOOL_RESULT_CHAR_LIMIT:
                break
            kept.append(item)
        return {**value, "items": kept, "count": len(kept), "truncated": True}
    return {"truncated": True, "summary": _clip(encoded, TOOL_RESULT_CHAR_LIMIT - 50)}


class ReadOnlyToolRegistry:
    """只暴露固定查询；调用方无法提供列名、SQL、排序表达式或路径。"""

    def __init__(self, engine: Engine, *, now_provider=None) -> None:
        self.engine = engine
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._handlers = {
            "list_courses": self._list_courses,
            "search_course_files": self._search_course_files,
            "list_course_files": self._list_course_files,
            "get_deadlines": self._get_deadlines,
            "search_messages": self._search_messages,
            "get_message_detail": self._get_message_detail,
            "get_material_tree": self._get_material_tree,
        }

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def definitions(self, allowed_tools: tuple[str, ...]) -> list[dict[str, Any]]:
        schemas: dict[str, tuple[str, dict[str, Any]]] = {
            "list_courses": ("列出本地课程", {"type": "object", "properties": {"query": {"type": "string", "maxLength": 120}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}),
            "search_course_files": ("按关键词搜索课程文件元数据", {"type": "object", "properties": {"query": {"type": "string", "maxLength": 160}, "course_id": {"type": "string", "maxLength": 128}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": ["query"], "additionalProperties": False}),
            "list_course_files": ("列举课程文件元数据", {"type": "object", "properties": {"course_id": {"type": "string", "maxLength": 128}, "category": {"type": "string", "enum": sorted(FILE_CATEGORIES)}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": ["course_id"], "additionalProperties": False}),
            "get_deadlines": ("查询未完成截止事项", {"type": "object", "properties": {"hours": {"type": "integer", "minimum": 1, "maximum": 8760}, "course_id": {"type": "string", "maxLength": 128}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}),
            "search_messages": ("搜索或列出最近的邮件、公告与作业消息", {"type": "object", "properties": {"query": {"type": "string", "maxLength": 160}, "kind": {"type": "string", "enum": ["all", "email", "announcement", "assignment"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}),
            "get_message_detail": ("读取一条消息的截断详情", {"type": "object", "properties": {"kind": {"type": "string", "enum": sorted(MESSAGE_KINDS)}, "ref": {"type": "string", "maxLength": 80}}, "required": ["kind", "ref"], "additionalProperties": False}),
            "get_material_tree": ("读取截断后的资料树", {"type": "object", "properties": {"course_id": {"type": "string", "maxLength": 128}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False}),
        }
        return [
            {"type": "function", "function": {"name": name, "description": schemas[name][0], "parameters": schemas[name][1]}}
            for name in allowed_tools
        ]

    def execute(self, name: object, arguments: object, allowed_tools: tuple[str, ...]) -> ToolExecution:
        if type(name) is not str or name not in self._handlers:
            raise AgentToolError("未知工具。")
        if name not in allowed_tools:
            raise AgentToolError("当前 agent preset 未授权该工具。")
        result, arg_summary = self._handlers[name](arguments)
        result = _bounded_payload(result)
        items = result.get("items")
        summary = {
            "status": "ok",
            "count": len(items) if isinstance(items, list) else result.get("count", 1),
            "truncated": bool(result.get("truncated", False)),
        }
        return ToolExecution(name, result, arg_summary, summary)

    def _list_courses(self, raw: object):
        args = _only(raw, {"query", "limit"})
        query = _bounded_text(args.get("query"), "query", limit=120, required=False)
        limit = _bounded_limit(args.get("limit"))
        statement = select(Course).order_by(Course.term_name.desc(), Course.name.asc()).limit(limit)
        if query:
            pattern = f"%{query.casefold()}%"
            statement = statement.where(or_(func.lower(Course.name).like(pattern), func.lower(Course.course_code).like(pattern)))
        with Session(self.engine) as session:
            rows = session.scalars(statement).all()
        items = [{"course_id": row.source_id, "name": _clip(row.name, 180), "course_code": _clip(row.course_code, 80) or None, "term": _clip(row.term_name, 100) or None} for row in rows]
        return {"items": items, "count": len(items)}, {"has_query": bool(query), "limit": limit}

    @staticmethod
    def _file_row(file: CourseFile, course: Course, folder: CourseFolder | None) -> dict[str, Any]:
        category = file.manual_category if file.manual_override else file.ai_category
        if category not in FILE_CATEGORIES:
            category = "other"
        return {
            "source_id": file.source_id,
            "name": _clip(file.display_name or file.filename, 255),
            "course_id": course.source_id,
            "course": _clip(course.name, 180),
            "folder": _clip(folder.full_name or folder.name, 240) if folder else None,
            "category": category,
            "content_type": _clip(file.content_type, 100) or None,
            "size": file.size,
            "updated_at": _iso(file.source_updated_at),
            "availability": "local" if file.local_path else ("cloud" if file.cloud_path else "remote"),
        }

    def _file_query(self, course_id: str, limit: int, query: str = "", category: str = "") -> list[dict[str, Any]]:
        statement = select(CourseFile, Course, CourseFolder).join(Course, Course.id == CourseFile.course_id).outerjoin(CourseFolder, CourseFolder.id == CourseFile.folder_id).where(CourseFile.is_active.is_(True)).order_by(Course.name.asc(), CourseFile.display_name.asc()).limit(limit)
        if course_id:
            statement = statement.where(Course.source_id == course_id)
        if query:
            pattern = f"%{query.casefold()}%"
            statement = statement.where(or_(func.lower(CourseFile.display_name).like(pattern), func.lower(CourseFile.filename).like(pattern), func.lower(Course.name).like(pattern)))
        if category:
            statement = statement.where(or_(CourseFile.manual_category == category, CourseFile.ai_category == category))
        with Session(self.engine) as session:
            return [self._file_row(*row) for row in session.execute(statement).all()]

    def _search_course_files(self, raw: object):
        args = _only(raw, {"query", "course_id", "limit"})
        query = _bounded_text(args.get("query"), "query", limit=160)
        course_id = _bounded_text(args.get("course_id"), "course_id", limit=128, required=False)
        limit = _bounded_limit(args.get("limit"))
        items = self._file_query(course_id, limit, query=query)
        return {"items": items, "count": len(items)}, {"query_length": len(query), "course_filtered": bool(course_id), "limit": limit}

    def _list_course_files(self, raw: object):
        args = _only(raw, {"course_id", "category", "limit"})
        course_id = _bounded_text(args.get("course_id"), "course_id", limit=128)
        category = _bounded_text(args.get("category"), "category", limit=32, required=False)
        if category and category not in FILE_CATEGORIES:
            raise AgentToolError("category 无效。")
        limit = _bounded_limit(args.get("limit"))
        items = self._file_query(course_id, limit, category=category)
        return {"items": items, "count": len(items)}, {"course_filtered": True, "category": category or "all", "limit": limit}

    def _get_deadlines(self, raw: object):
        args = _only(raw, {"hours", "course_id", "limit"})
        hours = args.get("hours", 24 * 30)
        if type(hours) is not int or not 1 <= hours <= 8760:
            raise AgentToolError("hours 必须为 1 到 8760 的整数。")
        course_id = _bounded_text(args.get("course_id"), "course_id", limit=128, required=False)
        limit = _bounded_limit(args.get("limit"))
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        statement = select(Assignment, Course).join(Course, Course.id == Assignment.course_id).where(Assignment.is_active.is_(True), Assignment.due_at.is_not(None), Assignment.due_at >= now, Assignment.due_at <= now + timedelta(hours=hours), or_(Assignment.submission_state.is_(None), func.lower(Assignment.submission_state).not_in(DONE_STATES))).order_by(Assignment.due_at.asc()).limit(limit)
        if course_id:
            statement = statement.where(Course.source_id == course_id)
        with Session(self.engine) as session:
            rows = session.execute(statement).all()
        items = [{"title": _clip(item.name, 240), "course_id": course.source_id, "course": _clip(course.name, 180), "due_at": _iso(item.due_at), "submission_state": item.submission_state or "unsubmitted"} for item, course in rows]
        return {"items": items, "count": len(items), "window_hours": hours}, {"hours": hours, "course_filtered": bool(course_id), "limit": limit}

    def _search_messages(self, raw: object):
        args = _only(raw, {"query", "kind", "limit"})
        query = _bounded_text(
            args.get("query"), "query", limit=160, required=False
        )
        kind = args.get("kind", "all")
        if type(kind) is not str or kind not in MESSAGE_KINDS | {"all"}:
            raise AgentToolError("kind 无效。")
        limit = _bounded_limit(args.get("limit"))
        pattern = f"%{query.casefold()}%"
        results: list[dict[str, Any]] = []
        with Session(self.engine) as session:
            if kind in {"all", "email"}:
                statement = (
                    select(Email, UnifiedItem)
                    .join(UnifiedItem, UnifiedItem.email_id == Email.id)
                    .where(UnifiedItem.is_active.is_(True))
                )
                if query:
                    statement = statement.where(
                        or_(
                            func.lower(Email.subject).like(pattern),
                            func.lower(Email.body_preview).like(pattern),
                            func.lower(Email.sender_name).like(pattern),
                        )
                    )
                rows = session.execute(
                    statement.order_by(UnifiedItem.occurred_at.desc()).limit(limit)
                ).all()
                results.extend(
                    {
                        "ref": f"email:{item.id}",
                        "kind": "email",
                        "title": _clip(item.subject, 240),
                        "source_label": "邮件发件人"
                        if "@" in (item.sender_name or "")
                        else _clip(item.sender_name or "邮件", 80),
                        "occurred_at": _iso(unified.occurred_at),
                        "preview": _clip(item.body_preview, 320),
                    }
                    for item, unified in rows
                )
            if kind in {"all", "announcement"}:
                statement = (
                    select(Announcement, Course, UnifiedItem)
                    .join(Course, Course.id == Announcement.course_id)
                    .join(UnifiedItem, UnifiedItem.announcement_id == Announcement.id)
                    .where(
                        Announcement.is_active.is_(True),
                        UnifiedItem.is_active.is_(True),
                    )
                )
                if query:
                    statement = statement.where(
                        or_(
                            func.lower(Announcement.title).like(pattern),
                            func.lower(Announcement.body).like(pattern),
                            func.lower(Course.name).like(pattern),
                        )
                    )
                rows = session.execute(
                    statement.order_by(UnifiedItem.occurred_at.desc()).limit(limit)
                ).all()
                results.extend(
                    {
                        "ref": f"announcement:{item.id}",
                        "kind": "announcement",
                        "title": _clip(item.title, 240),
                        "source_label": _clip(course.name, 180),
                        "occurred_at": _iso(unified.occurred_at),
                        "preview": _clip(item.body, 320),
                    }
                    for item, course, unified in rows
                )
            if kind in {"all", "assignment"}:
                statement = (
                    select(Assignment, Course, UnifiedItem)
                    .join(Course, Course.id == Assignment.course_id)
                    .join(UnifiedItem, UnifiedItem.assignment_id == Assignment.id)
                    .where(
                        Assignment.is_active.is_(True),
                        UnifiedItem.is_active.is_(True),
                    )
                )
                if query:
                    statement = statement.where(
                        or_(
                            func.lower(Assignment.name).like(pattern),
                            func.lower(Course.name).like(pattern),
                        )
                    )
                rows = session.execute(
                    statement.order_by(UnifiedItem.occurred_at.desc()).limit(limit)
                ).all()
                results.extend(
                    {
                        "ref": f"assignment:{item.id}",
                        "kind": "assignment",
                        "title": _clip(item.name, 240),
                        "source_label": _clip(course.name, 180),
                        "occurred_at": _iso(unified.occurred_at),
                        "preview": "",
                    }
                    for item, course, unified in rows
                )
        results.sort(key=lambda item: item["occurred_at"] or "", reverse=True)
        results = results[:limit]
        return {"items": results, "count": len(results)}, {
            "has_query": bool(query),
            "query_length": len(query),
            "kind": kind,
            "limit": limit,
        }

    def _get_message_detail(self, raw: object):
        args = _only(raw, {"kind", "ref"})
        kind = _bounded_text(args.get("kind"), "kind", limit=16)
        if kind not in MESSAGE_KINDS:
            raise AgentToolError("kind 无效。")
        ref = _bounded_text(args.get("ref"), "ref", limit=80)
        prefix = kind + ":"
        if not ref.startswith(prefix) or not ref[len(prefix):].isdigit():
            raise AgentToolError("消息 ref 无效。")
        item_id = int(ref[len(prefix):])
        with Session(self.engine) as session:
            if kind == "email":
                item = session.get(Email, item_id)
                if item is None:
                    raise AgentToolError("消息不存在。")
                result = {"ref": ref, "kind": kind, "title": _clip(item.subject, 240), "source_label": "邮件发件人" if "@" in (item.sender_name or "") else _clip(item.sender_name or "邮件", 80), "occurred_at": _iso(item.sent_at or item.received_at), "body": _clip(item.body_text or item.body_preview, 3000), "attachments": [{"name": _clip(a.filename, 255), "type": _clip(a.content_type, 100), "size": a.size} for a in item.attachments[:20]]}
            elif kind == "announcement":
                row = session.execute(select(Announcement, Course).join(Course, Course.id == Announcement.course_id).where(Announcement.id == item_id, Announcement.is_active.is_(True))).one_or_none()
                if row is None:
                    raise AgentToolError("消息不存在。")
                item, course = row
                result = {"ref": ref, "kind": kind, "title": _clip(item.title, 240), "source_label": _clip(course.name, 180), "occurred_at": _iso(item.posted_at), "body": _clip(item.body, 3000), "attachments": []}
            else:
                row = session.execute(select(Assignment, Course).join(Course, Course.id == Assignment.course_id).where(Assignment.id == item_id, Assignment.is_active.is_(True))).one_or_none()
                if row is None:
                    raise AgentToolError("消息不存在。")
                item, course = row
                description = item.raw_data.get("description", "") if isinstance(item.raw_data, Mapping) else ""
                result = {"ref": ref, "kind": kind, "title": _clip(item.name, 240), "source_label": _clip(course.name, 180), "occurred_at": _iso(item.due_at), "body": _clip(description, 3000), "attachments": []}
        return result, {"kind": kind, "ref_type": "opaque_local"}

    def _get_material_tree(self, raw: object):
        args = _only(raw, {"course_id", "limit"})
        course_id = _bounded_text(args.get("course_id"), "course_id", limit=128, required=False)
        limit = _bounded_limit(args.get("limit"), maximum=200, default=100)
        with Session(self.engine) as session:
            tree = build_material_tree(session)
        flat: list[dict[str, Any]] = []
        def visit(node: dict[str, Any], parents: tuple[str, ...] = ()) -> None:
            if len(flat) >= limit:
                return
            if node.get("kind") == "file" and (not course_id or node.get("course_id") == course_id):
                flat.append({"source_id": node.get("source_id"), "name": _clip(node.get("name"), 255), "course_id": node.get("course_id"), "category": node.get("category"), "folders": list(parents[-6:]), "size": node.get("size"), "availability": "cloud" if node.get("cloud_ready") else ("local" if node.get("can_open") else "remote")})
            next_parents = parents
            if node.get("kind") in {"term", "course", "category", "folder"}:
                next_parents = parents + (_clip(node.get("name"), 120),)
            for child in node.get("children", []):
                visit(child, next_parents)
        visit(tree["root"])
        return {"items": flat, "count": len(flat), "truncated": len(flat) >= limit}, {"course_filtered": bool(course_id), "limit": limit}
