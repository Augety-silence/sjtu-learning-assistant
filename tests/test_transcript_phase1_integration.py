from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sjtu_learning_assistant.course_learning_schemas import (
    CorrectionChange,
    CorrectionResult,
    CriticDecision,
    CriticResult,
    MemoryDelta,
    QualityReport,
    SubtitleChunk,
)
from sjtu_learning_assistant.transcript_pipeline import MAP_FALLBACK_WARNING, normalize_cues, parse_vtt
from sjtu_learning_assistant.transcript_service import (
    AIContext,
    TranscriptService,
    V2_ARTIFACT_FILES,
)


VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n本节课强调成本分析\n"


class CompleteV1Pipeline:
    def run(self, raw_vtt, ai_client, *, cached_chunks=None, on_chunk=None, on_cleaned=None, **kwargs):
        cues = kwargs.get("source_cues") or normalize_cues(parse_vtt(raw_vtt))
        rows = [cue.as_dict() for cue in cues]
        del ai_client, cached_chunks
        chunk = {
            "cleaned_transcript": rows,
            "topics": ["成本分析"],
            "emphasized_points": [],
            "concepts": [],
            "cases_formulas_conclusions": [],
            "review_questions": [],
        }
        on_chunk(0, chunk)
        cleaned = "# 规整字幕\n\n**00:00**  本节课强调成本分析\n"
        on_cleaned(cleaned)
        return SimpleNamespace(
            cues=rows,
            chunks=[chunk],
            cleaned_markdown=cleaned,
            summary={"lesson_topic": "成本分析"},
            summary_markdown="# 成本分析\n",
            partial_warnings=(),
            reduce_diagnostics=None,
            summary_empty=False,
        )


class FallbackOnceV1Pipeline(CompleteV1Pipeline):
    def __init__(self):
        self.calls = []

    def run(self, raw_vtt, ai_client, *, cached_chunks=None, on_chunk=None, on_cleaned=None, **kwargs):
        cached_chunks = cached_chunks or {}
        self.calls.append(set(cached_chunks))
        cues = kwargs.get("source_cues") or normalize_cues(parse_vtt(raw_vtt))
        rows = [cue.as_dict() for cue in cues]
        chunk = {
            "cleaned_transcript": rows,
            "topics": ["成本分析"],
            "emphasized_points": [],
            "concepts": [],
            "cases_formulas_conclusions": [],
            "review_questions": [],
        }
        first_run = len(self.calls) == 1
        if first_run:
            chunk["partial_warning"] = MAP_FALLBACK_WARNING
        on_chunk(0, chunk)
        cleaned = "# 规整字幕\n\n**00:00**  本节课强调成本分析\n"
        on_cleaned(cleaned)
        return SimpleNamespace(
            cues=rows,
            chunks=[chunk],
            cleaned_markdown=cleaned,
            summary={"lesson_topic": "成本分析"},
            summary_markdown="# 成本分析\n",
            partial_warnings=(MAP_FALLBACK_WARNING,) if first_run else (),
            reduce_diagnostics=None,
            summary_empty=False,
        )


class FakeOrchestrator:
    pipeline_version = "phase1-test-v1"
    prompt_version = "phase1-prompt-v1"
    model = "fake-model-v1"

    def __init__(self, *, fail: bool = False, strong: bool = True, uncertain: bool = False):
        self.fail = fail
        self.strong = strong
        self.uncertain = uncertain
        self.calls = []
        self.endpoint_fingerprints = []
        self.persist_raw = True

    def orchestrate(self, *, course_id, video_id, raw_vtt, memory_repo, **kwargs):
        self.calls.append((course_id, video_id, memory_repo.course_dir))
        self.endpoint_fingerprints.append(kwargs.get("endpoint_fingerprint"))
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback(0, 1, "reviewing")
        if self.fail:
            raise TimeoutError("simulated phase1 timeout")
        source = "本节课强调成本分析"
        if self.strong:
            start = source.index("成本")
            change = CorrectionChange(
                original="成本",
                corrected="成本法",
                type="technical_term",
                confidence=0.96,
                reason="字幕证据",
                start=start,
                end=start + 2,
                cue_ids=("cue-000001",),
                evidence=("成本",),
            )
            corrected = source[:start] + "成本法" + source[start + 2 :]
            changes = (change,)
            confidence = 0.96
            critic_confidence = 0.96
        else:
            corrected = source
            changes = ()
            confidence = 0.89
            critic_confidence = 0.89
        chunk = SubtitleChunk(
            chunk_id="a" * 64,
            course_id=course_id,
            video_id=video_id,
            chapter_id=None,
            start_index=0,
            end_index=0,
            previous_context="",
            current_text=source,
            next_context="",
            cue_ids=("cue-000001",),
        )
        correction = CorrectionResult(chunk.chunk_id, corrected, changes, (), confidence)
        critic = CriticResult(CriticDecision.PASS, critic_confidence, (), ())
        quality = QualityReport(
            score=confidence,
            passed=self.strong and not self.uncertain,
            metrics={"schema_pass": 1.0},
            warnings=(),
            schema_pass=True,
            critic_pass_rate=0.5 if self.uncertain else 1.0,
            uncertain_rate=0.5 if self.uncertain else 0.0,
            numeric_change_count=0,
            unsupported_change_count=0,
            status="completed_with_warnings" if self.uncertain else "completed",
        )
        if progress_callback:
            progress_callback(1, 1, "reviewing")
        event = {
            "stage": "test",
            "agent": "program",
            "model": self.model,
            "prompt_version": self.prompt_version,
            "token_usage": {},
            "latency_ms": 0.0,
            "confidence": confidence,
            "status": "ok",
        }
        return SimpleNamespace(
            chunks=(chunk,),
            corrections=(correction,),
            critics=(critic,),
            quality=quality,
            memory_delta=MemoryDelta((), (), (), (), ()),
            warnings=(),
            corrected_transcript=corrected,
            uncertain=(),
            memory_version=None,
            events=(event,),
            status="completed_with_warnings" if self.uncertain else "completed",
            raw_hash=hashlib.sha256(raw_vtt.encode("utf-8")).hexdigest(),
            cache_key="0" * 64,
        )


