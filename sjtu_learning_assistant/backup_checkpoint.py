"""账号/目标/文件级同步断点，复用 SyncState；每条更新独立事务。"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .models import SyncState

SOURCE = "cloud-backup-file-v1"
REVALIDATE_AFTER = timedelta(days=7)
_ACTIVE_OWNERS: set[str] = set()
_OWNER_LOCK = threading.Lock()


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        # Windows 的 os.kill(pid, 0) 会终止进程，不能用于存活探测。
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # 权限不足按仍活动处理。
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class BackupCheckpoint:
    def __init__(self, engine, identity: str):
        self.engine = engine
        self.identity = identity
        self.owner = uuid.uuid4().hex
        self.records = {}
        self._resources = {}
        self._selected_keys = set()
        self._counts = Counter()
        with _OWNER_LOCK:
            _ACTIVE_OWNERS.add(self.owner)

    def close(self) -> None:
        with _OWNER_LOCK:
            _ACTIVE_OWNERS.discard(self.owner)

    def resource(self, candidate) -> str:
        key = (candidate.source, candidate.key, candidate.remote_path)
        if key in self._resources:
            return self._resources[key]
        value = json.dumps([self.identity, candidate.source, candidate.key, candidate.remote_path], ensure_ascii=False)
        result = hashlib.sha256(value.encode()).hexdigest()
        self._resources[key] = result
        return result

    def load(self, candidates) -> None:
        keys = [self.resource(candidate) for candidate in candidates]
        with Session(self.engine) as session:
            for start in range(0, len(keys), 400):
                rows = session.scalars(select(SyncState).where(SyncState.source == SOURCE, SyncState.resource.in_(keys[start:start + 400])))
                for row in rows:
                    try:
                        data = json.loads(row.cursor or "null")
                        if type(data) is dict and data.get("version") == 1:
                            self.records[row.resource] = dict(data, status=row.status, verified_at=row.last_success_at)
                    except (ValueError, TypeError, RecursionError):
                        pass
        self._selected_keys = set(keys)
        self._counts = Counter(self.records.get(key, {}).get("status", "pending") for key in keys)

    def _set_record(self, resource, record):
        if resource in self._selected_keys:
            self._counts[self.records.get(resource, {}).get("status", "pending")] -= 1
            self._counts[record["status"]] += 1
        self.records[resource] = record

    def completed(self, candidate, fingerprint: dict, *, full_verify: bool = False) -> bool:
        record = self.records.get(self.resource(candidate), {})
        at = record.get("verified_at")
        if at is not None and at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return bool(not full_verify and record.get("status") == "completed"
                    and record.get("fingerprint") == fingerprint and at
                    and datetime.now(timezone.utc) - at < REVALIDATE_AFTER)

    def claim(self, candidate, fingerprint: dict) -> bool:
        resource = self.resource(candidate)
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            # 两个 worker 同时发现新文件时，唯一约束及原子条件更新避免相互覆盖。
            if self.engine.dialect.name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert
            elif self.engine.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                raise RuntimeError("同步断点数据库不支持原子更新。")
            session.execute(insert(SyncState).values(source=SOURCE, resource=resource, status="pending").on_conflict_do_nothing(index_elements=["source", "resource"]))
            row = session.scalar(select(SyncState).where(SyncState.source == SOURCE, SyncState.resource == resource))
            old = {}
            try:
                old = json.loads(row.cursor or "null") or {}
            except (ValueError, TypeError, RecursionError):
                pass
            if type(old) is not dict:
                old = {}
            alive = False
            if row.status == "uploading":
                pid = old.get("pid")
                if pid == os.getpid():
                    with _OWNER_LOCK:
                        alive = old.get("owner") in _ACTIVE_OWNERS
                elif type(pid) is int and pid > 0:
                    alive = _process_alive(pid)
            data = dict(version=1, owner=self.owner, fingerprint=fingerprint,
                        pid=os.getpid(),
                        attempts=min(1000000, int(old.get("attempts", 0))) + 1,
                        remote_path=list(candidate.remote_path))
            changed = session.execute(update(SyncState).where(SyncState.id == row.id,
                or_(not alive, SyncState.status != "uploading"), SyncState.cursor == row.cursor
            ).values(status="uploading", cursor=json.dumps(data), last_sync_at=now, last_error=None))
            if changed.rowcount != 1:
                return False
            self._set_record(resource, dict(data, status="uploading"))
        return True

    def finish(self, candidate, status: str, *, remote_id=None, error: str | None = None) -> None:
        resource = self.resource(candidate)
        record = self.records.get(resource, {})
        if record.get("owner") != self.owner or record.get("status") != "uploading":
            return
        data = {key: value for key, value in record.items() if key not in {"status", "verified_at"}}
        previous = json.dumps(data)
        if type(remote_id) is str:
            data["remote_id"] = remote_id[:256]
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            values = dict(status=status, cursor=json.dumps(data), last_error=error)
            if status == "completed":
                values["last_success_at"] = now
            changed = session.execute(update(SyncState).where(SyncState.source == SOURCE,
                SyncState.resource == resource, SyncState.status == "uploading", SyncState.cursor == previous).values(**values))
            if changed.rowcount != 1:
                raise RuntimeError("同步断点已由其他任务更新。")
        self._set_record(resource, dict(data, status=status, verified_at=now if status == "completed" else None))

    def counts(self, candidates) -> dict:
        return dict(completed=self._counts["completed"], failed=self._counts["failed"],
                    pending=sum(count for state, count in self._counts.items() if state not in {"completed", "failed"}))
