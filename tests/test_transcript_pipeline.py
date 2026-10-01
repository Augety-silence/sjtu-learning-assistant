from __future__ import annotations

import json
import unittest
from pathlib import Path

from sjtu_learning_assistant.transcript_pipeline import (
    MAP_FALLBACK_WARNING,
    MAX_MESSAGE_CHARS,
    PROMPT_VERSION,
    REDUCE_FALLBACK_WARNING,
    SUMMARY_EMPTY_WARNING,
    Cue,
    TranscriptAIFormatError,
    TranscriptPipeline,
    _validated_json,
    _validated_map_response,
    chunk_cues,
    deterministic_summary,
    normalize_cues,
    normalize_map_output,
    normalize_summary_output,
    parse_plain_map_output,
    parse_vtt,
    validate_map,
    validate_summary,
)

VTT = (
    "WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\n这是课程要点\n\n"
    "2\n00:00:01.500 --> 00:00:03.000\n这是课程要点\n\n"
    "3\n00:00:03.000 --> 00:00:05.000\n不要改动术语、数字或公式\n"
)


def map_payload() -> dict[str, object]:
    return {
        "cleaned_transcript": [
            {
                "cue_id": "cue-000001",
                "start_ms": 0,
                "end_ms": 3000,
                "text": "总结本节课程内容",
            }
        ],
        "topics": ["测试重点"],
        "emphasized_points": [
            {
                "text": "保留术语和数字",
                "evidence": [
                    {
                        "cue_id": "cue-000003",
                        "start_ms": 3000,
                        "end_ms": 5000,
                        "quote": "不要改动",
                    }
                ],
            }
        ],
        "concepts": [],
        "cases_formulas_conclusions": [],
        "review_questions": list(("什么是证据使用？",)),
        "knowledge_points": list((dict(kind="principle", concept="证据使用", statement="保留术语和数字", evidence=list((dict(cue_id="cue-000003", start_ms=3000, end_ms=5000, quote="不要改动"),))),)),
        "classroom_examples": list(),
        "practice_items": list((dict(type="recall", prompt="需要保留什么？", related_knowledge_points=list(("证据使用",)), answer_key=list(("术语和数字",)), rubric=list(), evidence=list((dict(cue_id="cue-000003", start_ms=3000, end_ms=5000, quote="不要改动"),))),)),
    }


def summary_payload() -> dict[str, object]:
    return {
        "lesson_topic": "测试重点",
        "learning_objectives": ["掌握证据使用"],
        "emphasized_points": [
            {
                "text": "保留术语和数字",
                "evidence": [
                    {
                        "cue_id": "cue-000003",
                        "start_ms": 3000,
                        "end_ms": 5000,
                        "quote": "不要改动",
                    }
                ],
            }
        ],
        "concepts": [],
        "cases_formulas_conclusions": [],
        "review_questions": ["本节课会考什么？"],
        "timeline": [
            {
                "cue_id": "cue-000001",
                "start_ms": 0,
                "title": "导入",
                "summary": "术语与数字",
            }
        ],
    }


class FakeAI:
    model = "qwen"

    def __init__(self, map_responses: list[dict[str, object]] | None = None):
        self.calls: list[list[dict[str, object]]] = []
        self.map_responses = list(map_responses or [])

    def chat_completion(self, messages, **kwargs):
        self.calls.append(messages)
        prompt = messages[-1]["content"]
        if "最终汇总" in prompt:
            return {"content": json.dumps(summary_payload(), ensure_ascii=False), "response_metadata": {"finish_reason": "stop"}}
        if self.map_responses:
            return self.map_responses.pop(0)
        return {"content": json.dumps(map_payload(), ensure_ascii=False), "response_metadata": {"finish_reason": "stop"}}


