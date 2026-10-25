"""固定 ORM 查询实现的结构化只读学习工具；不存在 SQL 字符串入口。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from sjtu_learning_assistant.ai_attachments import AIFileError, AIManagedFileService
from sjtu_learning_assistant.material_tree import build_material_tree
from sjtu_learning_assistant.text_content import content_to_plain_text
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
MAX_ATTACHMENT_ID = 2**63 - 1
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
DONE_STATES = {"submitted", "graded", "pending_review"}
MESSAGE_KINDS = frozenset({"email", "announcement", "assignment"})
FILE_CATEGORIES = frozenset({"assignments", "courseware", "supplementary", "other"})
SENSITIVE_TEXT = (
    # 不用 \b：中文字符属于 \w，“访问token=…”“资料在C:\…”之间没有词边界。
    re.compile(r"(?i)(?<![A-Za-z0-9])(bearer\s+)[A-Za-z0-9_.~+/=-]+"),
    re.compile(
        r"(?i)(?<![A-Za-z0-9])['\"]?(token|password|passwd|secret|api[_ -]?key|密码|口令|令牌|密钥)['\"]?"
        r"\s*[=:：]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;，；}\"']+)"
    ),
    re.compile(r"(?:/Users|/home)/[^\s]+"),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s]+"),
    re.compile(r"\\\\[^\s\\]+\\[^\s]+"),
    # (?<!\w) 只从单词串开头尝试匹配：结果与原式等价（最左匹配必从串首开始），
    # 但避免长无空白文本（大邮件正文）在每个位置重试导致的二次回溯。
    re.compile(r"(?<!\w)\w+://[^\s/:]+:[^\s]+@[^\s]+"),
)


class AgentToolError(RuntimeError):
    """安全、可展示的工具参数或执行错误。"""

    def __init__(
        self,
        message: str,
        *,
        category: str = "invalid_params",
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = category == "execution" if retryable is None else retryable


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
    return value.astimezone(LOCAL_TIMEZONE).isoformat(timespec="seconds")


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    for pattern in SENSITIVE_TEXT:
        text = pattern.sub("[已隐藏]", text)
    return text[:limit] + ("…" if len(text) > limit else "")


def _like_pattern(value: str) -> str:
    escaped = value.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
    text = value.get("text")
    if isinstance(text, str):
        # 附件正文超限时只截正文，保留 id/name/status 结构。
        clipped = text
        while clipped:
            candidate = {**value, "text": clipped, "truncated": True}
            overflow = len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) - TOOL_RESULT_CHAR_LIMIT
            if overflow <= 0:
                break
            clipped = clipped[: max(0, len(clipped) - overflow)]
        return {**value, "text": clipped, "truncated": True, "returned_chars": len(clipped)}
    return {"truncated": True, "summary": _clip(encoded, TOOL_RESULT_CHAR_LIMIT - 50)}


class ReadOnlyToolRegistry:
    """只暴露固定查询；调用方无法提供列名、SQL、排序表达式或路径。"""

    def __init__(
        self,
        engine: Engine,
        *,
        now_provider=None,
        ai_file_service: AIManagedFileService | None = None,
        attachment_ids: list[int] | tuple[int, ...] | None = None,
    ) -> None:
        self.engine = engine
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.ai_file_service = ai_file_service
        self.attachment_ids = (
            frozenset(attachment_ids) if attachment_ids is not None else None
        )
        self._handlers = {
            "list_courses": self._list_courses,
            "search_course_files": self._search_course_files,
            "list_course_files": self._list_course_files,
            "get_deadlines": self._get_deadlines,
            "search_messages": self._search_messages,
            "get_message_detail": self._get_message_detail,
            "get_material_tree": self._get_material_tree,
            "search_ai_attachments": self._search_ai_attachments,
            "read_ai_attachment_text": self._read_ai_attachment_text,
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
            "search_ai_attachments": ("只搜索 AI 附件的名称、摘要和标签，先用它定位附件 ID", {"type": "object", "properties": {"query": {"type": "string", "maxLength": 160}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}),
            "read_ai_attachment_text": ("按附件 ID 按需读取有上限的正文，不接受路径", {"type": "object", "properties": {"attachment_id": {"type": "integer", "minimum": 1, "maximum": MAX_ATTACHMENT_ID}, "max_chars": {"type": "integer", "minimum": 1, "maximum": 12000}}, "required": ["attachment_id"], "additionalProperties": False}),
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
        try:
            result, arg_summary = self._handlers[name](arguments)
        except SQLAlchemyError:
            # loop 只捕获 AgentToolError；数据库锁/损坏不应让整轮对话失败。
            raise AgentToolError("本地数据库暂时不可用，请稍后重试。", category="execution") from None
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
            pattern = _like_pattern(query)
            statement = statement.where(or_(func.lower(Course.name).like(pattern, escape="\\"), func.lower(Course.course_code).like(pattern, escape="\\")))
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
            pattern = _like_pattern(query)
            statement = statement.where(or_(func.lower(CourseFile.display_name).like(pattern, escape="\\"), func.lower(CourseFile.filename).like(pattern, escape="\\"), func.lower(Course.name).like(pattern, escape="\\")))
        if category:
            effective = case((CourseFile.manual_override.is_(True), CourseFile.manual_category), else_=CourseFile.ai_category)
            if category == "other":
                known = sorted(FILE_CATEGORIES - {"other"})
                statement = statement.where(or_(effective.is_(None), effective.not_in(known)))
            else:
                statement = statement.where(effective == category)
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
        # SQLite DateTime 绑定时直接丢弃偏移，必须先换算成 UTC。
        now = now.astimezone(timezone.utc)
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
        pattern = _like_pattern(query)
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
                            func.lower(Email.subject).like(pattern, escape="\\"),
                            func.lower(Email.body_preview).like(pattern, escape="\\"),
                            func.lower(Email.sender_name).like(pattern, escape="\\"),
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
                            func.lower(Announcement.title).like(pattern, escape="\\"),
                            func.lower(Announcement.body).like(pattern, escape="\\"),
                            func.lower(Course.name).like(pattern, escape="\\"),
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
                        "preview": _clip(content_to_plain_text(item.body), 320),
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
                            func.lower(Assignment.name).like(pattern, escape="\\"),
                            func.lower(Course.name).like(pattern, escape="\\"),
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
        digits = ref[len(prefix):]
        if not ref.startswith(prefix) or not digits.isascii() or not digits.isdigit() or len(digits) > 18:
            raise AgentToolError("消息 ref 无效。")
        item_id = int(ref[len(prefix):])
        with Session(self.engine) as session:
            if kind == "email":
                row = session.execute(
                    select(Email, UnifiedItem)
                    .join(UnifiedItem, UnifiedItem.email_id == Email.id)
                    .where(Email.id == item_id, UnifiedItem.is_active.is_(True))
                ).one_or_none()
                if row is None:
                    raise AgentToolError("消息不存在。")
                item, unified = row
                result = {"ref": ref, "kind": kind, "title": _clip(item.subject, 240), "source_label": "邮件发件人" if "@" in (item.sender_name or "") else _clip(item.sender_name or "邮件", 80), "occurred_at": _iso(unified.occurred_at), "body": _clip(item.body_text or item.body_preview, 3000), "attachments": [{"name": _clip(a.filename, 255), "type": _clip(a.content_type, 100), "size": a.size} for a in item.attachments[:20]]}
            elif kind == "announcement":
                row = session.execute(select(Announcement, Course).join(Course, Course.id == Announcement.course_id).where(Announcement.id == item_id, Announcement.is_active.is_(True))).one_or_none()
                if row is None:
                    raise AgentToolError("消息不存在。")
                item, course = row
                result = {"ref": ref, "kind": kind, "title": _clip(item.title, 240), "source_label": _clip(course.name, 180), "occurred_at": _iso(item.posted_at), "body": _clip(content_to_plain_text(item.body), 3000), "attachments": []}
            else:
                row = session.execute(select(Assignment, Course).join(Course, Course.id == Assignment.course_id).where(Assignment.id == item_id, Assignment.is_active.is_(True))).one_or_none()
                if row is None:
                    raise AgentToolError("消息不存在。")
                item, course = row
                description = item.raw_data.get("description", "") if isinstance(item.raw_data, Mapping) else ""
                result = {"ref": ref, "kind": kind, "title": _clip(item.name, 240), "source_label": _clip(course.name, 180), "occurred_at": _iso(item.due_at), "body": _clip(content_to_plain_text(description), 3000), "attachments": []}
        return result, {"kind": kind, "ref_type": "opaque_local"}

    def _get_material_tree(self, raw: object):
        args = _only(raw, {"course_id", "limit"})
        course_id = _bounded_text(args.get("course_id"), "course_id", limit=128, required=False)
        limit = _bounded_limit(args.get("limit"), maximum=200, default=100)
        with Session(self.engine) as session:
            tree = build_material_tree(session)
        flat: list[dict[str, Any]] = []
        def visit(node: dict[str, Any], parents: tuple[str, ...] = ()) -> None:
            if len(flat) > limit:
                return
            if node.get("kind") == "file" and (not course_id or node.get("course_id") == course_id):
                flat.append({"source_id": node.get("source_id"), "name": _clip(node.get("name"), 255), "course_id": node.get("course_id"), "category": node.get("category"), "folders": list(parents[-6:]), "size": node.get("size"), "availability": "cloud" if node.get("cloud_ready") else ("local" if node.get("can_open") else "remote")})
            next_parents = parents
            if node.get("kind") in {"term", "course", "category", "folder"}:
                next_parents = parents + (_clip(node.get("name"), 120),)
            for child in node.get("children", []):
                visit(child, next_parents)
        visit(tree["root"])
        truncated = len(flat) > limit
        flat = flat[:limit]
        return {"items": flat, "count": len(flat), "truncated": truncated}, {"course_filtered": bool(course_id), "limit": limit}


    def _require_ai_files(self) -> AIManagedFileService:
        if self.ai_file_service is None:
            raise AgentToolError("AI 附件服务不可用。")
        return self.ai_file_service

    def _search_ai_attachments(self, raw: object):
        args = _only(raw, {"query", "limit"})
        query = _bounded_text(
            args.get("query"), "query", limit=160, required=False
        )
        limit = _bounded_limit(args.get("limit"), maximum=50, default=20)
        scoped_ids = (
            sorted(self.attachment_ids) if self.attachment_ids is not None else None
        )
        try:
            result = self._require_ai_files().search(
                query, attachment_ids=scoped_ids, limit=limit
            )
        except AIFileError as exc:
            raise AgentToolError(str(exc), category="execution") from None
        result = {
            **result,
            "items": [
                {**item, "name": _clip(item.get("name"), 255), "summary": _clip(item.get("summary"), 400)}
                for item in result.get("items", []) if isinstance(item, dict)
            ],
        }
        return result, {
            "has_query": bool(query),
            "scope": "message" if scoped_ids is not None else "library",
            "limit": limit,
        }

    def _read_ai_attachment_text(self, raw: object):
        args = _only(raw, {"attachment_id", "max_chars"})
        attachment_id = args.get("attachment_id")
        max_chars = args.get("max_chars", 4000)
        # 上限对齐 BigInteger 主键；超大整数绑定到 SQLite 会抛 OverflowError
        # （不是 SQLAlchemyError），会绕过 execute 的兜底让整轮对话失败。
        if type(attachment_id) is not int or not 1 <= attachment_id <= MAX_ATTACHMENT_ID:
            raise AgentToolError("attachment_id 必须为正整数。")
        if type(max_chars) is not int or not 1 <= max_chars <= 12_000:
            raise AgentToolError("max_chars 必须为 1 到 12000 的整数。")
        if self.attachment_ids is not None and attachment_id not in self.attachment_ids:
            raise AgentToolError("附件不属于本轮消息。")
        try:
            result = self._require_ai_files().read_text(
                attachment_id, max_chars=max_chars
            )
        except AIFileError as exc:
            raise AgentToolError(str(exc), category="execution") from None
        if isinstance(result.get("text"), str):
            result["text"] = _clip(result["text"], max_chars)
            result["returned_chars"] = len(result["text"])
        if isinstance(result.get("name"), str):
            result["name"] = _clip(result["name"], 255)
        return result, {
            "attachment_id": attachment_id,
            "max_chars": max_chars,
        }
