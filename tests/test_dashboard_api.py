from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard_api import SECURITY_HEADERS, create_app


class FakeDashboardService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def health(self):
        return {"status": "ok", "time": "2026-09-21T12:00:00+08:00"}

    def overview(self):
        return {
            "courses": 4,
            "upcoming_deadlines": 2,
            "unread_emails": 3,
            "deadlines": [],
            "messages": [],
        }

    def deadlines(self, hours: int):
        self.calls.append(("deadlines", hours))
        return [{"source_id": "a1", "title": "作业", "course": "课程", "due_at": None, "submission_state": "unsubmitted", "url": None}]

    def messages(self, kind: str):
        self.calls.append(("messages", kind))
        return [{"kind": "email", "title": "通知", "source_label": "教务处", "occurred_at": None, "is_unread": True, "url": None}]

    def material_filters(self):
        return {"terms": [], "courses": [], "download_statuses": ["all"]}

    def materials(self, **kwargs):
        self.calls.append(("materials", kwargs))
        return [{"source_id": "f1", "name": "讲义.pdf", "course": "课程", "course_id": "c1", "term": "2026-2027 Fall", "size": 12, "updated_at": None, "download_status": "pending", "can_open": False}]

    def sync_status(self):
        return {"status": "idle", "last_success_at": None, "last_run_status": None, "last_run_at": None}

    def trigger_sync(self):
        self.calls.append(("sync", None))
        return {"status": "accepted", "mode": "sync_runner"}

    def download_material(self, source_id: str):
        self.calls.append(("download", source_id))
        return {"source_id": source_id, "status": "downloaded", "size": 12}

    def open_material(self, source_id: str):
        self.calls.append(("open", source_id))
        return {"source_id": source_id, "status": "opened"}


class DashboardApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        static_dir = Path(self.temp.name)
        (static_dir / "index.html").write_text("<main>dashboard</main>", encoding="utf-8")
        self.service = FakeDashboardService()
        self.client = TestClient(
            create_app(
                engine=SimpleNamespace(),
                service=self.service,
                static_dir=static_dir,
                csrf_token="test-csrf",
            )
        )
        self.base_headers = {"host": "127.0.0.1:17655"}

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def post_headers(self) -> dict[str, str]:
        return {
            **self.base_headers,
            "origin": "http://127.0.0.1:17655",
            "x-csrf-token": "test-csrf",
        }

    def test_get_queries_and_whitelisted_fields(self) -> None:
        response = self.client.get(
            "/api/deadlines?window=14d", headers=self.base_headers
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(("deadlines", 336), self.service.calls[-1])
        self.assertEqual(
            {"source_id", "title", "course", "due_at", "submission_state", "url"},
            set(response.json()["items"][0]),
        )
        self.assertFalse(
            {"raw_data", "token", "password", "database_url"}
            & set(response.text.lower())
        )

        response = self.client.get(
            "/api/materials?term=2026-2027%20Fall&course_id=c1&status=pending",
            headers=self.base_headers,
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            (
                "materials",
                {"term": "2026-2027 Fall", "course_id": "c1", "status_filter": "pending"},
            ),
            self.service.calls[-1],
        )

    def test_host_origin_and_csrf_are_enforced(self) -> None:
        self.assertEqual(
            400,
            self.client.get("/api/health", headers={"host": "evil.example"}).status_code,
        )
        self.assertEqual(
            403,
            self.client.post(
                "/api/sync-trigger",
                headers={**self.base_headers, "origin": "http://evil.example"},
            ).status_code,
        )
        self.assertEqual(
            403,
            self.client.post(
                "/api/sync-trigger",
                headers={**self.base_headers, "origin": "http://127.0.0.1:17655"},
            ).status_code,
        )
        response = self.client.post(
            "/api/sync-trigger", headers=self.post_headers()
        )
        self.assertEqual(202, response.status_code)
        self.assertEqual("accepted", response.json()["status"])

    def test_dto_rejects_extra_fields_and_security_headers_are_present(self) -> None:
        response = self.client.post(
            "/api/materials/download",
            headers=self.post_headers(),
            json={"source_id": "f1", "token": "must-not-pass"},
        )
        self.assertEqual(422, response.status_code)
        good = self.client.post(
            "/api/materials/download",
            headers=self.post_headers(),
            json={"source_id": "f1"},
        )
        self.assertEqual(200, good.status_code)
        for name, value in SECURITY_HEADERS.items():
            self.assertEqual(value, good.headers[name])
        self.assertEqual("no-store", good.headers["cache-control"])

    def test_csrf_endpoint_and_spa(self) -> None:
        token = self.client.get("/api/csrf", headers=self.base_headers)
        self.assertEqual({"csrf_token": "test-csrf"}, token.json())
        page = self.client.get("/anything", headers=self.base_headers)
        self.assertEqual(200, page.status_code)
        self.assertIn("dashboard", page.text)

    def test_unknown_api_paths_return_json_404(self) -> None:
        responses = (
            self.client.get("/api/does-not-exist", headers=self.base_headers),
            self.client.post("/api/does-not-exist", headers=self.post_headers()),
        )

        for response in responses:
            with self.subTest(method=response.request.method):
                self.assertEqual(404, response.status_code)
                self.assertEqual({"detail": "Not Found"}, response.json())
                self.assertTrue(
                    response.headers["content-type"].startswith("application/json")
                )
                for name, value in SECURITY_HEADERS.items():
                    self.assertEqual(value, response.headers[name])
                self.assertEqual("no-store", response.headers["cache-control"])


if __name__ == "__main__":
    unittest.main()
