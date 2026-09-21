#!/usr/bin/env python3
"""Incrementally sync Canvas and SJTU Mail into PostgreSQL."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from sqlalchemy.exc import SQLAlchemyError

from sjtu_learning_assistant.database import (
    DatabaseConfigError,
    check_database,
    create_database_engine,
)
from sjtu_learning_assistant.mail_client import (
    DEFAULT_INITIAL_LIMIT,
    fetch_incremental_mail,
)
from sjtu_learning_assistant.repository import (
    get_sync_state,
    persist_canvas_data,
    persist_emails,
)
from test_canvas import (
    CanvasCheckError,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    build_http_client,
    fetch_active_courses,
    fetch_course_announcements_incremental,
    fetch_course_assignments_incremental,
    fetch_course_files_incremental,
    fetch_course_folders,
    fetch_course_module_items,
    fetch_course_modules,
    get_token,
)
from test_mail import MailCheckError, get_password, normalize_email, prompt_email


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="增量同步 Canvas 与交大邮箱到 PostgreSQL。"
    )
    parser.add_argument("--email", help="交大邮箱地址；省略时交互输入。")
    parser.add_argument(
        "--canvas-only", action="store_true", help="只同步 Canvas，不同步邮箱。"
    )
    parser.add_argument(
        "--mail-only", action="store_true", help="只同步邮箱，不同步 Canvas。"
    )
    parser.add_argument(
        "--initial-mail-limit",
        type=int,
        default=DEFAULT_INITIAL_LIMIT,
        help=f"首次导入最近邮件数量，默认 {DEFAULT_INITIAL_LIMIT}。",
    )
    args = parser.parse_args(argv)
    if args.canvas_only and args.mail_only:
        parser.error("--canvas-only 和 --mail-only 不能同时使用。")
    if args.initial_mail_limit < 1 or args.initial_mail_limit > 5000:
        parser.error("--initial-mail-limit 必须在 1 到 5000 之间。")
    return args


def sync_canvas(engine) -> None:
    token, _ = get_token(use_keychain=True)
    print("正在增量读取 Canvas 课程、公告、作业、文件与模块 …")
    with build_http_client(
        DEFAULT_BASE_URL, token, DEFAULT_TIMEOUT_SECONDS
    ) as client:
        raw_courses = fetch_active_courses(client)
        announcements_by_course = {}
        assignments_by_course = {}
        folders_by_course = {}
        files_by_course = {}
        modules_by_course = {}
        module_items_by_course = {}
        announcement_cursors = {}
        assignment_cursors = {}
        file_cursors = {}
        unchanged_announcements = 0
        unchanged_assignments = 0
        unchanged_files = 0

        for course in raw_courses:
            source_id = str(course["id"])
            announcement_resource = f"course:{source_id}:announcements"
            assignment_resource = f"course:{source_id}:assignments"
            file_resource = f"course:{source_id}:files"
            announcement_state = get_sync_state(
                engine, "canvas", announcement_resource
            )
            assignment_state = get_sync_state(engine, "canvas", assignment_resource)
            file_state = get_sync_state(engine, "canvas", file_resource)

            announcement_result = fetch_course_announcements_incremental(
                client,
                source_id,
                etag=announcement_state.cursor if announcement_state else None,
            )
            assignment_result = fetch_course_assignments_incremental(
                client,
                source_id,
                etag=assignment_state.cursor if assignment_state else None,
            )
            file_result = fetch_course_files_incremental(
                client,
                source_id,
                etag=file_state.cursor if file_state else None,
            )
            folders = fetch_course_folders(client, source_id)
            modules = fetch_course_modules(client, source_id)
            module_items = []
            for module in modules:
                module_id = module.get("id")
                if module_id is None:
                    continue
                module_items.extend(
                    fetch_course_module_items(client, source_id, module_id)
                )

            announcement_cursors[source_id] = announcement_result.etag
            assignment_cursors[source_id] = assignment_result.etag
            file_cursors[source_id] = file_result.etag
            folders_by_course[source_id] = folders
            modules_by_course[source_id] = modules
            module_items_by_course[source_id] = module_items
            if announcement_result.not_modified:
                unchanged_announcements += 1
            else:
                announcements_by_course[source_id] = announcement_result.records
            if assignment_result.not_modified:
                unchanged_assignments += 1
            else:
                assignments_by_course[source_id] = assignment_result.records
            if file_result.not_modified:
                unchanged_files += 1
            else:
                files_by_course[source_id] = file_result.records

    result = persist_canvas_data(
        engine,
        raw_courses=raw_courses,
        announcements_by_course=announcements_by_course,
        assignments_by_course=assignments_by_course,
        folders_by_course=folders_by_course,
        files_by_course=files_by_course,
        modules_by_course=modules_by_course,
        module_items_by_course=module_items_by_course,
        announcement_cursors=announcement_cursors,
        assignment_cursors=assignment_cursors,
        file_cursors=file_cursors,
    )
    print(
        "Canvas 同步成功："
        f"课程 {result.courses.fetched}（新增 {result.courses.inserted} / "
        f"更新 {result.courses.updated}），"
        f"公告 {result.announcements.fetched}（新增 "
        f"{result.announcements.inserted} / 更新 {result.announcements.updated}，"
        f"未变化课程 {unchanged_announcements}），"
        f"作业 {result.assignments.fetched}（新增 {result.assignments.inserted} / "
        f"更新 {result.assignments.updated}，未变化课程 {unchanged_assignments}），"
        f"文件夹 {result.folders.fetched}，"
        f"文件 {result.files.fetched}（新增 {result.files.inserted} / "
        f"更新 {result.files.updated}，未变化课程 {unchanged_files}），"
        f"模块 {result.modules.fetched}，模块项 {result.module_items.fetched}。"
    )


def sync_mail(engine, email_address: str, initial_limit: int) -> None:
    password, _ = get_password(email_address, use_keychain=True)
    state = get_sync_state(engine, "email", "inbox")
    result = fetch_incremental_mail(
        email_address,
        password,
        state.cursor if state else None,
        initial_limit=initial_limit,
    )
    persisted = persist_emails(engine, result.messages, cursor=result.cursor)
    print(
        "邮箱同步成功："
        f"获取 {persisted.fetched}，新增 {persisted.inserted}，"
        f"更新 {persisted.updated}；最高 UID {result.highest_uid}。"
    )
    if result.bootstrap_truncated:
        print(
            f"提示：首次同步仅导入最近 {initial_limit} 封邮件；"
            "后续新邮件将通过 UID 游标完整增量同步。"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    engine = None
    try:
        engine = create_database_engine()
        health = check_database(engine)
        print(
            f"已连接 PostgreSQL：{health.database} "
            f"（server {health.server_version}）"
        )

        if not args.mail_only:
            sync_canvas(engine)
        if not args.canvas_only:
            email_address = (
                normalize_email(args.email) if args.email else prompt_email()
            )
            sync_mail(engine, email_address, args.initial_mail_limit)
        return 0
    except (CanvasCheckError, MailCheckError, DatabaseConfigError) as exc:
        print(f"同步失败：{exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"数据库事务失败，未推进对应同步游标：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
