from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sjtu_learning_assistant.transcript_pipeline import PIPELINE_VERSION, PROMPT_VERSION, REDUCE_FALLBACK_WARNING, SUMMARY_EMPTY_WARNING, TranscriptPipeline
from sjtu_learning_assistant.transcript_service import AIContext, TranscriptError, TranscriptService

VIDEO = {"source_id": "sjtu-video:12:99", "title": "第3周 · 周一 · 第10节", "classroom": "上院0101"}
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n本节课强调成本分析\n"


class TranscriptServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "transcripts"
        self.fetches = 0
        self.process_marker = patch("sjtu_learning_assistant.transcript_service._process_start_marker", side_effect=lambda pid: "test-current-process" if pid == os.getpid() else None)
        self.process_marker.start()
        self.addCleanup(self.process_marker.stop)

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, source_id):
        self.fetches += 1
        return {"status": "ready", "message": "已就绪", "vtt": VTT, "cue_count": 1}

    def service(self, autostart=False):
        return TranscriptService(self.root, memory_root=self.root.parent / "memory", subtitle_fetcher=self.fetch, ai_context_provider=lambda: AIContext(None, "qwen", "endpoint-hash", False), sleeper=lambda delay: None, autostart_worker=autostart)

    @staticmethod
    def valid_chunk():
        return dict(
            cleaned_transcript=list((dict(cue_id="cue-000001", start_ms=0, end_ms=2000, text="本节课强调成本分析"),)),
            topics=list(("成本分析",)),
            emphasized_points=list((dict(text="成本分析是本节重点", evidence=list((dict(cue_id="cue-000001", start_ms=0, end_ms=2000, quote="成本分析"),))),)),
            concepts=list(),
            cases_formulas_conclusions=list(),
            review_questions=list(("如何进行成本分析？",)),
            knowledge_points=list((dict(kind="method", concept="成本分析", statement="本节课强调成本分析", evidence=list((dict(cue_id="cue-000001", start_ms=0, end_ms=2000, quote="成本分析"),))),)),
            classroom_examples=list(),
            practice_items=list((dict(type="recall", prompt="本节强调什么？", related_knowledge_points=list(("成本分析",)), answer_key=list(("成本分析",)), rubric=list(), evidence=list((dict(cue_id="cue-000001", start_ms=0, end_ms=2000, quote="成本分析"),))),)),
        )

    def create_partial_with_complete_chunks(self):
        chunk = self.valid_chunk()

        class ChunksThenTimeout:
            def run(inner_self, raw_vtt, ai_client, cached_chunks=None, on_chunk=None, on_cleaned=None):
                on_chunk(0, chunk)
                on_cleaned("# 规整字幕\n\n**00:00**  本节课强调成本分析\n")
                raise TimeoutError("final reduce timeout")

        service = TranscriptService(
            self.root,
            memory_root=self.root.parent / "memory",
            subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(object(), "qwen", "endpoint-hash", True),
            pipeline=ChunksThenTimeout(),
            sleeper=lambda delay: None,
            autostart_worker=False,
        )
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=list((VIDEO,)))
        service.run_pending()
        job = next(iter(service.get_batch(batch.get("id")).get("jobs")))
        self.assertEqual("partial", job.get("status"))
        self.assertEqual(85, job.get("progress"))
        return service, batch, job

    def test_no_ai_still_saves_raw_privately_and_is_idempotent(self):
        service = self.service()
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service.run_pending()
        job = service.get_batch(batch["id"])["jobs"][0]
        self.assertEqual("completed_with_warnings", job["status"])
        summary = next(item for item in service.list_artifacts(job["id"])["items"] if item["kind"] == "summary_json")
        summary_data = json.loads(service.read_artifact(summary["id"])["content"])
        self.assertIn("study_guide", summary_data)
        self.assertTrue(summary_data["study_guide"]["themes"])
        raw = next(item for item in service.list_artifacts(job["id"])["items"] if item["kind"] == "raw_vtt")
        self.assertIn("WEBVTT", service.read_artifact(raw["id"])["content"])
        kinds = {item["kind"] for item in service.list_artifacts(job["id"])["items"]}
        self.assertIn("cues", kinds)
        manifest = json.loads(next(self.root.glob("videos/*/manifest.json")).read_text(encoding="utf-8"))
        self.assertNotIn("api_key", json.dumps(manifest))
        self.assertNotIn(str(self.root), json.dumps(manifest))
        if os.name != "nt":
            self.assertEqual(0o700, os.stat(self.root.parent).st_mode & 0o777)
            self.assertEqual(0o700, os.stat(self.root).st_mode & 0o777)
            self.assertEqual(0o600, os.stat(next(self.root.glob("videos/*/raw.vtt"))).st_mode & 0o777)
        second = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service.run_pending()
        self.assertEqual(1, self.fetches)
        self.assertTrue(service.get_batch(second["id"])["jobs"][0]["reused"])

    def test_reveal_reports_missing_linux_file_manager(self):
        with patch("sjtu_learning_assistant.transcript_service.sys.platform", "linux"), patch(
            "sjtu_learning_assistant.transcript_service.shutil.which", return_value=None
        ):
            with self.assertRaisesRegex(TranscriptError, "文件管理器"):
                TranscriptService._default_reveal(self.root / "artifact.md")

    def test_two_live_service_instances_do_not_interrupt_or_steal_batch(self):
        owner = self.service()
        batch = owner.start_batch(course_id="12", course_name="管理会计", videos=list((VIDEO,)))
        stored = json.loads(owner._batch_path(batch.get("id")).read_text(encoding="utf-8"))
        batch_owner = stored.get("owner")
        job_owner = next(iter(stored.get("jobs"))).get("owner")
        self.assertEqual(os.getpid(), batch_owner.get("pid"))
        self.assertEqual(batch_owner.get("instance_nonce"), job_owner.get("instance_nonce"))
        self.assertIsNotNone(batch_owner.get("heartbeat_at"))
        observer = self.service()
        self.assertEqual("queued", observer.get_batch(batch.get("id")).get("status"))
        observer.run_pending()
        self.assertEqual("queued", observer.get_batch(batch.get("id")).get("status"))
        owner.run_pending()
        self.assertEqual("completed_with_warnings", owner.get_batch(batch.get("id")).get("status"))

    def test_dead_or_reused_pid_owner_is_recovered_and_retry_can_cancel(self):
        service = self.service()
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=list((VIDEO,)))
        service.run_pending()
        path = service._batch_path(batch.get("id"))
        stored = json.loads(path.read_text(encoding="utf-8"))
        stale = dict(
            pid=os.getpid(),
            process_start="definitely-not-this-process",
            instance_nonce="old-instance",
            heartbeat_at=datetime.now(timezone.utc).isoformat(),
            lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        )
        stored.update(owner=stale, status="organizing")
        stored_job = next(iter(stored.get("jobs")))
        stored_job.update(owner=stale, status="organizing", stage="map_reduce")
        service._write_json(path, stored)
        manifest_path = service._manifest_path(VIDEO.get("source_id"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(status="organizing", stage="map_reduce")
        service._write_json(manifest_path, manifest)
        recovered = self.service()
        self.assertEqual("interrupted", recovered.get_batch(batch.get("id")).get("status"))
        self.assertEqual("interrupted", json.loads(manifest_path.read_text(encoding="utf-8")).get("status"))
        old_job = next(iter(batch.get("jobs")))
        retry = recovered.retry(old_job.get("id"))
        recovered.cancel(batch_id=retry.get("id"))
        recovered.run_pending()
        self.assertEqual("cancelled", recovered.get_batch(retry.get("id")).get("status"))

    def test_rejects_traversal_symlink_and_course_mismatch(self):
        service = self.service()
        with self.assertRaises(TranscriptError):
            service.read_artifact("../escape")
        with self.assertRaises(TranscriptError):
            service.start_batch(course_id="13", course_name="课程", videos=[VIDEO])
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service.run_pending()
        job = service.get_batch(batch["id"])["jobs"][0]
        raw = next(item for item in service.list_artifacts(job["id"])["items"] if item["kind"] == "raw_vtt")
        target = next(self.root.glob("videos/*/raw.vtt"))
        target.unlink()
        target.symlink_to(Path(self.temp.name) / "outside")
        with self.assertRaises(TranscriptError):
            service.read_artifact(raw["id"])


    def test_worker_stops_before_dispatching_after_close_signal(self):
        service = self.service()
        service._wake = Mock()
        service._wake.wait.side_effect = lambda _timeout: service._stop.set()
        service.run_pending = Mock()
        service._worker()
        service.run_pending.assert_not_called()

    def test_ai_failure_keeps_raw_and_marks_partial(self):
        class FailingPipeline:
            def run(self, raw_vtt, ai_client, **kwargs):
                kwargs.get("on_chunk")(0, dict(marker="value"))
                kwargs.get("on_cleaned")("# 已先保存的规整字幕\n")
                raise RuntimeError("provider failed")

        service = TranscriptService(
            self.root,
            memory_root=self.root.parent / "memory",
            subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(object(), "qwen", "endpoint-hash", True),
            pipeline=FailingPipeline(),
            sleeper=lambda delay: None,
            autostart_worker=False,
        )
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service.run_pending()
        job = service.get_batch(batch["id"])["jobs"][0]
        self.assertEqual("partial", job["status"])
        self.assertEqual("partial", service.get_batch(batch["id"])["status"])
        self.assertIn("AI 规整失败", job["message"])
        kinds = {item["kind"] for item in service.list_artifacts(job["id"])["items"]}
        self.assertIn("raw_vtt", kinds)
        self.assertIn("cues", kinds)
        self.assertIn("cleaned", kinds)
        video_dir = service._video_dir(VIDEO["source_id"])
        manifest = json.loads((video_dir / "manifest.json").read_text(encoding="utf-8"))
        record = manifest["chunks"]["0"]
        self.assertEqual("chunks/0000.json", record["path"])
        chunk_path = video_dir / "chunks" / "0000.json"
        if os.name != "nt":
            self.assertEqual(0o600, os.stat(chunk_path).st_mode & 0o777)
        self.assertEqual(hashlib.sha256(chunk_path.read_bytes()).hexdigest(), record["sha256"])


    def test_retry_refreshes_manifest_versions_and_model_fingerprint_immediately(self):
        service = self.service()
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=list((VIDEO,)))
        service.run_pending()
        job = next(iter(service.get_batch(batch.get("id")).get("jobs")))
        manifest_path = service._manifest_path(VIDEO.get("source_id"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(pipeline=dict(prompt_version="transcript-v1"), ai=dict(model="old", endpoint_fingerprint="old"))
        service._write_json(manifest_path, manifest)
        service.retry(job.get("id"))
        refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(PROMPT_VERSION, refreshed.get("pipeline").get("prompt_version"))
        self.assertEqual(PIPELINE_VERSION, refreshed.get("pipeline").get("pipeline_version"))
        self.assertEqual(64, len(refreshed.get("ai").get("model_fingerprint")))

    def test_map_fallback_artifacts_are_saved_and_job_is_partial_warning(self):
        class WarningPipeline:
            def run(self, raw_vtt, ai_client, cached_chunks=None, on_chunk=None, on_cleaned=None):
                chunk = dict(cleaned_transcript=(dict(cue_id="cue-000001", start_ms=0, end_ms=2000, text="规整文本"),), partial_warning="fallback")
                on_chunk(0, chunk)
                on_cleaned("# 规整字幕\n")
                return SimpleNamespace(
                    cues=list(),
                    chunks=(chunk,),
                    cleaned_markdown="# 规整字幕\n",
                    summary=dict(lesson_topic="已验证主题"),
                    summary_markdown="# 已验证主题\n",
                    partial_warnings=(REDUCE_FALLBACK_WARNING,),
                    reduce_diagnostics=dict(schema_mismatch=dict(path="review_questions", actual_type="dict")),
                )

        service = TranscriptService(
            self.root,
            memory_root=self.root.parent / "memory",
            subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(object(), "qwen", "endpoint-hash", True),
            pipeline=WarningPipeline(),
            sleeper=lambda delay: None,
            autostart_worker=False,
        )
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=(VIDEO,))
        service.run_pending()
        job = service.get_batch(batch.get("id")).get("jobs").pop()
        self.assertEqual("completed_with_warnings", job.get("status"))
        self.assertEqual("completed_with_warnings", job.get("stage"))
        self.assertTrue(job.get("partial_warning"))
        self.assertEqual(REDUCE_FALLBACK_WARNING, job.get("message"))
        self.assertEqual("completed_with_warnings", service.get_batch(batch.get("id")).get("status"))
        kinds = set(item.get("kind") for item in service.list_artifacts(job.get("id")).get("items"))
        self.assertIn("cleaned", kinds)
        self.assertIn("summary", kinds)



    def test_partial_complete_chunks_retry_is_offline_and_writes_verified_artifacts(self):
        service, batch, job = self.create_partial_with_complete_chunks()
        service.pipeline = TranscriptPipeline()

        class NoCalls:
            def __init__(inner_self):
                inner_self.calls = 0

            def chat_completion(inner_self, messages, max_tokens=None, temperature=None, system_prompt=None):
                inner_self.calls += 1
                raise AssertionError("offline recovery called AI")

        client = NoCalls()
        service.ai_context_provider = lambda: AIContext(client, "qwen", "endpoint-hash", True)
        retry = service.retry(job.get("id"))
        service.run_pending()
        recovered = next(iter(service.get_batch(retry.get("id")).get("jobs")))
        self.assertEqual(0, client.calls)
        self.assertEqual("completed_with_warnings", recovered.get("status"))
        self.assertTrue(recovered.get("partial_warning"))
        self.assertEqual("completed_with_warnings", service.get_batch(retry.get("id")).get("status"))
        video_dir = service._video_dir(VIDEO.get("source_id"))
        manifest = json.loads((video_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(2, manifest.get("schema_version"))
        self.assertEqual(manifest.get("idempotency_key"), manifest.get("artifact_idempotency_key"))
        self.assertNotIn("ai_warning", manifest)
        for kind, name in (("cleaned", "cleaned.md"), ("summary_json", "summary.json"), ("summary", "summary.md")):
            path = video_dir / name
            record = manifest.get("artifacts").get(kind)
            self.assertEqual(name, record.get("path"))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record.get("sha256"))
            if os.name != "nt":
                self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
        summary = json.loads((video_dir / "summary.json").read_text(encoding="utf-8"))
        evidence = next(iter(next(iter(summary.get("emphasized_points"))).get("evidence")))
        self.assertEqual("cue-000001", evidence.get("cue_id"))
        self.assertIn(evidence.get("quote"), VTT)

    def test_chunk_version_and_hash_mismatch_are_not_reused_offline(self):
        service, batch, job = self.create_partial_with_complete_chunks()
        service.pipeline = TranscriptPipeline()
        video_dir = service._video_dir(VIDEO.get("source_id"))
        manifest_path = video_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        context = AIContext(object(), "qwen", "endpoint-hash", True)
        raw = (video_dir / "raw.vtt").read_text(encoding="utf-8")
        original_context = dict(manifest.get("chunk_context"))
        original_raw_hash = manifest.get("raw_sha256")
        manifest.update(raw_sha256="0".ljust(64, "0"))
        self.assertIsNone(service._validated_cached_chunks(video_dir, manifest, job, context, raw, True))
        manifest.update(raw_sha256=original_raw_hash)
        manifest.get("chunk_context").update(prompt_version="old-prompt")
        service._write_json(manifest_path, manifest)
        self.assertIsNone(service._validated_cached_chunks(video_dir, manifest, job, context, raw, True))
        manifest.update(chunk_context=original_context)
        other_model = AIContext(object(), "other-model", "endpoint-hash", True)
        self.assertIsNone(service._validated_cached_chunks(video_dir, manifest, job, other_model, raw, True))
        manifest.get("chunks").get("0").update(sha256="0".ljust(64, "0"))
        service._write_json(manifest_path, manifest)
        self.assertIsNone(service._validated_cached_chunks(video_dir, manifest, job, context, raw, True))

        class CountingAI:
            def __init__(inner_self):
                inner_self.calls = 0

            def chat_completion(inner_self, messages, max_tokens=None, temperature=None, system_prompt=None):
                inner_self.calls += 1
                prompt = next(reversed(messages)).get("content")
                if "最终汇总" in prompt:
                    payload = dict(
                        lesson_topic="成本分析",
                        learning_objectives=list(),
                        emphasized_points=self.valid_chunk().get("emphasized_points"),
                        concepts=list(),
                        cases_formulas_conclusions=list(),
                        review_questions=list(),
                        timeline=list(),
                    )
                else:
                    payload = self.valid_chunk()
                return dict(content=json.dumps(payload, ensure_ascii=False))

        client = CountingAI()
        service.ai_context_provider = lambda: AIContext(client, "qwen", "endpoint-hash", True)
        retry = service.retry(job.get("id"))
        service.run_pending()
        self.assertGreaterEqual(client.calls, 3)
        retried_job = next(iter(service.get_batch(retry.get("id")).get("jobs")))
        self.assertEqual("completed_with_warnings", retried_job.get("status"))

    def test_summary_empty_is_partial_not_completed(self):
        class EmptySummaryPipeline:
            def run(inner_self, raw_vtt, ai_client, cached_chunks=None, on_chunk=None, on_cleaned=None):
                chunk = dict(
                    cleaned_transcript=list((dict(cue_id="cue-000001", start_ms=0, end_ms=2000, text="规整文本"),)),
                    topics=list(),
                    emphasized_points=list(),
                    concepts=list(),
                    cases_formulas_conclusions=list(),
                    review_questions=list(),
                    partial_warning="fallback",
                )
                on_chunk(0, chunk)
                on_cleaned("# 规整字幕\n")
                return SimpleNamespace(
                    cues=list(),
                    chunks=list((chunk,)),
                    cleaned_markdown="# 规整字幕\n",
                    summary=dict(
                        lesson_topic="",
                        learning_objectives=list(),
                        emphasized_points=list(),
                        concepts=list(),
                        cases_formulas_conclusions=list(),
                        review_questions=list(),
                        timeline=list(),
                    ),
                    summary_markdown="# 暂未提取到可验证的本节主题\n",
                    partial_warnings=(SUMMARY_EMPTY_WARNING,),
                    reduce_diagnostics=None,
                    summary_empty=True,
                )

        service = TranscriptService(
            self.root,
            memory_root=self.root.parent / "memory",
            subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(object(), "qwen", "endpoint-hash", True),
            pipeline=EmptySummaryPipeline(),
            sleeper=lambda delay: None,
            autostart_worker=False,
        )
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=(VIDEO,))
        service.run_pending()
        job = next(iter(service.get_batch(batch.get("id")).get("jobs")))
        self.assertEqual("partial", job.get("status"))
        self.assertEqual("summary_empty", job.get("stage"))
        self.assertEqual(SUMMARY_EMPTY_WARNING, job.get("message"))
        manifest = json.loads(service._manifest_path(VIDEO.get("source_id")).read_text(encoding="utf-8"))
        self.assertEqual("partial", manifest.get("status"))
        self.assertEqual("summary_empty", manifest.get("stage"))


    def test_completed_retry_reuses_artifacts_without_ai(self):
        service, batch, job = self.create_partial_with_complete_chunks()
        service.pipeline = TranscriptPipeline()

        class NoCalls:
            calls = 0

            def chat_completion(inner_self, messages, max_tokens=None, temperature=None, system_prompt=None):
                inner_self.calls += 1
                raise AssertionError("completed retry called AI")

        client = NoCalls()
        service.ai_context_provider = lambda: AIContext(client, "qwen", "endpoint-hash", True)
        first_retry = service.retry(job.get("id"))
        service.run_pending()
        completed_job = next(iter(service.get_batch(first_retry.get("id")).get("jobs")))
        second_retry = service.retry(completed_job.get("id"))
        service.run_pending()
        reused = next(iter(service.get_batch(second_retry.get("id")).get("jobs")))
        self.assertEqual(0, client.calls)
        self.assertTrue(reused.get("reused"))
        self.assertEqual("completed_with_warnings", reused.get("status"))
        self.assertEqual(1, self.fetches)


    def test_phase1_reviewing_stage_is_persisted_and_failure_keeps_v1_result(self):
        class FailingReview:
            pipeline_version = "phase1-test"
            prompt_version = "phase1-test"
            model_version = "phase1-test"

            def orchestrate(inner_self, **kwargs):
                del kwargs
                visible = service.get_batch(batch["id"])["jobs"][0]
                stored = json.loads(service._batch_path(batch["id"]).read_text(encoding="utf-8"))["jobs"][0]
                self.assertEqual("reviewing", visible["stage"])
                self.assertEqual("reviewing", stored["stage"])
                self.assertEqual("organizing", visible["status"])
                self.assertGreaterEqual(visible["progress"], 95)
                raise RuntimeError("review failed")

        service = TranscriptService(
            self.root, memory_root=self.root.parent / "memory", subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(None, "qwen", "endpoint-hash", False),
            orchestrator=FailingReview(), sleeper=lambda delay: None, autostart_worker=False,
        )
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service.run_pending()
        job = service.get_batch(batch["id"])["jobs"][0]
        self.assertEqual("completed_with_warnings", job["status"])
        self.assertEqual("completed_with_warnings", job["stage"])
        self.assertEqual("partial", job["phase1_status"])
        self.assertNotEqual("failed", service.get_batch(batch["id"])["status"])
        self.assertTrue(job["retryable"])
        self.assertEqual("task_error", job["diagnostic"]["code"])

    def test_reused_completed_phase1_does_not_review_again(self):
        class SuccessfulReview:
            pipeline_version = "phase1-test"
            prompt_version = "phase1-test"
            model_version = "phase1-test"

            def __init__(inner_self):
                inner_self.calls = 0
                inner_self.force_refresh_values = []

            def orchestrate(inner_self, **kwargs):
                inner_self.calls += 1
                inner_self.force_refresh_values.append(kwargs.get("force_refresh", False))
                visible = service.get_batch(current_batch["id"])["jobs"][0]
                self.assertEqual("reviewing", visible["stage"])
                raw = kwargs["raw_vtt"]
                return SimpleNamespace(
                    chunks=(), corrections=(), critics=(), quality={"passed": True}, warnings=(),
                    corrected_transcript=raw, uncertain=(), memory_version=None,
                    events=(), status="completed",
                    raw_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                )

        orchestrator = SuccessfulReview()
        service = TranscriptService(
            self.root, memory_root=self.root.parent / "memory", subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(None, "qwen", "endpoint-hash", False),
            orchestrator=orchestrator, sleeper=lambda delay: None, autostart_worker=False,
        )
        current_batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service.run_pending()
        self.assertEqual(1, orchestrator.calls)
        current_batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service.run_pending()
        reused = service.get_batch(current_batch["id"])["jobs"][0]
        self.assertTrue(reused["reused"])
        self.assertTrue(reused["phase1_reused"])
        self.assertEqual(1, orchestrator.calls)
        retried = service.retry(reused["id"])
        current_batch = retried
        service.run_pending()
        self.assertEqual(2, orchestrator.calls)
        self.assertEqual([False, True], orchestrator.force_refresh_values)
        self.assertEqual("completed", service.get_batch(retried["id"])["jobs"][0]["phase1_status"])

    def test_failed_phase1_refresh_preserves_valid_prior_v2_artifacts(self):
        class SuccessfulReview:
            pipeline_version = "phase1-test"
            prompt_version = "phase1-test"
            model_version = "phase1-test"

            def orchestrate(inner_self, **kwargs):
                raw = kwargs["raw_vtt"]
                return SimpleNamespace(
                    chunks=(), corrections=(), critics=(), quality={"passed": True}, warnings=(),
                    corrected_transcript=raw, uncertain=(), memory_version=None,
                    events=(), status="completed",
                    raw_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                )

        service = TranscriptService(
            self.root, memory_root=self.root.parent / "memory", subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(None, "qwen", "endpoint-hash", False),
            orchestrator=SuccessfulReview(), sleeper=lambda delay: None, autostart_worker=False,
        )
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service.run_pending()
        video_dir = service._video_dir(VIDEO["source_id"])
        v2_manifest_path = video_dir / "v2" / "manifest.json"
        previous_bytes = v2_manifest_path.read_bytes()
        previous = json.loads(previous_bytes)
        self.assertTrue(service._v2_artifacts_valid(video_dir, previous))

        class FailingReview:
            pipeline_version = "phase1-test"
            prompt_version = "phase1-next-version"
            model_version = "phase1-test"

            def orchestrate(self, **_kwargs):
                raise RuntimeError("refresh failed")

        service.orchestrator = FailingReview()
        stored = service.get_batch(batch["id"])
        job = stored["jobs"][0]
        raw = (video_dir / "raw.vtt").read_text(encoding="utf-8")
        service._run_phase1_safely(
            stored, job, video_dir, raw,
            AIContext(None, "qwen", "endpoint-hash", False),
            service._read_json(video_dir / "manifest.json"),
        )

        self.assertEqual(previous_bytes, v2_manifest_path.read_bytes())
        self.assertTrue(service._v2_artifacts_valid(video_dir, service._v2_manifest(video_dir)))
        self.assertIsNotNone(service._corrected_cues(video_dir, service._read_json(video_dir / "manifest.json"), raw))
        service.close()

    def test_clean_v1_and_phase1_clear_stale_job_diagnostics(self):
        chunk = self.valid_chunk()

        class CleanPipeline:
            def run(inner_self, raw_vtt, ai_client, *, cached_chunks=None, on_chunk=None, on_cleaned=None, **kwargs):
                del ai_client, cached_chunks, kwargs
                on_chunk(0, chunk)
                cleaned = "# 规整字幕\n\n**00:00** 本节课强调成本分析\n"
                on_cleaned(cleaned)
                return SimpleNamespace(
                    cues=chunk["cleaned_transcript"], chunks=[chunk],
                    cleaned_markdown=cleaned,
                    summary={"lesson_topic": "成本分析", "knowledge_points": chunk["knowledge_points"]},
                    summary_markdown="# 成本分析\n", partial_warnings=(),
                    reduce_diagnostics=None, summary_empty=False,
                )

        class SuccessfulReview:
            pipeline_version = "phase1-test"
            prompt_version = "phase1-test"
            model_version = "phase1-test"

            def orchestrate(inner_self, **kwargs):
                raw = kwargs["raw_vtt"]
                return SimpleNamespace(
                    chunks=(), corrections=(), critics=(), quality={"passed": True}, warnings=(),
                    corrected_transcript=raw, uncertain=(), memory_version=None,
                    events=(), status="completed",
                    raw_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                )

        service = TranscriptService(
            self.root, memory_root=self.root.parent / "memory", subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(None, "qwen", "endpoint-hash", False),
            pipeline=CleanPipeline(), orchestrator=SuccessfulReview(),
            sleeper=lambda delay: None, autostart_worker=False,
        )
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        stored = service._read_json(service._batch_path(batch["id"]))
        stored["jobs"][0].update(
            partial_warning=True,
            diagnostic={"stage": "guide_generation", "code": "v1_guide_schema", "retryable": True},
            phase1_warning="previous Phase1 timeout",
            retryable=True,
            auto_retryable=True,
            next_retry_at="2026-10-15T00:00:00+00:00",
        )
        service._write_json(service._batch_path(batch["id"]), stored)

        service.run_pending()

        job = service.get_batch(batch["id"])["jobs"][0]
        manifest = json.loads(service._manifest_path(VIDEO["source_id"]).read_text(encoding="utf-8"))
        self.assertEqual("completed", manifest["status"])
        self.assertNotIn("partial_warnings", manifest)
        self.assertEqual("completed", job["phase1_status"])
        self.assertNotIn("diagnostic", job)
        self.assertNotIn("partial_warning", job)
        self.assertNotIn("phase1_warning", job)
        self.assertFalse(job["retryable"])
        self.assertFalse(job["auto_retryable"])
        self.assertNotIn("next_retry_at", job)

    def test_successful_phase1_preserves_unresolved_v1_diagnostic(self):
        chunk = self.valid_chunk()

        class WarningPipeline:
            def run(inner_self, raw_vtt, ai_client, *, cached_chunks=None, on_chunk=None, on_cleaned=None, **kwargs):
                del raw_vtt, ai_client, cached_chunks, kwargs
                on_chunk(0, chunk)
                cleaned = "# 规整字幕\n"
                on_cleaned(cleaned)
                return SimpleNamespace(
                    cues=chunk["cleaned_transcript"], chunks=[chunk], cleaned_markdown=cleaned,
                    summary={
                        "lesson_topic": "成本分析", "knowledge_points": chunk["knowledge_points"],
                        "review": {"guide_generation_diagnostics": {"violation_code": "schema"}},
                    },
                    summary_markdown="# 成本分析\n", partial_warnings=("v1 guide warning",),
                    reduce_diagnostics=None, summary_empty=False,
                )

        class SuccessfulReview:
            pipeline_version = "phase1-test"
            prompt_version = "phase1-test"
            model_version = "phase1-test"

            def orchestrate(inner_self, **kwargs):
                raw = kwargs["raw_vtt"]
                return SimpleNamespace(
                    chunks=(), corrections=(), critics=(), quality={"passed": True}, warnings=(),
                    corrected_transcript=raw, uncertain=(), memory_version=None,
                    events=(), status="completed",
                    raw_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                )

        service = TranscriptService(
            self.root, memory_root=self.root.parent / "memory", subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(None, "qwen", "endpoint-hash", False),
            pipeline=WarningPipeline(), orchestrator=SuccessfulReview(),
            sleeper=lambda delay: None, autostart_worker=False,
        )
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        stored = service._read_json(service._batch_path(batch["id"]))
        stored["jobs"][0].update(
            partial_warning=True,
            diagnostic={"stage": "guide_generation", "code": "v1_guide_schema", "retryable": True},
        )
        service._write_json(service._batch_path(batch["id"]), stored)

        service.run_pending()

        job = service.get_batch(batch["id"])["jobs"][0]
        self.assertEqual("completed", job["phase1_status"])
        self.assertEqual("completed_with_warnings", job["status"])
        self.assertTrue(job["partial_warning"])
        self.assertEqual("v1_guide_schema", job["diagnostic"]["code"])

    def test_ready_video_is_organized_before_a_slow_sibling_fetch_finishes(self):
        entered_slow = threading.Event()
        release_slow = threading.Event()
        ready_fast = threading.Event()
        fast_source = "sjtu-video:12:101"

        def fetch(source_id):
            if source_id.endswith(":99"):
                entered_slow.set()
                release_slow.wait(3)
            return {"status": "ready", "message": "已就绪", "vtt": VTT, "cue_count": 1}

        class SignalingPipeline(TranscriptPipeline):
            def run(inner_self, raw_vtt, ai_client, **kwargs):
                if "成本分析" in raw_vtt:
                    ready_fast.set()
                return super().run(raw_vtt, ai_client, **kwargs)

        service = TranscriptService(
            self.root, memory_root=self.root.parent / "memory", subtitle_fetcher=fetch,
            ai_context_provider=lambda: AIContext(None, "qwen", "endpoint-hash", False),
            pipeline=SignalingPipeline(), sleeper=lambda _delay: None, autostart_worker=False,
        )
        batch = service.start_batch(
            course_id="12", course_name="管理会计",
            videos=[VIDEO, {"source_id": fast_source, "title": "当前播放录像"}],
        )
        runner = threading.Thread(target=service.run_pending)
        runner.start()
        try:
            self.assertTrue(entered_slow.wait(1))
            self.assertTrue(ready_fast.wait(1), "B 未在 A fetch 结束前进入整理")
        finally:
            release_slow.set()
            runner.join(4)
            service.close()

    def test_playback_network_failure_retries_after_cooldown_and_stops_at_limit(self):
        now = [datetime(2026, 10, 14, tzinfo=timezone.utc)]

        class RecoveringReview:
            pipeline_version = "phase1-test"
            prompt_version = "phase1-test"
            model_version = "phase1-test"

            def __init__(inner_self):
                inner_self.calls = 0

            def orchestrate(inner_self, **kwargs):
                inner_self.calls += 1
                if inner_self.calls == 1:
                    raise TimeoutError("timeout with secret=do-not-persist")
                raw = kwargs["raw_vtt"]
                return SimpleNamespace(
                    chunks=(), corrections=(), critics=(), quality={"passed": True}, warnings=(),
                    corrected_transcript=raw, uncertain=(), memory_version=None, events=(), status="completed",
                    raw_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                )

        orchestrator = RecoveringReview()
        service = TranscriptService(
            self.root, memory_root=self.root.parent / "memory", subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(None, "qwen", "endpoint-hash", False),
            orchestrator=orchestrator, sleeper=lambda _delay: None, autostart_worker=False,
            now_provider=lambda: now[0], playback_retry_cooldown_seconds=2, playback_auto_retry_limit=1,
        )
        first = service.prepare_for_playback(course_id="12", course_name="管理会计", video=VIDEO)
        service.run_pending()
        failed = service.get_batch(first["id"])["jobs"][0]
        self.assertEqual("timeout", failed["diagnostic"]["code"])
        stored_text = json.dumps(failed)
        self.assertNotIn("do-not-persist", stored_text)
        same = service.prepare_for_playback(course_id="12", course_name="管理会计", video=VIDEO)
        self.assertEqual(first["id"], same["id"])
        self.assertEqual(1, orchestrator.calls)
        now[0] += timedelta(seconds=3)
        service.run_pending()
        self.assertEqual(2, orchestrator.calls)
        latest = service.jobs("12")["items"]
        self.assertEqual("completed", next(job for job in latest if job["phase1_status"] == "completed")["phase1_status"])
        service.close()

    def test_consecutive_ai_failures_open_a_per_task_circuit(self):
        class FailingClient:
            calls = 0

            def chat_completion(self, *_args, **_kwargs):
                self.calls += 1
                raise ConnectionError("private URL query and request body")

        service = self.service()
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        stored_batch = service._read_json(service._batch_path(batch["id"]))
        job = stored_batch["jobs"][0]
        client = FailingClient()
        with patch.dict(os.environ, {
            "SJTU_AI_RETRY_ATTEMPTS": "2",
            "SJTU_AI_FAILURE_CIRCUIT_THRESHOLD": "3",
            "SJTU_AI_TASK_BUDGET_SECONDS": "60",
        }):
            context = service._task_context(AIContext(client, "qwen", "endpoint-hash", True), stored_batch, job)
            for _ in range(3):
                with self.assertRaises((ConnectionError, TranscriptError)):
                    context.client.chat_completion([{"role": "user", "content": "test"}])
        self.assertEqual(3, client.calls)
        serialized = json.dumps(service.get_batch(batch["id"]))
        self.assertNotIn("private URL", serialized)
        self.assertNotIn("request body", serialized)
        service.close()

    def test_provider_http_code_does_not_replace_actionable_failure_category(self):
        service = self.service()

        class AuthenticationFailure(RuntimeError):
            category = "authentication"
            error_code = "HTTP_401"
            status_code = 401

        job = {}
        service._record_ai_diagnostic(job, 1, time.monotonic(), AuthenticationFailure())
        self.assertEqual("authentication", job["diagnostic"]["code"])
        self.assertEqual("http_401", job["diagnostic"]["provider_code"])
        self.assertIn(
            "检查 API Key",
            service._learning_error_message(job, {}),
        )
        service.close()

    def test_windows_file_lock_has_actionable_retry_category(self):
        service = self.service()
        error = PermissionError("sharing violation")
        error.winerror = 32
        job = {}
        service._schedule_retry(job, error)
        self.assertEqual("file_locked", service._diagnostic_code(error))
        self.assertTrue(job["retryable"])
        self.assertTrue(job["auto_retryable"])
        self.assertIn("其他程序占用", service._learning_error_message({"diagnostic": {"code": "file_locked"}}, {}))
        service.close()

    def test_corrupt_stored_vtt_raises_actionable_artifact_error(self):
        service = self.service()
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service.run_pending()
        video_dir = service._video_dir(VIDEO["source_id"])
        raw_path = video_dir / "raw.vtt"
        manifest_path = video_dir / "manifest.json"
        raw_path.write_text("not webvtt", encoding="utf-8")
        digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        manifest = service._read_json(manifest_path)
        manifest["raw_sha256"] = digest
        manifest["artifacts"]["raw_vtt"]["sha256"] = digest
        service._write_json(manifest_path, manifest)
        with self.assertRaisesRegex(TranscriptError, "重新获取字幕"):
            service.learning_segments(VIDEO["source_id"])
        service.close()

    def test_worker_failure_logging_is_sanitized_and_rate_limited(self):
        service = self.service()
        calls = 0

        def fail_pending():
            nonlocal calls
            calls += 1
            if calls >= 2:
                service._stop.set()
            raise RuntimeError("private path and token")

        service.run_pending = fail_pending
        service._wake = SimpleNamespace(wait=lambda _timeout: True, clear=lambda: None, set=lambda: None)
        with patch("sjtu_learning_assistant.transcript_service.LOGGER.warning") as warning:
            service._worker()
        self.assertEqual(2, calls)
        warning.assert_called_once()
        logged = " ".join(str(item) for item in warning.call_args.args)
        self.assertNotIn("private path", logged)
        self.assertNotIn("token", logged)
        service.close()

    def test_run_pending_records_retry_metadata_for_escaped_worker_error(self):
        service = self.service()
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service._process_batch = Mock(side_effect=TimeoutError("worker timeout"))
        service.run_pending()
        job = service.get_batch(batch["id"])["jobs"][0]
        self.assertEqual("partial", job["status"])
        self.assertEqual("worker_failed", job["stage"])
        self.assertEqual("timeout", job["diagnostic"]["code"])
        self.assertTrue(job["retryable"])
        service.close()

    def test_unvalidated_nonempty_raw_does_not_prevent_failed_status(self):
        service = self.service()

        def save_raw_then_fail(batch, job):
            del batch
            video_dir = service._video_dir(job.get("source_id"))
            video_dir.mkdir(parents=True, exist_ok=True)
            (video_dir / "raw.vtt").write_text(VTT, encoding="utf-8")
            raise RuntimeError("post-save failure")

        service._fetch_and_store = save_raw_then_fail
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=list((VIDEO,)))
        service.run_pending()
        job = next(iter(service.get_batch(batch.get("id")).get("jobs")))
        self.assertEqual("failed", job.get("status"))
        self.assertIn("字幕存储失败", job.get("message"))
        self.assertEqual("failed", service.get_batch(batch.get("id")).get("status"))


    def test_fetch_job_mutation_waits_for_batch_serialization(self):
        service = self.service()
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service._synchronize_batch_jobs(batch)
        job = batch["jobs"][0]
        serialization_started = threading.Event()
        allow_serialization = threading.Event()
        mutation_finished = threading.Event()
        module = __import__("sjtu_learning_assistant.transcript_service", fromlist=["_json_bytes"])
        original_json_bytes = module._json_bytes

        def pause_batch_serialization(value):
            if isinstance(value, dict) and value.get("id") == batch["id"]:
                serialization_started.set()
                if not allow_serialization.wait(timeout=3):
                    raise TimeoutError("test serializer was not released")
            return original_json_bytes(value)

        writer = threading.Thread(target=service._save_batch, args=(batch,))
        with patch("sjtu_learning_assistant.transcript_service._json_bytes", side_effect=pause_batch_serialization):
            writer.start()
            self.assertTrue(serialization_started.wait(timeout=3))

            def mutate_job():
                job["worker_state"] = "fetch_complete"
                mutation_finished.set()

            worker = threading.Thread(target=mutate_job)
            worker.start()
            self.assertFalse(mutation_finished.wait(timeout=0.05))
            allow_serialization.set()
            writer.join(timeout=3)
            worker.join(timeout=3)

        self.assertFalse(writer.is_alive())
        self.assertFalse(worker.is_alive())
        self.assertTrue(mutation_finished.is_set())
        persisted = service._read_json(service._batch_path(batch["id"]))
        self.assertNotIn("worker_state", persisted["jobs"][0])
        service._save_batch(batch)
        self.assertEqual(
            "fetch_complete",
            service._read_json(service._batch_path(batch["id"]))["jobs"][0]["worker_state"],
        )
        service.close()

    def test_usable_subtitle_requires_current_manifest_hash_and_parse_validation(self):
        service = self.service()
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=list((VIDEO,)))
        service.run_pending()
        job = next(iter(service.get_batch(batch.get("id")).get("jobs")))
        video_dir = service._video_dir(VIDEO.get("source_id"))
        manifest_path = video_dir / "manifest.json"
        raw_path = video_dir / "raw.vtt"
        original_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        original_raw = raw_path.read_text(encoding="utf-8")
        self.assertTrue(service._job_has_usable_subtitle(job))

        raw_path.write_text("not vtt", encoding="utf-8")
        self.assertFalse(service._job_has_usable_subtitle(job))
        malformed = json.loads(json.dumps(original_manifest))
        malformed_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        malformed.update(raw_sha256=malformed_hash)
        malformed.get("artifacts").get("raw_vtt").update(sha256=malformed_hash)
        context = service.ai_context_provider()
        malformed.update(idempotency_key=service._idempotency_key(VIDEO.get("source_id"), malformed_hash, context))
        service._write_json(manifest_path, malformed)
        self.assertFalse(service._job_has_usable_subtitle(job))
        raw_path.write_text(original_raw, encoding="utf-8")

        stale = json.loads(json.dumps(original_manifest))
        stale.get("pipeline").update(prompt_version="stale-prompt")
        service._write_json(manifest_path, stale)
        self.assertFalse(service._job_has_usable_subtitle(job))

        mismatched = json.loads(json.dumps(original_manifest))
        mismatched.get("artifacts").get("raw_vtt").update(sha256="0".ljust(64, "0"))
        service._write_json(manifest_path, mismatched)
        self.assertFalse(service._job_has_usable_subtitle(job))


if __name__ == "__main__":
    unittest.main()
