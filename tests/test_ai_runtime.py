from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from sjtu_learning_assistant.ai_runtime import SharedAIRuntime, token_reservation

BODY = {"model": "deepseek-chat", "max_tokens": 256, "messages": [{"role": "user", "content": "合成问题"}]}


class RuntimeTests(unittest.TestCase):
    def test_shared_instances_enforce_rpm_tpm_and_preserve_unknown_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            now = [1000.0]
            def sleep(seconds):
                now[0] += seconds
            options = dict(clock=lambda: now[0], sleeper=sleep, rpm=1)
            first = SharedAIRuntime(Path(temp) / "runtime.sqlite3", "scope", **options)
            second = SharedAIRuntime(first.path, "scope", **options)
            with first.request(BODY, {}):
                pass
            with second.request(BODY, {}):
                pass
            self.assertGreaterEqual(now[0], 1060)
            with closing(sqlite3.connect(first.path)) as db:
                rows = db.execute("SELECT input,output,cache,reserved FROM requests").fetchall()
                self.assertEqual(2, len(rows))
                self.assertTrue(all(r[:3] == (None, None, None) and r[3] > 0 for r in rows))

    def test_inflight_lease_blocks_other_process_and_rate_limit_cools_all(self):
        with tempfile.TemporaryDirectory() as temp:
            now = [1000.0]
            def sleep(seconds):
                now[0] += seconds
            runtime = SharedAIRuntime(Path(temp) / "runtime.sqlite3", "scope", clock=lambda: now[0], sleeper=sleep, max_inflight=1)
            identity = runtime.reserve(BODY)
            other = runtime.reserve(BODY)
            self.assertGreaterEqual(now[0], 1360)
            runtime.finish(other, {"total_tokens": 20}, "http_429", now[0], rate_limited=True)
            start = now[0]
            runtime.reserve(BODY)
            self.assertGreaterEqual(now[0] - start, 60)
            runtime.finish(identity, {}, "interrupted", 1000)

    def test_token_budget_blocks_and_journal_contains_no_content(self):
        with tempfile.TemporaryDirectory() as temp:
            now = [1000.0]
            runtime = SharedAIRuntime(Path(temp) / "runtime.sqlite3", "scope", clock=lambda: now[0], sleeper=lambda n: now.__setitem__(0, now[0] + n), tpm=token_reservation(BODY))
            with runtime.request(BODY, {}):
                pass
            with runtime.request(BODY, {}):
                pass
            self.assertGreaterEqual(now[0], 1060)
            with closing(sqlite3.connect(runtime.path)) as db:
                text = str(db.execute("SELECT * FROM requests").fetchall())
                self.assertNotIn("合成问题", text)
            with self.assertRaises(RuntimeError):
                runtime.reserve({**BODY, "max_tokens": 999999})
