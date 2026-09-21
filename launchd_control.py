#!/usr/bin/env python3
"""管理 SJTU Learning Assistant 的 macOS launchd 后台同步任务。"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from sync_runner import APP_SUPPORT_DIR, run_once

LABEL = "com.sjtu.learningassistant.sync"
START_INTERVAL_SECONDS = 900
PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
CONTROL_SCRIPT = PROJECT_ROOT / "launchd_control.py"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS_DIR / f"{LABEL}.plist"
LOG_DIR = APP_SUPPORT_DIR / "logs"


def add_sync_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--canvas-only", action="store_true", help="只同步 Canvas。")
    parser.add_argument("--mail-only", action="store_true", help="只同步邮箱。")
    parser.add_argument("--email", help="交大邮箱地址（仅作为普通启动参数保存）。")
    parser.add_argument(
        "--initial-mail-limit",
        type=int,
        default=100,
        help="首次导入最近邮件数量，默认 100。",
    )


def validate_sync_options(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    non_interactive: bool,
) -> None:
    if args.canvas_only and args.mail_only:
        parser.error("--canvas-only 和 --mail-only 不能同时使用。")
    if not 1 <= args.initial_mail_limit <= 5000:
        parser.error("--initial-mail-limit 必须在 1 到 5000 之间。")
    if non_interactive and not args.canvas_only and not args.email:
        parser.error(
            "后台任务会同步邮箱，但未提供 --email；launchd 无法交互输入。"
            "请提供 --email，或明确使用 --canvas-only。"
        )


def sync_arguments(args: argparse.Namespace) -> list[str]:
    result: list[str] = []
    if args.canvas_only:
        result.append("--canvas-only")
    if args.mail_only:
        result.append("--mail-only")
    if args.email:
        result.extend(("--email", args.email))
    result.extend(("--initial-mail-limit", str(args.initial_mail_limit)))
    return result


def build_plist(args: argparse.Namespace) -> dict[str, object]:
    """构建 plist 对象；plistlib 负责 XML 参数转义。"""
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(VENV_PYTHON),
            str(CONTROL_SCRIPT),
            "run-once",
            *sync_arguments(args),
        ],
        "RunAtLoad": True,
        "StartInterval": START_INTERVAL_SECONDS,
        "EnvironmentVariables": {
            "TZ": "Asia/Shanghai",
            "PYTHONUNBUFFERED": "1",
        },
        "StandardOutPath": str(LOG_DIR / "launchd.stdout.log"),
        "StandardErrorPath": str(LOG_DIR / "launchd.stderr.log"),
    }


def launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def service_target() -> str:
    return f"{launchctl_domain()}/{LABEL}"


def run_launchctl(arguments: Sequence[str], *, check: bool = True) -> int:
    try:
        completed = subprocess.run(["launchctl", *arguments], check=False)
    except FileNotFoundError:
        print("错误：找不到 launchctl；该命令只能在 macOS 上使用。", file=sys.stderr)
        return 127
    if check and completed.returncode != 0:
        print(
            f"launchctl 执行失败（退出码 {completed.returncode}）。",
            file=sys.stderr,
        )
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
        if temporary_path.exists():
            temporary_path.unlink()


def install(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print("错误：install 仅支持 macOS。", file=sys.stderr)
        return 2
    if not VENV_PYTHON.is_file() or not os.access(VENV_PYTHON, os.X_OK):
        print(f"错误：未找到可执行的虚拟环境 Python：{VENV_PYTHON}", file=sys.stderr)
        return 2

    LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(LOG_DIR, 0o700)
    payload = plistlib.dumps(build_plist(args), fmt=plistlib.FMT_XML, sort_keys=False)
    write_plist_atomic(payload)

    # 重新安装时先卸载旧实例；未加载返回非零不影响后续 bootstrap。
    run_launchctl(("bootout", launchctl_domain(), str(PLIST_PATH)), check=False)
    result = run_launchctl(("bootstrap", launchctl_domain(), str(PLIST_PATH)))
    if result != 0:
        return result
    print(f"已安装并加载：{PLIST_PATH}")
    print(f"服务：{service_target()}")
    return 0


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="launchd_control.py", description="管理 macOS launchd 同步任务。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="生成、安装并加载 plist。")
    add_sync_options(install_parser)

    subparsers.add_parser("uninstall", help="卸载任务并删除 plist。")
    subparsers.add_parser("status", help="显示 launchd 服务状态。")
    subparsers.add_parser("kickstart", help="立即触发已安装服务。")

    run_parser = subparsers.add_parser("run-once", help="前台执行一次受锁和重试保护的同步。")
    add_sync_options(run_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "install":
        validate_sync_options(parser, args, non_interactive=True)
        return install(args)
    if args.command == "uninstall":
        return uninstall()
    if args.command == "status":
        return status()
    if args.command == "kickstart":
        return kickstart()
    if args.command == "run-once":
        validate_sync_options(parser, args, non_interactive=False)
        return run_once(sync_arguments(args))
    parser.error(f"未知命令：{args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
