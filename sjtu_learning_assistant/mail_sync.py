"""SJTU Mail synchronization helpers and Keychain-backed credentials."""
from __future__ import annotations

import getpass
import imaplib
import re
import socket
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime

from sjtu_learning_assistant.credential_store import MAIL_SERVICE as KEYCHAIN_SERVICE

IMAP_HOST = "mail.sjtu.edu.cn"
IMAP_PORT = 993
DEFAULT_LIMIT = 10
DEFAULT_TIMEOUT_SECONDS = 20

_credential_cache_lock = threading.RLock()
_cached_passwords: dict[str, str] = {}


class MailCheckError(RuntimeError):
    """A user-facing error raised during mail synchronization."""


@dataclass(frozen=True)
class MessageSummary:
    uid: str
    subject: str
    sender: str
    sent_at: str


def normalize_email(value: str) -> str:
    email_address = value.strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+", email_address):
        raise MailCheckError("邮箱地址格式不正确。")
    return email_address


def prompt_email() -> str:
    try:
        return normalize_email(input("请输入上海交大邮箱地址："))
    except (EOFError, KeyboardInterrupt) as exc:
        raise MailCheckError("已取消输入。") from exc


def load_keyring_module():
    try:
        import keyring  # type: ignore[import-not-found]
        from keyring.errors import KeyringError  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MailCheckError(
            "缺少 keyring 依赖。请先运行：python3 -m pip install -r requirements.txt"
        ) from exc
    return keyring, KeyringError


def clear_mail_credential_cache() -> None:
    """Clear the process-local mail credential cache."""
    with _credential_cache_lock:
        _cached_passwords.clear()


def get_password(email_address: str, use_keychain: bool) -> tuple[str, bool]:
    """Return ``(password, came_from_keychain)``."""
    if use_keychain:
        with _credential_cache_lock:
            cached = _cached_passwords.get(email_address)
            if cached is not None:
                return cached, True
            keyring, KeyringError = load_keyring_module()
            try:
                stored = keyring.get_password(KEYCHAIN_SERVICE, email_address)
            except KeyringError as exc:
                print(f"提示：暂时无法读取 Keychain，将改为安全输入（{exc}）。")
            else:
                if stored:
                    _cached_passwords[email_address] = stored
                    print("已从 macOS Keychain 读取邮箱密码。")
                    return stored, True

    try:
        password = getpass.getpass("请输入邮箱密码（输入内容不会显示）：")
    except (EOFError, KeyboardInterrupt) as exc:
        raise MailCheckError("已取消密码输入。") from exc
    if not password:
        raise MailCheckError("密码不能为空。")
    return password, False


def save_password(email_address: str, password: str) -> None:
    keyring, KeyringError = load_keyring_module()
    with _credential_cache_lock:
        try:
            keyring.set_password(KEYCHAIN_SERVICE, email_address, password)
        except KeyringError as exc:
            print(f"提示：登录已成功，但密码未能保存到 Keychain（{exc}）。")
        else:
            _cached_passwords[email_address] = password
            print("密码已安全保存到 macOS Keychain。")


def delete_password(email_address: str) -> None:
    with _credential_cache_lock:
        _cached_passwords.pop(email_address, None)
        keyring, KeyringError = load_keyring_module()
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, email_address)
        except KeyringError as exc:
            missing_error = getattr(
                getattr(keyring, "errors", None), "PasswordDeleteError", None
            )
            if isinstance(missing_error, type) and isinstance(exc, missing_error):
                print("Keychain 中没有找到该邮箱的已保存密码。")
                return
            raise MailCheckError(f"无法删除 Keychain 密码：{exc}") from exc
    print("已从 macOS Keychain 删除该邮箱密码。")


def decode_mime_text(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    try:
        return str(make_header(decode_header(value))).strip() or fallback
    except (LookupError, UnicodeError, ValueError):
        return value.strip() or fallback


def format_sender(value: str | None) -> str:
    decoded = decode_mime_text(value, "（未知发件人）")
    display_name, address = parseaddr(decoded)
    display_name = decode_mime_text(display_name, "")
    if display_name and address:
        return f"{display_name} <{address}>"
    return address or decoded


def format_sent_at(value: str | None) -> str:
    if not value:
        return "（时间未知）"
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        local_time: datetime = parsed.astimezone()
        return local_time.strftime("%Y-%m-%d %H:%M %Z")
    except (TypeError, ValueError, OverflowError):
        return value


def extract_header_bytes(fetch_data: list[object]) -> bytes:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    raise MailCheckError("服务器返回了无法识别的邮件头数据。")


def fetch_recent_messages(
    client: imaplib.IMAP4_SSL, limit: int
) -> list[MessageSummary]:
    status, select_data = client.select("INBOX", readonly=True)
    if status != "OK":
        raise MailCheckError("无法以只读方式打开 INBOX。")

    status, search_data = client.uid("search", None, "ALL")
    if status != "OK" or not search_data:
        raise MailCheckError("无法读取 INBOX 邮件列表。")

    all_uids = search_data[0].split()
    if not all_uids:
        return []

    recent_uids = list(reversed(all_uids[-limit:]))
    summaries: list[MessageSummary] = []
    for uid_bytes in recent_uids:
        status, fetch_data = client.uid(
            "fetch",
            uid_bytes,
            "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])",
        )
        if status != "OK" or not fetch_data:
            print(f"提示：UID {uid_bytes.decode(errors='replace')} 读取失败，已跳过。")
            continue

        raw_headers = extract_header_bytes(fetch_data)
        message = BytesParser(policy=policy.compat32).parsebytes(raw_headers)
        summaries.append(
            MessageSummary(
                uid=uid_bytes.decode("ascii", errors="replace"),
                subject=decode_mime_text(message.get("Subject"), "（无主题）"),
                sender=format_sender(message.get("From")),
                sent_at=format_sent_at(message.get("Date")),
            )
        )
    return summaries


def connect_and_fetch(
    email_address: str, password: str, limit: int
) -> list[MessageSummary]:
    tls_context = ssl.create_default_context()
    try:
        with imaplib.IMAP4_SSL(
            IMAP_HOST,
            IMAP_PORT,
            ssl_context=tls_context,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ) as client:
            client.login(email_address, password)
            return fetch_recent_messages(client, limit)
    except imaplib.IMAP4.error as exc:
        raise MailCheckError(
            "IMAP 登录或读取失败。请检查邮箱地址、jAccount 密码，以及邮箱是否允许 IMAP 登录。"
        ) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise MailCheckError(
            f"连接超时：{IMAP_HOST}:{IMAP_PORT}，请检查网络后重试。"
        ) from exc
    except ssl.SSLError as exc:
        raise MailCheckError(f"TLS 证书或加密连接失败：{exc}") from exc
    except OSError as exc:
        raise MailCheckError(f"无法连接 {IMAP_HOST}:{IMAP_PORT}：{exc}") from exc
