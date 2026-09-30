from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from sjtu_learning_assistant.transcript_pipeline import PIPELINE_VERSION, PROMPT_VERSION, REDUCE_FALLBACK_WARNING, SUMMARY_EMPTY_WARNING, TranscriptPipeline
from sjtu_learning_assistant.transcript_service import AIContext, TranscriptError, TranscriptService

VIDEO = {"source_id": "sjtu-video:12:99", "title": "第3周 · 周一 · 第10节", "classroom": "上院0101"}
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n本节课强调成本分析\n"


class TranscriptServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "transcripts"
        self.fetches = 0

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, source_id):
        self.fetches += 1
        return {"status": "ready", "message": "已就绪", "vtt": VTT, "cue_count": 1}

    def service(self, autostart=False):
        return TranscriptService(self.root, subtitle_fetcher=self.fetch, ai_context_provider=lambda: AIContext(None, "qwen", "endpoint-hash", False), sleeper=lambda delay: None, autostart_worker=autostart)

    @staticmethod
    def valid_chunk():
        return dict(
            cleaned_transcript=list((dict(cue_id="cue-000001", start_ms=0, end_ms=2000, text="本节课强调成本分析"),)),
            topics=list(("成本分析",)),
            emphasized_points=list((dict(text="成本分析是本节重点", evidence=list((dict(cue_id="cue-000001", start_ms=0, end_ms=2000, quote="成本分析"),))),)),
            concepts=list(),
            cases_formulas_conclusions=list(),
            review_questions=list(("如何进行成本分析？",)),
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
        self.assertEqual("waiting_for_ai", job["status"])
        raw = next(item for item in service.list_artifacts(job["id"])["items"] if item["kind"] == "raw_vtt")
        self.assertIn("WEBVTT", service.read_artifact(raw["id"])["content"])
        kinds = {item["kind"] for item in service.list_artifacts(job["id"])["items"]}
        self.assertIn("cues", kinds)
        manifest = json.loads(next(self.root.glob("videos/*/manifest.json")).read_text())
        self.assertNotIn("api_key", json.dumps(manifest))
        self.assertNotIn(str(self.root), json.dumps(manifest))
        self.assertEqual(0o700, os.stat(self.root.parent).st_mode & 0o777)
        self.assertEqual(0o700, os.stat(self.root).st_mode & 0o777)
        self.assertEqual(0o600, os.stat(next(self.root.glob("videos/*/raw.vtt"))).st_mode & 0o777)
        second = service.start_batch(course_id="12", course_name="管理会计", videos=[VIDEO])
        service.run_pending()
        self.assertEqual(1, self.fetches)
        self.assertTrue(service.get_batch(second["id"])["jobs"][0]["reused"])

    def test_two_live_service_instances_do_not_interrupt_or_steal_batch(self):
        owner = self.service()
        batch = owner.start_batch(course_id="12", course_name="管理会计", videos=list((VIDEO,)))
        stored = json.loads(owner._batch_path(batch.get("id")).read_text())
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
        self.assertEqual("waiting_for_ai", owner.get_batch(batch.get("id")).get("status"))

    def test_dead_or_reused_pid_owner_is_recovered_and_retry_can_cancel(self):
        service = self.service()
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=list((VIDEO,)))
        service.run_pending()
        path = service._batch_path(batch.get("id"))
        stored = json.loads(path.read_text())
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
        manifest = json.loads(manifest_path.read_text())
        manifest.update(status="organizing", stage="map_reduce")
        service._write_json(manifest_path, manifest)
        recovered = self.service()
        self.assertEqual("interrupted", recovered.get_batch(batch.get("id")).get("status"))
        self.assertEqual("interrupted", json.loads(manifest_path.read_text()).get("status"))
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
        manifest = json.loads((video_dir / "manifest.json").read_text())
        record = manifest["chunks"]["0"]
        self.assertEqual("chunks/0000.json", record["path"])
        chunk_path = video_dir / "chunks" / "0000.json"
        self.assertEqual(0o600, os.stat(chunk_path).st_mode & 0o777)
        self.assertEqual(hashlib.sha256(chunk_path.read_bytes()).hexdigest(), record["sha256"])


    def test_retry_refreshes_manifest_versions_and_model_fingerprint_immediately(self):
        service = self.service()
        batch = service.start_batch(course_id="12", course_name="管理会计", videos=list((VIDEO,)))
        service.run_pending()
        job = next(iter(service.get_batch(batch.get("id")).get("jobs")))
        manifest_path = service._manifest_path(VIDEO.get("source_id"))
        manifest = json.loads(manifest_path.read_text())
        manifest.update(pipeline=dict(prompt_version="transcript-v1"), ai=dict(model="old", endpoint_fingerprint="old"))
        service._write_json(manifest_path, manifest)
        service.retry(job.get("id"))
        refreshed = json.loads(manifest_path.read_text())
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
        self.assertEqual("completed", recovered.get("status"))
        self.assertEqual("字幕已存储并完成 AI 规整。", recovered.get("message"))
        self.assertEqual("completed", service.get_batch(retry.get("id")).get("status"))
        video_dir = service._video_dir(VIDEO.get("source_id"))
        manifest = json.loads((video_dir / "manifest.json").read_text())
        self.assertEqual(2, manifest.get("schema_version"))
        self.assertEqual(manifest.get("idempotency_key"), manifest.get("artifact_idempotency_key"))
        self.assertNotIn("ai_warning", manifest)
        for kind, name in (("cleaned", "cleaned.md"), ("summary_json", "summary.json"), ("summary", "summary.md")):
            path = video_dir / name
            record = manifest.get("artifacts").get(kind)
            self.assertEqual(name, record.get("path"))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record.get("sha256"))
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
        summary = json.loads((video_dir / "summary.json").read_text())
        evidence = next(iter(next(iter(summary.get("emphasized_points"))).get("evidence")))
        self.assertEqual("cue-000001", evidence.get("cue_id"))
        self.assertIn(evidence.get("quote"), VTT)

    def test_chunk_version_and_hash_mismatch_are_not_reused_offline(self):
        service, batch, job = self.create_partial_with_complete_chunks()
        service.pipeline = TranscriptPipeline()
        video_dir = service._video_dir(VIDEO.get("source_id"))
        manifest_path = video_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        context = AIContext(object(), "qwen", "endpoint-hash", True)
        raw = (video_dir / "raw.vtt").read_text()
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
        self.assertEqual(1, client.calls)
        retried_job = next(iter(service.get_batch(retry.get("id")).get("jobs")))
        self.assertEqual("completed", retried_job.get("status"))

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
        manifest = json.loads(service._manifest_path(VIDEO.get("source_id")).read_text())
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
        self.assertEqual("completed", reused.get("status"))
        self.assertEqual(1, self.fetches)


if __name__ == "__main__":
    unittest.main()
