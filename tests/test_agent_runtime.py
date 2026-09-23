from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from desktop_app import DesktopBridge
from sjtu_learning_assistant.agent_runtime import (
    AgentLoop,
    AgentPreset,
    AgentToolError,
    PresetError,
    PresetLoader,
    ReadOnlyToolRegistry,
)
from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient
from sjtu_learning_assistant.ai_attachments import AIManagedFileService
from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.models import (
    AIAgentTrace,
    AIChatMessage,
    Announcement,
    Assignment,
    Course,
    CourseFile,
    Email,
    UnifiedItem,
)


ALL_TOOLS = (
    "list_courses",
    "search_course_files",
    "list_course_files",
    "get_deadlines",
    "search_messages",
    "get_message_detail",
    "get_material_tree",
)


def preset(*tools: str) -> AgentPreset:
    return AgentPreset("test", "测试", "测试", tuple(tools), "仅依据工具事实回答。")


class ScriptedClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def chat_completion(self, messages, **kwargs):
        self.requests.append((messages, kwargs))
        return self.replies.pop(0)


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = create_database_engine(default_sqlite_url(self.root / "app.db"))
        bootstrap_sqlite(self.engine)
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            course = Course(source_id="course-1", name="安全课程", course_code="CS001", term_name="2026秋", raw_data={})
            session.add(course)
            session.flush()
            file = CourseFile(
                source_id="file-1",
                course_id=course.id,
                display_name="lecture.pdf",
                filename="lecture.pdf",
                size=100,
                local_path="/Users/private/archive/lecture.pdf",
                download_status="downloaded",
                is_active=True,
                last_seen_at=now,
                raw_data={},
            )
            assignment = Assignment(
                source_id="assignment-1",
                course_id=course.id,
                name="作业一",
                due_at=now + timedelta(days=2),
                is_active=True,
                last_seen_at=now,
                raw_data={"description": "完成习题"},
            )
            announcement = Announcement(
                source_id="announcement-1",
                course_id=course.id,
                title="考试公告",
                body="下周考试",
                posted_at=now,
                is_active=True,
                last_seen_at=now,
                raw_data={},
            )
            email = Email(
                source_id="mail:secret-account:1",
                subject="课程提醒",
                sender_name="teacher@example.com",
                sender_address="teacher@example.com",
                sent_at=now,
                body_preview="Bearer abc-secret",
                body_text="资料在 /Users/private/secret.txt token=top-secret",
                is_unread=True,
                raw_data={},
            )
            session.add_all((file, assignment, announcement, email))
            session.flush()
            session.add_all(
                (
                    UnifiedItem(source="canvas", item_type="assignment", source_id=assignment.source_id, course_id=course.id, assignment_id=assignment.id, title=assignment.name, occurred_at=now, due_at=assignment.due_at, is_active=True, raw_data={}),
                    UnifiedItem(source="canvas", item_type="announcement", source_id=announcement.source_id, course_id=course.id, announcement_id=announcement.id, title=announcement.title, occurred_at=now, is_active=True, raw_data={}),
                    UnifiedItem(source="email", item_type="email", source_id=email.source_id, email_id=email.id, title=email.subject, occurred_at=now, is_active=True, raw_data={}),
                )
            )
        self.registry = ReadOnlyToolRegistry(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def test_manifest_rejects_path_traversal_and_illegal_tools(self) -> None:
        preset_root = self.root / "manifest-case" / "agent_presets"
        (preset_root / "skills").mkdir(parents=True)
        (preset_root / "skills" / "ok.md").write_text("ok", encoding="utf-8")
        base = {"version": 1, "default_preset": "bad", "presets": [{"id": "bad", "name": "bad", "description": "bad", "skill": "../outside.md", "allowed_tools": []}]}
        (preset_root / "manifest.json").write_text(json.dumps(base), encoding="utf-8")
        with self.assertRaises(PresetError):
            PresetLoader(self.root / "manifest-case").load()
        base["presets"][0]["skill"] = "skills/ok.md"
        base["presets"][0]["allowed_tools"] = ["drop_database"]
        (preset_root / "manifest.json").write_text(json.dumps(base), encoding="utf-8")
        with self.assertRaisesRegex(PresetError, "非法工具"):
            PresetLoader(self.root / "manifest-case").load()

    def test_fixed_read_only_queries_validate_and_redact(self) -> None:
        courses = self.registry.execute("list_courses", {"limit": 5}, ALL_TOOLS).result
        self.assertEqual("course-1", courses["items"][0]["course_id"])
        files = self.registry.execute("search_course_files", {"query": "lecture", "limit": 5}, ALL_TOOLS).result
        encoded = json.dumps(files, ensure_ascii=False)
        self.assertIn("lecture.pdf", encoded)
        self.assertNotIn("/Users/", encoded)
        messages = self.registry.execute("search_messages", {"query": "课程", "kind": "email"}, ALL_TOOLS).result
        recent = self.registry.execute("search_messages", {"kind": "all", "limit": 10}, ALL_TOOLS).result
        self.assertGreaterEqual(recent["count"], 3)
        ref = messages["items"][0]["ref"]
        self.assertNotIn("secret-account", json.dumps(messages, ensure_ascii=False))
        detail = self.registry.execute("get_message_detail", {"kind": "email", "ref": ref}, ALL_TOOLS).result
        detail_text = json.dumps(detail, ensure_ascii=False)
        self.assertNotIn("abc-secret", detail_text)
        self.assertNotIn("top-secret", detail_text)
        self.assertNotIn("/Users/", detail_text)
        with self.assertRaises(AgentToolError):
            self.registry.execute("get_deadlines", {"limit": 999}, ALL_TOOLS)

    def test_attachment_tools_search_first_read_by_id_and_enforce_scope(self) -> None:
        first_source = self.root / "first.txt"
        first_source.write_text("量子计算课程重点 " + "细节" * 3000, encoding="utf-8")
        second_source = self.root / "second.txt"
        second_source.write_text("不应越权读取", encoding="utf-8")
        files = AIManagedFileService(self.engine, archive_root=self.root / "archive")
        first = files.ingest(first_source)
        second = files.ingest(second_source)
        registry = ReadOnlyToolRegistry(
            self.engine,
            ai_file_service=files,
            attachment_ids=[first["id"]],
        )
        allowed = ("search_ai_attachments", "read_ai_attachment_text")

        definitions = registry.definitions(allowed)
        self.assertEqual(
            {"search_ai_attachments", "read_ai_attachment_text"},
            {item["function"]["name"] for item in definitions},
        )
        search = registry.execute(
            "search_ai_attachments", {"query": "量子计算"}, allowed
        ).result
        self.assertEqual([first["id"]], [item["id"] for item in search["items"]])
        self.assertNotIn("text", search["items"][0])
        read = registry.execute(
            "read_ai_attachment_text",
            {"attachment_id": first["id"], "max_chars": 100},
            allowed,
        ).result
        self.assertEqual(100, len(read["text"]))
        self.assertTrue(read["truncated"])
        with self.assertRaisesRegex(AgentToolError, "不属于本轮消息"):
            registry.execute(
                "read_ai_attachment_text", {"attachment_id": second["id"]}, allowed
            )
        with self.assertRaises(AgentToolError):
            registry.execute(
                "read_ai_attachment_text",
                {"attachment_id": first["id"], "path": "/tmp/private"},
                allowed,
            )

    def test_deterministic_course_file_prefetch_without_model_tools(self) -> None:
        client = ScriptedClient([{"content": "找到课件。", "reasoning_content": None}])
        result = AgentLoop(client, self.registry, preset(*ALL_TOOLS)).run(
            [{"role": "user", "content": "有哪些课程资料和课件？"}],
            user_text="有哪些课程资料和课件？",
            max_tokens=500,
            temperature=0.2,
        )
        self.assertEqual("completed", result.status)
        self.assertIn("get_material_tree", [run["tool_name"] for run in result.tool_runs])
        request_text = json.dumps(client.requests[0][0], ensure_ascii=False)
        self.assertIn("lecture.pdf", request_text)

    def test_model_tool_calls_loop_and_reasoning(self) -> None:
        client = ScriptedClient(
            [
                {"content": "", "reasoning_content": "先查课程", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "list_courses", "arguments": "{\"limit\":2}"}}]},
                {"content": "共有一门课程。", "reasoning_content": "完成", "tool_calls": []},
            ]
        )
        result = AgentLoop(client, self.registry, preset("list_courses")).run(
            [{"role": "user", "content": "你好"}], user_text="你好", max_tokens=500, temperature=0
        )
        self.assertEqual(2, result.steps)
        self.assertEqual("共有一门课程。", result.content)
        self.assertIn("先查课程", result.reasoning_content or "")
        self.assertEqual("ok", result.tool_runs[0]["status"])
        self.assertTrue(any(message["role"] == "tool" for message in client.requests[1][0]))

    def test_unauthorized_tool_is_rejected(self) -> None:
        client = ScriptedClient(
            [
                {"content": "", "reasoning_content": None, "tool_calls": [{"id": "x", "type": "function", "function": {"name": "get_deadlines", "arguments": "{}"}}]},
                {"content": "无法调用未授权工具。", "reasoning_content": None, "tool_calls": []},
            ]
        )
        result = AgentLoop(client, self.registry, preset("list_courses")).run(
            [{"role": "user", "content": "你好"}], user_text="你好", max_tokens=500, temperature=0
        )
        self.assertEqual("rejected", result.tool_runs[0]["status"])
        self.assertIn("未授权", json.dumps(client.requests[1][0], ensure_ascii=False))

    def test_openai_client_sends_and_parses_tool_calls(self) -> None:
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": None, "reasoning_content": "查数据", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "list_courses", "arguments": "{}"}}]}}]},
                request=request,
            )

        client = OpenAIClassificationClient(
            api_key="fake",
            model="qwen",
            transport=httpx.MockTransport(handler),
            limiter=lambda: None,
        )
        definitions = self.registry.definitions(("list_courses",))
        result = client.chat_completion(
            [{"role": "user", "content": "课程"}],
            tools=definitions,
            tool_choice="auto",
        )
        client.close()
        self.assertEqual("list_courses", result["tool_calls"][0]["function"]["name"])
        self.assertEqual("查数据", result["reasoning_content"])
        self.assertEqual(definitions, seen[0]["tools"])
        self.assertEqual("auto", seen[0]["tool_choice"])

    def test_max_steps_is_bounded(self) -> None:
        call = {"content": "", "reasoning_content": None, "tool_calls": [{"id": "x", "type": "function", "function": {"name": "list_courses", "arguments": "{}"}}]}
        client = ScriptedClient([call for _ in range(6)])
        result = AgentLoop(client, self.registry, preset("list_courses")).run(
            [{"role": "user", "content": "你好"}], user_text="你好", max_tokens=500, temperature=0
        )
        self.assertEqual("max_steps", result.status)
        self.assertEqual(6, result.steps)
        self.assertEqual(6, len(client.requests))


