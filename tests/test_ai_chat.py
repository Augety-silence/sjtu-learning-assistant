from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from office_fixtures import build_pptx
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sjtu_learning_assistant.dashboard_service import DashboardError, DashboardService
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.models import AIChatMessage


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
        settings = SettingsStore(root / "config" / "settings.json")
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

    def test_transaction_conflict_retries_save_without_recalling_ai(self) -> None:
        chat = self.service.ai_chat_new("auto", "standard")
        original_flush = Session.flush
        conflicts = []

        def flush_once(session, *args, **kwargs):
            if any(isinstance(row, AIChatMessage) for row in session.new) and not conflicts:
                conflicts.append(True)
                raise IntegrityError("fake sequence conflict", {}, RuntimeError("fake"))
            return original_flush(session, *args, **kwargs)

        with patch.object(Session, "flush", flush_once):
            result = self.service.ai_chat_send(chat["id"], "假模型回归测试")
        self.assertEqual(1, len(conflicts))
        self.assertEqual(1, len(FakeAIClient.requested_messages))
        restored = self.service.ai_chat_session(chat["id"])
        self.assertEqual(2, len(restored["messages"]))
        self.assertEqual(1, len(restored["traces"]))
        self.assertGreaterEqual(result["trace"]["duration_ms"], 0)
        self.assertGreaterEqual(restored["traces"][0]["duration_ms"], 0)

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

    def test_turn_duration_is_returned_live_and_recovered_after_reload(self) -> None:
        moments = iter(
            [
                datetime(2026, 11, 2, 11, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 2, 11, 1, 53, tzinfo=timezone.utc),
            ]
        )
        last = [datetime(2026, 11, 2, 11, 1, 53, tzinfo=timezone.utc)]

        def clock() -> datetime:
            try:
                last[0] = next(moments)
            except StopIteration:
                pass
            return last[0]

        chat = self.service.ai_chat_new("deepseek-chat", "standard", "general")
        self.service.now_provider = clock
        result = self.service.ai_chat_send(chat["id"], "总结本周", "deepseek-chat", "standard", "general")

        self.assertIsInstance(result["trace"]["duration_ms"], int)
        self.assertGreaterEqual(result["trace"]["duration_ms"], 0)
        restored = self.service.ai_chat_session(chat["id"])
        # 用户消息以本轮开始时刻入库、助手消息以完成时刻入库，重载后仍能得到耗时。
        self.assertEqual(113_000, restored["traces"][0]["duration_ms"])
        self.assertLess(
            restored["messages"][0]["created_at"], restored["messages"][1]["created_at"]
        )

    def test_legacy_turns_without_distinct_timestamps_report_unknown_duration(self) -> None:
        fixed = datetime(2026, 11, 2, 11, 0, 0, tzinfo=timezone.utc)
        self.service.now_provider = lambda: fixed
        chat = self.service.ai_chat_new("deepseek-chat", "standard", "general")
        self.service.ai_chat_send(chat["id"], "旧记录", "deepseek-chat", "standard", "general")
        self.assertIsNone(self.service.ai_chat_session(chat["id"])["traces"][0]["duration_ms"])

    def test_long_session_history_is_packed_by_budget_and_flagged(self) -> None:
        from sjtu_learning_assistant.dashboard_service import pack_chat_history

        history = []
        for index in range(30):
            history.append(("user", f"问题 {index}"))
            history.append(("assistant", f"回答 {index} " + "很长的内容" * 2000))
        packed, omitted = pack_chat_history(history, budget=10_000)
        total = sum(len(item["content"]) for item in packed)
        self.assertLessEqual(total, 10_000)
        self.assertGreater(omitted, 0)
        self.assertEqual(len(history), len(packed) + omitted)
        self.assertEqual("user", packed[0]["role"])
        # 最近一条回答保留开头与结尾，中间注明已省略。
        self.assertTrue(packed[-1]["content"].startswith("回答 29"))
        self.assertIn("中间部分已省略", packed[-1]["content"])
        self.assertTrue(packed[-1]["content"].endswith("很长的内容"))
        short, none_omitted = pack_chat_history([("assistant", "孤立回答"), ("user", "你好")])
        self.assertEqual(([{"role": "user", "content": "你好"}], 1), (short, none_omitted))

    def test_long_session_sends_bounded_history_with_notice(self) -> None:
        chat = self.service.ai_chat_new("deepseek-chat", "standard", "general")
        for index in range(45):
            self.service.ai_chat_send(
                chat["id"], f"第 {index} 个问题 " + "背景说明" * 900, "deepseek-chat", "standard", "general"
            )
        self.service.ai_chat_send(chat["id"], "总结一下", "deepseek-chat", "standard", "general")
        request = FakeAIClient.requested_messages[-1]
        history_chars = sum(
            len(item["content"]) for item in request if item["role"] in {"user", "assistant"} and isinstance(item["content"], str)
        )
        # 历史按字符预算装入（约 24k + 本轮问题），不会随会话增长而无限变大。
        self.assertLess(history_chars, 30_000)
        self.assertEqual("总结一下", request[-1]["content"])
        notices = [item["content"] for item in request if item["role"] == "system" and "长会话提示" in item["content"]]
        self.assertEqual(1, len(notices))

    def test_user_skill_references_and_workspace_tools_reach_the_agent(self) -> None:
        notes = self.root / "skill-notes.txt"
        notes.write_text("宏观经济学术语表：GDP、CPI、乘数效应", encoding="utf-8")
        reference = self.service.ai_attachment_ingest(notes)
        workspace = self.root / "workspace"
        workspace.mkdir()
        (workspace / "提纲.md").write_text("# 提纲\n", encoding="utf-8")
        status = self.service.ai_workspace_set(str(workspace))
        self.assertEqual((True, True, "workspace"), (status["configured"], status["available"], status["name"]))
        saved = self.service.ai_skill_save(
            {
                "name": "宏观复习",
                "instructions": "用术语表解释概念。",
                "allowed_tools": ["read_ai_attachment_text", "list_workspace_files", "propose_file_change"],
                "reference_attachment_ids": [reference["id"]],
            }
        )
        skill_id = saved["skill"]["id"]
        self.assertIn(skill_id, [item["id"] for item in saved["items"]])
        chat = self.service.ai_chat_new("deepseek-chat", "standard", skill_id)
        self.service.ai_chat_send(chat["id"], "解释乘数效应", "deepseek-chat", "standard", skill_id)
        request = str(FakeAIClient.requested_messages[-1])
        self.assertIn("用术语表解释概念", request)
        self.assertIn("宏观复习", request)
        self.assertIn("skill-notes.txt", request)
        self.assertIn("AI 工作区「workspace」", request)
        self.assertNotIn(str(workspace), request)
        # 删除技能后，绑定该技能的会话回退到默认助手而不是报错。
        self.service.ai_skill_delete(skill_id)
        self.service.ai_chat_send(chat["id"], "继续", "deepseek-chat", "standard", skill_id)
        self.assertNotIn("用术语表解释概念", str(FakeAIClient.requested_messages[-1]))
        with self.assertRaises(DashboardError):
            self.service.ai_skill_save({"name": "x", "instructions": "y", "reference_attachment_ids": [99999]})
        with self.assertRaises(DashboardError):
            self.service.ai_workspace_set(str(self.root / "archive"))
        self.assertFalse(self.service.ai_workspace_set("")["configured"])

    def test_file_change_apply_goes_through_the_service(self) -> None:
        workspace = self.root / "ws2"
        workspace.mkdir()
        self.service.ai_workspace_set(str(workspace))
        from sjtu_learning_assistant.ai_workspace import AIWorkspace

        change = AIWorkspace(workspace, changes_dir=self.service.ai_changes_dir).propose(
            "总结.md", mode="create", content="# 总结\n"
        )
        self.assertEqual("pending", self.service.ai_file_change_get(change["change_id"])["status"])
        self.assertEqual("applied", self.service.ai_file_change_apply(change["change_id"])["status"])
        self.assertEqual("# 总结\n", (workspace / "总结.md").read_text(encoding="utf-8"))
        with self.assertRaises(DashboardError):
            self.service.ai_file_change_get("../../etc")

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
        # 从日程页带上下文进入对话：预算按学习对话，而不是日程页的单次调用。
        self.assertEqual("learning_chat", snapshot["task"]["capability_id"])
        self.assertGreaterEqual(snapshot["task"]["budget"]["max_model_calls"], 7)
        self.assertIn(
            "建议", snapshot["task"]["result"]["chat_result"]["assistant_message"]["content"]
        )
        self.assertEqual(1, snapshot["task"]["usage"]["unknown_usage_calls"])
        self.assertEqual(0, snapshot["task"]["usage"]["total_tokens"])


if __name__ == "__main__":
    unittest.main()
