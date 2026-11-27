"""为应用自己启动的同步进程提供有界等待与进程树清理。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any

DEFAULT_SYNC_TIMEOUT_SECONDS = 15 * 60
SYNC_STOP_GRACE_SECONDS = 5


def stop_sync_process(process: Any, *, force: bool = False) -> None:
    """仅清理以 start_new_session=True 启动的同步进程及其后代。"""
    if process.poll() is not None:
        return
    pid = getattr(process, "pid", None)
    if type(pid) is int and pid > 0:
        try:
            if sys.platform == "win32":
                # 源码模式的 run-once 会再启动子进程；只终止包装器会遗留同步。
                result = subprocess.run(
                    ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=SYNC_STOP_GRACE_SECONDS,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
                if result.returncode == 0:
                    return
            else:
                os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    if force:
        process.kill()
    else:
        process.terminate()
