"""Privacy-first one-click diagnostic bundle export."""

from __future__ import annotations

import json
import os
import platform
import re
import stat
import sys
import tempfile
import zipfile
import certifi
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from sjtu_learning_assistant import __version__
from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.mail_sync import IMAP_HOST, IMAP_PORT, DEFAULT_TIMEOUT_SECONDS
from sjtu_learning_assistant.mail_client import DEFAULT_INITIAL_LIMIT
from sjtu_learning_assistant.sync_diagnostics import DEFAULT_STAGE_LOG_PATH

try:
    from sync_runner import DEFAULT_JSONL_PATH
except ImportError:  # pragma: no cover - packaged import fallback
    from sjtu_learning_assistant.database import APP_SUPPORT_DIR

    DEFAULT_JSONL_PATH = (
        APP_SUPPORT_DIR / "logs" / "sync.jsonl"
    )

ZIP_ENTRY_ALLOWLIST = frozenset(
    {
        "manifest.json",
        "app_info.json",
        "status/summary.json",
        "status/sync_details.json",
        "logs/sync.jsonl",
        "logs/sync-stages.jsonl",
        "logs/sync-stages.jsonl.1",
        "logs/desktop.log",
        "logs/desktop.log.1",
    }
)
REQUIRED_ENTRIES = frozenset(
    {"manifest.json", "app_info.json", "status/summary.json", "status/sync_details.json"}
)
MAX_ENTRY_BYTES = 256 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 768 * 1024
MAX_ZIP_BYTES = 1024 * 1024
MAX_SANITIZE_INPUT_CHARS = 2 * 1024 * 1024
MAX_LOG_EXPORT_BYTES = 96 * 1024

