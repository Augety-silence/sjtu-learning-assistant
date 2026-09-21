#!/usr/bin/env python3
"""Phase 1C: verify SJTU Canvas courses, announcements, and assignments.

The access token is read from macOS Keychain when available. A token entered
with getpass is written to Keychain only after the courses API authenticates.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence
from urllib.parse import urlparse

import httpx

DEFAULT_BASE_URL = "https://oc.sjtu.edu.cn"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 100
KEYCHAIN_SERVICE = "SJTU Learning Assistant - oc.sjtu.edu.cn"
KEYCHAIN_ACCOUNT = "canvas-access-token"


class CanvasCheckError(RuntimeError):
    """A user-facing error raised during the Canvas API check."""


@dataclass(frozen=True)
class CourseSummary:
    course_id: int | str
    name: str
    course_code: str
    term_name: str


@dataclass(frozen=True)
class AnnouncementSummary:
    announcement_id: int | str
    title: str
    posted_at: str
    url: str


@dataclass(frozen=True)
class AssignmentSummary:
    assignment_id: int | str
    name: str
    due_at: str
    points_possible: str
    submission_state: str
    url: str


@dataclass
class CourseContent:
    course: CourseSummary
    announcements: list[AnnouncementSummary] = field(default_factory=list)
    assignments: list[AssignmentSummary] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CollectionFetchResult:
    records: list[dict[str, Any]]
    etag: str | None
    not_modified: bool


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


def normalize_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise CanvasCheckError("Canvas BASE_URL 必须是有效的 HTTPS 站点地址。")
    return base_url


def load_keyring_module():
    try:
        import keyring  # type: ignore[import-not-found]
        from keyring.errors import KeyringError  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CanvasCheckError(
            "缺少 keyring 依赖。请先运行：python3 -m pip install -r requirements.txt"
        ) from exc
    return keyring, KeyringError


def get_token(use_keychain: bool) -> tuple[str, bool]:
    """Return (token, came_from_keychain)."""
    if use_keychain:
        keyring, KeyringError = load_keyring_module()
        try:
            stored = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        except KeyringError as exc:
            print(f"提示：暂时无法读取 Keychain，将改为安全输入（{exc}）。")
        else:
            if stored:
                print("已从 macOS Keychain 读取 Canvas Access Token。")
                return stored, True

    try:
        token = getpass.getpass("请输入 Canvas Access Token（输入内容不会显示）：").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise CanvasCheckError("已取消 Token 输入。") from exc
    if not token:
        raise CanvasCheckError("Canvas Access Token 不能为空。")
    return token, False


def save_token(token: str) -> None:
    keyring, KeyringError = load_keyring_module()
    try:
        keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, token)
    except KeyringError as exc:
        print(f"提示：API 验证已成功，但 Token 未能保存到 Keychain（{exc}）。")
    else:
        print("Canvas Access Token 已安全保存到 macOS Keychain。")


def delete_token() -> None:
    keyring, KeyringError = load_keyring_module()
    try:
        existing = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        if existing is None:
            print("Keychain 中没有找到已保存的 Canvas Token。")
            return
        keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except KeyringError as exc:
        raise CanvasCheckError(f"无法删除 Keychain 中的 Canvas Token：{exc}") from exc
    print("已从 macOS Keychain 删除 Canvas Token。")


def ensure_same_origin(client: httpx.Client, url: str) -> None:
    """Prevent an untrusted pagination Link from receiving the bearer token."""
    target = client.base_url.join(url)
    base_port = client.base_url.port or 443
    target_port = target.port or 443
    if (
        target.scheme != client.base_url.scheme
        or target.host != client.base_url.host
        or target_port != base_port
    ):
        raise CanvasCheckError("Canvas 分页链接指向了其他站点，已停止以保护 Token。")


def request_json(
    client: httpx.Client,
    url: str,
    *,
    params: list[tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    ensure_same_origin(client, url)
    try:
        response = client.get(url, params=params)
    except httpx.TimeoutException as exc:
        raise CanvasCheckError("Canvas API 请求超时，请检查网络后重试。") from exc
    except httpx.NetworkError as exc:
        raise CanvasCheckError(f"无法连接 Canvas API：{exc}") from exc
    except httpx.HTTPError as exc:
        raise CanvasCheckError(f"Canvas API 请求失败：{exc}") from exc

    if response.status_code == 401:
        raise CanvasCheckError("Canvas Access Token 无效或已过期（HTTP 401）。")
    if response.status_code == 403:
        raise CanvasCheckError("当前 Token 没有访问此 Canvas 资源的权限（HTTP 403）。")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CanvasCheckError(
            f"Canvas API 返回 HTTP {response.status_code}。"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise CanvasCheckError("Canvas API 返回的不是有效 JSON。") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise CanvasCheckError("Canvas API 返回了无法识别的数据格式。")

    next_link = response.links.get("next")
    next_url = next_link.get("url") if next_link else None
    return payload, next_url


def fetch_paginated(
    client: httpx.Client,
    url: str,
    *,
    params: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    next_url: str | None = url
    next_params: list[tuple[str, str]] | None = params
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for _ in range(MAX_PAGES):
        if next_url is None:
            return records
        page, next_url = request_json(client, next_url, params=next_params)
        next_params = None  # Canvas 的 next Link 已经包含后续分页参数。
        for record in page:
            remote_id = str(record.get("id", ""))
            if not remote_id or remote_id not in seen_ids:
                if remote_id:
                    seen_ids.add(remote_id)
                records.append(record)
    raise CanvasCheckError(f"分页超过安全上限 {MAX_PAGES} 页，已停止请求。")


def fetch_paginated_with_etag(
    client: httpx.Client,
    url: str,
    *,
    params: list[tuple[str, str]],
    etag: str | None,
) -> CollectionFetchResult:
    """Fetch a collection, using ETag to skip unchanged resources."""
    ensure_same_origin(client, url)
    headers = {"If-None-Match": etag} if etag else None
    try:
        response = client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as exc:
        raise CanvasCheckError("Canvas API 请求超时，请检查网络后重试。") from exc
    except httpx.NetworkError as exc:
        raise CanvasCheckError(f"无法连接 Canvas API：{exc}") from exc
    except httpx.HTTPError as exc:
        raise CanvasCheckError(f"Canvas API 请求失败：{exc}") from exc

    if response.status_code == 304:
        return CollectionFetchResult(records=[], etag=etag, not_modified=True)
    if response.status_code == 401:
        raise CanvasCheckError("Canvas Access Token 无效或已过期（HTTP 401）。")
    if response.status_code == 403:
        raise CanvasCheckError("当前 Token 没有访问此 Canvas 资源的权限（HTTP 403）。")
    try:
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise CanvasCheckError(
            f"Canvas API 返回 HTTP {response.status_code}。"
        ) from exc
    except ValueError as exc:
        raise CanvasCheckError("Canvas API 返回的不是有效 JSON。") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise CanvasCheckError("Canvas API 返回了无法识别的数据格式。")

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    current_page = payload
    next_link = response.links.get("next")
    next_url = next_link.get("url") if next_link else None
    was_paginated = next_url is not None
    for _ in range(MAX_PAGES):
        for record in current_page:
            remote_id = str(record.get("id", ""))
            if not remote_id or remote_id not in seen_ids:
                if remote_id:
                    seen_ids.add(remote_id)
                records.append(record)
        if next_url is None:
            return CollectionFetchResult(
                records=records,
                etag=None if was_paginated else response.headers.get("etag"),
                not_modified=False,
            )
        current_page, next_url = request_json(client, next_url)
    raise CanvasCheckError(f"分页超过安全上限 {MAX_PAGES} 页，已停止请求。")


def fetch_active_courses(client: httpx.Client) -> list[dict[str, Any]]:
    return fetch_paginated(
        client,
        "/api/v1/courses",
        params=[
            ("enrollment_state", "active"),
            ("per_page", str(DEFAULT_PAGE_SIZE)),
            ("include[]", "term"),
        ],
    )


def fetch_course_announcements(
    client: httpx.Client,
    course_id: int | str,
    *,
    posted_since: str | None = None,
) -> list[dict[str, Any]]:
    params = [
        ("context_codes[]", f"course_{course_id}"),
        ("per_page", str(DEFAULT_PAGE_SIZE)),
    ]
    if posted_since:
        params.append(("start_date", posted_since))
    return fetch_paginated(
        client,
        "/api/v1/announcements",
        params=params,
    )


def fetch_course_assignments(
    client: httpx.Client,
    course_id: int | str,
    *,
    updated_since: str | None = None,
) -> list[dict[str, Any]]:
    params = [
        ("per_page", str(DEFAULT_PAGE_SIZE)),
        ("order_by", "due_at"),
        ("include[]", "submission"),
    ]
    if updated_since:
        params.append(("updated_since", updated_since))
    return fetch_paginated(
        client,
        f"/api/v1/courses/{course_id}/assignments",
        params=params,
    )


def fetch_course_announcements_incremental(
    client: httpx.Client,
    course_id: int | str,
    *,
    etag: str | None,
) -> CollectionFetchResult:
    return fetch_paginated_with_etag(
        client,
        "/api/v1/announcements",
        params=[
            ("context_codes[]", f"course_{course_id}"),
            ("per_page", str(DEFAULT_PAGE_SIZE)),
        ],
        etag=etag,
    )


def fetch_course_assignments_incremental(
    client: httpx.Client,
    course_id: int | str,
    *,
    etag: str | None,
) -> CollectionFetchResult:
    return fetch_paginated_with_etag(
        client,
        f"/api/v1/courses/{course_id}/assignments",
        params=[
            ("per_page", str(DEFAULT_PAGE_SIZE)),
            ("order_by", "due_at"),
            ("include[]", "submission"),
        ],
        etag=etag,
    )


def fetch_course_files(
    client: httpx.Client, course_id: int | str
) -> list[dict[str, Any]]:
    return fetch_paginated(
        client,
        f"/api/v1/courses/{course_id}/files",
        params=[("per_page", str(DEFAULT_PAGE_SIZE)), ("sort", "updated_at")],
    )


def fetch_course_files_incremental(
    client: httpx.Client,
    course_id: int | str,
    *,
    etag: str | None,
) -> CollectionFetchResult:
    return fetch_paginated_with_etag(
        client,
        f"/api/v1/courses/{course_id}/files",
        params=[("per_page", str(DEFAULT_PAGE_SIZE)), ("sort", "updated_at")],
        etag=etag,
    )


def fetch_course_folders(
    client: httpx.Client, course_id: int | str
) -> list[dict[str, Any]]:
    return fetch_paginated(
        client,
        f"/api/v1/courses/{course_id}/folders",
        params=[("per_page", str(DEFAULT_PAGE_SIZE))],
    )


def fetch_course_modules(
    client: httpx.Client, course_id: int | str
) -> list[dict[str, Any]]:
    return fetch_paginated(
        client,
        f"/api/v1/courses/{course_id}/modules",
        params=[("per_page", str(DEFAULT_PAGE_SIZE))],
    )


def fetch_course_module_items(
    client: httpx.Client,
    course_id: int | str,
    module_id: int | str,
) -> list[dict[str, Any]]:
    return fetch_paginated(
        client,
        f"/api/v1/courses/{course_id}/modules/{module_id}/items",
        params=[("per_page", str(DEFAULT_PAGE_SIZE))],
    )


def format_canvas_time(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "（时间未设置）"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except ValueError:
        return value


def summarize_course(course: dict[str, Any]) -> CourseSummary:
    term = course.get("term")
    term_name = term.get("name") if isinstance(term, dict) else None
    return CourseSummary(
        course_id=course.get("id", "（未知）"),
        name=str(course.get("name") or "（未命名课程）"),
        course_code=str(course.get("course_code") or "（无课程代码）"),
        term_name=str(term_name or "（学期未知）"),
    )


def summarize_announcement(announcement: dict[str, Any]) -> AnnouncementSummary:
    return AnnouncementSummary(
        announcement_id=announcement.get("id", "（未知）"),
        title=str(announcement.get("title") or "（无标题公告）"),
        posted_at=format_canvas_time(announcement.get("posted_at")),
        url=str(announcement.get("html_url") or ""),
    )


def summarize_assignment(assignment: dict[str, Any]) -> AssignmentSummary:
    submission = assignment.get("submission")
    submission_state = (
        submission.get("workflow_state") if isinstance(submission, dict) else None
    )
    points = assignment.get("points_possible")
    return AssignmentSummary(
        assignment_id=assignment.get("id", "（未知）"),
        name=str(assignment.get("name") or "（无标题作业）"),
        due_at=format_canvas_time(assignment.get("due_at")),
        points_possible="（未设置）" if points is None else str(points),
        submission_state=str(submission_state or "（状态未知）"),
        url=str(assignment.get("html_url") or ""),
    )


def build_http_client(base_url: str, token: str, timeout: float) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "SJTU-Learning-Assistant/Phase-1C",
        },
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
    )


def connect_and_fetch(
    base_url: str, token: str, timeout: float
) -> list[CourseSummary]:
    """Phase 1B-compatible courses-only entry point."""
    with build_http_client(base_url, token, timeout) as client:
        raw_courses = fetch_active_courses(client)
    return [summarize_course(course) for course in raw_courses]


def connect_and_fetch_all(
    base_url: str, token: str, timeout: float
) -> list[CourseContent]:
    with build_http_client(base_url, token, timeout) as client:
        raw_courses = fetch_active_courses(client)
        contents: list[CourseContent] = []
        for raw_course in raw_courses:
            course = summarize_course(raw_course)
            content = CourseContent(course=course)
            try:
                announcements = fetch_course_announcements(client, course.course_id)
                content.announcements = [
                    summarize_announcement(item) for item in announcements
                ]
            except CanvasCheckError as exc:
                content.errors.append(f"公告读取失败：{exc}")

            try:
                assignments = fetch_course_assignments(client, course.course_id)
                content.assignments = [summarize_assignment(item) for item in assignments]
            except CanvasCheckError as exc:
                content.errors.append(f"作业读取失败：{exc}")
            contents.append(content)
    return contents


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
