"""Incremental IMAP reader using UIDVALIDITY and UID cursors."""

from __future__ import annotations

import imaplib
import json
import re
import socket
import ssl
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Iterable, Protocol

from sjtu_learning_assistant.text_content import (
    html_to_plain_text,
    normalize_plain_text,
    sanitize_html,
)

from test_mail import (
    DEFAULT_TIMEOUT_SECONDS,
    IMAP_HOST,
    IMAP_PORT,
    MailCheckError,
    decode_mime_text,
)

DEFAULT_INITIAL_LIMIT = 100
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_MESSAGE_ATTACHMENT_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class EmailAttachment:
    resource_id: str
    filename: str
    content_type: str
    content_id: str | None
    disposition: str | None
    size: int
    payload: bytes | None
    is_inline: bool = False


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
    body_text: str | None = None
    body_html: str | None = None
    attachments: tuple[EmailAttachment, ...] = ()


@dataclass(frozen=True)
class MailFetchResult:
    messages: list[EmailRecord]
    cursor: str
    uid_validity: str
    highest_uid: int
    bootstrap_truncated: bool


class EmailBodyBackfillTarget(Protocol):
    source_id: str
    uid: int
    uid_validity: str


@dataclass(frozen=True)
class EmailBodyUpdate:
    source_id: str
    body_text: str | None
    body_preview: str | None
    body_html: str | None = None
    attachments: tuple[EmailAttachment, ...] = ()


@dataclass(frozen=True)
class MailBodyBackfillResult:
    updates: tuple[EmailBodyUpdate, ...]
    selected: int
    attempted: int
    failed: int
    uid_validity_mismatched: int


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
    message_bytes = b""
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2:
            if isinstance(item[0], bytes):
                metadata += item[0]
            if isinstance(item[1], bytes):
                message_bytes += item[1]
    if not message_bytes:
        raise MailCheckError("服务器返回了无法识别的邮件数据。")
    return metadata, message_bytes


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


def _decode_part_payload(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.encode(charset, errors="replace")
        except LookupError:
            return payload.encode("utf-8", errors="replace")
    # message/rfc822 attachments do not always expose a decoded byte payload.
    return part.as_bytes(policy=policy.default)


def _decode_text_part(part: Message) -> str | None:
    payload = _decode_part_payload(part)
    charset = part.get_content_charset()
    if charset:
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            pass
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("latin-1", errors="replace")


def _normalized_content_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = decode_mime_text(value, "").strip().strip("<>").strip()
    return normalized or None


def _message_content(
    message: Message,
) -> tuple[str | None, str | None, tuple[EmailAttachment, ...]]:
    attachments: list[EmailAttachment] = []
    attachment_index = 0

    def add_attachment(part: Message) -> None:
        nonlocal attachment_index
        attachment_index += 1
        content_id = _normalized_content_id(part.get("Content-ID"))
        resource_id = content_id or f"part-{attachment_index}"
        filename = decode_mime_text(part.get_filename(), "").strip()
        if not filename:
            filename = content_id or f"attachment-{attachment_index}"
        payload = _decode_part_payload(part)
        size = len(payload)
        disposition = (part.get_content_disposition() or "").casefold() or None
        attachments.append(
            EmailAttachment(
                resource_id=resource_id,
                filename=filename,
                content_type=part.get_content_type().casefold(),
                content_id=content_id,
                disposition=disposition,
                size=size,
                payload=payload if size <= MAX_ATTACHMENT_BYTES else None,
                is_inline=disposition == "inline" or bool(content_id),
            )
        )

    def extract(part: Message, *, force_attachment: bool = False) -> tuple[list[str], list[str]]:
        disposition = (part.get_content_disposition() or "").casefold()
        explicit_attachment = disposition == "attachment" or bool(part.get_filename())
        if force_attachment or explicit_attachment or part.get_content_maintype() == "message":
            add_attachment(part)
            return [], []

        if part.is_multipart():
            payload = part.get_payload()
            children = payload if isinstance(payload, list) else []
            subtype = part.get_content_subtype().casefold()
            if subtype == "alternative":
                selected_plain: list[str] = []
                selected_html: list[str] = []
                for child in children:
                    child_plain, child_html = extract(child)
                    if child_plain:
                        selected_plain = child_plain
                    if child_html:
                        selected_html = child_html
                return selected_plain, selected_html
            if subtype == "related" and children:
                start = _normalized_content_id(part.get_param("start"))
                root_index = 0
                if start:
                    for index, child in enumerate(children):
                        if _normalized_content_id(child.get("Content-ID")) == start:
                            root_index = index
                            break
                plain, html = extract(children[root_index])
                for index, child in enumerate(children):
                    if index != root_index:
                        extract(child, force_attachment=True)
                return plain, html
            plain: list[str] = []
            html: list[str] = []
            for child in children:
                child_plain, child_html = extract(child)
                plain.extend(child_plain)
                html.extend(child_html)
            return plain, html

        content_type = part.get_content_type().casefold()
        decoded = _decode_text_part(part)
        if content_type == "text/plain" and decoded is not None:
            text = normalize_plain_text(decoded)
            return ([text] if text else []), []
        if content_type == "text/html" and decoded is not None:
            return [], [decoded]
        add_attachment(part)
        return [], []

    plain_parts, html_parts = extract(message)
    body_html = sanitize_html("\n".join(html_parts)) if html_parts else None
    body_text = normalize_plain_text("\n\n".join(plain_parts)) if plain_parts else None
    if body_text is None and body_html:
        body_text = html_to_plain_text(body_html)

    if sum(item.size for item in attachments) > MAX_MESSAGE_ATTACHMENT_BYTES:
        attachments = [replace(item, payload=None) for item in attachments]
    return body_text, body_html, tuple(attachments)


def _message_body_text(message: Message) -> str | None:
    """Compatibility shim returning only the preferred plain-text body."""
    return _message_content(message)[0]


def _parse_message(
    *,
    email_address: str,
    uid_validity: str,
    uid: int,
    metadata: bytes,
    headers: bytes,
) -> EmailRecord:
    # ``headers`` retains its historical name so header-only callers remain compatible.
    message = BytesParser(policy=policy.default).parsebytes(headers)
    body_text, body_html, attachments = _message_content(message)
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
        body_preview=body_text[:300] if body_text else None,
        is_unread=is_unread,
        raw_data={
            "folder": "INBOX",
            "uid": uid,
            "uid_validity": uid_validity,
            "message_id": message_id,
            "rich_content_fetched": True,
        },
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
    )


