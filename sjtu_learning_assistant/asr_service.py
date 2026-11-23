"""独立 OpenAI 兼容语音识别：仅上传明确选择的音频，不自动重试。"""
from __future__ import annotations

import os
import logging
import stat
import sys
import threading
import wave
from pathlib import Path
from typing import Any

import httpx

from sjtu_learning_assistant.ai_keychain import load_keyring_module, validate_ai_api_key
from sjtu_learning_assistant.credential_store import keychain_item_exists
from sjtu_learning_assistant.local_settings import validate_ai_base_url, validate_asr_model

ASR_KEYCHAIN_SERVICE = "SJTU Learning Assistant - Speech Recognition"
ASR_KEYCHAIN_ACCOUNT = "siliconflow-api-key"
MAX_AUDIO_BYTES = 50 * 1024 * 1024
SUPPORTED_AUDIO = frozenset({".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".webm", ".aac"})
_request_lock = threading.Lock()
_credential_lock = threading.RLock()
_cached_asr_key: str | None = None
LOGGER = logging.getLogger(__name__)


class ASRError(RuntimeError):
    """错误不含密钥、音频内容或服务端原始响应。"""


def asr_api_key_saved() -> bool:
    return keychain_item_exists(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT)


def _credential_backend():
    if sys.platform == "darwin":
        from sjtu_learning_assistant.native_asr_keychain import NativeASRKeychain
        return NativeASRKeychain()
    return load_keyring_module()


def _credential_error(stage: str, exc: Exception) -> ASRError:
    status = getattr(exc, "status", None)
    if type(status) is not int or not -100000 <= status < 0:
        status = None
    LOGGER.warning("asr_credential stage=%s status=%s exception_type=%s", stage, status, type(exc).__name__)
    hints = {
        -128: "钥匙串授权已取消，请在系统授权窗口允许此 App 访问语音密钥后重试。",
        -25293: "钥匙串拒绝当前 App 的访问，请在系统授权窗口允许访问后重试。",
        -25308: "钥匙串暂时不允许交互，请解锁登录钥匙串后重试。",
        -67030: "当前 App 的签名或应用信息无效，请重新安装签名完整的版本。",
        -25299: "钥匙串条目已存在，未删除旧密钥；请重试保存。",
    }
    hint = hints.get(status, "请检查系统钥匙串是否解锁并允许此 App 访问。")
    return ASRError(f"语音密钥{stage}失败" + (f"（Keychain {status}）" if status else "") + f"。{hint}")


def clear_asr_credential_cache() -> None:
    global _cached_asr_key
    with _credential_lock:
        _cached_asr_key = None


def save_asr_api_key(value: object, *, keyring_module: Any = None) -> None:
    global _cached_asr_key
    key = validate_ai_api_key(value)
    with _credential_lock:
        try:
            backend = keyring_module or _credential_backend()
            backend.set_password(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT, key)
            if keyring_module is None:
                _cached_asr_key = None
            if backend.get_password(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT) != key:
                raise ASRError("语音密钥保存后回读校验失败，未标记配置完成。")
        except ASRError:
            raise
        except Exception as exc:
            raise _credential_error("保存", exc) from None
        if keyring_module is None:
            _cached_asr_key = key


def get_asr_api_key(*, keyring_module: Any = None) -> str | None:
    global _cached_asr_key
    with _credential_lock:
        if keyring_module is None and _cached_asr_key:
            return _cached_asr_key
        try:
            value = (keyring_module or _credential_backend()).get_password(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT)
            key = validate_ai_api_key(value) if value else None
        except Exception as exc:
            raise _credential_error("读取", exc) from None
        if keyring_module is None:
            _cached_asr_key = key
        return key


def delete_asr_api_key(*, keyring_module: Any = None) -> None:
    global _cached_asr_key
    with _credential_lock:
        try:
            backend = keyring_module or _credential_backend()
            if backend.get_password(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT) is not None:
                backend.delete_password(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT)
        except Exception as exc:
            raise _credential_error("删除", exc) from None
        if keyring_module is None:
            _cached_asr_key = None


def transcribe_audio(path: Path, *, endpoint: str, model: str, api_key: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    endpoint = validate_ai_base_url(endpoint)
    model = validate_asr_model(model)
    key = validate_ai_api_key(api_key)
    if path.suffix.lower() not in SUPPORTED_AUDIO:
        raise ASRError("请选择 WAV、MP3、M4A 等音频文件。")
    if not _request_lock.acquire(blocking=False):
        raise ASRError("已有音频正在转写，请等待完成。")
    try:
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            raise ASRError("音频无法读取。") from None
        with os.fdopen(descriptor, "rb") as audio:
            metadata = os.fstat(audio.fileno())
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_AUDIO_BYTES:
                raise ASRError("音频须为普通文件，大小在 0—50 MB 之间。")
            if path.suffix.lower() == ".wav":
                try:
                    with wave.open(audio, "rb") as source:
                        duration = source.getnframes() / source.getframerate()
                    audio.seek(0)
                except (wave.Error, EOFError, ZeroDivisionError):
                    raise ASRError("WAV 音频格式无效。") from None
                if duration > 3600:
                    raise ASRError("单段音频不能超过 1 小时。")
            try:
                with httpx.Client(transport=transport, timeout=httpx.Timeout(300, connect=15), follow_redirects=False, trust_env=False) as client:
                    response = client.post(endpoint, headers={"Authorization": f"Bearer {key}"},
                                           files={"file": ("audio" + path.suffix.lower(), audio, "application/octet-stream")}, data={"model": model})
            except httpx.TimeoutException:
                raise ASRError("语音识别超时；未自动重试，避免重复计费。") from None
            except httpx.HTTPError:
                raise ASRError("无法连接语音识别接口，请检查网络。") from None
        if response.status_code != 200:
            messages = {401: "密钥无效或未获授权。", 402: "语音服务账户欠费或可用余额不足，请核对服务商余额后手动重试。", 403: "账户或模型无访问权限。", 404: "接口或模型不存在，请核对模型名称。", 429: "语音接口限流，请稍后手动重试。", 413: "音频超过服务端大小限制。", 400: "模型或音频格式不受支持。"}
            raise ASRError(f"语音识别失败（HTTP {response.status_code}）：" + messages.get(response.status_code, "服务暂不可用；未自动重试。"))
        try:
            result = response.json()
        except ValueError:
            raise ASRError("语音接口返回了无效结果。") from None
        if not isinstance(result, dict) or not isinstance(result.get("text"), str) or not result["text"].strip():
            raise ASRError("语音接口未返回可用文字。")
        if len(result["text"]) > 1_000_000:
            raise ASRError("语音接口返回文字过长。")
        return {"ok": True, "model": model, "text": result["text"].strip()}
    finally:
        _request_lock.release()
