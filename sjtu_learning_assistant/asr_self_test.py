"""在真实签名应用身份内验收钥匙串和合成音频，不访问课堂资料。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from sjtu_learning_assistant.asr_service import (
    ASRError, _credential_backend, clear_asr_credential_cache,
    get_asr_api_key, save_asr_api_key, transcribe_audio,
)
from sjtu_learning_assistant.local_settings import SettingsStore


def exception_status(exc: Exception) -> int | None:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        status = getattr(exc, "status", None)
        if type(status) is int and -100000 <= status < 0:
            return status
        if exc.args and type(exc.args[0]) is int and -100000 <= exc.args[0] < 0:
            return exc.args[0]
        exc = exc.__cause__ or exc.__context__
    return None


def run(report_path: Path) -> int:
    # 不覆盖已有文件或跟随链接；报告不含凭据与课堂资料。
    descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    result = {"ok": False, "frozen_app": True}
    stage = "synthetic_keychain"
    try:
        import Security
        Security.SecKeychainSetUserInteractionAllowed(False)
        from sjtu_learning_assistant.native_asr_keychain import NativeASRKeychain
        backend = NativeASRKeychain(allow_interaction=False)
        service = "SJTU Learning Assistant - ASR Synthetic Probe"
        account = "probe-" + uuid.uuid4().hex
        sample = "synthetic-only-" + uuid.uuid4().hex
        try:
            backend.set_password(service, account, sample)
            assert backend.get_password(service, account) == sample
            backend.set_password(service, account, sample + "-updated")
            assert backend.get_password(service, account) == sample + "-updated"
            result["native_create_update_read"] = True
        finally:
            backend.delete_password(service, account)
        stage = "configured_keychain"
        clear_asr_credential_cache()
        key = get_asr_api_key(keyring_module=backend)
        if not key:
            raise ASRError("尚未保存语音密钥。")
        save_asr_api_key(key, keyring_module=backend)
        clear_asr_credential_cache()
        assert get_asr_api_key(keyring_module=backend) == key
        result["configured_save_read"] = True
        stage = "synthetic_transcription"
        settings = SettingsStore().load()
        with tempfile.TemporaryDirectory(prefix="sjtu-asr-native-") as directory:
            audio = Path(directory) / "sample.wav"
            subprocess.run(["/usr/bin/say", "--data-format=LEI16@16000", "-o", str(audio), "Hello. This is a short speech recognition test."], check=True, capture_output=True, timeout=20)
            started = time.monotonic()
            response = transcribe_audio(audio, endpoint=settings.asr_endpoint, model=settings.asr_model, api_key=key)
            result.update(response)
            result["seconds"] = round(time.monotonic() - started, 2)
    except Exception as exc:
        result.update(ok=False, stage=stage, status=exception_status(exc), exception_type=type(exc).__name__)
        if isinstance(exc, ASRError):
            result["error"] = str(exc)
    finally:
        try:
            Security.SecKeychainSetUserInteractionAllowed(True)
        except (NameError, AttributeError):
            pass
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
    return 0 if result["ok"] else 1
