"""合并流式说明，短句最多等一个刷新窗口；所有事件按同一锁落盘。"""

from __future__ import annotations

import threading
from typing import Any, Callable


class AIStreamEventBuffer:
    def __init__(
        self,
        emit: Callable[[str, dict[str, Any]], Any],
        check_active: Callable[[], None],
        *,
        interval: float = 0.1,
        timer_factory: Callable[..., Any] = threading.Timer,
    ) -> None:
        self._emit = emit
        self._check_active = check_active
        self._interval = interval
        self._timer_factory = timer_factory
        self._lock = threading.RLock()
        self._parts: dict[str, list[str]] = {
            "reasoning_delta": [], "progress_delta": [], "content_delta": [],
        }
        self._lengths = dict.fromkeys(self._parts, 0)
        self._timer: Any = None
        self._generation = 0
        self._closed = False
        self._error: Exception | None = None

    def __enter__(self) -> AIStreamEventBuffer:
        return self

    def __exit__(self, exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close(flush=exc_type is None)

    def _cancel_timer(self) -> None:
        self._generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _flush(self, kind: str) -> None:
        if not self._parts[kind]:
            return
        self._check_active()
        self._emit(kind, {"delta": "".join(self._parts[kind])})
        self._parts[kind].clear()
        self._lengths[kind] = 0

    def _flush_all(self) -> None:
        self._cancel_timer()
        for kind in self._parts:
            self._flush(kind)

    def _on_timer(self, generation: int) -> None:
        with self._lock:
            if self._closed or generation != self._generation:
                return
            self._timer = None
            try:
                self._flush("progress_delta")
            except Exception as exc:
                # 不让异常丢在定时线程；下一个事件或收尾交给任务线程处理。
                self._error = exc

    def add(self, kind: str, payload: dict[str, Any]) -> None:
        with self._lock:
            if self._error is not None:
                raise self._error
            if self._closed:
                return
            self._check_active()
            if kind not in self._parts:
                self._flush_all()
                self._emit(kind, payload)
                return
            delta = payload.get("delta")
            if type(delta) is not str or not delta:
                return
            self._parts[kind].append(delta)
            self._lengths[kind] += len(delta)
            if kind != "progress_delta":
                if self._lengths[kind] >= 12:
                    self._flush(kind)
            elif self._lengths[kind] >= 1024:
                # 防止单个刷新窗口在异常高速输出下积压无限量文本。
                self._cancel_timer()
                self._flush(kind)
            elif self._timer is None:
                self._generation += 1
                generation = self._generation
                self._timer = self._timer_factory(
                    self._interval, lambda: self._on_timer(generation),
                )
                self._timer.daemon = True
                self._timer.start()

    def close(self, *, flush: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_timer()
            try:
                if flush:
                    if self._error is not None:
                        raise self._error
                    self._flush_all()
            finally:
                for parts in self._parts.values():
                    parts.clear()
