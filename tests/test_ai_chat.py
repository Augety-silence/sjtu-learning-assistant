from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine

from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.local_settings import SettingsStore


class FakeAIClient:
    created_models: list[str] = []

    def __init__(self, *, model: str, **_kwargs):
        self.model = model
        self.created_models.append(model)

    def chat_completion(self, messages, **_kwargs):
        return {
            "content": "## 建议\n\n- 先完成最近截止的任务",
            "reasoning_content": "按截止时间排序。" if self.model == "deepseek-reasoner" else None,
        }

    def close(self):
        return None


class AIChatHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.engine = create_engine(f"sqlite:///{root / 'app.db'}")
        bootstrap_sqlite(self.engine)
        settings = SettingsStore(root / "settings.json")
        settings.update({"ai_key_saved": True})
        FakeAIClient.created_models.clear()
        self.service = DashboardService(
            self.engine,
            archive_root=root / "archive",
            settings_store=settings,
            ai_key_loader=lambda: "test-key",
            ai_client_factory=FakeAIClient,
        )

    def tearDown(self) -> None:
        self.service.close()
        self.engine.dispose()
        self.temporary.cleanup()

    def test_history_persists_and_deep_auto_routes_to_reasoner(self) -> None:
        chat = self.service.ai_chat_new("auto", "standard")
        result = self.service.ai_chat_send(
            chat["id"], "请给我安排复习计划", "auto", "deep"
        )
        self.assertEqual("deepseek-reasoner", result["assistant_message"]["model"])
        self.assertEqual("按截止时间排序。", result["assistant_message"]["reasoning_content"])
        self.assertEqual(["deepseek-reasoner"], FakeAIClient.created_models)

        restored = self.service.ai_chat_session(chat["id"])
        self.assertEqual(2, len(restored["messages"]))
        self.assertEqual("请给我安排复习计划", restored["messages"][0]["content"])
        self.assertIn("建议", restored["messages"][1]["content"])
        self.assertEqual(chat["id"], self.service.ai_chat_sessions()["items"][0]["id"])

    def test_delete_is_idempotent(self) -> None:
        chat = self.service.ai_chat_new("deepseek-chat", "quick")
        self.assertTrue(self.service.ai_chat_delete(chat["id"])["deleted"])
        self.assertFalse(self.service.ai_chat_delete(chat["id"])["deleted"])


if __name__ == "__main__":
    unittest.main()
