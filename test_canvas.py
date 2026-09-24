#!/usr/bin/env python3
"""Verify SJTU Canvas connectivity from the command line."""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from sjtu_learning_assistant.canvas_sync import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    CanvasCheckError,
    CourseContent,
    CourseSummary,
    connect_and_fetch,
    connect_and_fetch_all,
    delete_token,
    get_token,
    normalize_base_url,
    save_token,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通过 Canvas REST API 读取 active courses、公告和作业。"
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Canvas 站点地址，默认 {DEFAULT_BASE_URL}。",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"每次请求超时秒数，默认 {DEFAULT_TIMEOUT_SECONDS:g}。",
    )
    parser.add_argument(
        "--courses-only",
        action="store_true",
        help="仅执行 Phase 1B 课程列表验证，不请求公告和作业。",
    )
    parser.add_argument(
        "--no-keychain",
        action="store_true",
        help="本次不读取或保存 macOS Keychain，每次都安全输入 Token。",
    )
    parser.add_argument(
        "--forget-token",
        action="store_true",
        help="删除 macOS Keychain 中保存的 Canvas Token 后退出。",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.timeout > 300:
        parser.error("--timeout 必须大于 0 且不超过 300 秒。")
    args.base_url = normalize_base_url(args.base_url)
    return args


def print_courses(courses: list[CourseSummary]) -> None:
    print(f"\nAPI 验证成功，获取到 {len(courses)} 门 active courses：")
    if not courses:
        print("当前账号没有可访问的 active courses。")
        return
    for index, course in enumerate(courses, start=1):
        print(f"\n{index}. {course.name}")
        print(f"   ID：{course.course_id}")
        print(f"   课程代码：{course.course_code}")
        print(f"   学期：{course.term_name}")


def print_course_contents(contents: list[CourseContent]) -> None:
    print(f"\nPhase 1C 验证完成，共读取 {len(contents)} 门 active courses。")
    if not contents:
        print("当前账号没有可访问的 active courses。")
        return

    for index, content in enumerate(contents, start=1):
        print(f"\n{'=' * 72}")
        print(f"{index}. {content.course.name}（ID: {content.course.course_id}）")
        print(
            f"   公告 {len(content.announcements)} 条；"
            f"作业 {len(content.assignments)} 项"
        )

        print("\n   [公告]")
        if not content.announcements:
            print("   暂无可见公告。")
        for item_index, announcement in enumerate(content.announcements, start=1):
            print(f"   {item_index}. {announcement.title}")
            print(f"      发布时间：{announcement.posted_at}")
            if announcement.url:
                print(f"      链接：{announcement.url}")

        print("\n   [作业]")
        if not content.assignments:
            print("   暂无可见作业。")
        for item_index, assignment in enumerate(content.assignments, start=1):
            print(f"   {item_index}. {assignment.name}")
            print(f"      截止时间：{assignment.due_at}")
            print(f"      分值：{assignment.points_possible}")
            print(f"      提交状态：{assignment.submission_state}")
            if assignment.url:
                print(f"      链接：{assignment.url}")

        for error in content.errors:
            print(f"\n   警告：{error}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.forget_token:
            delete_token()
            return 0

        token, from_keychain = get_token(use_keychain=not args.no_keychain)
        if args.courses_only:
            print(f"正在请求 {args.base_url}/api/v1/courses …")
            courses = connect_and_fetch(args.base_url, token, args.timeout)
            if not args.no_keychain and not from_keychain:
                save_token(token)
            print_courses(courses)
            return 0

        print("正在读取 active courses、announcements 和 assignments …")
        contents = connect_and_fetch_all(args.base_url, token, args.timeout)
        if not args.no_keychain and not from_keychain:
            save_token(token)
        print_course_contents(contents)
        partial_errors = sum(len(content.errors) for content in contents)
        if partial_errors:
            print(
                f"\n部分完成：有 {partial_errors} 个课程接口读取失败，请查看警告。",
                file=sys.stderr,
            )
            return 2
        return 0
    except CanvasCheckError as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
