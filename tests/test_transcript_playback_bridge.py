from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from desktop_app import DesktopBridge
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.models import Base, Course
from sjtu_learning_assistant.transcript_service import TranscriptService


class FakeTranscripts:
    def prepare_for_playback(self, **kwargs):
        return {"id": "batch", "jobs": [], "course_id": kwargs["course_id"]}

    def update_playback(self, source_id, position_seconds, playback_rate=1, seq=None):
        return {"source_id": source_id, "position_seconds": position_seconds, "playback_rate": playback_rate, "seq": seq, "stale": False}

    def learning_segments(self, source_id):
        return {"status": "processing", "segments": [], "source_id": source_id}


class FakeVideos:
    def list_course_videos(self, *_args, **_kwargs):
        return [{"source_id": "sjtu-video:12:99", "name": "课堂录像"}]


class PlaybackBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(Course(source_id="12", name="文本分析", raw_data={}))
            session.commit()
        self.service = DashboardService(self.engine, archive_root=Path(self.temp.name) / "archive", video_service=FakeVideos())
        self.service._transcript_service_instance = FakeTranscripts()
        self.bridge = DesktopBridge(self.service)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_playback_contract_crosses_real_bridge_and_course_validation(self):
        payload = {"course_id": 12, "source_id": "sjtu-video:12:99", "position_seconds": 123, "playback_rate": 2}
        prepared = self.bridge.invoke("transcript_playback_prepare", payload)
        self.assertTrue(prepared["ok"], prepared)
        updated = self.bridge.invoke("transcript_playback_update", {**payload, "seq": 1})
        self.assertTrue(updated["ok"], updated)
        result = self.bridge.invoke("transcript_learning_segments", {"course_id": 12, "source_id": payload["source_id"]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["status"], "processing")

    def test_rejects_foreign_video_non_finite_values_and_unknown_parameters(self):
        base = {"course_id": 12, "source_id": "sjtu-video:12:99"}
        bad = [{**base, "source_id": "sjtu-video:13:99"}, {**base, "position_seconds": float("nan")},
               {**base, "position_seconds": True}, {**base, "playback_rate": 0}, {**base, "endpoint": "https://invalid"}]
        for payload in bad:
            self.assertFalse(self.bridge.invoke("transcript_playback_prepare", payload)["ok"], payload)
        self.assertFalse(self.bridge.invoke("transcript_playback_prepare", {**base, "source_id": "sjtu-video:12:100"})["ok"])

    def test_playback_update_requires_safe_sequence(self):
        base = {"course_id": 12, "source_id": "sjtu-video:12:99", "position_seconds": 3, "playback_rate": 1}
        self.assertFalse(self.bridge.invoke("transcript_playback_update", base)["ok"])
        for seq in (True, -1, 1.5, 9_007_199_254_740_992):
            self.assertFalse(self.bridge.invoke("transcript_playback_update", {**base, "seq": seq})["ok"])

    def test_bridge_worker_artifact_projection_and_prepare_deduplication(self):
        transcripts = TranscriptService(
            root=Path(self.temp.name) / "transcripts", memory_root=Path(self.temp.name) / "memory",
            autostart_worker=False, enable_phase1=False,
            subtitle_fetcher=lambda _source: {"status": "ready", "vtt":
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n原问题有界。\n\n"
                "00:00:01.000 --> 00:00:02.000\n对偶问题可行。\n"},
        )
        self.service._transcript_service_instance = transcripts
        payload = {"course_id": 12, "source_id": "sjtu-video:12:99"}
        try:
            initial = self.bridge.invoke("transcript_learning_segments", payload)
            self.assertEqual("not_started", initial["data"]["processing_state"])
            prepared = self.bridge.invoke("transcript_playback_prepare", payload)
            self.assertTrue(prepared["ok"], prepared)
            queued = self.bridge.invoke("transcript_learning_segments", payload)["data"]
            self.assertEqual("processing", queued["processing_state"])
            self.assertFalse(queued["processing_done"])
            updated = self.bridge.invoke("transcript_playback_update", {**payload, "position_seconds": 1, "playback_rate": 2, "seq": 4})
            self.assertTrue(updated["ok"], updated)
            transcripts.run_pending()
            result = self.bridge.invoke("transcript_learning_segments", payload)
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["data"]["segments"])
            self.assertTrue(result["data"]["available_ranges"])
            self.assertEqual(result["data"]["source_raw_sha256"], hashlib.sha256((transcripts._video_dir(payload["source_id"]) / "raw.vtt").read_bytes()).hexdigest())
            self.assertTrue(result["data"]["learning_revision"])
            # Phase1 disabled: deterministic material is readable but never claims AI review success.
            self.assertEqual("ai_unconfigured", result["data"]["processing_state"])
            self.assertFalse(result["data"]["processing_done"])
            self.assertFalse(result["data"]["auto_retryable"])
            self.assertIsNone(result["data"]["retry_after"])
            before = len(list((transcripts.root / "batches").glob("*.json")))
            again = self.bridge.invoke("transcript_playback_prepare", payload)
            self.assertTrue(again["ok"], again)
            self.assertEqual(len(list((transcripts.root / "batches").glob("*.json"))), before)
        finally:
            transcripts.close()

    def test_bridge_cancel_reports_cancelled_state_without_auto_recovery(self):
        transcripts = TranscriptService(
            root=Path(self.temp.name) / "cancel-transcripts", memory_root=Path(self.temp.name) / "cancel-memory",
            autostart_worker=False, enable_phase1=False,
            subtitle_fetcher=lambda _source: {"status": "ready", "vtt":
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n合成字幕。\n"},
        )
        self.service._transcript_service_instance = transcripts
        payload = {"course_id": 12, "source_id": "sjtu-video:12:99"}
        try:
            prepared = self.bridge.invoke("transcript_playback_prepare", payload)
            self.assertTrue(prepared["ok"], prepared)
            cancelled = self.bridge.invoke("transcript_cancel", {"batch_id": prepared["data"]["id"]})
            self.assertTrue(cancelled["ok"], cancelled)
            transcripts.run_pending()
            state = self.bridge.invoke("transcript_learning_segments", payload)["data"]
            self.assertEqual("cancelled", state["processing_state"])
            self.assertFalse(state["processing_done"])
            self.assertFalse(state["auto_retryable"])
            self.assertIsNone(state["retry_after"])
        finally:
            transcripts.close()

    def test_playback_sequence_is_monotonic_and_isolated_by_source(self):
        transcripts = TranscriptService(
            root=Path(self.temp.name) / "seq-transcripts", memory_root=Path(self.temp.name) / "seq-memory",
            autostart_worker=False, enable_phase1=False,
        )
        try:
            first = transcripts.update_playback("sjtu-video:12:99", 20, 1, seq=8)
            stale = transcripts.update_playback("sjtu-video:12:99", 3, 2, seq=7)
            other = transcripts.update_playback("sjtu-video:12:100", 5, 1, seq=1)
            self.assertFalse(first["stale"])
            self.assertTrue(stale["stale"])
            self.assertEqual(20, stale["position_seconds"])
            self.assertFalse(other["stale"])
            self.assertEqual("sjtu-video:12:100", transcripts._foreground_source)
        finally:
            transcripts.close()


if __name__ == "__main__":
    unittest.main()
