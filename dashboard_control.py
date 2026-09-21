#!/usr/bin/env python3
"""管理 SJTU Learning Assistant 本地仪表盘 LaunchAgent。"""

from __future__ import annotations

import argparse
import os
import plistlib
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from sync_runner import APP_SUPPORT_DIR

LABEL = "com.sjtu.learningassistant.dashboard"
HOST = "127.0.0.1"
DEFAULT_PORT = 17655
PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS_DIR / f"{LABEL}.plist"
LOG_DIR = APP_SUPPORT_DIR / "logs"


def launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def service_target() -> str:
    return f"{launchctl_domain()}/{LABEL}"


def port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def build_plist(port: int = DEFAULT_PORT) -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(VENV_PYTHON),
            "-m",
            "uvicorn",
            "dashboard_api:app",
            "--host",
            HOST,
            "--port",
            str(port),
            "--no-access-log",
        ],
        "WorkingDirectory": str(PROJECT_ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "EnvironmentVariables": {
            "TZ": "Asia/Shanghai",
            "PYTHONUNBUFFERED": "1",
        },
        "StandardOutPath": str(LOG_DIR / "dashboard.stdout.log"),
        "StandardErrorPath": str(LOG_DIR / "dashboard.stderr.log"),
    }


def run_launchctl(arguments: Sequence[str], *, check: bool = True) -> int:
    try:
        completed = subprocess.run(["launchctl", *arguments], check=False)
    except FileNotFoundError:
        print("错误：找不到 launchctl；该命令只能在 macOS 上使用。", file=sys.stderr)
        return 127
    if check and completed.returncode != 0:
        print(f"launchctl 执行失败（退出码 {completed.returncode}）。", file=sys.stderr)
    return completed.returncode


def write_plist_atomic(payload: bytes) -> None:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{LABEL}.", suffix=".plist", dir=LAUNCH_AGENTS_DIR
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, PLIST_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)


def install(port: int) -> int:
    if sys.platform != "darwin":
        print("错误：install 仅支持 macOS。", file=sys.stderr)
        return 2
    if not VENV_PYTHON.is_file() or not os.access(VENV_PYTHON, os.X_OK):
        print(f"错误：未找到可执行的虚拟环境 Python：{VENV_PYTHON}", file=sys.stderr)
        return 2
    if not port_is_available(port):
        print(
            f"错误：{HOST}:{port} 已被占用；请先停止占用该端口的进程。",
            file=sys.stderr,
        )
        return 2

    LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(LOG_DIR, 0o700)
    payload = plistlib.dumps(build_plist(port), fmt=plistlib.FMT_XML, sort_keys=False)
    write_plist_atomic(payload)
    run_launchctl(("bootout", launchctl_domain(), str(PLIST_PATH)), check=False)
    result = run_launchctl(("bootstrap", launchctl_domain(), str(PLIST_PATH)))
    if result == 0:
        print(f"已安装并加载：{PLIST_PATH}")
        print(f"本地地址：http://{HOST}:{port}/")
    return result


def uninstall() -> int:
    result = 0
    if PLIST_PATH.exists():
        launch_result = run_launchctl(
            ("bootout", launchctl_domain(), str(PLIST_PATH)), check=False
        )
        if launch_result not in (0, 3):
            result = launch_result
        PLIST_PATH.unlink()
        print(f"已移除：{PLIST_PATH}")
    else:
        print(f"未安装：{PLIST_PATH}")
    return result


def status() -> int:
    return run_launchctl(("print", service_target()), check=False)


def kickstart() -> int:
    return run_launchctl(("kickstart", "-k", service_target()))


def open_dashboard(port: int) -> int:
    completed = subprocess.run(
        ["/usr/bin/open", f"http://{HOST}:{port}/"], check=False
    )
    if completed.returncode != 0:
        print("错误：无法打开本地仪表盘。", file=sys.stderr)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dashboard_control.py", description="管理本地学习仪表盘服务。"
    )
    parser.add_argument(
        "command", choices=("install", "uninstall", "status", "kickstart", "open")
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1024 <= args.port <= 65535:
        print("错误：端口必须在 1024 到 65535 之间。", file=sys.stderr)
        return 2
    if args.command == "install":
        return install(args.port)
    if args.command == "uninstall":
        return uninstall()
    if args.command == "status":
        return status()
    if args.command == "kickstart":
        return kickstart()
    return open_dashboard(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
