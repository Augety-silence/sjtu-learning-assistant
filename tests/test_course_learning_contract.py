from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from sjtu_learning_assistant.ai_classifier import AIClassificationError, OpenAIClassificationClient
from sjtu_learning_assistant.course_learning_orchestrator import (
    PROMPT_VERSION, CourseLearningOrchestrator, OpenAIClassificationSemanticAdapter,
    _data_messages, _strict_schema, _strict_terms,
)
from sjtu_learning_assistant.course_learning_prompts import decode_semantic_json, semantic_diagnostics
from sjtu_learning_assistant.course_learning_schemas import CorrectionResult, CriticResult, MemoryDelta, SchemaValidationError
from sjtu_learning_assistant.course_memory import CourseMemoryStore


class CourseLearningContractTests(unittest.TestCase):
    def client(self, handler):
        return OpenAIClassificationClient(api_key="fake-key", model="qwen",
                                          transport=httpx.MockTransport(handler), limiter=lambda: None)

    def test_production_adapter_explicit_contract_reaches_all_roles_and_passes_gates(self):
        seen = []

        def handler(request):
            body = json.loads(request.content)
            seen.append(body)
            prompt = body["messages"][1]["content"]
            schema_start = prompt.index("JSON Schema：") + len("JSON Schema：")
            schema_end = prompt.index("\n合法保守模板：", schema_start)
            contract = json.loads(prompt[schema_start:schema_end])
            self.assertEqual(set(contract["properties"]), set(contract["required"]))
            self.assertFalse(contract["additionalProperties"])
            start = prompt.index("\n<UNTRUSTED_DATA-")
            data = json.loads(prompt[prompt.index("\n", start + 1) + 1:prompt.rindex("\n</UNTRUSTED_DATA-")])
            if "terms" in contract["properties"]:
                output = {"terms": []}
            elif "corrected_text" in contract["properties"]:
                output = dict(chunk_id=data["chunk_id"], corrected_text=data["current_text"], changes=[], uncertain=[], confidence=0.9)
            elif "decision" in contract["properties"]:
                output = dict(decision="PASS", confidence=0.9, issues=[], revision_instructions=[])
            else:
                output = dict(add_terms=[], update_terms=[], new_error_patterns=[], new_concepts=[], new_relationships=[])
            self.assertEqual(set(contract["required"]), set(output))
            return httpx.Response(200, json=dict(choices=[dict(message=dict(content=json.dumps(output, ensure_ascii=False)), finish_reason="stop")], usage=dict(prompt_tokens=3, completion_tokens=2, total_tokens=5)), request=request)

        client = self.client(handler)
        try:
            with tempfile.TemporaryDirectory() as directory:
                orchestrator = CourseLearningOrchestrator(OpenAIClassificationSemanticAdapter(client, json_mode=True), persist_raw=False)
                result = orchestrator.orchestrate(course_id="123", video_id="456", cues=[dict(cue_id="cue-1", start_ms=0, end_ms=1000, text="合成资料保持原文。")], memory_repo=CourseMemoryStore(Path(directory), "123"))
                self.assertEqual("completed", result.status)
                self.assertTrue(result.quality.schema_pass)
                self.assertEqual(1.0, result.quality.critic_pass_rate)
                self.assertIn("diagnostics", json.loads(result.to_json())["events"][3])
                self.assertEqual(4, len(seen))
                for body in seen:
                    self.assertEqual({"type": "json_object"}, body["response_format"])
                    self.assertEqual(2, len(body["messages"]))
                    self.assertNotIn("AI 助理", body["messages"][0]["content"])
                for event in result.events:
                    if event["agent"] in {"terminology", "correction", "critic", "memory"}:
                        self.assertEqual("stop", event["diagnostics"]["finish_reason"])
                        self.assertEqual(5, event["token_usage"]["total_tokens"])
        finally:
            client.close()

    def test_decoder_rejects_duplicate_nan_fences_and_missing_unknown_fields(self):
        for content in ('{"terms":[],"terms":[]}', '{"terms":[],"x":NaN}', '```json\n{"terms":[]}\n```'):
            with self.subTest(content=content), self.assertRaises(SchemaValidationError):
                _strict_terms(content)
        with self.assertRaises(SchemaValidationError):
            _strict_terms('{"terms":[],"unknown":1}')
        for schema, content in (
            (CorrectionResult, '{"chunk_id":"c","corrected_text":"x","changes":[],"uncertain":[]}'),
            (CriticResult, '{"decision":"PASS","confidence":0.9,"issues":["bad"],"revision_instructions":[]}'),
            (MemoryDelta, '{"add_terms":[]}'),
        ):
            with self.subTest(schema=schema), self.assertRaises(SchemaValidationError):
                _strict_schema(content, schema)
        self.assertEqual({"terms": []}, decode_semantic_json('{"terms":[]}'))

    def test_failure_diagnostics_preserve_structure_usage_and_do_not_echo_response(self):
        marker = "private-synthetic-marker"

        def handler(request):
            return httpx.Response(200, json=dict(choices=[dict(message=dict(content='{"terms":[],"'+marker+'":1}'), finish_reason="length")], usage=dict(prompt_tokens=4, completion_tokens=2)), request=request)

        client = self.client(handler)
        try:
            orchestrator = CourseLearningOrchestrator(OpenAIClassificationSemanticAdapter(client), persist_raw=False)
            events = []
            with self.assertRaises(SchemaValidationError):
                orchestrator._call("terminology", _data_messages("Terminology", "synthetic", {}), _strict_terms, events)
            event = events[0]
            self.assertEqual("truncated", event["status"])
            self.assertEqual("$", event["diagnostics"]["schema_path"])
            self.assertGreater(event["diagnostics"]["content_length"], 0)
            self.assertEqual(4, event["token_usage"]["prompt_tokens"])
            self.assertNotIn(marker, json.dumps(events))
        finally:
            client.close()
        auth = semantic_diagnostics(None, AIClassificationError(marker, category="authentication", status_code=401))
        self.assertEqual("authentication", auth["category"])
        self.assertEqual(401, auth["status_code"])
        self.assertNotIn(marker, json.dumps(auth))

    def test_json_mode_is_optional_and_unsupported_format_is_rejected(self):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"terms":[]}'}}]}, request=request)
        client = self.client(handler)
        try:
            OpenAIClassificationSemanticAdapter(client).terminology(_data_messages("Terminology", "synthetic", {}), prompt_version=PROMPT_VERSION)
            self.assertNotIn("response_format", seen[0])
            with self.assertRaises(AIClassificationError):
                client.chat_completion([dict(role="user", content="test")], response_format={"type": "json_schema"})
            self.assertEqual(1, len(seen))
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
