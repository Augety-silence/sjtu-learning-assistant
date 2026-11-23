"""独立 OpenAI 兼容语音识别：仅上传明确选择的音频，不自动重试。"""
from __future__ import annotations

import os
import stat
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


class ASRError(RuntimeError):
    """错误不含密钥、音频内容或服务端原始响应。"""


def asr_api_key_saved() -> bool:
    return keychain_item_exists(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT)


def save_asr_api_key(value: object, *, keyring_module: Any = None) -> None:
    key = validate_ai_api_key(value)
    try:
        (keyring_module or load_keyring_module()).set_password(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT, key)
    except Exception:
        raise ASRError("无法将语音识别密钥保存到系统凭据库。") from None


def get_asr_api_key(*, keyring_module: Any = None) -> str | None:
    try:
        value = (keyring_module or load_keyring_module()).get_password(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT)
        return validate_ai_api_key(value) if value else None
    except Exception:
        raise ASRError("无法读取语音识别密钥。") from None


def delete_asr_api_key(*, keyring_module: Any = None) -> None:
    try:
        backend = keyring_module or load_keyring_module()
        if backend.get_password(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT) is not None:
            backend.delete_password(ASR_KEYCHAIN_SERVICE, ASR_KEYCHAIN_ACCOUNT)
    except Exception:
        raise ASRError("无法删除语音识别密钥。") from None


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
            messages = {401: "密钥无效或未获授权。", 403: "账户或模型无访问权限。", 404: "接口或模型不存在，请核对模型名称。", 429: "语音接口限流，请稍后手动重试。", 413: "音频超过服务端大小限制。", 400: "模型或音频格式不受支持。"}
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
