"""本机真实失败资料的隔离验收；不改变生产清单、原件或人工稿。"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient
from sjtu_learning_assistant.ai_keychain import get_ai_api_key
from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.knowledge_documents import DocumentCompiler, CompilationDeferred, CONTRACT, VISION_MODEL, digest, _validate_renderable_pptx
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.transcript_notes import compile_summary, SummaryDeferred, PROMPT_VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("transcript", "course"))
    args = parser.parse_args()
    settings = SettingsStore().load()
    key = get_ai_api_key()
    if not key:
        raise RuntimeError("未找到本机已保存凭据。")
    clients, uses = {}, []
    def client(model):
        if model not in clients:
            import httpx
            clients[model] = OpenAIClassificationClient(api_key=key, base_url=settings.ai_base_url, model=model,
                timeout=httpx.Timeout(connect=15, read=240, write=60, pool=15),
                usage_recorder=lambda model, usage: uses.append((model, usage)))
        return clients[model]
    def runner(model, system, content):
        return client(model).chat_completion([{"role": "system", "content": system}, {"role": "user", "content": content}],
            max_tokens=5000 if "总结" in system else 8192, system_prompt=False, knowledge_compilation=True,
            response_format={"type": "json_object"} if "json" in system.casefold() else None)["content"]
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="sjtu-acceleration-validation-") as temporary:
            target = Path(temporary).resolve()
            if args.kind == "transcript":
                candidates = []
                for directory in (APP_SUPPORT_DIR / "transcripts/v1/videos").iterdir():
                    manifest = directory / "manifest.json"
                    if directory.is_symlink() or not manifest.is_file():
                        continue
                    data = json.loads(manifest.read_text())
                    notes = data.get("lesson_notes", {})
                    raw = directory / "raw.vtt"
                    if (raw.is_file() and notes.get("status") != "user_modified"
                            and (notes.get("status") != "completed" or notes.get("prompt_version") != PROMPT_VERSION)
                            and hashlib.sha256(raw.read_bytes()).hexdigest() == data.get("raw_sha256")):
                        candidates.append((raw.stat().st_size, raw))
                if not candidates:
                    raise RuntimeError("没有待验收的真实未完成字幕。")
                raw_path = sorted(candidates)[0][1]
                raw = raw_path.read_text()
                before = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                cache = {}
                options = dict(cached=cache.get, commit=lambda k, v: cache.setdefault(k, v), max_calls=2)
                for turns in range(100):
                    try:
                        body, report = compile_summary(raw, client("deepseek-chat"), **options)
                        break
                    except SummaryDeferred:
                        continue
                else:
                    raise RuntimeError("字幕验收轮次超限。")
                calls = len(uses)
                resumed, resume_report = compile_summary(raw, client("deepseek-chat"), **options)
                assert resumed == body and resume_report["calls"] == 0
                assert before == hashlib.sha256(raw_path.read_bytes()).hexdigest()
                result = {"kind": args.kind, "passed": True, "source_chunks": len(report["covered_chunks"]),
                          "summary_chars": len(body), "requests": calls, "resume_requests": len(uses) - calls,
                          "slices": turns + 1, "source_unchanged": True}
            else:
                source = Path(settings.archive_root)
                production = source / "Knowledge Markdown"
                state = json.loads((production / "99_System/Course-Sync.json").read_text())
                candidates = []
                for relative, item in state["items"].items():
                    path = source / relative
                    if item.get("status") == "failed" and path.is_file() and path.suffix.casefold() in {".pdf", ".pptx"}:
                        if path.suffix.casefold() == ".pptx":
                            try:
                                _validate_renderable_pptx(path)
                            except Exception:
                                continue  # 安全拒绝项不通过移除安全门放行。
                        candidates.append((path.stat().st_size, relative, path))
                if not candidates:
                    raise RuntimeError("没有待验收的真实失败课件。")
                _, relative, path = sorted(candidates)[0]
                before = digest(path.read_bytes())
                source_id = digest(relative.encode())[:16]
                revision = digest((before + CONTRACT + VISION_MODEL).encode())[:16]
                cache = production / "90_Inbox/Documents" / source_id / revision
                destination = target / "90_Inbox/Documents" / source_id / revision
                if cache.is_dir():
                    shutil.copytree(cache, destination)
                for turns in range(200):
                    try:
                        compiler = DocumentCompiler(runner, threading.Event(), slice_calls=2, text_first=True)
                        note = compiler.compile(source, path, target)
                        break
                    except CompilationDeferred:
                        continue
                else:
                    raise RuntimeError("课件验收轮次超限。")
                calls = len(uses)
                again = DocumentCompiler(runner, threading.Event(), slice_calls=2, text_first=True).compile(source, path, target)
                assert again.read_bytes() == note.read_bytes() and len(uses) == calls
                assert digest(path.read_bytes()) == before
                report = json.loads((note.parent / "review.json").read_text())
                result = {"kind": args.kind, "passed": True, "pages": len(report["covered_pages"]),
                          "summary_chars": report["summary_chars"], "requests": calls, "resume_requests": 0,
                          "slices": turns + 1, "source_unchanged": True}
            result.update(seconds=round(time.monotonic() - started, 2),
                reported_input=sum(u.get("prompt_tokens", 0) for _, u in uses),
                reported_output=sum(u.get("completion_tokens", 0) for _, u in uses),
                usage_missing_calls=sum("total_tokens" not in u for _, u in uses))
            print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        known_errors = ("编译输出路径越界。", "编译输出不能包含符号链接。", "另一个进程正在编译此输出目录。")
        print(json.dumps({"kind": args.kind, "passed": False, "error_type": type(exc).__name__,
                          "local_error": str(exc) if str(exc) in known_errors else None,
                          "category": getattr(exc, "category", None), "requests": len(uses)}, ensure_ascii=False), flush=True)
        return 1
    finally:
        for item in clients.values():
            item.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
