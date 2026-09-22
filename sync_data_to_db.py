#!/usr/bin/env python3
"""Incrementally sync Canvas and SJTU Mail into the configured database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from sqlalchemy.exc import SQLAlchemyError

from sjtu_learning_assistant.ai_classifier import (
    AIClassificationError,
    OpenAIClassificationClient,
)
from sjtu_learning_assistant.ai_keychain import AIKeychainError, get_ai_api_key
from sjtu_learning_assistant.archive_service import (
    DEFAULT_ARCHIVE_ROOT,
    ArchiveError,
    ArchiveService,
    normalize_term,
)
from sjtu_learning_assistant.database import (
    DatabaseConfigError,
    check_database,
    create_database_engine,
)
from sjtu_learning_assistant.desktop_database import (
    DesktopDatabaseError,
    initialize_desktop_database,
)
from sjtu_learning_assistant.local_settings import SettingsError, SettingsStore
from sjtu_learning_assistant.mail_client import (
    DEFAULT_INITIAL_LIMIT,
    fetch_email_body_backfill,
    fetch_incremental_mail,
)
from sjtu_learning_assistant.notifications import NotificationService
from sjtu_learning_assistant.repository import (
    get_email_body_backfill_targets,
    get_sync_state,
    persist_canvas_data,
    persist_email_body_backfill,
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

DEFAULT_EMAIL_BODY_BACKFILL_LIMIT = 50


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="增量同步 Canvas 与交大邮箱到本地数据库。"
    )
    parser.add_argument("--email", help="交大邮箱地址；省略时交互输入。")
    parser.add_argument(
        "--canvas-only", action="store_true", help="只同步 Canvas，不同步邮箱。"
    )
    parser.add_argument(
        "--mail-only", action="store_true", help="只同步邮箱，不同步 Canvas。"
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="初始化全新 SQLite 时不导入旧 PostgreSQL 数据。",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="本次同步不发送或登记系统通知。",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="仍同步全部文件元数据，但本次不自动下载课程文件。",
    )
    parser.add_argument(
        "--archive-root",
        default=None,
        help="显式覆盖设置中的课程文件归档根目录。",
    )
    parser.add_argument(
        "--no-organize-by-category",
        action="store_true",
        help="显式关闭按类别整理，维持旧目录结构。",
    )
    parser.add_argument(
        "--current-term",
        help="覆盖按日期推导的当前学期，格式如 2026-2027 Fall。",
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
    if args.archive_root is not None:
        if not args.archive_root.strip():
            parser.error("--archive-root 不能为空白路径。")
        args.archive_root = Path(args.archive_root).expanduser()
    if args.current_term is not None:
        try:
            args.current_term = normalize_term(args.current_term)
        except ArchiveError as exc:
            parser.error(str(exc))
    return args


def sync_canvas(
    engine,
    *,
    notify: bool = True,
    download: bool = True,
    archive_root: Path = DEFAULT_ARCHIVE_ROOT,
    current_term: str | None = None,
    organize_by_category: bool = True,
    ai_client: OpenAIClassificationClient | None = None,
    ai_enabled: bool = False,
) -> None:
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
    if download:
        with build_http_client(
            DEFAULT_BASE_URL, token, DEFAULT_TIMEOUT_SECONDS
        ) as archive_client:
            archive_result = ArchiveService(
                engine,
                archive_client,
                archive_root=archive_root,
                current_term=current_term,
                organize_by_category=organize_by_category,
                active_course_source_ids={str(course["id"]) for course in raw_courses},
                ai_client=ai_client,
                ai_enabled=ai_enabled,
            ).archive_current_term()
        if archive_result.skipped:
            print("文件归档跳过：%s" % archive_result.message)
        else:
            print(
                "文件归档完成："
                f"学期 {archive_result.term_name}，下载 {archive_result.downloaded}，"
                f"未变化 {archive_result.unchanged}，失败 {archive_result.failed}。"
            )
        for file_result in archive_result.results:
            if file_result.status == "failed":
                print(
                    f"警告：文件 {file_result.source_id} 下载失败：{file_result.error}",
                    file=sys.stderr,
                )

    if notify:
        try:
            notification_result = NotificationService(engine).process_canvas_sync()
            print(
                "通知处理完成："
                f"发送 {notification_result.sent_batches} 批，"
                f"失败 {notification_result.failed_batches} 批，"
                f"首次基线抑制 {notification_result.suppressed_events} 条，"
                f"幂等跳过 {notification_result.duplicate_events} 条。"
            )
        except Exception as exc:
            print(f"警告：通知处理失败，但数据同步已成功：{exc}", file=sys.stderr)


def sync_mail(
    engine,
    email_address: str,
    initial_limit: int,
    *,
    body_backfill_limit: int = DEFAULT_EMAIL_BODY_BACKFILL_LIMIT,
) -> None:
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

    try:
        targets = get_email_body_backfill_targets(
            engine, email_address, limit=body_backfill_limit
        )
        if targets:
            backfill = fetch_email_body_backfill(email_address, password, targets)
            updated = persist_email_body_backfill(engine, backfill.updates)
            print(
                "旧邮件正文回填："
                f"候选 {backfill.selected}，尝试 {backfill.attempted}，"
                f"成功 {updated}，失败 {backfill.failed}，"
                f"UIDVALIDITY 不匹配 {backfill.uid_validity_mismatched}。"
            )
    except (MailCheckError, SQLAlchemyError):
        # Incremental mail is already committed. Keep the backfill retryable and do not
        # print exception details that might contain server data or credentials.
        print(
            "警告：旧邮件正文回填本次未完成；新邮件同步已成功，稍后可安全重试。",
            file=sys.stderr,
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
        if getattr(getattr(engine, "dialect", None), "name", None) == "sqlite":
            initialize_desktop_database(engine, skip_import=args.skip_import)
        health = check_database(engine)
        print(
            f"已连接数据库：{health.database} "
            f"（server {health.server_version}）"
        )

        settings = SettingsStore().resolve(
            archive_root=args.archive_root,
            auto_download_current_term=False if args.no_download else None,
            organize_by_category=False if args.no_organize_by_category else None,
        )

        if not args.mail_only:
            sync_options = {}
            ai_client = None
            if settings.ai_enabled and settings.ai_key_saved:
                try:
                    ai_key = get_ai_api_key()
                    if ai_key:
                        ai_client = OpenAIClassificationClient(
                            api_key=ai_key,
                            base_url=settings.ai_base_url,
                            model=settings.ai_model,
                        )
                except (AIKeychainError, AIClassificationError):
                    ai_client = None
            if not settings.organize_by_category:
                sync_options["organize_by_category"] = False
            if settings.ai_enabled:
                sync_options["ai_client"] = ai_client
                sync_options["ai_enabled"] = True
            try:
                sync_canvas(
                    engine,
                    notify=not args.no_notify,
                    download=settings.auto_download_current_term,
                    archive_root=Path(settings.archive_root),
                    current_term=args.current_term,
                    **sync_options,
                )
            finally:
                if ai_client is not None:
                    ai_client.close()
        if not args.canvas_only:
            email_address = (
                normalize_email(args.email) if args.email else prompt_email()
            )
            sync_mail(engine, email_address, args.initial_mail_limit)
        return 0
    except (
        ArchiveError,
        CanvasCheckError,
        MailCheckError,
        DatabaseConfigError,
        DesktopDatabaseError,
        SettingsError,
    ) as exc:
        print(f"同步失败：{exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("数据库事务失败，未推进对应同步游标。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
