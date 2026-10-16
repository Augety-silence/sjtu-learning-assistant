from __future__ import annotations

import unittest

from sjtu_learning_assistant.transcript_pipeline import Cue
from sjtu_learning_assistant.transcript_playback import (
    build_learning_segments, corrected_source_cues, cues_to_vtt,
)


class PlaybackProjectionTests(unittest.TestCase):
    def setUp(self):
        self.cues = [Cue(0, 1000, "原问题有界", "cue-000001"), Cue(1000, 2000, "对偶问题可行", "cue-000002")]

    def test_uses_verified_cue_times_and_excludes_unknown_evidence(self):
        rows = [{"knowledge_points": [
            {"concept": "有界", "statement": "原问题有界", "evidence": [{"cue_id": "cue-000001", "start_ms": 99999}]},
            {"concept": "无证据", "statement": "不能显示", "evidence": [{"cue_id": "unknown"}]},
        ], "cleaned_transcript": [self.cues[0].as_dict()]}]
        result = build_learning_segments(self.cues, rows)
        self.assertEqual(result["available_ranges"], [[0, 1000]])
        self.assertEqual(len(result["segments"]), 1)
        self.assertEqual(result["segments"][0]["start_ms"], 0)
        self.assertEqual(result["segments"][0]["end_ms"], 1000)
        self.assertTrue(result["segments"][0]["provisional"])

    def test_partial_rows_publish_only_finished_chunks_in_source_order(self):
        rows = [{"cleaned_transcript": [self.cues[1].as_dict()]}, {"cleaned_transcript": [self.cues[0].as_dict()]}]
        result = build_learning_segments(self.cues, rows)
        self.assertEqual(result["available_ranges"], [[0, 2000]])
        self.assertEqual([s["start_ms"] for s in result["segments"]], [0, 1000])
        self.assertTrue(all(s["kind"] == "transcript" for s in result["segments"]))
        self.assertEqual(build_learning_segments(self.cues, [rows[0]])["available_ranges"], [[1000, 2000]])

    def test_applies_corrections_by_offsets_and_preserves_time_and_ids(self):
        chunks = [{"chunk_id": "c1", "cue_ids": ["cue-000001", "cue-000002"], "current_text": "原问题有界\n对偶问题可行"}]
        corrections = [{"chunk_id": "c1", "changes": [{"start": 0, "end": 3, "original": "原问题", "corrected": "原始问题", "cue_ids": ["cue-000001"]}]}]
        updated = corrected_source_cues(self.cues, chunks, corrections)
        self.assertIsNotNone(updated)
        self.assertEqual(updated[0], Cue(0, 1000, "原始问题有界", "cue-000001"))
        self.assertEqual(updated[1], self.cues[1])
        self.assertIn("00:00:00.000 --> 00:00:01.000", cues_to_vtt(updated))

    def test_final_summary_replaces_older_chunk_statements(self):
        evidence = [{"cue_id": "cue-000001"}]
        chunks = [{"cleaned_transcript": [self.cues[0].as_dict()], "knowledge_points": [
            {"concept": "术语", "statement": "旧版", "evidence": evidence}]}]
        summary = {"knowledge_points": [{"concept": "术语", "statement": "校正后", "evidence": evidence}]}
        result = build_learning_segments(self.cues, chunks, summary, provisional=False)
        self.assertEqual([s["content"] for s in result["segments"]], ["校正后"])
        self.assertFalse(result["segments"][0]["provisional"])

    def test_rejects_unmappable_corrections_instead_of_inventing_timestamps(self):
        chunks = [{"chunk_id": "c1", "cue_ids": ["cue-000001", "cue-000002"], "current_text": "原问题有界\n对偶问题可行"}]
        bad = [{"chunk_id": "c1", "changes": [{"start": 3, "end": 8, "original": "有界\n对偶", "corrected": "修订", "cue_ids": ["cue-000001", "cue-000002"]}]}]
        self.assertIsNone(corrected_source_cues(self.cues, chunks, bad))
        bad[0]["changes"][0]["original"] = "不匹配"
        self.assertIsNone(corrected_source_cues(self.cues, chunks, bad))


if __name__ == "__main__":
    unittest.main()
