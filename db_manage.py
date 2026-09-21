#!/usr/bin/env python3
"""Manage PostgreSQL configuration, health checks, and Alembic migrations."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Sequence

from alembic import command
from alembic.config import Config

from sjtu_learning_assistant.database import (
    DatabaseConfigError,
    check_database,
    create_database_engine,
    delete_database_url,
    get_database_url,
    redact_database_url,
    save_database_url,
)

PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="管理学习助手 PostgreSQL 数据库。")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="检查当前数据库配置与连接。")
    subparsers.add_parser("upgrade", help="执行 Alembic 迁移到最新版本。")
    subparsers.add_parser("configure", help="安全输入并保存远程数据库连接地址。")
    subparsers.add_parser("forget-config", help="删除 Keychain 中的数据库连接配置。")
    return parser.parse_args(argv)


def alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


def configure_database() -> None:
    try:
        database_url = getpass.getpass(
            "请输入 PostgreSQL URL（不会显示，格式 postgresql+psycopg://...）："
        )
    except (EOFError, KeyboardInterrupt) as exc:
        raise DatabaseConfigError("已取消数据库配置。") from exc
    save_database_url(database_url)
    print("数据库连接配置已安全保存到 macOS Keychain。")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "configure":
            configure_database()
            return 0
        if args.command == "forget-config":
            removed = delete_database_url()
            print("已删除 Keychain 配置。" if removed else "Keychain 中没有数据库配置。")
            return 0

        database_url = get_database_url()
        print(f"数据库：{redact_database_url(database_url)}")
        if args.command == "upgrade":
            command.upgrade(alembic_config(), "head")
            print("数据库迁移已完成。")
            return 0
        if args.command == "status":
            engine = create_database_engine(database_url)
            try:
                health = check_database(engine)
            finally:
                engine.dispose()
            print(f"连接成功：database={health.database}, user={health.user}")
            print(f"PostgreSQL {health.server_version}")
            return 0
    except DatabaseConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"数据库操作失败：{exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
