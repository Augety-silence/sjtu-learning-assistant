"""AI 分类 API key 的 macOS Keychain 专用存取。"""

from __future__ import annotations

from typing import Any

AI_KEYCHAIN_SERVICE = "SJTU Learning Assistant - AI Classification"
AI_KEYCHAIN_ACCOUNT = "openai-api-key"
MAX_API_KEY_LENGTH = 4096


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


def save_ai_api_key(value: object, *, keyring_module: Any | None = None) -> None:
    key = validate_ai_api_key(value)
    backend = keyring_module or load_keyring_module()
    try:
        backend.set_password(AI_KEYCHAIN_SERVICE, AI_KEYCHAIN_ACCOUNT, key)
    except Exception:
        raise AIKeychainError("无法保存 AI API key 到 macOS Keychain。") from None


def get_ai_api_key(*, keyring_module: Any | None = None) -> str | None:
    backend = keyring_module or load_keyring_module()
    try:
        value = backend.get_password(AI_KEYCHAIN_SERVICE, AI_KEYCHAIN_ACCOUNT)
    except Exception:
        raise AIKeychainError("无法读取 AI API key。") from None
    if value is None:
        return None
    return validate_ai_api_key(value)
