from __future__ import annotations

import unittest
from types import SimpleNamespace

from sjtu_learning_assistant.ai_capabilities import (
    OUTPUT_MODES, ai_capabilities, ai_configuration_preflight, ai_task_catalog,
)


class AICapabilityTests(unittest.TestCase):
    def test_fourteen_pages_separate_plans_from_implemented_features(self):
        catalog = ai_task_catalog()
        tasks = catalog["tasks"]
        self.assertEqual(14, len({task["page_id"] for task in tasks}))
        self.assertEqual({"planned", "partial", "implemented"}, {task["implementation_status"] for task in tasks})
        for task in tasks:
            self.assertIn(task["output_mode"], OUTPUT_MODES)
            self.assertTrue(task["input_sources"])
            self.assertTrue(task["input_requirements"])
            self.assertEqual("readonly_suggestions", task["decision_boundary"])
            self.assertEqual("enforced_for_page_ai", task["budget_status"])
            self.assertTrue(task["page_ai_available"])
            self.assertEqual("persistent_ledger", task["task_runtime"])
            self.assertGreater(task["max_model_calls"], 0)
            self.assertLessEqual(task["max_output_tokens"], 4096)
            if task["implementation_status"] == "planned":
                self.assertEqual([], task["backend_actions"])
        chat = ai_task_catalog("ai-chat")["tasks"][0]
        self.assertNotIn("stream_not_implemented", chat["limitations"])
        self.assertEqual("text_stream", chat["output_mode"])
        self.assertIn("ai_chat_stream_start", chat["backend_actions"])

    def test_configuration_never_claims_live_connection_or_key_access(self):
        settings = SimpleNamespace(ai_enabled=True, ai_key_saved=True, ai_model="qwen")
        status = ai_configuration_preflight(settings)
        self.assertTrue(status["configured"])
        self.assertTrue(status["ready_for_request"])
        self.assertFalse(status["connection_verified"])
        self.assertFalse(status["key_access_verified"])
        self.assertFalse(status["network_probe_performed"])
        self.assertEqual("unknown_until_provider_response", status["usage_status"])
        self.assertEqual([], status["reasons"])
        self.assertTrue(status["streaming_implemented"])

    def test_preflight_reads_only_declared_nonsecret_fields(self):
        class Settings:
            ai_enabled = True
            ai_key_saved = True
            ai_model = "deepseek-chat"

            @property
            def api_key(self):
                raise AssertionError("Must never read a key")

            @property
            def ai_base_url(self):
                raise AssertionError("Must never initiate a connection")

        result = ai_capabilities(Settings(), page_id="videos")
        self.assertEqual(1, len(result["tasks"]))
        self.assertTrue(result["preflight"]["configured"])

    def test_missing_disabled_and_unsupported_conditions_are_actionable(self):
        settings = dict(ai_enabled=False, ai_key_saved=False, ai_model="unknown")
        status = ai_configuration_preflight(settings, protocol_client_supported=False, connection_verified=True)
        self.assertFalse(status["configured"])
        self.assertFalse(status["ready_for_request"])
        self.assertFalse(status["connection_verified"])
        self.assertEqual(["ai_disabled", "key_not_saved", "model_not_supported", "protocol_client_not_supported"], status["reasons"])
        configured_off = ai_configuration_preflight(dict(ai_enabled=False, ai_key_saved=True, ai_model="deepseek-reasoner"))
        self.assertTrue(configured_off["configured"])
        self.assertFalse(configured_off["ready_for_request"])
        # Strings such as 'false'/'true' are not configuration evidence.
        malformed = ai_configuration_preflight(dict(ai_enabled="true", ai_key_saved="true", ai_model="qwen"))
        self.assertFalse(malformed["configured"])

    def test_unknown_pages_and_nonboolean_evidence_flags_are_rejected(self):
        with self.assertRaises(ValueError):
            ai_task_catalog("similar-page")
        with self.assertRaises(ValueError):
            ai_configuration_preflight({}, connection_verified="true")


if __name__ == "__main__":
    unittest.main()
