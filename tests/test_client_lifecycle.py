from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

import httpx
import sync_data_to_db
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine

from desktop_app import run_desktop_app
from sjtu_learning_assistant.archive_service import ArchiveError, ArchiveService
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.local_settings import LocalSettings
from sjtu_learning_assistant.models import Base


class CloseableClient:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class AIClient(CloseableClient):
    model = "deepseek-chat"

    def test_connection(self) -> str:
        return "courseware"

    def classify_many(self, items):
        return {item.source_id: "courseware" for item in items}


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve(self, **_overrides):
        return LocalSettings(
            archive_root=str(self.root),
            auto_download_current_term=False,
            organize_by_category=True,
            ai_enabled=True,
            ai_key_saved=True,
        )


class DashboardClientLifecycleTests(unittest.TestCase):
    def test_canvas_client_is_reused_and_close_is_idempotent(self) -> None:
        created: list[CloseableClient] = []

        def factory():
            client = CloseableClient()
            created.append(client)
            return client

        service = DashboardService(SimpleNamespace(), canvas_client_factory=factory)
        self.assertIs(service._get_canvas_client(), service._get_canvas_client())
        self.assertEqual(1, len(created))
        service.close()
        service.close()
        self.assertEqual(1, created[0].close_calls)
        with self.assertRaisesRegex(Exception, "已关闭"):
            service._get_canvas_client()

    def test_multiple_canvas_images_construct_one_client(self) -> None:
        factory_calls = 0
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=b"image",
                request=request,
            )

        client = httpx.Client(
            headers={"Authorization": "Bearer test"},
            transport=httpx.MockTransport(handler),
        )

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            return client

        service = DashboardService(SimpleNamespace(), canvas_client_factory=factory)
        service._canvas_image_data_url("https://oc.sjtu.edu.cn/images/one.png")
        service._canvas_image_data_url("https://oc.sjtu.edu.cn/images/two.png")
        self.assertEqual(1, factory_calls)
        self.assertEqual(2, len(requests))
        self.assertFalse(client.is_closed)
        service.close()
        self.assertTrue(client.is_closed)

    def test_ai_test_and_organize_share_one_key_read_and_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = create_engine(f"sqlite+pysqlite:///{root / 'app.db'}")
            Base.metadata.create_all(engine)
            key_reads: list[None] = []
            clients: list[AIClient] = []

            def load_key():
                key_reads.append(None)
                return "ai-key"

            def make_ai(**_kwargs):
                client = AIClient()
                clients.append(client)
                return client

            def fail_canvas():
                raise AssertionError("AI 整理不得构造或读取 Canvas 客户端")

            service = DashboardService(
                engine,
                archive_root=root,
                settings_store=Store(root),
                ai_key_loader=load_key,
                ai_client_factory=make_ai,
                canvas_client_factory=fail_canvas,
            )
            self.assertTrue(service.test_ai_connection()["ok"])
            self.assertTrue(service.test_ai_connection()["ok"])
            service.organize_archive()
            self.assertEqual(1, len(key_reads))
            self.assertEqual(1, len(clients))
            service.close()
            service.close()
            self.assertEqual(1, clients[0].close_calls)
            engine.dispose()


class DesktopLifecycleTests(unittest.TestCase):
    def test_scheduler_stops_before_service_close(self) -> None:
        events: list[str] = []
        engine = SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql"),
            dispose=lambda: events.append("dispose"),
        )
        service = SimpleNamespace(
            trigger_sync=lambda: None,
            close=lambda: events.append("close"),
        )
        scheduler = SimpleNamespace(
            start=lambda: events.append("start"),
            stop=lambda: events.append("stop"),
        )
        webview = SimpleNamespace(
            windows=[],
            FOLDER_DIALOG=object(),
            create_window=lambda *_args, **_kwargs: None,
            start=lambda **_kwargs: events.append("window-exit"),
        )
        static_index = SimpleNamespace(
            is_file=lambda: True,
            as_uri=lambda: "file:///index.html",
        )
        with (
            patch.dict("sys.modules", {"webview": webview}),
            patch("desktop_app.STATIC_INDEX", static_index),
            patch("desktop_app.create_database_engine", return_value=engine),
            patch("desktop_app.DashboardService", return_value=service),
            patch("desktop_app.DesktopScheduler", return_value=scheduler),
        ):
            self.assertEqual(0, run_desktop_app())
        self.assertLess(events.index("stop"), events.index("close"))
        self.assertLess(events.index("close"), events.index("dispose"))


class SyncCredentialLifecycleTests(unittest.TestCase):
    def test_no_download_does_not_read_ai_key(self) -> None:
        engine = SimpleNamespace(
            dialect=SimpleNamespace(name="postgresql"),
            dispose=lambda: None,
        )
        settings = LocalSettings(
            archive_root="/tmp/archive",
            auto_download_current_term=False,
            organize_by_category=True,
            ai_enabled=True,
            ai_key_saved=True,
        )
        health = SimpleNamespace(database="test", server_version="16")
        with (
            patch.object(sync_data_to_db.SettingsStore, "resolve", return_value=settings),
            patch.object(sync_data_to_db, "create_database_engine", return_value=engine),
            patch.object(sync_data_to_db, "check_database", return_value=health),
            patch.object(sync_data_to_db, "get_ai_api_key") as get_ai_api_key,
            patch.object(sync_data_to_db, "sync_canvas") as sync_canvas,
        ):
            self.assertEqual(
                0,
                sync_data_to_db.main(["--canvas-only", "--no-download"]),
            )
        get_ai_api_key.assert_not_called()
        sync_canvas.assert_called_once()
        self.assertFalse(sync_canvas.call_args.kwargs["download"])
        self.assertTrue(sync_canvas.call_args.kwargs["ai_enabled"])
        self.assertIsNone(sync_canvas.call_args.kwargs["ai_client"])


class ArchiveOptionalCanvasTests(unittest.TestCase):
    def test_local_organize_allows_none_but_download_requires_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = create_engine(f"sqlite+pysqlite:///{root / 'app.db'}")
            Base.metadata.create_all(engine)
            service = ArchiveService(
                engine,
                None,
                archive_root=root,
                active_course_source_ids=set(),
            )
            summary = service.organize_current_term()
            self.assertEqual(0, summary.moved)
            with self.assertRaisesRegex(ArchiveError, "需要 Canvas 客户端"):
                service.archive_current_term()
            with self.assertRaisesRegex(ArchiveError, "需要 Canvas 客户端"):
                service.download_file_by_source_id("1")
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
