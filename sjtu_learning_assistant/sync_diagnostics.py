"""同步阶段诊断：仅记录固定元数据，不记录账号、凭据或服务端正文。"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from sjtu_learning_assistant.database import APP_SUPPORT_DIR

DEFAULT_STAGE_LOG_PATH = APP_SUPPORT_DIR / "logs" / "sync-stages.jsonl"
MAX_LOG_BYTES = 512 * 1024
_active: ContextVar[tuple[Path, str] | None] = ContextVar("sync_diagnostics", default=None)
_lock = threading.Lock()


def exception_metadata(exc: BaseException) -> dict:
    """保留类型与数值错误码；禁止序列化异常文本/参数/服务端响应。"""
    chain = []
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 4:
        seen.add(id(current))
        name = type(current).__name__
        item = {"type": name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) else "Exception"}
        for field in ("errno", "winerror", "verify_code", "status_code"):
            value = getattr(current, field, None)
            if field == "status_code" and value is None:
                value = getattr(getattr(current, "response", None), "status_code", None)
            if type(value) is int:
                item[field] = value
        chain.append(item)
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return {"exception_chain": chain}


def record_event(source: str, stage: str, event: str, **metadata: object) -> None:
    active = _active.get()
    if active is None:
        return
    path, run_id = active
    record = {
        "utc_time": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "source": source,
        "stage": stage,
        "event": event,
        **metadata,
    }
    _write_record(path, record)


def _write_record(path: Path, record: dict) -> None:
    try:
        payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.parent.is_symlink() or path.is_symlink():
                return
            if path.exists() and not path.is_file():
                return
            if os.name != "nt":
                path.parent.chmod(0o700)
            if path.exists() and path.stat().st_size + len(payload) > MAX_LOG_BYTES:
                # 单个上一轮尾部足以排障；不覆盖其他用户文件/链接。
                backup = path.with_name(path.name + ".1")
                if backup.is_symlink() or (backup.exists() and not backup.is_file()):
                    return
                os.replace(path, backup)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    return
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o600)
                if os.write(fd, payload) != len(payload):
                    raise OSError("诊断日志未完整写入")
            finally:
                os.close(fd)
    except (OSError, ValueError, TypeError):
        # 诊断日志不可写不能阻止真实同步。
        pass


@contextmanager
def diagnostic_run(path: Path | None = None, *, source: str = "sync"):
    token = _active.set((path or DEFAULT_STAGE_LOG_PATH, str(uuid.uuid4())))
    record_event(source, "run", "started")
    try:
        yield
    except BaseException as exc:
        record_event(source, "run", "failed", **exception_metadata(exc))
        raise
    finally:
        _active.reset(token)


@contextmanager
def diagnostic_stage(source: str, stage: str):
    started = time.monotonic()
    record_event(source, stage, "started")
    try:
        yield
    except BaseException as exc:
        record_event(source, stage, "failed", duration_ms=round((time.monotonic() - started) * 1000), **exception_metadata(exc))
        raise
    else:
        record_event(source, stage, "succeeded", duration_ms=round((time.monotonic() - started) * 1000))
