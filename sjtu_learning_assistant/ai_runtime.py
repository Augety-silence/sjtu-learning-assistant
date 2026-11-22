"""跨进程请求预约和轻量耗时观测；不保存密钥、地址、提示词或响应。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import threading
from contextlib import contextmanager, closing
from pathlib import Path
from uuid import uuid4

_cancellation = threading.local()


@contextmanager
def cancellation_scope(cancelled):
    previous = getattr(_cancellation, 'check', None)
    _cancellation.check = cancelled
    try:
        yield
    finally:
        _cancellation.check = previous


def token_reservation(body: dict) -> int:
    """文本按 UTF-8 字节保守预约；图片独立预留，完成后使用上游计数。"""
    total = int(body.get("max_tokens") or 8192)
    for message in body.get("messages", []):
        content = message.get("content", "")
        if isinstance(content, str):
            total += len(content.encode("utf-8"))
        elif isinstance(content, list):
            for part in content:
                total += 30000 if part.get("type") == "image_url" else len(str(part.get("text", "")).encode("utf-8"))
    total += len(json.dumps(body.get("tools", []), ensure_ascii=False).encode("utf-8"))
    return total


class SharedAIRuntime:
    def __init__(self, path: Path, group: str, *, clock=time.time, sleeper=time.sleep,
                 rpm=10, tpm=300000, max_inflight=10):
        self.path, self.group = path, group
        self.clock, self.sleeper = clock, sleeper
        self.rpm, self.tpm, self.max_inflight = rpm, tpm, max_inflight

    @contextmanager
    def _db(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise RuntimeError("模型调度记录路径不安全。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, scope TEXT, queued REAL, started REAL, ended REAL, lease REAL, reserved INTEGER, model TEXT, phase TEXT, task TEXT, outcome TEXT, elapsed_ms INTEGER, input INTEGER, output INTEGER, cache INTEGER)")
            db.execute("CREATE INDEX IF NOT EXISTS request_scope ON requests(scope, started)")
            db.execute("CREATE TABLE IF NOT EXISTS cooldowns (scope TEXT PRIMARY KEY, until REAL)")
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            yield db

    def reserve(self, body: dict, *, cancelled=lambda: False) -> str:
        reserved = token_reservation(body)
        if reserved > self.tpm:
            raise RuntimeError("本次输入超过分钟 token 预约容量，请进一步分块。")
        identity, queued = uuid4().hex, self.clock()
        encoded = json.dumps(body.get("messages", []), ensure_ascii=False, sort_keys=True)
        task = hashlib.sha256(encoded.encode()).hexdigest()[:16]
        system = str(next(iter(body.get("messages", [])), {}).get("content", ""))
        phase = "review" if "审校" in system else "summary" if "总结" in system else "vision" if "页面" in system else "chat"
        with self._db() as db:
            # 有界保留观测；过期执行租约允许崩溃恢复，原任务检查点另行保存。
            db.execute("DELETE FROM requests WHERE queued < ?", (queued - 7 * 86400,))
            db.execute("DELETE FROM requests WHERE id IN (SELECT id FROM requests WHERE ended IS NOT NULL ORDER BY queued DESC LIMIT -1 OFFSET 20000)")
            db.execute("INSERT INTO requests(id,scope,queued,reserved,model,phase,task,lease) VALUES(?,?,?,?,?,?,?,?)",
                       (identity, self.group, queued, reserved, body.get("model"), phase, task, queued + 1800))
        while True:
            now = self.clock()
            scoped = getattr(_cancellation, 'check', None)
            if cancelled() or (scoped is not None and scoped()) or now - queued > 1800:
                self.finish(identity, {}, "cancelled", queued)
                raise RuntimeError("模型排队已停止。")
            with self._db() as db:
                count, tokens = db.execute("SELECT count(*),coalesce(sum(reserved),0) FROM requests WHERE scope=? AND started>?", (self.group, now - 60)).fetchone()
                # 先派发剩余 token 容量能容纳的最早任务，避免一个大输入堵住全部小请求。
                # 大任务在下一分钟容量释放后仍按原排队时间优先，不解除任何配额。
                oldest = db.execute("SELECT id FROM requests WHERE scope=? AND started IS NULL AND ended IS NULL AND lease>? AND reserved<=? ORDER BY queued,id LIMIT 1", (self.group, now, self.tpm - tokens)).fetchone()
                active = db.execute("SELECT count(*) FROM requests WHERE scope=? AND started IS NOT NULL AND ended IS NULL AND lease>?", (self.group, now)).fetchone()[0]
                cooldown = db.execute("SELECT until FROM cooldowns WHERE scope=?", (self.group,)).fetchone()
                if (oldest and oldest[0] == identity and count < self.rpm and tokens + reserved <= self.tpm
                        and active < self.max_inflight and (not cooldown or cooldown[0] <= now)):
                    db.execute("UPDATE requests SET started=?,lease=? WHERE id=?", (now, now + 360, identity))
                    return identity
            self.sleeper(0.5)

    def finish(self, identity: str, usage: dict, outcome: str, started: float, *, rate_limited=False):
        now = self.clock()
        cache = usage.get("cached_tokens", usage.get("prompt_cache_hit_tokens"))
        with self._db() as db:
            # 缺失计数保持 NULL，预约不因上游漏报而放松。
            db.execute("UPDATE requests SET ended=?,outcome=?,elapsed_ms=?,input=?,output=?,cache=?,reserved=CASE WHEN ? IS NULL THEN reserved ELSE ? END WHERE id=?",
                       (now, outcome, max(0, int((now-started)*1000)), usage.get("prompt_tokens"), usage.get("completion_tokens"), cache,
                        usage.get("total_tokens"), usage.get("total_tokens"), identity))
            if rate_limited:
                db.execute("INSERT INTO cooldowns VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET until=max(until,excluded.until)", (self.group, now + 60))

    @contextmanager
    def request(self, body: dict, usage: dict, *, cancelled=lambda: False):
        identity = self.reserve(body, cancelled=cancelled)
        started, outcome, limited = self.clock(), "http_success", False
        stopped = threading.Event()
        def renew():
            while not stopped.wait(30):
                try:
                    with self._db() as db:
                        db.execute("UPDATE requests SET lease=? WHERE id=? AND ended IS NULL", (self.clock() + 360, identity))
                except Exception:
                    pass
        heartbeat = threading.Thread(target=renew, name="ai-request-lease", daemon=True)
        heartbeat.start()
        try:
            yield
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            outcome = "http_" + str(status) if status else type(exc).__name__
            limited = status == 429
            raise
        finally:
            stopped.set()
            heartbeat.join(timeout=1)
            try:
                self.finish(identity, usage, outcome, started, rate_limited=limited)
            except Exception:
                # 观测提交失败不可将已计费响应变成一次重试。
                import logging
                logging.getLogger(__name__).warning("模型请求观测提交失败。")