class TraceAndBridgeTests(unittest.TestCase):
    class Client:
        def __init__(self, **_kwargs):
            pass

        def chat_completion(self, _messages, **_kwargs):
            return {"content": "已根据本地资料回答。", "reasoning_content": "安全推理", "tool_calls": []}

        def close(self):
            pass

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.engine = create_database_engine(default_sqlite_url(root / "app.db"))
        bootstrap_sqlite(self.engine)
        settings = SettingsStore(root / "settings.json")
        settings.update({"ai_key_saved": True})
        self.service = DashboardService(self.engine, archive_root=root / "archive", settings_store=settings, ai_key_loader=lambda: "key", ai_client_factory=self.Client)

    def tearDown(self) -> None:
        self.service.close()
        self.engine.dispose()
        self.temporary.cleanup()

    def test_trace_persistence_history_and_cascade(self) -> None:
        chat = self.service.ai_chat_new("auto", "standard", "general")
        sent = self.service.ai_chat_send(chat["id"], "请列出课程资料", preset_id="general")
        self.assertEqual(sent["trace"]["id"], sent["assistant_message"]["trace_id"])
        restored = self.service.ai_chat_session(chat["id"])
        self.assertEqual("general", restored["preset_id"])
        self.assertEqual(1, len(restored["traces"]))
        self.assertEqual(restored["traces"][0]["tool_runs"], restored["messages"][1]["tool_runs"])
        self.service.ai_chat_delete(chat["id"])
        with Session(self.engine) as session:
            self.assertEqual(0, session.scalar(select(func.count(AIAgentTrace.id))))
            self.assertEqual(0, session.scalar(select(func.count(AIChatMessage.id))))

    def test_bridge_preset_payload_contract(self) -> None:
        class Service:
            def ai_presets(self):
                return {"default_preset_id": "general", "items": []}

            def ai_chat_new(self, model, depth, preset_id):
                return {"model": model, "depth": depth, "preset_id": preset_id}

            def ai_chat_send(self, session_id, content, model, depth, preset_id):
                return {"session_id": session_id, "content": content, "model": model, "depth": depth, "preset_id": preset_id}

        bridge = DesktopBridge(Service())
        self.assertTrue(bridge.invoke("ai_presets", {})["ok"])
        payload = {"session_id": "12345678-1234-1234-1234-123456789012", "content": "你好", "model": "auto", "thinking_depth": "deep", "preset_id": "review-planner"}
        response = bridge.invoke("ai_chat_send", payload)
        self.assertTrue(response["ok"])
        self.assertEqual("review-planner", response["data"]["preset_id"])
        self.assertFalse(bridge.invoke("ai_chat_send", {**payload, "preset_id": "../bad"})["ok"])

    def test_bridge_accepts_attachment_ids_and_rejects_extra_or_invalid_fields(self) -> None:
        class Service:
            def ai_chat_send(self, session_id, content, model, depth, preset_id, attachment_ids):
                return {
                    "session_id": session_id,
                    "content": content,
                    "model": model,
                    "depth": depth,
                    "preset_id": preset_id,
                    "attachment_ids": attachment_ids,
                }

        bridge = DesktopBridge(Service())
        payload = {
            "session_id": "12345678-1234-1234-1234-123456789012",
            "content": "读取附件",
            "attachment_ids": [3, 3, 5],
        }
        response = bridge.invoke("ai_chat_send", payload)
        self.assertTrue(response["ok"])
        self.assertEqual([3, 5], response["data"]["attachment_ids"])
        self.assertFalse(bridge.invoke("ai_chat_send", {**payload, "attachment_ids": [True]})["ok"])
        self.assertFalse(bridge.invoke("ai_chat_send", {**payload, "path": "/tmp/a"})["ok"])


if __name__ == "__main__":
    unittest.main()
