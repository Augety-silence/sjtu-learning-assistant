from __future__ import annotations

import threading
import time

import pytest

from sjtu_learning_assistant.ai_stream_buffer import AIStreamEventBuffer


class Timer:
    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.cancelled = False
        self.daemon = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # 即使取消发生时 callback 已就绪，也不能串到下一轮或终态之后。
        self.callback()


def setup_buffer(check=lambda: None, emit=None):
    events, timers = [], []

    def factory(interval, callback):
        timer = Timer(interval, callback)
        timers.append(timer)
        return timer

    buffer = AIStreamEventBuffer(emit or (lambda k, p: events.append((k, p))), check, timer_factory=factory)
    return buffer, events, timers


def test_short_note_flushes_without_more_network_tokens():
    events = []
    arrived = threading.Event()
    started = time.monotonic()

    def emit(kind, payload):
        events.append((kind, payload, time.monotonic() - started))
        arrived.set()

    with AIStreamEventBuffer(emit, lambda: None) as buffer:
        buffer.add("progress_delta", {"delta": "正在查"})
        assert arrived.wait(1), "短句不能一直等待后续 token 或工具事件"
    assert events[0][:2] == ("progress_delta", {"delta": "正在查"})
    assert events[0][2] >= 0.08
    assert len(events) == 1


def test_high_frequency_tokens_share_one_timer_and_write():
    buffer, events, timers = setup_buffer()
    for _ in range(100):
        buffer.add("progress_delta", {"delta": "字"})
    assert len(timers) == 1 and timers[0].interval == 0.1
    assert not events
    timers[0].fire()
    assert events == [("progress_delta", {"delta": "字" * 100})]
    buffer.close()


def test_old_timer_cannot_flush_new_segment_and_finish_stops_writes():
    buffer, events, timers = setup_buffer()
    buffer.add("progress_delta", {"delta": "说明"})
    buffer.add("tool", {"tool_name": "list_courses"})
    buffer.add("progress_delta", {"delta": "答案"})
    timers[0].fire()
    assert len(events) == 2
    buffer.add("answer_start", {})
    buffer.add("content_delta", {"delta": "结果"})
    buffer.close()
    assert [kind for kind, _ in events] == ["progress_delta", "tool", "progress_delta", "answer_start", "content_delta"]
    for timer in timers:
        timer.fire()
    buffer.add("progress_delta", {"delta": "终态之后"})
    assert len(events) == 5


def test_failure_discards_pending_text_and_cancels_timer():
    buffer, events, timers = setup_buffer()
    with pytest.raises(ValueError), buffer:
        buffer.add("progress_delta", {"delta": "未完成"})
        raise ValueError("断开")
    assert timers[0].cancelled
    timers[0].fire()
    assert not events


@pytest.mark.parametrize("finish", [False, True])
def test_timer_error_is_returned_to_task_thread(finish):
    def fail(*args):
        raise RuntimeError("模拟数据库写入失败")

    buffer, _, timers = setup_buffer(emit=fail)
    buffer.add("progress_delta", {"delta": "说明"})
    timers[0].fire()  # 定时线程不抛未捕获异常。
    with pytest.raises(RuntimeError, match="数据库"):
        if finish:
            buffer.close()
        else:
            buffer.add("tool", {})
    buffer.close(flush=False)


def test_cancel_checked_before_timer_write():
    cancelled = False

    def check():
        if cancelled:
            raise RuntimeError("取消")

    buffer, events, timers = setup_buffer(check)
    buffer.add("progress_delta", {"delta": "说明"})
    cancelled = True
    timers[0].fire()
    assert not events
    with pytest.raises(RuntimeError, match="取消"):
        buffer.close()


def test_abnormally_large_chunk_is_bounded_and_not_written_twice():
    buffer, events, timers = setup_buffer()
    buffer.add("progress_delta", {"delta": "短"})
    buffer.add("progress_delta", {"delta": "文" * 1024})
    timers[0].fire()
    buffer.close()
    assert events == [("progress_delta", {"delta": "短" + "文" * 1024})]
