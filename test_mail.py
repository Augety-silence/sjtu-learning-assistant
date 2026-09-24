#!/usr/bin/env python3
"""Verify SJTU Mail IMAP SSL access from the command line."""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from sjtu_learning_assistant.mail_sync import (
    DEFAULT_LIMIT,
    IMAP_HOST,
    IMAP_PORT,
    MailCheckError,
    MessageSummary,
    connect_and_fetch,
    delete_password,
    get_password,
    normalize_email,
    prompt_email,
    save_password,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通过 IMAP SSL 读取上海交大邮箱 INBOX 中最近的邮件标题。"
    )
    parser.add_argument("--email", help="交大邮箱地址；省略时会安全地交互输入。")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"读取数量，默认 {DEFAULT_LIMIT}。",
    )
    parser.add_argument(
        "--no-keychain",
        action="store_true",
        help="本次不读取或保存 macOS Keychain，每次都安全输入密码。",
    )
    parser.add_argument(
        "--forget-password",
        action="store_true",
        help="删除该邮箱已保存在 macOS Keychain 中的密码后退出。",
    )
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > 100:
        parser.error("--limit 必须在 1 到 100 之间。")
    return args


def print_summaries(messages: list[MessageSummary]) -> None:
    print(f"\n连接成功，读取到最近 {len(messages)} 封邮件（从新到旧）：")
    if not messages:
        print("INBOX 当前没有邮件。")
        return
    for index, message in enumerate(messages, start=1):
        print(f"\n{index}. {message.subject}")
        print(f"   发件人：{message.sender}")
        print(f"   时间：{message.sent_at}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        email_address = normalize_email(args.email) if args.email else prompt_email()
        if args.forget_password:
            delete_password(email_address)
            return 0

        password, from_keychain = get_password(
            email_address, use_keychain=not args.no_keychain
        )
        print(f"正在通过 IMAP SSL 连接 {IMAP_HOST}:{IMAP_PORT} …")
        messages = connect_and_fetch(email_address, password, args.limit)

        if not args.no_keychain and not from_keychain:
            save_password(email_address, password)
        print_summaries(messages)
        return 0
    except MailCheckError as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
