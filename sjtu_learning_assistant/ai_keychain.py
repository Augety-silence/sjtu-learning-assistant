"""AI 分类 API key 的 macOS Keychain 专用存取。"""

from __future__ import annotations

import threading
from typing import Any

from sjtu_learning_assistant.credential_store import keychain_item_exists

AI_KEYCHAIN_SERVICE = "SJTU Learning Assistant - AI Classification"
AI_KEYCHAIN_ACCOUNT = "openai-api-key"
MAX_API_KEY_LENGTH = 4096

_credential_cache_lock = threading.RLock()
_cached_api_key: str | None = None


class AIKeychainError(RuntimeError):
    """不包含底层异常或凭据的可展示 Keychain 错误。"""


def load_keyring_module() -> Any:
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AIKeychainError("缺少 keyring 依赖，无法使用 AI 分类凭据。") from exc
    return keyring


def validate_ai_api_key(value: object) -> str:
    if type(value) is not str:
        raise AIKeychainError("AI API key 格式不正确。")
    key = value.strip()
    if (
        not key
        or len(key) > MAX_API_KEY_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in key)
    ):
        raise AIKeychainError("AI API key 格式不正确。")
    return key


def ai_api_key_saved() -> bool:
    return keychain_item_exists(AI_KEYCHAIN_SERVICE, AI_KEYCHAIN_ACCOUNT)


def clear_ai_credential_cache() -> None:
    """Clear the process-local AI credential cache."""
    global _cached_api_key
    with _credential_cache_lock:
        _cached_api_key = None


def save_ai_api_key(value: object, *, keyring_module: Any | None = None) -> None:
    global _cached_api_key
    key = validate_ai_api_key(value)
    if keyring_module is not None:
        try:
            keyring_module.set_password(AI_KEYCHAIN_SERVICE, AI_KEYCHAIN_ACCOUNT, key)
        except Exception:
            raise AIKeychainError("无法保存 AI API key 到 macOS Keychain。") from None
        return

    with _credential_cache_lock:
        backend = load_keyring_module()
        try:
            backend.set_password(AI_KEYCHAIN_SERVICE, AI_KEYCHAIN_ACCOUNT, key)
        except Exception:
            raise AIKeychainError("无法保存 AI API key 到 macOS Keychain。") from None
        _cached_api_key = key


def get_ai_api_key(*, keyring_module: Any | None = None) -> str | None:
    global _cached_api_key
    if keyring_module is not None:
        try:
            value = keyring_module.get_password(
                AI_KEYCHAIN_SERVICE, AI_KEYCHAIN_ACCOUNT
            )
        except Exception:
            raise AIKeychainError("无法读取 AI API key。") from None
        if value is None:
            return None
        return validate_ai_api_key(value)

    with _credential_cache_lock:
        if _cached_api_key is not None:
            return _cached_api_key
        backend = load_keyring_module()
        try:
            value = backend.get_password(AI_KEYCHAIN_SERVICE, AI_KEYCHAIN_ACCOUNT)
        except Exception:
            raise AIKeychainError("无法读取 AI API key。") from None
        if value is None:
            return None
        key = validate_ai_api_key(value)
        _cached_api_key = key
        return key


def delete_ai_api_key(*, keyring_module: Any | None = None) -> None:
    global _cached_api_key
    if keyring_module is not None:
        try:
            keyring_module.delete_password(AI_KEYCHAIN_SERVICE, AI_KEYCHAIN_ACCOUNT)
        except Exception:
            raise AIKeychainError("无法从 macOS Keychain 删除 AI API key。") from None
        return

    with _credential_cache_lock:
        _cached_api_key = None
        backend = load_keyring_module()
        try:
            backend.delete_password(AI_KEYCHAIN_SERVICE, AI_KEYCHAIN_ACCOUNT)
        except Exception:
            raise AIKeychainError("无法从 macOS Keychain 删除 AI API key。") from None
