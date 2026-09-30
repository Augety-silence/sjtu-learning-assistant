from __future__ import annotations

import copy
import unittest

from sjtu_learning_assistant.semantic_chunker import (
    SemanticChunker,
    chunk_transcript,
    preprocess_cues,
)
from sjtu_learning_assistant.transcript_pipeline import Cue


class SemanticChunkerTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual([], chunk_transcript([], course_id="c", video_id="v"))

    def test_accepts_transcript_cues_and_keeps_context_and_boundaries(self):
        cues = [
            Cue(0, 1000, "第一部分介绍回归。", "cue-1"),
            Cue(1100, 2000, "回归用于解释变量关系。", "cue-2"),
            Cue(5000, 6000, "接下来讨论神经网络。", "cue-3"),
            Cue(6100, 7000, "Neural networks learn representations.", "cue-4"),
        ]
        chunks = SemanticChunker(
            target_chars=24,
            hard_limit_chars=80,
            min_chunk_chars=8,
            pause_ms=1500,
            context_chars=200,
        ).chunk(cues, course_id="course", video_id="video")
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual("", chunks[0].previous_context)
        self.assertEqual(chunks[1].current_text, chunks[0].next_context)
        self.assertEqual(chunks[0].current_text, chunks[1].previous_context)
        self.assertEqual(0, chunks[0].start_index)
        self.assertIn("cue-3", chunks[1].cue_ids)
        self.assertIsNone(chunks[0].chapter_id)

    def test_ids_are_stable_and_path_like_ids_are_only_data(self):
        cues = [{
            "cue_id": "../../cue",
            "start_ms": 0,
            "end_ms": 1000,
            "text": "安全的数据内容。",
        }]
        kwargs = {"course_id": "../../course", "video_id": "/tmp/video", "chapter_id": "../chapter"}
        first = chunk_transcript(cues, **kwargs)
        second = chunk_transcript(copy.deepcopy(cues), **kwargs)
        self.assertEqual(first[0].chunk_id, second[0].chunk_id)
        self.assertRegex(first[0].chunk_id, r"^[0-9a-f]{64}$")
        self.assertEqual("../../course", first[0].course_id)
        self.assertEqual(("../../cue",), first[0].cue_ids)

    def test_preprocessing_preserves_raw_negation_and_input(self):
        cues = [{
            "cue_id": "c1",
            "start_ms": 0,
            "end_ms": 1000,
            "text": "  我们  不 能 删除 not   或者 no  ",
        }]
        original = copy.deepcopy(cues)
        normalized = preprocess_cues(cues)
        self.assertEqual(original, cues)
        self.assertEqual(original[0]["text"], normalized[0].raw_text)
        self.assertIn("不", normalized[0].cleaned_text)
        self.assertIn("not", normalized[0].cleaned_text)
        self.assertIn("no", normalized[0].cleaned_text)

    def test_obvious_overlapping_duplicates_are_merged_with_provenance(self):
        cues = [
            {"cue_id": "one", "start_ms": 0, "end_ms": 1000, "text": "重复字幕"},
            {"cue_id": "two", "start_ms": 900, "end_ms": 1800, "text": " 重复 字幕 "},
        ]
        normalized = preprocess_cues(cues)
        self.assertEqual(1, len(normalized))
        self.assertEqual(("one", "two"), normalized[0].source_cue_ids)
        chunks = chunk_transcript(cues, course_id="c", video_id="v")
        self.assertEqual(("one", "two"), chunks[0].cue_ids)

    def test_chinese_and_english_topic_changes_are_not_fixed_token_slices(self):
        cues = [
            {"cue_id": "zh1", "start_ms": 0, "end_ms": 900, "text": "线性回归用于估计系数。"},
            {"cue_id": "zh2", "start_ms": 900, "end_ms": 1800, "text": "回归系数描述变量关系。"},
            {"cue_id": "en1", "start_ms": 4000, "end_ms": 5000, "text": "Next, neural networks use hidden layers."},
            {"cue_id": "en2", "start_ms": 5000, "end_ms": 6000, "text": "Hidden layers learn useful representations."},
        ]
        chunks = chunk_transcript(
            cues,
            course_id="bilingual",
            video_id="lecture",
            target_chars=80,
            hard_limit_chars=120,
            min_chunk_chars=10,
            pause_ms=1200,
        )
        self.assertEqual(2, len(chunks))
        self.assertEqual(("zh1", "zh2"), chunks[0].cue_ids)
        self.assertEqual(("en1", "en2"), chunks[1].cue_ids)

    def test_very_long_single_cue_respects_hard_limit(self):
        raw = "这是一个完整句子。" * 80 + "do not remove negation " * 30
        cue = {"cue_id": "long", "start_ms": 0, "end_ms": 120000, "text": raw}
        chunks = chunk_transcript(
            [cue],
            course_id="course",
            video_id="video",
            target_chars=90,
            hard_limit_chars=120,
            min_chunk_chars=30,
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.current_text) <= 120 for chunk in chunks))
        self.assertTrue(all(chunk.cue_ids == ("long",) for chunk in chunks))
        self.assertEqual(raw, cue["text"])

    def test_invalid_cues_fail_without_coercion(self):
        with self.assertRaises(ValueError):
            chunk_transcript(
                [{"cue_id": "x", "start_ms": "0", "end_ms": 1, "text": "bad"}],
                course_id="c",
                video_id="v",
            )


if __name__ == "__main__":
    unittest.main()
