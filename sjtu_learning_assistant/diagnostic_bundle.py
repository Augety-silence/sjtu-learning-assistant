"""Privacy-first one-click diagnostic bundle export."""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from sjtu_learning_assistant import __version__

try:
    from sync_runner import DEFAULT_JSONL_PATH
except ImportError:  # pragma: no cover - packaged import fallback
    DEFAULT_JSONL_PATH = (
        Path.home()
        / "Library"
        / "Application Support"
        / "sjtu-learning-assistant"
        / "logs"
        / "sync.jsonl"
    )

ZIP_ENTRY_ALLOWLIST = frozenset(
    {
        "manifest.json",
        "app_info.json",
        "status/summary.json",
        "logs/sync.jsonl",
    }
)
REQUIRED_ENTRIES = frozenset(
    {"manifest.json", "app_info.json", "status/summary.json"}
)
MAX_ENTRY_BYTES = 256 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 768 * 1024
MAX_ZIP_BYTES = 1024 * 1024
MAX_SANITIZE_INPUT_CHARS = 2 * 1024 * 1024

_URL_PATTERN = re.compile(r"(?i:https?)://[^\s<>\"']+")
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b([a-z][a-z0-9+.%]*://)[^\s/:]+:[^\s@]+@"
)
_BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)[a-z0-9_.~+/=-]+")
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)\b(authorization|proxy-authorization|token|access_token|refresh_token|"
    r"password|passwd|secret|api[_ -]?key|cookie|set-cookie|database_url)\b[\"']?"
    r"\s*[:=]\s*(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;}]+)"
)
_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I
)
_MASKED_EMAIL_PATTERN = re.compile(r"\[ph_EMAIL_[0-9]+_ph\]")
_MAC_USER_PATH = re.compile(r"/Users/[^/\s\"'<>]+(?:/[^\s\"'<>]*)?")
_LINUX_USER_PATH = re.compile(r"/home/[^/\s\"'<>]+(?:/[^\s\"'<>]*)?")
_WINDOWS_USER_PATH = re.compile(
    r"(?i)(?:\\\\\?\\)?[A-Z]:\\Users\\[^\\\r\n\t\"'<>]+"
    r"(?:\\[^\r\n\t\"'<>]*)?"
)
_JWT_PATTERN = re.compile(
    r"\beyJ[a-z0-9_-]{20,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\b", re.I
)
_KNOWN_TOKEN_PATTERN = re.compile(
    r"\b(?:sk[-_][a-z0-9_-]{8,}|ghp_[a-z0-9]{10,}|gho_[a-z0-9]{10,})\b",
    re.I,
)


class DiagnosticBundleError(RuntimeError):
    """Safe, user-visible diagnostic export error."""


def _sanitize_url(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    trailing = ""
    while raw_url and raw_url[-1] in ".,);]}":
        trailing = raw_url[-1] + trailing
        raw_url = raw_url[:-1]
    try:
        parsed = urlsplit(raw_url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
        netloc = host + port
        query = "[redacted]" if parsed.query else ""
        fragment = "[redacted]" if parsed.fragment else ""
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment)) + trailing
    except ValueError:
        return "[redacted_url]" + trailing


def sanitize_text(value: object, *, limit: int = MAX_ENTRY_BYTES) -> str:
    """Sanitize before bounding output so secrets cannot cross the cut point."""
    text_value = str(value)
    text_value = text_value[:MAX_SANITIZE_INPUT_CHARS]
    text_value = _URL_CREDENTIAL_PATTERN.sub(r"\1[redacted]@", text_value)
    text_value = _BEARER_PATTERN.sub(r"\1[redacted]", text_value)
    text_value = _URL_PATTERN.sub(_sanitize_url, text_value)
    text_value = _SECRET_VALUE_PATTERN.sub(
        lambda match: f"{match.group(1)}=[redacted]", text_value
    )
    text_value = _EMAIL_PATTERN.sub("[redacted_email]", text_value)
    text_value = _MASKED_EMAIL_PATTERN.sub("[redacted_email]", text_value)
    text_value = _MAC_USER_PATH.sub("/Users/[redacted]", text_value)
    text_value = _LINUX_USER_PATH.sub("/home/[redacted]", text_value)
    text_value = _WINDOWS_USER_PATH.sub(
        lambda _match: r"C:\Users\[redacted]", text_value
    )
    text_value = _JWT_PATTERN.sub("[redacted_token]", text_value)
    text_value = _KNOWN_TOKEN_PATTERN.sub("[redacted_token]", text_value)
    payload = text_value.encode("utf-8")[:limit]
    return payload.decode("utf-8", errors="ignore")


