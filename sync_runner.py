#!/usr/bin/env python3
"""可测试的单实例同步运行器，负责重试、信号转发与结构化审计日志。"""

from __future__ import annotations

import errno
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

from platformdirs import user_data_path

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt

APP_SUPPORT_DIR = (
    user_data_path("sjtu-learning-assistant", appauthor=False)
    if sys.platform == "win32"
    else Path.home() / "Library" / "Application Support" / "sjtu-learning-assistant"
)
DEFAULT_RUNTIME_DIR = APP_SUPPORT_DIR / "run"
DEFAULT_LOG_DIR = APP_SUPPORT_DIR / "logs"
DEFAULT_LOCK_PATH = DEFAULT_RUNTIME_DIR / "sync.lock"
DEFAULT_JSONL_PATH = DEFAULT_LOG_DIR / "sync.jsonl"
RETRY_DELAYS_SECONDS = (5, 30)
MAX_ATTEMPTS = 3
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class JsonlEventLogger:
    """只记录固定的非敏感元数据，避免意外持久化凭据或数据库 URL。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def log(
        self,
        *,
        run_id: str,
        attempt: int,
        event: str,
        exit_code: int | None,
        duration_ms: int,
    ) -> None:
        now_utc = datetime.now(timezone.utc)
        record = {
            "utc_time": now_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "local_time": now_utc.astimezone(SHANGHAI_TZ).isoformat(timespec="milliseconds"),
            "run_id": run_id,
            "attempt": attempt,
            "event": event,
            "exit_code": exit_code,
            "duration_ms": max(0, int(duration_ms)),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            if os.write(fd, payload) != len(payload):
                raise OSError("JSONL 日志未完整写入")
        finally:
            os.close(fd)


class SyncRunner:
    """以子进程执行核心同步脚本，并提供单实例及有限重试。"""

    def __init__(
        self,
        command: Sequence[str],
        *,
        lock_path: Path = DEFAULT_LOCK_PATH,
        log_path: Path = DEFAULT_JSONL_PATH,
        sleeper: Callable[[float], None] = time.sleep,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.command = tuple(str(part) for part in command)
        self.lock_path = Path(lock_path)
        self.logger = JsonlEventLogger(Path(log_path))
        self.sleeper = sleeper
        self.popen_factory = popen_factory
        self.clock = clock
        self._child: subprocess.Popen | None = None
        self._received_signal: int | None = None
        self._stop_event = threading.Event()

    @staticmethod
    def _elapsed_ms(started_at: float, clock: Callable[[], float]) -> int:
        return max(0, round((clock() - started_at) * 1000))

    def _acquire_lock(self) -> int | None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.lock_path.parent, 0o700)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return None
            raise
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        return fd

    @staticmethod
    def _release_lock(fd: int) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            else:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)

    def _handle_signal(self, signum: int, _frame: object) -> None:
        self._received_signal = signum
        self._stop_event.set()
        child = self._child
        if child is not None and child.poll() is None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    def _install_signal_handlers(self) -> dict[int, object]:
        if threading.current_thread() is not threading.main_thread():
            return {}
        previous: dict[int, object] = {}
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)
        return previous

    @staticmethod
    def _restore_signal_handlers(previous: dict[int, object]) -> None:
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    def _run_child(self) -> int:
        try:
            self._child = self.popen_factory(self.command)
            if self._received_signal is not None and self._child.poll() is None:
                self._child.send_signal(self._received_signal)
            return_code = self._child.wait()
            if return_code < 0:
                return 128 + abs(return_code)
            return return_code
        except OSError:
            return 1
        finally:
            self._child = None

    def _sleep_before_retry(self, delay: float) -> None:
        if self.sleeper is time.sleep:
            self._stop_event.wait(delay)
        else:
            self.sleeper(delay)

    def run(self) -> int:
        run_id = str(uuid.uuid4())
        run_started_at = self.clock()
        lock_fd = self._acquire_lock()
        if lock_fd is None:
            self.logger.log(
                run_id=run_id,
                attempt=0,
                event="skip_locked",
                exit_code=0,
                duration_ms=self._elapsed_ms(run_started_at, self.clock),
            )
            return 0

        previous_handlers = self._install_signal_handlers()
        exit_code = 1
        try:
            self.logger.log(
                run_id=run_id,
                attempt=0,
                event="run_started",
                exit_code=None,
                duration_ms=0,
            )
            for attempt in range(1, MAX_ATTEMPTS + 1):
                attempt_started_at = self.clock()
                self.logger.log(
                    run_id=run_id,
                    attempt=attempt,
                    event="attempt_started",
                    exit_code=None,
                    duration_ms=0,
                )
                exit_code = self._run_child()
                self.logger.log(
                    run_id=run_id,
                    attempt=attempt,
                    event="attempt_finished",
                    exit_code=exit_code,
                    duration_ms=self._elapsed_ms(attempt_started_at, self.clock),
                )

                if self._received_signal is not None:
                    exit_code = 128 + self._received_signal
                    break
                if exit_code == 0 or exit_code == 130 or attempt == MAX_ATTEMPTS:
                    break

                delay = RETRY_DELAYS_SECONDS[attempt - 1]
                self.logger.log(
                    run_id=run_id,
                    attempt=attempt,
                    event="retry_scheduled",
                    exit_code=exit_code,
                    duration_ms=self._elapsed_ms(run_started_at, self.clock),
                )
                self._sleep_before_retry(delay)
                if self._received_signal is not None:
                    exit_code = 128 + self._received_signal
                    break

            self.logger.log(
                run_id=run_id,
                attempt=attempt,
                event="run_finished",
                exit_code=exit_code,
                duration_ms=self._elapsed_ms(run_started_at, self.clock),
            )
            return exit_code
        finally:
            self._restore_signal_handlers(previous_handlers)
            self._release_lock(lock_fd)


def build_sync_command(sync_arguments: Sequence[str]) -> list[str]:
    project_root = Path(__file__).resolve().parent
    venv_python = project_root / ".venv" / "bin" / "python"
    python = venv_python if venv_python.is_file() else Path(sys.executable)
    return [str(python), str(project_root / "sync_data_to_db.py"), *sync_arguments]


def run_once(sync_arguments: Sequence[str]) -> int:
    return SyncRunner(build_sync_command(sync_arguments)).run()
