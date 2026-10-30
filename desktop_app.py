#!/usr/bin/env python3
"""Portless pywebview desktop entry, allowlisted bridge, and in-app scheduler."""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import time

LOGGER = logging.getLogger(__name__)

def _configure_keyring_backend(platform_name: str | None = None) -> None:
    selected_platform = platform_name or sys.platform
    if selected_platform == "darwin":
        os.environ.setdefault(
            "PYTHON_KEYRING_BACKEND", "keyring.backends.macOS.Keyring"
        )
    elif selected_platform == "win32":
        os.environ.setdefault(
            "PYTHON_KEYRING_BACKEND", "keyring.backends.Windows.WinVaultKeyring"
        )


_configure_keyring_backend()

import re
import threading
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlparse

from sqlalchemy.exc import SQLAlchemyError

from sjtu_learning_assistant.archive_service import ArchiveError
from sjtu_learning_assistant.academic_features import AcademicFeatureError
from sjtu_learning_assistant.assignment_service import AssignmentServiceError
from sjtu_learning_assistant.backup_service import BackupError, BackupManager
from sjtu_learning_assistant.cloud_archive_service import CloudArchiveError
from sjtu_learning_assistant.restore_service import RestoreError
from sjtu_learning_assistant.canvas_client import CanvasError
from sjtu_learning_assistant.cloud_storage import (
    CloudStorageError,
    delete_user_token,
    save_user_token,
)
from sjtu_learning_assistant.local_settings import LocalSettings, SettingsError
from sjtu_learning_assistant.media_features import MediaFeatureError
from sjtu_learning_assistant.update_service import UpdateServiceError
from sjtu_learning_assistant.timetable import TimetableError, TimetableService
from sjtu_learning_assistant.dashboard_service import DashboardError, DashboardService
from sjtu_learning_assistant.database import (
    APP_SUPPORT_DIR,
    DatabaseConfigError,
    create_database_engine,
    database_recovery_result,
)
from sjtu_learning_assistant.desktop_database import (
    DesktopDatabaseError,
    initialize_desktop_database,
)
from sjtu_learning_assistant.diagnostic_bundle import (
    DiagnosticBundleError,
    DiagnosticBundleService,
    default_bundle_filename,
    sanitize_text,
)
from sjtu_learning_assistant.notifications import (
    NotificationError,
    default_notification_sender,
)
from sjtu_learning_assistant.repository import MAIL_ATTACHMENTS_ROOT
from sjtu_learning_assistant.desktop_learning_service import (
    DesktopLearningService,
    LearningServiceError,
)

PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC_INDEX = PROJECT_ROOT / "dashboard-web" / "dist" / "index.html"
SYNC_INTERVAL_SECONDS = 15 * 60

_SENSITIVE_PATTERNS = (
    re.compile(r"\w+://[^\s/:]+:[^\s]+@[^\s]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_.~+/=-]+"),
    re.compile(r"(?i)(?:token|password|secret|database_url)\s*[=:]\s*[^\s,;]+"),
    re.compile(r"/Users/[^\s'\"]+"),
    re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+[^\s'\"]+"),
)


def safe_message(value: object) -> str:
    """Return a bounded user-facing error without secrets or local paths."""
    text = str(value)
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[已隐藏]", text)
    text = sanitize_text(" ".join(text.split()), limit=800)
    bounded = text[:400]
    marker = "[已隐藏]"
    marker_position = text.find(marker)
    if 0 <= marker_position < 400 and marker not in bounded:
        bounded = bounded[: 400 - len(marker)] + marker
    return bounded or "操作失败，请稍后重试。"


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


def _validated_local_attachment_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        # Windows treats "/" like "\", so "/\server\share" is UNC and "/\.\pipe" a device.
        # On POSIX, however, a path such as "/\lecture-notes.md" is a valid absolute path.
        or (
            sys.platform == "win32"
            and (
                value.startswith("\\")
                or (len(value) > 1 and value[0] in "\\/" and value[1] in "\\/")
            )
        )
        or not Path(value).is_absolute()
    ):
        raise DashboardError("拖入文件路径无效，请改用回形针按钮选择文件。")
    return value


def _preset_id(value: object) -> str:
    result = _bounded_id(value, limit=32, label="Agent preset")
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", result) is None:
        raise DashboardError("Agent preset 不正确。")
    return result


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


def _transcript_id(value: object, label: str) -> str:
    result = _bounded_id(value, limit=80, label=label)
    if len(result) != 32 or any(character not in "0123456789abcdef" for character in result):
        raise DashboardError(f"{label}不正确。")
    return result


def _artifact_id(value: object) -> str:
    result = _bounded_id(value, limit=110, label="字幕工件标识")
    if re.fullmatch(r"[0-9a-f]{32}:v2:[0-9a-f]{32}", result) is not None:
        return result
    job_id, separator, kind = result.partition(":")
    if (
        not separator
        or len(job_id) != 32
        or any(character not in "0123456789abcdef" for character in job_id)
        or kind not in {"raw_vtt", "cues", "cleaned", "summary_json", "summary"}
    ):
        raise DashboardError("字幕工件标识不正确。")
    return result


