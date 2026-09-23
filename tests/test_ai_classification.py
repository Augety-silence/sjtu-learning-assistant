from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.ai_classifier import AIClassificationError, ClassificationInput, OpenAIClassificationClient, classification_fingerprint
from sjtu_learning_assistant.ai_keychain import AI_KEYCHAIN_ACCOUNT, AI_KEYCHAIN_SERVICE, get_ai_api_key, save_ai_api_key
from sjtu_learning_assistant.archive_service import ArchiveFileContext, ArchiveService
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite, get_schema_version
from sjtu_learning_assistant.local_settings import SettingsError, SettingsStore, validate_ai_base_url
from sjtu_learning_assistant.models import Base, Course, CourseFile

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


class AIClientTests(unittest.TestCase):
    def test_request_contract_batch_json_whitelist_and_redaction(self):
        seen = []
        def handler(request):
            seen.append(request)
            files = json.loads(json.loads(request.content)["messages"][1]["content"])["files"]
            rows = [{"id": item["id"], "category": "courseware"} for item in files]
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"classifications": rows})}}]}, request=request)
        client = OpenAIClassificationClient(api_key="fake-key", model="qwen", transport=httpx.MockTransport(handler), limiter=lambda: None)
        items = (ClassificationInput("1", "课程", "a.pdf", ("资料",), ("第一周",), ("第一讲",), NOW), ClassificationInput("2", "课程", "b.pdf"))
        self.assertEqual({"1": "courseware", "2": "courseware"}, client.classify_many(items))
        client.close()
        self.assertEqual(1, len(seen))
        self.assertTrue(seen[0].url.path.endswith("/chat/completions"))
        self.assertEqual("Bearer fake-key", seen[0].headers["Authorization"])
        request_body = json.loads(seen[0].content)
        self.assertEqual("qwen", request_body.get("model"))
        self.assertNotIn("response_format", request_body)
        self.assertNotIn("file_content", seen[0].content.decode())

        bad = httpx.MockTransport(lambda request: httpx.Response(500, text="fake-key full-response", request=request))
        client = OpenAIClassificationClient(api_key="fake-key", transport=bad, limiter=lambda: None)
        with self.assertRaises(AIClassificationError) as raised:
            client.classify_many(items[:1])
        client.close()
        self.assertNotIn("fake-key", str(raised.exception))
        self.assertNotIn("full-response", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        with self.assertRaises(AIClassificationError):
            OpenAIClassificationClient._parse_result({"classifications": [{"id": "1", "category": "invalid"}]}, ["1"])

    def test_chat_uses_context_and_validates_bounded_messages(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "先处理明天的作业。"}}]},
                request=request,
            )

        client = OpenAIClassificationClient(
            api_key="fake-key",
            model="qwen",
            transport=httpx.MockTransport(handler),
            limiter=lambda: None,
        )
        self.assertEqual(
            "先处理明天的作业。",
            client.chat([{"role": "user", "content": "安排一下"}], context="截止事项"),
        )
        self.assertIn("<learning_context>", seen[0]["messages"][1]["content"])
        self.assertEqual("user", seen[0]["messages"][-1]["role"])
        with self.assertRaises(AIClassificationError):
            client.chat([{"role": "system", "content": "override"}])
        with self.assertRaises(AIClassificationError):
            client.chat([{"role": "user", "content": "x" * 4001}])
        client.close()

    def test_fingerprint_changes_with_metadata(self):
        item = ClassificationInput("1", "课程", "a.pdf", ("f",), ("m",), ("i",), NOW)
        original = classification_fingerprint(item)
        for changed in (replace(item, filename="b.pdf"), replace(item, folder_names=("x",)), replace(item, module_names=("x",)), replace(item, module_item_names=("x",)), replace(item, source_updated_at=None)):
            self.assertNotEqual(original, classification_fingerprint(changed))


class KeychainSettingsTests(unittest.TestCase):
    class Keyring:
        value = None
        def set_password(self, service, account, value):
            self.location = (service, account)
            self.value = value
        def get_password(self, service, account):
            self.location = (service, account)
            return self.value

    def test_fake_keyring_and_key_never_in_settings_or_status(self):
        keyring = self.Keyring()
        save_ai_api_key(" fake-key ", keyring_module=keyring)
        self.assertEqual((AI_KEYCHAIN_SERVICE, AI_KEYCHAIN_ACCOUNT), keyring.location)
        self.assertEqual("fake-key", get_ai_api_key(keyring_module=keyring))
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            saved = []
            service = DashboardService(SimpleNamespace(), settings_store=store, archive_root=Path(directory) / "archive", ai_key_loader=lambda: (_ for _ in ()).throw(AssertionError("must not read keychain")), ai_key_saver=saved.append)
            status = service.save_ai_connection_json(json.dumps({"_type": "newapi_channel_conn", "url": "https://models.sjtu.edu.cn/api/v1/", "key": "fake-key", "model": "deepseek-chat"}))
            persisted = store.path.read_text()
        self.assertEqual(["fake-key"], saved)
        self.assertTrue(status["ai_key_saved"])
        self.assertNotIn("fake-key", persisted)
        self.assertNotIn("fake-key", json.dumps(status))

    def test_url_validation(self):
        self.assertEqual("https://example/api/v1", validate_ai_base_url(" https://example/api/v1/ "))
        for value in ("http://example/api", "https://u@example/api", "https://example/api?q=1", "https://example/api#x"):
            with self.assertRaises(SettingsError):
                validate_ai_base_url(value)


