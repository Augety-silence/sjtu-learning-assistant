from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from desktop_app import DesktopBridge
from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.knowledge_wiki import CourseWiki
from sjtu_learning_assistant.transcript_notes import (
    CONTRACT, PROMPT_VERSION, _validate, compile_summary, original_markdown,
)
from sjtu_learning_assistant.transcript_service import AIContext, TranscriptService

RAW = "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n机会成本是放弃的最好选择。\n\n00:00:05.000 --> 00:00:09.000\n老师强调决策时要比较相关成本。\n"
SOURCE = "sjtu-video:12:99"


class NotesClient:
    def __init__(self):
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        data = json.loads(messages[-1]["content"])
        self.calls.append(data)
        if "task" in data:
            value = {"approved": True, "covered_chunks": data["covered_chunks"], "issues": []}
        else:
            if "cues" in data:
                cue = data["cues"][-1]
                evidence = [{"cue_id": cue["cue_id"], "quote": cue["text"][:80]}]
            else:
                evidence = [{"cue_id": n} for n in data["input_summaries"][-1]["sections"][0]["cue_ids"]]
            value = {"covered_chunks": data["covered_chunks"], "sections": [{"title": "决策主线", "text": "老师先介绍机会成本，再强调比较相关成本。", "cue_ids": [e["cue_id"] for e in evidence]}]}
        return {"content": json.dumps(value, ensure_ascii=False), "finish_reason": "stop"}


