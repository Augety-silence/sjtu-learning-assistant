"""独立 ASR 配置、安全存储及真实 multipart 格式的离线回归。"""
import errno
import json
import os
import stat
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from sjtu_learning_assistant import asr_service
from sjtu_learning_assistant.asr_service import (
    ASRError, ASR_KEYCHAIN_SERVICE, MAX_AUDIO_BYTES, delete_asr_api_key,
    get_asr_api_key, save_asr_api_key, transcribe_audio,
)
from sjtu_learning_assistant.ai_keychain import AI_KEYCHAIN_SERVICE
from sjtu_learning_assistant.local_settings import ASR_KEYS, LocalSettings, SettingsError, SettingsStore


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "private-class-name.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 16000)
    return path


def invoke(audio, handler, **kwargs):
    return transcribe_audio(audio, endpoint="https://api.siliconflow.cn/v1/audio/transcriptions", model="Qwen/Qwen3-ASR-1.7B", api_key="synthetic-key", transport=httpx.MockTransport(handler), **kwargs)


def test_old_settings_migrate_without_changing_llm(tmp_path):
    path = tmp_path / "settings.json"
    original = replace(LocalSettings(), ai_enabled=True, ai_key_saved=True, ai_model="minimax")
    payload = original.to_dict()
    for key in ASR_KEYS:
        payload.pop(key)
    path.write_text(json.dumps(payload))
    updated = SettingsStore(path).load(environ={})
    assert updated.ai_model == "minimax" and updated.ai_key_saved and updated.ai_enabled
    assert not updated.asr_key_saved
    assert updated.asr_model == "XingChenAGI/XingChenASR-V3.2-Ultra"
    SettingsStore(path).save(updated)
    assert set(json.loads(path.read_text())) == set(updated.to_dict())


def test_separate_keychain_service_and_no_echo():
    backend = Mock()
    backend.get_password.return_value = "test-not-a-real-secret"
    save_asr_api_key("test-not-a-real-secret", keyring_module=backend)
    assert ASR_KEYCHAIN_SERVICE != AI_KEYCHAIN_SERVICE
    assert backend.set_password.call_args.args[0] == ASR_KEYCHAIN_SERVICE
    assert get_asr_api_key(keyring_module=backend) == "test-not-a-real-secret"
    delete_asr_api_key(keyring_module=backend)
    assert backend.delete_password.call_args.args[0] == ASR_KEYCHAIN_SERVICE
    backend.set_password.side_effect = RuntimeError("test-not-a-real-secret")
    with pytest.raises(ASRError) as error:
        save_asr_api_key("test-not-a-real-secret", keyring_module=backend)
    assert "test-not-a-real-secret" not in str(error.value)


def test_multipart_upload_no_filename_disclosure(audio):
    def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers.get("authorization") == "Bearer synthetic-key"
        assert b"Qwen/Qwen3-ASR-1.7B" in request.content
        assert b'filename="audio.wav"' in request.content
        assert b"private-class-name" not in request.content
        return httpx.Response(200, json={"text": "  测试文字。 "})
    result = invoke(audio, handler)
    assert result == {"ok": True, "model": "Qwen/Qwen3-ASR-1.7B", "text": "测试文字。"}


