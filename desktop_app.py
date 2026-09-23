#!/usr/bin/env python3
"""Portless pywebview desktop entry, allowlisted bridge, and in-app scheduler."""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from sqlalchemy.exc import SQLAlchemyError

from sjtu_learning_assistant.archive_service import ArchiveError
from sjtu_learning_assistant.assignment_service import AssignmentServiceError
from sjtu_learning_assistant.backup_service import BackupError, BackupManager
from sjtu_learning_assistant.canvas_client import CanvasError
from sjtu_learning_assistant.cloud_storage import CloudStorageError
from sjtu_learning_assistant.local_settings import LocalSettings, SettingsError
from sjtu_learning_assistant.dashboard_service import DashboardError, DashboardService
from sjtu_learning_assistant.database import create_database_engine
from sjtu_learning_assistant.desktop_database import initialize_desktop_database
from sjtu_learning_assistant.repository import MAIL_ATTACHMENTS_ROOT
from sjtu_learning_assistant.desktop_learning_service import (
    DesktopLearningService,
    LearningServiceError,
)

PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_INDEX = PROJECT_ROOT / "dashboard-web" / "dist" / "index.html"
SYNC_INTERVAL_SECONDS = 15 * 60

_SENSITIVE_PATTERNS = (
    re.compile(r"\w+://[^\s/:]+:[^\s]+@[^\s]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_.~+/=-]+"),
    re.compile(r"(?i)(?:token|password|secret|database_url)\s*[=:]\s*[^\s,;]+"),
    re.compile(r"/Users/[^\s'\"]+"),
)


def safe_message(value: object) -> str:
    """Return a bounded user-facing error without secrets or local paths."""
    text = str(value)
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[已隐藏]", text)
    text = " ".join(text.split())[:400]
    return text or "操作失败，请稍后重试。"


def _require_payload(payload: object) -> Mapping[str, Any]:
    if payload is None:
        return {}
    if type(payload) is not dict or len(payload) > 20:
        raise DashboardError("请求参数格式不正确。")
    if any(type(key) is not str or len(key) > 80 for key in payload):
        raise DashboardError("请求参数格式不正确。")
    return payload


def _empty_payload(payload: Mapping[str, Any]) -> None:
    if payload:
        raise DashboardError("该操作不接受参数。")


def _only_keys(payload: Mapping[str, Any], allowed: set[str]) -> None:
    if set(payload) - allowed:
        raise DashboardError("请求包含不支持的参数。")


def _bounded_id(value: object, *, limit: int, label: str) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise DashboardError(f"{label}不正确。")
    return value.strip()


def _source_id(payload: Mapping[str, Any]) -> str:
    _only_keys(payload, {"source_id"})
    return _bounded_id(payload.get("source_id"), limit=255, label="资源标识")


def _target_node_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("target_node_id")
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise DashboardError("目标目录标识不正确。")
    return value.strip()


def _message_kind(payload: Mapping[str, Any], *, allow_all: bool = True) -> str:
    value = payload.get("kind", "all")
    allowed = {"email", "announcement", "assignment"}
    if allow_all:
        allowed.add("all")
    if type(value) is not str or value not in allowed:
        raise DashboardError("消息筛选条件无效。")
    return value


def _source_ids(value: object) -> list[str]:
    if type(value) is not list or not 1 <= len(value) <= 500:
        raise DashboardError("消息标识列表不正确。")
    result: list[str] = []
    for source_id in value:
        if (
            type(source_id) is not str
            or not source_id.strip()
            or len(source_id) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in source_id)
        ):
            raise DashboardError("消息标识列表不正确。")
        normalized = source_id.strip()
        if normalized not in result:
            result.append(normalized)
    return result


def _positive_id(value: object, label: str) -> int:
    if type(value) is not int or value <= 0 or value > 9_007_199_254_740_991:
        raise DashboardError(f"{label}不正确。")
    return value


def _bounded_text(value: object, *, limit: int, label: str, allow_empty: bool = False) -> str:
    if type(value) is not str or len(value) > limit or "\x00" in value:
        raise DashboardError(f"{label}不正确。")
    clean = value.strip()
    if not allow_empty and not clean:
        raise DashboardError(f"{label}不能为空。")
    return clean


