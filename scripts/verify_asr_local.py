"""仅上传本机生成的短测试音频；不读取课程音频，不输出密钥。"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.asr_service import ASRError, get_asr_api_key, transcribe_audio
from sjtu_learning_assistant.local_settings import SettingsStore


def main():
    settings = SettingsStore().load()
    key = get_asr_api_key()
    if not key:
        raise SystemExit("独立语音密钥尚未保存")
    with tempfile.TemporaryDirectory(prefix="sjtu-asr-probe-") as directory:
        audio = Path(directory) / "sample.wav"
        subprocess.run(["/usr/bin/say", "--data-format=LEI16@16000", "-o", str(audio), "Hello. This is a short speech recognition test."], check=True, timeout=20)
        started = time.monotonic()
        try:
            result = transcribe_audio(audio, endpoint=settings.asr_endpoint, model=settings.asr_model, api_key=key)
        except ASRError as error:
            print(json.dumps({"model": settings.asr_model, "ok": False, "seconds": round(time.monotonic() - started, 2), "error": str(error)}, ensure_ascii=False))
            return 1
        print(json.dumps({**result, "seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
