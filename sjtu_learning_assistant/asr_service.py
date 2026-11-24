"""独立 OpenAI 兼容语音识别：仅上传明确选择的音频，不自动重试。"""
from __future__ import annotations

import os
import logging
import re
import stat
import subprocess
import sys
import tempfile
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
MAX_AUDIO_SECONDS = 3600
SUPPORTED_AUDIO = frozenset({".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".webm", ".aac"})
_request_lock = threading.Lock()
_chunk_lock = threading.Lock()
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


def _regular_audio_file(metadata: os.stat_result) -> bool:
    # Windows reparse points need explicit rejection even without O_NOFOLLOW.
    return (stat.S_ISREG(metadata.st_mode)
            and not (getattr(metadata, "st_file_attributes", 0)
                     & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))


def _open_audio_file(path: Path):
    """Reject links before opening, then verify the descriptor is the selected file."""
    descriptor = None
    try:
        before = path.lstat()
        if not _regular_audio_file(before):
            raise ASRError("音频须为普通文件，不能是符号链接或重解析点。")
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        after = path.lstat()
        if (not _regular_audio_file(opened) or not _regular_audio_file(after)
                or not os.path.samestat(before, opened)
                or not os.path.samestat(after, opened)):
            raise ASRError("音频文件在打开时发生变化，请重新选择。")
        audio = os.fdopen(descriptor, "rb")
        descriptor = None  # The file object now owns the descriptor.
        return audio
    except OSError:
        raise ASRError("音频无法读取。") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def transcribe_audio(path: Path, *, endpoint: str, model: str, api_key: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    endpoint = validate_ai_base_url(endpoint)
    model = validate_asr_model(model)
    key = validate_ai_api_key(api_key)
    if path.suffix.lower() not in SUPPORTED_AUDIO:
        raise ASRError("请选择 WAV、MP3、M4A 等音频文件。")
    if not _request_lock.acquire(blocking=False):
        raise ASRError("已有音频正在转写，请等待完成。")
    try:
        with _open_audio_file(path) as audio:
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


def _duration_seconds(path: Path) -> float:
    """读取音频时长；未知或无效时返回 0，由体积上限兜底。"""
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as source:
                if source.getframerate() <= 0:
                    return 0.0
                return source.getnframes() / source.getframerate()
        except (wave.Error, EOFError, ZeroDivisionError):
            return 0.0
    try:
        output = subprocess.run(["/usr/bin/afinfo", str(path)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    found = re.search(r"estimated duration:\s*(\d+\.?\d*)", output.stdout)
    return float(found.group(1)) if found else 0.0


def _convert_to_wav(source: Path, destination: Path) -> None:
    """把压缩音频转成 16 kHz 单声道 PCM WAV，便于按帧切分；不联网、不重试。"""
    try:
        result = subprocess.run(
            ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(source), str(destination)],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        raise ASRError("无法调用系统音频转换；请改用 WAV 或安装可用解码器。") from None
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        raise ASRError("音频无法转换为可切分的 WAV。")


def _split_wav(source: Path, directory: Path, *, max_seconds: int, max_bytes: int) -> list[Path]:
    """按最长时长和最大字节双上限切分 PCM WAV；切点取整帧，保留全部采样。"""
    chunks: list[Path] = []
    try:
        with wave.open(str(source), "rb") as reader:
            params = reader.getparams()
            rate = reader.getframerate()
            frame_bytes = params.nchannels * params.sampwidth
            if rate <= 0 or frame_bytes <= 0:
                raise ASRError("WAV 采样参数无效。")
            by_seconds = int(max_seconds * rate)
            by_bytes = int(max_bytes / frame_bytes)
            limit = max(1, min(by_seconds, by_bytes))
            total = reader.getnframes()
            start = 0
            while start < total:
                end = min(total, start + limit)
                destination = directory / f"chunk-{len(chunks):03d}.wav"
                with wave.open(str(destination), "wb") as writer:
                    writer.setparams(params)
                    reader.setpos(start)
                    writer.writeframes(reader.readframes(end - start))
                chunks.append(destination)
                start = end
    except (wave.Error, EOFError, ZeroDivisionError):
        raise ASRError("WAV 音频格式无效，无法切分。") from None
    if not chunks:
        raise ASRError("音频没有可转写的内容。")
    return chunks


def transcribe_audio_chunked(path: Path, *, endpoint: str, model: str, api_key: str,
                             transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """单文件 ≤50 MB 且 ≤1 小时直接转写；否则本地转 WAV 并按双上限切分，逐段转写后合并。
    全程不自动重试，避免重复计费；临时切片在结束后删除。"""
    if path.suffix.lower() not in SUPPORTED_AUDIO:
        raise ASRError("请选择 WAV、MP3、M4A 等音频文件。")
    if not _chunk_lock.acquire(blocking=False):
        raise ASRError("已有音频正在切分转写，请等待完成。")
    try:
        try:
            size = path.stat().st_size
        except OSError:
            raise ASRError("音频无法读取。") from None
        duration = _duration_seconds(path)
        fits = 0 < size <= MAX_AUDIO_BYTES and (duration <= 0 or duration <= MAX_AUDIO_SECONDS)
        if fits:
            return transcribe_audio(path, endpoint=endpoint, model=model, api_key=api_key, transport=transport)
        with tempfile.TemporaryDirectory(prefix="sjtu-asr-chunks-") as raw:
            work = Path(raw)
            source = path
            if path.suffix.lower() != ".wav":
                source = work / "source.wav"
                _convert_to_wav(path, source)
            chunks = _split_wav(source, work, max_seconds=MAX_AUDIO_SECONDS, max_bytes=MAX_AUDIO_BYTES)
            pieces: list[str] = []
            for chunk in chunks:
                result = transcribe_audio(chunk, endpoint=endpoint, model=model, api_key=api_key, transport=transport)
                value = result.get("text") if isinstance(result, dict) else None
                if isinstance(value, str) and value.strip():
                    pieces.append(value.strip())
            merged = "\n".join(pieces)
            if not merged:
                raise ASRError("语音接口未返回可用文字。")
            return {"ok": True, "model": model, "text": merged, "chunks": len(chunks)}
    finally:
        _chunk_lock.release()
