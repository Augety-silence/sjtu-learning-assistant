#!/usr/bin/env python3
"""Portless pywebview desktop entry, allowlisted bridge, and in-app scheduler."""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from sqlalchemy.exc import SQLAlchemyError

from sjtu_learning_assistant.archive_service import ArchiveError, DEFAULT_ARCHIVE_ROOT
from sjtu_learning_assistant.dashboard_service import DashboardError, DashboardService
from sjtu_learning_assistant.database import create_database_engine
from sjtu_learning_assistant.desktop_database import initialize_desktop_database

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
    if not isinstance(payload, dict):
        raise DashboardError("请求参数格式不正确。")
    return payload


def _source_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("source_id")
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 255
        or any(ord(character) < 32 for character in value)
    ):
        raise DashboardError("文件标识不正确。")
    return value.strip()


class DesktopBridge:
    """Single pywebview API surface; actions are explicit and allowlisted."""

    def __init__(self, service: DashboardService) -> None:
        self._service = service
        self._handlers: dict[str, Callable[[Mapping[str, Any]], Any]] = {
            "health": lambda _: service.health(),
            "overview": lambda _: service.overview(),
            "deadlines": self._deadlines,
            "messages": self._messages,
            "material_tree": lambda _: service.material_tree(),
            "sync_status": lambda _: service.sync_status(),
            "sync_trigger": lambda _: service.trigger_sync(),
            "material_download": lambda payload: service.download_material(_source_id(payload)),
            "material_open": lambda payload: service.open_material(_source_id(payload)),
            "material_reveal": lambda payload: service.reveal_material(_source_id(payload)),
            "settings_status": lambda _: service.settings_status(),
            "open_external": self._open_external,
        }

    def _deadlines(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        window = payload.get("window", "7d")
        hours = {"24h": 24, "7d": 168, "14d": 336}.get(window)
        if hours is None:
            raise DashboardError("截止时间筛选条件无效。")
        return {"items": self._service.deadlines(hours)}

    def _messages(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        kind = payload.get("kind", "all")
        if kind not in {"all", "email", "announcement"}:
            raise DashboardError("消息筛选条件无效。")
        return {"items": self._service.messages(kind)}

    def _open_external(self, payload: Mapping[str, Any]) -> dict[str, str]:
        value = payload.get("url")
        if not isinstance(value, str):
            raise DashboardError("链接不正确。")
        return self._service.open_external(value)

    def invoke(self, action: str, payload: object = None) -> dict[str, Any]:
        """The only public Bridge method exposed to JavaScript."""
        try:
            handler = self._handlers.get(action)
            if handler is None:
                return {
                    "ok": False,
                    "error": {"code": "not_allowed", "message": "不支持的操作。"},
                }
            result = handler(_require_payload(payload))
            return {"ok": True, "data": result}
        except (DashboardError, ArchiveError) as exc:
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
    try:
        if engine.dialect.name == "sqlite":
            # Desktop startup must never migrate/import a real legacy database implicitly.
            initialize_desktop_database(engine, skip_import=True)
        service = DashboardService(engine, archive_root=DEFAULT_ARCHIVE_ROOT)
        bridge = DesktopBridge(service)
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
        engine.dispose()


def main() -> int:
    """Dispatch a frozen background sync without opening a second window."""
    if getattr(sys, "frozen", False) and sys.argv[1:] == ["--background-sync"]:
        from sync_data_to_db import main as sync_main

        return sync_main(["--canvas-only", "--no-download"])
    return run_desktop_app()


if __name__ == "__main__":
    raise SystemExit(main())
