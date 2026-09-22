"""Dashboard 可复用的数据查询、同步与本地文件安全操作服务。"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import sys
import threading
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, or_, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.archive_service import (
    DEFAULT_ARCHIVE_ROOT,
    ArchiveService,
)
from sjtu_learning_assistant.local_settings import (
    LocalSettings,
    SettingsStore,
)
from sjtu_learning_assistant.material_tree import build_material_tree
from sjtu_learning_assistant.models import (
    Announcement,
    Assignment,
    Course,
    CourseFile,
    Email,
    SyncRun,
    SyncState,
)
from sync_runner import DEFAULT_LOCK_PATH

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DONE_STATES = {"submitted", "graded", "pending_review"}


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
                select(func.count(Email.id)).where(Email.is_unread.is_(True))
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

    def messages(self, kind: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with Session(self.engine) as session:
            if kind in {"all", "announcement"}:
                announcement_rows = session.execute(
                    select(Announcement, Course)
                    .join(Course, Course.id == Announcement.course_id)
                    .where(Announcement.is_active.is_(True))
                    .order_by(Announcement.posted_at.desc().nulls_last())
                    .limit(limit)
                ).all()
                rows.extend(
                    {
                        "kind": "announcement",
                        "title": item.title,
                        "source_label": _course_name(course.name),
                        "occurred_at": to_shanghai(item.posted_at),
                        "is_unread": False,
                        "url": item.url,
                    }
                    for item, course in announcement_rows
                )
            if kind in {"all", "email"}:
                email_rows = session.scalars(
                    select(Email)
                    .order_by(Email.sent_at.desc().nulls_last())
                    .limit(limit)
                ).all()
                rows.extend(
                    {
                        "kind": "email",
                        "title": item.subject,
                        "source_label": _sender_name(item.sender_name),
                        "occurred_at": to_shanghai(item.sent_at or item.received_at),
                        "is_unread": bool(item.is_unread),
                        "url": None,
                    }
                    for item in email_rows
                )
        rows.sort(key=lambda row: row["occurred_at"] or "", reverse=True)
        return rows[:limit]

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
        return ArchiveService(
            self.engine,
            client,
            archive_root=self.archive_root,
            organize_by_category=settings.organize_by_category,
            use_recent_active_courses=True,
        )

    def download_material(self, source_id: str) -> dict[str, Any]:
        service = self._new_archive_service()
        try:
            result = service.download_file_by_source_id(source_id)
        finally:
            client = getattr(service, "canvas_client", None)
            if client is not None and hasattr(client, "close"):
                client.close()
        if result.status == "failed":
            raise DashboardError("文件下载失败，请稍后重试。")
        return {
            "source_id": result.source_id,
            "status": result.status,
            "size": result.size,
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
            client = getattr(service, "canvas_client", None)
            if client is not None and hasattr(client, "close"):
                client.close()
        return {
            "moved": summary.moved,
            "unchanged": summary.unchanged,
            "failed": summary.failed,
        }

    def download_current_term(self) -> dict[str, Any]:
        service = self._new_archive_service()
        try:
            summary = service.archive_current_term()
        finally:
            client = getattr(service, "canvas_client", None)
            if client is not None and hasattr(client, "close"):
                client.close()
        return {
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
