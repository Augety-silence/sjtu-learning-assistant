"""Dashboard 可复用的数据查询、同步与本地文件安全操作服务。"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import stat
import subprocess
import sys
import threading
from urllib.parse import urljoin, urlparse, urlsplit
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, or_, select, update
from sqlalchemy.orm import Session

from sjtu_learning_assistant.ai_classifier import (
    AIClassificationError,
    OpenAIClassificationClient,
)
from sjtu_learning_assistant.ai_keychain import (
    AIKeychainError,
    get_ai_api_key,
    save_ai_api_key,
)
from sjtu_learning_assistant.archive_service import (
    DEFAULT_ARCHIVE_ROOT,
    ArchiveService,
)
from sjtu_learning_assistant.local_settings import (
    LocalSettings,
    SettingsError,
    SettingsStore,
    validate_ai_base_url,
    validate_ai_model,
)
from sjtu_learning_assistant.material_tree import build_material_tree
from sjtu_learning_assistant.models import (
    Announcement,
    Assignment,
    Course,
    CourseFile,
    Email,
    EmailAttachment,
    SyncRun,
    SyncState,
    UnifiedItem,
)
from sjtu_learning_assistant.repository import MAIL_ATTACHMENTS_ROOT
from sjtu_learning_assistant.text_content import (
    extract_html_resource_ids,
    html_to_plain_text,
    sanitize_html,
    sanitize_html_with_resources,
)
from sync_runner import DEFAULT_LOCK_PATH

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DONE_STATES = {"submitted", "graded", "pending_review"}
MESSAGE_KINDS = frozenset({"email", "announcement", "assignment"})
CANVAS_ORIGIN = ("https", "oc.sjtu.edu.cn", 443)
CANVAS_REDIRECT_LIMIT = 5
INLINE_IMAGE_LIMIT = 5 * 1024 * 1024
INLINE_IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)


class DashboardError(RuntimeError):
    """可安全展示给用户的 Dashboard 错误。"""


class NotFoundError(DashboardError):
    """请求的本地资源不存在。"""


def to_shanghai(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SHANGHAI_TZ).isoformat(timespec="seconds")


def _course_name(value: str | None) -> str:
    return value or "未归属课程"


def _sender_name(value: str | None) -> str:
    # sender_address 和包含账号信息的邮件 source_id 永不进入 API DTO。
    clean = " ".join((value or "").split()).strip()
    if not clean:
        return "邮件"
    if "@" in clean or "<" in clean or ">" in clean:
        return "邮件发件人"
    return clean[:80]


class DashboardService:
    def __init__(
        self,
        engine: Engine,
        *,
        archive_root: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        settings_store: SettingsStore | None = None,
        folder_picker: Callable[[], str | None] | None = None,
        archive_service_factory: Callable[[], ArchiveService] | None = None,
        command_runner: Callable[..., Any] | None = None,
        process_launcher: Callable[..., Any] | None = None,
        ai_key_loader: Callable[[], str | None] = get_ai_api_key,
        ai_key_saver: Callable[[object], None] = save_ai_api_key,
        ai_client_factory: Callable[..., OpenAIClassificationClient] = OpenAIClassificationClient,
        mail_attachments_root: Path | None = None,
        canvas_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.engine = engine
        self.settings_store = settings_store or SettingsStore()
        self._archive_root_override = archive_root
        settings = self.settings_store.resolve(archive_root=archive_root)
        self.archive_root = Path(settings.archive_root)
        self.folder_picker = folder_picker
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.archive_service_factory = archive_service_factory
        self.command_runner = command_runner or subprocess.run
        self.process_launcher = process_launcher or subprocess.Popen
        self.ai_key_loader = ai_key_loader
        self.ai_key_saver = ai_key_saver
        self.ai_client_factory = ai_client_factory
        self.mail_attachments_root = Path(
            mail_attachments_root or MAIL_ATTACHMENTS_ROOT
        )
        self.canvas_client_factory = canvas_client_factory
        self._sync_lock = threading.Lock()

    def now(self) -> datetime:
        value = self.now_provider()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def health(self) -> dict[str, Any]:
        with self.engine.connect() as connection:
            connection.execute(select(1))
        return {"status": "ok", "time": to_shanghai(self.now())}

    @staticmethod
    def _unfinished_assignment_conditions(now: datetime, until: datetime) -> tuple[Any, ...]:
        return (
            Assignment.is_active.is_(True),
            Assignment.due_at.is_not(None),
            Assignment.due_at >= now,
            Assignment.due_at <= until,
            or_(
                Assignment.submission_state.is_(None),
                func.lower(Assignment.submission_state).not_in(DONE_STATES),
            ),
        )

    def overview(self) -> dict[str, Any]:
        now = self.now()
        until = now + timedelta(days=7)
        with Session(self.engine) as session:
            courses = session.scalar(select(func.count(Course.id))) or 0
            deadlines = session.scalar(
                select(func.count(Assignment.id)).where(
                    *self._unfinished_assignment_conditions(now, until)
                )
            ) or 0
            unread = session.scalar(
                select(func.count(UnifiedItem.id)).where(
                    UnifiedItem.source == "email",
                    UnifiedItem.item_type == "email",
                    UnifiedItem.is_active.is_(True),
                    UnifiedItem.is_read.is_(False),
                )
            ) or 0
        return {
            "courses": int(courses),
            "upcoming_deadlines": int(deadlines),
            "unread_emails": int(unread),
            "deadlines": self.deadlines(168, limit=5),
            "messages": self.messages("all", limit=5),
        }

    def deadlines(self, hours: int, *, limit: int = 100) -> list[dict[str, Any]]:
        now = self.now()
        until = now + timedelta(hours=hours)
        with Session(self.engine) as session:
            rows = session.execute(
                select(Assignment, Course)
                .join(Course, Course.id == Assignment.course_id)
                .where(*self._unfinished_assignment_conditions(now, until))
                .order_by(Assignment.due_at.asc(), Assignment.id.asc())
                .limit(limit)
            ).all()
        return [
            {
                "source_id": assignment.source_id,
                "title": assignment.name,
                "course": _course_name(course.name),
                "due_at": to_shanghai(assignment.due_at),
                "submission_state": assignment.submission_state or "unsubmitted",
                "url": assignment.url,
            }
            for assignment, course in rows
        ]

    @staticmethod
    def _validate_message_kind(kind: str, *, allow_all: bool = True) -> str:
        allowed = MESSAGE_KINDS | ({"all"} if allow_all else set())
        if type(kind) is not str or kind not in allowed:
            raise DashboardError("消息筛选条件无效。")
        return kind

    def messages(self, kind: str, *, limit: int = 100) -> list[dict[str, Any]]:
        kind = self._validate_message_kind(kind)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise DashboardError("消息数量限制无效。")
        rows: list[dict[str, Any]] = []
        with Session(self.engine) as session:
            if kind in {"all", "announcement"}:
                announcement_rows = session.execute(
                    select(UnifiedItem, Announcement, Course)
                    .join(Announcement, Announcement.id == UnifiedItem.announcement_id)
                    .join(Course, Course.id == Announcement.course_id)
                    .where(
                        UnifiedItem.item_type == "announcement",
                        UnifiedItem.is_active.is_(True),
                        Announcement.is_active.is_(True),
                    )
                    .order_by(UnifiedItem.occurred_at.desc().nulls_last())
                    .limit(limit)
                ).all()
                rows.extend(
                    {
                        "source_id": unified.source_id,
                        "kind": "announcement",
                        "title": item.title,
                        "source_label": _course_name(course.name),
                        "occurred_at": to_shanghai(unified.occurred_at),
                        "is_unread": not bool(unified.is_read),
                        "url": item.url,
                    }
                    for unified, item, course in announcement_rows
                )
            if kind in {"all", "assignment"}:
                assignment_rows = session.execute(
                    select(UnifiedItem, Assignment, Course)
                    .join(Assignment, Assignment.id == UnifiedItem.assignment_id)
                    .join(Course, Course.id == Assignment.course_id)
                    .where(
                        UnifiedItem.item_type == "assignment",
                        UnifiedItem.is_active.is_(True),
                        Assignment.is_active.is_(True),
                    )
                    .order_by(UnifiedItem.occurred_at.desc().nulls_last())
                    .limit(limit)
                ).all()
                rows.extend(
                    {
                        "source_id": unified.source_id,
                        "kind": "assignment",
                        "title": item.name,
                        "source_label": _course_name(course.name),
                        "occurred_at": to_shanghai(unified.occurred_at),
                        "is_unread": not bool(unified.is_read),
                        "url": item.url,
                    }
                    for unified, item, course in assignment_rows
                )
            if kind in {"all", "email"}:
                email_rows = session.execute(
                    select(UnifiedItem, Email)
                    .join(Email, Email.id == UnifiedItem.email_id)
                    .where(
                        UnifiedItem.item_type == "email",
                        UnifiedItem.is_active.is_(True),
                    )
                    .order_by(UnifiedItem.occurred_at.desc().nulls_last())
                    .limit(limit)
                ).all()
                rows.extend(
                    {
                        "source_id": unified.source_id,
                        "kind": "email",
                        "title": item.subject,
                        "source_label": _sender_name(item.sender_name),
                        "occurred_at": to_shanghai(unified.occurred_at),
                        "is_unread": not bool(unified.is_read),
                        "url": unified.url,
                    }
                    for unified, item in email_rows
                )
        rows.sort(key=lambda row: row["occurred_at"] or "", reverse=True)
        return rows[:limit]

    @staticmethod
    def _assignment_html(raw_data: object) -> str | None:
        if not isinstance(raw_data, Mapping):
            return None
        value = raw_data.get("description")
        return value if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _safe_canvas_attachments(raw_data: object) -> list[dict[str, Any]]:
        if not isinstance(raw_data, Mapping):
            return []
        results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for key in ("attachments", "files"):
            values = raw_data.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                url = next(
                    (
                        candidate.strip()
                        for field in ("url", "download_url", "html_url")
                        if isinstance((candidate := value.get(field)), str)
                        and DashboardService._safe_https_url(candidate)
                    ),
                    None,
                )
                if url is None or url in seen_urls:
                    continue
                raw_name = next(
                    (
                        candidate
                        for field in ("display_name", "filename", "name")
                        if isinstance((candidate := value.get(field)), str)
                        and candidate.strip()
                    ),
                    "附件",
                )
                name = raw_name.replace("\\", "/").split("/")[-1]
                name = " ".join(name.split()).strip(" .")[:255] or "附件"
                raw_size = value.get("size")
                size = raw_size if type(raw_size) is int and raw_size >= 0 else None
                results.append({"name": name, "size": size, "url": url})
                seen_urls.add(url)
        return results

    @staticmethod
    def _safe_https_url(value: str) -> bool:
        try:
            parsed = urlsplit(value.strip())
            _port = parsed.port
        except ValueError:
            return False
        return bool(
            parsed.scheme.casefold() == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        )

    @staticmethod
    def _canvas_url_allowed(value: str) -> bool:
        try:
            parsed = urlsplit(value)
            port = parsed.port if parsed.port is not None else 443
        except ValueError:
            return False
        return (
            parsed.scheme.casefold(),
            (parsed.hostname or "").casefold(),
            port,
        ) == CANVAS_ORIGIN and parsed.username is None and parsed.password is None

    @staticmethod
    def _mail_attachment_name(value: str) -> str:
        leaf = value.replace("\\", "/").split("/")[-1]
        return " ".join(leaf.split()).strip(" .")[:255] or "附件"

    def _resolve_controlled_mail_file(self, local_path: str | None) -> Path:
        if not local_path:
            raise NotFoundError("邮件附件不可用。")
        try:
            lexical_root = Path(os.path.abspath(self.mail_attachments_root.expanduser()))
            if stat.S_ISLNK(lexical_root.lstat().st_mode):
                raise DashboardError("邮件附件路径不安全，已拒绝操作。")
            resolved_root = lexical_root.resolve(strict=True)
            candidate = Path(local_path).expanduser()
            if not candidate.is_absolute():
                candidate = lexical_root / candidate
            lexical = Path(os.path.abspath(candidate))
            relative = lexical.relative_to(lexical_root)
            current = lexical_root
            for part in relative.parts:
                current = current / part
                mode = current.lstat().st_mode
                if stat.S_ISLNK(mode):
                    raise DashboardError("邮件附件路径不安全，已拒绝操作。")
            resolved = lexical.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except DashboardError:
            raise
        except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as exc:
            raise NotFoundError("邮件附件不可用。") from exc
        if not stat.S_ISREG(resolved.lstat().st_mode):
            raise DashboardError("邮件附件不是普通文件，已拒绝操作。")
        return resolved

    def _read_inline_attachment(self, attachment: EmailAttachment) -> str | None:
        content_type = (attachment.content_type or "").split(";", 1)[0].casefold()
        if (
            not attachment.content_id
            or not attachment.is_inline
            or content_type not in INLINE_IMAGE_TYPES
            or attachment.size > INLINE_IMAGE_LIMIT
        ):
            return None
        try:
            path = self._resolve_controlled_mail_file(attachment.local_path)
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_size > INLINE_IMAGE_LIMIT:
                    return None
                payload = os.read(descriptor, INLINE_IMAGE_LIMIT + 1)
            finally:
                os.close(descriptor)
        except (DashboardError, OSError):
            return None
        if len(payload) > INLINE_IMAGE_LIMIT:
            return None
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    def _email_attachment_dto(self, attachment: EmailAttachment) -> dict[str, Any]:
        try:
            self._resolve_controlled_mail_file(attachment.local_path)
            available = True
        except DashboardError:
            available = False
        return {
            "id": attachment.resource_id,
            "name": self._mail_attachment_name(attachment.filename),
            "type": attachment.content_type,
            "size": attachment.size,
            "is_inline": bool(attachment.is_inline),
            "available": available,
            "inline_data_url": self._read_inline_attachment(attachment),
        }

    def message_detail(self, kind: str, source_id: str) -> dict[str, Any]:
        kind = self._validate_message_kind(kind, allow_all=False)
        if type(source_id) is not str or not source_id:
            raise DashboardError("消息标识不正确。")
        with Session(self.engine) as session:
            if kind == "email":
                row = session.execute(
                    select(UnifiedItem, Email)
                    .join(Email, Email.id == UnifiedItem.email_id)
                    .where(
                        UnifiedItem.item_type == kind,
                        UnifiedItem.source_id == source_id,
                        UnifiedItem.is_active.is_(True),
                    )
                ).one_or_none()
                if row is None:
                    raise NotFoundError("未找到消息详情。")
                unified, item = row
                title = item.subject
                source_label = _sender_name(item.sender_name)
                body = item.body_text
                body_html = sanitize_html(item.body_html)
                if not body:
                    body = html_to_plain_text(body_html)
                raw_data = item.raw_data if isinstance(item.raw_data, Mapping) else {}
                pending_body_sync = raw_data.get("rich_content_fetched") is not True
                attachments = [
                    self._email_attachment_dto(attachment)
                    for attachment in sorted(item.attachments, key=lambda value: value.id)
                ]
                resources: list[dict[str, str]] = []
                url = unified.url
            elif kind == "announcement":
                row = session.execute(
                    select(UnifiedItem, Announcement, Course)
                    .join(Announcement, Announcement.id == UnifiedItem.announcement_id)
                    .join(Course, Course.id == Announcement.course_id)
                    .where(
                        UnifiedItem.item_type == kind,
                        UnifiedItem.source_id == source_id,
                        UnifiedItem.is_active.is_(True),
                        Announcement.is_active.is_(True),
                    )
                ).one_or_none()
                if row is None:
                    raise NotFoundError("未找到消息详情。")
                unified, item, course = row
                title = item.title
                source_label = _course_name(course.name)
                body_html, resource_urls = sanitize_html_with_resources(item.body)
                body = html_to_plain_text(body_html)
                pending_body_sync = False
                attachments = self._safe_canvas_attachments(item.raw_data)
                resources = [
                    {"id": resource_id, "type": "image"} for resource_id in resource_urls
                ]
                url = item.url
            else:
                row = session.execute(
                    select(UnifiedItem, Assignment, Course)
                    .join(Assignment, Assignment.id == UnifiedItem.assignment_id)
                    .join(Course, Course.id == Assignment.course_id)
                    .where(
                        UnifiedItem.item_type == kind,
                        UnifiedItem.source_id == source_id,
                        UnifiedItem.is_active.is_(True),
                        Assignment.is_active.is_(True),
                    )
                ).one_or_none()
                if row is None:
                    raise NotFoundError("未找到消息详情。")
                unified, item, course = row
                title = item.name
                source_label = _course_name(course.name)
                raw_html = self._assignment_html(item.raw_data)
                body_html, resource_urls = sanitize_html_with_resources(raw_html)
                body = html_to_plain_text(body_html)
                pending_body_sync = False
                attachments = self._safe_canvas_attachments(item.raw_data)
                resources = [
                    {"id": resource_id, "type": "image"} for resource_id in resource_urls
                ]
                url = item.url
            return {
                "title": title,
                "source_label": source_label,
                "occurred_at": to_shanghai(unified.occurred_at),
                "body": body or "",
                "body_html": body_html,
                "format": "html" if body_html else "text",
                "pending_body_sync": pending_body_sync,
                "attachments": attachments,
                "resources": resources,
                "url": url,
                "is_unread": not bool(unified.is_read),
            }

    def _email_attachment(
        self, source_id: str, attachment_id: str
    ) -> EmailAttachment:
        with Session(self.engine) as session:
            attachment = session.scalar(
                select(EmailAttachment)
                .join(Email, Email.id == EmailAttachment.email_id)
                .where(
                    Email.source_id == source_id,
                    EmailAttachment.resource_id == attachment_id,
                )
            )
            if attachment is None:
                raise NotFoundError("未找到邮件附件。")
            session.expunge(attachment)
            return attachment

    def _mail_attachment_action(
        self, source_id: str, attachment_id: str, *, reveal: bool
    ) -> dict[str, str]:
        attachment = self._email_attachment(source_id, attachment_id)
        path = self._resolve_controlled_mail_file(attachment.local_path)
        command = ["/usr/bin/open"]
        if reveal:
            command.append("-R")
        command.append(str(path))
        completed = self.command_runner(command, check=False)
        if getattr(completed, "returncode", 0) != 0:
            raise DashboardError("无法通过 macOS 操作邮件附件。")
        return {
            "source_id": source_id,
            "attachment_id": attachment_id,
            "status": "revealed" if reveal else "opened",
        }

    def open_mail_attachment(self, source_id: str, attachment_id: str) -> dict[str, str]:
        return self._mail_attachment_action(source_id, attachment_id, reveal=False)

    def reveal_mail_attachment(self, source_id: str, attachment_id: str) -> dict[str, str]:
        return self._mail_attachment_action(source_id, attachment_id, reveal=True)

    def _new_canvas_resource_client(self) -> Any:
        if self.canvas_client_factory is not None:
            return self.canvas_client_factory()
        from test_canvas import (
            DEFAULT_BASE_URL,
            DEFAULT_TIMEOUT_SECONDS,
            build_http_client,
            get_token,
        )

        token, _ = get_token(use_keychain=True)
        return build_http_client(DEFAULT_BASE_URL, token, DEFAULT_TIMEOUT_SECONDS)

    def _canvas_image_data_url(self, resource_url: str) -> str:
        if not self._canvas_url_allowed(resource_url):
            raise NotFoundError("消息资源不可用。")
        client = self._new_canvas_resource_client()
        current_url = resource_url
        try:
            for redirect_count in range(CANVAS_REDIRECT_LIMIT + 1):
                if not self._canvas_url_allowed(current_url):
                    raise DashboardError("Canvas 图片跳转地址不安全。")
                try:
                    request = client.build_request(
                        "GET",
                        current_url,
                        headers={"Accept": ", ".join(sorted(INLINE_IMAGE_TYPES))},
                    )
                    response = client.send(
                        request, stream=True, follow_redirects=False
                    )
                except Exception as exc:
                    raise DashboardError("Canvas 图片读取失败。") from exc
                try:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= CANVAS_REDIRECT_LIMIT:
                            raise DashboardError("Canvas 图片跳转次数过多。")
                        location = response.headers.get("location")
                        if not location:
                            raise DashboardError("Canvas 图片跳转响应无效。")
                        current_url = urljoin(str(response.url), location)
                        if not self._canvas_url_allowed(current_url):
                            raise DashboardError("Canvas 图片跳转地址不安全。")
                        continue
                    if response.status_code != 200:
                        raise DashboardError("Canvas 图片读取失败。")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
                    if content_type not in INLINE_IMAGE_TYPES:
                        raise DashboardError("Canvas 资源不是受支持的图片。")
                    content_length = response.headers.get("content-length")
                    try:
                        if content_length is not None and int(content_length) > INLINE_IMAGE_LIMIT:
                            raise DashboardError("Canvas 图片超过大小限制。")
                    except ValueError:
                        pass
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > INLINE_IMAGE_LIMIT:
                            raise DashboardError("Canvas 图片超过大小限制。")
                        chunks.append(chunk)
                    encoded = base64.b64encode(b"".join(chunks)).decode("ascii")
                    return f"data:{content_type};base64,{encoded}"
                finally:
                    response.close()
            raise DashboardError("Canvas 图片跳转次数过多。")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def message_resource(
        self, kind: str, source_id: str, resource_id: str
    ) -> dict[str, str]:
        kind = self._validate_message_kind(kind, allow_all=False)
        if (
            type(source_id) is not str
            or not source_id
            or len(source_id) > 255
            or type(resource_id) is not str
            or not resource_id
            or len(resource_id) > 512
        ):
            raise DashboardError("消息资源标识不正确。")
        if kind == "email":
            with Session(self.engine) as session:
                email = session.scalar(select(Email).where(Email.source_id == source_id))
                if email is None:
                    raise NotFoundError("未找到消息资源。")
                allowed_ids = extract_html_resource_ids(email.body_html)
            if resource_id not in allowed_ids:
                raise NotFoundError("未找到消息资源。")
            attachment = self._email_attachment(source_id, resource_id)
            data_url = self._read_inline_attachment(attachment)
            if data_url is None:
                raise NotFoundError("消息资源不可用。")
            return {"data_url": data_url}

        with Session(self.engine) as session:
            if kind == "announcement":
                item = session.scalar(
                    select(Announcement).where(
                        Announcement.source_id == source_id,
                        Announcement.is_active.is_(True),
                    )
                )
                raw_html = item.body if item is not None else None
            else:
                item = session.scalar(
                    select(Assignment).where(
                        Assignment.source_id == source_id,
                        Assignment.is_active.is_(True),
                    )
                )
                raw_html = self._assignment_html(item.raw_data) if item is not None else None
        if item is None:
            raise NotFoundError("未找到消息资源。")
        _html, resource_urls = sanitize_html_with_resources(raw_html)
        resource_url = resource_urls.get(resource_id)
        if resource_url is None:
            raise NotFoundError("未找到消息资源。")
        return {"data_url": self._canvas_image_data_url(resource_url)}

    def message_mark_read(
        self, kind: str, source_ids: Sequence[str] | None = None
    ) -> dict[str, Any]:
        kind = self._validate_message_kind(kind)
        conditions = [
            UnifiedItem.is_active.is_(True),
            UnifiedItem.is_read.is_(False),
            UnifiedItem.item_type.in_(MESSAGE_KINDS),
        ]
        if kind != "all":
            conditions.append(UnifiedItem.item_type == kind)
        if source_ids is not None:
            if isinstance(source_ids, (str, bytes)) or len(source_ids) > 500:
                raise DashboardError("消息标识列表不正确。")
            unique_ids = tuple(dict.fromkeys(source_ids))
            if not unique_ids or any(
                type(source_id) is not str
                or not source_id.strip()
                or len(source_id) > 255
                or any(ord(character) < 32 or ord(character) == 127 for character in source_id)
                for source_id in unique_ids
            ):
                raise DashboardError("消息标识列表不正确。")
            conditions.append(UnifiedItem.source_id.in_(unique_ids))
        with Session(self.engine) as session, session.begin():
            result = session.execute(
                update(UnifiedItem).where(*conditions).values(is_read=True)
            )
        return {"updated": max(int(result.rowcount or 0), 0)}

    def material_tree(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            return build_material_tree(session)

    def _effective_settings(self) -> LocalSettings:
        return self.settings_store.resolve(archive_root=self._archive_root_override)

    def _new_archive_service(self) -> ArchiveService:
        if self.archive_service_factory is not None:
            return self.archive_service_factory()
        settings = self._effective_settings()
        self.archive_root = Path(settings.archive_root)
        from test_canvas import (
            DEFAULT_BASE_URL,
            DEFAULT_TIMEOUT_SECONDS,
            build_http_client,
            get_token,
        )

        token, _ = get_token(use_keychain=True)
        client = build_http_client(DEFAULT_BASE_URL, token, DEFAULT_TIMEOUT_SECONDS)
        ai_client = None
        if settings.ai_enabled and settings.ai_key_saved:
            try:
                key = self.ai_key_loader()
                if key:
                    ai_client = self.ai_client_factory(
                        api_key=key,
                        base_url=settings.ai_base_url,
                        model=settings.ai_model,
                    )
            except (AIKeychainError, AIClassificationError):
                ai_client = None
        return ArchiveService(
            self.engine,
            client,
            archive_root=self.archive_root,
            organize_by_category=settings.organize_by_category,
            use_recent_active_courses=True,
            ai_client=ai_client,
            ai_enabled=settings.ai_enabled,
        )

    def download_material(self, source_id: str) -> dict[str, Any]:
        service = self._new_archive_service()
        try:
            result = service.download_file_by_source_id(source_id)
        finally:
            self._close_archive_service(service)
        if result.status == "failed":
            raise DashboardError("文件下载失败，请稍后重试。")
        return {
            "source_id": result.source_id,
            "status": result.status,
            "size": result.size,
        }

    def move_material(self, source_id: str, target_node_id: str) -> dict[str, Any]:
        service = self._new_archive_service()
        try:
            result = service.move_file_by_source_id(source_id, target_node_id)
        finally:
            self._close_archive_service(service)
        return {
            "source_id": result.source_id,
            "status": result.status,
            "local_path": result.local_path,
        }

    def restore_material_auto(self, source_id: str) -> dict[str, Any]:
        service = self._new_archive_service()
        try:
            result = service.restore_file_auto(source_id)
        finally:
            self._close_archive_service(service)
        return {
            "source_id": result.source_id,
            "status": result.status,
            "local_path": result.local_path,
        }

    def _local_path_for_source_id(self, source_id: str) -> str | None:
        with Session(self.engine) as session:
            return session.scalar(
                select(CourseFile.local_path).where(CourseFile.source_id == source_id)
            )

    def _resolve_local_file(self, source_id: str) -> Path:
        local_path = self._local_path_for_source_id(source_id)
        if not local_path:
            raise NotFoundError("未找到可打开的本地文件。")
        try:
            lexical_root = self.archive_root.expanduser().absolute()
            root = lexical_root.resolve(strict=True)
            candidate = Path(local_path).expanduser()
            if not candidate.is_absolute():
                candidate = lexical_root / candidate
            lexical = candidate.absolute()
            lexical_relative = lexical.relative_to(lexical_root)
            current = lexical_root
            for part in lexical_relative.parts:
                current = current / part
                mode = current.lstat().st_mode
                if stat.S_ISLNK(mode):
                    raise DashboardError(
                        "本地文件路径包含 symlink，已拒绝打开。"
                    )
            resolved = lexical.resolve(strict=True)
            resolved.relative_to(root)
        except DashboardError:
            raise
        except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
            raise NotFoundError("本地文件不存在或不在归档目录内，已拒绝打开。") from exc

        if not stat.S_ISREG(resolved.lstat().st_mode):
            raise DashboardError("本地目标不是普通文件，已拒绝打开。")
        return resolved

    def open_material(self, source_id: str) -> dict[str, str]:
        path = self._resolve_local_file(source_id)
        completed = self.command_runner(["/usr/bin/open", str(path)], check=False)
        if getattr(completed, "returncode", 0) != 0:
            raise DashboardError("无法通过 macOS 打开本地文件。")
        return {"source_id": source_id, "status": "opened"}

    def reveal_material(self, source_id: str) -> dict[str, str]:
        path = self._resolve_local_file(source_id)
        completed = self.command_runner(["/usr/bin/open", "-R", str(path)], check=False)
        if getattr(completed, "returncode", 0) != 0:
            raise DashboardError("无法在 Finder 中显示本地文件。")
        return {"source_id": source_id, "status": "revealed"}

    def open_external(self, url: str) -> dict[str, str]:
        parsed = urlparse(url.strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise DashboardError("只允许打开安全的 HTTPS 链接。")
        completed = self.command_runner(["/usr/bin/open", url.strip()], check=False)
        if getattr(completed, "returncode", 0) != 0:
            raise DashboardError("无法打开外部链接。")
        return {"status": "opened"}

    def save_ai_connection_json(self, raw_config: object) -> dict[str, Any]:
        if type(raw_config) is not str or not raw_config.strip() or len(raw_config) > 16384:
            raise DashboardError("AI 连接配置 JSON 格式不正确。")
        try:
            payload = json.loads(raw_config)
        except json.JSONDecodeError:
            raise DashboardError("AI 连接配置 JSON 格式不正确。") from None
        if type(payload) is not dict or set(payload) - {"_type", "url", "key", "model"}:
            raise DashboardError("AI 连接配置字段不正确。")
        if payload.get("_type") != "newapi_channel_conn":
            raise DashboardError("AI 连接配置类型不受支持。")
        if "url" not in payload or "key" not in payload:
            raise DashboardError("AI 连接配置缺少 url 或 key。")
        try:
            base_url = validate_ai_base_url(payload["url"])
            model = validate_ai_model(
                payload.get("model", self._effective_settings().ai_model)
            )
            self.ai_key_saver(payload["key"])
            self.settings_store.update(
                {
                    "ai_base_url": base_url,
                    "ai_model": model,
                    "ai_key_saved": True,
                }
            )
        except (SettingsError, AIKeychainError) as exc:
            raise DashboardError(str(exc)) from exc
        return self.settings_status()

    def test_ai_connection(self) -> dict[str, Any]:
        settings = self._effective_settings()
        if not settings.ai_key_saved:
            raise DashboardError("请先保存 AI 连接配置。")
        try:
            key = self.ai_key_loader()
            if not key:
                raise DashboardError("未找到已保存的 AI API key。")
            client = self.ai_client_factory(
                api_key=key,
                base_url=settings.ai_base_url,
                model=settings.ai_model,
            )
            try:
                category = client.test_connection()
            finally:
                client.close()
        except DashboardError:
            raise
        except (AIKeychainError, AIClassificationError) as exc:
            raise DashboardError("AI 归档连接测试失败。") from exc
        return {"ok": True, "model": settings.ai_model, "category": category}

    @staticmethod
    def _close_archive_service(service: ArchiveService) -> None:
        for client_name in ("canvas_client", "ai_client"):
            client = getattr(service, client_name, None)
            if client is not None and hasattr(client, "close"):
                client.close()

    def update_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        settings = self.settings_store.update(dict(payload))
        if self._archive_root_override is None:
            self.archive_root = Path(settings.archive_root)
        return self.settings_status()

    def pick_archive_root(self) -> dict[str, Any]:
        if self.folder_picker is None:
            raise DashboardError("当前环境不支持选择文件夹。")
        selected = self.folder_picker()
        if selected is None:
            return {"cancelled": True, "settings": self.settings_status()}
        updated = self.settings_store.update({"archive_root": selected})
        if self._archive_root_override is None:
            self.archive_root = Path(updated.archive_root)
        return {"cancelled": False, "settings": self.settings_status()}

    def organize_archive(self) -> dict[str, Any]:
        settings = self._effective_settings()
        if not settings.organize_by_category:
            raise DashboardError("请先开启“按类别整理”。")
        service = self._new_archive_service()
        try:
            summary = service.organize_current_term()
        finally:
            self._close_archive_service(service)
        return {
            "classified": summary.classified,
            "reused": summary.reused,
            "fallback": summary.fallback,
            "moved": summary.moved,
            "unchanged": summary.unchanged,
            "failed": summary.failed,
        }

    def download_current_term(self) -> dict[str, Any]:
        service = self._new_archive_service()
        try:
            summary = service.archive_current_term()
        finally:
            self._close_archive_service(service)
        return {
            "classified": summary.classified,
            "reused": summary.reused,
            "fallback": summary.fallback,
            "downloaded": summary.downloaded,
            "unchanged": summary.unchanged,
            "failed": summary.failed,
            "skipped": summary.skipped,
            "message": summary.message,
        }

    def settings_status(self) -> dict[str, Any]:
        """Return non-sensitive preferences without probing Keychain credentials."""
        settings = self._effective_settings()
        self.archive_root = Path(settings.archive_root)
        return {
            "archive_root_ready": self.archive_root.is_dir(),
            "archive_root": str(self.archive_root),
            "auto_download_current_term": settings.auto_download_current_term,
            "organize_by_category": settings.organize_by_category,
            "mail_account": settings.mail_account,
            "ai_enabled": settings.ai_enabled,
            "ai_base_url": settings.ai_base_url,
            "ai_model": settings.ai_model,
            "ai_key_saved": settings.ai_key_saved,
        }

    def _release_sync_when_done(self, process: Any) -> None:
        try:
            waiter = getattr(process, "wait", None)
            if waiter is not None:
                waiter()
        finally:
            self._sync_lock.release()

    def trigger_sync(self) -> dict[str, str]:
        if not self._sync_lock.acquire(blocking=False):
            return {"status": "already_running", "mode": "in_app"}
        try:
            process = self.process_launcher(
                self._fallback_sync_command(),
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
            threading.Thread(
                target=self._release_sync_when_done,
                args=(process,),
                name="sjtu-sync-waiter",
                daemon=True,
            ).start()
            return {"status": "accepted", "mode": "sync_runner"}
        except Exception:
            if self._sync_lock.locked():
                self._sync_lock.release()
            raise DashboardError("无法启动同步，请稍后重试。")

    def _fallback_sync_command(self) -> list[str]:
        settings = self._effective_settings()
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--background-sync"]
        else:
            python = PROJECT_ROOT / ".venv" / "bin" / "python"
            if not python.is_file():
                python = Path(sys.executable)
            command = [
                str(python),
                str(PROJECT_ROOT / "launchd_control.py"),
                "run-once",
            ]
        if settings.mail_account:
            command.extend(("--email", settings.mail_account))
        else:
            command.append("--canvas-only")
        command.extend(("--archive-root", settings.archive_root))
        if not settings.auto_download_current_term:
            command.append("--no-download")
        if not settings.organize_by_category:
            command.append("--no-organize-by-category")
        return command

    def sync_status(self) -> dict[str, Any]:
        running = self._sync_lock.locked()
        lock = Path(DEFAULT_LOCK_PATH)
        if lock.exists():
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except BlockingIOError:
                    running = True
            finally:
                os.close(fd)

        with Session(self.engine) as session:
            last_success = session.scalar(select(func.max(SyncState.last_success_at)))
            latest_run = session.scalars(
                select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1)
            ).first()
        return {
            "status": "syncing" if running else "idle",
            "last_success_at": to_shanghai(last_success),
            "last_run_status": getattr(latest_run, "status", None),
            "last_run_at": to_shanghai(getattr(latest_run, "started_at", None)),
        }
