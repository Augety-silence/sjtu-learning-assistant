from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx
from sqlalchemy import create_engine

from desktop_app import DesktopBridge
from sjtu_learning_assistant.canvas_client import CanvasClient, CanvasProtocolError
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.models import Base


class CanvasHelperClientContractTests(unittest.TestCase):
    def test_calendar_roster_submission_and_grading_routes(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/v1/users/self/colors":
                payload: object = {"custom_colors": {"course_1": "#001122"}}
            elif request.method in {"PUT", "POST"}:
                payload = {"id": 90, "grade": "9"}
            elif request.url.path.endswith("/submissions/10"):
                payload = {"id": 90, "submission_comments": []}
            else:
                payload = [{"id": len(requests)}]
            return httpx.Response(200, json=payload, request=request)

        with httpx.Client(
            base_url="https://oc.sjtu.edu.cn",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = CanvasClient(client=http_client, token="secret")
            self.assertIn("custom_colors", client.colors())
            client.calendar_events(
                ["course_1", "course_2"],
                "2026-09-01T00:00:00+00:00",
                "2026-10-01T00:00:00+00:00",
            )
            client.course_users(1)
            client.course_students(1)
            client.assignment_submissions(1, 21)
            client.submission_for_user(1, 21, 10)
            client.user_submissions(1, [10, 11])
            client.update_user_submission(
                1,
                21,
                10,
                [
                    ("submission[posted_grade]", "9"),
                    ("comment[text_comment]", "很好"),
                ],
            )
            client.submit_assignment(
                1, 21, "online_upload", file_ids=[31, 32], comment="已提交"
            )

        by_path = {request.url.path: request for request in requests}
        calendar = by_path["/api/v1/calendar_events"]
        self.assertEqual("assignment", calendar.url.params["type"])
        self.assertEqual(["course_1", "course_2"], calendar.url.params.get_list("context_codes[]"))
        students = [
            request
            for request in requests
            if request.url.path == "/api/v1/courses/1/users"
            and request.url.params.get("enrollment_type[]") == "student"
        ][0]
        self.assertEqual("student", students.url.params["enrollment_type[]"])
        grouped = by_path["/api/v1/courses/1/students/submissions"]
        self.assertEqual(["10", "11"], grouped.url.params.get_list("student_ids[]"))
        update = [request for request in requests if request.method == "PUT"][0]
        self.assertEqual(
            "/api/v1/courses/1/assignments/21/submissions/10", update.url.path
        )
        self.assertIn(b"submission%5Bposted_grade%5D=9", update.content)
        self.assertIn(b"comment%5Btext_comment%5D=", update.content)
        submission = [request for request in requests if request.method == "POST"][0]
        self.assertEqual(
            "/api/v1/courses/1/assignments/21/submissions", submission.url.path
        )
        self.assertEqual(
            2, submission.content.count(b"submission%5Bfile_ids%5D%5B%5D=")
        )

    def test_canvas_identifiers_and_write_fields_cannot_be_paths(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=[], request=request)

        with httpx.Client(
            base_url="https://oc.sjtu.edu.cn",
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = CanvasClient(client=http_client)
            with self.assertRaises(CanvasProtocolError):
                client.course_users("../users")
            with self.assertRaises(CanvasProtocolError):
                client.calendar_events(
                    ["https://evil.example"], "2026-09-01", "2026-10-01"
                )
            with self.assertRaises(CanvasProtocolError):
                client.update_user_submission(1, 2, 3, [("url", "https://evil.example")])
        self.assertEqual(0, calls)


class FakeAcademicService:
    def list_course_capabilities(self):
        return [{"course_id": "1", "roles": ["teacher"]}]

    def course_capabilities(self, course_id):
        return {"course_id": str(course_id), "can_manage_grades": True}

    def calendar_overview(self, year, month, **_kwargs):
        return {
            "month_start": f"{year:04d}-{month:02d}-01T00:00:00+08:00",
            "month_end": "2026-10-01T00:00:00+08:00",
            "month_events": (),
            "upcoming_start": "2026-09-29T00:00:00+08:00",
            "upcoming_end": "2026-10-06T00:00:00+08:00",
            "upcoming_events": (),
        }

    def gradebook(self, course_id):
        return {"course_id": str(course_id), "rows": []}

    def list_members(self, course_id, **_kwargs):
        return [{"id": 10, "course_id": str(course_id)}]

    def list_submissions(self, course_id, assignment_id):
        return [{"course_id": course_id, "assignment_id": assignment_id}]

    def submission_detail(self, course_id, assignment_id, student_id):
        return {"course_id": course_id, "assignment_id": assignment_id, "student_id": student_id}

    def update_submission(self, course_id, assignment_id, student_id, **options):
        return {
            "submission": {
                "course_id": course_id,
                "assignment_id": assignment_id,
                "student_id": student_id,
            },
            "grade_verified": options["set_grade"],
            "comment_verified": options["comment"] is not None,
            "recovered_after_uncertain_write": False,
        }


class FakeMediaService:
    limits = type("Limits", (), {"max_file_size": 1024})()

    def capabilities(self):
        return {"pdf_merge": {"available": False}}


class FakeUpdateService:
    def __init__(self) -> None:
        self.calls = 0

    def check(self):
        self.calls += 1
        return {
            "current_version": "1.2.1",
            "latest_version": "1.3.0",
            "update_available": True,
            "download_plan": {"asset_name": "safe.exe"},
        }


class DashboardBridgeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.update = FakeUpdateService()
        self.service = DashboardService(
            self.engine,
            archive_root=Path(self.temporary.name),
            academic_service=FakeAcademicService(),
            media_service=FakeMediaService(),
            update_service=self.update,
        )
        self.bridge = DesktopBridge(self.service)

    def tearDown(self) -> None:
        self.service.close()
        self.engine.dispose()
        self.temporary.cleanup()

    def test_new_services_are_wired_and_mcp_is_a_stdio_command(self) -> None:
        self.assertEqual("1", self.service.course_capabilities(1)["course_id"])
        self.assertEqual([], self.service.calendar(2026, 9, course_ids=[1])["month_events"])
        self.assertEqual("1", self.service.gradebook(1)["course_id"])
        self.assertEqual(10, self.service.roster(1)["items"][0]["id"])
        self.assertEqual(1, len(self.service.grading(1, 2)["items"]))
        self.assertTrue(self.service.grading_update(1, 2, 10, grade=9, set_grade=True)["grade_verified"])
        update = self.service.update_check()
        self.assertTrue(update["update_available"])
        self.assertFalse(update["automatic_install"])
        self.assertEqual(1, self.update.calls)
        mcp = self.service.mcp_config()
        self.assertEqual("stdio", mcp["transport"])
        self.assertIn("sjtu_learning_assistant.mcp_server", mcp["args"])

    def test_bridge_allowlist_rejects_paths_urls_and_unknown_fields(self) -> None:
        expected = {
            "capabilities",
            "calendar",
            "gradebook",
            "roster",
            "grading",
            "media",
            "video",
            "update_check",
            "mcp_config",
        }
        self.assertTrue(expected <= set(self.bridge._handlers))
        self.assertTrue(self.bridge.invoke("calendar", {"year": 2026, "month": 9, "course_ids": [1]})["ok"])
        self.assertTrue(self.bridge.invoke("update_check", {})["ok"])
        self.assertTrue(self.bridge.invoke("mcp_config", {})["ok"])
        for action, payload in (
            ("calendar", {"year": 2026, "month": 9, "url": "https://evil.example"}),
            ("roster", {"course_id": 1, "path": "/tmp/users.csv"}),
            ("update_check", {"url": "https://evil.example/release.json"}),
            ("mcp_config", {"command": "/tmp/server"}),
            ("video", {"source_id": "video-1", "path": "/tmp/video.mp4"}),
        ):
            with self.subTest(action=action):
                response = self.bridge.invoke(action, payload)
                self.assertFalse(response["ok"])
                self.assertEqual("operation_failed", response["error"]["code"])


if __name__ == "__main__":
    unittest.main()