def _v2_artifact_id(value: object) -> str:
    result = _artifact_id(value)
    if re.fullmatch(r"[0-9a-f]{32}:v2:[0-9a-f]{32}", result) is None:
        raise DashboardError("Phase1 工件标识不正确。")
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
        ics_file_picker: Callable[[], str | None] | None = None,
        save_file_picker: Callable[[str], str | None] | None = None,
        backup_manager: BackupManager | None = None,
        diagnostic_bundle: DiagnosticBundleService | None = None,
        timetable_service: TimetableService | None = None,
    ) -> None:
        self._service = service
        self._learning_service = learning_service
        self._file_picker = file_picker
        self._ics_file_picker = ics_file_picker
        self._save_file_picker = save_file_picker
        self._backup_manager = backup_manager
        self._diagnostic_bundle = diagnostic_bundle
        self._timetable = timetable_service
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
            "material_preview": lambda payload: service.material_preview(_source_id(payload)),
            "material_reveal": lambda payload: service.reveal_material(_source_id(payload)),
            "settings_status": self._settings_status,
            "settings_update": self._settings_update,
            "settings_ai_import": self._settings_ai_import,
            "settings_ai_test": self._settings_ai_test,
            "settings_credential_save": self._settings_credential_save,
            "settings_credential_delete": self._settings_credential_delete,
            "debug_bundle_export": self._debug_bundle_export,
            "ai_chat": self._ai_chat,
            "ai_presets": self._ai_presets,
            "ai_chat_sessions": self._ai_chat_sessions,
            "ai_chat_session": self._ai_chat_session,
            "ai_chat_new": self._ai_chat_new,
            "ai_chat_send": self._ai_chat_send,
            "ai_chat_stream_start": self._ai_chat_stream_start,
            "ai_task_status": self._ai_task_status,
            "ai_task_cancel": self._ai_task_cancel,
            "ai_usage_summary": self._ai_usage_summary,
            "ai_chat_delete": self._ai_chat_delete,
            "ai_attachment_list": self._ai_attachment_list,
            "knowledge_compiler_pick_folder": self._knowledge_compiler_pick_folder,
            "knowledge_compiler_inspect": self._knowledge_compiler_inspect,
            "knowledge_compiler_start": self._knowledge_compiler_start,
            "knowledge_compiler_status": self._knowledge_compiler_status,
            "knowledge_compiler_cancel": self._knowledge_compiler_cancel,
            "local_projects_list": self._local_projects_list,
            "local_projects_add": self._local_projects_add,
            "local_projects_refresh": self._local_projects_refresh,
            "local_projects_remove": self._local_projects_remove,
            "ai_attachment_pick": self._ai_attachment_pick,
            "ai_attachment_ingest": self._ai_attachment_ingest,
            "ai_attachment_restore": self._ai_attachment_restore,
            "ai_attachment_reveal": self._ai_attachment_reveal,
            "settings_pick_archive_root": self._settings_pick_archive_root,
            "archive_organize": self._archive_organize,
            "archive_download_current_term": self._archive_download_current_term,
            "backup_status": self._backup_status,
            "backup_start": self._backup_start,
            "backup_token_save": self._backup_token_save,
            "backup_token_delete": self._backup_token_delete,
            "archive_list": self._archive_list,
            "archive_detail": self._archive_detail,
            "archive_start": self._archive_start,
            "archive_retry": self._archive_retry,
            "archive_jobs": self._archive_jobs,
            "archive_job_events": self._archive_job_events,
            "restore_plan": self._restore_plan,
            "restore_execute": self._restore_execute,
            "archive_authorize_root": self._archive_authorize_root,
            "capabilities": self._capabilities,
            "course_capabilities": self._capabilities,
            "calendar": self._calendar,
            "calendar_events": self._calendar,
            "timetable_status": self._timetable_status,
            "timetable_schedule": self._timetable_schedule,
            "timetable_preview_local_file": self._timetable_preview,
            "timetable_commit_preview": self._timetable_commit,
            "timetable_load_bundled_sample": self._timetable_sample,
            "timetable_import_ics": self._timetable_import_ics,
            "gradebook": self._gradebook,
            "gradebook_export": self._gradebook_export,
            "roster": self._roster,
            "roster_export": self._roster_export,
            "academic_export_reveal": self._academic_export_reveal,
            "grading": self._grading,
            "grading_submissions": self._grading_submissions,
            "grading_submission": self._grading_submission,
            "grading_update": self._grading_update,
            "media": self._course_media,
            "course_media": self._course_media,
            "media_capabilities": self._media_capabilities,
            "ai_capabilities": self._ai_capabilities,
            "media_preview": self._media_preview,
            "video": self._video,
            "video_playback": self._video,
            "video_subtitles": self._video_subtitles,
            "video_slides_metadata": self._video_slides_metadata,
            "video_slide_image": self._video_slide_image,
            "video_slides_pdf": self._video_slides_pdf,
            "video_screenshot_pdf": self._video_screenshot_pdf,
            "transcript_batch_start": self._transcript_batch_start,
            "transcript_playback_prepare": self._transcript_playback_prepare,
            "transcript_playback_update": self._transcript_playback_update,
            "transcript_learning_segments": self._transcript_learning_segments,
            "transcript_batch_get": self._transcript_batch_get,
            "transcript_jobs": self._transcript_jobs,
            "transcript_retry": self._transcript_retry,
            "transcript_cancel": self._transcript_cancel,
            "transcript_artifacts": self._transcript_artifacts,
            "transcript_v2_artifacts": self._transcript_v2_artifacts,
            "transcript_artifact_read": self._transcript_artifact_read,
            "transcript_v2_artifact_read": self._transcript_v2_artifact_read,
            "transcript_artifact_reveal": self._transcript_artifact_reveal,
            "update": self._update_check,
            "update_check": self._update_check,
            "mcp": self._mcp_config,
            "mcp_config": self._mcp_config,
            "open_external": self._open_external,
            "assignments_list": self._assignments_list,
            "detail": self._assignment_detail,
            "can_submit": self._assignment_can_submit,
            "submit_text": self._assignment_submit_text,
            "submit_url": self._assignment_submit_url,
            "pick_local_file": self._assignment_pick_local_file,
            "register_local_file": self._assignment_register_local_file,
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
                "ai_chat_send_shortcut",
                "ai_reply_language",
                "ai_attachment_context_budget",
                "ai_auto_open_activity",
                "ai_code_line_numbers",
                "theme_mode",
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

    def _settings_credential_save(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"kind", "value", "account"})
        kind = _bounded_text(payload.get("kind"), limit=16, label="配置类型")
        value = _bounded_text(payload.get("value"), limit=4096, label="凭据")
        account = payload.get("account", "")
        if type(account) is not str or len(account) > 254:
            raise DashboardError("账号格式不正确。")
        return self._service.save_credential(kind, value, account)

    def _settings_credential_delete(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"kind", "account"})
        kind = _bounded_text(payload.get("kind"), limit=16, label="配置类型")
        account = payload.get("account", "")
        if type(account) is not str or len(account) > 254:
            raise DashboardError("账号格式不正确。")
        return self._service.delete_credential(kind, account)

    def _ai_chat(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"messages"})
        if "messages" not in payload:
            raise DashboardError("缺少 AI 对话消息。")
        return self._service.ai_chat(payload["messages"])

    def _ai_presets(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.ai_presets()

    def _ai_chat_sessions(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.ai_chat_sessions()

    def _chat_session_id(self, payload: Mapping[str, Any]) -> str:
        return _bounded_id(payload.get("session_id"), limit=36, label="对话标识")

    def _ai_chat_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"session_id"})
        return self._service.ai_chat_session(self._chat_session_id(payload))

    def _ai_chat_new(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"model", "thinking_depth", "preset_id"})
        return self._service.ai_chat_new(
            payload.get("model", "auto"),
            payload.get("thinking_depth", "standard"),
            _preset_id(payload["preset_id"]) if payload.get("preset_id") is not None else None,
        )

    def _ai_chat_send(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(
            payload,
            {
                "session_id",
                "content",
                "model",
                "thinking_depth",
                "preset_id",
                "attachment_ids",
            },
        )
        preset_id = payload.get("preset_id")
        if preset_id is not None:
            preset_id = _preset_id(preset_id)
        base_arguments = (
            self._chat_session_id(payload),
            payload.get("content"),
            payload.get("model", "auto"),
            payload.get("thinking_depth", "standard"),
            preset_id,
        )
        if "attachment_ids" not in payload:
            # Preserve the five-argument bridge contract for older service adapters.
            return self._service.ai_chat_send(*base_arguments)
        attachment_ids = payload["attachment_ids"]
        if type(attachment_ids) is not list or len(attachment_ids) > 20:
            raise DashboardError("附件标识列表无效。")
        normalized: list[int] = []
        for value in attachment_ids:
            attachment_id = _positive_id(value, "附件标识")
            if attachment_id not in normalized:
                normalized.append(attachment_id)
        return self._service.ai_chat_send(*base_arguments, normalized)

    def _ai_chat_stream_start(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(
            payload,
            {
                "session_id",
                "content",
                "model",
                "thinking_depth",
                "preset_id",
                "attachment_ids",
                "page_context",
            },
        )
        preset_id = payload.get("preset_id")
        if preset_id is not None:
            preset_id = _preset_id(preset_id)
        attachment_ids = payload.get("attachment_ids")
        if attachment_ids is not None:
            if type(attachment_ids) is not list or len(attachment_ids) > 20:
                raise DashboardError("附件标识列表无效。")
            attachment_ids = [
                _positive_id(value, "附件标识") for value in attachment_ids
            ]
        page_context = payload.get("page_context")
        if page_context is not None and type(page_context) is not dict:
            raise DashboardError("页面上下文格式无效。")
        return self._service.ai_chat_stream_start(
            self._chat_session_id(payload),
            payload.get("content"),
            payload.get("model", "auto"),
            payload.get("thinking_depth", "standard"),
            preset_id,
            attachment_ids,
            page_context,
        )

    def _ai_task_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"task_id", "after_sequence"})
        task_id = _bounded_id(payload.get("task_id"), limit=36, label="AI 任务标识")
        after_sequence = payload.get("after_sequence", 0)
        if type(after_sequence) is not int or after_sequence < 0:
            raise DashboardError("AI 任务事件游标无效。")
        return self._service.ai_task_status(task_id, after_sequence)

    def _ai_task_cancel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"task_id"})
        return self._service.ai_task_cancel(
            _bounded_id(payload.get("task_id"), limit=36, label="AI 任务标识")
        )

    def _ai_usage_summary(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"page_id"})
        page_id = payload.get("page_id")
        if page_id is not None:
            page_id = _bounded_id(page_id, limit=32, label="页面标识")
        return self._service.ai_usage_summary(page_id)

    def _ai_chat_delete(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"session_id"})
        return self._service.ai_chat_delete(self._chat_session_id(payload))

    def _knowledge_compiler_pick_folder(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.knowledge_compiler_pick_folder()

    def _knowledge_compiler_inspect(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _only_keys(payload, {"source_root"})
        source_root = _bounded_id(
            payload.get("source_root"), limit=4096, label="素材目录"
        )
        return self._service.knowledge_compiler_inspect(source_root)

    def _knowledge_compiler_start(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _only_keys(payload, {"source_root", "target_root", "mode"})
        source_root = _bounded_id(
            payload.get("source_root"), limit=4096, label="素材目录"
        )
        target_root = _bounded_id(
            payload.get("target_root"), limit=4096, label="输出目录"
        )
        mode = payload.get("mode")
        if type(mode) is not str or mode not in {"foundation", "full"}:
            raise DashboardError("编译范围不受支持。")
        return self._service.knowledge_compiler_start(
            source_root, target_root, mode
        )

    def _knowledge_compiler_status(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _only_keys(payload, {"target_root"})
        target_root = payload.get("target_root")
        if target_root is not None:
            target_root = _bounded_id(target_root, limit=4096, label="输出目录")
        return self._service.knowledge_compiler_status(target_root)

    def _knowledge_compiler_cancel(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.knowledge_compiler_cancel()

    def _local_projects_list(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.local_projects_list()

    def _local_projects_add(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _only_keys(payload, {"source_root", "target_root"})
        source_root = _bounded_id(
            payload.get("source_root"), limit=4096, label="素材目录"
        )
        target_root = _bounded_id(
            payload.get("target_root"), limit=4096, label="输出目录"
        )
        return self._service.local_projects_add(source_root, target_root)

    def _local_projects_refresh(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _only_keys(payload, {"project_id"})
        project_id = _bounded_id(
            payload.get("project_id"), limit=24, label="项目标识"
        )
        return self._service.local_projects_refresh(project_id)

    def _local_projects_remove(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        _only_keys(payload, {"project_id"})
        project_id = _bounded_id(
            payload.get("project_id"), limit=24, label="项目标识"
        )
        return self._service.local_projects_remove(project_id)

    def _ai_attachment_list(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"limit"})
        limit = payload.get("limit", 100)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise DashboardError("附件数量限制无效。")
        return self._service.ai_attachment_list(limit)

    def _ai_attachment_pick(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        if self._file_picker is None:
            raise DashboardError("当前环境不支持本地文件选择。")
        selected = self._file_picker()
        if not selected:
            return {"cancelled": True}
        try:
            selected = _validated_local_attachment_path(selected)
        except DashboardError:
            raise DashboardError("所选文件无效。") from None
        return {
            "cancelled": False,
            "attachment": self._service.ai_attachment_ingest(selected),
        }

    def _ai_attachment_ingest(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Ingest one dropped file without returning its original absolute path."""
        _only_keys(payload, {"path"})
        raw_path = _validated_local_attachment_path(payload.get("path"))
        return self._service.ai_attachment_ingest(raw_path)

    def _ai_attachment_id(self, payload: Mapping[str, Any]) -> int:
        _only_keys(payload, {"attachment_id"})
        return _positive_id(payload.get("attachment_id"), "附件标识")

    def _ai_attachment_restore(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._service.ai_attachment_restore(self._ai_attachment_id(payload))

    def _ai_attachment_reveal(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._service.ai_attachment_reveal(self._ai_attachment_id(payload))

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

    def _archive_list(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"limit", "offset", "query", "status", "sort", "cursor"})
        limit = payload.get("limit", 100)
        offset = payload.get("offset", 0)
        if type(limit) is not int or type(offset) is not int:
            raise DashboardError("归档列表分页参数无效。")
        values = {}
        for key, maximum in (("query", 256), ("status", 32), ("sort", 32), ("cursor", 128)):
            value = payload.get(key)
            if value is not None:
                if type(value) is not str or len(value) > maximum:
                    raise DashboardError("归档列表查询参数无效。")
                values[key] = value
        return self._service.archive_list(limit, offset, **values)

    def _archive_detail(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"entry_id"})
        return self._service.archive_detail(
            _bounded_id(payload.get("entry_id"), limit=64, label="归档条目标识")
        )

    def _archive_start(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"idempotency_key"})
        if self._file_picker is None:
            raise DashboardError("当前环境不支持选择归档文件。")
        selected = self._file_picker()
        if not selected:
            raise DashboardError("未选择归档文件。")
        key = payload.get("idempotency_key")
        if key is not None:
            key = _bounded_id(key, limit=128, label="幂等键")
        return self._service.archive_start(selected, key)

    def _archive_retry(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"job_id"})
        return self._service.archive_retry(
            _bounded_id(payload.get("job_id"), limit=64, label="任务标识")
        )

    def _archive_jobs(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"limit", "status"})
        limit = payload.get("limit", 100)
        if type(limit) is not int:
            raise DashboardError("任务列表分页参数无效。")
        status = payload.get("status")
        if status is not None:
            status = _bounded_id(status, limit=24, label="任务状态")
        return self._service.archive_jobs(limit, status)

    def _archive_job_events(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"job_id"})
        return self._service.archive_job_events(
            _bounded_id(payload.get("job_id"), limit=64, label="任务标识")
        )

    def _archive_authorize_root(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.archive_authorize_root()

    def _restore_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"entry_id", "version_id", "mode", "authorized_root_id"})
        entry_id = _bounded_id(payload.get("entry_id"), limit=64, label="归档条目标识")
        version_id = payload.get("version_id")
        root_id = payload.get("authorized_root_id")
        mode = payload.get("mode", "original")
        if version_id is not None:
            version_id = _bounded_id(version_id, limit=64, label="归档版本标识")
        if root_id is not None:
            root_id = _bounded_id(root_id, limit=64, label="授权根标识")
        if type(mode) is not str:
            raise DashboardError("恢复模式无效。")
        return self._service.restore_plan(entry_id, version_id, mode, root_id)

    def _restore_execute(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"job_id", "conflict_policy", "confirm_create_dirs"})
        job_id = _bounded_id(payload.get("job_id"), limit=64, label="任务标识")
        policy = _bounded_id(payload.get("conflict_policy"), limit=16, label="冲突策略")
        confirm = payload.get("confirm_create_dirs", False)
        if type(confirm) is not bool:
            raise DashboardError("目录创建确认参数无效。")
        return self._service.restore_execute(job_id, policy, confirm)

    def _backup(self) -> BackupManager:
        if self._backup_manager is None:
            raise BackupError("备份服务未配置。")
        return self._backup_manager

    def _backup_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._backup().status()

    def _backup_start(self, payload: Mapping[str, Any]) -> dict[str, str]:
        _only_keys(payload, {"remove_local"})
        if "remove_local" not in payload or type(payload["remove_local"]) is not bool:
            raise DashboardError("必须明确选择是否释放本地空间。")
        return self._backup().start(remove_local=payload["remove_local"])

    @staticmethod
    def _backup_token_save(payload: Mapping[str, Any]) -> dict[str, bool]:
        _only_keys(payload, {"token"})
        if "token" not in payload:
            raise DashboardError("缺少交大云盘 UserToken。")
        save_user_token(payload["token"])
        return {"configured": True}

    @staticmethod
    def _backup_token_delete(payload: Mapping[str, Any]) -> dict[str, bool]:
        _empty_payload(payload)
        delete_user_token()
        return {"configured": False}

    def _deadlines(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"window"})
        window = payload.get("window", "7d")
        hours = (
            {"24h": 24, "7d": 168, "14d": 336}.get(window)
            if type(window) is str
            else None
        )
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
        category = payload.get("category", "all")
        categories = {"all", "today", "upcoming", "overdue", "missing", "unsubmitted", "submitted", "pending_review", "graded"}
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

    def _register_assignment_local_file(self, value: object) -> dict[str, Any]:
        raw = _validated_local_attachment_path(value)
        path = Path(raw).expanduser().resolve()
        try:
            is_file = path.is_file()
            size = path.stat().st_size if is_file else -1
        except OSError:
            raise DashboardError("拖入文件无效或无法读取。") from None
        if not is_file or size < 0 or size > 2 * 1024**3:
            raise DashboardError("拖入文件无效或超过 2 GiB。")
        normalized = str(path)
        self._picked_files.add(normalized)
        return {"cancelled": False, "path": normalized, "name": path.name, "size": size}

    def _assignment_pick_local_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        if self._file_picker is None:
            raise LearningServiceError("当前环境不支持本地文件选择。")
        selected = self._file_picker()
        if not selected:
            return {"cancelled": True}
        try:
            return self._register_assignment_local_file(selected)
        except DashboardError:
            raise DashboardError("所选文件无效或超过 2 GiB。")

    def _assignment_register_local_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"path", "drop_token"})
        raw = _validated_local_attachment_path(payload.get("path"))
        normalized = str(Path(raw).expanduser().resolve())
        drop_token = _bounded_text(
            payload.get("drop_token"), limit=128, label="拖放凭证"
        )
        if not _consume_native_drop_capability(normalized, drop_token):
            raise DashboardError("拖入文件凭证无效或已过期，请重新拖入文件。")
        return self._register_assignment_local_file(normalized)

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

    @staticmethod
    def _canvas_ids(value: object, label: str, *, maximum: int = 500) -> list[int]:
        if type(value) is not list or not 1 <= len(value) <= maximum:
            raise DashboardError(f"{label}列表不正确。")
        result: list[int] = []
        for item in value:
            normalized = _positive_id(item, label)
            if normalized not in result:
                result.append(normalized)
        return result

    def _ai_capabilities(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"page_id"})
        page_id = payload.get("page_id")
        if page_id is not None and (
            type(page_id) is not str or not 1 <= len(page_id) <= 40
        ):
            raise DashboardError("AI 能力页面不正确。")
        return self._service.ai_capabilities(page_id)

    def _capabilities(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"course_id"})
        course_id = payload.get("course_id")
        if course_id is not None:
            course_id = _positive_id(course_id, "课程标识")
        return self._service.capabilities(course_id)

    def _calendar(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"year", "month", "course_ids"})
        year = payload.get("year")
        month = payload.get("month")
        if type(year) is not int or not 2000 <= year <= 2100:
            raise DashboardError("日历年份不正确。")
        if type(month) is not int or not 1 <= month <= 12:
            raise DashboardError("日历月份不正确。")
        course_ids = payload.get("course_ids")
        if course_ids is not None:
            course_ids = self._canvas_ids(course_ids, "课程标识")
        return self._service.calendar(year, month, course_ids=course_ids)

    def _require_timetable(self) -> TimetableService:
        if self._timetable is None:
            raise DashboardError("课表服务不可用。")
        return self._timetable

    def _timetable_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._require_timetable().status()

    def _timetable_schedule(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"startAt", "endAt"})
        try:
            start = datetime.fromisoformat(_bounded_text(payload.get("startAt"), limit=64, label="开始时间").replace("Z", "+00:00"))
            end = datetime.fromisoformat(_bounded_text(payload.get("endAt"), limit=64, label="结束时间").replace("Z", "+00:00"))
        except ValueError as exc:
            raise DashboardError("课表时间格式无效。") from exc
        return self._require_timetable().schedule(start, end)

    def _timetable_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        if self._file_picker is None:
            raise DashboardError("当前环境不支持本地文件选择。")
        selected = self._file_picker()
        if not selected:
            return {"cancelled": True}
        return self._require_timetable().preview_local_file(selected)

    def _timetable_commit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"previewId"})
        return self._require_timetable().commit_preview(_bounded_id(payload.get("previewId"), limit=32, label="预览标识"))

    def _timetable_sample(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._require_timetable().load_bundled_sample()

    def _timetable_import_ics(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        if self._ics_file_picker is None:
            raise DashboardError("当前环境不支持本地文件选择。")
        selected = self._ics_file_picker()
        if not selected:
            return {"cancelled": True}
        return self._require_timetable().import_ics_local_file(selected)

    def _gradebook(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"course_id"})
        return self._service.gradebook(_positive_id(payload.get("course_id"), "课程标识"))

    def _gradebook_export(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"course_id"})
        return self._service.gradebook_export(
            _positive_id(payload.get("course_id"), "课程标识")
        )

    @staticmethod
    def _roster_options(payload: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        roles = payload.get("roles")
        allowed_roles = {"teacher", "ta", "student", "observer", "designer"}
        if roles is not None:
            if type(roles) is not list or not 1 <= len(roles) <= len(allowed_roles):
                raise DashboardError("课程角色筛选不正确。")
            if any(type(role) is not str or role not in allowed_roles for role in roles):
                raise DashboardError("课程角色筛选不正确。")
            result["roles"] = list(dict.fromkeys(roles))
        query = payload.get("query")
        if query is not None:
            result["query"] = _bounded_text(
                query, limit=160, label="成员搜索词", allow_empty=True
            )
        return result

    def _roster(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"course_id", "roles", "query"})
        return self._service.roster(
            _positive_id(payload.get("course_id"), "课程标识"),
            **self._roster_options(payload),
        )

    def _roster_export(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"course_id", "user_ids"})
        user_ids = payload.get("user_ids")
        if user_ids is not None:
            user_ids = self._canvas_ids(user_ids, "用户标识")
        return self._service.roster_export(
            _positive_id(payload.get("course_id"), "课程标识"), user_ids=user_ids
        )

    def _academic_export_reveal(self, payload: Mapping[str, Any]) -> dict[str, str]:
        _only_keys(payload, {"token"})
        token = _bounded_id(payload.get("token"), limit=255, label="导出文件标识")
        return self._service.reveal_academic_export(token)

    @staticmethod
    def _grading_ids(payload: Mapping[str, Any], extra: set[str]) -> tuple[int, int, int | None]:
        _only_keys(payload, {"course_id", "assignment_id", "student_id"} | extra)
        course_id = _positive_id(payload.get("course_id"), "课程标识")
        assignment_id = _positive_id(payload.get("assignment_id"), "作业标识")
        student_id = payload.get("student_id")
        return (
            course_id,
            assignment_id,
            _positive_id(student_id, "学生标识") if student_id is not None else None,
        )

    def _grading_submissions(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id, student_id = self._grading_ids(payload, set())
        if student_id is not None:
            raise DashboardError("提交列表不接受学生标识。")
        return self._service.grading_submissions(course_id, assignment_id)

    def _grading(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id, student_id = self._grading_ids(payload, set())
        return self._service.grading(course_id, assignment_id, student_id)

    def _grading_submission(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id, student_id = self._grading_ids(payload, set())
        if student_id is None:
            raise DashboardError("缺少学生标识。")
        return self._service.grading_submission(course_id, assignment_id, student_id)

    def _grading_update(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        course_id, assignment_id, student_id = self._grading_ids(
            payload, {"grade", "comment"}
        )
        if student_id is None:
            raise DashboardError("缺少学生标识。")
        has_grade = "grade" in payload
        has_comment = "comment" in payload
        if not has_grade and not has_comment:
            raise DashboardError("评分和评论不能同时为空。")
        grade = payload.get("grade")
        if has_grade:
            if grade is not None and type(grade) not in {str, int, float}:
                raise DashboardError("评分格式不正确。")
            if type(grade) is float and not isfinite(grade):
                raise DashboardError("评分格式不正确。")
            if type(grade) is str:
                grade = _bounded_text(grade, limit=64, label="评分", allow_empty=True)
        comment = payload.get("comment")
        if has_comment:
            comment = _bounded_text(comment, limit=10_000, label="评论")
        return self._service.grading_update(
            course_id,
            assignment_id,
            student_id,
            grade=grade,
            comment=comment,
            set_grade=has_grade,
        )

    def _course_media(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"course_id", "limit"})
        limit = payload.get("limit", 5000)
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise DashboardError("媒体数量限制不正确。")
        return self._service.course_media(
            _positive_id(payload.get("course_id"), "课程标识"), limit=limit
        )

    def _media_capabilities(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.media_capabilities()

    def _media_preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"source_id"})
        return self._service.media_preview(
            _bounded_id(payload.get("source_id"), limit=255, label="媒体标识")
        )

    def _video(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"source_id"})
        return self._service.video(
            _bounded_id(payload.get("source_id"), limit=255, label="媒体标识")
        )

    def _video_subtitles(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"source_id"})
        source_id = _bounded_id(
            payload.get("source_id"), limit=255, label="远程视频标识"
        )
        if re.fullmatch(r"sjtu-video:[1-9][0-9]{0,18}:[1-9][0-9]{0,18}", source_id) is None:
            raise DashboardError("远程视频标识不正确。")
        return self._service.video_subtitles(source_id)

    def _transcript_batch_start(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"course_id", "source_ids"})
        course_id = _positive_id(payload.get("course_id"), "课程标识")
        raw_source_ids = payload.get("source_ids")
        if type(raw_source_ids) is not list or not 1 <= len(raw_source_ids) <= 100:
            raise DashboardError("录像列表不正确。")
        source_ids: list[str] = list()
        for value in raw_source_ids:
            source_id = _bounded_id(value, limit=255, label="远程视频标识")
            match = re.fullmatch(
                r"sjtu-video:([1-9][0-9]{0,18}):[1-9][0-9]{0,18}", source_id
            )
            if match is None or int(match.group(1)) != course_id:
                raise DashboardError("所选录像不属于当前课程。")
            if source_id in source_ids:
                raise DashboardError("录像列表包含重复项目。")
            source_ids.append(source_id)
        return self._service.transcript_batch_start(course_id, source_ids)

    def _transcript_batch_get(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"batch_id"})
        return self._service.transcript_batch_get(
            _transcript_id(payload.get("batch_id"), "字幕批次标识")
        )

    def _playback_payload(self, payload, *, timed=True, with_seq=False):
        keys = {"course_id", "source_id"}
        if timed:
            keys |= {"position_seconds", "playback_rate"}
        if with_seq:
            keys.add("seq")
        _only_keys(payload, keys)
        course_id = _positive_id(payload.get("course_id"), "课程标识")
        source_id = _bounded_id(payload.get("source_id"), limit=255, label="远程视频标识")
        if re.fullmatch(r"sjtu-video:[1-9][0-9]{0,18}:[1-9][0-9]{0,18}", source_id) is None:
            raise DashboardError("远程视频标识不正确。")
        if not timed:
            return course_id, source_id
        position, rate = payload.get("position_seconds", 0), payload.get("playback_rate", 1)
        for value, minimum, maximum in ((position, 0, 86400), (rate, 0.25, 4)):
            if type(value) not in (int, float) or not isfinite(value) or not minimum <= value <= maximum:
                raise DashboardError("视频播放位置或速度不正确。")
        if with_seq:
            seq = payload.get("seq")
            if type(seq) is not int or not 0 <= seq <= 9_007_199_254_740_991:
                raise DashboardError("播放更新序号不正确。")
            return course_id, source_id, position, rate, seq
        return course_id, source_id, position, rate

    def _transcript_playback_prepare(self, payload):
        return self._service.transcript_playback_prepare(*self._playback_payload(payload))

    def _transcript_playback_update(self, payload):
        return self._service.transcript_playback_update(*self._playback_payload(payload, with_seq=True))

    def _transcript_learning_segments(self, payload):
        return self._service.transcript_learning_segments(*self._playback_payload(payload, timed=False))

    def _transcript_jobs(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"course_id"})
        course_id = payload.get("course_id")
        if course_id is not None:
            course_id = _positive_id(course_id, "课程标识")
        return self._service.transcript_jobs(course_id)

    def _transcript_retry(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"job_id"})
        return self._service.transcript_retry(
            _transcript_id(payload.get("job_id"), "字幕任务标识")
        )

    def _transcript_cancel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"batch_id", "job_id"})
        has_batch = "batch_id" in payload
        has_job = "job_id" in payload
        if has_batch == has_job:
            raise DashboardError("必须且只能指定一个待取消任务。")
        return self._service.transcript_cancel(
            batch_id=_transcript_id(payload.get("batch_id"), "字幕批次标识") if has_batch else None,
            job_id=_transcript_id(payload.get("job_id"), "字幕任务标识") if has_job else None,
        )

    def _transcript_artifacts(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"job_id"})
        return self._service.transcript_artifacts(
            _transcript_id(payload.get("job_id"), "字幕任务标识")
        )

    def _transcript_v2_artifacts(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"job_id"})
        return self._service.transcript_v2_artifacts(
            _transcript_id(payload.get("job_id"), "字幕任务标识")
        )

    def _transcript_artifact_read(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"artifact_id"})
        return self._service.transcript_artifact_read(_artifact_id(payload.get("artifact_id")))

    def _transcript_v2_artifact_read(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"artifact_id"})
        return self._service.transcript_v2_artifact_read(
            _v2_artifact_id(payload.get("artifact_id"))
        )

    def _transcript_artifact_reveal(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"artifact_id"})
        return self._service.transcript_artifact_reveal(_artifact_id(payload.get("artifact_id")))

    def _video_slides_pdf(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"source_id"})
        source_id = _bounded_id(
            payload.get("source_id"), limit=255, label="远程视频标识"
        )
        if re.fullmatch(r"sjtu-video:[1-9][0-9]{0,18}:[1-9][0-9]{0,18}", source_id) is None:
            raise DashboardError("远程视频标识不正确。")
        return self._service.video_slides_pdf(source_id)

    def _video_slides_metadata(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"source_id"})
        source_id = _bounded_id(
            payload.get("source_id"), limit=255, label="远程视频标识"
        )
        if re.fullmatch(r"sjtu-video:[1-9][0-9]{0,18}:[1-9][0-9]{0,18}", source_id) is None:
            raise DashboardError("远程视频标识不正确。")
        return self._service.video_slides_metadata(source_id)

    def _video_slide_image(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"source_id", "page_index"})
        source_id = _bounded_id(
            payload.get("source_id"), limit=255, label="远程视频标识"
        )
        page_index = payload.get("page_index")
        if re.fullmatch(r"sjtu-video:[1-9][0-9]{0,18}:[1-9][0-9]{0,18}", source_id) is None:
            raise DashboardError("远程视频标识不正确。")
        if type(page_index) is not int or not 0 <= page_index < 200:
            raise DashboardError("课件页码不正确。")
        return self._service.video_slide_image(source_id, page_index)

    def _video_screenshot_pdf(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _only_keys(payload, {"source_id", "interval_seconds"})
        interval = payload.get("interval_seconds", 60)
        if type(interval) is not int or not 1 <= interval <= 3600:
            raise DashboardError("截图间隔必须在 1 到 3600 秒之间。")
        return self._service.video_screenshot_pdf(
            _bounded_id(payload.get("source_id"), limit=255, label="媒体标识"),
            interval,
        )

    def _debug_bundle_export(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        if self._save_file_picker is None or self._diagnostic_bundle is None:
            raise DiagnosticBundleError("当前环境不支持导出调试包。")
        destination = self._save_file_picker(default_bundle_filename())
        if not destination:
            return dict(status="cancelled")
        return self._diagnostic_bundle.export(destination)

    def _update_check(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        # No URL, path, asset or install flag is accepted from the renderer.
        _empty_payload(payload)
        return self._service.update_check()

    def _mcp_config(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _empty_payload(payload)
        return self._service.mcp_config()

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
            AcademicFeatureError,
            ArchiveError,
            BackupError,
            CloudArchiveError,
            RestoreError,
            MediaFeatureError,
            UpdateServiceError,
            DiagnosticBundleError,
            SettingsError,
            LearningServiceError,
            AssignmentServiceError,
            CanvasError,
            CloudStorageError,
            TimetableError,
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


def _notify_database_recovery(engine, *, sender=None) -> None:
    recovery = database_recovery_result(engine)
    if recovery is None or not recovery.requires_notice:
        return
    message = recovery.user_message()
    if sys.stderr is not None:
        print(f"警告：{message}", file=sys.stderr)
    try:
        (sender or default_notification_sender()).send(
            "SJTU Learning Assistant",
            "本地数据库已自动恢复",
            message,
        )
    except NotificationError:
        pass


def _enable_macos_window_material(window: object) -> None:
    """Restore native shadow and a unified titlebar over pywebview vibrancy.

    Keep a faint dynamic window background instead of a fully clear NSWindow.
    A clear window can disappear from the macOS compositor after switching
    between Aqua and Dark Aqua while its WebKit view remains alive.
    """
    if sys.platform != "darwin" or window is None:
        return
    try:
        import AppKit  # type: ignore[import-not-found]
        from PyObjCTools import AppHelper  # type: ignore[import-not-found]
    except ImportError:
        return

    def apply_material() -> None:
        try:
            native = getattr(window, "native", None)
            if native is None:
                return
            native.setOpaque_(False)
            material_base = AppKit.NSColor.windowBackgroundColor()
            native.setBackgroundColor_(material_base.colorWithAlphaComponent_(0.08))
            native.setHasShadow_(True)
            native.setTitlebarAppearsTransparent_(True)
            native.setTitleVisibility_(AppKit.NSWindowTitleHidden)
            native.setMovableByWindowBackground_(False)
        except (AttributeError, RuntimeError):
            # Visual polish must never block the application from starting.
            return

    AppHelper.callAfter(apply_material)


NATIVE_FILE_DROP_EVENT = "sjtu-native-file-drop"
NATIVE_DROP_CAPABILITY_TTL_SECONDS = 15.0
_MACOS_NATIVE_DROP_HOOK_INSTALLED = False
_NATIVE_DROP_CAPABILITY_LOCK = threading.Lock()
_NATIVE_DROP_CAPABILITIES: dict[str, tuple[str, float]] = {}


def _authorize_native_drop_paths(paths: tuple[str, ...]) -> list[dict[str, str]]:
    """Mint short-lived, single-use capabilities for OS-validated drop paths."""
    now = time.monotonic()
    result: list[dict[str, str]] = []
    with _NATIVE_DROP_CAPABILITY_LOCK:
        for token, (_path, expires_at) in tuple(_NATIVE_DROP_CAPABILITIES.items()):
            if expires_at < now:
                _NATIVE_DROP_CAPABILITIES.pop(token, None)
        for raw in paths:
            normalized = str(Path(raw).expanduser().resolve())
            token = secrets.token_urlsafe(32)
            _NATIVE_DROP_CAPABILITIES[token] = (
                normalized,
                now + NATIVE_DROP_CAPABILITY_TTL_SECONDS,
            )
            result.append({"path": normalized, "drop_token": token})
    return result


def _consume_native_drop_capability(path: str, token: str) -> bool:
    """Consume a capability only when it authorizes this exact local path."""
    now = time.monotonic()
    with _NATIVE_DROP_CAPABILITY_LOCK:
        authorization = _NATIVE_DROP_CAPABILITIES.pop(token, None)
    if authorization is None:
        return False
    authorized_path, expires_at = authorization
    return expires_at >= now and authorized_path == path


def _macos_dragged_file_paths(
    sender: object,
    *,
    url_class: object,
    file_urls_only_key: object,
    true_value: object = True,
) -> tuple[str, ...]:
    """Extract regular files straight from the native Finder pasteboard."""
    try:
        if sender.draggingSource() is not None:
            return ()
        pasteboard = sender.draggingPasteboard()
        urls = pasteboard.readObjectsForClasses_options_(
            [url_class], {file_urls_only_key: true_value}
        ) or []
    except (AttributeError, RuntimeError):
        return ()
    result: list[str] = []
    for url in urls[:20]:
        try:
            absolute = str(url.filePathURL().absoluteString())
        except (AttributeError, RuntimeError):
            continue
        parsed = urlparse(absolute)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            continue
        path = Path(unquote(parsed.path))
        try:
            is_file = path.is_file()
        except OSError:
            continue
        if is_file and str(path) not in result:
            result.append(str(path))
    return tuple(result)


def _install_macos_native_file_drop_hook() -> bool:
    """Dispatch Finder paths from WKWebView before pywebview joins by filename."""
    global _MACOS_NATIVE_DROP_HOOK_INSTALLED
    if sys.platform != "darwin":
        return False
    if _MACOS_NATIVE_DROP_HOOK_INSTALLED:
        return True
    try:
        import AppKit  # type: ignore[import-not-found]
        from webview.platforms.cocoa import BrowserView  # type: ignore[import-not-found]

        host_class = BrowserView.WebKitHost
        original = host_class.performDragOperation_
    except (ImportError, AttributeError, RuntimeError):
        return False

    def perform_drag_operation(host: object, sender: object) -> object:
        paths = _macos_dragged_file_paths(
            sender,
            url_class=AppKit.NSURL,
            file_urls_only_key=AppKit.NSPasteboardURLReadingFileURLsOnlyKey,
            true_value=AppKit.NSNumber.numberWithBool_(True),
        )
        result = original(host, sender)
        if paths:
            event_name = json.dumps(NATIVE_FILE_DROP_EVENT)
            files = _authorize_native_drop_paths(paths)
            detail = json.dumps({"files": files}, ensure_ascii=False)
            script = (
                "window.setTimeout(function(){window.dispatchEvent(new CustomEvent("
                f"{event_name}, {{detail: {detail}}}));}}, 0);"
            )
            try:
                host.evaluateJavaScript_completionHandler_(script, None)
            except (AttributeError, RuntimeError):
                pass
        return result

    try:
        host_class.performDragOperation_ = perform_drag_operation
    except (AttributeError, RuntimeError, TypeError):
        return False
    _MACOS_NATIVE_DROP_HOOK_INSTALLED = True
    return True


def _native_file_drop_paths(
    event: object,
    pending_native_paths: object = (),
) -> tuple[str, ...]:
    """Read OS-validated paths populated by pywebview's native drop handler."""
    result: list[str] = []

    def add_path(value: object) -> None:
        if len(result) >= 20:
            return
        if type(value) is not str or not value or "\x00" in value:
            return
        path = Path(value)
        try:
            is_file = path.is_file()
        except OSError:
            return
        if not is_file:
            return
        normalized = str(path)
        if normalized not in result:
            result.append(normalized)

    if isinstance(event, Mapping):
        transfer = event.get("dataTransfer")
        files = transfer.get("files") if isinstance(transfer, Mapping) else None
        if isinstance(files, list):
            for item in files[:20]:
                if isinstance(item, Mapping):
                    add_path(item.get("pywebviewFullPath"))

    # Cocoa records every Finder path before serializing the DOM event. Its
    # filename-based join can fail for downloaded files whose display name and
    # JavaScript File.name differ, so consume the remaining native queue too.
    if isinstance(pending_native_paths, list):
        for item in pending_native_paths[:20]:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                add_path(item[1])
    return tuple(result)


def _install_native_file_drop_bridge(window: object) -> None:
    """Forward native file paths from pywebview DOM events to the React app."""
    try:
        loaded = getattr(getattr(window, "events", None), "loaded", None)
        if loaded is None or not loaded.wait(60):
            LOGGER.warning("文件拖放桥接等待页面加载超时；本次启动不启用拖放。")
            return
        document = window.dom.document
    except (AttributeError, RuntimeError):
        return

    try:
        window.evaluate_js(
            "document.addEventListener('dragover', event => event.preventDefault(), {capture: true});"
            "document.addEventListener('drop', event => event.preventDefault(), {capture: true});"
        )
    except Exception:
        LOGGER.warning("文件拖放默认导航保护安装失败。")
        return

    def forward_drop(event: object) -> None:
        pending_native_paths: object = ()
        dnd_state: dict[str, object] | None = None
        try:
            from webview.dom import _dnd_state  # type: ignore[import-not-found]

            if isinstance(_dnd_state, dict):
                dnd_state = _dnd_state
                pending_native_paths = _dnd_state.get("paths", ())
        except (ImportError, AttributeError):
            pass
        paths = _native_file_drop_paths(event, pending_native_paths)
        if dnd_state is not None:
            dnd_state["paths"] = []
        if not paths:
            return
        files = _authorize_native_drop_paths(paths)
        # AIChatView still consumes the historical paths contract, while the
        # assignment workspace requires the per-drop capability tokens.
        detail = json.dumps({"paths": paths, "files": files}, ensure_ascii=False)
        event_name = json.dumps(NATIVE_FILE_DROP_EVENT)
        try:
            window.evaluate_js(
                f"window.dispatchEvent(new CustomEvent({event_name}, {{detail: {detail}}}))"
            )
        except Exception:
            LOGGER.warning("文件拖放事件转发失败。")
            return

    try:
        document.events.drop += forward_drop
        # Keep both objects alive for the lifetime of the native window.
        setattr(window, "_sjtu_drop_document", document)
        setattr(window, "_sjtu_drop_handler", forward_drop)
    except (AttributeError, RuntimeError):
        return
    _promote_drop_listener_to_capture(window, document, forward_drop)


def _promote_drop_listener_to_capture(window: object, document: object, handler: object) -> None:
    """Run pywebview's document drop listener in the capture phase.

    pywebview registers DOM listeners in the bubble phase, and on WebView2 the
    native file paths are only posted from inside that listener. React 17+
    dispatches from the root container, so a drop zone calling
    ``event.stopPropagation()`` would otherwise stop the native path bridge.
    """
    handler_ids = getattr(document, "_event_handler_ids", None)
    handler_id = handler_ids.get(handler) if isinstance(handler_ids, dict) else None
    if type(handler_id) is not str or re.fullmatch(r"[A-Za-z0-9]{1,32}", handler_id) is None:
        return
    key = json.dumps(handler_id)
    try:
        window.evaluate_js(
            "(function(){var p=window.pywebview,h=p&&p._eventHandlers&&"
            f"p._eventHandlers[{key}];if(!h)return;"
            "document.removeEventListener('drop',h);"
            "document.addEventListener('drop',h,{capture:true});})();"
        )
    except Exception:
        LOGGER.warning("文件拖放监听切换到捕获阶段失败。")


def _initialize_desktop_window(window: object) -> None:
    _enable_macos_window_material(window)
    if not _MACOS_NATIVE_DROP_HOOK_INSTALLED:
        _install_native_file_drop_bridge(window)


def _report_startup_error(message: str) -> None:
    """Show a fatal startup error even in windowed builds without a console.

    PyInstaller windowed builds (console=False) set sys.stderr to None, so a
    plain print would exit silently with no window on Windows.
    """
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    if sys.platform == "win32" and (sys.stderr is None or getattr(sys, "frozen", False)):
        try:
            import ctypes

            # MB_OK | MB_ICONERROR
            ctypes.windll.user32.MessageBoxW(None, message, "SJTU 学习助手", 0x10)
        except (AttributeError, OSError):
            pass
    if sys.platform == "darwin" and (sys.stderr is None or getattr(sys, "frozen", False)):
        script = """
on run argv
    display dialog (item 1 of argv) with title "SJTU 学习助手" buttons {"退出"} default button "退出" with icon stop
end run
""".strip()
        try:
            subprocess.run(
                ["/usr/bin/osascript", "-e", script, "--", message],
                check=False,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def run_desktop_app() -> int:
    """Create the native window. Tests exercise components without calling this."""
    if not STATIC_INDEX.is_file():
        if getattr(sys, "frozen", False):
            _report_startup_error("错误：安装包缺少界面资源，请重新安装 SJTU 学习助手。")
        else:
            _report_startup_error("错误：前端尚未构建，请先运行 cd dashboard-web && npm run build。")
        return 2
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        _report_startup_error("错误：缺少 pywebview，请重新安装依赖。")
        return 2

    _install_macos_native_file_drop_hook()
    try:
        engine = create_database_engine()
    except DatabaseConfigError as exc:
        _report_startup_error(f"错误：{safe_message(exc)}")
        return 2
    scheduler: DesktopScheduler | None = None
    service: DashboardService | None = None
    learning_service: DesktopLearningService | None = None
    backup_manager: BackupManager | None = None
    try:
        if engine.dialect.name == "sqlite":
            # Desktop startup must never migrate/import a real legacy database implicitly.
            try:
                initialize_desktop_database(engine, skip_import=True)
            except DesktopDatabaseError as exc:
                _report_startup_error(f"错误：{safe_message(exc)}")
                return 2
            _notify_database_recovery(engine)
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

        def pick_ics_file() -> str | None:
            result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=("ICS 日历文件 (*.ics)",),
            )
            if not result:
                return None
            return str(result[0] if isinstance(result, (list, tuple)) else result)

        def save_file(default_name: str) -> str | None:
            result = webview.windows[0].create_file_dialog(
                webview.SAVE_DIALOG, save_filename=default_name
            )
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
        diagnostic_bundle = DiagnosticBundleService(
            engine,
            config_status_provider=getattr(service, "settings_status", lambda: {}),
            status_provider=getattr(service, "sync_status", lambda: {}),
        )
        bridge = DesktopBridge(
            service,
            learning_service,
            file_picker=pick_file,
            ics_file_picker=pick_ics_file,
            save_file_picker=save_file,
            backup_manager=backup_manager,
            diagnostic_bundle=diagnostic_bundle,
            timetable_service=TimetableService(
                engine, PROJECT_ROOT / "resources" / "samples" / "sjtu_lessons_anonymous.json"
            ),
        )
        scheduler = DesktopScheduler(service.trigger_sync)
        native_vibrancy = sys.platform == "darwin"
        window = webview.create_window(
            "SJTU 学习助手",
            STATIC_INDEX.as_uri(),
            js_api=bridge,
            width=1280,
            height=820,
            min_size=(960, 640),
            background_color="#EAF1FB",
            transparent=native_vibrancy,
            vibrancy=native_vibrancy,
        )
        scheduler.start()
        start_options = {
            "debug": False,
            "private_mode": False,
            "storage_path": str(APP_SUPPORT_DIR / "webview"),
        }
        try:
            webview.start(_initialize_desktop_window, (window,), **start_options)
        except Exception as exc:  # noqa: BLE001 - surface GUI backend failures in windowed builds
            hint = (
                "请安装或修复 Microsoft Edge WebView2 Runtime 后重试。"
                if sys.platform == "win32"
                else "请重新安装 SJTU 学习助手。"
            )
            _report_startup_error(
                f"错误：无法启动界面（{type(exc).__name__}: {safe_message(exc)}）。{hint}"
            )
            return 2
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
    """Dispatch frozen helper modes without opening a desktop window."""
    if getattr(sys, "frozen", False) and sys.argv[1:2] == ["--mcp-stdio"]:
        from sjtu_learning_assistant.agent_runtime import ReadOnlyToolRegistry
        from sjtu_learning_assistant.mcp_server import run_stdio_server

        if sys.stdin is None or sys.stdout is None:
            _report_startup_error("错误：MCP stdio 模式需要可用的标准输入和标准输出。")
            return 2
        for stream in (sys.stdin, sys.stdout):
            # MCP stdio 要求 UTF-8 + LF；Windows 无控制台时默认是本地代码页（如 cp936）。
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", newline="\n")
        engine = create_database_engine()
        try:
            run_stdio_server(ReadOnlyToolRegistry(engine))
        finally:
            engine.dispose()
        return 0
    if getattr(sys, "frozen", False) and sys.argv[1:2] == ["--background-sync"]:
        from sync_data_to_db import main as sync_main

        return sync_main(sys.argv[2:])
    return run_desktop_app()


if __name__ == "__main__":
    raise SystemExit(main())