_URL_PATTERN = re.compile(r"(?i:https?)://[^\s<>\"']+")
# 用后顾断言锚定 scheme 起点、并限定 userinfo 长度：原先 `\b` + 无界量词在
# 长串无空白文本（如 "a.a.a..."、JWT 片段）上是 O(n^2)，2 MB 输入可卡死桥接线程。
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9+.%])([a-z][a-z0-9+.%]*://)[^\s/:]{0,256}:[^\s@]{1,512}@"
)
_BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)[a-z0-9_.~+/=-]+")
# 认证头的 Digest/自定义方案可能包含多个逗号分隔字段。通用键值规则只能安全
# 识别单个值，因此先将整段头值脱敏；JSON 中带引号的头值只替换该字符串，
# 普通日志头则替换到行尾，避免 realm/nonce/response 等后续字段泄漏。
# 线性复杂度要点：(1) 前瞻里 `(?<![ \t])` 只允许在空白段起点展开 `[ \t]+`，
# 否则懒惰量词每前进一格都重扫整段空白（O(n^2)）；(2) 终止条件用 `[\r\n]|$`
# 而非 `\r?$`：孤立的 \r 后接非换行字符时旧写法整体匹配失败，叠加前导
# `[ \t]*` 回溯后退化为 O(n^3)（约 1 KB 空白 + "\rx" 即需 12 s）。
_AUTHORIZATION_HEADER_PATTERN = re.compile(
    r"(?im)\b(authorization|proxy-authorization)\b[\"']?[ \t]*"
    r"(?::|=|%3d)[ \t]*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|"
    r"[^\r\n]*?(?=(?<![ \t])[ \t]+(?:authorization|proxy-authorization|token|"
    r"access_token|refresh_token|password|passwd|secret|api[_ -]?key|cookie|"
    r"set-cookie|database_url)\b[\"']?[ \t]*(?::|=|%3d)|[\r\n]|$))"
)
_SECRET_VALUE_PATTERN = re.compile(
    # 起点用后顾断言而非 \b：允许 client_secret / db_password 这类下划线前缀，
    # 同时阻止在 camelCase 中间（sessionToken 的 Token）重复起扫。关键字补齐
    # 云存储 STS 凭据（tmpSecretId/tmpSecretKey/sessionToken/SecurityToken/
    # AccessKeyId 等）与 sslpassword；每个起点只做常数长度的关键字匹配，仍为线性。
    r"(?i)(?<![a-z0-9])(authorization|proxy-authorization|token|access_token|"
    r"refresh_token|(?:ssl)?password|passwd|"
    r"(?:tmp[_-]?)?secret(?:[_-]?(?:key|id))?|"
    r"(?:x[_-]?(?:cos[_-]?)?)?(?:session|security)[_-]?token|"
    r"access[_-]?key(?:[_-]?(?:id|secret))?|"
    r"api[_ -]?key|cookie|set-cookie|database_url)\b[\"']?"
    # 认证头的值形如 "Basic <凭据>"：只吞掉方案名会把真正的凭据原样留下，
    # 因此无引号分支允许可选的认证方案前缀（起点受关键字锚定，仍为线性）。
    r"\s*(?::|=|%3d)\s*(\"[^\"\n]*\"|'[^'\n]*'|"
    r"(?:(?:basic|digest|bearer|token|negotiate|ntlm)[ \t]+)?[^\s,;}]+)"
)
# 后顾断言必须覆盖本地部分字符集的全部字符（含 %），否则 "%%%%..." 这类
# 输入每个位置都会重新起扫，2 MB 时退化为 O(n^2)。
_EMAIL_PATTERN = re.compile(
    r"(?<![\w.%+-])[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.I
)
_MASKED_EMAIL_PATTERN = re.compile(r"\[ph_EMAIL_[0-9]+_ph\]")
_MAC_USER_PATH = re.compile(r"/Users/[^/\s\"'<>]+(?:/[^\s\"'<>]*)?")
_LINUX_USER_PATH = re.compile(r"/home/[^/\s\"'<>]+(?:/[^\s\"'<>]*)?")
_WINDOWS_USER_PATH = re.compile(
    r"(?i)(?:\\{2,4}\?\\{1,2})?[A-Z]:\\{1,2}Users\\{1,2}[^\\\r\n\t\"'<>]+"
    r"(?:\\[^\r\n\t\"'<>]*)?"
)
# 用后顾断言代替前导 \b：\b 在 "-eyJ-eyJ..." 中每个 eyJ 前都成立，随后
# [a-z0-9_-]{20,} 扫到串尾再回溯，同样是 O(n^2)。保留 "-eyJ..."（单个连字符
# 前缀）仍可命中，但同一 token 字符串内部不再重复起扫。
_JWT_PATTERN = re.compile(
    r"(?<![a-z0-9_])(?<![a-z0-9_-]-)"
    r"eyJ[a-z0-9_-]{20,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\b",
    re.I,
)
_KNOWN_TOKEN_PATTERN = re.compile(
    r"\b(?:sk[-_][a-z0-9_-]{8,}|ghp_[a-z0-9]{10,}|gho_[a-z0-9]{10,})\b",
    re.I,
)


class DiagnosticBundleError(RuntimeError):
    """Safe, user-visible diagnostic export error."""


