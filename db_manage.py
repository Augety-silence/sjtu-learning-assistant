#!/usr/bin/env python3
"""Manage the default SQLite database and explicit PostgreSQL fallback."""

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
    default_sqlite_url,
    get_database_url,
    get_postgres_database_url,
    redact_database_url,
    save_database_url,
)
from sjtu_learning_assistant.desktop_database import (
    DesktopDatabaseError,
    get_schema_version,
    import_postgres_data,
    initialize_desktop_database,
)

PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="管理学习助手 SQLite/PostgreSQL 数据库。")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="检查当前默认数据库。")
    upgrade = subparsers.add_parser("upgrade", help="初始化 SQLite 或升级显式 PostgreSQL。")
    upgrade.add_argument(
        "--skip-import",
        action="store_true",
        help="初始化 SQLite 时不从 Keychain 中的 PostgreSQL 导入。",
    )
    subparsers.add_parser(
        "import-postgres", help="显式将 Keychain PostgreSQL 数据导入全新空 SQLite。"
    )
    subparsers.add_parser(
        "use-postgres-status", help="检查 Keychain/环境变量中显式 PostgreSQL。"
    )
    subparsers.add_parser(
        "use-postgres-upgrade", help="对 Keychain/环境变量中的 PostgreSQL 执行 Alembic。"
    )
    subparsers.add_parser("configure", help="安全输入并保存 PostgreSQL 连接地址。")
    subparsers.add_parser("forget-config", help="删除 Keychain 中的 PostgreSQL 配置。")
    return parser.parse_args(argv)


def alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def configure_database() -> None:
    try:
        database_url = getpass.getpass(
            "请输入 PostgreSQL URL（不会显示，格式 postgresql+psycopg://...）："
        )
    except (EOFError, KeyboardInterrupt) as exc:
        raise DatabaseConfigError("已取消数据库配置。") from exc
    save_database_url(database_url)
    print("PostgreSQL 连接配置已安全保存到 macOS Keychain。")


def _print_health(database_url: str) -> None:
    engine = create_database_engine(database_url)
    try:
        health = check_database(engine)
        print(f"数据库：{redact_database_url(database_url)}")
        if health.dialect == "sqlite":
            print(f"连接成功：SQLite {health.server_version}，文件={health.database}")
            print(f"Schema version：{get_schema_version(engine) or '未初始化'}")
        else:
            print(f"连接成功：database={health.database}, user={health.user}")
            print(f"PostgreSQL {health.server_version}")
    finally:
        engine.dispose()


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
        if args.command == "use-postgres-status":
            _print_health(get_postgres_database_url(required=True))
            return 0
        if args.command == "use-postgres-upgrade":
            database_url = get_postgres_database_url(required=True)
            command.upgrade(alembic_config(database_url), "head")
            print("PostgreSQL Alembic 迁移已完成。")
            return 0
        if args.command == "import-postgres":
            database_url = default_sqlite_url()
            engine = create_database_engine(database_url)
            try:
                if engine.dialect.name != "sqlite":
                    raise DatabaseConfigError(
                        "import-postgres 的目标必须是 SQLite；请取消 PostgreSQL 环境覆盖。"
                    )
                summary = import_postgres_data(engine)
            finally:
                engine.dispose()
            print(summary.format())
            return 0

        database_url = get_database_url()
        if args.command == "status":
            _print_health(database_url)
            return 0
        if args.command == "upgrade":
            engine = create_database_engine(database_url)
            try:
                if engine.dialect.name == "sqlite":
                    result = initialize_desktop_database(
                        engine, skip_import=args.skip_import
                    )
                    print(f"SQLite schema version {result.schema_version} 已就绪。")
                    if result.import_summary is not None:
                        print(result.import_summary.format())
                else:
                    engine.dispose()
                    engine = None
                    command.upgrade(alembic_config(database_url), "head")
                    print("PostgreSQL Alembic 迁移已完成。")
            finally:
                if engine is not None:
                    engine.dispose()
            return 0
    except (DatabaseConfigError, DesktopDatabaseError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception:
        print("数据库操作失败；为避免泄露连接信息，未输出底层异常。", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
