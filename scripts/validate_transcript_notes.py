"""显式选择一份真实字幕，只读源、独立输出、使用本机保存的API凭据验收。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import create_engine

from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient
from sjtu_learning_assistant.ai_keychain import get_ai_api_key
from sjtu_learning_assistant.agent_runtime import AgentLoop, PresetLoader, ReadOnlyToolRegistry
from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge, _safe
from sjtu_learning_assistant.course_scope import transcript_course_metadata
from sjtu_learning_assistant.database import DEFAULT_SQLITE_PATH
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.transcript_notes import compile_summary
from sjtu_learning_assistant.transcript_service import DEFAULT_TRANSCRIPT_ROOT, TranscriptService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-directory", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = _safe(DEFAULT_TRANSCRIPT_ROOT / "videos", args.video_directory)
    original = (source / "raw.vtt").read_bytes()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    before = hashlib.sha256(original).hexdigest()
    assert before == manifest["raw_sha256"]
    output = Path(args.output).resolve()
    assert not output.exists(), "验收输出必须是全新目录"
    settings = SettingsStore().load()
    assert settings.ai_base_url.rstrip("/") == "https://models.sjtu.edu.cn/api/v1"
    key = get_ai_api_key()
    assert key, "本机保存的密钥不可读取"
    uri = "file:" + quote(str(DEFAULT_SQLITE_PATH)) + "?mode=ro"
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True))
    class ObservedClient(OpenAIClassificationClient):
        def chat_completion(self, *args, **kwargs):
            result = super().chat_completion(*args, **kwargs)
            try:
                data = json.loads(result.get("content", ""))
                sections = data.get("sections", [])
                evidence_counts = [len(section.get("cue_ids", section.get("evidence", []))) for section in sections if type(section) is dict]
                print(json.dumps({"phase": "response_structure", "sections": len(sections), "evidence_counts": evidence_counts,
                                  "approved": data.get("approved")}), flush=True)
            except (ValueError, TypeError, AttributeError):
                print(json.dumps({"phase": "response_structure", "parseable": False}), flush=True)
            return result
    client = ObservedClient(api_key=key, base_url=settings.ai_base_url, model="deepseek-chat",
                                        timeout=httpx.Timeout(connect=15, read=100, write=60, pool=15))
    print(json.dumps({"phase": "start", "source_bytes": len(original), "model": "deepseek-chat"}), flush=True)
    try:
        checkpoint_service = TranscriptService(Path(tempfile.gettempdir()) / "sjtu-classroom-validation-checkpoints" / "v1",
                                               autostart_worker=False)
        def cached(identity):
            try:
                return checkpoint_service._read_json(checkpoint_service.root / (identity + ".json"))
            except Exception:
                return None
        def commit(identity, value):
            path = checkpoint_service.root / (identity + ".json")
            if not path.exists():
                checkpoint_service._write_json(path, value)
        try:
            summary, report = compile_summary(original.decode("utf-8"), client, cached=cached, commit=commit)
        finally:
            checkpoint_service.close()
        service = TranscriptService(output / "transcripts", autostart_worker=False,
                                     course_metadata_provider=lambda: transcript_course_metadata(engine))
        try:
            directory = service._video_dir(manifest["source"]["source_id"])
            directory.mkdir()
            service._atomic_write(directory / "raw.vtt", original)
            independent = dict(manifest, artifacts={})
            assert service._save_lesson_notes(directory, independent, original.decode("utf-8"), summary, report)
            knowledge = CompiledKnowledge(projects_path=output / "no-projects.json", transcript_root=service.root, engine=engine)
            course_id = str(manifest["course"]["id"])
            catalog = knowledge.search(source_id="automatic-transcripts", course_id=course_id)
            assert catalog["count"] == 1
            document = catalog["items"][0]
            text = knowledge.read(document["id"])["text"]
            assert "## 当堂老师讲了什么" in text and "## 原始字幕" in text
            reads = []

            class ObservedRegistry(ReadOnlyToolRegistry):
                def execute(self, name, arguments, allowed_tools):
                    result = super().execute(name, arguments, allowed_tools)
                    if name == "read_compiled_knowledge" and result.result.get("id") == document["id"]:
                        reads.append({"offset": result.result.get("offset"), "chars": len(result.result.get("text", ""))})
                    return result

            registry = ObservedRegistry(engine, compiled_knowledge=knowledge)
            question = f"读取知识库文档 {document['id']}，概括这堂课老师讲了什么，并注明字幕时间依据，不要只列文件名。"
            answer = AgentLoop(client, registry, PresetLoader().get("general")).run(
                [{"role": "user", "content": question}], user_text=question, max_tokens=1500, temperature=0)
            assert answer.status == "completed" and reads, "真实AI没有读到新课堂MD正文"
            assert before == hashlib.sha256((source / "raw.vtt").read_bytes()).hexdigest()
            print(json.dumps({"ok": True, "phase": "result", "summary_chars": len(summary),
                "covered_chunks": report["covered_chunks"], "summary_calls": report["calls"],
                "source_cues": report["source_cues"], "actual_ai_reads": reads,
                "output": str(directory / "lesson.md"), "source_unchanged": True, "production_db_writes": False,
                "answer_chars": len(answer.content)}, ensure_ascii=False), flush=True)
        finally:
            service.close()
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
