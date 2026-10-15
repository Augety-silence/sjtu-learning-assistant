from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sjtu_learning_assistant.transcript_service import AIContext, TranscriptService
from tests import test_transcript_service as service_fixtures


VIDEO = {"source_id": "sjtu-video:12:99", "title": "测试录像"}
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n本节课强调成本分析\n"
READY = {"status": "ready", "vtt": VTT}


class CompletePipeline:
    def __init__(self, *, invoke_ai=False):
        self.calls = []
        self.invoke_ai = invoke_ai

    def run(self, raw, client, **options):
        self.calls.append(options)
        if self.invoke_ai and not options.get("deterministic_only"):
            client.chat_completion([])
        if options.get("on_cleaned"):
            options["on_cleaned"]("# 字幕\n")
        return SimpleNamespace(
            cues=[{"cue_id": "cue-000001", "start_ms": 0, "end_ms": 2000, "text": "本节课强调成本分析"}],
            summary={"lesson_topic": "成本分析"}, summary_markdown="# 成本分析\n",
            partial_warnings=(), summary_empty=False,
        )


class TranscriptAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="transcript-automation-test-")
        self.root = Path(self.temp.name)
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.close()
        self.temp.cleanup()

    def service(self, **options):
        defaults = dict(
            root=self.root / "transcripts", memory_root=self.root / "memory",
            subtitle_fetcher=lambda _: READY,
            ai_context_provider=lambda: AIContext(None, "model", "endpoint", False),
            pipeline=CompletePipeline(), enable_phase1=False, autostart_worker=False,
            sleeper=lambda _: None,
        )
        defaults.update(options)
        service = TranscriptService(**defaults)
        self.services.append(service)
        return service

    @staticmethod
    def start(service, video=VIDEO):
        return service.start_batch(course_id="12", course_name="课程", videos=[video])

    def test_processing_subtitles_retry_when_due_without_another_button(self):
        clock = [datetime(2026, 10, 14, tzinfo=timezone.utc)]
        calls = []

        def fetch(_):
            calls.append(1)
            return {"status": "processing"} if len(calls) == 1 else READY

        service = self.service(subtitle_fetcher=fetch, now_provider=lambda: clock[0])
        batch = self.start(service)
        service.run_pending()
        self.assertEqual("waiting_remote", service.get_batch(batch["id"])["status"])
        service.run_pending()
        self.assertEqual(1, len(calls))
        clock[0] += timedelta(minutes=1)
        service.run_pending()
        self.assertEqual("completed", service.get_batch(batch["id"])["status"])
        self.assertEqual(2, len(calls))

    def test_running_cancel_survives_worker_save_and_skips_organization(self):
        entered, release = threading.Event(), threading.Event()

        def fetch(_):
            entered.set()
            release.wait(3)
            return READY

        pipeline = CompletePipeline()
        service = self.service(subtitle_fetcher=fetch, pipeline=pipeline)
        batch = self.start(service)
        thread = threading.Thread(target=service.run_pending)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            service.cancel(batch_id=batch["id"])
        finally:
            release.set()
            thread.join(3)
        stored = json.loads(service._batch_path(batch["id"]).read_text())
        self.assertTrue(stored["cancel_requested"])
        self.assertEqual("cancelled", stored["jobs"][0]["status"])
        self.assertEqual([], pipeline.calls)

    def test_cancel_between_model_calls_stops_next_call(self):
        service = self.service()
        batch = self.start(service)
        calls = []

        class Client:
            def chat_completion(inner, *args, **kwargs):
                calls.append(1)
                service.cancel(batch_id=batch["id"])
                return {"content": "mock"}

        class Pipeline(CompletePipeline):
            def run(inner, raw, client, **options):
                client.chat_completion([])
                client.chat_completion([])
                return super().run(raw, client, **options)

        service.pipeline = Pipeline()
        service.ai_context_provider = lambda: AIContext(Client(), "model", "endpoint", True)
        service.run_pending()
        self.assertEqual(1, len(calls))
        self.assertEqual("cancelled", service.get_batch(batch["id"])["status"])

    def test_enabling_ai_upgrades_deterministic_artifacts(self):
        enabled = [False]
        pipeline = CompletePipeline()
        service = self.service(
            pipeline=pipeline,
            ai_context_provider=lambda: AIContext(object() if enabled[0] else None, "model", "endpoint", enabled[0]),
        )
        self.start(service)
        service.run_pending()
        enabled[0] = True
        upgraded = self.start(service)
        service.run_pending()
        self.assertEqual(2, len(pipeline.calls))
        self.assertFalse(pipeline.calls[-1].get("deterministic_only", False))
        self.assertEqual("completed", service.get_batch(upgraded["id"])["status"])

    def test_organization_setup_failure_does_not_stop_following_batch(self):
        calls = []

        def context():
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("mock context unavailable")
            return AIContext(None, "model", "endpoint", False)

        service = self.service(ai_context_provider=context)
        first = self.start(service)
        second = self.start(service, dict(VIDEO, source_id="sjtu-video:12:100"))
        service.run_pending()
        self.assertIn(service.get_batch(first["id"])["status"], {"partial", "failed"})
        self.assertEqual("completed", service.get_batch(second["id"])["status"])

    def test_transient_ai_failure_retries_at_model_boundary(self):
        calls, delays = [], []

        class Client:
            def chat_completion(inner, *args, **kwargs):
                calls.append(1)
                if len(calls) < 3:
                    raise ConnectionError("mock temporary connection failure")
                return {"content": "mock"}

        service = self.service(
            pipeline=CompletePipeline(invoke_ai=True), sleeper=delays.append,
            ai_context_provider=lambda: AIContext(Client(), "model", "endpoint", True),
        )
        with patch.dict(os.environ, {"SJTU_AI_RETRY_ATTEMPTS": "3"}):
            batch = self.start(service)
            service.run_pending()
        self.assertEqual(3, len(calls))
        self.assertEqual([0.25, 0.5], delays)
        self.assertEqual("completed", service.get_batch(batch["id"])["status"])

    def test_default_ai_retry_budget_is_finite_and_cools_down(self):
        calls, delays = [], []

        class Client:
            def chat_completion(inner, *args, **kwargs):
                calls.append(1)
                raise ConnectionError("mock temporary connection failure")

        service = self.service(
            pipeline=CompletePipeline(invoke_ai=True), sleeper=delays.append,
            ai_context_provider=lambda: AIContext(Client(), "model", "endpoint", True),
        )
        with patch.dict(os.environ):
            os.environ.pop("SJTU_AI_RETRY_ATTEMPTS", None)
            batch = self.start(service)
            service.run_pending()
        self.assertEqual(2, len(calls))
        self.assertEqual([0.25], delays)
        self.assertIn(service.get_batch(batch["id"])["status"], {"completed_with_warnings", "partial"})

    def test_playback_prepare_deduplicates_active_and_completed(self):
        service = self.service()
        first = service.prepare_for_playback(course_id="12", course_name="课程", video=VIDEO)
        duplicate = service.prepare_for_playback(course_id="12", course_name="课程", video=VIDEO, position_seconds=1)
        self.assertEqual(first["id"], duplicate["id"])
        service.run_pending()
        completed = service.prepare_for_playback(course_id="12", course_name="课程", video=VIDEO)
        self.assertEqual(first["id"], completed["id"])
        self.assertEqual(1, len(service._all_batches()))

    def test_switching_recording_yields_at_model_boundary_then_resumes_previous(self):
        events = []
        service = self.service()
        other = dict(VIDEO, source_id="sjtu-video:12:100")
        a_vtt, b_vtt = VTT, VTT.replace("成本分析", "第二录像")
        service.subtitle_fetcher = lambda source: dict(READY, vtt=a_vtt if source == VIDEO["source_id"] else b_vtt)

        class Client:
            def chat_completion(inner, *args, **kwargs):
                events.append("model")
                return {"content": "mock"}

        class Pipeline(CompletePipeline):
            switched = False

            def run(inner, raw, client, **options):
                if raw == a_vtt and not inner.switched:
                    inner.switched = True
                    events.append("A chunk saved")
                    options["on_chunk"](0, {
                        "cleaned_transcript": [{"cue_id": "cue-000001", "start_ms": 0, "end_ms": 2000, "text": "本节课强调成本分析"}],
                        "topics": [], "emphasized_points": [], "concepts": [],
                        "cases_formulas_conclusions": [], "review_questions": [],
                    })
                    service.prepare_for_playback(course_id="12", course_name="课程", video=other)
                    client.chat_completion([])
                events.append("A resumed" if raw == a_vtt else "B organized")
                return super().run(raw, client, **options)

        service.pipeline = Pipeline()
        service.ai_context_provider = lambda: AIContext(Client(), "model", "endpoint", True)
        first = service.prepare_for_playback(course_id="12", course_name="课程", video=VIDEO)
        service.run_pending()
        self.assertEqual(["A chunk saved", "B organized", "A resumed"], events)
        self.assertEqual("completed", service.get_batch(first["id"])["status"])
        self.assertFalse(json.loads(service._batch_path(first["id"]).read_text())["cancel_requested"])

    def test_remote_wait_in_mixed_failure_batch_still_retries_and_is_bounded(self):
        clock = [datetime(2026, 10, 14, tzinfo=timezone.utc)]
        calls = []

        def fetch(source):
            if source == VIDEO["source_id"]:
                return {"status": "empty", "message": "no captions"}
            calls.append(1)
            return {"status": "processing"}

        service = self.service(subtitle_fetcher=fetch, now_provider=lambda: clock[0])
        other = dict(VIDEO, source_id="sjtu-video:12:100")
        batch = service.start_batch(course_id="12", course_name="课程", videos=[VIDEO, other])
        for _ in range(10):
            service.run_pending()
            clock[0] += timedelta(hours=1)
        job = service.get_batch(batch["id"])["jobs"][1]
        self.assertEqual("failed", job["status"])
        self.assertEqual("remote_retry_exhausted", job["stage"])
        self.assertEqual(9, len(calls))

    def test_automatic_failed_prepare_does_not_repeat_on_playback_updates(self):
        service = self.service(subtitle_fetcher=lambda _: {"status": "empty"})
        first = service.prepare_for_playback(course_id="12", course_name="课程", video=VIDEO)
        service.run_pending()
        for position in (1, 2, 3):
            service.update_playback(VIDEO["source_id"], position)
            prepared = service.prepare_for_playback(course_id="12", course_name="课程", video=VIDEO, position_seconds=position)
            self.assertEqual(first["id"], prepared["id"])
        self.assertEqual(1, len(service._all_batches()))

    def test_saved_chunk_is_available_before_full_summary_and_tamper_is_excluded(self):
        observed = []
        service = self.service()

        class Pipeline(CompletePipeline):
            def run(inner, raw, client, **options):
                options["on_chunk"](0, service_fixtures.TranscriptServiceTests.valid_chunk())
                observed.append(service.learning_segments(VIDEO["source_id"]))
                raise TimeoutError("mock later stage failure")

        service.pipeline = Pipeline()
        batch = self.start(service)
        service.run_pending()
        self.assertEqual("partial", service.get_batch(batch["id"])["status"])
        self.assertEqual("ready", observed[0]["status"])
        self.assertEqual([[0, 2000]], observed[0]["available_ranges"])
        self.assertTrue(observed[0]["segments"][0]["provisional"])
        path = service._video_dir(VIDEO["source_id"]) / "chunks" / "0000.json"
        path.write_text("{}", encoding="utf-8")
        self.assertEqual([], service.learning_segments(VIDEO["source_id"])["segments"])

    def test_cancel_waiting_job_finishes_without_fetching_again(self):
        calls = []
        service = self.service(subtitle_fetcher=lambda _: calls.append(1) or {"status": "processing"})
        batch = self.start(service)
        service.run_pending()
        job = service.get_batch(batch["id"])["jobs"][0]
        service.cancel(job_id=job["id"])
        service.run_pending()
        self.assertEqual("cancelled", service.get_batch(batch["id"])["status"])
        self.assertEqual(1, len(calls))

    def test_no_job_is_empty_but_queued_without_artifacts_is_processing(self):
        service = self.service()
        missing = service.learning_segments(VIDEO["source_id"])
        self.assertEqual("empty", missing["status"])
        self.assertFalse(missing["is_processing"])
        self.start(service)
        queued = service.learning_segments(VIDEO["source_id"])
        self.assertEqual("processing", queued["status"])
        self.assertTrue(queued["is_processing"])

    def test_terminal_fetch_failure_without_artifacts_is_empty(self):
        service = self.service(subtitle_fetcher=lambda _: {"status": "empty", "message": "该录像暂无字幕。"})
        batch = self.start(service)
        service.run_pending()
        self.assertEqual("failed", service.get_batch(batch["id"])["status"])
        result = service.learning_segments(VIDEO["source_id"])
        self.assertEqual("empty", result["status"])
        self.assertFalse(result["is_processing"])
        self.assertNotIn("正在", result["message"])

    def test_terminal_ai_failure_without_insights_is_empty(self):
        class FailingPipeline:
            def run(inner, raw, client, **options):
                raise ConnectionError("mock unavailable")

        service = self.service(pipeline=FailingPipeline())
        self.start(service)
        service.run_pending()
        result = service.learning_segments(VIDEO["source_id"])
        self.assertEqual("empty", result["status"])
        self.assertFalse(result["is_processing"])
        self.assertIn("未完成", result["message"])

    def test_disabled_completed_without_insights_is_empty_and_explains_draft(self):
        service = self.service()
        self.start(service)
        service.run_pending()
        result = service.learning_segments(VIDEO["source_id"])
        self.assertEqual("empty", result["status"])
        self.assertFalse(result["is_processing"])
        self.assertIn("初稿", result["message"])
