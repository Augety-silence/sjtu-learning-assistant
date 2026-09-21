"""Incremental IMAP reader using UIDVALIDITY and UID cursors."""

from __future__ import annotations

import imaplib
import json
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from test_mail import (
    DEFAULT_TIMEOUT_SECONDS,
    IMAP_HOST,
    IMAP_PORT,
    MailCheckError,
    decode_mime_text,
)

DEFAULT_INITIAL_LIMIT = 100


@dataclass(frozen=True)
class EmailRecord:
    source_id: str
    subject: str
    sender_name: str | None
    sender_address: str | None
    sent_at: datetime | None
    received_at: datetime | None
    body_preview: str | None
    is_unread: bool
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class MailFetchResult:
    messages: list[EmailRecord]
    cursor: str
    uid_validity: str
    highest_uid: int
    bootstrap_truncated: bool


def parse_cursor(cursor: str | None) -> tuple[str | None, int]:
    if not cursor:
        return None, 0
    try:
        payload = json.loads(cursor)
        uid_validity = str(payload["uid_validity"])
        last_uid = int(payload["last_uid"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None, 0
    return uid_validity, max(last_uid, 0)


def make_cursor(uid_validity: str, last_uid: int) -> str:
    return json.dumps(
        {"uid_validity": uid_validity, "last_uid": last_uid},
        separators=(",", ":"),
        sort_keys=True,
    )


def _uid_validity(client: imaplib.IMAP4_SSL) -> str:
    status, values = client.response("UIDVALIDITY")
    if status != "UIDVALIDITY" or not values or not values[0]:
        raise MailCheckError("服务器没有返回 INBOX UIDVALIDITY，无法安全增量同步。")
    value = values[0]
    return value.decode("ascii") if isinstance(value, bytes) else str(value)


def _extract_fetch_parts(fetch_data: list[object]) -> tuple[bytes, bytes]:
    metadata = b""
    headers = b""
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2:
            if isinstance(item[0], bytes):
                metadata += item[0]
            if isinstance(item[1], bytes):
                headers += item[1]
    if not headers:
        raise MailCheckError("服务器返回了无法识别的邮件头数据。")
    return metadata, headers


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_internal_date(metadata: bytes) -> datetime | None:
    match = re.search(rb'INTERNALDATE "([^"]+)"', metadata)
    if not match:
        return None
    return _parse_datetime(match.group(1).decode("ascii", errors="replace"))


def _parse_message(
    *,
    email_address: str,
    uid_validity: str,
    uid: int,
    metadata: bytes,
    headers: bytes,
) -> EmailRecord:
    message = BytesParser(policy=policy.compat32).parsebytes(headers)
    sender_raw = decode_mime_text(message.get("From"), "")
    sender_name, sender_address = parseaddr(sender_raw)
    sender_name = decode_mime_text(sender_name, "") or None
    sender_address = sender_address or None
    sent_at = _parse_datetime(message.get("Date"))
    received_at = _parse_internal_date(metadata) or sent_at
    flags_match = re.search(rb"FLAGS \(([^)]*)\)", metadata)
    flags = flags_match.group(1).split() if flags_match else []
    is_unread = b"\\Seen" not in flags
    message_id = decode_mime_text(message.get("Message-ID"), "") or None

    return EmailRecord(
        source_id=f"{email_address}:INBOX:{uid_validity}:{uid}",
        subject=decode_mime_text(message.get("Subject"), "（无主题）"),
        sender_name=sender_name,
        sender_address=sender_address,
        sent_at=sent_at,
        received_at=received_at,
        body_preview=None,
        is_unread=is_unread,
        raw_data={
            "folder": "INBOX",
            "uid": uid,
            "uid_validity": uid_validity,
            "message_id": message_id,
        },
    )


def fetch_incremental_mail(
    email_address: str,
    password: str,
    cursor: str | None,
    *,
    initial_limit: int = DEFAULT_INITIAL_LIMIT,
) -> MailFetchResult:
    if initial_limit < 1:
        raise MailCheckError("首次同步邮件数量必须大于 0。")

    tls_context = ssl.create_default_context()
    try:
        with imaplib.IMAP4_SSL(
            IMAP_HOST,
            IMAP_PORT,
            ssl_context=tls_context,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ) as client:
            client.login(email_address, password)
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                raise MailCheckError("无法以只读方式打开 INBOX。")

            uid_validity = _uid_validity(client)
            previous_validity, previous_uid = parse_cursor(cursor)
            is_incremental = previous_validity == uid_validity and previous_uid > 0
            search_start = previous_uid + 1 if is_incremental else 1

            status, search_data = client.uid(
                "search", None, "UID", f"{search_start}:4294967295"
            )
            if status != "OK" or not search_data:
                raise MailCheckError("无法读取 INBOX UID 列表。")

            all_uids = sorted(
                int(value)
                for value in search_data[0].split()
                if value.isdigit() and int(value) >= search_start
            )
            highest_uid = all_uids[-1] if all_uids else previous_uid
            bootstrap_truncated = not is_incremental and len(all_uids) > initial_limit
            selected_uids = all_uids if is_incremental else all_uids[-initial_limit:]

            messages: list[EmailRecord] = []
            for uid in selected_uids:
                status, fetch_data = client.uid(
                    "fetch",
                    str(uid),
                    "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM DATE)] "
                    "FLAGS INTERNALDATE)",
                )
                if status != "OK" or not fetch_data:
                    raise MailCheckError(f"UID {uid} 邮件头读取失败。")
                metadata, headers = _extract_fetch_parts(fetch_data)
                messages.append(
                    _parse_message(
                        email_address=email_address,
                        uid_validity=uid_validity,
                        uid=uid,
                        metadata=metadata,
                        headers=headers,
                    )
                )

            return MailFetchResult(
                messages=messages,
                cursor=make_cursor(uid_validity, highest_uid),
                uid_validity=uid_validity,
                highest_uid=highest_uid,
                bootstrap_truncated=bootstrap_truncated,
            )
    except imaplib.IMAP4.error as exc:
        raise MailCheckError("IMAP 登录或增量读取失败，请检查账号和密码。") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise MailCheckError("IMAP 连接超时，请检查网络后重试。") from exc
    except ssl.SSLError as exc:
        raise MailCheckError(f"IMAP TLS 连接失败：{exc}") from exc
    except OSError as exc:
        raise MailCheckError(f"无法连接 {IMAP_HOST}:{IMAP_PORT}：{exc}") from exc