class TranscriptNotesTests(unittest.TestCase):
    def test_desktop_bridge_reads_and_reveals_all_new_markdown_ids_from_service(self):
        with tempfile.TemporaryDirectory() as temp:
            revealed = []
            service = TranscriptService(Path(temp) / "transcripts", autostart_worker=False,
                subtitle_fetcher=lambda source: {"status": "ready", "vtt": RAW},
                ai_context_provider=lambda: AIContext(NotesClient(), "deepseek-chat", "endpoint", True),
                revealer=revealed.append)
            self.addCleanup(service.close)
            batch = service.start_batch(course_id="12", course_name="经济学",
                videos=[{"source_id": SOURCE, "title": "第三讲"}])
            batch = service._read_json(service._batch_path(batch["id"]))
            job = batch["jobs"][0]
            service._fetch_and_store(batch, job)
            directory = service._video_dir(SOURCE)
            manifest = service._read_json(directory / "manifest.json")
            manifest["status"] = "completed"
            service._write_json(directory / "manifest.json", manifest)
            service._backfill_next_lesson()
            dashboard = DashboardService.__new__(DashboardService)
            dashboard._client_lock = threading.RLock()
            dashboard._transcript_service_instance = service
            bridge = DesktopBridge(dashboard)
            listed = bridge.invoke("transcript_artifacts", {"job_id": job["id"]})
            self.assertTrue(listed["ok"], listed)
            for item in listed["data"]["items"]:
                if item["kind"] not in {"lesson", "lesson_summary", "original"}:
                    continue
                with self.subTest(kind=item["kind"]):
                    result = bridge.invoke("transcript_artifact_read", {"artifact_id": item["id"]})
                    self.assertTrue(result["ok"], result)
                    self.assertIn("机会成本", result["data"]["content"])
                    self.assertTrue(bridge.invoke("transcript_artifact_reveal", {"artifact_id": item["id"]})["ok"])
            self.assertEqual(3, len(revealed))
            for kind in ("../original", "original.md", "original:extra", "secret", "v2:../raw.vtt"):
                with self.subTest(kind=kind):
                    result = bridge.invoke("transcript_artifact_read", {"artifact_id": f"{job['id']}:{kind}"})
                    self.assertFalse(result["ok"])
            self.assertEqual(3, len(revealed))

    def test_app_startup_backfills_saved_subtitles_without_entering_video_page(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initial = TranscriptService(root / "transcripts" / "v1", autostart_worker=False)
            directory = initial._video_dir(SOURCE)
            directory.mkdir()
            (directory / "raw.vtt").write_text(RAW, encoding="utf-8", newline="\n")
            initial._write_json(directory / "manifest.json", {"source": {"source_id": SOURCE},
                "course": {"id": "12", "name": "经济学"}, "video": {"title": "课堂"},
                "raw_sha256": hashlib.sha256(RAW.encode()).hexdigest(), "status": "partial", "artifacts": {}})
            initial.close()
            dashboard = DashboardService.__new__(DashboardService)
            dashboard.local_projects_path = root / "local-projects.json"
            dashboard._client_lock = threading.RLock()
            dashboard._transcript_service_instance = None
            dashboard._transcript_ai_context = lambda: AIContext(None, "", "", False)
            dashboard.video_subtitles = lambda source: self.fail("不应重新抓取或启动旧校对")
            dashboard.engine = object()
            with patch("sjtu_learning_assistant.dashboard_service.transcript_course_metadata", return_value={}), ExitStack() as worker_scope:
                dashboard.start_transcript_markdown_sync()
                service = dashboard._transcript_service_instance
                # 后台线程必须先于临时目录释放，断言失败时也不能留下 Windows 文件锁。
                worker_scope.callback(service.close)
                self.assertEqual(initial.root, service.root)
                until = time.monotonic() + 3
                while not (directory / "lesson.md").exists() and time.monotonic() < until:
                    service._wake.wait(0.02)
                self.assertIn("机会成本", (directory / "lesson.md").read_text(encoding="utf-8"))
                dashboard.start_transcript_markdown_sync()
                self.assertIs(service, dashboard._transcript_service_instance)
            self.assertFalse(service._thread.is_alive(), "临时目录清理前后台字幕线程必须退出")

    def test_original_text_is_not_rewritten_and_markdown_payload_is_literal(self):
        raw = RAW.replace("机会成本是放弃的最好选择。", "# 原话\n<script>not a command</script>\n[x](https://example.org)")
        result = original_markdown(raw)
        self.assertIn("    # 原话", result)
        self.assertIn("[x](https://example.org)", result)
        self.assertIn("00:00:05", result)

    def test_concise_summary_and_independent_review(self):
        client = NotesClient()
        body, report = compile_summary(RAW, client)
        self.assertLessEqual(len(body), 2000)
        self.assertIn("当堂老师讲了什么", body)
        self.assertIn("[00:00:05]", body)
        self.assertEqual(CONTRACT, report["contract"])
        self.assertEqual([0], report["covered_chunks"])
        self.assertEqual(2, report["calls"])
        self.assertEqual(2, len(client.calls[-1]["cues"]))

    def test_all_long_subtitle_blocks_are_processed_and_reviewed(self):
        raw = "WEBVTT\n\n" + "\n\n".join(
            f"00:{i // 60:02}:{i % 60:02}.000 --> 00:{(i + 1) // 60:02}:{(i + 1) % 60:02}.000\n第{i}段知识点。" + "解释条件。" * 15
            for i in range(120))
        client = NotesClient()
        body, report = compile_summary(raw, client)
        originals = [d for d in client.calls if "cues" in d and "task" not in d]
        reviewed = [d for d in client.calls if "task" in d]
        self.assertGreater(len(originals), 1)
        self.assertEqual(list(range(len(originals))), report["covered_chunks"])
        self.assertEqual(120, sum(len(d["cues"]) for d in originals))
        self.assertEqual(len(originals), len(reviewed))
        self.assertLessEqual(len(body), 2000)

    def test_invalid_coverage_forged_quote_and_overlength_fail_without_truncation(self):
        valid = {"covered_chunks": [0], "sections": [{"title": "主题", "text": "课堂总结", "evidence": [{"cue_id": "cue-1", "quote": "原话"}]}]}
        for changed in (dict(valid, covered_chunks=[]), dict(valid, covered_chunks=[False]),
                        {"covered_chunks": [0], "sections": [{"title": "主题", "text": "字" * 2701, "evidence": valid["sections"][0]["evidence"]}]},
                        {"covered_chunks": [0], "sections": [{"title": "主题", "text": "总结", "evidence": [{"cue_id": "cue-1", "quote": "编造"}]}]}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                _validate(changed, [0], {"cue-1": "字幕原话"}, {"cue-1": 0})

    def test_review_failure_does_not_publish(self):
        class Reject(NotesClient):
            def chat_completion(self, messages, **kwargs):
                result = super().chat_completion(messages, **kwargs)
                if "task" in self.calls[-1]:
                    result["content"] = json.dumps({"approved": False, "covered_chunks": [0], "issues": ["缺少必要条件"]})
                return result
        with self.assertRaises(ValueError):
            compile_summary(RAW, Reject())

    def test_source_ids_get_verbatim_local_quotes_and_math_conditions_remain_valid(self):
        draft = {"covered_chunks": [0], "sections": [{"title": "适用条件", "text": "老师强调 p < 0.05 时的判断条件。", "cue_ids": ["cue-1"]}]}
        result = _validate(draft, [0], {"cue-1": "当p < 0.05时。"}, {"cue-1": 0})
        self.assertEqual("当p < 0.05时。", result["sections"][0]["evidence"][0]["quote"])
        with self.assertRaises(ValueError):
            _validate({"covered_chunks": [0], "sections": [{"title": "主题", "text": "总结", "cue_ids": ["forged"]}]}, [0], {"cue-1": "原话"}, {"cue-1": 0})

    def test_validated_checkpoints_resume_but_final_source_review_still_runs(self):
        cache = {}
        client = NotesClient()
        first, _ = compile_summary(RAW, client, cached=cache.get, commit=lambda key, value: cache.setdefault(key, value))
        second, report = compile_summary(RAW, client, cached=cache.get, commit=lambda key, value: cache.setdefault(key, value))
        self.assertEqual(first, second)
        self.assertEqual(1, report["calls"])
        self.assertEqual(3, len(client.calls))
        self.assertIn("task", client.calls[-1])

    def test_saved_and_legacy_transcripts_are_auto_indexed_and_manual_edits_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client = NotesClient()
            service = TranscriptService(root / "transcripts", autostart_worker=False,
                subtitle_fetcher=lambda source: {"status": "ready", "vtt": RAW},
                ai_context_provider=lambda: AIContext(client, "deepseek-chat", "endpoint", True))
            self.addCleanup(service.close)
            batch = service.start_batch(course_id="12", course_name="经济学", videos=[{"source_id": SOURCE, "title": "第三讲"}])
            batch = service._read_json(service._batch_path(batch["id"]))
            job = batch["jobs"][0]
            service._fetch_and_store(batch, job)
            directory = service._video_dir(SOURCE)
            original = (directory / "raw.vtt").read_bytes()
            manifest = service._read_json(directory / "manifest.json")
            manifest["status"] = "completed"
            service._write_json(directory / "manifest.json", manifest)
            service._backfill_next_lesson()
            knowledge = CompiledKnowledge(lambda: SimpleNamespace(archive_root=str(root / "archive")), root / "projects.json",
                transcript_root=service.root, transcript_metadata=lambda: {"12": {"course_name": "经济学", "term_id": "term:30", "term_name": "2026 Fall"}})
            results = knowledge.search("相关成本", course_id="12", term_id="term:30")
            self.assertEqual(1, results["count"])
            self.assertIn("2026 Fall/经济学/课堂字幕", results["items"][0]["folder"])
            document = knowledge.read(results["items"][0]["id"])
            self.assertIn("原始字幕", document["text"])
            self.assertIn("当堂老师讲了什么", document["text"])
            self.assertEqual(0, knowledge.search(course_id="13")["count"])
            groups = CourseWiki(knowledge).snapshot()
            self.assertEqual(1, len(groups[0]["ready"]))
            self.assertLessEqual(len(groups[0]["ready"][0]["text"]), 2000)
            kinds = {item["kind"] for item in service.list_artifacts(job["id"])["items"]}
            self.assertTrue({"lesson", "lesson_summary", "original"} <= kinds)
            self.assertEqual(original, (directory / "raw.vtt").read_bytes())
            modified = (directory / "lesson.md").read_text(encoding="utf-8") + "\n人工笔记\n"
            (directory / "lesson.md").write_text(modified, encoding="utf-8", newline="\n")
            current = service._read_json(directory / "manifest.json")
            self.assertFalse(service._save_lesson_notes(directory, current, RAW, "新的总结"))
            self.assertEqual(modified, (directory / "lesson.md").read_text(encoding="utf-8"))
            self.assertEqual("user_modified", service._read_json(directory / "manifest.json")["lesson_notes"]["status"])

    def test_failed_upgrade_keeps_readable_raw_md_and_cools_down(self):
        with tempfile.TemporaryDirectory() as temp:
            service = TranscriptService(Path(temp) / "transcripts", autostart_worker=False,
                ai_context_provider=lambda: AIContext(NotesClient(), "deepseek-chat", "endpoint", True))
            self.addCleanup(service.close)
            directory = service._video_dir(SOURCE)
            directory.mkdir()
            (directory / "raw.vtt").write_text(RAW, encoding="utf-8", newline="\n")
            manifest = {"source": {"source_id": SOURCE}, "course": {"id": "12", "name": "经济学"}, "video": {"title": "第一堂"},
                        "raw_sha256": hashlib.sha256(RAW.encode()).hexdigest(), "status": "partial", "artifacts": {}}
            service._write_json(directory / "manifest.json", manifest)
            with patch("sjtu_learning_assistant.transcript_service.compile_summary", side_effect=TimeoutError):
                service._backfill_next_lesson()
            self.assertIn("机会成本", (directory / "lesson.md").read_text(encoding="utf-8"))
            self.assertFalse((directory / "lecture-summary.md").exists())
            current = service._read_json(directory / "manifest.json")
            self.assertEqual("pending", current["lesson_notes"]["status"])
            self.assertEqual("timeout", current["lesson_notes"]["error_code"])
            self.assertTrue(current["lesson_notes"]["retry_after"])

    def test_three_failed_attempts_resume_after_cooldown_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            now = datetime(2026, 11, 22, 1, tzinfo=timezone.utc)
            client = NotesClient()
            context = lambda: AIContext(client, 'deepseek-chat', 'endpoint', True)
            root = Path(temp) / 'transcripts'
            service = TranscriptService(root, autostart_worker=False, ai_context_provider=context, now_provider=lambda: now)
            directory = service._video_dir(SOURCE)
            directory.mkdir()
            (directory / 'raw.vtt').write_text(RAW, encoding='utf-8')
            raw_hash = hashlib.sha256(RAW.encode()).hexdigest()
            manifest = {'source': {'source_id': SOURCE}, 'course': {'id': '12', 'name': '经济学'}, 'video': {'title': '课堂'},
                        'raw_sha256': raw_hash, 'status': 'partial', 'artifacts': {}}
            service._save_lesson_notes(directory, manifest, RAW)
            manifest['lesson_notes'].update(attempts=3, fingerprint=hashlib.sha256((PROMPT_VERSION+'deepseek-chat'+'endpoint'+raw_hash).encode()).hexdigest(),
                retry_after=(now-timedelta(minutes=1)).isoformat(), error_code='timeout')
            service._write_json(directory/'manifest.json', manifest)
            with patch('sjtu_learning_assistant.transcript_service.compile_summary', side_effect=TimeoutError) as compile_mock:
                service._backfill_next_lesson()
                self.assertEqual(1, compile_mock.call_count)
            failed = service._read_json(directory/'manifest.json')['lesson_notes']
            self.assertEqual(4, failed['attempts'])
            self.assertEqual(now+timedelta(hours=2), datetime.fromisoformat(failed['retry_after']))
            service.close()
            restarted = TranscriptService(root, autostart_worker=False, ai_context_provider=context, now_provider=lambda: now)
            self.addCleanup(restarted.close)
            restarted._backfill_next_lesson()
            self.assertEqual([], client.calls)
            now += timedelta(hours=2, seconds=1)
            restarted._next_notes_scan = 0
            restarted._backfill_next_lesson()
            self.assertEqual('completed', restarted._read_json(directory/'manifest.json')['lesson_notes']['status'])
            self.assertEqual(RAW.encode(), (directory/'raw.vtt').read_bytes())

    def test_summary_accepts_over_two_thousand_and_hard_caps_three_thousand(self):
        draft = {'covered_chunks': [0], 'sections': [{'title': '课堂主线', 'text': '知识关系' * 550, 'cue_ids': ['cue-1']}]}
        result = _validate(draft, [0], {'cue-1': '原话'}, {'cue-1': 0})
        self.assertGreater(len(result['sections'][0]['text']), 2000)
        result['sections'][0]['text'] = '字' * 3001
        with self.assertRaises(ValueError):
            _validate(result, [0], {'cue-1': '原话'}, {'cue-1': 0})

    def test_dedicated_summary_context_does_not_change_chat_model(self):
        dashboard = DashboardService.__new__(DashboardService)
        dashboard._effective_settings = lambda: SimpleNamespace(ai_enabled=True, ai_key_saved=True, ai_base_url='https://models.sjtu.edu.cn/api/v1', ai_model='deepseek-reasoner')
        dashboard._run_knowledge_compiler_ai = lambda model, system, payload, **kwargs: json.dumps({'model': model, 'max_tokens': kwargs['max_tokens']})
        context = dashboard._lesson_summary_ai_context()
        self.assertEqual('deepseek-chat', context.model)
        result = context.client.chat_completion([{'content': 'system'}, {'content': 'data'}], max_tokens=5000)
        self.assertEqual({'model': 'deepseek-chat', 'max_tokens': 5000}, json.loads(result['content']))
        self.assertEqual('deepseek-reasoner', dashboard._effective_settings().ai_model)

    def test_all_saved_raw_documents_are_visible_before_first_ai_request(self):
        with tempfile.TemporaryDirectory() as temp:
            directories = []
            class CheckVisible:
                def chat_completion(self, *args, **kwargs):
                    assert all((directory / "lesson.md").is_file() for directory in directories)
                    raise TimeoutError()
            service = TranscriptService(Path(temp) / "transcripts", autostart_worker=False,
                ai_context_provider=lambda: AIContext(CheckVisible(), "deepseek-chat", "endpoint", True))
            self.addCleanup(service.close)
            for source_id in (SOURCE, "sjtu-video:12:100"):
                directory = service._video_dir(source_id)
                directory.mkdir()
                (directory / "raw.vtt").write_text(RAW, encoding="utf-8", newline="\n")
                service._write_json(directory / "manifest.json", {"source": {"source_id": source_id},
                    "course": {"id": "12", "name": "经济学"}, "video": {"title": "课堂"},
                    "raw_sha256": hashlib.sha256(RAW.encode()).hexdigest(), "status": "partial", "artifacts": {}})
                directories.append(directory)
            service._backfill_next_lesson()
            self.assertTrue(all((directory / "lesson.md").is_file() for directory in directories))

    def test_saved_and_manual_chinese_notes_ignore_system_text_defaults(self):
        read_text, write_text = Path.read_text, Path.write_text

        def windows_read(path, *args, **kwargs):
            kwargs.setdefault("encoding", "cp1252")
            return read_text(path, *args, **kwargs)

        def windows_write(path, data, *args, **kwargs):
            kwargs.setdefault("encoding", "cp1252")
            kwargs.setdefault("newline", "\r\n")
            return write_text(path, data, *args, **kwargs)

        # 复用完整入库与人工编辑保护流程，只改变未显式指定的系统文本默认值。
        with patch.object(Path, "read_text", windows_read), patch.object(Path, "write_text", windows_write):
            self.test_saved_and_legacy_transcripts_are_auto_indexed_and_manual_edits_preserved()
