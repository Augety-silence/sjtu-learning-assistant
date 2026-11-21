#!/usr/bin/env python3
"""已保存学校API的合成输入/流式用量探针；只写独立临时计量库。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine
from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient
from sjtu_learning_assistant.ai_keychain import get_ai_api_key
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.models import Base
from sjtu_learning_assistant.provider_usage import ProviderUsageLedger


def main():
    settings = SettingsStore().load()
    if settings.ai_base_url.rstrip("/") != "https://models.sjtu.edu.cn/api/v1":
        print(json.dumps(dict(ok=False, reason="configured_endpoint_not_school")))
        return 1
    key = get_ai_api_key()
    if not key:
        print(json.dumps(dict(ok=False, reason="credential_missing")))
        return 1
    with tempfile.TemporaryDirectory(prefix="sjtu-usage-probe-") as tmp:
        engine = create_engine("sqlite:///"+str(Path(tmp)/"usage.db"))
        Base.metadata.create_all(engine)
        ledger = ProviderUsageLedger(engine)
        client = OpenAIClassificationClient(api_key=key, base_url=settings.ai_base_url,
            model="deepseek-chat", usage_recorder=ledger.record)
        try:
            messages = [dict(role="user", content="这是软件计量合成测试，不包含学习资料。请只回复：测试通过。")]
            first = client.chat_completion(messages, max_tokens=256)
            second = client.chat_completion_stream(messages, max_tokens=256, on_delta=lambda *args: None)
            result = ledger.summary()["periods"]["all"]
            assert result["calls"] == 2
            assert result["metrics"]["input"]["reported_calls"] == 2
            assert result["metrics"]["output"]["reported_calls"] == 2
            print(json.dumps(dict(ok=True, real_school_api=True, synthetic_content=True,
                nonstream_usage=first["usage"], stream_usage=second["usage"], ledger=result), ensure_ascii=False))
            return 0
        except Exception as exc:
            print(json.dumps(dict(ok=False,error_type=type(exc).__name__)))
            return 1
        finally:
            client.close()
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
