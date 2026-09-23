from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sjtu_learning_assistant.assignment_service import (
    AssignmentService,
    AssignmentServiceError,
    SubmissionOutcomeUncertain,
    SubmissionVerificationError,
)
from sjtu_learning_assistant.canvas_client import CanvasNetworkError


class FakeCanvas:
    def __init__(self, submission_type: str) -> None:
        self.assignment_data = {
            "id": 2,
            "submission_types": [submission_type],
            "html_url": "https://oc.sjtu.edu.cn/courses/1/assignments/2",
        }
        self.current = {"workflow_state": "unsubmitted"}
        self.posts = 0
        self.interrupt_after_post = False
        self.do_not_apply = False
        self.uploaded_paths: list[Path] = []

    def assignment(self, _course_id, _assignment_id):
        return self.assignment_data

    def submission(self, _course_id, _assignment_id):
        return self.current

    def submit_assignment(self, _course_id, _assignment_id, submission_type, **kwargs):
        self.posts += 1
        if not self.do_not_apply:
            self.current = {
                "id": 99,
                "workflow_state": "submitted",
                "submission_type": submission_type,
                "body": kwargs.get("body"),
                "url": kwargs.get("url"),
                "attachments": [{"id": item} for item in kwargs.get("file_ids") or []],
            }
        if self.interrupt_after_post:
            raise CanvasNetworkError("lost", operation_uncertain=True)
        return self.current

    def request_submission_file_upload(self, _course_id, _assignment_id, path):
        self.uploaded_paths.append(Path(path))
        return {"upload_url": "https://upload.example.test", "upload_params": {}}

    def upload_file(self, _upload, path):
        self.uploaded_paths.append(Path(path))
        return {"id": 44}


class AssignmentServiceTests(unittest.TestCase):
    def test_text_url_and_local_file_submission(self) -> None:
        text_canvas = FakeCanvas("online_text_entry")
        self.assertTrue(AssignmentService(text_canvas).submit_text(1, 2, "answer").verified)

        url_canvas = FakeCanvas("online_url")
        self.assertTrue(
            AssignmentService(url_canvas).submit_url(1, 2, "https://example.test/work").verified
        )

        file_canvas = FakeCanvas("online_upload")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "work.txt"
            path.write_text("answer")
            result = AssignmentService(file_canvas).submit_local_file(1, 2, path)
        self.assertTrue(result.verified)
        self.assertEqual({"44"}, {str(item["id"]) for item in result.submission["attachments"]})

    def test_external_type_does_not_post(self) -> None:
        canvas = FakeCanvas("external_tool")
        result = AssignmentService(canvas).submit_text(1, 2, "answer")
        self.assertTrue(result.requires_external_submission)
        self.assertEqual(0, canvas.posts)

    def test_verification_failure_is_reported(self) -> None:
        canvas = FakeCanvas("online_text_entry")
        canvas.do_not_apply = True
        with self.assertRaises(SubmissionVerificationError):
            AssignmentService(canvas).submit_text(1, 2, "answer")
        self.assertEqual(1, canvas.posts)

    def test_network_interruption_verifies_without_reposting(self) -> None:
        canvas = FakeCanvas("online_text_entry")
        canvas.interrupt_after_post = True
        result = AssignmentService(canvas).submit_text(1, 2, "answer")
        self.assertTrue(result.verified)
        self.assertEqual(1, canvas.posts)

        uncertain = FakeCanvas("online_text_entry")
        uncertain.interrupt_after_post = True
        uncertain.do_not_apply = True
        with self.assertRaises(SubmissionOutcomeUncertain):
            AssignmentService(uncertain).submit_text(1, 2, "answer")
        self.assertEqual(1, uncertain.posts)

    def test_cloud_temporary_file_is_always_removed(self) -> None:
        class Provider:
            def __init__(self, path: Path) -> None:
                self.path = path

            def download_temp(self, _remote_id: str) -> Path:
                return self.path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cloud.txt"
            path.write_text("cloud answer")
            canvas = FakeCanvas("online_upload")
            result = AssignmentService(canvas).submit_cloud_file(1, 2, Provider(path), "remote")
            self.assertTrue(result.verified)
            self.assertFalse(path.exists())

    def test_real_canvas_response_normalization_and_attachment_shapes(self) -> None:
        text = FakeCanvas("online_text_entry")
        text.current = {
            "workflow_state": "submitted",
            "submission_type": "online_text_entry",
            "body": "<p>Hello&nbsp; Canvas</p>",
        }
        self.assertIsNotNone(
            AssignmentService(text).verify(1, 2, "online_text_entry", body="Hello Canvas")
        )

        url = FakeCanvas("online_url")
        url.current = {
            "workflow_state": "graded",
            "submission_type": "online_url",
            "url": "HTTPS://Example.COM:443/work/",
        }
        self.assertIsNotNone(
            AssignmentService(url).verify(1, 2, "online_url", url="https://example.com/work")
        )

        upload = FakeCanvas("online_upload")
        upload.current = {
            "workflow_state": "pending_review",
            "submission_type": "online_upload",
            "attachment_ids": [44],
        }
        self.assertIsNotNone(
            AssignmentService(upload).verify(1, 2, "online_upload", file_ids=["44"])
        )
        self.assertIsNone(
            AssignmentService(upload).verify(1, 2, "online_upload", file_ids=[45])
        )

    def test_uploaded_file_id_shapes_and_invalid_values(self) -> None:
        service = AssignmentService(FakeCanvas("online_upload"))
        self.assertEqual(1, service._uploaded_file_id({"attachment": {"id": 1}}))
        self.assertEqual("2", service._uploaded_file_id({"file": {"id": "2"}}))
        self.assertEqual(3, service._uploaded_file_id({"attachments": [{"id": 3}]}))
        for payload in ({}, {"id": True}, {"id": ""}, {"attachments": [{"id": 1}, {"id": 2}]}):
            with self.assertRaises(AssignmentServiceError):
                service._uploaded_file_id(payload)

    def test_temporary_download_context_exits_on_failure(self) -> None:
        class Download:
            def __init__(self, path: Path) -> None:
                self.path = path
                self.exited = False
                self.exit_exception = None

            def __enter__(self) -> Path:
                return self.path

            def __exit__(self, exc_type, _exc, _traceback) -> None:
                self.exited = True
                self.exit_exception = exc_type
                self.path.unlink(missing_ok=True)

        class Provider:
            def __init__(self, download: Download) -> None:
                self.download = download

            def download_temp(self, _remote_id: str) -> Download:
                return self.download

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cloud.txt"
            path.write_text("cloud answer")
            download = Download(path)
            canvas = FakeCanvas("online_upload")
            canvas.do_not_apply = True
            with self.assertRaises(SubmissionVerificationError):
                AssignmentService(canvas).submit_cloud_file(
                    1, 2, Provider(download), "remote"
                )
            self.assertTrue(download.exited)
            self.assertIs(download.exit_exception, SubmissionVerificationError)
            self.assertFalse(path.exists())



if __name__ == "__main__":
    unittest.main()
