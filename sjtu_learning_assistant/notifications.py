"""幂等生成并安全发送 macOS 学习提醒。"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from sjtu_learning_assistant.models import (
    Assignment,
    Course,
    NotificationEvent,
    SyncState,
    UnifiedItem,
)

OSASCRIPT_PATH = "/usr/bin/osascript"
MAX_NOTIFICATIONS_PER_CATEGORY = 3
MAX_TEXT_LENGTH = 512
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
COMPLETED_SUBMISSION_STATES = frozenset(
    {"submitted", "graded", "pending_review", "complete"}
)

# 通知内容只通过 argv 传入，绝不拼进 AppleScript 源码。
NOTIFICATION_SCRIPT = """
on run argv
    set notificationTitle to item 1 of argv
    set notificationSubtitle to item 2 of argv
    set notificationBody to item 3 of argv
    display notification notificationBody with title notificationTitle subtitle notificationSubtitle
end run
""".strip()

CATEGORY_LABELS = {
    "new_announcement": "新公告",
    "new_assignment": "新作业",
    "new_file": "新文件",
    "assignment_due_24h": "24 小时内截止作业",
}


class NotificationError(RuntimeError):
    """系统通知无法发送。"""


@dataclass(frozen=True)
class NotificationCandidate:
    event_key: str
    event_type: str
    title: str
    subtitle: str
    body: str
    item_id: int | None = None


@dataclass
class NotificationSummary:
    sent_batches: int = 0
    failed_batches: int = 0
    suppressed_events: int = 0
    duplicate_events: int = 0
    completed: bool = True

    def merge(self, other: "NotificationSummary") -> None:
        self.sent_batches += other.sent_batches
        self.failed_batches += other.failed_batches
        self.suppressed_events += other.suppressed_events
        self.duplicate_events += other.duplicate_events
        self.completed = self.completed and other.completed


class NotificationSender(Protocol):
    def send(self, title: str, subtitle: str, body: str) -> None: ...


class EventStore(Protocol):
    def reserve(
        self, candidates: Iterable[NotificationCandidate], *, suppressed: bool
    ) -> list[NotificationCandidate]: ...

    def record_sent(self, candidates: Iterable[NotificationCandidate]) -> None: ...

    def load_canvas_cursor(self) -> int | None: ...

    def advance_canvas_cursor(self, cursor: int) -> None: ...


def _dialect_insert(session: Session, model):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as postgresql_insert

        return postgresql_insert(model)
    if dialect == "sqlite":
        return sqlite_insert(model)
    raise RuntimeError(f"不支持的数据库方言：{dialect}")


def sanitize_notification_text(value: object, *, limit: int = MAX_TEXT_LENGTH) -> str:
    """移除 NUL/控制字符并限制长度，保留正文中的换行与制表符。"""
    text = str(value or "")
    cleaned = "".join(
        character
        for character in text
        if character in "\n\t" or (character >= " " and character != "\x7f")
    ).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


class MacOSNotificationSender:
    """使用固定路径 osascript；不启用 shell，也不插值外部文本。"""

    def __init__(self, *, runner=subprocess.run, platform: str | None = None) -> None:
        self.runner = runner
        self.platform = platform or sys.platform

    def send(self, title: str, subtitle: str, body: str) -> None:
        if self.platform != "darwin":
            raise NotificationError("macOS 系统通知只能在 macOS 上发送。")
        arguments = [
            OSASCRIPT_PATH,
            "-e",
            NOTIFICATION_SCRIPT,
            "--",
            sanitize_notification_text(title) or "SJTU Learning Assistant",
            sanitize_notification_text(subtitle),
            sanitize_notification_text(body) or "同步完成。",
        ]
        try:
            completed = self.runner(
                arguments,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise NotificationError(f"无法执行 {OSASCRIPT_PATH}：{exc}") from exc
        if completed.returncode != 0:
            detail = sanitize_notification_text(completed.stderr, limit=200)
            raise NotificationError(
                f"osascript 退出码 {completed.returncode}"
                + (f"：{detail}" if detail else "")
            )


class SqlNotificationEventStore:
    """查询已记账事件，并仅在抑制或发送成功后写入账本。"""

    NOTIFICATION_SOURCE = "notification"
    CANVAS_CURSOR_RESOURCE = "canvas_items"

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _event_rows(
        candidates: Iterable[NotificationCandidate],
        *,
        status: str,
        now: datetime,
    ) -> list[dict[str, object]]:
        return [
            {
                "event_key": candidate.event_key,
                "event_type": candidate.event_type,
                "item_id": candidate.item_id,
                "title": candidate.title,
                "body": candidate.body,
                "status": status,
                "attempted_at": now,
                "sent_at": now if status == "sent" else None,
                "last_error": None,
            }
            for candidate in candidates
        ]

    def reserve(
        self, candidates: Iterable[NotificationCandidate], *, suppressed: bool
    ) -> list[NotificationCandidate]:
        unique = {candidate.event_key: candidate for candidate in candidates}
        if not unique:
            return []

        if not suppressed:
            # Phase 3A 的单实例锁负责避免重复发送。这里不提前插入 pending，
            # 因而发送失败后不会留下阻止下次重试的永久占位。
            with Session(self.engine) as session:
                recorded_keys = set(
                    session.scalars(
                        select(NotificationEvent.event_key).where(
                            NotificationEvent.event_key.in_(tuple(unique)),
                            NotificationEvent.status.in_(("sent", "suppressed")),
                        )
                    ).all()
                )
            return [
                candidate
                for event_key, candidate in unique.items()
                if event_key not in recorded_keys
            ]

        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            statement = _dialect_insert(session, NotificationEvent).values(
                self._event_rows(unique.values(), status="suppressed", now=now)
            )
            # 兼容旧实现遗留的 pending/failed 占位：新通知基线应将它们抑制；
            # 已经 sent/suppressed 的记录保持原样。并发唯一键冲突不会抛错。
            statement = statement.on_conflict_do_update(
                index_elements=[NotificationEvent.event_key],
                set_={
                    "event_type": statement.excluded.event_type,
                    "item_id": statement.excluded.item_id,
                    "title": statement.excluded.title,
                    "body": statement.excluded.body,
                    "status": "suppressed",
                    "attempted_at": now,
                    "sent_at": None,
                    "last_error": None,
                    "updated_at": now,
                },
                where=NotificationEvent.status.in_(("pending", "failed")),
            ).returning(NotificationEvent.event_key)
            recorded_keys = set(session.scalars(statement).all())
        return [
            candidate
            for event_key, candidate in unique.items()
            if event_key in recorded_keys
        ]

    def record_sent(self, candidates: Iterable[NotificationCandidate]) -> None:
        unique = {candidate.event_key: candidate for candidate in candidates}
        if not unique:
            return
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            statement = _dialect_insert(session, NotificationEvent).values(
                self._event_rows(unique.values(), status="sent", now=now)
            )
            # 正常路径为成功发送后的 INSERT。若与并发写入或旧版失败占位冲突，
            # 只把 pending/failed 修正为 sent；既有 sent/suppressed 保持不变。
            statement = statement.on_conflict_do_update(
                index_elements=[NotificationEvent.event_key],
                set_={
                    "event_type": statement.excluded.event_type,
                    "item_id": statement.excluded.item_id,
                    "title": statement.excluded.title,
                    "body": statement.excluded.body,
                    "status": "sent",
                    "attempted_at": now,
                    "sent_at": now,
                    "last_error": None,
                    "updated_at": now,
                },
                where=NotificationEvent.status.in_(("pending", "failed")),
            )
            session.execute(statement)

    def load_canvas_cursor(self) -> int | None:
        with Session(self.engine) as session:
            cursor = session.scalar(
                select(SyncState.cursor).where(
                    SyncState.source == self.NOTIFICATION_SOURCE,
                    SyncState.resource == self.CANVAS_CURSOR_RESOURCE,
                )
            )
        if cursor is None:
            return None
        try:
            parsed = int(cursor)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"非法通知游标：{cursor!r}") from exc
        if parsed < 0:
            raise ValueError(f"非法通知游标：{cursor!r}")
        return parsed

    def advance_canvas_cursor(self, cursor: int) -> None:
        if cursor < 0:
            raise ValueError("通知游标不能为负数")
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            statement = _dialect_insert(session, SyncState).values(
                source=self.NOTIFICATION_SOURCE,
                resource=self.CANVAS_CURSOR_RESOURCE,
                cursor=str(cursor),
                last_sync_at=now,
                last_success_at=now,
                status="success",
                last_error=None,
            )
            statement = statement.on_conflict_do_update(
                index_elements=[SyncState.source, SyncState.resource],
                set_={
                    "cursor": str(cursor),
                    "last_sync_at": now,
                    "last_success_at": now,
                    "status": "success",
                    "last_error": None,
                    "updated_at": now,
                },
            )
            session.execute(statement)


def _event_key(prefix: str, source_id: str, suffix: str | None = None) -> str:
    parts = [prefix, source_id]
    if suffix:
        parts.append(suffix)
    return ":".join(parts)


def _course_subtitle(course_name: str | None) -> str:
    return course_name or "Canvas"


class NotificationService:
    def __init__(
        self,
        engine: Engine,
        *,
        sender: NotificationSender | None = None,
        store: EventStore | None = None,
        now: datetime | None = None,
    ) -> None:
        self.engine = engine
        self.sender = sender or MacOSNotificationSender()
        self.store = store or SqlNotificationEventStore(engine)
        current = now or datetime.now(timezone.utc)
        self.now = current if current.tzinfo else current.replace(tzinfo=timezone.utc)

    def process_candidates(
        self, candidates: Iterable[NotificationCandidate], *, baseline: bool = False
    ) -> NotificationSummary:
        source = list(candidates)
        try:
            reserved = self.store.reserve(source, suppressed=baseline)
        except Exception:
            # 通知账本不可用时既不发送无幂等保护的通知，也不影响同步。
            return NotificationSummary(failed_batches=1, completed=False)
        summary = NotificationSummary(duplicate_events=len(source) - len(reserved))
        if baseline:
            summary.suppressed_events = len(reserved)
            return summary

        for batch in self._build_batches(reserved):
            title, subtitle, body = self._batch_content(batch)
            try:
                self.sender.send(title, subtitle, body)
            except Exception:
                # 发送失败不写永久占位；同一 event_key 下一轮仍可 reserve。
                summary.failed_batches += 1
                summary.completed = False
                continue
            try:
                self.store.record_sent(batch)
            except Exception:
                # 系统通知已显示但账本未落盘。保留 cursor 后下次重试记账；
                # 这可能再次提醒，但不会丢失事件，也不破坏数据同步。
                summary.failed_batches += 1
                summary.completed = False
                continue
            summary.sent_batches += 1
        return summary

    @staticmethod
    def _build_batches(
        candidates: list[NotificationCandidate],
    ) -> list[list[NotificationCandidate]]:
        if len(candidates) <= MAX_NOTIFICATIONS_PER_CATEGORY:
            return [[candidate] for candidate in candidates]
        individual_count = MAX_NOTIFICATIONS_PER_CATEGORY - 1
        return [
            *[[candidate] for candidate in candidates[:individual_count]],
            candidates[individual_count:],
        ]

    @staticmethod
    def _batch_content(
        batch: list[NotificationCandidate],
    ) -> tuple[str, str, str]:
        if len(batch) == 1:
            candidate = batch[0]
            return candidate.title, candidate.subtitle, candidate.body
        label = CATEGORY_LABELS.get(batch[0].event_type, "学习事项")
        lines = [f"• {item.subtitle} · {item.title}" for item in batch[:8]]
        if len(batch) > 8:
            lines.append(f"…以及另外 {len(batch) - 8} 条")
        return (
            f"另有 {len(batch)} 条{label}",
            "SJTU Learning Assistant",
            "\n".join(lines),
        )

    def process_canvas_sync(self) -> NotificationSummary:
        """处理 Canvas 通知；只有全部候选成功处理后才推进专属 Item 游标。"""
        summary = NotificationSummary()
        try:
            cursor = self.store.load_canvas_cursor()
            high_watermark = self._canvas_item_high_watermark()
        except Exception:
            return NotificationSummary(failed_batches=1, completed=False)

        baseline = cursor is None
        after_id = None if baseline else cursor
        for item_type in ("announcement", "assignment", "file"):
            summary.merge(
                self.process_candidates(
                    self._new_item_candidates(item_type, after_id=after_id),
                    baseline=baseline,
                )
            )
        # due24h 不依赖 Item cursor，每轮都扫描，并由 event_key 去重。
        summary.merge(
            self.process_candidates(
                self._due_assignment_candidates(), baseline=baseline
            )
        )

        if summary.completed:
            try:
                self.store.advance_canvas_cursor(high_watermark)
            except Exception:
                summary.failed_batches += 1
                summary.completed = False
        return summary

    def _canvas_item_high_watermark(self) -> int:
        with Session(self.engine) as session:
            value = session.scalar(
                select(func.max(UnifiedItem.id)).where(
                    UnifiedItem.source == "canvas",
                    UnifiedItem.item_type.in_(("announcement", "assignment", "file")),
                )
            )
        return int(value or 0)

    def _new_item_candidates(
        self, item_type: str, *, after_id: int | None
    ) -> list[NotificationCandidate]:
        conditions = [
            UnifiedItem.source == "canvas",
            UnifiedItem.item_type == item_type,
            UnifiedItem.is_active.is_(True),
        ]
        if after_id is not None:
            conditions.append(UnifiedItem.id > after_id)
        with Session(self.engine) as session:
            rows = session.execute(
                select(UnifiedItem, Course.name)
                .outerjoin(Course, UnifiedItem.course_id == Course.id)
                .where(*conditions)
                .order_by(UnifiedItem.id)
            ).all()
        event_type = f"new_{item_type}"
        labels = {
            "announcement": "新公告",
            "assignment": "新作业",
            "file": "新文件",
        }
        return [
            NotificationCandidate(
                event_key=_event_key(event_type, item.source_id),
                event_type=event_type,
                item_id=item.id,
                title=f"{labels[item_type]}：{item.title}",
                subtitle=_course_subtitle(course_name),
                body="点击 Canvas 查看详情。" if item.url else "请打开 Canvas 查看详情。",
            )
            for item, course_name in rows
        ]

    def _due_assignment_candidates(self) -> list[NotificationCandidate]:
        deadline = self.now + timedelta(hours=24)
        with Session(self.engine) as session:
            rows = session.execute(
                select(Assignment, Course.name, UnifiedItem.id)
                .join(Course, Assignment.course_id == Course.id)
                .outerjoin(UnifiedItem, UnifiedItem.assignment_id == Assignment.id)
                .where(
                    Assignment.is_active.is_(True),
                    Assignment.due_at.is_not(None),
                    Assignment.due_at >= self.now,
                    Assignment.due_at <= deadline,
                    or_(
                        Assignment.submission_state.is_(None),
                        Assignment.submission_state.not_in(COMPLETED_SUBMISSION_STATES),
                    ),
                )
                .order_by(Assignment.due_at, Assignment.id)
            ).all()
        candidates: list[NotificationCandidate] = []
        for assignment, course_name, item_id in rows:
            due_at = assignment.due_at
            if due_at is None:
                continue
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            due_key = due_at.astimezone(timezone.utc).isoformat()
            local_due = due_at.astimezone(SHANGHAI_TZ).strftime("%m 月 %d 日 %H:%M")
            candidates.append(
                NotificationCandidate(
                    event_key=_event_key(
                        "assignment_due_24h", assignment.source_id, due_key
                    ),
                    event_type="assignment_due_24h",
                    item_id=item_id,
                    title=f"作业即将截止：{assignment.name}",
                    subtitle=_course_subtitle(course_name),
                    body=f"截止时间：{local_due}；当前状态：未提交。",
                )
            )
        return candidates


def send_test_notification(sender: NotificationSender | None = None) -> None:
    (sender or MacOSNotificationSender()).send(
        "SJTU Learning Assistant",
        "通知测试",
        "系统通知工作正常。",
    )