@pytest.mark.parametrize("code", [301, 400, 401, 402, 403, 404, 413, 429, 500])
def test_failure_does_not_retry_or_echo_server(audio, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(code, text="server-body-secret", headers={"Location": "https://other.example"})
    with pytest.raises(ASRError) as error:
        invoke(audio, handler)
    assert len(calls) == 1
    assert "server-body-secret" not in str(error.value)
    assert f"HTTP {code}" in str(error.value)


@pytest.mark.parametrize("result", [{}, {"text": ""}, {"text": 2}, [], {"text": "x" * 1_000_001}])
def test_bad_results(audio, result):
    with pytest.raises(ASRError):
        invoke(audio, lambda request: httpx.Response(200, json=result))


def test_timeout_releases_lock(audio):
    def timeout(request):
        raise httpx.ReadTimeout("secret-error", request=request)
    with pytest.raises(ASRError, match="未自动重试"):
        invoke(audio, timeout)
    assert invoke(audio, lambda r: httpx.Response(200, json={"text": "ok"}))["ok"]


@pytest.mark.parametrize("nofollow", (True, False), ids=("native", "without-nofollow"))
def test_size_and_symlink_no_request(audio, tmp_path, monkeypatch, nofollow):
    if not nofollow:
        monkeypatch.delattr(asr_service.os, "O_NOFOLLOW", raising=False)
    handler = Mock(return_value=httpx.Response(200, json=dict(text="unexpected upload")))
    huge = tmp_path / "huge.mp3"
    with huge.open("wb") as stream:
        stream.truncate(MAX_AUDIO_BYTES + 1)
    with pytest.raises(ASRError):
        invoke(huge, handler)
    handler.assert_not_called()
    link = tmp_path / "link.wav"
    try:
        link.symlink_to(audio)
    except NotImplementedError:
        pytest.skip("Platform does not support symlinks")
    except OSError as exc:
        if (getattr(exc, "winerror", None) == 1314
                or exc.errno in (errno.EPERM, errno.EACCES, errno.ENOSYS, errno.ENOTSUP)):
            pytest.skip("Symlink creation is unavailable or requires platform privileges")
        raise
    with pytest.raises(ASRError):
        invoke(link, handler)
    handler.assert_not_called()


@pytest.mark.parametrize("kind", ("empty", "oversize", "directory"))
def test_invalid_audio_no_request_without_nofollow(tmp_path, monkeypatch, kind):
    monkeypatch.delattr(asr_service.os, "O_NOFOLLOW", raising=False)
    path = tmp_path / "invalid.mp3"
    if kind == "directory":
        path.mkdir()
    else:
        with path.open("wb") as stream:
            stream.truncate(MAX_AUDIO_BYTES + 1 if kind == "oversize" else 0)
    handler = Mock(return_value=httpx.Response(200, json=dict(text="unexpected upload")))
    with pytest.raises(ASRError):
        invoke(path, handler)
    handler.assert_not_called()


def test_regular_audio_without_nofollow(audio, monkeypatch):
    monkeypatch.delattr(asr_service.os, "O_NOFOLLOW", raising=False)
    handler = Mock(return_value=httpx.Response(200, json=dict(text="ok")))
    assert invoke(audio, handler).get("text") == "ok"
    handler.assert_called_once()


def test_reparse_point_no_open_or_request(audio, monkeypatch):
    metadata = SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
    monkeypatch.setattr(Path, "lstat", lambda self: metadata)
    opener = Mock(side_effect=AssertionError("Reparse point must not be opened"))
    monkeypatch.setattr(asr_service.os, "open", opener)
    handler = Mock(return_value=httpx.Response(200, json=dict(text="unexpected upload")))
    with pytest.raises(ASRError):
        invoke(audio, handler)
    opener.assert_not_called()
    handler.assert_not_called()


@pytest.mark.parametrize("phase", ("descriptor", "path-recheck"))
def test_audio_identity_race_closes_descriptor_without_request(audio, tmp_path, monkeypatch, phase):
    monkeypatch.delattr(asr_service.os, "O_NOFOLLOW", raising=False)
    replacement = tmp_path / "replacement.wav"
    replacement.write_bytes(audio.read_bytes())
    original_open, original_lstat = os.open, Path.lstat
    descriptors = list()
    def changed_open(path, flags):
        descriptor = original_open(replacement if phase == "descriptor" else path, flags)
        descriptors.append(descriptor)
        if phase == "path-recheck":
            monkeypatch.setattr(Path, "lstat", lambda self: replacement.stat() if self == audio else original_lstat(self))
        return descriptor
    monkeypatch.setattr(asr_service.os, "open", changed_open)
    handler = Mock(return_value=httpx.Response(200, json=dict(text="unexpected upload")))
    with pytest.raises(ASRError, match="发生变化"):
        invoke(audio, handler)
    handler.assert_not_called()
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors.pop())


@pytest.mark.parametrize("endpoint", ["http://example.com", "https://example.com:99999", "https://user:password@example.com", "https://example.com?q=secret"])
def test_endpoint_safety(tmp_path, endpoint):
    with pytest.raises(SettingsError):
        SettingsStore(tmp_path / "settings.json").update({"asr_endpoint": endpoint})


def test_model_not_in_chat_whitelist_but_valid_for_asr(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.update({"asr_model": "FunAudioLLM/SenseVoiceSmall"}).asr_model == "FunAudioLLM/SenseVoiceSmall"
    with pytest.raises(SettingsError):
        store.update({"ai_model": "FunAudioLLM/SenseVoiceSmall"})


def test_long_wav_rejected_before_upload(tmp_path):
    path = tmp_path / "long.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 1, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 3601)
    handler = Mock()
    with pytest.raises(ASRError, match="1 小时"):
        invoke(path, handler)
    handler.assert_not_called()


def test_duplicate_request_is_not_sent(audio):
    def handler(request):
        with pytest.raises(ASRError, match="已有音频"):
            invoke(audio, Mock())
        return httpx.Response(200, json={"text": "ok"})
    assert invoke(audio, handler)["ok"]


def test_bridge_cannot_read_frontend_chosen_path(tmp_path):
    from desktop_app import DesktopBridge
    service = Mock()
    service.transcribe_selected_audio.return_value = {"text": "ok"}
    picker = Mock(return_value=str(tmp_path / "selected.wav"))
    bridge = DesktopBridge(service, file_picker=picker)
    assert not bridge.invoke("settings_asr_transcribe", {"path": "/etc/passwd"})["ok"]
    picker.assert_not_called()
    service.transcribe_selected_audio.assert_not_called()
    assert bridge.invoke("settings_asr_transcribe", {})["ok"]
    service.transcribe_selected_audio.assert_called_once_with(tmp_path / "selected.wav")


def test_bridge_cancel_does_not_upload():
    from desktop_app import DesktopBridge
    service = Mock()
    bridge = DesktopBridge(service, file_picker=lambda: None)
    assert bridge.invoke("settings_asr_transcribe", {})["data"] == {"cancelled": True}
    service.transcribe_selected_audio.assert_not_called()
