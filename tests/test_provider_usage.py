from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import httpx
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.models import Base, ProviderUsage
from sjtu_learning_assistant.provider_usage import ProviderUsageLedger


class ProviderUsageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.ledger = ProviderUsageLedger(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_cache_aliases_unknown_and_partial_do_not_double_add(self):
        self.ledger.record("deepseek-chat", dict(prompt_tokens=100, completion_tokens=10, total_tokens=110, prompt_cache_hit_tokens=60, cached_tokens=60))
        self.ledger.record("qwen", dict(prompt_tokens=0, completion_tokens=0, total_tokens=0))
        self.ledger.record("minimax", {})
        result = self.ledger.summary()["periods"]["all"]
        self.assertEqual(3, result["calls"])
        self.assertEqual(100, result["metrics"]["input"]["tokens"])
        self.assertEqual(110, result["metrics"]["total"]["tokens"])
        self.assertEqual(dict(tokens=60, reported_calls=1, missing_calls=2), result["metrics"]["cache"])

    def test_calendar_range_and_no_500_row_cap(self):
        now = datetime(2026, 11, 21, 9, tzinfo=timezone(timedelta(hours=8)))
        with Session(self.engine) as session, session.begin():
            for i in range(501):
                session.add(ProviderUsage(id=str(i), model="qwen", prompt_tokens=1, created_at=now.astimezone(timezone.utc)))
            session.add(ProviderUsage(id="yesterday", model="qwen", prompt_tokens=10, created_at=(now-timedelta(days=1)).astimezone(timezone.utc)))
            session.add(ProviderUsage(id="old", model="qwen", created_at=(now-timedelta(days=7)).astimezone(timezone.utc)))
        result = self.ledger.summary(now=now)["periods"]
        self.assertEqual(501, result["today"]["calls"])
        self.assertEqual(502, result["week"]["calls"])
        self.assertEqual(503, result["all"]["calls"])

    def test_nonstream_invalid_response_is_counted_and_secret_not_stored(self):
        def handler(request):
            return httpx.Response(200, json={"choices": [], "usage": {"prompt_tokens": 3, "api_key": "secret"}}, request=request)
        client = OpenAIClassificationClient(api_key="fake", transport=httpx.MockTransport(handler), limiter=lambda: None, usage_recorder=self.ledger.record)
        with self.assertRaises(Exception):
            client.chat_completion([dict(role="user", content="private-text")])
        client.close()
        result = self.ledger.summary()["periods"]["all"]
        self.assertEqual(1, result["calls"])
        self.assertEqual(3, result["metrics"]["input"]["tokens"])
        names = {column["name"] for column in inspect(self.engine).get_columns("provider_usage")}
        self.assertTrue(names.isdisjoint({"api_key", "content", "prompt", "base_url"}))

    def test_stream_fallback_counts_each_attempt_once(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            if count == 1:
                return httpx.Response(400, request=request)
            stream = ('data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
                      'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1,"total_tokens":6,"prompt_tokens_details":{"cached_tokens":2}}}\n\n'
                      'data: [DONE]\n\n')
            return httpx.Response(200, text=stream, request=request)
        client = OpenAIClassificationClient(api_key="fake", transport=httpx.MockTransport(handler), limiter=lambda: None, usage_recorder=self.ledger.record)
        client.chat_completion_stream([dict(role="user", content="test")], on_delta=lambda *a: None)
        client.close()
        result = self.ledger.summary()["periods"]["all"]
        self.assertEqual(2, result["calls"])
        self.assertEqual(dict(tokens=2, reported_calls=1, missing_calls=1), result["metrics"]["cache"])

    def test_meter_failure_does_not_retry_or_break_completion(self):
        def fail(*args):
            raise RuntimeError("secret exception")
        client = OpenAIClassificationClient(api_key="fake", usage_recorder=fail, limiter=lambda: None,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, request=r, json={"choices":[{"message":{"content":"ok"}}]})))
        self.assertEqual("ok", client.chat_completion([dict(role="user", content="test")])["content"])
        client.close()

    def test_existing_0021_schema_upgrade_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine("sqlite:///" + str(Path(tmp)/"app.db"))
            bootstrap_sqlite(engine)
            with engine.begin() as conn:
                conn.exec_driver_sql("DROP TABLE provider_usage")
                conn.exec_driver_sql("UPDATE alembic_version SET version_num='0021'")
            self.assertEqual("0022", bootstrap_sqlite(engine))
            self.assertIn("provider_usage", inspect(engine).get_table_names())
            engine.dispose()