def safe_error_message(value: object) -> str:
    return sanitize_text(value, limit=400) or "无法生成调试包，请稍后重试。"


def default_bundle_filename(now: datetime | None = None) -> str:
    value = now or datetime.now().astimezone()
    return f"sjtu-learning-assistant-debug-{value:%Y%m%d_%H%M%S}.zip"


def _safe_status(value: object) -> str:
    status = str(value or "unknown").lower()
    return status if re.fullmatch(r"[a-z0-9_-]{1,32}", status) else "other"


def _error_category(value: object) -> str:
    error = str(value or "").lower()
    if any(word in error for word in ("401", "403", "auth", "token", "credential")):
        return "authentication"
    if any(word in error for word in ("timeout", "timed out")):
        return "timeout"
    if any(word in error for word in ("network", "connect", "dns", "ssl")):
        return "network"
    if any(word in error for word in ("permission", "denied", "forbidden")):
        return "permission"
    if any(word in error for word in ("database", "sqlite", "postgres", "sql")):
        return "database"
    return "other"


class DiagnosticBundleService:
    """Create a strictly allowlisted, bounded and sanitized ZIP."""

    def __init__(
        self,
        engine: Any,
        *,
        config_status_provider: Callable[[], Mapping[str, Any]] | None = None,
        status_provider: Callable[[], Mapping[str, Any]] | None = None,
        log_path: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.engine = engine
        self.config_status_provider = config_status_provider
        self.status_provider = status_provider
        self.log_path = Path(log_path or DEFAULT_JSONL_PATH)
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _database_reachable(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def _app_info(self) -> dict[str, Any]:
        config: Mapping[str, Any] = {}
        config_status_available = self.config_status_provider is not None
        if self.config_status_provider is not None:
            try:
                config = dict(self.config_status_provider())
            except Exception:
                config_status_available = False
        dialect = getattr(getattr(self.engine, "dialect", None), "name", "unknown")
        return {
            "app_version": __version__,
            "generated_at": self.now_provider().astimezone(timezone.utc).isoformat(),
            "os": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
            "python": platform.python_version(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "database": {
                "dialect": sanitize_text(dialect, limit=32),
                "reachable": self._database_reachable(),
            },
            "config_status_available": config_status_available,
            "config": {
                "canvas_configured": bool(config.get("canvas_token_saved", False)),
                "mail_configured": bool(config.get("mail_password_saved", False)),
                "cloud_configured": bool(config.get("cloud_token_saved", False)),
                "ai_configured": bool(config.get("ai_key_saved", False)),
            },
        }

    def _database_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"tables": {}}
        try:
            table_names = set(inspect(self.engine).get_table_names())
        except Exception:
            return {"database_summary_available": False, "tables": {}}

        queries = {
            "sync_runs": (
                "SELECT status, error FROM sync_runs "
                "ORDER BY started_at DESC LIMIT 100"
            ),
            "archive_jobs": (
                "SELECT status, NULL AS error FROM archive_jobs "
                "ORDER BY created_at DESC LIMIT 100"
            ),
        }
        for table_name, query in queries.items():
            if table_name not in table_names:
                continue
            try:
                with self.engine.connect() as connection:
                    rows = connection.execute(text(query)).all()
            except SQLAlchemyError:
                continue
            status_counts = Counter(_safe_status(row.status) for row in rows)
            error_counts = Counter(
                _error_category(row.error) for row in rows if row.error
            )
            summary["tables"][table_name] = {
                "recent_count": len(rows),
                "status_counts": dict(sorted(status_counts.items())),
                "error_category_counts": dict(sorted(error_counts.items())),
            }
        summary["database_summary_available"] = True
        return summary

    def _runtime_status(self) -> dict[str, Any]:
        if self.status_provider is None:
            return {"available": False}
        try:
            status = dict(self.status_provider())
        except Exception:
            return {"available": False}
        return {
            "available": True,
            "running": status.get("status") == "syncing",
            "last_run_status": (
                _safe_status(status.get("last_run_status"))
                if status.get("last_run_status") is not None
                else None
            ),
        }

    def _read_log(self) -> str | None:
        if not self.log_path.is_file():
            return None
        try:
            with self.log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                read_size = min(size, MAX_ENTRY_BYTES * 2)
                handle.seek(max(0, size - read_size))
                payload = handle.read(read_size)
            if size > read_size:
                _, separator, payload = payload.partition(b"\n")
                if not separator:
                    payload = b""
            return sanitize_text(payload.decode("utf-8", errors="replace"))
        except OSError:
            return None

    @staticmethod
    def _json_bytes(value: Any) -> bytes:
        raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        return (sanitize_text(raw) + "\n").encode("utf-8")

    @staticmethod
    def _validate_entry(name: str, payload: bytes) -> None:
        if (
            name not in ZIP_ENTRY_ALLOWLIST
            or name.startswith("/")
            or ".." in Path(name).parts
        ):
            raise DiagnosticBundleError("调试包条目不符合安全要求。")
        if len(payload) > MAX_ENTRY_BYTES:
            raise DiagnosticBundleError("调试包单文件大小已超限。")

    def _build_entries(self) -> dict[str, bytes]:
        entries: dict[str, bytes] = {
            "app_info.json": self._json_bytes(self._app_info()),
            "status/summary.json": self._json_bytes(
                {
                    "runtime": self._runtime_status(),
                    "database": self._database_summary(),
                }
            ),
        }
        log = self._read_log()
        if log is not None:
            entries["logs/sync.jsonl"] = log.encode("utf-8")
        entries["manifest.json"] = self._json_bytes(
            {
                "schema_version": 1,
                "entries": sorted(
                    REQUIRED_ENTRIES
                    | ({"logs/sync.jsonl"} if log is not None else set())
                ),
                "limits": {
                    "per_entry_bytes": MAX_ENTRY_BYTES,
                    "total_uncompressed_bytes": MAX_TOTAL_UNCOMPRESSED_BYTES,
                    "zip_bytes": MAX_ZIP_BYTES,
                },
                "privacy": {
                    "sanitized": True,
                    "includes_credentials": False,
                    "includes_user_content": False,
                },
            }
        )
        for name, payload in entries.items():
            self._validate_entry(name, payload)
        if sum(len(payload) for payload in entries.values()) > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise DiagnosticBundleError("调试包内容已超限。")
        return entries

    def export(self, destination: str | Path) -> dict[str, Any]:
        destination_path = Path(destination)
        if destination_path.suffix.lower() != ".zip":
            destination_path = destination_path.with_name(destination_path.name + ".zip")
        if not destination_path.parent.is_dir():
            raise DiagnosticBundleError("保存位置不可用。")

        entries = self._build_entries()
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".sjtu-debug-",
                suffix=".tmp",
                dir=destination_path.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                if hasattr(os, "fchmod"):
                    os.fchmod(handle.fileno(), 0o600)
            with zipfile.ZipFile(
                temporary_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as zip_file:
                for name in sorted(entries):
                    zip_file.writestr(name, entries[name])
            if temporary_path.stat().st_size > MAX_ZIP_BYTES:
                raise DiagnosticBundleError("调试包压缩文件大小已超限。")
            os.replace(temporary_path, destination_path)
            temporary_path = None
            try:
                os.chmod(destination_path, 0o600)
            except OSError:
                pass
            return {
                "status": "created",
                "filename": destination_path.name,
                "size": destination_path.stat().st_size,
                "entry_count": len(entries),
            }
        except DiagnosticBundleError:
            raise
        except Exception as exc:
            raise DiagnosticBundleError("无法生成调试包。") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
