from __future__ import annotations

import json
import unittest

from sjtu_learning_assistant.course_learning_schemas import (
    CorrectionChange,
    CorrectionResult,
    CriticDecision,
    CriticResult,
    MemoryDelta,
    OrchestratorResult,
    QualityReport,
    SchemaValidationError,
    SubtitleChunk,
    TermCandidate,
    UncertainSpan,
    stable_json_dumps,
)


def chunk_payload() -> dict[str, object]:
    return {
        "chunk_id": "a" * 64,
        "course_id": "course/../../只作为数据",
        "video_id": "video-1",
        "chapter_id": None,
        "start_index": 0,
        "end_index": 1,
        "previous_context": "",
        "current_text": "不要改动否定词。",
        "next_context": "Next context.",
        "cue_ids": ["../cue-a", "cue-b"],
    }


def term_payload() -> dict[str, object]:
    return {
        "original": "gradient decent",
        "canonical": "gradient descent",
        "category": "technical_term",
        "confidence": 0.96,
        "source": "course_material",
        "cue_ids": ["cue-b"],
        "evidence": ["lecture slide"],
    }


def result_payload() -> dict[str, object]:
    return {
        "chunk_id": "a" * 64,
        "corrected_text": "gradient descent",
        "changes": [{
            "original": "gradient decent",
            "corrected": "gradient descent",
            "type": "technical_term",
            "confidence": 0.96,
            "reason": "verified glossary",
            "start": 0,
            "end": 15,
            "cue_ids": ["cue-b"],
            "evidence": ["lecture slide"],
        }],
        "uncertain": [{
            "text": "VIF",
            "candidates": [term_payload()],
            "confidence": 0.4,
            "start": 17,
            "end": 20,
            "cue_ids": ["cue-b"],
        }],
        "confidence": 0.92,
    }


class CourseLearningSchemaTests(unittest.TestCase):
    def test_round_trip_is_strict_and_serialization_is_stable(self):
        chunk = SubtitleChunk.from_dict(chunk_payload())
        self.assertEqual(chunk_payload(), chunk.to_dict())
        first = chunk.to_json()
        second = stable_json_dumps(SubtitleChunk.from_dict(json.loads(first)))
        self.assertEqual(first, second)
        self.assertEqual(sorted(json.loads(first)), list(json.loads(first)))

    def test_bad_json_duplicate_keys_and_nan_are_rejected(self):
        for value in ("{", '{"chunk_id":"a","chunk_id":"b"}', '{"chunk_id":NaN}'):
            with self.subTest(value=value), self.assertRaises(SchemaValidationError):
                SubtitleChunk.from_json(value)

    def test_missing_unknown_and_wrong_type_are_rejected(self):
        for mutation in ("missing", "unknown", "wrong"):
            payload = chunk_payload()
            if mutation == "missing":
                payload.pop("video_id")
            elif mutation == "unknown":
                payload["command"] = "delete everything"
            else:
                payload["start_index"] = True
            with self.subTest(mutation=mutation), self.assertRaises(SchemaValidationError):
                SubtitleChunk.from_dict(payload)

    def test_range_confidence_and_enum_are_rejected(self):
        payload = term_payload()
        payload["confidence"] = 1.01
        with self.assertRaises(SchemaValidationError):
            TermCandidate.from_dict(payload)
        critic = {
            "decision": "APPROVE",
            "confidence": 1.0,
            "issues": [],
            "revision_instructions": [],
        }
        with self.assertRaises(SchemaValidationError):
            CriticResult.from_dict(critic)
        change = result_payload()["changes"][0]
        change["end"] = 14
        with self.assertRaises(SchemaValidationError):
            CorrectionChange.from_dict(change)

    def test_nested_correction_round_trip(self):
        result = CorrectionResult.from_dict(result_payload())
        self.assertIsInstance(result.changes[0], CorrectionChange)
        self.assertIsInstance(result.uncertain[0], UncertainSpan)
        self.assertIsInstance(result.uncertain[0].candidates[0], TermCandidate)
        self.assertEqual(result_payload(), result.to_dict())

    def test_all_aggregate_schemas_validate(self):
        critic = CriticResult(
            decision=CriticDecision.REVISE,
            confidence=0.8,
            issues=("术语证据不足",),
            revision_instructions=("保留原文",),
        )
        quality = QualityReport(
            score=0.9,
            passed=True,
            metrics={"faithfulness": 1.0, "coverage": 0.8},
            warnings=(),
        )
        delta = MemoryDelta(
            add_terms=({"term": "VIF", "confidence": 0.9},),
            update_terms=(),
            new_error_patterns=(),
            new_concepts=(),
            new_relationships=(),
        )
        aggregate = OrchestratorResult(
            course_id="course/../../data",
            video_id="video",
            chunks=(SubtitleChunk.from_dict(chunk_payload()),),
            corrections=(CorrectionResult.from_dict(result_payload()),),
            critics=(critic,),
            quality=quality,
            memory_delta=delta,
            warnings=(),
        )
        restored = OrchestratorResult.from_dict(aggregate.to_dict())
        self.assertEqual(aggregate.to_json(), restored.to_json())

    def test_pass_cannot_hide_issues_and_non_json_memory_is_rejected(self):
        with self.assertRaises(SchemaValidationError):
            CriticResult(CriticDecision.PASS, 1.0, ("issue",), ())
        with self.assertRaises(SchemaValidationError):
            MemoryDelta(
                add_terms=({"bad": object()},),
                update_terms=(),
                new_error_patterns=(),
                new_concepts=(),
                new_relationships=(),
            )


if __name__ == "__main__":
    unittest.main()
