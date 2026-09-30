from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
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
from sjtu_learning_assistant.transcript_service import (
    AIContext,
    TranscriptError,
    TranscriptService,
    V2_ARTIFACT_FILES,
)


VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n本节课强调成本分析\n"


class CompleteV1Pipeline:
    def run(self, raw_vtt, ai_client, *, cached_chunks=None, on_chunk=None, on_cleaned=None, **kwargs):
        del raw_vtt, ai_client, cached_chunks, kwargs
        chunk = {
            "cleaned_transcript": [
                {"cue_id": "cue-000001", "start_ms": 0, "end_ms": 2000, "text": "本节课强调成本分析"}
            ],
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
            cues=[{"cue_id": "cue-000001", "start_ms": 0, "end_ms": 2000, "text": "本节课强调成本分析"}],
            chunks=[chunk],
            cleaned_markdown=cleaned,
            summary={"lesson_topic": "成本分析"},
            summary_markdown="# 成本分析\n",
            partial_warnings=(),
            reduce_diagnostics=None,
            summary_empty=False,
        )


class FakeOrchestrator:
    pipeline_version = "phase1-test-v1"
    prompt_version = "phase1-prompt-v1"
    model = "fake-model-v1"

    def __init__(
        self,
        *,
        fail: bool = False,
        strong: bool = True,
        status: str = "completed",
        seed_revision: str = "test-seeds@1.0.0",
    ):
        self.fail = fail
        self.strong = strong
        self.status = status
        self.seed_glossary_status = {
            "status": "ready",
            "revision": seed_revision,
        }
        self.calls = []
        self.persist_raw = True

    def orchestrate(self, *, course_id, video_id, raw_vtt, memory_repo, **kwargs):
        del kwargs
        self.calls.append((course_id, video_id, memory_repo.course_dir))
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
            passed=self.strong,
            metrics={"schema_pass": 1.0},
            warnings=(),
            schema_pass=True,
            critic_pass_rate=1.0,
            uncertain_rate=0.0,
            numeric_change_count=0,
            unsupported_change_count=0,
            status=self.status,
        )
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
            status=self.status,
            raw_hash=hashlib.sha256(raw_vtt.encode("utf-8")).hexdigest(),
            cache_key="0" * 64,
        )


class TrackingOrchestrator(FakeOrchestrator):
    def __init__(self):
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.thread_names = []
        self._tracking_lock = threading.Lock()

    def orchestrate(self, **kwargs):
        with self._tracking_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.thread_names.append(threading.current_thread().name)
        try:
            time.sleep(0.02)
            return super().orchestrate(**kwargs)
        finally:
            with self._tracking_lock:
                self.active -= 1


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

    def service(self, orchestrator, *, phase1=True):
        return TranscriptService(
            self.root,
            subtitle_fetcher=self.fetch,
            ai_context_provider=lambda: AIContext(object(), "v1-model", "endpoint", True),
            pipeline=CompleteV1Pipeline(),
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

    def test_explicit_phase1_disable_wins_over_injected_orchestrator(self):
        orchestrator = FakeOrchestrator()
        service = self.service(orchestrator, phase1=False)
        job = self.run_job(service)

        self.assertEqual("completed", job["status"])
        self.assertNotIn("phase1_status", job)
        self.assertEqual([], orchestrator.calls)

    def test_phase1_runs_serially_after_parallel_fetch_workers(self):
        orchestrator = TrackingOrchestrator()
        service = self.service(orchestrator)
        batch = service.start_batch(
            course_id="12",
            course_name="课程12",
            videos=[self.video(video="99"), self.video(video="100")],
        )
        service.run_pending()
        jobs = service.get_batch(batch["id"])["jobs"]

        self.assertEqual(["completed", "completed"], [item["status"] for item in jobs])
        self.assertEqual(1, orchestrator.max_active)
        self.assertEqual(2, len(orchestrator.calls))
        self.assertTrue(
            all(not name.startswith("subtitle-fetch") for name in orchestrator.thread_names)
        )

    def test_cancel_is_reread_after_v1_and_skips_phase1(self):
        orchestrator = FakeOrchestrator()
        service = self.service(orchestrator)
        original_organize = service._organize

        def organize_then_cancel(batch, job):
            original_organize(batch, job)
            service.cancel(job_id=job["id"])

        service._organize = organize_then_cancel
        job = self.run_job(service)
        video_dir = service._video_dir(job["source_id"])

        self.assertEqual("cancelled", job["status"])
        self.assertEqual([], orchestrator.calls)
        self.assertTrue((video_dir / "summary.md").is_file())
        self.assertTrue((video_dir / "raw.vtt").is_file())

    def test_v2_list_and_read_reject_manifest_not_bound_to_current_v1(self):
        service = self.service(FakeOrchestrator())
        job = self.run_job(service)
        video_dir = service._video_dir(job["source_id"])
        listed = service.list_v2_artifacts(job["id"])
        artifact_id = next(item["id"] for item in listed["items"] if item["available"])
        manifest_path = video_dir / "v2" / "manifest.json"
        original = json.loads(manifest_path.read_text("utf-8"))

        for field, value in (
            ("raw_sha256", "0" * 64),
            ("course_id", "13"),
            ("source_id", "sjtu-video:12:other"),
            ("video_id", "other"),
        ):
            with self.subTest(field=field):
                mutated = dict(original)
                mutated[field] = value
                manifest_path.write_text(json.dumps(mutated), encoding="utf-8")
                unavailable = service.list_v2_artifacts(job["id"])
                self.assertFalse(unavailable["available"])
                self.assertTrue(all(not item["available"] for item in unavailable["items"]))
                with self.assertRaises(TranscriptError):
                    service.read_artifact(artifact_id)
        manifest_path.write_text(json.dumps(original), encoding="utf-8")
        self.assertTrue(service.list_v2_artifacts(job["id"])["available"])

    def test_seed_revision_invalidates_phase1_manifest_cache(self):
        orchestrator = FakeOrchestrator(seed_revision="test-seeds@1.0.0")
        service = self.service(orchestrator)
        self.run_job(service)
        reused = self.run_job(service)
        self.assertTrue(reused["phase1_reused"])
        self.assertEqual(1, len(orchestrator.calls))

        orchestrator.seed_glossary_status["revision"] = "test-seeds@2.0.0"
        refreshed = self.run_job(service)
        manifest = json.loads(
            (
                service._video_dir(refreshed["source_id"])
                / "v2"
                / "manifest.json"
            ).read_text("utf-8")
        )
        self.assertFalse(refreshed["phase1_reused"])
        self.assertEqual(2, len(orchestrator.calls))
        self.assertEqual("test-seeds@2.0.0", manifest["seed_glossary_revision"])

    def test_partial_and_failed_phase1_results_are_never_reused(self):
        for status in ("partial", "failed"):
            with self.subTest(status=status):
                orchestrator = FakeOrchestrator(status=status)
                service = self.service(orchestrator)
                first = self.run_job(service, video="101" if status == "partial" else "102")
                second = self.run_job(service, video="101" if status == "partial" else "102")
                self.assertEqual(status, first["phase1_status"])
                self.assertEqual(status, second["phase1_status"])
                self.assertFalse(second["phase1_reused"])
                self.assertEqual(2, len(orchestrator.calls))

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


if __name__ == "__main__":
    unittest.main()