class TranscriptPipelineTests(unittest.TestCase):
    def test_parse_normalize_and_deduplicate_rolling_asr(self):
        cues = normalize_cues(parse_vtt(VTT))
        self.assertEqual(2, len(cues))
        self.assertEqual("cue-000001", cues[0].cue_id)
        self.assertEqual(0, cues[0].start_ms)
        self.assertIn("不要改动术语、数字或公式", cues[1].text)

    def test_rejects_invalid_vtt(self):
        with self.assertRaises(ValueError):
            parse_vtt("00:00.000 --> 00:01.000\nbad")

    def test_chunks_at_cue_boundaries_under_safer_limit(self):
        text = "WEBVTT\n\n" + "\n\n".join(
            "00:00:%02d.000 --> 00:00:%02d.900\n%s-%d"
            % (index % 59, (index + 1) % 59, "字" * 800, index)
            for index in range(30)
        )
        chunks = chunk_cues(normalize_cues(parse_vtt(text)))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.as_prompt_text()) < 6000 for chunk in chunks))

    def test_extracts_fenced_prefixed_bom_json_with_nested_braces_in_string(self):
        payload = {"value": "公式集合 {a, {b}} 不应影响平衡"}
        content = "\ufeff说明文字\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n结束"
        result = _validated_json(content, lambda value: value, {"response_metadata": {"finish_reason": "stop"}})
        self.assertEqual(payload, result)

    def test_truncation_is_classified_with_safe_structural_diagnostics(self):
        sensitive = "不得出现在错误对象中的字幕"
        with self.assertRaises(TranscriptAIFormatError) as raised:
            _validated_json(
                '{"value":"' + sensitive,
                lambda value: value,
                {"response_metadata": {"finish_reason": "length"}},
            )
        error = raised.exception
        self.assertEqual("truncated", error.category)
        self.assertTrue(error.diagnostics["suspected_truncation"])
        self.assertEqual("length", error.diagnostics["finish_reason"])
        self.assertNotIn(sensitive, str(error))
        self.assertNotIn(sensitive, json.dumps(error.diagnostics, ensure_ascii=False))

    def test_unbalanced_object_is_classified_as_truncated_without_finish_reason(self):
        with self.assertRaises(TranscriptAIFormatError) as raised:
            _validated_json(chr(123) + chr(34) + "value" + chr(34) + ":" + chr(34) + "unfinished", lambda value: value, dict())
        self.assertEqual("truncated", raised.exception.category)
        self.assertTrue(raised.exception.diagnostics.get("suspected_truncation"))

    def test_schema_rejects_unknown_cleaned_cue_and_rebinds_evidence(self):
        cues = normalize_cues(parse_vtt(VTT))
        bad = map_payload()
        next(iter(bad.get("cleaned_transcript"))).update(cue_id="missing")
        with self.assertRaises(ValueError):
            validate_map(bad, cues)
        rebound = map_payload()
        point = next(iter(rebound.get("emphasized_points")))
        next(iter(point.get("evidence"))).update(end_ms=9000)
        normalized = validate_map(rebound, cues)
        point = next(iter(normalized.get("emphasized_points")))
        evidence = next(iter(point.get("evidence")))
        self.assertEqual(5000, evidence.get("end_ms"))
        invalid = map_payload()
        point = next(iter(invalid.get("emphasized_points")))
        next(iter(point.get("evidence"))).update(quote="不存在的原文")
        self.assertEqual(list(), validate_map(invalid, cues).get("emphasized_points"))

    def test_schema_diagnostics_report_only_field_path_type_and_length(self):
        payload = summary_payload()
        payload.update(review_questions=42)
        with self.assertRaises(TranscriptAIFormatError) as raised:
            _validated_json(
                json.dumps(payload, ensure_ascii=False),
                lambda value: validate_summary(value, normalize_cues(parse_vtt(VTT))),
                dict(finish_reason="stop"),
            )
        mismatch = raised.exception.diagnostics.get("schema_mismatch")
        self.assertEqual("review_questions", mismatch.get("path"))
        self.assertEqual("int", mismatch.get("actual_type"))
        self.assertNotIn("value", mismatch)

    def test_common_semantic_shapes_are_normalized_and_unknown_fields_dropped(self):
        cues = normalize_cues(parse_vtt(VTT))
        payload = dict(
            cleanedTranscript=dict(cueId="cue-000001", start="00:00:00.000", end="3s", description="规整内容"),
            topic=dict(name="主题"),
            key_points=dict(title="重点", evidence=dict(cueId="cue-000003", timestamp="00:03", text="不要改动")),
            terms=list((dict(name="概念"),)),
            formulas=dict(description="公式字段", evidence=dict(cueId="cue-000003", start="3s", description="不要改动")),
            questions=dict(question="问题？"),
            ignored="drop",
        )
        normalized = normalize_map_output(payload, cues)
        self.assertEqual(list(("主题",)), normalized.get("topics"))
        formula = next(iter(normalized.get("cases_formulas_conclusions")))
        self.assertEqual("公式字段", formula.get("text"))
        point = next(iter(normalized.get("emphasized_points")))
        evidence = next(iter(point.get("evidence")))
        self.assertEqual(dict(cue_id="cue-000003", start_ms=3000, end_ms=5000, quote="不要改动"), evidence)
        self.assertNotIn("ignored", normalized)

    def test_summary_aliases_objects_and_time_strings_are_normalized(self):
        cues = normalize_cues(parse_vtt(VTT))
        evidence = dict(cueId="cue-000003", time="00:03", text="不要改动")
        payload = dict(
            title="课程主题",
            goals=dict(description="目标"),
            highlights=dict(name="重点", evidence=evidence),
            terms=list(),
            conclusions=list(),
            questions="复习什么？",
            chapters=dict(cueId="cue-000003", timestamp="3s", name="阶段", description="说明"),
            unknown="drop",
        )
        summary = normalize_summary_output(payload, cues)
        self.assertEqual("课程主题", summary.get("lesson_topic"))
        self.assertEqual(list(("目标",)), summary.get("learning_objectives"))
        timeline = next(iter(summary.get("timeline")))
        self.assertEqual(3000, timeline.get("start_ms"))
        self.assertNotIn("unknown", summary)

    def test_plain_structured_map_fallback_is_strict_and_evidence_bound(self):
        cues = normalize_cues(parse_vtt(VTT))
        heading = lambda value: chr(91) + value + chr(93)
        plain = "\n".join((
            heading("TOPICS"),
            "- 字幕证据",
            heading("EMPHASIZED_POINTS"),
            "- 必须保留术语与数字 || cue-000003 || 不要改动术语、数字或公式",
            heading("CONCEPTS"),
            "- cue 是证据定位单位 || cue-000003 || 术语、数字或公式",
            heading("CASES_FORMULAS_CONCLUSIONS"),
            "- NONE",
            heading("REVIEW_QUESTIONS"),
            "- NONE",
        ))
        result = parse_plain_map_output(plain, cues)
        self.assertEqual(list(("字幕证据",)), result.get("topics"))
        evidence = next(iter(next(iter(result.get("emphasized_points"))).get("evidence")))
        self.assertEqual("cue-000003", evidence.get("cue_id"))
        self.assertEqual(3000, evidence.get("start_ms"))
        invalid = plain.replace("不要改动术语、数字或公式", "不存在的引文").replace("术语、数字或公式", "另一段不存在的引文")
        with self.assertRaises(ValueError):
            parse_plain_map_output(invalid, cues)

    def test_pipeline_accepts_plain_structured_response_without_second_call(self):
        heading = lambda value: chr(91) + value + chr(93)
        plain = "\n".join((
            heading("TOPICS"),
            "- 字幕证据",
            heading("EMPHASIZED_POINTS"),
            "- 必须保留术语与数字 || cue-000003 || 不要改动术语、数字或公式",
            heading("CONCEPTS"),
            "- NONE",
            heading("CASES_FORMULAS_CONCLUSIONS"),
            "- NONE",
            heading("REVIEW_QUESTIONS"),
            "- 为什么不能改动术语与数字？",
        ))
        ai = FakeAI(list((dict(content=plain, response_metadata=dict(finish_reason="stop")),)))
        result = TranscriptPipeline().run(VTT, ai)
        self.assertEqual(1, len(ai.calls))
        self.assertFalse(result.summary_empty)
        self.assertEqual("字幕证据", result.summary.get("lesson_topic"))
        self.assertEqual("必须保留术语与数字", next(iter(result.summary.get("emphasized_points"))).get("text"))

    def test_redacted_real_shape_fixture_reports_length_truncation(self):
        shape = json.loads((Path(__file__).parent / "fixtures" / "transcript_map_length_diagnostic.json").read_text())
        fixture = (chr(123) + chr(34) + "redacted" + chr(34) + ":" + chr(91) + chr(34) + "REDACTED").ljust(shape.get("content_length"), "x")
        with self.assertRaises(TranscriptAIFormatError) as raised:
            _validated_map_response(
                fixture,
                dict(response_metadata=dict(finish_reason=shape.get("finish_reason"))),
                normalize_cues(parse_vtt(VTT)),
            )
        diagnostics = raised.exception.diagnostics
        self.assertEqual("truncated", raised.exception.category)
        self.assertEqual(shape.get("content_length"), diagnostics.get("content_length"))
        self.assertEqual(shape.get("finish_reason"), diagnostics.get("finish_reason"))
        self.assertEqual(shape.get("has_code_fence"), diagnostics.get("has_code_fence"))
        self.assertEqual("unparsed", shape.get("top_level_type"))
        self.assertEqual("unavailable", shape.get("field_types").get("topics"))


    def test_invalid_map_is_summary_empty_and_never_dispatches_reduce(self):
        ai = FakeAI(list((dict(content=(chr(123) + chr(34) + "cleaned_transcript" + chr(34) + ":" + chr(91)), response_metadata=dict(finish_reason="length")),)))
        result = TranscriptPipeline().run(VTT, ai)
        self.assertTrue(result.summary_empty)
        self.assertEqual(MAP_FALLBACK_WARNING, result.chunks[0].get("partial_warning"))
        self.assertEqual("", result.summary.get("lesson_topic"))
        self.assertIn(SUMMARY_EMPTY_WARNING, result.partial_warnings)
        self.assertEqual(1, len(ai.calls))


    def test_valid_map_uses_deterministic_summary_without_reduce_request(self):
        ai = FakeAI()
        result = TranscriptPipeline().run(VTT, ai)
        self.assertEqual("测试重点", result.summary.get("lesson_topic"))
        self.assertEqual(list(), result.summary.get("learning_objectives"))
        self.assertEqual(list(), result.summary.get("timeline"))
        self.assertFalse(result.summary_empty)
        self.assertEqual(1, len(ai.calls))


    def test_deterministic_reduce_deduplicates_in_stable_order_without_invention(self):
        evidence = dict(cue_id="cue-000003", start_ms=3000, end_ms=5000, quote="不要改动")
        first = dict(topics=list(("主题甲", "主题乙")), emphasized_points=list((dict(text="重点甲", evidence=list((evidence,))),)), concepts=list((dict(text="概念甲", evidence=list((evidence,))),)), cases_formulas_conclusions=list(), review_questions=list(("问题甲",)))
        second = dict(topics=list(("主题甲",)), emphasized_points=list((dict(text="重点甲", evidence=list((evidence,))), dict(text="重点乙", evidence=list()))), concepts=list(), cases_formulas_conclusions=list((dict(text="结论甲", evidence=list((evidence,))),)), review_questions=list(("问题甲", "问题乙")))
        summary = deterministic_summary(list((first, second)))
        self.assertEqual("主题甲", summary.get("lesson_topic"))
        self.assertEqual(list(("重点甲",)), list(item.get("text") for item in summary.get("emphasized_points")))
        self.assertEqual(1, len(next(iter(summary.get("emphasized_points"))).get("evidence")))
        self.assertEqual(list(("概念甲",)), list(item.get("text") for item in summary.get("concepts")))
        self.assertEqual(list(("结论甲",)), list(item.get("text") for item in summary.get("cases_formulas_conclusions")))
        self.assertEqual(list(("问题甲", "问题乙")), summary.get("review_questions"))
        self.assertEqual(list(), summary.get("learning_objectives"))
        self.assertEqual(list(), summary.get("timeline"))

    def test_markdown_is_escaped_and_generated_from_validated_map(self):
        payload = map_payload()
        payload.update(topics=list(("<script>bad()</script>",)))
        ai = FakeAI(list((dict(content=json.dumps(payload, ensure_ascii=False)),)))
        result = TranscriptPipeline().run(VTT, ai)
        self.assertNotIn("<script>", result.summary_markdown)
        self.assertIn("&lt;script&gt;", result.summary_markdown)


    def test_map_produces_deterministic_summary_with_bounded_plain_prompt(self):
        ai = FakeAI()
        result = TranscriptPipeline().run(VTT, ai)
        self.assertEqual(PROMPT_VERSION, result.prompt_version)
        self.assertEqual("测试重点", result.summary.get("lesson_topic"))
        self.assertIn("# 测试重点", result.summary_markdown)
        self.assertEqual(1, len(ai.calls))
        prompt = next(reversed(ai.calls)).pop().get("content")
        self.assertIn("EMPHASIZED_POINTS", prompt)
        self.assertTrue(len(prompt) < MAX_MESSAGE_CHARS)


    def test_map_json_shape_omits_cleaned_transcript_from_model_without_losing_cues(self):
        payload = map_payload()
        payload.pop("cleaned_transcript")
        ai = FakeAI(list((dict(content=json.dumps(payload, ensure_ascii=False)),)))
        result = TranscriptPipeline().run(VTT, ai)
        self.assertFalse(result.summary_empty)
        self.assertEqual(2, len(result.chunks[0].get("cleaned_transcript")))
        point = next(iter(result.summary.get("emphasized_points")))
        evidence = next(iter(point.get("evidence")))
        source = next(cue.text for cue in normalize_cues(parse_vtt(VTT)) if cue.cue_id == evidence.get("cue_id"))
        self.assertIn(evidence.get("quote"), source)


    def test_offline_only_never_calls_ai_and_requires_every_chunk(self):
        cues = normalize_cues(parse_vtt(VTT))
        chunks = chunk_cues(cues)
        cached = dict((chunk.index, validate_map(map_payload(), chunk.cues)) for chunk in chunks)
        ai = FakeAI()
        result = TranscriptPipeline().run(VTT, ai, cached_chunks=cached, offline_only=True)
        self.assertEqual(0, len(ai.calls))
        self.assertFalse(result.summary_empty)
        self.assertNotIn(REDUCE_FALLBACK_WARNING, result.partial_warnings)
        with self.assertRaises(ValueError):
            TranscriptPipeline().run(VTT, ai, cached_chunks=dict(), offline_only=True)



    def test_structured_learning_products_are_grounded_linked_and_deduplicated(self):
        evidence_a = dict(cue_id="cue-1", start_ms=0, end_ms=1000, quote="均值是总和除以数量")
        evidence_b = dict(cue_id="cue-2", start_ms=1000, end_ms=2000, quote="比如三个人的平均分")
        chunks = list((
            dict(
                topics=list(("均值",)), emphasized_points=list(), concepts=list(),
                cases_formulas_conclusions=list(), review_questions=list(),
                knowledge_points=list((dict(concept="均值", kind="definition", statement="均值是总和除以数量。", evidence=list((evidence_a,))),)),
                classroom_examples=list((dict(example="三个人平均分的演示", related_knowledge_points=list(("均值",)), evidence=list((evidence_b,))),)),
                practice_items=list(),
            ),
            dict(
                topics=list(("均值",)), emphasized_points=list(), concepts=list(),
                cases_formulas_conclusions=list(), review_questions=list(),
                knowledge_points=list((dict(concept="均 值", kind="definition", statement="均值是总和除以数量", evidence=list((evidence_a,))),)),
                classroom_examples=list((dict(example="无关联例子", related_knowledge_points=list(("不存在",)), evidence=list((evidence_b,))),)),
                practice_items=list(),
            ),
        ))
        summary = deterministic_summary(chunks)
        self.assertEqual(1, len(summary.get("knowledge_points")))
        self.assertEqual(1, len(summary.get("classroom_examples")))
        practice = next(iter(summary.get("practice_items")))
        self.assertEqual("recall", practice.get("type"))
        self.assertEqual(list(("均值是总和除以数量。",)), practice.get("answer_key"))
        self.assertNotIn("ASR", json.dumps(practice, ensure_ascii=False))

    def test_duplicate_practice_merges_complementary_fields_stably(self):
        evidence_a = dict(cue_id="cue-1", start_ms=0, end_ms=1000, quote="均值与方差")
        evidence_b = dict(cue_id="cue-2", start_ms=1000, end_ms=2000, quote="比较两组数据")
        knowledge = list((
            dict(concept="均值", kind="definition", statement="均值描述中心", evidence=list((evidence_a,))),
            dict(concept="方差", kind="definition", statement="方差描述离散", evidence=list((evidence_a,))),
        ))
        chunks = list((
            dict(knowledge_points=knowledge, classroom_examples=list(), practice_items=list((dict(
                type="application", prompt="比较两组数据！",
                related_knowledge_points=list(("均值",)),
                answer_key=list(("先计算均值",)), rubric=list(("说明中心位置",)),
                evidence=list((evidence_a,)),
            ),))),
            dict(knowledge_points=knowledge, classroom_examples=list(), practice_items=list((dict(
                type="application", prompt=" 比较两组数据 ",
                related_knowledge_points=list(("均 值", "方差")),
                answer_key=list(("先 计算均值", "再计算方差")),
                rubric=list(("比较离散程度",)), evidence=list((evidence_b,)),
            ),))),
        ))
        summary = deterministic_summary(chunks)
        practice = next(iter(summary.get("practice_items")))
        self.assertEqual(list(("均值", "方差")), practice.get("related_knowledge_points"))
        self.assertEqual(list(("先计算均值", "再计算方差")), practice.get("answer_key"))
        self.assertEqual(list(("说明中心位置", "比较离散程度")), practice.get("rubric"))
        self.assertEqual(list(("cue-1", "cue-2")), list(row.get("cue_id") for row in practice.get("evidence")))
        self.assertEqual(summary, deterministic_summary(chunks))

    def test_learning_product_schema_rejects_unsupported_practice_type_and_unbound_evidence(self):
        cues = normalize_cues(parse_vtt(VTT))
        payload = map_payload()
        payload.update(
            knowledge_points=list((dict(
                kind="definition", concept="证据", statement="证据必须可核验",
                evidence=list((dict(cue_id="cue-000003", quote="不要改动"),)),
            ),)),
            classroom_examples=list(),
            practice_items=list((dict(
                type="asr_correction", prompt="修正错字",
                related_knowledge_points=list(("证据",)), answer_key=list(("改字",)),
                rubric=list(), evidence=list((dict(cue_id="cue-000003", quote="不要改动"),)),
            ),)),
        )
        with self.assertRaises(ValueError):
            normalize_map_output(payload, cues)
        payload.get("practice_items").clear()
        payload.get("knowledge_points").pop().get("evidence").clear()
        self.assertEqual(list(), normalize_map_output(payload, cues).get("knowledge_points"))

    def test_prompt_explicitly_separates_knowledge_examples_and_asr_training(self):
        ai = FakeAI()
        TranscriptPipeline().run(VTT, ai)
        prompt = ai.calls.pop().pop().get("content")
        self.assertIn("KNOWLEDGE_POINTS", prompt)
        self.assertIn("CLASSROOM_EXAMPLES", prompt)
        self.assertIn("PRACTICE_ITEMS", prompt)
        self.assertIn("ASR 纠错", prompt)


if __name__ == "__main__":
    unittest.main()
