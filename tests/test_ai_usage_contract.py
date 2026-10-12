from __future__ import annotations

import unittest

import httpx

from sjtu_learning_assistant.ai_classifier import (
    OpenAIClassificationClient, extract_token_usage, normalize_token_usage,
)
from sjtu_learning_assistant.course_learning_orchestrator import (
    CourseLearningOrchestrator, OpenAIClassificationSemanticAdapter, _agent_output,
)


class AIUsageContractTests(unittest.TestCase):
    def test_provider_client_adapter_and_pipeline_event_preserve_usage(self):
        reported = dict(prompt_tokens=91, completion_tokens=13, total_tokens=104,
                        prompt_cache_hit_tokens=20, prompt_cache_miss_tokens=71,
                        prompt_tokens_details=dict(cached_tokens=20),
                        completion_tokens_details=dict(reasoning_tokens=5))
        expected = dict(prompt_tokens=91, completion_tokens=13, total_tokens=104,
                        prompt_cache_hit_tokens=20, prompt_cache_miss_tokens=71,
                        cached_tokens=20, reasoning_tokens=5)
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"terms": []}'}, "finish_reason": "stop"}], "usage": reported}, request=request)

        with_client = OpenAIClassificationClient(api_key="fake-key", model="qwen",
                                                 transport=httpx.MockTransport(handler), limiter=lambda: None)
        try:
            adapter = OpenAIClassificationSemanticAdapter(with_client)
            response = adapter.terminology([dict(role="user", content="test")], prompt_version="test")
            self.assertEqual(expected, response["usage"])
            self.assertEqual(expected, response["response_metadata"]["usage"])
            self.assertEqual(expected, _agent_output(response).usage)
            events = []
            orchestrator = CourseLearningOrchestrator(adapter, persist_raw=False)
            result = orchestrator._call("terminology", [dict(role="user", content="test")], lambda value: value, events)
            self.assertEqual('{"terms": []}', result)
            self.assertEqual(expected, events[0]["token_usage"])
            self.assertEqual(2, len(requests))
        finally:
            with_client.close()

    def test_legacy_flat_metadata_and_nested_metadata_are_compatible(self):
        legacy = dict(content="ok", response_metadata=dict(prompt_tokens=3, completion_tokens=4))
        self.assertEqual(dict(prompt_tokens=3, completion_tokens=4), _agent_output(legacy).usage)
        mixed = dict(response_metadata=dict(prompt_tokens=3, usage=dict(completion_tokens=4)), usage=dict(prompt_tokens=5))
        self.assertEqual(dict(prompt_tokens=5, completion_tokens=4), extract_token_usage(mixed))

    def test_missing_or_invalid_counts_are_unknown_and_real_zero_is_retained(self):
        self.assertEqual({}, normalize_token_usage(None))
        self.assertEqual({}, _agent_output(dict(content="ok")).usage)
        self.assertEqual({}, normalize_token_usage(dict(prompt_tokens=True, completion_tokens=-1, total_tokens="5", api_key="secret")))
        self.assertEqual(dict(prompt_tokens=0), normalize_token_usage(dict(prompt_tokens=0)))
        self.assertNotIn("total_tokens", normalize_token_usage(dict(prompt_tokens=1, completion_tokens=2)))

    def test_compatible_input_output_and_cache_detail_aliases(self):
        self.assertEqual(dict(prompt_tokens=6, completion_tokens=2, cached_tokens=1, reasoning_tokens=0), normalize_token_usage(dict(input_tokens=6, output_tokens=2, input_tokens_details=dict(cached_tokens=1), output_tokens_details=dict(reasoning_tokens=0))))


if __name__ == "__main__":
    unittest.main()
