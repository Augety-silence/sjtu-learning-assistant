#!/usr/bin/env python3
"""Phase 1B: verify SJTU Canvas REST API access and list active courses.

The access token is read from macOS Keychain when available. A token entered
with getpass is written to Keychain only after a successful API request.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from dataclasses import dataclass
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通过 Canvas REST API 读取上海交大 Canvas 当前 active courses。"
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


def request_json(
    client: httpx.Client,
    url: str,
    *,
    params: list[tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
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
        raise CanvasCheckError("当前 Token 没有访问课程列表的权限（HTTP 403）。")
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
        raise CanvasCheckError("Canvas API 返回了无法识别的课程数据格式。")

    next_link = response.links.get("next")
    next_url = next_link.get("url") if next_link else None
    return payload, next_url


def fetch_active_courses(client: httpx.Client) -> list[dict[str, Any]]:
    next_url: str | None = "/api/v1/courses"
    params: list[tuple[str, str]] | None = [
        ("enrollment_state", "active"),
        ("per_page", str(DEFAULT_PAGE_SIZE)),
        ("include[]", "term"),
    ]
    courses: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for _ in range(MAX_PAGES):
        if next_url is None:
            return courses
        page, next_url = request_json(client, next_url, params=params)
        params = None  # Canvas 的 next Link 已经包含后续分页参数。
        for course in page:
            course_id = str(course.get("id", ""))
            if course_id and course_id not in seen_ids:
                seen_ids.add(course_id)
                courses.append(course)
    raise CanvasCheckError(f"分页超过安全上限 {MAX_PAGES} 页，已停止请求。")


def summarize_course(course: dict[str, Any]) -> CourseSummary:
    term = course.get("term")
    term_name = term.get("name") if isinstance(term, dict) else None
    return CourseSummary(
        course_id=course.get("id", "（未知）"),
        name=str(course.get("name") or "（未命名课程）"),
        course_code=str(course.get("course_code") or "（无课程代码）"),
        term_name=str(term_name or "（学期未知）"),
    )


def connect_and_fetch(base_url: str, token: str, timeout: float) -> list[CourseSummary]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "SJTU-Learning-Assistant/Phase-1B",
    }
    with httpx.Client(
        base_url=base_url,
        headers=headers,
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
    ) as client:
        raw_courses = fetch_active_courses(client)
    return [summarize_course(course) for course in raw_courses]


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


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.forget_token:
            delete_token()
            return 0

        token, from_keychain = get_token(use_keychain=not args.no_keychain)
        print(f"正在请求 {args.base_url}/api/v1/courses …")
        courses = connect_and_fetch(args.base_url, token, args.timeout)

        if not args.no_keychain and not from_keychain:
            save_token(token)
        print_courses(courses)
        return 0
    except CanvasCheckError as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
