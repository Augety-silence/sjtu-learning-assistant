#!/usr/bin/env python3
"""Fetch active Canvas courses and persist them to the configured database."""

from __future__ import annotations

import sys

from sqlalchemy.exc import SQLAlchemyError

from sjtu_learning_assistant.database import (
    DatabaseConfigError,
    check_database,
    create_database_engine,
)
from sjtu_learning_assistant.desktop_database import (
    DesktopDatabaseError,
    initialize_desktop_database,
)
from sjtu_learning_assistant.repository import upsert_courses
from test_canvas import (
    CanvasCheckError,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    connect_and_fetch,
    get_token,
)


def main() -> int:
    try:
        token, _ = get_token(use_keychain=True)
        print("正在从 Canvas 获取 active courses …")
        courses = connect_and_fetch(
            DEFAULT_BASE_URL, token, DEFAULT_TIMEOUT_SECONDS
        )

        engine = create_database_engine()
        try:
            if engine.dialect.name == "sqlite":
                initialize_desktop_database(engine)
            health = check_database(engine)
            print(
                f"已连接数据库：{health.database} "
                f"（server {health.server_version}）"
            )
            result = upsert_courses(engine, courses)
        finally:
            engine.dispose()

        print(
            "课程同步成功："
            f"获取 {result.fetched}，新增 {result.inserted}，更新 {result.updated}。"
        )
        return 0
    except (CanvasCheckError, DatabaseConfigError, DesktopDatabaseError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("数据库事务失败，未推进同步状态。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
