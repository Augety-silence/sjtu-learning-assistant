"""同服务、同模型、同输入的编译响应复用；仅保存响应，不保存密钥或提示词。"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

_locks = [threading.RLock() for _ in range(64)]


class CompilationResponseCache:
    def __init__(self, path: Path, *, clock=time.time, ttl: int = 7 * 86400, limit: int = 256):
        self.path, self.clock, self.ttl, self.limit = path, clock, ttl, limit

    @staticmethod
    def identity(scope: tuple, system: str, content, max_tokens: int) -> str:
        # JSON 字段顺序不产生新身份；正文字符串本身不改写。
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, (dict, list)):
                    content = parsed
            except (ValueError, RecursionError):
                pass
        wire = json.dumps([scope, system, content, max_tokens, 'response-cache-v1'],
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(wire.encode()).hexdigest()

    @staticmethod
    def _valid(content: str) -> bool:
        try:
            return type(content) is str and len(content) <= 50000 and type(json.loads(content)) is dict
        except (ValueError, RecursionError):
            return False

    def _connect(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise OSError('编译缓存路径不安全。')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            os.chmod(self.path, 0o600)
            db.execute('CREATE TABLE IF NOT EXISTS responses (id TEXT PRIMARY KEY, content TEXT NOT NULL, sha TEXT NOT NULL, created REAL NOT NULL)')
        except BaseException:
            # Ownership transfers to the caller only after setup succeeds.
            db.close()
            raise
        return db

    def get(self, identity: str) -> str | None:
        try:
            with closing(self._connect()) as db:
                row = db.execute('SELECT content,sha,created FROM responses WHERE id=?', (identity,)).fetchone()
            if (row and row[2] > self.clock() - self.ttl and self._valid(row[0])
                    and hashlib.sha256(row[0].encode()).hexdigest() == row[1]):
                return row[0]
        except (OSError, sqlite3.Error, ValueError, TypeError):
            pass  # 缓存损坏或不可用不阻塞原有编译及逐稿校验。
        return None

    def store(self, identity: str, content: str):
        if not self._valid(content):
            return
        try:
            with closing(self._connect()) as db, db:
                db.execute('INSERT OR REPLACE INTO responses VALUES (?,?,?,?)',
                           (identity, content, hashlib.sha256(content.encode()).hexdigest(), self.clock()))
                db.execute('DELETE FROM responses WHERE created<=?', (self.clock() - self.ttl,))
                db.execute('DELETE FROM responses WHERE id IN (SELECT id FROM responses ORDER BY created DESC,rowid DESC LIMIT -1 OFFSET ?)', (self.limit,))
        except (OSError, sqlite3.Error):
            pass

    def get_or_compute(self, identity: str, compute: Callable[[], str]) -> str:
        # 固定数量分片锁，避免重复课堂/文件同时请求同一输入，也避免锁字典无限增长。
        with _locks[int(identity[:8], 16) % len(_locks)]:
            content = self.get(identity)
            if content is not None:
                return content
            content = compute()  # 失败/截断异常不进入缓存。
            self.store(identity, content)
            return content
