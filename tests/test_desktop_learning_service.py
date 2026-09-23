from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sjtu_learning_assistant.assignment_service import SubmissionResult
from sjtu_learning_assistant.desktop_learning_service import (
    DesktopLearningService,
    LearningServiceError,
    load_canvas_token,
)


class FakeCanvas:
    def courses(self):
        return [{"id": 1, "name": "人机交互"}]

    def assignments(self, _course_id):
        return [
            {
                "id": 2,
                "name": "项目报告",
                "due_at": "2026-09-23T12:00:00+08:00",
                "submission_types": ["online_upload"],
                "submission": {"workflow_state": "unsubmitted", "missing": True, "late": False},
            },
            {
                "id": 3,
                "name": "已评分作业",
                "due_at": "2026-09-20T12:00:00+08:00",
                "submission_types": ["external_tool"],
                "submission": {"id": 7, "workflow_state": "graded", "missing": False, "late": True},
            },
        ]

    def assignment(self, _course_id, assignment_id):
        return next(item for item in self.assignments(1) if item["id"] == assignment_id)

    def course(self, _course_id):
        return {"id": 1, "name": "人机交互"}

    def close(self):
        pass


class FakeAssignmentService:
    def __init__(self):
        self.canvas = FakeCanvas()
        self.cloud_call = None

    def detail(self, course_id, assignment_id):
        return self.canvas.assignment(course_id, assignment_id)

    def can_submit(self, _course_id, assignment_id, submission_type=None):
        return assignment_id == 2 and submission_type in {None, "online_upload"}

    def submit_text(self, *_args):
        return SubmissionResult("verified", "online_text_entry", {
            "id": 99, "workflow_state": "submitted", "submitted_at": "2026-09-23T02:00:00Z", "attempt": 2,
        })

    def submit_url(self, *_args):
        return self.submit_text()

    def submit_local_file(self, *_args):
        return self.submit_text()

    def submit_cloud_file(self, course_id, assignment_id, provider, remote_path):
        self.cloud_call = (course_id, assignment_id, provider, remote_path)
        return SubmissionResult("verified", "online_upload", {
            "id": 100, "workflow_state": "submitted", "attempt": 1,
            "attachments": [{"id": 4, "filename": "report.pdf"}],
        })

    def open_external(self, *_args):
        return SubmissionResult("requires_external_submission", "external_tool", message="已打开")


class DesktopLearningServiceTests(unittest.TestCase):
    def setUp(self):
        self.assignment_service = FakeAssignmentService()
        self.pan = Mock()
        self.service = DesktopLearningService(
            assignment_service=self.assignment_service,
            pan_provider=self.pan,
            now=lambda: datetime(2026, 9, 23, 2, 23, tzinfo=timezone.utc),
        )

    def test_categories_use_submission_state_and_dates(self):
        today = self.service.assignments_list("today")["items"]
        self.assertEqual(["项目报告"], [item["name"] for item in today])
        missing = self.service.assignments_list("missing")["items"]
        self.assertEqual(["项目报告"], [item["name"] for item in missing])
        graded = self.service.assignments_list("graded")["items"]
        self.assertEqual(["已评分作业"], [item["name"] for item in graded])
        self.assertTrue(graded[0]["requires_external_submission"])

    def test_verified_result_is_server_derived(self):
        result = self.service.submit_text(1, 2, "answer")
        self.assertEqual({"verified": True, "submission_id": 99, "attempt": 2}, {
            key: result[key] for key in ("verified", "submission_id", "attempt")
        })

    def test_cloud_submit_passes_provider_and_remote_path(self):
        result = self.service.submit_cloud_file(1, 2, "课程/report.pdf")
        self.assertTrue(result["verified"])
        self.assertEqual("report.pdf", result["attachments"][0]["name"])
        self.assertEqual((1, 2, self.pan, "课程/report.pdf"), self.assignment_service.cloud_call)

    def test_pan_pagination_shape(self):
        item = SimpleNamespace(path=("课程", "a.pdf"), name="a.pdf", is_directory=False, size=12, modified_at="now")
        self.pan.list_directory.return_value = SimpleNamespace(
            path=("课程",), page=2, page_size=20, total=45, has_more=True, items=(item,)
        )
        result = self.service.pan_list("课程", 2, 20)
        self.assertTrue(result["has_more"])
        self.assertEqual("课程/a.pdf", result["items"][0]["remote_path"])

    def test_service_is_lazy_without_credentials_and_closes_owned_clients(self):
        token_loader = Mock(side_effect=LearningServiceError("尚未配置"))
        lazy_service = DesktopLearningService(token_loader=token_loader)
        token_loader.assert_not_called()
        lazy_service.close()
        token_loader.assert_not_called()

        canvas = Mock()
        canvas.courses.return_value = []
        pan = Mock()
        pan.list_directory.return_value = SimpleNamespace(
            path=(), page=1, page_size=20, total=0, has_more=False, items=()
        )
        with (
            patch(
                "sjtu_learning_assistant.desktop_learning_service.CanvasClient",
                return_value=canvas,
            ),
            patch(
                "sjtu_learning_assistant.desktop_learning_service.SJTUCloudPanProvider",
                return_value=pan,
            ),
        ):
            owned = DesktopLearningService(token_loader=lambda: "test-token")
            owned.assignments_list("today")
            owned.pan_list("", 1, 20)
            owned.close()
        canvas.close.assert_called_once_with()
        pan.close.assert_called_once_with()

    def test_injected_clients_are_not_closed_by_service(self):
        canvas = Mock()
        pan = Mock()
        service = DesktopLearningService(canvas=canvas, pan_provider=pan)
        service.close()
        canvas.close.assert_not_called()
        pan.close.assert_not_called()

    def test_missing_canvas_credential_is_safe(self):
        keyring = Mock()
        keyring.get_password.return_value = None
        with patch("sjtu_learning_assistant.desktop_learning_service.load_keyring_module", return_value=(keyring, Exception)):
            with self.assertRaisesRegex(LearningServiceError, "尚未"):
                load_canvas_token()


if __name__ == "__main__":
    unittest.main()
