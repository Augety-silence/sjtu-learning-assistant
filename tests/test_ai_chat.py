from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from office_fixtures import build_pptx
from PIL import Image
from sqlalchemy import create_engine

from sjtu_learning_assistant.dashboard_service import DashboardError, DashboardService
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

    def test_qwen_vision_receives_image_bytes_and_other_models_fail_early(self) -> None:
        source = self.root / "whiteboard.png"
        Image.new("RGB", (64, 48), "white").save(source)
        attachment = self.service.ai_attachment_ingest(source)
        chat = self.service.ai_chat_new("qwen3.8-27b", "standard", "general")

        sent = self.service.ai_chat_send(
            chat["id"],
            "识别图片内容",
            "qwen3.8-27b",
            "standard",
            "general",
            [attachment["id"]],
        )

        self.assertIn("建议", sent["assistant_message"]["content"])
        user_content = FakeAIClient.requested_messages[-1][-1]["content"]
        self.assertIsInstance(user_content, list)
        self.assertEqual("image_url", user_content[0]["type"])
        self.assertEqual({"url"}, set(user_content[0]["image_url"]))
        self.assertTrue(user_content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual("text", user_content[1]["type"])
        with self.assertRaisesRegex(DashboardError, "Qwen 3.8"):
            self.service.ai_chat_send(
                chat["id"], "再次识别", "deepseek-chat", "standard", "general", [attachment["id"]]
            )

    def test_auto_routes_image_attachment_to_qwen(self) -> None:
        source = self.root / "auto-whiteboard.png"
        Image.new("RGB", (32, 24), "white").save(source)
        attachment = self.service.ai_attachment_ingest(source)
        chat = self.service.ai_chat_new("auto", "standard", "general")

        result = self.service.ai_chat_send(
            chat["id"], "识别图片", "auto", "standard", "general", [attachment["id"]]
        )

        self.assertEqual("qwen3.8-27b", result["assistant_message"]["model"])
        self.assertEqual("image_url", FakeAIClient.requested_messages[-1][-1]["content"][0]["type"])

    def test_qwen_receives_pptx_embedded_images_while_text_models_get_a_notice(self) -> None:
        attachment = self.service.ai_attachment_ingest(build_pptx(self.root / "课件.pptx"))
        self.assertEqual("ready", attachment["text_status"])
        chat = self.service.ai_chat_new("auto", "standard", "general")

        auto = self.service.ai_chat_send(
            chat["id"], "总结这份课件", "auto", "standard", "general", [attachment["id"]]
        )
        # 文档不会把 auto 改路由到 Qwen；文本模型只收到“图片未发送”的说明。
        self.assertEqual("deepseek-chat", auto["assistant_message"]["model"])
        text_request = FakeAIClient.requested_messages[-1]
        self.assertIsInstance(text_request[-1]["content"], str)
        notices = [m["content"] for m in text_request if m["role"] == "system" and "文档内嵌图片" in m["content"]]
        self.assertEqual(1, len(notices))
        self.assertIn("当前模型不支持图片", notices[0])

        qwen = self.service.ai_chat_send(
            chat["id"], "图里画了什么", "qwen3.8-27b", "standard", "general", [attachment["id"]]
        )
        self.assertEqual("qwen3.8-27b", qwen["assistant_message"]["model"])
        vision_request = FakeAIClient.requested_messages[-1]
        user_content = vision_request[-1]["content"]
        self.assertEqual(["image_url", "text"], [part["type"] for part in user_content])
        labels = [m["content"] for m in vision_request if m["role"] == "system" and "随消息附带了文档内嵌图片" in m["content"]]
        self.assertEqual(1, len(labels))
        self.assertIn("第 2 页", labels[0])
        self.assertNotIn(str(self.root), str(vision_request))

    def test_delete_is_idempotent(self) -> None:
        chat = self.service.ai_chat_new("deepseek-chat", "quick")
        self.assertTrue(self.service.ai_chat_delete(chat["id"])["deleted"])
        self.assertFalse(self.service.ai_chat_delete(chat["id"])["deleted"])

    def test_stream_task_persists_page_context_result_and_unknown_usage(self) -> None:
        chat = self.service.ai_chat_new("auto", "standard")
        started = self.service.ai_chat_stream_start(
            chat["id"],
            "检查最近的作业截止时间",
            "auto",
            "standard",
            "general",
            page_context={
                "page_id": "calendar",
                "source": "page-header",
                "title": "日程",
            },
        )
        task_id = started["task"]["id"]
        snapshot = started
        for _ in range(100):
            snapshot = self.service.ai_task_status(task_id)
            if snapshot["task"]["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.01)
        self.assertEqual("completed", snapshot["task"]["status"])
        self.assertEqual("calendar", snapshot["task"]["page_id"])
        self.assertIn(
            "建议", snapshot["task"]["result"]["chat_result"]["assistant_message"]["content"]
        )
        self.assertEqual(1, snapshot["task"]["usage"]["unknown_usage_calls"])
        self.assertEqual(0, snapshot["task"]["usage"]["total_tokens"])


if __name__ == "__main__":
    unittest.main()
