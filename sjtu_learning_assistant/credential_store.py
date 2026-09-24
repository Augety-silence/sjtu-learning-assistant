"""Centralized, secret-safe access to Canvas and mail credentials."""

from __future__ import annotations

import threading
from typing import Any

CANVAS_SERVICE = "SJTU Learning Assistant - oc.sjtu.edu.cn"
CANVAS_ACCOUNT = "canvas-access-token"
MAIL_SERVICE = "SJTU Learning Assistant - mail.sjtu.edu.cn"
MAX_SECRET_LENGTH = 4096

_lock = threading.RLock()


class CredentialStoreError(RuntimeError):
    """A bounded error that never includes credential values."""


def _keyring() -> Any:
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CredentialStoreError("缺少 keyring 依赖，无法访问 macOS Keychain。") from exc
    return keyring


def _secret(value: object, label: str) -> str:
    if type(value) is not str:
        raise CredentialStoreError(f"{label}格式不正确。")
    clean = value.strip()
    if (
        not clean
        or len(clean) > MAX_SECRET_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in clean)
    ):
        raise CredentialStoreError(f"{label}格式不正确。")
    return clean


def normalize_mail_account(value: object) -> str:
    if type(value) is not str:
        raise CredentialStoreError("邮箱账号格式不正确。")
    account = value.strip()
    if not account or len(account) > 320 or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in account
    ):
        raise CredentialStoreError("邮箱账号格式不正确。")
    return account


def _exists(service: str, account: str) -> bool:
    try:
        with _lock:
            return bool(_keyring().get_password(service, account))
    except Exception:
        raise CredentialStoreError("无法读取 macOS Keychain 中的配置状态。") from None


def _save(service: str, account: str, value: object, label: str) -> None:
    clean = _secret(value, label)
    try:
        with _lock:
            _keyring().set_password(service, account, clean)
    except Exception:
        raise CredentialStoreError(f"无法将{label}保存到 macOS Keychain。") from None


def _delete(service: str, account: str, label: str) -> None:
    backend = _keyring()
    try:
        with _lock:
            existing = backend.get_password(service, account)
            if existing is not None:
                backend.delete_password(service, account)
    except Exception:
        raise CredentialStoreError(f"无法从 macOS Keychain 删除{label}。") from None


def canvas_token_saved() -> bool:
    return _exists(CANVAS_SERVICE, CANVAS_ACCOUNT)


def save_canvas_token(value: object) -> None:
    _save(CANVAS_SERVICE, CANVAS_ACCOUNT, value, "Canvas Access Token")
    from sjtu_learning_assistant.canvas_sync import clear_canvas_credential_cache

    clear_canvas_credential_cache()


def delete_canvas_token() -> None:
    _delete(CANVAS_SERVICE, CANVAS_ACCOUNT, "Canvas Access Token")
    from sjtu_learning_assistant.canvas_sync import clear_canvas_credential_cache

    clear_canvas_credential_cache()


def mail_password_saved(account: object) -> bool:
    if type(account) is not str or not account.strip():
        return False
    return _exists(MAIL_SERVICE, normalize_mail_account(account))


def save_mail_password(account: object, value: object) -> None:
    normalized = normalize_mail_account(account)
    _save(MAIL_SERVICE, normalized, value, "邮箱密码")
    from sjtu_learning_assistant.mail_sync import clear_mail_credential_cache

    clear_mail_credential_cache()


def delete_mail_password(account: object) -> None:
    normalized = normalize_mail_account(account)
    _delete(MAIL_SERVICE, normalized, "邮箱密码")
    from sjtu_learning_assistant.mail_sync import clear_mail_credential_cache

    clear_mail_credential_cache()