def fetch_email_body_backfill(
    email_address: str,
    password: str,
    targets: Iterable[EmailBodyBackfillTarget],
) -> MailBodyBackfillResult:
    """Fetch missing rich bodies without changing any remote message flags."""
    selected_targets = list(targets)
    if not selected_targets:
        return MailBodyBackfillResult((), 0, 0, 0, 0)

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

            current_uid_validity = _uid_validity(client)
            eligible = [
                target
                for target in selected_targets
                if target.uid_validity == current_uid_validity
            ]
            mismatched = len(selected_targets) - len(eligible)
            updates: list[EmailBodyUpdate] = []
            failed = 0
            for target in eligible:
                try:
                    status, fetch_data = client.uid(
                        "fetch", str(target.uid), "(BODY.PEEK[])"
                    )
                    if status != "OK" or not fetch_data:
                        raise MailCheckError("旧邮件正文读取失败。")
                    _, raw_message = _extract_fetch_parts(fetch_data)
                    message = BytesParser(policy=policy.default).parsebytes(raw_message)
                    body_text, body_html, attachments = _message_content(message)
                    if not body_text and not body_html and not attachments:
                        raise MailCheckError("旧邮件没有可回填的正文或附件。")
                    updates.append(
                        EmailBodyUpdate(
                            source_id=target.source_id,
                            body_text=body_text,
                            body_preview=body_text[:300] if body_text else None,
                            body_html=body_html,
                            attachments=attachments,
                        )
                    )
                except Exception:
                    # One malformed or unavailable legacy message must not stop the batch.
                    failed += 1

            return MailBodyBackfillResult(
                updates=tuple(updates),
                selected=len(selected_targets),
                attempted=len(eligible),
                failed=failed,
                uid_validity_mismatched=mismatched,
            )
    except imaplib.IMAP4.error as exc:
        raise MailCheckError("IMAP 登录或旧邮件正文回填失败。") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise MailCheckError("IMAP 正文回填连接超时，请检查网络后重试。") from exc
    except ssl.SSLError as exc:
        raise MailCheckError(f"IMAP TLS 连接失败：{exc}") from exc
    except OSError as exc:
        raise MailCheckError(f"无法连接 {IMAP_HOST}:{IMAP_PORT}：{exc}") from exc


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
                    "(BODY.PEEK[] FLAGS INTERNALDATE)",
                )
                if status != "OK" or not fetch_data:
                    raise MailCheckError(f"UID {uid} 邮件读取失败。")
                metadata, message_bytes = _extract_fetch_parts(fetch_data)
                messages.append(
                    _parse_message(
                        email_address=email_address,
                        uid_validity=uid_validity,
                        uid=uid,
                        metadata=metadata,
                        headers=message_bytes,
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