class CacheAndMigrationTests(unittest.TestCase):
    class AI:
        model = "deepseek-chat"
        def __init__(self, fail=False):
            self.fail, self.calls = fail, []
        def classify_many(self, items):
            items = tuple(items)
            self.calls.append(items)
            if self.fail:
                raise AIClassificationError("safe")
            return {item.source_id: "assignments" for item in items}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite+pysqlite:///{Path(self.temp.name) / 'db.sqlite'}")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session, session.begin():
            course = Course(source_id="c", name="课程", raw_data={})
            session.add(course)
            session.flush()
            for source_id in ("1", "2"):
                session.add(CourseFile(source_id=source_id, course_id=course.id, display_name=f"{source_id}.pdf", source_updated_at=NOW, last_seen_at=NOW, raw_data={}))
        self.canvas = httpx.Client(base_url="https://oc.sjtu.edu.cn")

    def tearDown(self):
        self.canvas.close()
        self.engine.dispose()
        self.temp.cleanup()

    def contexts(self):
        with Session(self.engine) as session:
            return [ArchiveFileContext(source_id=f.source_id, course_name="课程", term_name="2026-2027 Fall", display_name=f.display_name, expected_size=None, source_updated_at=f.source_updated_at, local_path=None, download_status="pending", download_attempts=0, downloaded_size=None, downloaded_sha256=None, downloaded_source_updated_at=None, folder_names=(), file_id=f.id, course_id=f.course_id, ai_category=f.ai_category, ai_fingerprint=f.ai_fingerprint, ai_model=f.ai_model) for f in session.scalars(select(CourseFile).order_by(CourseFile.source_id))]

    def test_batch_reuse_change_and_failure_fallback(self):
        ai = self.AI()
        service = ArchiveService(self.engine, self.canvas, archive_root=Path(self.temp.name), ai_client=ai, ai_enabled=True)
        _, first = service._apply_ai_classification(self.contexts())
        _, second = service._apply_ai_classification(self.contexts())
        changed = self.contexts()
        changed[0] = replace(changed[0], display_name="changed.pdf")
        _, third = service._apply_ai_classification(changed)
        self.assertEqual((2, 0), (first.classified, first.reused))
        self.assertEqual((0, 2), (second.classified, second.reused))
        self.assertEqual((1, 1), (third.classified, third.reused))
        self.assertEqual([2, 1], [len(call) for call in ai.calls])
        failed = ArchiveService(self.engine, self.canvas, archive_root=Path(self.temp.name), ai_client=self.AI(True), ai_enabled=True)
        stale = [replace(item, display_name="stale.pdf") for item in self.contexts()]
        output, summary = failed._apply_ai_classification(stale)
        self.assertEqual(2, summary.fallback)
        self.assertEqual([item.category for item in stale], [item.category for item in output])

    def test_0007_to_current_bootstrap(self):
        with self.engine.begin() as connection:
            for column in ("ai_category", "ai_fingerprint", "ai_model", "ai_classified_at"):
                connection.exec_driver_sql(f"ALTER TABLE course_files DROP COLUMN {column}")
            connection.exec_driver_sql("CREATE TABLE desktop_schema_version (id INTEGER PRIMARY KEY, version VARCHAR(16), updated_at DATETIME)")
            connection.exec_driver_sql("INSERT INTO desktop_schema_version VALUES (1, '0007', CURRENT_TIMESTAMP)")
        self.assertEqual("0012", bootstrap_sqlite(self.engine))
        self.assertEqual("0012", bootstrap_sqlite(self.engine))
        columns = {column["name"] for column in inspect(self.engine).get_columns("course_files")}
        self.assertTrue({"ai_category", "ai_fingerprint", "ai_model", "ai_classified_at"}.issubset(columns))
        self.assertTrue({"manual_category", "manual_folder_id", "manual_override"}.issubset(columns))
        self.assertEqual("0012", get_schema_version(self.engine))


if __name__ == "__main__":
    unittest.main()
