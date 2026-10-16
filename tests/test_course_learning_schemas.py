from __future__ import annotations

import json
import unittest
from dataclasses import fields

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
    resolve_product_status,
    stable_json_dumps,
    unique_text_span,
)
from sjtu_learning_assistant.course_learning_prompts import ROLE_SCHEMAS


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
    def test_unique_text_span_computes_unicode_offsets_and_rejects_ambiguity(self):
        source = "开头\n机器学席是关键词。\n结尾"
        start, end = unique_text_span(source, "机器学席是关键词。", "机器学席")
        self.assertEqual("机器学席", source[start:end])
        self.assertEqual((3, 7), (start, end))
        for cue_text, original in (
            ("重复", "词"),
            ("唯一词唯一词", "唯一"),
            ("不在源中的 cue", "词"),
        ):
            with self.subTest(cue_text=cue_text, original=original), self.assertRaises(SchemaValidationError):
                unique_text_span("重复 cue 内有词，另一处有词", cue_text, original)

    def test_model_contract_fields_match_strict_dataclasses_at_every_depth(self):
        contracts = [
            (TermCandidate, ROLE_SCHEMAS["Terminology"]["properties"]["terms"]["items"]),
            (TermCandidate, ROLE_SCHEMAS["Correction"]["properties"]["uncertain"]["items"]["properties"]["candidates"]["items"]),
            (CriticResult, ROLE_SCHEMAS["Critic"]),
            (MemoryDelta, ROLE_SCHEMAS["Memory"]),
        ]
        for schema, contract in contracts:
            with self.subTest(schema=schema):
                expected = {field.name for field in fields(schema)}
                self.assertEqual(expected, set(contract["required"]))
                self.assertEqual(expected, set(contract["properties"]))
                self.assertFalse(contract["additionalProperties"])
        correction_wire = ROLE_SCHEMAS["Correction"]
        self.assertEqual({"changes", "uncertain", "confidence"}, set(correction_wire["required"]))
        self.assertEqual({"chunk_id", "corrected_text", "changes", "uncertain", "confidence"}, set(correction_wire["properties"]))
        proposal = correction_wire["properties"]["changes"]["items"]
        self.assertEqual(
            {"cue_id", "original", "replacement", "type", "confidence", "reason", "evidence"},
            set(proposal["required"]),
        )
        self.assertNotIn("chunk_id", correction_wire["required"])
        self.assertNotIn("corrected_text", correction_wire["required"])
        self.assertNotIn("start", proposal["properties"])
        self.assertNotIn("end", proposal["properties"])
        uncertain_wire = correction_wire["properties"]["uncertain"]["items"]
        self.assertEqual({"cue_id", "text", "candidates", "confidence"}, set(uncertain_wire["required"]))
        self.assertNotIn("start", uncertain_wire["properties"])
        self.assertNotIn("end", uncertain_wire["properties"])
        self.assertNotIn("cue_ids", uncertain_wire["properties"])
        self.assertEqual(
            {decision.value for decision in CriticDecision},
            set(ROLE_SCHEMAS["Critic"]["properties"]["decision"]["enum"]),
        )

    def test_null_values_and_boolean_offsets_do_not_bypass_strict_types(self):
        for field in ("chunk_id", "corrected_text", "changes", "uncertain", "confidence"):
            payload = result_payload()
            payload[field] = None
            with self.subTest(field=field), self.assertRaises(SchemaValidationError):
                CorrectionResult.from_dict(payload)
        payload = result_payload()
        payload["changes"][0]["start"] = False
        with self.assertRaises(SchemaValidationError):
            CorrectionResult.from_dict(payload)

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


    def test_shared_status_semantics_reserve_failed_for_no_usable_artifact(self):
        self.assertEqual("failed", resolve_product_status(False, False))
        self.assertEqual("partial", resolve_product_status(True, False, True))
        self.assertEqual("completed", resolve_product_status(True, True))
        self.assertEqual("completed_with_warnings", resolve_product_status(True, True, True))

        with self.assertRaises(SchemaValidationError):
            OrchestratorResult(
                course_id="course", video_id="video",
                chunks=(SubtitleChunk.from_dict(chunk_payload()),),
                corrections=(), critics=(),
                quality=QualityReport(0.0, False, dict(), (), status="failed"),
                memory_delta=MemoryDelta((), (), (), (), ()), warnings=(),
                corrected_transcript="仍有可用字幕", status="failed",
            )


if __name__ == "__main__":
    unittest.main()