class TranscriptPhase1IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "transcripts"
        self.memory_root = Path(self.temporary.name) / "course-memory"
        self.fetches = 0

    def tearDown(self):
        self.temporary.cleanup()

    def fetch(self, source_id):
        self.fetches += 1
        return {"status": "ready", "vtt": VTT}

    def service(self, orchestrator, *, phase1=True, pipeline=None):
        return TranscriptService(
            self.root,
            subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(object(), "v1-model", "endpoint", True),
            pipeline=pipeline or CompleteV1Pipeline(),
            orchestrator=orchestrator,
            memory_root=self.memory_root,
            enable_phase1=phase1,
            sleeper=lambda _delay: None,
            autostart_worker=False,
        )

    @staticmethod
    def video(course="12", video="99"):
        return {"source_id": f"sjtu-video:{course}:{video}", "title": "测试课程录像"}

    def run_job(self, service, *, course="12", video="99"):
        batch = service.start_batch(course_id=course, course_name=f"课程{course}", videos=[self.video(course, video)])
        service.run_pending()
        return service.get_batch(batch["id"])["jobs"][0]

    def test_success_writes_v2_manifest_and_all_qualified_artifacts_without_copying_raw(self):
        orchestrator = FakeOrchestrator()
        service = self.service(orchestrator)
        job = self.run_job(service)
        video_dir = service._video_dir(job["source_id"])
        raw_before = (video_dir / "raw.vtt").read_bytes()
        manifest = json.loads((video_dir / "v2" / "manifest.json").read_text("utf-8"))

        self.assertEqual("completed", job["status"])
        self.assertEqual("completed", job["phase1_status"])
        self.assertEqual("completed", job["review_status"])
        self.assertEqual(1, job["review_completed"])
        self.assertEqual(1, job["review_total"])
        self.assertEqual(hashlib.sha256(raw_before).hexdigest(), manifest["raw_sha256"])
        self.assertFalse((video_dir / "v2" / "raw.vtt").exists())
        self.assertEqual(set(V2_ARTIFACT_FILES), set(manifest["artifacts"]))
        for kind, record in manifest["artifacts"].items():
            self.assertEqual(V2_ARTIFACT_FILES[kind], record["path"])
            self.assertEqual(Path(record["path"]).name, record["path"])
            self.assertEqual(32, len(record["id"]))
            path = video_dir / "v2" / record["path"]
            self.assertEqual(path.stat().st_size, record["size"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
        self.assertEqual(raw_before, (video_dir / "raw.vtt").read_bytes())

    def test_cache_reuse_and_single_corrupt_artifact_reruns_only_v2(self):
        orchestrator = FakeOrchestrator()
        service = self.service(orchestrator)
        first = self.run_job(service)
        video_dir = service._video_dir(first["source_id"])
        raw_before = (video_dir / "raw.vtt").read_bytes()
        second = self.run_job(service)
        self.assertTrue(second["phase1_reused"])
        self.assertEqual(1, len(orchestrator.calls))
        self.assertEqual(1, self.fetches)

        (video_dir / "v2" / "quality.json").write_text("{}", encoding="utf-8")
        third = self.run_job(service)
        self.assertFalse(third["phase1_reused"])
        self.assertEqual(2, len(orchestrator.calls))
        self.assertEqual(1, self.fetches)
        self.assertEqual(raw_before, (video_dir / "raw.vtt").read_bytes())
        listed = service.list_v2_artifacts(third["id"])
        self.assertTrue(listed["available"])
        self.assertTrue(all(item["available"] for item in listed["items"]))

    def test_orchestrator_failure_keeps_v1_completed_and_records_independent_warning(self):
        service = self.service(FakeOrchestrator(fail=True))
        job = self.run_job(service)
        video_dir = service._video_dir(job["source_id"])
        self.assertEqual("completed", job["status"])
        self.assertEqual("partial", job["phase1_status"])
        self.assertIn("旧版字幕与摘要仍可用", job["message"])
        self.assertTrue((video_dir / "summary.json").is_file())
        self.assertTrue((video_dir / "cleaned.md").is_file())
        self.assertEqual("partial", service.list_v2_artifacts(job["id"])["status"])

    def test_retry_forces_phase1_after_partial_cache(self):
        orchestrator = FakeOrchestrator(fail=True)
        service = self.service(orchestrator)
        first = self.run_job(service)
        self.assertEqual("partial", first["phase1_status"])
        self.assertEqual(1, len(orchestrator.calls))

        orchestrator.fail = False
        retried_batch = service.retry(first["id"])
        service.run_pending()
        retried = service.get_batch(retried_batch["id"])["jobs"][0]
        self.assertEqual("completed", retried["phase1_status"])
        self.assertFalse(retried["phase1_reused"])
        self.assertEqual(2, len(orchestrator.calls))

    def test_retry_reprocesses_v1_fallback_and_reuses_only_successful_chunks(self):
        orchestrator = FakeOrchestrator(fail=True)
        pipeline = FallbackOnceV1Pipeline()
        service = TranscriptService(
            self.root,
            subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(object(), "v1-model", "endpoint", True),
            pipeline=pipeline,
            orchestrator=orchestrator,
            memory_root=self.memory_root,
            enable_phase1=True,
            sleeper=lambda _delay: None,
            autostart_worker=False,
        )
        first = self.run_job(service)
        self.assertEqual("completed_with_warnings", first["status"])
        self.assertEqual(1, len(pipeline.calls))

        orchestrator.fail = False
        retried_batch = service.retry(first["id"])
        service.run_pending()
        retried = service.get_batch(retried_batch["id"])["jobs"][0]

        self.assertEqual("completed", retried["phase1_status"])
        self.assertEqual(3, len(pipeline.calls))
        self.assertEqual(set(), pipeline.calls[1])
        self.assertEqual(1, self.fetches)
        self.assertEqual(2, len(orchestrator.calls))
        video_dir = service._video_dir(first["source_id"])
        manifest = json.loads((video_dir / "manifest.json").read_text("utf-8"))
        self.assertEqual("completed", manifest["status"])
        self.assertEqual([], manifest.get("partial_warnings", []))

    def test_symlinked_v2_directory_is_rejected_before_agent_call(self):
        orchestrator = FakeOrchestrator()
        service = self.service(orchestrator)
        video_dir = service._video_dir(self.video()["source_id"])
        video_dir.mkdir(parents=True, mode=0o700)
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (video_dir / "v2").symlink_to(outside, target_is_directory=True)
        job = self.run_job(service)
        self.assertEqual("completed", job["status"])
        self.assertEqual("partial", job["phase1_status"])
        self.assertEqual([], orchestrator.calls)
        self.assertEqual([], list(outside.iterdir()))

    def test_course_memory_is_isolated_by_course(self):
        orchestrator = FakeOrchestrator()
        service = self.service(orchestrator)
        self.run_job(service, course="12", video="99")
        self.run_job(service, course="13", video="99")
        self.assertEqual({"12", "13"}, {call[0] for call in orchestrator.calls})
        self.assertNotEqual(orchestrator.calls[0][2], orchestrator.calls[1][2])
        self.assertEqual(self.memory_root.resolve(), orchestrator.calls[0][2].parents[2].resolve())

    def test_absent_v2_is_explicit_and_available_v2_read_is_validated(self):
        legacy = self.service(None, phase1=False)
        old_job = self.run_job(legacy)
        absent = legacy.list_v2_artifacts(old_job["id"])
        self.assertFalse(absent["available"])
        self.assertTrue(all(not item["available"] and item["id"] is None for item in absent["items"]))

        service = self.service(FakeOrchestrator())
        job = self.run_job(service, video="100")
        available = service.list_v2_artifacts(job["id"])
        quality = next(item for item in available["items"] if item["kind"] == "quality")
        payload = service.read_artifact(quality["id"])
        self.assertEqual("v2", payload["version"])
        self.assertIsInstance(payload["data"], dict)
        self.assertNotIn(str(self.root), json.dumps(available, ensure_ascii=False))

    def test_training_example_file_is_omitted_when_quality_gate_does_not_pass(self):
        service = self.service(FakeOrchestrator(strong=False))
        job = self.run_job(service)
        video_dir = service._video_dir(job["source_id"])
        manifest = json.loads((video_dir / "v2" / "manifest.json").read_text("utf-8"))
        self.assertNotIn("training_examples", manifest["artifacts"])
        self.assertFalse((video_dir / "v2" / "training_examples.jsonl").exists())
        listed = service.list_v2_artifacts(job["id"])
        training = next(item for item in listed["items"] if item["kind"] == "training_examples")
        self.assertFalse(training["available"])
        self.assertIsNone(training["id"])

    def test_passed_correction_updates_timed_subtitles_cues_and_learning_products(self):
        service = self.service(FakeOrchestrator())
        job = self.run_job(service)
        video_dir = service._video_dir(job["source_id"])
        manifest = json.loads((video_dir / "manifest.json").read_text())
        cues = json.loads((video_dir / "cues.json").read_text())
        self.assertEqual("corrected", job["learning_sync"])
        self.assertEqual("corrected", manifest["learning_source"])
        self.assertEqual(manifest["learning_source_sha256"], manifest["artifact_learning_source_sha256"])
        self.assertEqual("本节课强调成本法分析", cues[0]["text"])
        self.assertEqual("cue-000001", cues[0]["cue_id"])
        self.assertEqual(0, cues[0]["start_ms"])
        self.assertEqual(VTT, (video_dir / "raw.vtt").read_text())
        subtitles = service.corrected_subtitles(job["source_id"])
        self.assertIn("成本法分析", subtitles["vtt"])
        segments = service.learning_segments(job["source_id"])
        self.assertEqual("corrected", segments["source_version"])
        self.assertTrue(segments["segments"])
        self.assertFalse(segments["segments"][0]["provisional"])

    def test_failed_quality_or_corrupt_correction_cannot_become_subtitle_track(self):
        weak = self.service(FakeOrchestrator(strong=False))
        weak_job = self.run_job(weak)
        self.assertIsNone(weak.corrected_subtitles(weak_job["source_id"]))
        strong = self.service(FakeOrchestrator())
        strong_job = self.run_job(strong, video="100")
        quality = strong._video_dir(strong_job["source_id"]) / "v2" / "quality.json"
        quality.write_text("{}", encoding="utf-8")
        self.assertIsNone(strong.corrected_subtitles(strong_job["source_id"]))

    def test_valid_uncertain_review_needs_human_check_and_does_not_auto_retry(self):
        orchestrator = FakeOrchestrator(uncertain=True)
        service = self.service(orchestrator)
        job = self.run_job(service)
        result = service.learning_segments(job["source_id"])

        self.assertEqual("content_uncertain", result["error_code"])
        self.assertEqual("needs_review", result["stage"])
        self.assertTrue(result["processing_done"])
        self.assertFalse(result["auto_retryable"])
        self.assertTrue(result["retryable"])  # explicit user retry remains available
        self.assertIn("人工核对", result["error_message"])
        self.assertFalse(service._phase1_needs_upgrade(service._video_dir(job["source_id"]), service._read_json(service._manifest_path(job["source_id"]))))

        stored_batch, stored_job = service._find_job(job["id"])
        self.assertIsNone(stored_job.get("next_retry_at"))
        stored_batch["origin"] = "playback"
        stored_job["auto_retryable"] = False
        stored_job["next_retry_at"] = (service.now_provider() - timedelta(seconds=1)).isoformat()
        service._save_batch(stored_batch)
        service._schedule_due_playback_retries()
        self.assertEqual(1, len(service._all_batches()))
        self.assertEqual(1, len(orchestrator.calls))

    def test_v1_stage_failure_remains_auto_recoverable_when_phase1_is_uncertain(self):
        orchestrator = FakeOrchestrator(uncertain=True)
        service = self.service(orchestrator, pipeline=FallbackOnceV1Pipeline())
        job = self.run_job(service)
        video_dir = service._video_dir(job["source_id"])
        manifest = service._read_json(service._manifest_path(job["source_id"]))
        result = service.learning_segments(job["source_id"])

        self.assertEqual("completed_with_warnings", manifest["status"])
        self.assertEqual("v1_map_fallback", result["error_code"])
        self.assertEqual("map", result["stage"])
        self.assertFalse(result["processing_done"])
        self.assertTrue(result["phase1_processing_done"])
        self.assertTrue(result["retryable"])
        self.assertTrue(result["auto_retryable"])
        self.assertTrue(service._phase1_needs_upgrade(video_dir, manifest))
        batch, stored_job = service._find_job(job["id"])
        self.assertTrue(stored_job["auto_retryable"])
        self.assertIsNotNone(stored_job.get("next_retry_at"))

    def test_v1_guide_and_audit_schema_failures_are_preserved_with_uncertain_phase1(self):
        class GuideAndAuditFailurePipeline(CompleteV1Pipeline):
            def run(self, raw_vtt, ai_client, *, cached_chunks=None, on_chunk=None, on_cleaned=None, **kwargs):
                result = super().run(raw_vtt, ai_client, cached_chunks=cached_chunks, on_chunk=on_chunk, on_cleaned=on_cleaned, **kwargs)
                result.partial_warnings = ("guide schema failure", "audit schema failure")
                result.summary.setdefault("study_guide", {})["incomplete"] = True
                result.summary["review"] = {
                    "status": "completed_with_warnings",
                    "issues": [],
                    "coverage_ledger": [{
                        "chunk_index": 0,
                        "status": "not_audited",
                        "diagnostics": {"violation_code": "schema", "validator_path": "audit.chunk_index"},
                    }],
                    "guide_generation_diagnostics": {"violation_code": "schema", "validator_path": "course_map"},
                }
                return result

        service = self.service(FakeOrchestrator(uncertain=True), pipeline=GuideAndAuditFailurePipeline())
        job = self.run_job(service)
        video_dir = service._video_dir(job["source_id"])
        manifest = service._read_json(service._manifest_path(job["source_id"]))
        v1_summary = service._read_json(video_dir / "summary.json")
        self.assertEqual("schema", v1_summary["review"]["guide_generation_diagnostics"]["violation_code"])
        self.assertTrue(service._hashed_file_valid(video_dir / "summary.json", manifest["artifacts"]["summary_json"]["sha256"]))
        self.assertEqual("v1_guide_schema", service._v1_recovery_diagnostic(video_dir, manifest)["code"])
        result = service.learning_segments(job["source_id"])
        stage_codes = {(item["stage"], item["code"]) for item in result["failed_stages"]}

        self.assertEqual("v1_guide_schema", result["error_code"])
        self.assertEqual("guide_generation", result["stage"])
        self.assertEqual({("guide_generation", "v1_guide_schema"), ("audit", "v1_audit_schema")}, stage_codes)
        self.assertFalse(result["processing_done"])
        self.assertTrue(result["phase1_processing_done"])
        self.assertTrue(result["auto_retryable"])

    def playback_service(self, orchestrator, pipeline, now):
        return TranscriptService(
            self.root,
            subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(object(), "v1-model", "endpoint", True),
            pipeline=pipeline,
            orchestrator=orchestrator,
            memory_root=self.memory_root,
            enable_phase1=True,
            sleeper=lambda _delay: None,
            autostart_worker=False,
            now_provider=lambda: now[0],
            playback_retry_cooldown_seconds=2,
            playback_auto_retry_limit=3,
        )

    def latest_job(self, service):
        return service._all_batches()[0]["jobs"][0]

    def due_playback_parent(self, service, now):
        service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        service.run_pending()
        batch = next(item for item in service._all_batches() if item.get("origin") == "playback")
        job = batch["jobs"][0]
        job.update(
            status="partial",
            retryable=True,
            auto_retryable=True,
            auto_retry_scheduled=False,
            automatic_retry_count=0,
            next_retry_at=(now[0] - timedelta(seconds=1)).isoformat(),
        )
        service._write_json(service._batch_path(batch["id"]), batch)
        return batch, job

    def assert_clean_v1_needs_review(self, service, source_id):
        result = service.learning_segments(source_id)
        self.assertTrue(result["processing_done"])
        self.assertEqual("needs_review", result["stage"])
        self.assertEqual("content_uncertain", result["error_code"])
        self.assertFalse(result["auto_retryable"])
        self.assertTrue(result["retryable"])
        latest = self.latest_job(service)
        self.assertIsNot(latest.get("auto_retryable"), True)
        self.assertIsNone(latest.get("next_retry_at"))

    def test_scheduled_v1_recovery_with_uncertain_phase1_reuses_phase1_without_review_calls(self):
        now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
        orchestrator = FakeOrchestrator(uncertain=True)
        pipeline = FallbackOnceV1Pipeline()
        service = self.playback_service(orchestrator, pipeline, now)
        service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        service.run_pending()
        self.assertEqual(1, len(orchestrator.calls))
        self.assertTrue(self.latest_job(service)["auto_retryable"])

        now[0] += timedelta(seconds=3)
        service.run_pending()  # scheduler path: _schedule_due_playback_retries

        self.assertEqual(2, len(service._all_batches()))
        self.assertEqual(1, len(orchestrator.calls))  # Phase1 cache reused, no Term/Correction/Critic
        self.assertEqual(2, len(pipeline.calls))
        self.assertEqual(set(), pipeline.calls[1])  # fallback chunk regenerated, not reused
        self.assertEqual(1, self.fetches)
        latest = self.latest_job(service)
        self.assertTrue(latest["phase1_reused"])
        self.assertEqual(1, latest["automatic_retry_count"])
        manifest = service._read_json(service._manifest_path(latest["source_id"]))
        self.assertEqual("completed", manifest["status"])
        v2 = service._read_json(service._video_dir(latest["source_id"]) / "v2" / "manifest.json")
        self.assertIs(v2["quality"]["passed"], False)  # partial quality artifact kept, not forced to PASS
        self.assert_clean_v1_needs_review(service, latest["source_id"])

        now[0] += timedelta(hours=2)
        service.run_pending()
        service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        service.run_pending()
        self.assertEqual(2, len(service._all_batches()))
        self.assertEqual(1, len(orchestrator.calls))
        self.assertEqual(2, len(pipeline.calls))
        service.close()

    def test_phase1_cache_reused_for_same_endpoint_but_not_changed_endpoint(self):
        now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
        orchestrator = FakeOrchestrator(uncertain=True)
        service = self.playback_service(orchestrator, FallbackOnceV1Pipeline(), now)
        service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        service.run_pending()
        first = self.latest_job(service)
        video_dir = service._video_dir(first["source_id"])
        manifest = service._read_json(video_dir / "v2" / "manifest.json")
        self.assertEqual("endpoint", manifest["endpoint_fingerprint"])

        now[0] += timedelta(seconds=3)
        service.run_pending()
        self.assertEqual(1, len(orchestrator.calls))
        self.assertTrue(self.latest_job(service)["phase1_reused"])

        service.ai_context_provider = lambda: AIContext(object(), "v1-model", "different-endpoint", True)
        service.start_batch(course_id="12", course_name="课程12", videos=[self.video()])
        service.run_pending()
        self.assertEqual(2, len(orchestrator.calls))
        self.assertEqual(["endpoint", "different-endpoint"], orchestrator.endpoint_fingerprints)
        self.assertFalse(self.latest_job(service)["phase1_reused"])
        self.assertEqual("different-endpoint", service._read_json(video_dir / "v2" / "manifest.json")["endpoint_fingerprint"])
        service.close()

    def test_scheduler_rechecks_cancel_after_snapshot_before_creating_retry(self):
        now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
        service = self.playback_service(FakeOrchestrator(uncertain=True), FallbackOnceV1Pipeline(), now)
        service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        service.run_pending()
        batch = service._all_batches()[0]
        job = batch["jobs"][0]
        batch["origin"] = "playback"
        job.update(
            status="partial",
            retryable=True,
            auto_retryable=True,
            auto_retry_scheduled=False,
            automatic_retry_count=0,
            next_retry_at=(now[0] - timedelta(seconds=1)).isoformat(),
        )
        service._write_json(service._batch_path(batch["id"]), batch)

        original_all_batches = service._all_batches
        cancelled = [False]

        def snapshot_then_cancel():
            snapshot = original_all_batches()
            if not cancelled[0]:
                cancelled[0] = True
                service.cancel(job_id=job["id"])
            return snapshot

        service._all_batches = snapshot_then_cancel
        service._schedule_due_playback_retries()

        self.assertTrue(service._find_job(job["id"])[1]["cancel_requested"])
        self.assertEqual(1, len(original_all_batches()))
        self.assertEqual("cancelled", service.learning_segments(job["source_id"])["processing_state"])
        service.close()

    def test_scheduler_reconciles_child_written_before_parent_marker_after_interruption(self):
        now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
        service = self.playback_service(FakeOrchestrator(uncertain=True), FallbackOnceV1Pipeline(), now)
        parent, parent_job = self.due_playback_parent(service, now)
        parent_path = service._batch_path(parent["id"])
        write_json = service._write_json
        interrupted = [False]

        def fail_before_parent_marker(path, value):
            if Path(path) == parent_path and not interrupted[0]:
                children = [
                    item for item in service._all_batches()
                    if item.get("retry_parent_batch_id") == parent["id"]
                ]
                if children:
                    interrupted[0] = True
                    raise OSError("simulated process interruption before parent marker")
            return write_json(path, value)

        service._write_json = fail_before_parent_marker
        with self.assertRaisesRegex(OSError, "simulated process interruption"):
            service._schedule_due_playback_retries()
        service._write_json = write_json

        children = [
            item for item in service._all_batches()
            if item.get("retry_parent_batch_id") == parent["id"]
        ]
        self.assertEqual(1, len(children))
        child = children[0]
        self.assertEqual("playback", child["origin"])
        self.assertEqual(parent["id"], child["retry_parent_batch_id"])
        self.assertEqual(parent_job["id"], child["retry_parent_job_id"])
        self.assertEqual(1, child["retry_count"])
        self.assertEqual(parent["prepare_key"], child["prepare_key"])
        self.assertFalse(service._find_job(parent_job["id"])[1].get("auto_retry_scheduled", False))

        service._schedule_due_playback_retries()
        self.assertEqual(2, len(service._all_batches()))
        _, reconciled_job = service._find_job(parent_job["id"])
        self.assertTrue(reconciled_job["auto_retry_scheduled"])
        self.assertEqual(child["id"], next(
            item["id"] for item in service._all_batches()
            if item.get("retry_parent_job_id") == parent_job["id"]
        ))
        service.close()

    def test_cancelling_playback_parent_cancels_queued_retry_descendant_without_fetch(self):
        for cancel_by in ("job", "batch"):
            with self.subTest(cancel_by=cancel_by):
                self.root = Path(self.temporary.name) / f"transcripts-{cancel_by}"
                self.memory_root = Path(self.temporary.name) / f"memory-{cancel_by}"
                self.fetches = 0
                now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
                service = self.playback_service(FakeOrchestrator(uncertain=True), FallbackOnceV1Pipeline(), now)
                parent, parent_job = self.due_playback_parent(service, now)
                service._schedule_due_playback_retries()
                children = [
                    item for item in service._all_batches()
                    if item.get("retry_parent_job_id") == parent_job["id"]
                ]
                self.assertEqual(1, len(children))
                child_id = children[0]["id"]
                self.assertEqual("queued", children[0]["status"])
                fetches_before_cancel = self.fetches

                if cancel_by == "job":
                    service.cancel(job_id=parent_job["id"])
                else:
                    service.cancel(batch_id=parent["id"])

                child = service._read_json(service._batch_path(child_id))
                self.assertTrue(child["cancel_requested"])
                self.assertEqual("cancelled", child["status"])
                self.assertEqual("cancelled", child["jobs"][0]["status"])
                manual = service.start_batch(
                    course_id="12", course_name="课程12", videos=[self.video(video="100")]
                )
                # Cancellation propagation is scoped to automatic retry lineage.
                self.assertFalse(service._read_json(service._batch_path(manual["id"]))["cancel_requested"])
                service.cancel(batch_id=manual["id"])

                service.run_pending()
                self.assertEqual(fetches_before_cancel, self.fetches)
                self.assertEqual("cancelled", service.get_batch(child_id)["jobs"][0]["status"])
                service.close()

    def test_prepare_retry_child_is_idempotent_after_interruption(self):
        now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
        service = self.playback_service(FakeOrchestrator(uncertain=True), FallbackOnceV1Pipeline(), now)
        parent, parent_job = self.due_playback_parent(service, now)
        parent_path = service._batch_path(parent["id"])
        write_json = service._write_json
        interrupted = [False]

        def fail_before_parent_marker(path, value):
            if Path(path) == parent_path and not interrupted[0]:
                children = [
                    item for item in service._all_batches()
                    if item.get("retry_parent_job_id") == parent_job["id"]
                ]
                if children:
                    interrupted[0] = True
                    raise OSError("simulated prepare interruption")
            return write_json(path, value)

        service._write_json = fail_before_parent_marker
        with self.assertRaisesRegex(OSError, "simulated prepare interruption"):
            service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        service._write_json = write_json

        children = [
            item for item in service._all_batches()
            if item.get("retry_parent_job_id") == parent_job["id"]
        ]
        self.assertEqual(1, len(children))
        child = children[0]
        self.assertEqual(parent["id"], child["retry_parent_batch_id"])
        self.assertEqual(1, child["retry_count"])
        self.assertTrue(child["prepare_key"])
        self.assertFalse(service._find_job(parent_job["id"])[1].get("auto_retry_scheduled", False))

        retried = service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        self.assertEqual(child["id"], retried["id"])
        self.assertEqual(2, len(service._all_batches()))
        self.assertTrue(service._find_job(parent_job["id"])[1]["auto_retry_scheduled"])
        service.close()

    def test_cancelling_prepare_scheduled_retry_cancels_child_without_fetch(self):
        now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
        service = self.playback_service(FakeOrchestrator(uncertain=True), FallbackOnceV1Pipeline(), now)
        parent, parent_job = self.due_playback_parent(service, now)
        fetches_before_retry = self.fetches
        retried = service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        child = service._read_json(service._batch_path(retried["id"]))
        self.assertEqual(parent["id"], child["retry_parent_batch_id"])
        self.assertEqual(parent_job["id"], child["retry_parent_job_id"])
        self.assertEqual(1, child["retry_count"])

        service.cancel(job_id=parent_job["id"])
        child = service._read_json(service._batch_path(retried["id"]))
        self.assertTrue(child["cancel_requested"])
        self.assertEqual("cancelled", child["jobs"][0]["status"])
        service.run_pending()
        self.assertEqual(fetches_before_retry, self.fetches)
        service.close()

    def test_restart_interrupted_retry_waits_for_explicit_manual_retry(self):
        now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
        service = self.playback_service(FakeOrchestrator(uncertain=True), FallbackOnceV1Pipeline(), now)
        parent, parent_job = self.due_playback_parent(service, now)
        parent_path = service._batch_path(parent["id"])
        write_json = service._write_json

        def fail_before_parent_marker(path, value):
            if Path(path) == parent_path:
                children = [
                    item for item in service._all_batches()
                    if item.get("retry_parent_job_id") == parent_job["id"]
                ]
                if children:
                    raise OSError("simulated crash after retry child write")
            return write_json(path, value)

        service._write_json = fail_before_parent_marker
        with self.assertRaisesRegex(OSError, "simulated crash after retry child write"):
            service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        service._write_json = write_json
        children = [
            item for item in service._all_batches()
            if item.get("retry_parent_job_id") == parent_job["id"]
        ]
        self.assertEqual(1, len(children))
        child_id = children[0]["id"]
        child_path = service._batch_path(child_id)
        child = service._read_json(child_path)
        stale_owner = {
            "pid": os.getpid(),
            "process_start": "stale-process-start",
            "instance_nonce": "stale-instance",
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
            "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        }
        child["owner"] = stale_owner
        child["jobs"][0]["owner"] = stale_owner
        service._write_json(child_path, child)
        fetches_before_restart = self.fetches
        service.close()

        recovered = self.playback_service(FakeOrchestrator(uncertain=True), FallbackOnceV1Pipeline(), now)
        interrupted = recovered._read_json(child_path)
        interrupted_job = interrupted["jobs"][0]
        self.assertEqual("interrupted", interrupted["status"])
        self.assertEqual("interrupted", interrupted_job["status"])
        self.assertIs(interrupted_job["auto_retryable"], False)
        self.assertIs(interrupted_job["retryable"], True)
        self.assertIsNone(interrupted_job["next_retry_at"])

        first_prepare = recovered.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        recovered._schedule_due_playback_retries()
        second_prepare = recovered.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        self.assertEqual(child_id, first_prepare["id"])
        self.assertEqual(child_id, second_prepare["id"])
        self.assertEqual(2, len(recovered._all_batches()))
        self.assertTrue(recovered._find_job(parent_job["id"])[1]["auto_retry_scheduled"])
        self.assertEqual(fetches_before_restart, self.fetches)

        video_dir = recovered._video_dir(parent_job["source_id"])
        cache_before_retry = {
            str(path.relative_to(video_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in video_dir.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        manual_retry = recovered.retry(interrupted_job["id"])
        manual_job = recovered._read_json(recovered._batch_path(manual_retry["id"]))["jobs"][0]
        self.assertEqual(0, manual_job["automatic_retry_count"])
        self.assertNotIn("retry_parent_job_id", recovered._read_json(recovered._batch_path(manual_retry["id"])))
        self.assertNotIn("origin", recovered._read_json(recovered._batch_path(manual_retry["id"])))
        self.assertEqual(3, len(recovered._all_batches()))
        cache_after_retry = {
            str(path.relative_to(video_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in video_dir.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        self.assertEqual(cache_before_retry, cache_after_retry)
        self.assertEqual(fetches_before_restart, self.fetches)
        recovered.close()

    def test_playback_prepare_v1_recovery_with_uncertain_phase1_does_not_force_phase1(self):
        now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
        orchestrator = FakeOrchestrator(uncertain=True)
        pipeline = FallbackOnceV1Pipeline()
        service = self.playback_service(orchestrator, pipeline, now)
        service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        service.run_pending()

        now[0] += timedelta(seconds=3)
        retried = service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())  # prepare path
        self.assertEqual(2, len(service._all_batches()))
        stored = service._read_json(service._batch_path(retried["id"]))["jobs"][0]
        self.assertIs(stored["force_phase1"], False)
        self.assertIs(stored["retry_failed_v1"], True)
        service.run_pending()

        self.assertEqual(2, len(service._all_batches()))
        self.assertEqual(1, len(orchestrator.calls))
        self.assertEqual(set(), pipeline.calls[1])
        self.assert_clean_v1_needs_review(service, self.video()["source_id"])
        service.close()

    def test_phase1_technical_failure_still_auto_recovers_with_forced_review(self):
        now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
        orchestrator = FakeOrchestrator(fail=True)
        pipeline = FallbackOnceV1Pipeline()
        service = self.playback_service(orchestrator, pipeline, now)
        service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        service.run_pending()
        first = self.latest_job(service)
        self.assertEqual("partial", first["phase1_status"])
        self.assertTrue(first["auto_retryable"])
        self.assertTrue(service._automatic_retry_forces_phase1(first))

        orchestrator.fail = False
        now[0] += timedelta(seconds=3)
        service.run_pending()

        self.assertEqual(2, len(orchestrator.calls))
        latest = self.latest_job(service)
        self.assertEqual("completed", latest["phase1_status"])
        self.assertFalse(latest["phase1_reused"])
        self.assertEqual(set(), pipeline.calls[1])
        service.close()

    def test_uncertain_phase1_v1_recovery_still_rebinds_cache_to_current_model(self):
        now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
        orchestrator = FakeOrchestrator(uncertain=True)
        pipeline = FallbackOnceV1Pipeline()
        service = self.playback_service(orchestrator, pipeline, now)
        service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
        service.run_pending()
        self.assertEqual(1, len(orchestrator.calls))

        orchestrator.model = "fake-model-v2"  # binding changed: old partial review must not be reused
        now[0] += timedelta(seconds=3)
        service.run_pending()

        latest = self.latest_job(service)
        self.assertEqual(2, len(orchestrator.calls))
        self.assertFalse(latest["phase1_reused"])
        v2 = service._read_json(service._video_dir(latest["source_id"]) / "v2" / "manifest.json")
        self.assertIs(v2["quality"]["passed"], False)
        service.close()

    def test_schema_failed_phase1_quality_still_forces_phase1(self):
        uncertain = {"schema_pass": True, "passed": False, "critic_pass_rate": 0.5, "uncertain_rate": 0.5}
        schema_failed = dict(uncertain, schema_pass=False)
        base = {"phase1_status": "partial", "review_status": "completed"}
        self.assertFalse(TranscriptService._automatic_retry_forces_phase1(dict(base, quality=uncertain)))
        self.assertTrue(TranscriptService._automatic_retry_forces_phase1(dict(base, quality=schema_failed)))
        self.assertTrue(TranscriptService._automatic_retry_forces_phase1(dict(base, quality=None)))
        self.assertTrue(TranscriptService._automatic_retry_forces_phase1(dict(base, quality=uncertain, review_status="failed")))
        self.assertTrue(TranscriptService._automatic_retry_forces_phase1(dict(base, quality=uncertain, phase1_warning="timeout")))
        self.assertFalse(TranscriptService._automatic_retry_forces_phase1({"phase1_status": "completed"}))

    def test_v1_pipeline_receives_provider_json_mode_without_ai_calls(self):
        class JsonModeCapturePipeline(CompleteV1Pipeline):
            def run(self, raw_vtt, ai_client, *, cached_chunks=None, on_chunk=None, on_cleaned=None, **kwargs):
                self.json_mode = kwargs.get("json_mode")
                return super().run(raw_vtt, ai_client, cached_chunks=cached_chunks, on_chunk=on_chunk, on_cleaned=on_cleaned, **kwargs)

        pipeline = JsonModeCapturePipeline()
        service = self.service(FakeOrchestrator(), phase1=False, pipeline=pipeline)
        service.ai_context_provider = lambda: AIContext(object(), "fake-model", "endpoint", True, True)

        self.run_job(service)

        self.assertIs(pipeline.json_mode, True)

    def test_processing_does_not_make_verified_corrected_content_provisional(self):
        service = self.service(FakeOrchestrator())
        completed = self.run_job(service)
        batch, job = service._find_job(completed["id"])
        job.update(status="organizing", stage="reviewing")
        service._save_batch(batch)
        path = service._video_dir(job["source_id"]) / "manifest.json"
        manifest = service._read_json(path)
        manifest["status"] = "organizing"
        service._write_json(path, manifest)
        result = service.learning_segments(job["source_id"])
        self.assertTrue(result["is_processing"])
        self.assertEqual("ready", result["status"])
        self.assertEqual("corrected", result["source_version"])
        self.assertTrue(result["segments"])
        self.assertTrue(all(not segment["provisional"] for segment in result["segments"]))

    # -- C3: single overall processing_state contract -------------------------

    def assert_processing_state(self, result, state, *, done, auto, retry_after=None, content=True):
        self.assertEqual(state, result["processing_state"])
        self.assertIs(done, result["processing_done"])
        self.assertIs(auto, result["auto_retryable"])
        self.assertEqual(retry_after, result["retry_after"])
        if content:
            # Usable content is never dropped because of the overall state.
            self.assertEqual("ready", result["status"])
            self.assertTrue(result["segments"])
            self.assertTrue(result["available_ranges"])

    def set_stored_job(self, service, **changes):
        batch = service._all_batches()[0]
        batch["jobs"][0].update(changes)
        service._write_json(service._batch_path(batch["id"]), batch)

    def run_playback_retry_attempt(self, service, count):
        batch = service.start_batch(
            course_id="12", course_name="课程12", videos=[self.video()], automatic_retry_count=count,
        )
        stored = service._read_json(service._batch_path(batch["id"]))
        stored["origin"] = "playback"
        service._write_json(service._batch_path(batch["id"]), stored)
        service.run_pending()
        return self.latest_job(service)

    def test_processing_state_finished_outcomes_table(self):
        cases = (
            ("success", FakeOrchestrator(), "completed", "completed"),
            ("uncertain", FakeOrchestrator(uncertain=True), "finished_needs_review", "content_uncertain"),
        )
        for name, orchestrator, state, code in cases:
            with self.subTest(name):
                self.root = Path(self.temporary.name) / name / "transcripts"
                self.memory_root = Path(self.temporary.name) / name / "memory"
                service = self.service(orchestrator)
                job = self.run_job(service)
                result = service.learning_segments(job["source_id"])
                self.assert_processing_state(result, state, done=True, auto=False)
                self.assertTrue(result["phase1_processing_done"])
                self.assertEqual(code == "content_uncertain", result["error_code"] == code)
                self.assertIsNot(self.latest_job(service).get("auto_retryable"), True)
                self.assertIsNone(self.latest_job(service).get("next_retry_at"))

    def test_processing_state_recovery_budget_table(self):
        # count -> (state, auto_retryable): the V1 diagnostic stays retryable
        # throughout, only the job's real retry budget decides.
        cases = ((0, "recovering", True), (2, "recovering", True), (3, "recovery_exhausted", False))
        for count, state, auto in cases:
            with self.subTest(automatic_retry_count=count):
                self.root = Path(self.temporary.name) / f"budget-{count}" / "transcripts"
                self.memory_root = Path(self.temporary.name) / f"budget-{count}" / "memory"
                now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
                service = self.playback_service(FakeOrchestrator(uncertain=True), FallbackOnceV1Pipeline(), now)
                job = self.run_playback_retry_attempt(service, count)
                video_dir = service._video_dir(job["source_id"])
                manifest = service._read_json(service._manifest_path(job["source_id"]))
                self.assertIs(True, service._v1_recovery_diagnostic(video_dir, manifest)["auto_retryable"])
                self.assertEqual(count, job["automatic_retry_count"])
                result = service.learning_segments(job["source_id"])
                expected_retry = (now[0] + timedelta(seconds=2 * 2 ** count)).isoformat() if auto else None
                self.assert_processing_state(result, state, done=False, auto=auto, retry_after=expected_retry)
                self.assertTrue(result["phase1_processing_done"])
                self.assertTrue(result["retryable"])  # manual retry keeps existing rules
                self.assertEqual("v1_map_fallback", result["error_code"])
                self.assertIs(auto, job["auto_retryable"])
                self.assertEqual(expected_retry, job.get("next_retry_at"))
                self.assertIs(not auto, job.get("auto_retry_exhausted", False))
                if not auto:
                    now[0] += timedelta(hours=2)
                    service.run_pending()
                    service.prepare_for_playback(course_id="12", course_name="课程12", video=self.video())
                    self.assertEqual(1, len(service._all_batches()))
                    self.assertEqual(
                        "recovery_exhausted", service.learning_segments(job["source_id"])["processing_state"],
                    )
                service.close()

    def test_processing_state_cancel_and_in_progress_table(self):
        service = self.service(FakeOrchestrator())
        job = self.run_job(service)
        baseline = service.learning_segments(job["source_id"])
        future = (service.now_provider() + timedelta(seconds=1)).isoformat()
        past = (service.now_provider() - timedelta(seconds=1)).isoformat()
        cases = (
            ("stale organizing after job cancel", dict(status="organizing", cancel_requested=True), None, "cancelled"),
            ("stale organizing after batch cancel", dict(status="organizing"), True, "cancelled"),
            ("cancelled with due retry", dict(status="cancelled", auto_retryable=True, retryable=True, next_retry_at=past), None, "cancelled"),
            ("organizing", dict(status="organizing", stage="reviewing"), None, "processing"),
            ("queued", dict(status="queued", stage="queued"), None, "processing"),
        )
        for name, changes, batch_cancel, state in cases:
            with self.subTest(name):
                batch = service._all_batches()[0]
                batch["origin"] = "playback"
                batch.pop("cancel_requested", None)
                if batch_cancel:
                    batch["cancel_requested"] = True
                stored = batch["jobs"][0]
                for key in ("cancel_requested", "auto_retryable", "next_retry_at"):
                    stored.pop(key, None)
                stored.update(dict(status="completed", stage="completed"), **changes)
                service._write_json(service._batch_path(batch["id"]), batch)
                result = service.learning_segments(job["source_id"])
                self.assert_processing_state(result, state, done=False, auto=False)
                self.assertEqual(baseline["segments"], result["segments"])
                self.assertEqual(baseline["available_ranges"], result["available_ranges"])
                self.assertIs(state == "processing", result["is_processing"])
                if state == "cancelled":
                    service._schedule_due_playback_retries()
                    self.assertEqual(1, len(service._all_batches()))
        self.set_stored_job(service, status="completed", stage="completed")
        self.assertEqual("completed", service.learning_segments(job["source_id"])["processing_state"])

    def test_processing_state_waiting_source_and_ai_unconfigured_are_not_done_or_exhausted(self):
        self.assertEqual(
            "not_started",
            self.service(FakeOrchestrator()).learning_segments(self.video()["source_id"])["processing_state"],
        )
        remote = TranscriptService(
            self.root,
            subtitle_fetcher=lambda _source: {"status": "processing"},
            ai_context_provider=lambda: AIContext(object(), "v1-model", "endpoint", True),
            pipeline=CompleteV1Pipeline(),
            orchestrator=FakeOrchestrator(),
            memory_root=self.memory_root,
            sleeper=lambda _delay: None,
            autostart_worker=False,
        )
        job = self.run_job(remote)
        self.assertEqual("waiting_remote", job["status"])
        result = remote.learning_segments(job["source_id"])
        self.assert_processing_state(result, "waiting_source", done=False, auto=False, retry_after=job["next_retry_at"], content=False)
        self.set_stored_job(remote, status="failed", stage="remote_retry_exhausted", automatic_retry_count=3, auto_retryable=True)
        result = remote.learning_segments(job["source_id"])
        self.assert_processing_state(result, "waiting_source", done=False, auto=False, content=False)
        self.set_stored_job(remote, status="waiting_for_ai", stage="waiting_for_ai")
        self.assertEqual("ai_unconfigured", remote.learning_segments(job["source_id"])["processing_state"])

        for phase1 in (False, True):
            with self.subTest(ai_disabled_phase1=phase1):
                self.root = Path(self.temporary.name) / f"no-ai-{phase1}" / "transcripts"
                service = self.service(FakeOrchestrator(), phase1=phase1)
                service.ai_context_provider = lambda: AIContext(None, "", "", False)
                job = self.run_job(service)
                self.set_stored_job(service, automatic_retry_count=3)
                result = service.learning_segments(job["source_id"])
                self.assert_processing_state(result, "ai_unconfigured", done=False, auto=False)
                self.assertIsNone(result["retry_after"])


if __name__ == "__main__":
    unittest.main()
