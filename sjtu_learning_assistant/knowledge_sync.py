"""显式授权目录的本地增量编译入口；不扫描用户其他目录、不启动云服务。"""
from __future__ import annotations

import argparse
import json
import threading

from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient
from sjtu_learning_assistant.ai_keychain import get_ai_api_key
from sjtu_learning_assistant.knowledge_compiler import KnowledgeCompilerService
from sjtu_learning_assistant.local_settings import SettingsStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PPTX/PDF 逐页图文编译为 Markdown；默认只检查，不调用模型")
    parser.add_argument("--source", required=True, help="明确授权的素材绝对目录")
    parser.add_argument("--target", required=True, help="独立的空目录或本功能 Vault")
    parser.add_argument("--allow-api", action="store_true", help="授权发送此目录课件页图与文字到已配置模型接口")
    parser.add_argument("--watch", type=int, default=0, help="增量轮询秒数，至少 30；0 表示运行一次")
    parser.add_argument("--max-calls", type=int, default=100, help="整个进程生命周期的 API 调用上限，包含失败尝试")
    args = parser.parse_args(argv)
    if args.watch and args.watch < 30 or not 1 <= args.max_calls <= 10_000:
        parser.error("轮询至少 30 秒，max-calls 必须在 1—10000 内")

    def runner(model, system, user):
        settings = SettingsStore().load()
        if not settings.ai_enabled:
            raise RuntimeError("AI 未启用")
        key = get_ai_api_key()
        if not key:
            raise RuntimeError("缺少已保存 AI 凭据")
        client = OpenAIClassificationClient(api_key=key, base_url=settings.ai_base_url, model=model)
        try:
            result = client.chat_completion(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=4096, temperature=0.1, system_prompt=False,
            )
            if (result.get("response_metadata") or {}).get("finish_reason") == "length" or result.get("finish_reason") == "length":
                raise RuntimeError("输出截断，请拆分页面")
            return result["content"]
        finally:
            client.close()

    service = KnowledgeCompilerService(ai_runner=runner, max_document_calls=args.max_calls)
    try:
        _, _, inspection = service.validate_project(args.source, args.target)
        print(json.dumps({"inspection": inspection, "api_authorized": args.allow_api}, ensure_ascii=False), flush=True)
        if not args.allow_api:
            return 0
        remaining = args.max_calls
        while remaining > 0:
            service.max_document_calls = remaining
            service.start(args.source, args.target, "documents")
            while service._thread is not None and service._thread.is_alive():
                service._thread.join(timeout=0.5)
            status = service.status(args.target)
            print(json.dumps(status, ensure_ascii=False), flush=True)
            remaining -= status["counts"].get("document_calls", 0)
            if not args.watch or status["status"] == "failed" or remaining <= 0:
                return 0 if status["status"] == "completed" else 1
            threading.Event().wait(args.watch)
        return 1
    except KeyboardInterrupt:
        service.cancel()
        if service._thread:
            service._thread.join(timeout=125)
        return 130
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
