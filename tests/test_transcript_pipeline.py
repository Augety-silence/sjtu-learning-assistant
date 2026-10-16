from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from sjtu_learning_assistant.transcript_pipeline import (
    AUDIT_WIRE_VERSION,
    MAP_FALLBACK_WARNING,
    MAX_MESSAGE_CHARS,
    PROMPT_VERSION,
    REDUCE_FALLBACK_WARNING,
    SUMMARY_EMPTY_WARNING,
    Cue,
    SchemaValidationError,
    TranscriptChunk,
    TranscriptAIFormatError,
    TranscriptPipeline,
    TranscriptPipelineCancelled,
    _validated_json,
    _validated_map_response,
    _normalize_study_guide,
    _normalize_audit_evidence,
    _fallback_study_guide,
    _guide_prompt,
    _json_content,
    _audit_guide,
    chunk_cues,
    deterministic_summary,
    normalize_cues,
    normalize_map_output,
    normalize_summary_output,
    parse_plain_map_output,
    parse_vtt,
    validate_map,
    validate_summary,
    render_summary,
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


def study_guide_payload() -> dict[str, object]:
    evidence = [dict(cue_id="cue-000003", quote="不要改动")]
    theme = dict(
        topic_id="topic-evidence-use",
        title="证据使用", conclusion="保留术语和数字", principle_and_context="在核对课程内容时使用。",
        why_and_consequences="改动会造成含义失真。", steps=["定位原句", "核对边界", "修改并复核"],
        classroom_example=None, boundaries="只处理字幕可验证内容。", connections="先理解目标，再检查表述。",
        source="课堂明确讲述", evidence=evidence,
    )
    return dict(
        course_map=["明确整体目标", "检查题目表述", "评估伦理", "转成研究问题"],
        themes=[theme],
        checklist=["核对问卷长度", "检查现有量表", "检查编码一致", "解释提问目的", "使用通俗语言", "按漏斗式排序", "发布前试填"],
        questions=[dict(type="recall", prompt=f"问题{i}", answer=f"答案{i}", source="自编练习或例子") for i in range(1, 6)],
        extensions=["如何检查边界？", "如何评估试填反馈？"],
    )


class FakeAI:
    model = "qwen"

    def __init__(self, map_responses: list[dict[str, object]] | None = None):
        self.calls: list[list[dict[str, object]]] = []
        self.map_responses = list(map_responses or [])

    def chat_completion(self, messages, **kwargs):
        self.calls.append(messages)
        prompt = messages[-1]["content"]
        if "阶段2" in prompt:
            transcript = prompt.split("本段字幕：\n", 1)[1]
            rows = re.findall(r"\[([^ ]+) (\d+)-(\d+)\] (.+)", transcript)
            first, last = rows[0], rows[-1]
            chunk_index = len([call for call in self.calls[:-1] if "阶段2" in call[-1]["content"]])
            evidence = [dict(cue_id=first[0], quote=first[3][:20])]
            payload = dict(
                chunk_index=chunk_index,
                time_range=dict(start_ms=int(first[1]), end_ms=int(last[2])),
                reviewed_cue_ids=list(dict.fromkeys(row[0] for row in rows)),
                coverage_confirmation=dict(confirmed=True, evidence=evidence),
                issues=[],
                revised_study_guide=study_guide_payload(),
            )
            return {"content": json.dumps(payload, ensure_ascii=False), "response_metadata": {"finish_reason": "stop"}}
        if "阶段1" in prompt:
            return {"content": json.dumps({"study_guide": study_guide_payload()}, ensure_ascii=False), "response_metadata": {"finish_reason": "stop"}}
        if self.map_responses:
            return self.map_responses.pop(0)
        return {"content": json.dumps(map_payload(), ensure_ascii=False), "response_metadata": {"finish_reason": "stop"}}


class TranscriptPipelineTests(unittest.TestCase):
    def test_audit_evidence_requires_current_cue_id_and_exact_quote_binding(self):
        long_cue = Cue(1000, 3000, "当前课程这一段提供了可逐字核对的学习证据。其范围仅限本cue。", "cue-current")
        other_cue = Cue(3000, 5000, "另一连续时间段有不同的引文内容，不能跨块借用作为证据。", "cue-other")
        short_cue = Cue(5000, 5500, "先看此例。", "cue-short")
        long_cue_125 = Cue(5500, 6000, "甲" * 125, "cue-125")
        over_limit_cue = Cue(6000, 7000, "乙" * 241, "cue-over-limit")
        thousand_char_cue = Cue(6500, 7000, "丁" * 1000, "cue-1000")
        safety_limit_cue = Cue(7000, 8000, "丙" * (MAX_MESSAGE_CHARS + 1), "cue-over-safety-limit")
        current_chunk = TranscriptChunk(4, (long_cue, short_cue, long_cue_125, over_limit_cue, thousand_char_cue, safety_limit_cue))

        valid_local_quote = long_cue.text[3:29]
        normalized = _normalize_audit_evidence(
            [dict(cue_id=long_cue.cue_id, quote=valid_local_quote)],
            current_chunk,
            "audit.coverage_confirmation.evidence",
        )
        self.assertEqual(long_cue.cue_id, normalized[0]["cue_id"])
        self.assertEqual((long_cue.start_ms, long_cue.end_ms), (normalized[0]["start_ms"], normalized[0]["end_ms"]))
        self.assertEqual(valid_local_quote, normalized[0]["quote"])

        source_ref = _normalize_audit_evidence(
            [dict(cue_id=long_cue.cue_id, quote_mode="source_cue")],
            current_chunk,
            "audit.coverage_confirmation.evidence",
        )
        self.assertEqual(long_cue.text, source_ref[0]["quote"])
        self.assertEqual((long_cue.start_ms, long_cue.end_ms), (source_ref[0]["start_ms"], source_ref[0]["end_ms"]))
        long_source_ref = _normalize_audit_evidence(
            [dict(cue_id=over_limit_cue.cue_id, quote_mode="source_cue")],
            current_chunk,
            "audit.coverage_confirmation.evidence",
        )
        self.assertEqual(over_limit_cue.text, long_source_ref[0]["quote"])
        self.assertEqual((over_limit_cue.start_ms, over_limit_cue.end_ms), (long_source_ref[0]["start_ms"], long_source_ref[0]["end_ms"]))
        self.assertEqual("丁" * 1000, _normalize_audit_evidence(
            [dict(cue_id=thousand_char_cue.cue_id, quote_mode="source_cue")],
            current_chunk,
            "audit.coverage_confirmation.evidence",
        )[0]["quote"])
        safe_legacy_pair = _normalize_audit_evidence(
            [dict(cue_id=long_cue.cue_id, quote=long_cue.text[:20]), dict(cue_id=long_cue.cue_id, quote=long_cue.text[20:40])],
            current_chunk,
            "audit.coverage_confirmation.evidence",
        )
        self.assertEqual(2, len(safe_legacy_pair))

        # Short exact source fragments are structurally valid; semantic sufficiency
        # remains the auditor's responsibility, not a character-count side effect.
        seventeen_char_quote = long_cue.text[:17]
        short_normalized = _normalize_audit_evidence(
            [dict(cue_id=long_cue.cue_id, quote=seventeen_char_quote)],
            current_chunk,
            "audit.issues[0].evidence",
        )
        self.assertEqual(seventeen_char_quote, short_normalized[0]["quote"])

        short_full = _normalize_audit_evidence(
            [dict(cue_id=short_cue.cue_id, quote=short_cue.text)],
            current_chunk,
            "audit.coverage_confirmation.evidence",
        )
        self.assertEqual(short_cue.text, short_full[0]["quote"])

        exact_125 = _normalize_audit_evidence(
            [dict(cue_id=long_cue_125.cue_id, quote=long_cue_125.text)],
            current_chunk,
            "audit.coverage_confirmation.evidence",
        )
        self.assertEqual(125, len(exact_125[0]["quote"]))

        invalid_rows = [
            # A valid quote cannot repair a mismatched cue ID.
            [dict(cue_id=other_cue.cue_id, quote=valid_local_quote)],
            # A wrong ID paired with a cross-chunk quote is also rejected.
            [dict(cue_id=other_cue.cue_id, quote=other_cue.text)],
            # A current ID cannot authorize a quote from another chunk.
            [dict(cue_id=long_cue.cue_id, quote=other_cue.text)],
            # Old timestamp/source metadata is not part of the audit evidence contract.
            [dict(cue_id=long_cue.cue_id, quote=valid_local_quote, start_ms=long_cue.start_ms)],
            # Empty, whitespace-padded, or concatenated fragments are never evidence.
            [dict(cue_id=long_cue.cue_id, quote="   ")],
            [dict(cue_id=long_cue.cue_id, quote=" " + valid_local_quote)],
            [dict(cue_id=long_cue.cue_id, quote=long_cue.text[:8] + long_cue.text[-8:])],
            [dict(cue_id=long_cue.cue_id, quote=valid_local_quote), dict(cue_id=long_cue.cue_id, quote=valid_local_quote)],
            [dict(cue_id=over_limit_cue.cue_id, quote=over_limit_cue.text)],
            [dict(cue_id=safety_limit_cue.cue_id, quote_mode="source_cue")],
            [dict(cue_id=other_cue.cue_id, quote_mode="source_cue")],
            [dict(cue_id=long_cue.cue_id, quote_mode="literal")],
            [dict(cue_id=long_cue.cue_id)],
            [dict(cue_id=long_cue.cue_id, quote_mode="source_cue", quote=valid_local_quote)],
            [dict(cue_id=long_cue.cue_id, quote_mode="source_cue"), dict(cue_id=long_cue.cue_id, quote_mode="source_cue")],
            [dict(cue_id=long_cue.cue_id, quote_mode="source_cue"), dict(cue_id=long_cue.cue_id, quote=valid_local_quote)],
            [dict(cue_id=long_cue.cue_id, quote=valid_local_quote), dict(cue_id=long_cue.cue_id, quote_mode="source_cue")],
            [dict(cue_id=over_limit_cue.cue_id, quote="乙" * 241)],
            # Additional source metadata is not accepted as part of an audit pair.
            [dict(cue_id=long_cue.cue_id, quote=valid_local_quote, start_ms=long_cue.start_ms)],
        ]
        for rows in invalid_rows:
            with self.subTest(row=rows[0]):
                with self.assertRaises(ValueError):
                    _normalize_audit_evidence(rows, current_chunk, "audit.coverage_confirmation.evidence")

    def test_audit_prompt_uses_local_topic_aliases_and_source_cue_refs(self):
        cues = [Cue(0, 1000, "第一主题的当前证据片段。", "cue-a"), Cue(1000, 2000, "第二主题的当前证据片段。", "cue-b")]
        guide = study_guide_payload()
        guide["themes"] = [
            dict(guide["themes"][0], topic_id="topic-cue-a", title="第一主题", conclusion="第一结论", evidence=[dict(cue_id="cue-a", quote=cues[0].text)]),
            dict(guide["themes"][0], topic_id="topic-cue-b", title="第二主题", conclusion="第二结论", evidence=[dict(cue_id="cue-b", quote=cues[1].text)]),
        ]

        class CaptureAuditAI:
            def __init__(inner_self):
                inner_self.prompts = []

            def chat_completion(inner_self, messages, **kwargs):
                inner_self.prompts.append(messages[-1]["content"])
                cue = cues[len(inner_self.prompts) - 1]
                return {"content": json.dumps(dict(
                    reviewed_cue_ids=[cue.cue_id],
                    coverage_confirmation=dict(confirmed=True, evidence=[dict(cue_id=cue.cue_id, quote=cue.text)]),
                    issues=[], theme_patches=[],
                ), ensure_ascii=False)}

        ai = CaptureAuditAI()
        _audit_guide(ai, guide, [TranscriptChunk(0, (cues[0],)), TranscriptChunk(1, (cues[1],))], cues)
        self.assertEqual(2, len(ai.prompts))
        self.assertIn('"topic_ref":"T1"', ai.prompts[0])
        self.assertNotIn("topic-cue-a", ai.prompts[0])
        self.assertIn("合法topic_ref", ai.prompts[0])
        self.assertIn('"quote_mode":"source_cue"', ai.prompts[0])
        self.assertIn("程序将从该cue自动填入完整原文和时间", ai.prompts[0])
        self.assertIn("程序会引用该cue完整原文，长度受现有消息安全上限约束", ai.prompts[0])

    def test_audit_schema_repair_receives_safe_mismatch_and_current_cues_but_still_rejects_fabricated_quote(self):
        cue = Cue(0, 1000, "当前审校范围中的真实原文证据。", "cue-current")
        theme = dict(
            topic_id="topic-current", title="当前主题", conclusion="当前结论",
            evidence=[dict(cue_id=cue.cue_id, quote=cue.text)],
        )
        guide = dict(themes=[theme])

        class QuoteRepairAI:
            def __init__(inner_self, repair_with_source):
                inner_self.prompts = []
                inner_self.repair_with_source = repair_with_source

            def chat_completion(inner_self, messages, **kwargs):
                prompt = messages[-1]["content"]
                inner_self.prompts.append(prompt)
                is_repair = "本次校验失败位置与要求" in prompt
                quote = cue.text if is_repair and inner_self.repair_with_source else "模型编造的错引文"
                payload = dict(
                    reviewed_cue_ids=[cue.cue_id],
                    coverage_confirmation=dict(confirmed=True, evidence=[dict(cue_id=cue.cue_id, quote=cue.text)]),
                    issues=[dict(
                        type="证据问题", target_topic_id="T1", problem="仅保留原问题含义",
                        evidence=[dict(cue_id=cue.cue_id, quote=quote)], fix="使用同一cue的准确引文",
                    )],
                    theme_patches=[],
                )
                return {"content": json.dumps(payload, ensure_ascii=False)}

        valid_ai = QuoteRepairAI(repair_with_source=True)
        _valid_guide, valid_review = _audit_guide(
            valid_ai, guide, [TranscriptChunk(0, (cue,))], [cue]
        )
        self.assertEqual("audited", valid_review["coverage_ledger"][0]["status"])
        self.assertEqual(2, len(valid_ai.prompts))
        repair_prompt = valid_ai.prompts[1]
        self.assertIn('"path":"audit.issues[0].evidence[0].quote"', repair_prompt)
        self.assertIn('"expected":"non-empty trimmed literal substring from the paired current cue, at most 240 characters"', repair_prompt)
        self.assertIn(cue.text, repair_prompt)
        self.assertIn('"topic_ref":"T1"', repair_prompt)
        self.assertIn("不得删除issue", repair_prompt)
        self.assertNotIn("actual_type", repair_prompt)

        invalid_ai = QuoteRepairAI(repair_with_source=False)
        _unchanged, invalid_review = _audit_guide(
            invalid_ai, guide, [TranscriptChunk(0, (cue,))], [cue]
        )
        self.assertEqual("not_audited", invalid_review["coverage_ledger"][0]["status"])
        self.assertEqual("audit.issues[0].evidence[0].quote", invalid_review["coverage_ledger"][0]["diagnostics"]["validator_path"])

    def test_audit_wrong_cue_id_cannot_be_rebound_by_a_unique_quote(self):
        cue_a = Cue(0, 1000, "当前块中的引文长度超过二十字且逐字可核对。", "cue-a")
        cue_b = Cue(1000, 2000, "下一块有另一段不同的引文长度同样超过二十字。", "cue-b")
        chunk = TranscriptChunk(0, (cue_a, cue_b))

        class WrongIdAuditAI:
            def chat_completion(self, messages, **kwargs):
                payload = dict(
                    reviewed_cue_ids=[cue_a.cue_id, cue_b.cue_id],
                    coverage_confirmation=dict(
                        confirmed=True,
                        evidence=[dict(cue_id=cue_a.cue_id, quote=cue_b.text)],
                    ),
                    issues=[],
                    theme_patches=[],
                )
                return {"content": json.dumps(payload, ensure_ascii=False), "response_metadata": {"finish_reason": "stop"}}

        _guide, review = _audit_guide(WrongIdAuditAI(), {"themes": []}, [chunk], [cue_a, cue_b])
        row = review["coverage_ledger"][0]
        self.assertEqual("not_audited", row["status"])
        self.assertEqual("audit.coverage_confirmation.evidence[0].quote", row["diagnostics"]["validator_path"])

    def test_audit_chunk_index_and_time_are_program_bound_not_model_identity(self):
        cue = Cue(2000, 3000, "本片段覆盖已经由当前字幕原句明确给出。", "cue-current")
        chunk = TranscriptChunk(5, (cue,))

        class WrongEchoAuditAI:
            def chat_completion(self, messages, **kwargs):
                payload = dict(
                    chunk_index=0,
                    time_range={"start_ms": 0, "end_ms": 1},
                    reviewed_cue_ids=[cue.cue_id],
                    coverage_confirmation=dict(
                        confirmed=True,
                        evidence=[dict(cue_id=cue.cue_id, quote=cue.text)],
                    ),
                    issues=[],
                    theme_patches=[],
                )
                return {"content": json.dumps(payload, ensure_ascii=False)}

        _guide, review = _audit_guide(WrongEchoAuditAI(), {"themes": []}, [chunk], [cue])
        row = review["coverage_ledger"][0]
        self.assertEqual("audited", row["status"])
        self.assertEqual(5, row["chunk_index"])
        self.assertEqual((cue.start_ms, cue.end_ms), (row["start_ms"], row["end_ms"]))

    def test_provider_json_mode_reaches_map_guide_and_audit_and_old_fake_signatures_survive(self):
        class JsonFormatAI(FakeAI):
            def __init__(self):
                super().__init__()
                self.formats = []

            def chat_completion(self, messages, **kwargs):
                self.formats.append(kwargs.get("response_format"))
                return super().chat_completion(messages, **kwargs)

        ai = JsonFormatAI()
        TranscriptPipeline().run(VTT, ai, json_mode=True)
        self.assertEqual(len(ai.calls), len(ai.formats))
        self.assertTrue(ai.formats)
        self.assertTrue(all(item == {"type": "json_object"} for item in ai.formats))

        class StrictLegacyFake:
            def __init__(self):
                self.delegate = FakeAI()
                self.calls = []

            def chat_completion(self, messages, max_tokens=4096, temperature=0, system_prompt=False):
                self.calls.append(messages)
                return self.delegate.chat_completion(messages, max_tokens=max_tokens, temperature=temperature, system_prompt=system_prompt)

        legacy = StrictLegacyFake()
        result = TranscriptPipeline().run(VTT, legacy, json_mode=True)
        self.assertTrue(legacy.calls)
        self.assertTrue(result.summary["review"]["coverage_ledger"])

    def test_audit_schema_diagnostics_keep_stage_chunk_path_type_and_call_index(self):
        cue_ids = [cue.cue_id for cue in normalize_cues(parse_vtt(VTT))]
        first_quote = normalize_cues(parse_vtt(VTT))[0].text[:20]

        class MalformedAuditAI(FakeAI):
            def chat_completion(self, messages, **kwargs):
                is_audit = "阶段2" in messages[-1]["content"]
                is_audit_repair = "只修复 JSON" in messages[0]["content"]
                if not (is_audit or is_audit_repair):
                    return super().chat_completion(messages, **kwargs)
                self.calls.append(messages)
                payload = dict(
                    reviewed_cue_ids=cue_ids,
                    coverage_confirmation=dict(
                        confirmed=True,
                        evidence=[dict(cue_id=cue_ids[0], quote=first_quote)],
                    ),
                    issues=[None],
                    theme_patches=[],
                )
                return {"content": json.dumps(payload, ensure_ascii=False), "response_metadata": {"finish_reason": "stop"}}

        result = TranscriptPipeline().run(VTT, MalformedAuditAI())
        diagnostic = result.summary["review"]["coverage_ledger"][0]["diagnostics"]
        self.assertEqual("study_guide_audit", diagnostic["stage"])
        self.assertEqual(0, diagnostic["chunk_index"])
        self.assertEqual("audit.issues[0]", diagnostic["validator_path"])
        self.assertEqual("issue object with type/target_topic_id/problem/evidence/fix", diagnostic["validator_expected"])
        self.assertEqual("NoneType", diagnostic["validator_actual_type"])
        self.assertEqual("stop", diagnostic["finish_reason"])
        self.assertEqual(4, diagnostic["call_index"])

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
        self.assertEqual(3, len(ai.calls))
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
        self.assertFalse(result.summary_empty)
        self.assertEqual(MAP_FALLBACK_WARNING, result.chunks[0].get("partial_warning"))
        self.assertEqual("", result.summary.get("lesson_topic"))
        self.assertIn("study_guide", result.summary)
        self.assertEqual(3, len(ai.calls))


    def test_valid_map_uses_deterministic_summary_without_reduce_request(self):
        ai = FakeAI()
        result = TranscriptPipeline().run(VTT, ai)
        self.assertEqual("测试重点", result.summary.get("lesson_topic"))
        self.assertEqual(list(), result.summary.get("learning_objectives"))
        self.assertEqual(list(), result.summary.get("timeline"))
        self.assertFalse(result.summary_empty)
        self.assertEqual(3, len(ai.calls))


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
        self.assertEqual(3, len(ai.calls))
        prompt = ai.calls[0][-1].get("content")
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
        prompt = ai.calls[0][-1].get("content")
        self.assertIn("KNOWLEDGE_POINTS", prompt)
        self.assertIn("CLASSROOM_EXAMPLES", prompt)
        self.assertIn("PRACTICE_ITEMS", prompt)
        self.assertIn("ASR 纠错", prompt)

    def test_json_map_prompt_has_complete_typed_contract_without_empty_template_signal(self):
        ai = FakeAI()
        TranscriptPipeline().run(VTT, ai, json_mode=True)
        prompt = ai.calls[0][-1].get("content")
        for field in (
            '"topics"', '"emphasized_points"', '"concepts"',
            '"cases_formulas_conclusions"', '"review_questions"',
            '"knowledge_points"', '"classroom_examples"', '"practice_items"',
        ):
            self.assertIn(field, prompt)
        self.assertIn("definition/principle/method/fact/formula/conclusion", prompt)
        self.assertIn("recall/application", prompt)
        source_cue = normalize_cues(parse_vtt(VTT))[0]
        self.assertIn('"cue_id":"' + source_cue.cue_id + '"', prompt)
        self.assertIn('"quote":"' + source_cue.text[:120] + '"', prompt)
        self.assertIn("不是已经提取完成的证据", prompt)
        self.assertIn("不能据此推断全片内容不足", prompt)

    def test_guide_prompt_uses_current_course_without_injecting_questionnaire_case(self):
        for topic in ("微积分的导数", "问卷长度"):
            with self.subTest(topic=topic):
                prompt = _guide_prompt(
                    dict(lesson_topic=topic, knowledge_points=[], classroom_examples=[], emphasized_points=[]),
                    dict(themes=[]),
                )
                self.assertIn(topic, prompt)
                for unrelated in ("现有量表", "编码一致", "漏斗排序", "发布前试填", "23:34", "某快餐", "多久吃一次"):
                    self.assertNotIn(unrelated, prompt)
                self.assertIn("当前字幕证据", prompt)
                self.assertIn('"type":"recall"', prompt)
                self.assertIn('"prompt":"问题（须由当前字幕主题支持）"', prompt)
                self.assertIn('"answer":"基于字幕的简答"', prompt)
                self.assertIn('"source":"根据课堂内容归纳"', prompt)
                self.assertIn("questions 是5-8个对象", prompt)
                self.assertIn("不同的学习路线要点", prompt)
                self.assertIn("不得拆成重复改写", prompt)
                self.assertIn("不要凑数", prompt)
                self.assertIn("classroom_example可为null", prompt)
                self.assertIn("original_question必须逐字出现在", prompt)
                self.assertIn("问题prompt与答案answer的来源分别判断", prompt)
                self.assertIn("仅题面逐字出现在当前cue才可标为课堂明确讲述", prompt)
                self.assertIn("默认标为根据课堂内容归纳", prompt)
                self.assertIn("改变场景或问法的题目标为自编练习或例子", prompt)
                self.assertIn("evidence为一个或多个{cue_id,quote}对象", prompt)
                self.assertIn("steps为1-8条非空字符串", prompt)

    def test_guide_prompt_embeds_a_real_evidence_bound_theme_example(self):
        cue = Cue(0, 2000, "方差衡量数据的离散程度。", "cue-real")
        fallback = _fallback_study_guide(
            {"knowledge_points": [dict(
                concept="方差", statement="方差衡量数据的离散程度。", source="课堂明确讲述",
                evidence=[dict(cue_id="cue-real", quote="方差衡量数据的离散程度。")],
            )]},
            [cue],
        )
        prompt = _guide_prompt(
            dict(lesson_topic="统计", knowledge_points=[], classroom_examples=[], emphasized_points=[]),
            fallback,
        )
        self.assertIn('"cue_id":"cue-real","quote":"方差衡量数据的离散程度。"', prompt)
        self.assertIn('"classroom_example":null', prompt)
        self.assertIn('"topic_id":"topic-', prompt)

    def test_dynamic_priority_publishes_ahead_first_and_merges_in_source_order(self):
        raw = "WEBVTT\n\n" + "\n\n".join(
            f"00:00:{index * 2:02d}.000 --> 00:00:{index * 2 + 1:02d}.000\n段{index}" + ("字" * 2800)
            for index in range(4)
        )
        expected = TranscriptPipeline().run(raw, None, deterministic_only=True)
        target = [len(expected.chunks) - 1]
        completed = []

        def publish(index, value):
            completed.append(index)
            target[0] = 1

        result = TranscriptPipeline().run(
            raw, None, deterministic_only=True,
            chunk_priority=lambda chunk: abs(chunk.index - target[0]), on_chunk=publish,
        )
        self.assertEqual(len(expected.chunks) - 1, completed[0])
        self.assertEqual(1, completed[1])
        self.assertEqual(len(expected.chunks), len(set(completed)))
        self.assertEqual(expected.chunks, result.chunks)
        self.assertEqual(expected.cleaned_markdown, result.cleaned_markdown)
        self.assertEqual(expected.summary, result.summary)
        self.assertEqual(expected.partial_warnings, result.partial_warnings)

    def test_cancel_after_chunk_keeps_published_chunk_and_does_not_finish(self):
        cancelled = [False]
        published = []
        cleaned = []

        def publish(index, value):
            published.append(index)
            cancelled[0] = True

        with self.assertRaises(TranscriptPipelineCancelled):
            TranscriptPipeline().run(
                VTT, None, deterministic_only=True, on_chunk=publish,
                should_cancel=lambda: cancelled[0], on_cleaned=cleaned.append,
            )
        self.assertEqual([0], published)
        self.assertEqual([], cleaned)

    def test_cancellation_after_ai_response_is_not_reported_as_fallback(self):
        cancelled = [False]

        class CancellingAI(FakeAI):
            def chat_completion(inner_self, messages, **kwargs):
                response = super(CancellingAI, inner_self).chat_completion(messages, **kwargs)
                cancelled[0] = True
                return response

        ai = CancellingAI()
        published = []
        with self.assertRaises(TranscriptPipelineCancelled):
            TranscriptPipeline().run(
                VTT, ai, should_cancel=lambda: cancelled[0], on_chunk=lambda *args: published.append(args),
            )
        self.assertEqual(1, len(ai.calls))
        self.assertEqual([], published)

    def test_audit_cancellation_propagates_instead_of_completed_with_warnings(self):
        cancelled = [False]

        class CancellingAuditAI(FakeAI):
            def chat_completion(inner_self, messages, **kwargs):
                response = super(CancellingAuditAI, inner_self).chat_completion(messages, **kwargs)
                if "阶段2" in messages[-1]["content"]:
                    cancelled[0] = True
                return response

        ai = CancellingAuditAI()
        with self.assertRaises(TranscriptPipelineCancelled):
            TranscriptPipeline().run(VTT, ai, should_cancel=lambda: cancelled[0])
        self.assertEqual(3, len(ai.calls))

    def test_corrected_cues_replace_evidence_and_do_not_reuse_old_chunk(self):
        cues = normalize_cues(parse_vtt(VTT))
        corrected = [Cue(cue.start_ms, cue.end_ms, cue.text.replace("不要改动", "必须保留"), cue.cue_id) for cue in cues]
        cached = {0: validate_map(map_payload(), cues)}
        payload = map_payload()
        for name in ("emphasized_points", "knowledge_points", "practice_items"):
            for item in payload[name]:
                for evidence in item["evidence"]:
                    evidence["quote"] = "必须保留"
        class CorrectedSourceAI(FakeAI):
            def chat_completion(inner_self, messages, **kwargs):
                response = super(CorrectedSourceAI, inner_self).chat_completion(messages, **kwargs)
                return dict(response, content=response["content"].replace("不要改动", "必须保留"))

        ai = CorrectedSourceAI([dict(content=json.dumps(payload, ensure_ascii=False))])
        result = TranscriptPipeline().run(VTT, ai, cached_chunks=cached, source_cues=corrected)
        self.assertNotIn("不要改动", result.cleaned_markdown)
        point = result.summary["knowledge_points"][0]
        self.assertEqual("必须保留", point["evidence"][0]["quote"])
        self.assertEqual(3, len(ai.calls))
        self.assertEqual([cue.cue_id for cue in cues], [cue["cue_id"] for cue in result.cues])

    def test_corrected_cues_cannot_add_remove_or_retime_source_cues(self):
        cues = normalize_cues(parse_vtt(VTT))
        invalid_sources = [
            cues[:-1],
            cues + [Cue(6000, 7000, "虚构字幕", "fake-cue")],
            [Cue(cues[0].start_ms + 1, cues[0].end_ms, cues[0].text, cues[0].cue_id)] + cues[1:],
        ]
        for candidate in invalid_sources:
            with self.subTest(source=candidate), self.assertRaises(ValueError):
                TranscriptPipeline().run(VTT, None, source_cues=candidate, deterministic_only=True)

    def test_unchanged_corrected_cues_keep_valid_cache_and_publish_without_ai(self):
        cues = normalize_cues(parse_vtt(VTT))
        cached = {0: validate_map(map_payload(), cues)}
        published = []
        ai = FakeAI()
        result = TranscriptPipeline().run(
            VTT, ai, cached_chunks=cached, source_cues=list(reversed(cues)), offline_only=True,
            on_chunk=lambda index, value: published.append(index),
        )
        self.assertEqual([], ai.calls)
        self.assertEqual([0], published)
        self.assertEqual(cached[0], result.chunks[0])

    def test_cancelled_before_start_never_requests_ai_or_publishes(self):
        ai = FakeAI()
        published = []
        with self.assertRaises(TranscriptPipelineCancelled):
            TranscriptPipeline().run(
                VTT, ai, should_cancel=lambda: True, on_chunk=lambda *args: published.append(args),
            )
        self.assertEqual([], ai.calls)
        self.assertEqual([], published)

    def test_json_repair_without_context_includes_only_safe_mismatch_metadata(self):
        class RepairAI:
            def __init__(inner_self):
                inner_self.prompts = []

            def chat_completion(inner_self, messages, **kwargs):
                inner_self.prompts.append(messages[-1]["content"])
                content = '{"missing":true}' if len(inner_self.prompts) == 1 else '{"ok":true}'
                return {"content": content}

        def validator(value):
            if value.get("ok") is not True:
                raise SchemaValidationError("result.ok", "boolean true", value.get("ok"))
            return {"ok": True}

        ai = RepairAI()
        self.assertEqual({"ok": True}, _json_content(ai, "legacy request", validator, schema_hint="ok is required"))
        self.assertEqual(2, len(ai.prompts))
        self.assertIn("只修复下列 JSON 片段的语法和字段结构", ai.prompts[1])
        self.assertIn('"path":"result.ok","expected":"boolean true"', ai.prompts[1])
        self.assertIn("本次校验失败位置与要求", ai.prompts[1])
        self.assertNotIn('"actual"', ai.prompts[1])
        self.assertNotIn("当前来源范围", ai.prompts[1])

    def test_guide_source_repair_changes_only_metadata_and_preserves_bad_prompt_rejection(self):
        guide = study_guide_payload()
        question = guide["questions"][0]
        question.update(prompt="一个原字幕没有出现的问题？", answer="保留这条答案原文。", source="课堂明确讲述")
        cues = normalize_cues(parse_vtt(VTT))
        original = json.dumps(guide, ensure_ascii=False)
        repair_context = {
            "allowed_question_sources": ["根据课堂内容归纳", "自编练习或例子", "课堂明确讲述"],
            "repair_limits": ["只可调整source元数据", "不得改写prompt或answer", "不匹配source cue时不得标为课堂明确讲述"],
        }

        class GuideRepairAI:
            def __init__(inner_self, repaired_source):
                inner_self.prompts = []
                inner_self.repaired_source = repaired_source

            def chat_completion(inner_self, messages, **kwargs):
                inner_self.prompts.append(messages[-1]["content"])
                if len(inner_self.prompts) == 1:
                    return {"content": original}
                repaired = json.loads(original)
                repaired["questions"][0]["source"] = inner_self.repaired_source
                return {"content": json.dumps(repaired, ensure_ascii=False)}

        validator = lambda value: _normalize_study_guide(value, cues)
        rejected = GuideRepairAI("课堂明确讲述")
        with self.assertRaises(TranscriptAIFormatError):
            _json_content(
                rejected, "guide request", validator,
                schema_hint="question source rule; source-only repair",
                repair_context=repair_context,
            )
        repair_prompt = rejected.prompts[1]
        self.assertIn('"path":"questions[0].source"', repair_prompt)
        self.assertIn("课堂明确讲述", repair_prompt)
        self.assertIn("只可调整source元数据", repair_prompt)
        self.assertIn("不得改写prompt或answer", repair_prompt)
        self.assertIn("不匹配source cue时不得标为课堂明确讲述", repair_prompt)

        accepted = GuideRepairAI("根据课堂内容归纳")
        result = _json_content(
            accepted, "guide request", validator,
            schema_hint="question source rule; source-only repair",
            repair_context=repair_context,
        )
        self.assertEqual(question["prompt"], result["questions"][0]["prompt"])
        self.assertEqual(question["answer"], result["questions"][0]["answer"])
        self.assertEqual("根据课堂内容归纳", result["questions"][0]["source"])

    GENUINE_PROMPT = "这是课程要点"
    FAKE_PROMPT = "合成题面甲：原字幕没有出现的问题？"
    FAKE_ANSWER = "合成答案甲：保留这条答案原文。"

    def _source_repair_guide(self, fake_indices=(0,)):
        guide = study_guide_payload()
        guide["questions"].append(dict(type="application", prompt="问题6", answer="答案6", source="根据课堂内容归纳"))
        for index in fake_indices:
            guide["questions"][index].update(
                prompt=f"{self.FAKE_PROMPT}{index}", answer=f"{self.FAKE_ANSWER}{index}", source="课堂明确讲述",
            )
        guide["questions"][1].update(prompt=self.GENUINE_PROMPT, source="课堂明确讲述")
        return guide

    @staticmethod
    def _source_repair_cues():
        return normalize_cues(parse_vtt(VTT))

    @staticmethod
    def _scripted_ai(first_content, repair_content, finish_reason="stop"):
        class ScriptedAI:
            def __init__(inner_self):
                inner_self.prompts = []

            def chat_completion(inner_self, messages, **kwargs):
                inner_self.prompts.append(messages[-1]["content"])
                if len(inner_self.prompts) == 1:
                    return {"content": first_content, "response_metadata": {"finish_reason": finish_reason}}
                return {"content": repair_content, "response_metadata": {"finish_reason": "stop"}}

        return ScriptedAI()

    def _run_source_repair(self, first, repaired, *, cues=None, guide_source_cues="default", first_content=None):
        cues = self._source_repair_cues() if cues is None else cues
        ai = self._scripted_ai(
            first_content if first_content is not None else json.dumps(first, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        )
        result = _json_content(
            ai, "guide request",
            lambda value: _normalize_study_guide(value.get("study_guide") if "study_guide" in value else value, cues),
            schema_hint="question source rule; source-only repair",
            repair_context={"stage": "study_guide_generation"},
            guide_source_cues=cues if guide_source_cues == "default" else guide_source_cues,
        )
        return result, ai

    def test_guide_source_only_repair_accepts_allowed_source_downgrade_with_everything_else_identical(self):
        cues = self._source_repair_cues()
        first = self._source_repair_guide()
        with self.assertRaises(SchemaValidationError) as raised:
            _normalize_study_guide(first, cues)
        self.assertEqual("questions[0].source", raised.exception.detail["path"])
        repaired = json.loads(json.dumps(first, ensure_ascii=False))
        repaired["questions"][0]["source"] = "根据课堂内容归纳"
        result, ai = self._run_source_repair(first, repaired)
        self.assertEqual(2, len(ai.prompts))
        self.assertEqual(_normalize_study_guide(repaired, cues), result)
        self.assertEqual(first["questions"][0]["prompt"], result["questions"][0]["prompt"])
        self.assertEqual(first["questions"][0]["answer"], result["questions"][0]["answer"])
        self.assertEqual(first["questions"][0]["type"], result["questions"][0]["type"])
        self.assertEqual("根据课堂内容归纳", result["questions"][0]["source"])
        self.assertEqual("课堂明确讲述", result["questions"][1]["source"])
        for key in ("course_map", "checklist", "extensions"):
            self.assertEqual(first[key], result[key])
        self.assertEqual(first["themes"][0]["title"], result["themes"][0]["title"])

        wrapped, _ai = self._run_source_repair({"study_guide": first}, {"study_guide": repaired})
        self.assertEqual(result, wrapped)

    def test_guide_source_only_repair_rejects_any_non_source_edit_that_would_pass_schema(self):
        cues = self._source_repair_cues()
        first = self._source_repair_guide()

        def downgraded():
            value = json.loads(json.dumps(first, ensure_ascii=False))
            value["questions"][0]["source"] = "根据课堂内容归纳"
            return value

        def rewrite_prompt_to_cue(value):
            value["questions"][0].update(prompt="不要改动术语、数字或公式", source="课堂明确讲述")

        def reorder(value):
            value["questions"][2], value["questions"][3] = value["questions"][3], value["questions"][2]

        mutations = {
            "rewrite_prompt_to_match_cue": (rewrite_prompt_to_cue, "questions[0].prompt"),
            "rewrite_prompt_keep_downgrade": (lambda value: value["questions"][0].update(prompt="改写后的题面？"), "questions[0].prompt"),
            "delete_prompt": (lambda value: value["questions"][0].update(prompt=""), "questions[0].prompt"),
            "change_answer": (lambda value: value["questions"][0].update(answer="改写后的答案。"), "questions[0].answer"),
            "change_type": (lambda value: value["questions"][0].update(type="application"), "questions[0].type"),
            "change_topic_title": (lambda value: value["themes"][0].update(title="改写主题"), "themes[0]"),
            "change_topic_conclusion": (lambda value: value["themes"][0].update(conclusion="改写结论"), "themes[0]"),
            "change_evidence": (lambda value: value["themes"][0].update(evidence=[dict(cue_id="cue-000003", quote="术语、数字")]), "themes[0]"),
            "change_course_map": (lambda value: value["course_map"].__setitem__(0, "改写地图"), "course_map[0]"),
            "change_checklist": (lambda value: value["checklist"].pop(), "checklist"),
            "change_extension": (lambda value: value["extensions"].__setitem__(1, "改写延伸？"), "extensions[1]"),
            "add_question": (lambda value: value["questions"].append(dict(type="recall", prompt="新增题？", answer="新增答。", source="自编练习或例子")), "questions"),
            "remove_failing_question": (lambda value: value["questions"].pop(0), "questions"),
            "remove_other_question": (lambda value: value["questions"].pop(), "questions"),
            "reorder_questions": (reorder, "questions[2].prompt"),
        }
        for name, (mutate, expected_path) in mutations.items():
            with self.subTest(name=name):
                repaired = downgraded()
                mutate(repaired)
                _normalize_study_guide(repaired, cues)
                with self.assertRaises(TranscriptAIFormatError) as raised:
                    self._run_source_repair(first, repaired)
                self.assertEqual("schema", raised.exception.category)
                mismatch = raised.exception.diagnostics["schema_mismatch"]
                self.assertEqual(expected_path, mismatch["path"])
                self.assertNotIn("actual", mismatch)

    def test_guide_source_only_repair_rejects_unrelated_source_changes_and_forged_classroom_claims(self):
        cues = self._source_repair_cues()
        first = self._source_repair_guide()
        first["questions"][4].update(prompt="不要改动术语、数字或公式", source="自编练习或例子")

        def repaired_with(**sources):
            value = json.loads(json.dumps(first, ensure_ascii=False))
            value["questions"][0]["source"] = "根据课堂内容归纳"
            for key, source in sources.items():
                value["questions"][int(key[1:])]["source"] = source
            return value

        cases = {
            "non_failing_question_source": (repaired_with(q3="根据课堂内容归纳"), "questions[3].source", True),
            "forged_classroom_claim_on_cue_prompt": (repaired_with(q4="课堂明确讲述"), "questions[4].source", True),
            "downgrade_genuine_classroom": (repaired_with(q1="根据课堂内容归纳"), "questions[1].source", True),
            "retain_unsupported_classroom": (repaired_with(q0="课堂明确讲述"), "questions[0].source", False),
            "unknown_source_value": (repaired_with(q0="猜测来源"), "questions[0].source", False),
        }
        for name, (repaired, expected_path, passes_validator) in cases.items():
            with self.subTest(name=name):
                if passes_validator:
                    _normalize_study_guide(repaired, cues)
                with self.assertRaises(TranscriptAIFormatError) as raised:
                    self._run_source_repair(first, repaired)
                self.assertEqual(expected_path, raised.exception.diagnostics["schema_mismatch"]["path"])

    def test_guide_source_only_repair_fixes_bounded_multi_error_set_without_downgrading_genuine_classroom(self):
        cues = self._source_repair_cues()
        first = self._source_repair_guide(fake_indices=(0, 2))
        repaired = json.loads(json.dumps(first, ensure_ascii=False))
        repaired["questions"][0]["source"] = "根据课堂内容归纳"
        repaired["questions"][2]["source"] = "自编练习或例子"
        result, _ai = self._run_source_repair(first, repaired)
        self.assertEqual(
            ["根据课堂内容归纳", "课堂明确讲述", "自编练习或例子"],
            [item["source"] for item in result["questions"][:3]],
        )
        self.assertEqual([item["prompt"] for item in first["questions"]], [item["prompt"] for item in result["questions"]])
        self.assertEqual([item["answer"] for item in first["questions"]], [item["answer"] for item in result["questions"]])

        over_repaired = json.loads(json.dumps(repaired, ensure_ascii=False))
        over_repaired["questions"][1]["source"] = "根据课堂内容归纳"
        _normalize_study_guide(over_repaired, cues)
        with self.assertRaises(TranscriptAIFormatError) as raised:
            self._run_source_repair(first, over_repaired)
        self.assertEqual("questions[1].source", raised.exception.diagnostics["schema_mismatch"]["path"])

        partially_repaired = json.loads(json.dumps(first, ensure_ascii=False))
        partially_repaired["questions"][0]["source"] = "根据课堂内容归纳"
        with self.assertRaises(TranscriptAIFormatError) as raised:
            self._run_source_repair(first, partially_repaired)
        self.assertEqual("questions[2].source", raised.exception.diagnostics["schema_mismatch"]["path"])

    def test_guide_repair_fails_closed_when_earlier_schema_error_masks_source_mismatch(self):
        cues = self._source_repair_cues()
        first = self._source_repair_guide()
        first["course_map"] = first["course_map"][:2]

        repairs = {}
        rewrite_prompt = json.loads(json.dumps(first, ensure_ascii=False))
        rewrite_prompt["course_map"] = ["地图甲", "地图乙", "地图丙", "地图丁"]
        rewrite_prompt["questions"][0]["prompt"] = cues[0].text
        repairs["rewrite prompt to make classroom claim pass"] = (first, rewrite_prompt, "course_map")

        change_answer = json.loads(json.dumps(first, ensure_ascii=False))
        change_answer["course_map"] = ["地图甲", "地图乙", "地图丙", "地图丁"]
        change_answer["questions"][0]["source"] = "根据课堂内容归纳"
        change_answer["questions"][0]["answer"] = "改写后的答案。"
        repairs["change answer while downgrading source"] = (first, change_answer, "course_map")

        change_topic = json.loads(json.dumps(first, ensure_ascii=False))
        change_topic["course_map"] = ["地图甲", "地图乙", "地图丙", "地图丁"]
        change_topic["questions"][0]["source"] = "根据课堂内容归纳"
        change_topic["themes"][0]["title"] = "改写主题"
        repairs["change topic while downgrading source"] = (first, change_topic, "course_map")

        bad_type = json.loads(json.dumps(self._source_repair_guide(), ensure_ascii=False))
        bad_type["questions"][0]["type"] = "unsupported"
        fix_type_and_prompt = json.loads(json.dumps(bad_type, ensure_ascii=False))
        fix_type_and_prompt["questions"][0]["type"] = "recall"
        fix_type_and_prompt["questions"][0]["prompt"] = cues[0].text
        repairs["question type masks same question source"] = (bad_type, fix_type_and_prompt, "questions[0].type")

        bad_answer = json.loads(json.dumps(self._source_repair_guide(), ensure_ascii=False))
        bad_answer["questions"][0]["answer"] = ""
        fix_answer_and_prompt = json.loads(json.dumps(bad_answer, ensure_ascii=False))
        fix_answer_and_prompt["questions"][0]["answer"] = "改写后的答案。"
        fix_answer_and_prompt["questions"][0]["prompt"] = cues[0].text
        repairs["answer failure masks source mismatch"] = (bad_answer, fix_answer_and_prompt, "questions[0].source")

        late_schema_error = json.loads(json.dumps(self._source_repair_guide(), ensure_ascii=False))
        late_schema_error["extensions"] = []
        source_and_extensions_fixed = json.loads(json.dumps(late_schema_error, ensure_ascii=False))
        source_and_extensions_fixed["questions"][0]["source"] = "根据课堂内容归纳"
        source_and_extensions_fixed["extensions"] = ["延伸一？", "延伸二？"]
        repairs["later schema error is not part of source repair"] = (
            late_schema_error, source_and_extensions_fixed, "questions[0].source"
        )

        nested_first = {"study_guide": first}
        nested_repair = json.loads(json.dumps(nested_first, ensure_ascii=False))
        nested_repair["study_guide"]["course_map"] = ["地图甲", "地图乙", "地图丙", "地图丁"]
        nested_repair["study_guide"]["questions"][0]["source"] = "根据课堂内容归纳"
        nested_repair["study_guide"]["themes"][0]["title"] = "改写嵌套主题"
        repairs["nested study_guide envelope"] = (nested_first, nested_repair, "course_map")

        invalid_questions = json.loads(json.dumps(first, ensure_ascii=False))
        invalid_questions["questions"] = "无法确认的问题基线"
        reconstructed = json.loads(json.dumps(self._source_repair_guide(), ensure_ascii=False))
        reconstructed["course_map"] = ["地图甲", "地图乙", "地图丙", "地图丁"]
        reconstructed["questions"][0]["source"] = "根据课堂内容归纳"
        repairs["non-list questions baseline"] = (invalid_questions, reconstructed, "questions")

        empty_questions = json.loads(json.dumps(first, ensure_ascii=False))
        empty_questions["questions"] = []
        repairs["empty questions baseline"] = (empty_questions, reconstructed, "course_map")

        too_few_questions = json.loads(json.dumps(first, ensure_ascii=False))
        too_few_questions["questions"][0]["source"] = "根据课堂内容归纳"
        too_few_questions["questions"] = too_few_questions["questions"][:4]
        repairs["fewer than five questions baseline"] = (too_few_questions, reconstructed, "course_map")

        too_many_questions = json.loads(json.dumps(first, ensure_ascii=False))
        too_many_questions["questions"][0]["source"] = "根据课堂内容归纳"
        too_many_questions["questions"].extend(
            dict(type="recall", prompt=f"额外问题{i}", answer=f"额外答案{i}", source="自编练习或例子")
            for i in range(3)
        )
        repairs["more than eight questions baseline"] = (too_many_questions, reconstructed, "course_map")

        invalid_question_item = json.loads(json.dumps(first, ensure_ascii=False))
        invalid_question_item["questions"][0] = "无法确认的题目对象"
        rebuilt_item = json.loads(json.dumps(reconstructed, ensure_ascii=False))
        repairs["non-object question baseline"] = (invalid_question_item, rebuilt_item, "course_map")

        def unwrap(value):
            return value.get("study_guide") if type(value) is dict and "study_guide" in value else value

        for name, (initial, repaired, expected_path) in repairs.items():
            with self.subTest(name=name):
                _normalize_study_guide(unwrap(repaired), cues)
                ai = self._scripted_ai(
                    json.dumps(initial, ensure_ascii=False),
                    json.dumps(repaired, ensure_ascii=False),
                )
                with self.assertRaises(TranscriptAIFormatError) as raised:
                    _json_content(
                        ai, "guide request", lambda value: _normalize_study_guide(unwrap(value), cues),
                        schema_hint="source-only repair",
                        repair_context={"stage": "study_guide_generation"},
                        guide_source_cues=cues,
                    )
                self.assertEqual(1, len(ai.prompts), "unsafe baseline must not trigger a repair call")
                self.assertEqual(expected_path, raised.exception.diagnostics["schema_mismatch"]["path"])

    def test_guide_source_only_guard_not_enabled_for_unprovable_first_responses(self):
        cues = self._source_repair_cues()
        first = self._source_repair_guide()
        rewritten = json.loads(json.dumps(first, ensure_ascii=False))
        rewritten["questions"][0].update(prompt="改写后的题面？", source="根据课堂内容归纳")

        truncated = self._scripted_ai(json.dumps(first, ensure_ascii=False)[:-40], json.dumps(rewritten, ensure_ascii=False))
        with self.assertRaises(TranscriptAIFormatError) as raised:
            _json_content(
                truncated, "guide request", lambda value: _normalize_study_guide(value, cues),
                schema_hint="source-only repair", repair_context={"stage": "study_guide_generation"},
                guide_source_cues=cues,
            )
        self.assertEqual("truncated", raised.exception.category)
        self.assertEqual(1, len(truncated.prompts))

        length_cut = self._scripted_ai(json.dumps(first, ensure_ascii=False), json.dumps(rewritten, ensure_ascii=False), finish_reason="length")
        with self.assertRaises(TranscriptAIFormatError) as raised:
            _json_content(
                length_cut, "guide request", lambda value: _normalize_study_guide(value, cues),
                schema_hint="source-only repair", repair_context={"stage": "study_guide_generation"},
                guide_source_cues=cues,
            )
        self.assertEqual("truncated", raised.exception.category)
        self.assertEqual(1, len(length_cut.prompts))

        over_limit = json.loads(json.dumps(first, ensure_ascii=False))
        over_limit["checklist"].append("长" * 7000)
        too_long = self._scripted_ai(json.dumps(over_limit, ensure_ascii=False), json.dumps(rewritten, ensure_ascii=False))
        with self.assertRaises(TranscriptAIFormatError):
            _json_content(
                too_long, "guide request", lambda value: _normalize_study_guide(value, cues),
                schema_hint="source-only repair", repair_context={"stage": "study_guide_generation"},
                guide_source_cues=cues,
            )
        self.assertEqual(1, len(too_long.prompts))

        invalid_json = '{"course_map": [1, 2,], "questions": "无效"}'
        replacements = {
            "prompt": lambda value: value["questions"][0].update(prompt="改写后的题面？"),
            "answer": lambda value: value["questions"][0].update(answer="替换后的答案。"),
            "theme": lambda value: value["themes"][0].update(title="替换后的主题"),
        }
        for field, replace_content in replacements.items():
            with self.subTest(field=field):
                candidate = json.loads(json.dumps(first, ensure_ascii=False))
                replace_content(candidate)
                ai = self._scripted_ai(invalid_json, json.dumps(candidate, ensure_ascii=False))
                with self.assertRaises(TranscriptAIFormatError):
                    _json_content(
                        ai, "guide request", lambda value: _normalize_study_guide(value, cues),
                        schema_hint="source-only repair",
                        repair_context={"stage": "study_guide_generation"},
                        guide_source_cues=cues,
                    )
                self.assertEqual(1, len(ai.prompts))

        other_error = json.loads(json.dumps(first, ensure_ascii=False))
        other_error["questions"][0]["source"] = "根据课堂内容归纳"
        other_error["extensions"] = ["只有一条？"]
        fixed_other = json.loads(json.dumps(other_error, ensure_ascii=False))
        fixed_other["extensions"] = ["延伸一？", "延伸二？"]
        result, _ai = self._run_source_repair(other_error, fixed_other)
        self.assertEqual(["延伸一？", "延伸二？"], result["extensions"])

        for context in (None, ()):
            with self.subTest(guide_source_cues=context):
                with self.assertRaises(TranscriptAIFormatError):
                    self._run_source_repair(first, first, guide_source_cues=context)
                ai = self._scripted_ai(
                    json.dumps(first, ensure_ascii=False),
                    json.dumps(rewritten, ensure_ascii=False),
                )
                with self.assertRaises(TranscriptAIFormatError):
                    _json_content(
                        ai, "guide request",
                        lambda value: _normalize_study_guide(value, cues),
                        schema_hint="source-only repair",
                        repair_context={"stage": "study_guide_generation"},
                        guide_source_cues=context,
                    )
                self.assertEqual(1, len(ai.prompts))

    def test_pipeline_rejected_source_repair_falls_back_with_path_only_diagnostics(self):
        first = self._source_repair_guide()
        first["questions"][0]["source"] = "课堂明确讲述"
        secret_prompt = first["questions"][0]["prompt"]
        secret_answer = first["questions"][0]["answer"]
        rewritten = json.loads(json.dumps(first, ensure_ascii=False))
        rewritten["questions"][0].update(prompt="不要改动术语、数字或公式", answer="改写答案以通过校验。")

        class GuideRepairPipelineAI(FakeAI):
            def __init__(inner_self, repaired):
                super().__init__()
                inner_self.repaired = repaired
                inner_self.guide_prompts = []

            def chat_completion(inner_self, messages, **kwargs):
                prompt = messages[-1]["content"]
                if "study_guide_generation" in prompt:
                    inner_self.calls.append(messages)
                    inner_self.guide_prompts.append(prompt)
                    return {"content": json.dumps({"study_guide": inner_self.repaired}, ensure_ascii=False), "response_metadata": {"finish_reason": "stop"}}
                if "阶段1" in prompt:
                    inner_self.calls.append(messages)
                    inner_self.guide_prompts.append(prompt)
                    return {"content": json.dumps({"study_guide": first}, ensure_ascii=False), "response_metadata": {"finish_reason": "stop"}}
                return super(GuideRepairPipelineAI, inner_self).chat_completion(messages, **kwargs)

        ai = GuideRepairPipelineAI(rewritten)
        result = TranscriptPipeline().run(VTT, ai)
        self.assertEqual(2, len(ai.guide_prompts))
        repair_prompt = ai.guide_prompts[1]
        self.assertIn('"path":"questions[0].source","expected":"classroom question must occur in source cues"', repair_prompt)
        self.assertNotIn('"actual"', repair_prompt)
        self.assertIn("阶段1讲义生成未通过验证，已使用确定性结构化讲义。", result.partial_warnings)
        diagnostics = result.summary["review"]["guide_generation_diagnostics"]
        self.assertEqual("questions[0].prompt", diagnostics["validator_path"])
        self.assertEqual("schema", diagnostics["category"])
        self.assertEqual("source-only repair must preserve every non-source field", diagnostics["validator_expected"])
        encoded = json.dumps(diagnostics, ensure_ascii=False)
        for text in (secret_prompt, secret_answer, "不要改动术语、数字或公式", "改写答案以通过校验。"):
            self.assertNotIn(text, encoded)
        prompts = [item["prompt"] for item in result.summary["study_guide"]["questions"]]
        self.assertNotIn("不要改动术语、数字或公式", prompts)

        accepted_repair = json.loads(json.dumps(first, ensure_ascii=False))
        accepted_repair["questions"][0]["source"] = "根据课堂内容归纳"
        accepted = TranscriptPipeline().run(VTT, GuideRepairPipelineAI(accepted_repair))
        self.assertNotIn("guide_generation_diagnostics", accepted.summary["review"])
        self.assertNotIn("阶段1讲义生成未通过验证，已使用确定性结构化讲义。", accepted.partial_warnings)

    def test_cancel_during_schema_validation_stops_followup_repair_request(self):
        cancelled = [False]
        ai = FakeAI([dict(content='{"unrecognized":true}')])

        def invalid(value):
            cancelled[0] = True
            raise ValueError("invalid schema")

        with self.assertRaises(TranscriptPipelineCancelled):
            _json_content(ai, "测试请求", invalid, schema_hint="required field", should_cancel=lambda: cancelled[0])
        self.assertEqual(1, len(ai.calls))

    def test_classroom_question_claim_is_grounded_in_any_course_not_fixed_fast_food_case(self):
        guide = study_guide_payload()
        guide["questions"][0].update(prompt="每天多久吃一次饭？", source="课堂明确讲述")
        cues = normalize_cues(parse_vtt(VTT)) + [
            Cue(5000, 6000, "老师提问：“每天多久吃一次饭？”请大家讨论。", "cue-question")
        ]
        normalized = _normalize_study_guide(guide, cues)
        self.assertEqual("课堂明确讲述", normalized["questions"][0]["source"])

        # Keep exact classroom questions valid when quoted inside a longer cue,
        # while rejecting a short suffix that only happens to end in “？”.
        guide["questions"][0]["prompt"] = "饭？"
        with self.assertRaises(ValueError):
            _normalize_study_guide(guide, cues)
        guide["questions"][0]["prompt"] = "类？"
        with self.assertRaises(ValueError):
            _normalize_study_guide(
                guide,
                cues + [Cue(7000, 8000, "接下来讨论分类方法。", "cue-fragment")],
            )
        guide["questions"][0]["prompt"] = "导数的定义是什么？"
        with self.assertRaises(ValueError):
            _normalize_study_guide(guide, cues)
    def test_two_stage_audit_sees_draft_and_subtitle_and_records_every_chunk(self):
        long_vtt = "WEBVTT\n\n" + "\n\n".join(
            f"00:00:{index * 2:02d}.000 --> 00:00:{index * 2 + 1:02d}.000\n段{index}" + ("字" * 2800) + ("不要改动" if index == 2 else "")
            for index in range(4)
        )
        expected_chunks = chunk_cues(normalize_cues(parse_vtt(long_vtt)))
        self.assertGreater(len(expected_chunks), 1)
        ai = FakeAI()
        result = TranscriptPipeline().run(long_vtt, ai)
        audit_prompts = [call[-1]["content"] for call in ai.calls if "阶段2" in call[-1]["content"]]
        self.assertEqual(len(expected_chunks), len(audit_prompts))
        self.assertTrue(all("当前相关主题" in prompt and "本段字幕" in prompt for prompt in audit_prompts))
        self.assertTrue(all("available_evidence_cues" in prompt for prompt in audit_prompts))
        self.assertTrue(all('"cue_id"' in prompt and '"text"' in prompt for prompt in audit_prompts))
        self.assertTrue(all('"quote_mode":"source_cue"' in prompt and "不要复制、改写或输出quote/start_ms/end_ms" in prompt for prompt in audit_prompts))
        self.assertTrue(all("程序将从该cue自动填入完整原文和时间" in prompt for prompt in audit_prompts))
        self.assertTrue(all("程序会引用该cue完整原文，长度受现有消息安全上限约束" in prompt for prompt in audit_prompts))
        ledger = result.summary["review"]["coverage_ledger"]
        self.assertEqual(list(range(len(ledger))), [row["chunk_index"] for row in ledger])
        self.assertTrue(all(row["status"] == "audited" and row["coverage_evidence"] for row in ledger))

    def test_audit_failure_keeps_stage_one_guide_with_warning(self):
        class AuditFailureAI(FakeAI):
            def chat_completion(inner_self, messages, **kwargs):
                if "阶段2" in messages[-1]["content"]:
                    inner_self.calls.append(messages)
                    raise TimeoutError("audit timeout")
                return super(AuditFailureAI, inner_self).chat_completion(messages, **kwargs)

        result = TranscriptPipeline().run(VTT, AuditFailureAI())
        self.assertTrue(result.summary["study_guide"]["themes"])
        self.assertEqual("completed_with_warnings", result.summary["review"]["status"])
        self.assertTrue(all(row["status"] == "not_audited" for row in result.summary["review"]["coverage_ledger"]))
        diagnostic = result.summary["review"]["coverage_ledger"][0]["diagnostics"]
        self.assertEqual("study_guide_audit", diagnostic["validator"])
        self.assertEqual("timeout", diagnostic["category"])
        self.assertGreater(diagnostic["request_chars"], 0)
        self.assertIsNotNone(diagnostic["latency_ms"])

    def test_source_evidence_examples_placeholders_and_fast_food_case(self):
        cues = [Cue(1_414_000, 1_416_000, "为什么现在不吃某快餐", "cue-fast-food")]
        nullable_guide = study_guide_payload()
        nullable_guide["themes"][0]["source"] = "课堂明确讲述"
        nullable_guide["themes"][0]["evidence"] = [dict(cue_id="cue-fast-food", quote="为什么现在不吃某快餐")]
        nullable_guide["themes"][0]["classroom_example"] = None
        nullable_result = _normalize_study_guide(nullable_guide, cues)
        self.assertIsNone(nullable_result["themes"][0]["classroom_example"])

        guide = study_guide_payload()
        theme = guide["themes"][0]
        theme["evidence"] = [dict(cue_id="cue-fast-food", quote="为什么现在不吃某快餐")]
        theme["classroom_example"] = dict(original_question="为什么现在不吃某快餐", problem="目的不清会得到含混回答", improvement="先说明要了解当下不购买的原因")
        guide["questions"][0] = dict(type="application", prompt="多久吃一次该快餐？", answer="检查频率选项边界。", source="自编练习或例子")
        guide["checklist"].extend(["要点", "兼容概念说明"])
        guide["themes"].append(dict(theme))
        normalized = _normalize_study_guide(guide, cues)
        self.assertEqual(1, len(normalized["themes"]))
        self.assertTrue(normalized["themes"][0]["why_and_consequences"])
        self.assertGreaterEqual(len(normalized["themes"][0]["steps"]), 3)
        self.assertEqual("为什么现在不吃某快餐", normalized["themes"][0]["classroom_example"]["original_question"])
        self.assertEqual("自编练习或例子", normalized["questions"][0]["source"])
        guide["questions"][0]["source"] = "课堂明确讲述"
        with self.assertRaises(ValueError):
            _normalize_study_guide(guide, cues)
        guide["questions"][0]["source"] = "自编练习或例子"
        self.assertNotIn("要点", normalized["checklist"])
        theme["evidence"] = []
        with self.assertRaises(ValueError):
            _normalize_study_guide(guide, cues)

    def test_local_audit_cannot_drop_prior_theme_and_requires_current_chunk_coverage(self):
        cues = [Cue(0, 1000, "第一主题证据", "cue-a"), Cue(1000, 2000, "第二主题证据", "cue-b")]
        chunks = [TranscriptChunk(index, (cue,)) for index, cue in enumerate(cues)]

        def theme(title, cue):
            return dict(
                topic_id="topic-" + cue.cue_id,
                title=title, conclusion=cue.text, principle_and_context="用于对应字幕片段。",
                why_and_consequences="遗漏会失去前序课程信息。", steps=["定位", "核对", "保留"],
                classroom_example=None, boundaries="仅限引文。", connections="关系待核实。",
                source="课堂明确讲述",
                evidence=[dict(cue_id=cue.cue_id, quote=cue.text)],
            )

        guide = dict(
            course_map=["第一", "第二", "第三待核实", "第四待核实"],
            themes=[theme("第一主题", cues[0]), theme("第二主题", cues[1])],
            checklist=["逐段核对"],
            questions=[dict(type="recall", prompt=f"题目{i}", answer=f"答案{i}", source="自编练习或例子") for i in range(5)],
            extensions=["关系一？", "关系二？"],
        )

        class LocalAuditAI:
            def __init__(inner_self, wrong_second=False):
                inner_self.index = 0
                inner_self.wrong_second = wrong_second

            def chat_completion(inner_self, messages, **kwargs):
                index = inner_self.index
                inner_self.index += 1
                cue = cues[index]
                evidence_cue = cues[0] if inner_self.wrong_second and index == 1 else cue
                revised = dict(guide)
                revised["themes"] = [theme("第二主题", cues[1])] if index == 1 else list(guide["themes"])
                payload = dict(
                    chunk_index=index,
                    time_range=dict(start_ms=cue.start_ms, end_ms=cue.end_ms),
                    reviewed_cue_ids=[cue.cue_id],
                    coverage_confirmation=dict(
                        confirmed=True,
                        evidence=[dict(cue_id=evidence_cue.cue_id, quote=evidence_cue.text)],
                    ),
                    issues=[],
                    revised_study_guide=revised,
                )
                return dict(content=json.dumps(payload, ensure_ascii=False))

        audited, review = _audit_guide(LocalAuditAI(), guide, chunks, cues)
        self.assertEqual(["第一主题", "第二主题"], [item["title"] for item in audited["themes"]])
        self.assertTrue(all(row["status"] == "audited" and row["coverage_evidence"] for row in review["coverage_ledger"]))

        _audited, failed_review = _audit_guide(LocalAuditAI(wrong_second=True), guide, chunks, cues)
        self.assertEqual("not_audited", failed_review["coverage_ledger"][1]["status"])
        self.assertEqual([], failed_review["coverage_ledger"][1]["coverage_evidence"])

        class PartialCoverageAI:
            def chat_completion(inner_self, messages, **kwargs):
                payload = dict(
                    chunk_index=0,
                    time_range=dict(start_ms=0, end_ms=2000),
                    reviewed_cue_ids=["cue-a"],
                    coverage_confirmation=dict(
                        confirmed=True,
                        evidence=[dict(cue_id="cue-a", quote="第一主题证据")],
                    ),
                    issues=[],
                    revised_study_guide=guide,
                )
                return dict(content=json.dumps(payload, ensure_ascii=False))

        _partial_guide, partial_review = _audit_guide(
            PartialCoverageAI(), guide, [TranscriptChunk(0, tuple(cues))], cues
        )
        partial_row = partial_review["coverage_ledger"][0]
        self.assertEqual("partial", partial_row["status"])
        self.assertEqual(["cue-b"], partial_row["missing_cue_ids"])

        class RenameTopicAI:
            def chat_completion(inner_self, messages, **kwargs):
                renamed = dict(guide)
                renamed_theme = dict(guide["themes"][0], title="第一主题（修订）")
                renamed["themes"] = [renamed_theme, guide["themes"][1]]
                payload = dict(
                    chunk_index=0,
                    time_range=dict(start_ms=0, end_ms=1000),
                    reviewed_cue_ids=["cue-a"],
                    coverage_confirmation=dict(
                        confirmed=True,
                        evidence=[dict(cue_id="cue-a", quote="第一主题证据")],
                    ),
                    issues=[dict(
                        type="标题修正",
                        target_topic_id="topic-cue-a",
                        problem="第一主题标题需要修正",
                        evidence=[dict(cue_id="cue-a", quote="第一主题证据")],
                        fix="改为第一主题（修订）",
                    )],
                    revised_study_guide=renamed,
                )
                return dict(content=json.dumps(payload, ensure_ascii=False))

        renamed, renamed_review = _audit_guide(
            RenameTopicAI(), guide, [TranscriptChunk(0, (cues[0],))], cues
        )
        self.assertEqual("completed", renamed_review["status"])
        self.assertEqual(
            ["第一主题（修订）", "第二主题"],
            [item["title"] for item in renamed["themes"]],
        )

        class LocalPatchAI:
            def chat_completion(inner_self, messages, **kwargs):
                payload = dict(
                    chunk_index=0,
                    time_range=dict(start_ms=0, end_ms=1000),
                    reviewed_cue_ids=["cue-a"],
                    coverage_confirmation=dict(
                        confirmed=True,
                        evidence=[dict(cue_id="cue-a", quote_mode="source_cue")],
                    ),
                    issues=[dict(
                        type="标题修正", target_topic_id="topic-cue-a",
                        problem="第一主题标题需要修正",
                        evidence=[dict(cue_id="cue-a", quote_mode="source_cue")],
                        fix="改为第一主题（补丁）",
                    )],
                    theme_patches=[dict(
                        target_topic_id="topic-cue-a",
                        changes=dict(title="第一主题（补丁）"),
                    )],
                )
                return dict(content=json.dumps(payload, ensure_ascii=False))

        patched, patch_review = _audit_guide(LocalPatchAI(), guide, [TranscriptChunk(0, (cues[0],))], cues)
        self.assertEqual("第一主题（补丁）", patched["themes"][0]["title"])
        self.assertEqual("第二主题", patched["themes"][1]["title"])
        self.assertEqual("audited", patch_review["coverage_ledger"][0]["status"])
        self.assertEqual(cues[0].text, patch_review["coverage_ledger"][0]["coverage_evidence"][0]["quote"])
        self.assertEqual(cues[0].start_ms, patch_review["coverage_ledger"][0]["coverage_evidence"][0]["start_ms"])
        self.assertEqual(AUDIT_WIRE_VERSION, patch_review["coverage_ledger"][0]["diagnostics"]["audit_wire_version"])

        class AliasPatchAI:
            def __init__(inner_self, target, reviewed=None, evidence_cue=None):
                inner_self.target = target
                inner_self.reviewed = reviewed or cues
                inner_self.evidence_cue = evidence_cue or cues[1]

            def chat_completion(inner_self, messages, **kwargs):
                evidence = dict(cue_id=inner_self.evidence_cue.cue_id, quote=inner_self.evidence_cue.text)
                payload = dict(
                    reviewed_cue_ids=[cue.cue_id for cue in inner_self.reviewed],
                    coverage_confirmation=dict(confirmed=True, evidence=[evidence]),
                    issues=[dict(type="标题修正", target_topic_id=inner_self.target, problem="第二主题需精确修正", evidence=[evidence], fix="采用新标题")],
                    theme_patches=[dict(target_topic_id=inner_self.target, changes=dict(title="第二主题（别名修正）"))],
                )
                return {"content": json.dumps(payload, ensure_ascii=False)}

        alias_patched, alias_review = _audit_guide(
            AliasPatchAI("T2"), guide, [TranscriptChunk(0, tuple(cues))], cues
        )
        self.assertEqual("audited", alias_review["coverage_ledger"][0]["status"])
        self.assertEqual("第二主题（别名修正）", alias_patched["themes"][1]["title"])

        # A stale alias from another chunk and a current-but-nonlocal canonical ID
        # are not rebound by topic title or by full-guide membership.
        for foreign_target, target_chunk in (("T2", TranscriptChunk(0, (cues[0],))), ("topic-cue-b", TranscriptChunk(0, (cues[0],)))):
            with self.subTest(foreign_target=foreign_target):
                _unchanged, foreign_review = _audit_guide(
                    AliasPatchAI(foreign_target, reviewed=[cues[0]], evidence_cue=cues[0]),
                    guide, [target_chunk], cues,
                )
                self.assertEqual("not_audited", foreign_review["coverage_ledger"][0]["status"])
                self.assertEqual("audit.issues[0].target_topic_id", foreign_review["coverage_ledger"][0]["diagnostics"]["validator_path"])
        for invalid_target in ("T9", ""):
            with self.subTest(invalid_target=invalid_target):
                _unchanged, invalid_target_review = _audit_guide(
                    AliasPatchAI(invalid_target, reviewed=[cues[0]], evidence_cue=cues[0]),
                    guide, [TranscriptChunk(0, (cues[0],))], cues,
                )
                self.assertEqual("not_audited", invalid_target_review["coverage_ledger"][0]["status"])
                self.assertEqual("audit.issues[0].target_topic_id", invalid_target_review["coverage_ledger"][0]["diagnostics"]["validator_path"])

        class MalformedIssueAI:
            def __init__(inner_self, issues):
                inner_self.issues = issues

            def chat_completion(inner_self, messages, **kwargs):
                return dict(content=json.dumps(dict(
                    chunk_index=0,
                    time_range=dict(start_ms=0, end_ms=1000),
                    reviewed_cue_ids=["cue-a"],
                    coverage_confirmation=dict(
                        confirmed=True,
                        evidence=[dict(cue_id="cue-a", quote="第一主题证据")],
                    ),
                    issues=inner_self.issues,
                    theme_patches=[],
                ), ensure_ascii=False))

        malformed_issues = [
            {"issues": {"type": "问题", "problem": "内容不完整", "evidence": [], "fix": "补充"}},
            {"issues": [dict(type="问题", problem="内容不完整", evidence=[], fix="补充")]},
            {"issues": [None]},
        ]
        for invalid in malformed_issues:
            with self.subTest(invalid=invalid):
                _unchanged_guide, invalid_review = _audit_guide(
                    MalformedIssueAI(invalid["issues"]), guide,
                    [TranscriptChunk(0, (cues[0],))], cues,
                )
                ledger_row = invalid_review["coverage_ledger"][0]
                self.assertEqual("completed_with_warnings", invalid_review["status"])
                self.assertEqual("not_audited", ledger_row["status"])
                self.assertEqual("study_guide_audit", ledger_row["diagnostics"]["validator"])
                self.assertTrue(ledger_row["diagnostics"].get("validator_path", "").startswith("audit.issues"))

    def test_fallback_is_structured_questions_are_answered_and_markdown_ordered(self):
        cues = normalize_cues(parse_vtt(VTT))
        summary = deterministic_summary([validate_map(map_payload(), cues)])
        summary["study_guide"] = _fallback_study_guide(summary, cues)
        summary["review"] = dict(status="completed", issues=[], coverage_ledger=[])
        guide = summary["study_guide"]
        self.assertTrue(guide["incomplete"])
        self.assertLess(len(guide["questions"]), 5)
        prompts = [item["prompt"] for item in guide["questions"]]
        self.assertEqual(len(prompts), len(set(prompts)))
        encoded = json.dumps(guide, ensure_ascii=False)
        for unrelated in ("问卷", "现有量表", "漏斗式", "调研伦理", "管理会计"):
            self.assertNotIn(unrelated, encoded)
        self.assertTrue(all(item["answer"] for item in guide["questions"]))
        markdown = render_summary(summary)
        headings = ["## 课程地图", "## 分主题讲义", "## 一页检查清单", "## 题目", "## 延伸问题", "## 参考答案", "## 审校记录"]
        self.assertEqual(sorted(markdown.index(item) for item in headings), [markdown.index(item) for item in headings])
        self.assertLess(markdown.index("## 题目"), markdown.index("## 参考答案"))


if __name__ == "__main__":
    unittest.main()