def _safe_http_url(value: object) -> str:
    url = _bounded_text(value, limit=4096, label="链接")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise DashboardError("链接必须是有效的 HTTP 或 HTTPS 地址。")
    return url


def _remote_path(value: object, *, allow_root: bool = True) -> str:
    if type(value) is not list or len(value) > 128:
        raise DashboardError("云盘路径必须是分段数组。")
    if not value and not allow_root:
        raise DashboardError("云盘文件路径不能为空。")
    parts: list[str] = []
    for raw_part in value:
        if type(raw_part) is not str:
            raise DashboardError("云盘路径分段不正确。")
        part = raw_part.strip()
        if (
            not part
            or len(part) > 255
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
            or any(ord(character) < 32 or ord(character) == 127 for character in part)
        ):
            raise DashboardError("云盘路径分段不正确。")
        parts.append(part)
    if len("/".join(parts)) > 2048:
        raise DashboardError("云盘路径过长。")
    return "/".join(parts)


class DesktopBridge:
    """Single pywebview API surface; actions are explicit and allowlisted."""

    def __init__(
        self,
        service: DashboardService,
        learning_service: DesktopLearningService | None = None,
        *,
        file_picker: Callable[[], str | None] | None = None,
        backup_manager: BackupManager | None = None,
    ) -> None:
        self._service = service
        self._learning_service = learning_service
        self._file_picker = file_picker
        self._backup_manager = backup_manager
        self._picked_files: set[str] = set()
        self._handlers: dict[str, Callable[[Mapping[str, Any]], Any]] = {
            "health": lambda payload: self._without_payload(
                payload, service.health
            ),
            "overview": lambda payload: self._without_payload(
                payload, service.overview
            ),
            "deadlines": self._deadlines,
            "messages": self._messages,
            "message_detail": self._message_detail,
            "message_resource": self._message_resource,
            "mail_attachment_open": self._mail_attachment_open,
            "mail_attachment_reveal": self._mail_attachment_reveal,
            "message_mark_read": self._message_mark_read,
            "material_tree": lambda payload: self._without_payload(
                payload, service.material_tree
            ),
            "sync_status": lambda payload: self._without_payload(
                payload, service.sync_status
            ),
            "sync_trigger": lambda payload: self._without_payload(
                payload, service.trigger_sync
            ),
            "material_download": lambda payload: service.download_material(_source_id(payload)),
            "material_move": self._material_move,
            "material_restore_auto": self._material_restore_auto,
            "material_open": lambda payload: service.open_material(_source_id(payload)),
            "material_reveal": lambda payload: service.reveal_material(_source_id(payload)),
            "settings_status": self._settings_status,
            "settings_update": self._settings_update,
            "settings_ai_import": self._settings_ai_import,
            "settings_ai_test": self._settings_ai_test,
            "settings_pick_archive_root": self._settings_pick_archive_root,
            "archive_organize": self._archive_organize,
            "archive_download_current_term": self._archive_download_current_term,
            "backup_status": self._backup_status,
            "backup_start": self._backup_start,
            "open_external": self._open_external,
            "assignments_list": self._assignments_list,
            "detail": self._assignment_detail,
            "can_submit": self._assignment_can_submit,
            "submit_text": self._assignment_submit_text,
            "submit_url": self._assignment_submit_url,
            "pick_local_file": self._assignment_pick_local_file,
            "submit_local_file": self._assignment_submit_local_file,
            "pan_list": self._assignment_pan_list,
            "submit_cloud_file": self._assignment_submit_cloud_file,
            "open_external_assignment": self._assignment_open_external,
        }

    @staticmethod
    def _without_payload(
        payload: Mapping[str, Any], callback: Callable[[], Any]
    ) -> Any:
        _empty_payload(payload)
        return callback()

    def _material_move(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"source_id", "target_node_id"})
        if "source_id" not in payload or "target_node_id" not in payload:
            raise DashboardError("移动资料参数不完整。")
        source_id = _source_id(dict(source_id=payload.get("source_id")))
        return self._service.move_material(source_id, _target_node_id(payload))

    def _material_restore_auto(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"source_id"})
        if "source_id" not in payload:
            raise DashboardError("恢复自动分类参数不完整。")
        return self._service.restore_material_auto(_source_id(payload))

    def _settings_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.settings_status()

    def _settings_update(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(
            payload,
            {
                "archive_root",
                "auto_download_current_term",
                "organize_by_category",
                "mail_account",
                "ai_enabled",
                "ai_base_url",
                "ai_model",
            },
        )
        if not payload:
            raise DashboardError("没有可更新的设置。")
        return self._service.update_settings(payload)

    def _settings_ai_import(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"config_json"})
        if "config_json" not in payload:
            raise DashboardError("缺少 AI 连接配置。")
        return self._service.save_ai_connection_json(payload["config_json"])

    def _settings_ai_test(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.test_ai_connection()

    def _settings_pick_archive_root(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.pick_archive_root()

    def _archive_organize(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.organize_archive()

    def _archive_download_current_term(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.download_current_term()

    def _backup(self) -> BackupManager:
        if self._backup_manager is None:
            raise BackupError("备份服务未配置。")
        return self._backup_manager

    def _backup_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._backup().status()

    def _backup_start(self, payload: Mapping[str, Any]) -> dict[str, str]:
        _empty_payload(payload)
        return self._backup().start()

    def _deadlines(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"window"})
        window = payload.get("window", "7d")
        hours = {"24h": 24, "7d": 168, "14d": 336}.get(window)
        if hours is None:
            raise DashboardError("截止时间筛选条件无效。")
        return {"items": self._service.deadlines(hours)}

    def _messages(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"kind"})
        return {"items": self._service.messages(_message_kind(payload))}

    def _message_detail(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"kind", "source_id"})
        if "kind" not in payload or "source_id" not in payload:
            raise DashboardError("消息详情参数不完整。")
        return self._service.message_detail(
            _message_kind(payload, allow_all=False), _source_id({"source_id": payload["source_id"]})
        )

    def _message_resource(self, payload: Mapping[str, Any]) -> dict[str, str]:
        _only_keys(payload, {"kind", "source_id", "resource_id"})
        if not {"kind", "source_id", "resource_id"}.issubset(payload):
            raise DashboardError("消息资源参数不完整。")
        return self._service.message_resource(
            _message_kind(payload, allow_all=False),
            _bounded_id(payload["source_id"], limit=255, label="消息标识"),
            _bounded_id(payload["resource_id"], limit=512, label="消息资源标识"),
        )

    def _mail_attachment_payload(
        self, payload: Mapping[str, Any]
    ) -> tuple[str, str]:
        _only_keys(payload, {"kind", "source_id", "attachment_id"})
        if not {"source_id", "attachment_id"}.issubset(payload):
            raise DashboardError("邮件附件参数不完整。")
        if "kind" in payload and _message_kind(payload, allow_all=False) != "email":
            raise DashboardError("邮件附件类型不正确。")
        return (
            _bounded_id(payload["source_id"], limit=255, label="消息标识"),
            _bounded_id(payload["attachment_id"], limit=512, label="附件标识"),
        )

    def _mail_attachment_open(self, payload: Mapping[str, Any]) -> dict[str, str]:
        source_id, attachment_id = self._mail_attachment_payload(payload)
        return self._service.open_mail_attachment(source_id, attachment_id)

    def _mail_attachment_reveal(self, payload: Mapping[str, Any]) -> dict[str, str]:
        source_id, attachment_id = self._mail_attachment_payload(payload)
        return self._service.reveal_mail_attachment(source_id, attachment_id)

    def _message_mark_read(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"kind", "ids", "all"})
        kind = _message_kind(payload)
        mark_all = payload.get("all", False)
        if type(mark_all) is not bool:
            raise DashboardError("全部已读参数不正确。")
        has_ids = "ids" in payload
        if mark_all and has_ids:
            raise DashboardError("请选择消息标识列表或全部标已读。")
        if "all" in payload and not mark_all and not has_ids:
            raise DashboardError("全部已读参数不正确。")
        ids = _source_ids(payload["ids"]) if has_ids else None
        return self._service.message_mark_read(kind, ids)

    def _learning(self) -> DesktopLearningService:
        if self._learning_service is None:
            raise LearningServiceError("作业服务未配置；应用的其他功能仍可正常使用。")
        return self._learning_service

    @staticmethod
    def _assignment_ids(payload: Mapping[str, Any], extra: set[str] | None = None) -> tuple[int, int]:
        allowed = {"course_id", "assignment_id"} | (extra or set())
        _only_keys(payload, allowed)
        if not {"course_id", "assignment_id"}.issubset(payload):
            raise DashboardError("课程或作业标识缺失。")
        return (
            _positive_id(payload["course_id"], "课程标识"),
            _positive_id(payload["assignment_id"], "作业标识"),
        )

    def _assignments_list(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"category"})
        category = payload.get("category", "today")
        categories = {"today", "upcoming", "overdue", "missing", "unsubmitted", "submitted", "pending_review", "graded"}
        if type(category) is not str or category not in categories:
            raise DashboardError("作业分类不正确。")
        return self._learning().assignments_list(category)

    def _assignment_detail(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id = self._assignment_ids(payload)
        return self._learning().assignment_detail(course_id, assignment_id)

    def _assignment_can_submit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id = self._assignment_ids(payload, {"submission_type"})
        value = payload.get("submission_type")
        submission_type = None
        if value is not None:
            submission_type = _bounded_text(value, limit=80, label="提交类型")
            if submission_type not in {"online_text_entry", "online_url", "online_upload"}:
                raise DashboardError("提交类型不正确。")
        return self._learning().can_submit(course_id, assignment_id, submission_type)

    def _assignment_submit_text(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id = self._assignment_ids(payload, {"text"})
        return self._learning().submit_text(
            course_id, assignment_id, _bounded_text(payload.get("text"), limit=200_000, label="文本内容")
        )

    def _assignment_submit_url(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id = self._assignment_ids(payload, {"url"})
        return self._learning().submit_url(course_id, assignment_id, _safe_http_url(payload.get("url")))

    def _assignment_pick_local_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        if self._file_picker is None:
            raise LearningServiceError("当前环境不支持本地文件选择。")
        selected = self._file_picker()
        if not selected:
            return {"cancelled": True}
        path = Path(selected).expanduser().resolve()
        if not path.is_file() or len(str(path)) > 4096 or path.stat().st_size > 2 * 1024**3:
            raise DashboardError("所选文件无效或超过 2 GiB。")
        normalized = str(path)
        self._picked_files.add(normalized)
        return {"cancelled": False, "path": normalized, "name": path.name, "size": path.stat().st_size}

    def _assignment_submit_local_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id = self._assignment_ids(payload, {"path"})
        raw = _bounded_text(payload.get("path"), limit=4096, label="本地文件路径")
        path = str(Path(raw).expanduser().resolve())
        if path not in self._picked_files or not Path(path).is_file():
            raise DashboardError("请先通过安全文件选择器选择待提交文件。")
        self._picked_files.discard(path)
        return self._learning().submit_local_file(course_id, assignment_id, path)

    def _assignment_pan_list(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"remote_path", "page", "page_size"})
        path = _remote_path(payload.get("remote_path", []))
        page = payload.get("page", 1)
        page_size = payload.get("page_size", 50)
        if type(page) is not int or not 1 <= page <= 10_000:
            raise DashboardError("页码不正确。")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise DashboardError("每页数量不正确。")
        return self._learning().pan_list(path, page, page_size)

    def _assignment_submit_cloud_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id = self._assignment_ids(payload, {"remote_path"})
        path = _remote_path(payload.get("remote_path"), allow_root=False)
        return self._learning().submit_cloud_file(course_id, assignment_id, path)

    def _assignment_open_external(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id = self._assignment_ids(payload)
        return self._learning().open_external_assignment(course_id, assignment_id)

    def _open_external(self, payload: Mapping[str, Any]) -> dict[str, str]:
        _only_keys(payload, {"url"})
        value = payload.get("url")
        if not isinstance(value, str):
            raise DashboardError("链接不正确。")
        return self._service.open_external(value)

    def invoke(self, action: str, payload: object = None) -> dict[str, Any]:
        """The only public Bridge method exposed to JavaScript."""
        try:
            if type(action) is not str or len(action) > 80:
                return {
                    "ok": False,
                    "error": {"code": "not_allowed", "message": "不支持的操作。"},
                }
            handler = self._handlers.get(action)
            if handler is None:
                return {
                    "ok": False,
                    "error": {"code": "not_allowed", "message": "不支持的操作。"},
                }
            result = handler(_require_payload(payload))
            return {"ok": True, "data": result}
        except (
            DashboardError,
            ArchiveError,
            BackupError,
            SettingsError,
            LearningServiceError,
            AssignmentServiceError,
            CanvasError,
            CloudStorageError,
        ) as exc:
            return {
                "ok": False,
                "error": {"code": "operation_failed", "message": safe_message(exc)},
            }
        except SQLAlchemyError:
            return {
                "ok": False,
                "error": {"code": "database_unavailable", "message": "本地数据库暂时不可用。"},
            }
        except Exception:
            return {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": "操作失败；未显示底层异常，请稍后重试。",
                },
            }


class DesktopScheduler:
    """Runs a task every 15 minutes; the service owns sync de-duplication."""

    def __init__(
        self,
        task: Callable[[], Any],
        *,
        interval_seconds: float = SYNC_INTERVAL_SECONDS,
    ) -> None:
        self._task = task
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="sjtu-sync-scheduler",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self._task()
            except Exception:
                # A scheduler failure must not terminate the UI. Errors are visible
                # through sync_status without exposing exception details.
                continue

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)


def run_desktop_app() -> int:
    """Create the native window. Tests exercise components without calling this."""
    if not STATIC_INDEX.is_file():
        print("错误：前端尚未构建，请先运行 cd dashboard-web && npm run build。", file=sys.stderr)
        return 2
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        print("错误：缺少 pywebview，请重新安装依赖。", file=sys.stderr)
        return 2

    engine = create_database_engine()
    scheduler: DesktopScheduler | None = None
    service: DashboardService | None = None
    learning_service: DesktopLearningService | None = None
    backup_manager: BackupManager | None = None
    try:
        if engine.dialect.name == "sqlite":
            # Desktop startup must never migrate/import a real legacy database implicitly.
            initialize_desktop_database(engine, skip_import=True)
        def pick_folder() -> str | None:
            result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if not result:
                return None
            return str(result[0] if isinstance(result, (list, tuple)) else result)

        def pick_file() -> str | None:
            result = webview.windows[0].create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
            if not result:
                return None
            return str(result[0] if isinstance(result, (list, tuple)) else result)

        service = DashboardService(engine, folder_picker=pick_folder)
        learning_service = DesktopLearningService(engine)
        settings_store = getattr(service, "settings_store", None)
        settings = settings_store.load() if settings_store is not None else LocalSettings()
        archive_root = getattr(service, "archive_root", None) or Path(
            getattr(settings, "archive_root", LocalSettings().archive_root)
        )
        backup_manager = BackupManager(
            engine,
            archive_root=archive_root,
            mail_attachments_root=getattr(
                service, "mail_attachments_root", MAIL_ATTACHMENTS_ROOT
            ),
            mail_account=settings.mail_account,
        )
        bridge = DesktopBridge(
            service,
            learning_service,
            file_picker=pick_file,
            backup_manager=backup_manager,
        )
        scheduler = DesktopScheduler(service.trigger_sync)
        webview.create_window(
            "SJTU 学习助手",
            STATIC_INDEX.as_uri(),
            js_api=bridge,
            width=1280,
            height=820,
            min_size=(960, 640),
        )
        scheduler.start()
        webview.start(debug=False)
        return 0
    finally:
        if scheduler is not None:
            scheduler.stop()
        if backup_manager is not None:
            backup_manager.close()
        if service is not None:
            service.close()
        if learning_service is not None:
            learning_service.close()
        engine.dispose()


def main() -> int:
    """Dispatch a frozen background sync without opening a second window."""
    if getattr(sys, "frozen", False) and sys.argv[1:2] == ["--background-sync"]:
        from sync_data_to_db import main as sync_main

        return sync_main(sys.argv[2:])
    return run_desktop_app()


if __name__ == "__main__":
    raise SystemExit(main())