def _sanitize_url(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    # 用索引回退代替“切片 + 前置拼接”循环：后者每轮都复制整个字符串，
    # 在超长尾随标点（如 ")" * 400k）下退化为 O(n^2)，2 MB 输入可阻塞约百秒。
    cut = len(raw_url)
    while cut and raw_url[cut - 1] in ".,);]}":
        cut -= 1
    trailing = raw_url[cut:]
    raw_url = raw_url[:cut]
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
    text_value = _AUTHORIZATION_HEADER_PATTERN.sub(
        lambda match: f"{match.group(1)}=[redacted]", text_value
    )
    text_value = _SECRET_VALUE_PATTERN.sub(
        lambda match: f"{match.group(1)}=[redacted]", text_value
    )
    text_value = _EMAIL_PATTERN.sub("[redacted_email]", text_value)
    text_value = _MASKED_EMAIL_PATTERN.sub("[redacted_email]", text_value)
    text_value = _MAC_USER_PATH.sub("/Users/[redacted]", text_value)
    text_value = _LINUX_USER_PATH.sub("/home/[redacted]", text_value)
    text_value = _WINDOWS_USER_PATH.sub(
        lambda match: r"C:\\Users\\[redacted]"
        if "Users\\\\" in match.group(0)
        else r"C:\Users\[redacted]",
        text_value,
    )
    text_value = _JWT_PATTERN.sub("[redacted_token]", text_value)
    text_value = _KNOWN_TOKEN_PATTERN.sub("[redacted_token]", text_value)
    # 孤立代理（Windows 未配对 UTF-16 文件名、JSON "\udc80"、surrogateescape）
    # 不能编码为 UTF-8；严格编码会让 safe_message 在桥接层 except 分支内再抛
    # UnicodeEncodeError，把含原始异常文本的 traceback 交给 pywebview。
    payload = text_value.encode("utf-8", errors="replace")[:limit]
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
    if any(word in error for word in ("401", "403", "auth", "token", "credential", "密码", "凭据")):
        return "authentication"
    if any(word in error for word in ("timeout", "timed out", "超时")):
        return "timeout"
    if any(word in error for word in ("network", "connect", "dns", "ssl", "tls", "网络", "连接")):
        return "network"
    if any(word in error for word in ("permission", "denied", "forbidden")):
        return "permission"
    if any(word in error for word in ("database", "sqlite", "postgres", "sql")):
        return "database"
    return "other"


def _timestamp(value: object) -> str | None:
    result = str(value or "")
    return result if re.fullmatch(r"[0-9TtZz:.+ -]{10,40}", result) else None


def _source(value: object) -> str:
    return str(value) if value in ("canvas", "email", "sync") else "other"


def _safe_error_details(value: object) -> dict[str, str] | None:
    if not value:
        return None
    # 历史异常可能包含邮件/HTTP正文，不能只靠敏感词正则后原样导出。
    # 精确阶段和底层错误码由新 sync-stages 日志提供；旧数据仅导出分类。
    message = str(value)
    code = "unspecified"
    for needle, label in (
        ("尚未保存邮箱密码", "mail_credentials_missing"),
        ("无法读取系统凭据库", "credential_store_unavailable"),
        ("TLS", "tls_failure"),
        ("超时", "timeout"),
        ("IMAP 连接被中断", "imap_disconnected"),
        ("IMAP 登录", "imap_auth_or_protocol"),
        ("无法以只读方式打开 INBOX", "inbox_select_failed"),
        ("无法读取 INBOX UID", "inbox_search_failed"),
        ("邮件读取失败", "mail_fetch_failed"),
    ):
        if needle in message:
            code = label
            break
    return {"category": _error_category(message), "code": code}


class DiagnosticBundleService:
    """Create a strictly allowlisted, bounded and sanitized ZIP."""

    def __init__(
        self,
        engine: Any,
        *,
        config_status_provider: Callable[[], Mapping[str, Any]] | None = None,
        status_provider: Callable[[], Mapping[str, Any]] | None = None,
        log_path: Path | None = None,
        desktop_log_path: Path | None = None,
        stage_log_path: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.engine = engine
        self.config_status_provider = config_status_provider
        self.status_provider = status_provider
        self.log_path = Path(log_path or DEFAULT_JSONL_PATH)
        self.desktop_log_path = Path(desktop_log_path or APP_SUPPORT_DIR / "logs" / "desktop.log")
        self.stage_log_path = Path(stage_log_path or DEFAULT_STAGE_LOG_PATH)
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
            "runtime_environment": {
                "profile_override_set": bool(os.environ.get("SJTU_APP_SUPPORT_DIR")),
                "database_override_set": bool(os.environ.get("SJTU_DATABASE_URL")),
                "profile_exists": APP_SUPPORT_DIR.is_dir(),
                "profile_writable": os.access(APP_SUPPORT_DIR, os.W_OK),
                "executable_exists": Path(sys.executable).is_file(),
                "bundle_resources_exist": Path(getattr(sys, "_MEIPASS", Path(__file__).parent)).is_dir(),
                "credential_status_error": bool(config.get("credential_status_error")),
                "mail_account_configured": bool(config.get("mail_account")),
            },
            "mail_sync_contract": {
                "host": IMAP_HOST, "port": IMAP_PORT,
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                "folder": "INBOX", "initial_limit": DEFAULT_INITIAL_LIMIT,
                "history_auto_backfill": False,
                "credentials_are_per_os_user": True,
                "configured_does_not_prove_authentication": True,
                "bundled_ca_available": Path(certifi.where()).is_file(),
                "certificate_verification_required": True,
            },
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
            "last_run_at": _timestamp(status.get("last_run_at")),
            "last_success_at": _timestamp(status.get("last_success_at")),
            "last_canvas_success_at": _timestamp(status.get("last_canvas_success_at")),
            "last_run_status": (
                _safe_status(status.get("last_run_status"))
                if status.get("last_run_status") is not None
                else None
            ),
        }

    def _sync_details(self) -> dict[str, Any]:
        result: dict[str, Any] = {"recent_by_source": {}, "recent_failures": [], "states": [], "available": False}
        try:
            inspector = inspect(self.engine)
            tables = set(inspector.get_table_names())
            if "sync_runs" in tables:
                columns = {column["name"] for column in inspector.get_columns("sync_runs")}
                fields = [name for name in ("source", "resource", "status", "started_at", "finished_at", "fetched_count", "inserted_count", "updated_count", "error") if name in columns]
                if {"status", "started_at"} <= columns:
                    with self.engine.connect() as connection:
                        sources = ("canvas", "email", "sync") if "source" in columns else ("unknown",)
                        for source in sources:
                            where = " WHERE source = :source" if "source" in columns else ""
                            rows = connection.execute(text(f"SELECT {', '.join(fields)} FROM sync_runs{where} ORDER BY started_at DESC LIMIT 20"), {"source": source}).mappings()
                            result["recent_by_source"][source] = [self._safe_sync_row(row) for row in rows]
                        rows = connection.execute(text(f"SELECT {', '.join(fields)} FROM sync_runs WHERE status = 'failed' ORDER BY started_at DESC LIMIT 20")).mappings()
                        result["recent_failures"] = [self._safe_sync_row(row) for row in rows]
            if "sync_state" in tables:
                columns = {column["name"] for column in inspector.get_columns("sync_state")}
                fields = [name for name in ("source", "resource", "status", "last_sync_at", "last_success_at", "last_error") if name in columns]
                if {"source", "resource"} <= columns:
                    with self.engine.connect() as connection:
                        rows = connection.execute(text(f"SELECT {', '.join(fields)} FROM sync_state WHERE resource IN ('inbox', 'run') LIMIT 30")).mappings()
                        result["states"] = [self._safe_sync_row(row) for row in rows]
            result["available"] = True
        except SQLAlchemyError:
            pass
        return result

    @staticmethod
    def _safe_sync_row(row: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in row.items():
            if key == "source":
                result[key] = _source(value)
            elif key == "resource":
                result[key] = value if value in ("run", "inbox", "courses") else "other"
            elif key == "status":
                result[key] = _safe_status(value)
            elif key in ("error", "last_error"):
                result[key] = _safe_error_details(value)
            elif key.endswith("_count"):
                result[key] = value if type(value) is int and 0 <= value <= 10**9 else None
            else:
                result[key] = _timestamp(value)
        return result

    def _read_log(self, path: Path | None = None, *, structured: bool = False, desktop: bool = False) -> tuple[str | None, str]:
        path = path or self.log_path
        try:
            if path.is_symlink():
                return None, "unsafe_symlink"
            if not path.is_file():
                return None, "missing"
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    return None, "not_regular_file"
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                read_size = min(size, MAX_ENTRY_BYTES * 2)
                handle.seek(max(0, size - read_size))
                payload = handle.read(read_size)
            if size > read_size:
                _, separator, payload = payload.partition(b"\n")
                if not separator:
                    payload = b""
            # 先完整脱敏读取窗口，再保留“最新”的尾部；sanitize_text 的 limit
            # 从头部截断，直接套用会丢掉最近的日志行。
            decoded = payload.decode("utf-8", errors="replace")
            if desktop:
                decoded = self._desktop_metadata(decoded)
            elif structured:
                decoded = self._stage_metadata(decoded)
            sanitized = sanitize_text(
                decoded,
                limit=MAX_ENTRY_BYTES * 4,
            ).encode("utf-8")
            if len(sanitized) > MAX_LOG_EXPORT_BYTES:
                _, _, sanitized = sanitized[-MAX_LOG_EXPORT_BYTES:].partition(b"\n")
            return sanitized.decode("utf-8", errors="ignore"), "included" if sanitized else "empty_or_filtered"
        except OSError:
            return None, "unreadable"

    @staticmethod
    def _desktop_metadata(value: str) -> str:
        lines = []
        for line in value.splitlines():
            header = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) (WARNING|ERROR|CRITICAL) ([a-zA-Z_][a-zA-Z0-9_.]{0,100}):", line)
            frame = re.match(r'^\s+File "([^"\r\n]+)", line (\d+), in ([a-zA-Z_][a-zA-Z0-9_<>]{0,100})', line)
            exception = re.match(r"^([a-zA-Z_][a-zA-Z0-9_.]{0,100}(?:Error|Exception|Interrupt|Exit))(?::|$)", line)
            if header:
                lines.append(json.dumps({"time": header[1], "level": header[2], "logger": header[3], "message": "[omitted_for_privacy]"}))
            elif frame:
                name = frame[1].replace("\\", "/").rsplit("/", 1)[-1]
                # 只保留工程代码文件名，不保留用户附件路径/文件名。
                if re.fullmatch(r"[a-z_][a-z0-9_]{0,80}\.py", name):
                    lines.append(json.dumps({"file": name, "line": int(frame[2]), "function": frame[3]}))
            elif exception:
                lines.append(json.dumps({"exception_type": exception[1]}))
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _stage_metadata(value: str) -> str:
        lines = []
        numeric = {"duration_ms", "exit_code", "attempt", "selected", "incremental", "bootstrap_truncated", "fetched", "inserted", "updated"}
        for line in value.splitlines():
            try:
                row = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(row, dict):
                continue
            safe = {}
            for key in ("utc_time", "run_id", "source", "stage", "event"):
                item = row.get(key)
                if key == "utc_time":
                    safe[key] = _timestamp(item)
                elif key == "run_id":
                    safe[key] = item if isinstance(item, str) and re.fullmatch(r"[0-9a-f-]{36}", item) else None
                else:
                    safe[key] = item if isinstance(item, str) and re.fullmatch(r"[a-z_]{1,40}", item) else "other"
            for key in numeric:
                if type(row.get(key)) is int:
                    safe[key] = row[key]
            if "local_time" in row:
                safe["local_time"] = _timestamp(row["local_time"])
            chain = row.get("exception_chain")
            if isinstance(chain, list):
                safe["exception_chain"] = []
                for item in chain[:4]:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("type")
                    clean = {"type": name if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) else "Exception"}
                    clean.update({key: item[key] for key in ("errno", "winerror", "verify_code", "status_code") if type(item.get(key)) is int})
                    safe["exception_chain"].append(clean)
            lines.append(json.dumps(safe, ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")

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
            "status/sync_details.json": self._json_bytes(self._sync_details()),
        }
        log_status = {}
        for name, path, structured, desktop in (
            ("logs/sync.jsonl", self.log_path, True, False),
            ("logs/sync-stages.jsonl", self.stage_log_path, True, False),
            ("logs/sync-stages.jsonl.1", self.stage_log_path.with_name(self.stage_log_path.name + ".1"), True, False),
            ("logs/desktop.log", self.desktop_log_path, False, True),
            ("logs/desktop.log.1", self.desktop_log_path.with_name(self.desktop_log_path.name + ".1"), False, True),
        ):
            log, status = self._read_log(path, structured=structured, desktop=desktop)
            log_status[name] = status
            if log is not None:
                entries[name] = log.encode("utf-8")
        entries["manifest.json"] = self._json_bytes(
            {
                "schema_version": 2,
                "entries": sorted(set(entries) | {"manifest.json"}),
                "log_status": log_status,
                "log_policy": "desktop metadata and traceback locations only; no raw exception messages or user content",
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
