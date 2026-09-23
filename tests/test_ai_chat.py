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
    requested_messages: list[list[dict]] = []

    def __init__(self, *, model: str, **_kwargs):
        self.model = model
        self.created_models.append(model)

    def chat_completion(self, messages, **_kwargs):
        self.requested_messages.append(messages)
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
        self.root = root
        self.engine = create_engine(f"sqlite:///{root / 'app.db'}")
        bootstrap_sqlite(self.engine)
        settings = SettingsStore(root / "settings.json")
        settings.update({"ai_key_saved": True})
        FakeAIClient.created_models.clear()
        FakeAIClient.requested_messages.clear()
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

    def test_attachment_ids_are_summary_first_persisted_and_path_safe(self) -> None:
        source = self.root / "private-course-notes.txt"
        hidden_tail = "FULL-TEXT-SENTINEL-SHOULD-NOT-BE-PREFETCHED"
        source.write_text("课程摘要关键词 " + "甲" * 700 + hidden_tail, encoding="utf-8")
        attachment = self.service.ai_attachment_ingest(source)
        chat = self.service.ai_chat_new("auto", "standard", "general")

        sent = self.service.ai_chat_send(
            chat["id"],
            "概括附件",
            "auto",
            "standard",
            "general",
            [attachment["id"], attachment["id"]],
        )

        self.assertEqual([attachment["id"]], [item["id"] for item in sent["user_message"]["attachments"]])
        restored = self.service.ai_chat_session(chat["id"])
        self.assertEqual(attachment["id"], restored["messages"][0]["attachments"][0]["id"])
        encoded_dto = str(restored)
        self.assertNotIn(str(self.root), encoded_dto)
        model_input = str(FakeAIClient.requested_messages)
        self.assertIn("课程摘要关键词", model_input)
        self.assertNotIn(hidden_tail, model_input)

    def test_delete_is_idempotent(self) -> None:
        chat = self.service.ai_chat_new("deepseek-chat", "quick")
        self.assertTrue(self.service.ai_chat_delete(chat["id"])["deleted"])
        self.assertFalse(self.service.ai_chat_delete(chat["id"])["deleted"])


if __name__ == "__main__":
    unittest.main()
