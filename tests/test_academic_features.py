from __future__ import annotations

import csv
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sjtu_learning_assistant.academic_features import (
    AcademicFeatureError,
    AcademicFeatureService,
    AcademicOutcomeUncertain,
    AcademicPermissionError,
    AcademicValidationError,
    AcademicVerificationError,
)
from sjtu_learning_assistant.canvas_client import CanvasError, CanvasNetworkError
from sjtu_learning_assistant.models import Base, Course


class FakeCanvas:
    def __init__(self, role: str = "TeacherEnrollment") -> None:
        self.role = role
        self.course_calls = 0
        self.calendar_calls: list[tuple[list[str], str, str]] = []
        self.update_calls: list[tuple[str, str, str, list[tuple[str, str]]]] = []
        self.raise_after_update = False
        self.apply_update = True
        self.members = [
            {
                "id": 10,
                "name": "Alice",
                "sortable_name": "Alice",
                "short_name": "Ali",
                "login_id": "student-a",
                "email": "alice@example.test",
                "created_at": "2026-01-01T00:00:00Z",
                "enrollments": [{"type": "StudentEnrollment"}],
            },
            {
                "id": 11,
                "name": "=Formula",
                "sortable_name": "Formula",
                "short_name": "F",
                "login_id": "student-b",
                "email": "formula@example.test",
                "created_at": "2026-01-02T00:00:00Z",
                "enrollments": [{"role": "StudentEnrollment"}],
            },
            {
                "id": 12,
                "name": "Tutor",
                "sortable_name": "Tutor",
                "short_name": "TA",
                "login_id": "ta-1",
                "email": "ta@example.test",
                "created_at": "2026-01-03T00:00:00Z",
                "enrollments": [{"role": "TaEnrollment"}],
            },
        ]
        self.assignment_rows = [
            {"id": 21, "name": "Homework, One", "points_possible": 10},
            {"id": 22, "name": "Homework Two", "points_possible": 20},
        ]
        self.grouped_submissions = [
            {
                "user_id": 10,
                "submissions": [
                    {"assignment_id": 21, "grade": "8", "score": 8},
                    {"assignment_id": 22, "grade": "18", "score": 18},
                ],
            },
            {
                "user_id": 11,
                "submissions": [
                    {"assignment_id": 21, "grade": "10", "score": 10},
                    {"assignment_id": 22, "grade": None, "score": None},
                ],
            },
        ]
        self.current_submission = {
            "id": 90,
            "user_id": 10,
            "assignment_id": 21,
            "workflow_state": "submitted",
            "grade": None,
            "score": None,
            "submission_comments": [{"id": 1, "comment": "existing"}],
        }

    def course(self, course_id):
        self.course_calls += 1
        return {
            "id": int(course_id),
            "enrollments": [{"role": self.role}],
        }

    def courses(self):
        return [self.course(1)]

    def calendar_events(self, context_codes, start_date, end_date):
        self.calendar_calls.append((list(context_codes), start_date, end_date))
        events = []
        for code in context_codes:
            course_id = int(code.split("_")[-1])
            events.append(
                {
                    "id": 1000 + course_id,
                    "title": f"Course {course_id}",
                    "context_code": code,
                    "start_at": "2026-09-15T08:00:00+00:00",
                    "assignment": {"id": course_id},
                }
            )
        if "course_1" in context_codes:
            events.extend(
                [
                    {
                        "id": 9998,
                        "title": "Duplicate assignment",
                        "context_code": "course_1",
                        "end_at": "2026-09-16T08:00:00Z",
                        "assignment": {"id": 1},
                    },
                    {
                        "id": 9999,
                        "title": "Outside range",
                        "context_code": "course_1",
                        "end_at": "2026-10-10T08:00:00Z",
                        "assignment": {"id": 9999},
                    },
                ]
            )
        return events

    def course_users(self, _course_id):
        return list(self.members)

    def assignments(self, _course_id):
        return list(self.assignment_rows)

    def user_submissions(self, _course_id, student_ids):
        selected = set(map(str, student_ids))
        return [
            item for item in self.grouped_submissions if str(item["user_id"]) in selected
        ]

    def assignment_submissions(self, _course_id, _assignment_id):
        return [dict(self.current_submission)]

    def submission_for_user(self, _course_id, _assignment_id, _student_id):
        result = dict(self.current_submission)
        result["submission_comments"] = [
            dict(item) for item in self.current_submission["submission_comments"]
        ]
        return result

    def update_user_submission(self, course_id, assignment_id, student_id, data):
        self.update_calls.append((course_id, assignment_id, student_id, list(data)))
        if self.apply_update:
            for key, value in data:
                if key == "submission[posted_grade]":
                    self.current_submission["grade"] = value or None
                    self.current_submission["score"] = float(value) if value else None
                if key == "comment[text_comment]":
                    comments = self.current_submission["submission_comments"]
                    comments.append({"id": len(comments) + 1, "comment": value})
        if self.raise_after_update:
            raise CanvasNetworkError("Bearer hidden-token", operation_uncertain=True)
        return dict(self.current_submission)


class AcademicFeatureServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.export_root = Path(self.temporary_directory.name) / "exports"
        self.canvas = FakeCanvas()
        self.service = AcademicFeatureService(
            self.canvas, export_root=self.export_root
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_course_roles_and_capabilities_for_teacher_ta_and_student(self) -> None:
        teacher = self.service.course_capabilities(1)
        self.assertEqual(("teacher",), teacher.roles)
        self.assertEqual("课程 1", teacher.course_name)
        self.assertEqual("canvas", teacher.role_source)
        self.assertTrue(teacher.can_manage_grades)
        self.assertTrue(teacher.can_view_submissions)

        ta = AcademicFeatureService(
            FakeCanvas("TaEnrollment"), export_root=self.export_root
        ).course_capabilities(1)
        self.assertEqual(("ta",), ta.roles)
        self.assertTrue(ta.can_comment_submissions)

        student = AcademicFeatureService(
            FakeCanvas("StudentEnrollment"), export_root=self.export_root
        ).course_capabilities(1)
        self.assertEqual(("student",), student.roles)
        self.assertTrue(student.can_view_calendar)
        self.assertFalse(student.can_manage_grades)

    def test_local_raw_data_can_describe_role_but_never_grants_staff_access(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session, session.begin():
            session.add(
                Course(
                    source_id="1",
                    name="Cached Course",
                    raw_data={"enrollments": [{"role": "TeacherEnrollment"}]},
                )
            )
        canvas = FakeCanvas("StudentEnrollment")
        service = AcademicFeatureService(
            canvas, engine=engine, export_root=self.export_root
        )
        capabilities = service.course_capabilities(1)
        self.assertEqual(("student",), capabilities.roles)
        with self.assertRaises(AcademicPermissionError):
            service.list_submissions(1, 21)
        engine.dispose()

    def test_local_raw_data_fallback_is_marked_non_authoritative(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session, session.begin():
            session.add(
                Course(
                    source_id="1",
                    name="Cached Course",
                    raw_data={"enrollments": [{"role": "TaEnrollment"}]},
                )
            )

        class OfflineCanvas:
            def course(self, _course_id):
                raise CanvasError("Bearer secret-token")

            def courses(self):
                raise CanvasError("token=secret-token")

        service = AcademicFeatureService(
            OfflineCanvas(), engine=engine, export_root=self.export_root
        )
        capabilities = service.course_capabilities(1)
        self.assertEqual(("ta",), capabilities.roles)
        self.assertEqual("local_cache", capabilities.role_source)
        self.assertFalse(capabilities.can_manage_grades)
        engine.dispose()

    def test_month_and_next_seven_days_are_batched_deduplicated_and_sorted(self) -> None:
        course_ids = list(range(1, 13))
        events = self.service.month_events(2026, 9, course_ids=course_ids)
        self.assertEqual(12, len(events))
        self.assertEqual(2, len(self.canvas.calendar_calls))
        self.assertEqual(10, len(self.canvas.calendar_calls[0][0]))
        self.assertEqual(2, len(self.canvas.calendar_calls[1][0]))
        self.assertEqual("Course 1", events[0]["title"])

        self.canvas.calendar_calls.clear()
        upcoming = self.service.upcoming_events(
            now=datetime(2026, 9, 14, tzinfo=timezone.utc),
            course_ids=[1, 2],
        )
        self.assertEqual([1, 2], [item["assignment"]["id"] for item in upcoming])
        start = datetime.fromisoformat(self.canvas.calendar_calls[0][1])
        end = datetime.fromisoformat(self.canvas.calendar_calls[0][2])
        self.assertEqual(7, (end - start).days)

    def test_calendar_overview_aggregates_month_and_upcoming(self) -> None:
        overview = self.service.calendar_overview(
            2026,
            9,
            now=datetime(2026, 9, 14, tzinfo=timezone.utc),
            course_ids=[1],
        )
        self.assertEqual(1, len(overview.month_events))
        # 31 天窗口保证近一周为空时，仍能展示最近的远期截止；
        # “Duplicate assignment”与 “Course 1” 属于同一作业，按 ID 去重。
        self.assertEqual(
            ["Course 1", "Outside range"],
            [item["title"] for item in overview.upcoming_events],
        )
        self.assertEqual("2026-09-01T00:00:00+00:00", overview.month_start)

    def test_members_filter_and_csv_export_are_safe(self) -> None:
        students = self.service.list_members(1, roles="student", query="example.test")
        self.assertEqual([10, 11], [item["id"] for item in students])
        self.assertEqual(["student"], students[0]["academic_roles"])

        artifact = self.service.export_members_csv(
            1, user_ids=[11], roles="student", filename="members"
        )
        self.assertEqual(self.export_root.resolve(), artifact.path.parent)
        self.assertEqual("members.csv", artifact.filename)
        self.assertEqual(2, artifact.row_count)
        with artifact.path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual("'=Formula", rows[1][1])
        self.assertEqual("student", rows[1][-1])
        self.assertFalse(any(path.suffix == ".tmp" for path in self.export_root.iterdir()))

        with self.assertRaises(AcademicValidationError):
            self.service.export_members_csv(1, filename="../escaped.csv")
        with self.assertRaises(AcademicValidationError):
            self.service.export_members_csv(1, filename=str(Path(os.sep) / "tmp" / "x.csv"))

    def test_student_cannot_export_member_private_data(self) -> None:
        student_service = AcademicFeatureService(
            FakeCanvas("StudentEnrollment"), export_root=self.export_root
        )
        with self.assertRaises(AcademicPermissionError):
            student_service.export_members_csv(1)

    def test_grade_statistics_and_atomic_csv_export(self) -> None:
        book = self.service.gradebook(1)
        statistics = book["statistics"]
        self.assertEqual(2, statistics["student_count"])
        self.assertEqual(2, statistics["assignment_count"])
        self.assertEqual(3, statistics["graded_count"])
        self.assertEqual(30.0, statistics["total_possible"])
        self.assertEqual(9, statistics["assignments"][0]["mean"])
        self.assertEqual([26.0, 10.0], statistics["student_totals"]["values"])

        target_existed_during_replace: list[bool] = []
        real_replace = os.replace

        def checking_replace(source, target):
            self.assertEqual(self.export_root.resolve(), Path(source).parent)
            self.assertEqual(self.export_root.resolve(), Path(target).parent.resolve())
            target_existed_during_replace.append(Path(target).exists())
            real_replace(source, target)

        with patch(
            "sjtu_learning_assistant.academic_features.os.replace",
            side_effect=checking_replace,
        ):
            artifact = self.service.export_grades_csv(1, filename="grades.csv")
        self.assertEqual([False], target_existed_during_replace)
        self.assertTrue(artifact.path.is_file())
        self.assertEqual(3, artifact.row_count)
        with artifact.path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual("Homework, One", rows[0][3])
        self.assertEqual("26.0", rows[1][-1])

    def test_staff_submission_list_and_detail_have_backend_role_gate(self) -> None:
        ta_canvas = FakeCanvas("TaEnrollment")
        service = AcademicFeatureService(ta_canvas, export_root=self.export_root)
        self.assertEqual(90, service.list_submissions(1, 21)[0]["id"])
        self.assertEqual(90, service.submission_detail(1, 21, 10)["id"])
        self.assertGreaterEqual(ta_canvas.course_calls, 2)

        student_service = AcademicFeatureService(
            FakeCanvas("StudentEnrollment"), export_root=self.export_root
        )
        with self.assertRaises(AcademicPermissionError):
            student_service.list_submissions(1, 21)
        with self.assertRaises(AcademicPermissionError):
            student_service.submission_detail(1, 21, 10)

    def test_grade_and_comment_write_once_then_read_after_write_verify(self) -> None:
        result = self.service.grade_and_comment_submission(1, 21, 10, "9.5", "Good")
        self.assertTrue(result.grade_verified)
        self.assertTrue(result.comment_verified)
        self.assertFalse(result.recovered_after_uncertain_write)
        self.assertEqual(1, len(self.canvas.update_calls))
        self.assertEqual(
            [
                ("submission[posted_grade]", "9.5"),
                ("comment[text_comment]", "Good"),
            ],
            self.canvas.update_calls[0][3],
        )
        self.assertEqual("9.5", result.submission["grade"])
        self.assertEqual("Good", result.submission["submission_comments"][-1]["comment"])

    def test_uncertain_write_is_verified_without_retry(self) -> None:
        self.canvas.raise_after_update = True
        result = self.service.comment_submission(1, 21, 10, "Applied once")
        self.assertTrue(result.recovered_after_uncertain_write)
        self.assertTrue(result.comment_verified)
        self.assertEqual(1, len(self.canvas.update_calls))

    def test_uncertain_and_normal_verification_failures_are_distinct(self) -> None:
        self.canvas.apply_update = False
        with self.assertRaises(AcademicVerificationError):
            self.service.grade_submission(1, 21, 10, 8)
        self.assertEqual(1, len(self.canvas.update_calls))

        uncertain_canvas = FakeCanvas()
        uncertain_canvas.apply_update = False
        uncertain_canvas.raise_after_update = True
        uncertain_service = AcademicFeatureService(
            uncertain_canvas, export_root=self.export_root
        )
        with self.assertRaises(AcademicOutcomeUncertain):
            uncertain_service.comment_submission(1, 21, 10, "not applied")
        self.assertEqual(1, len(uncertain_canvas.update_calls))

    def test_comment_verification_requires_a_new_comment(self) -> None:
        self.canvas.current_submission["submission_comments"].append(
            {"id": 2, "comment": "duplicate"}
        )
        self.canvas.apply_update = False
        with self.assertRaises(AcademicVerificationError):
            self.service.comment_submission(1, 21, 10, "duplicate")

    def test_invalid_grade_comment_and_ids_are_rejected_before_write(self) -> None:
        for invalid_grade in (-1, float("inf"), "NaN"):
            with self.assertRaises(AcademicValidationError):
                self.service.grade_submission(1, 21, 10, invalid_grade)
        with self.assertRaises(AcademicValidationError):
            self.service.comment_submission(1, 21, 10, "   ")
        with self.assertRaises(AcademicValidationError):
            self.service.submission_detail(1, 21, "../10")
        self.assertEqual([], self.canvas.update_calls)

    def test_canvas_errors_are_redacted(self) -> None:
        class FailingCanvas(FakeCanvas):
            def course_users(self, _course_id):
                raise CanvasError(
                    "Authorization: Bearer super-secret token=second-secret\ninternal"
                )

        service = AcademicFeatureService(FailingCanvas(), export_root=self.export_root)
        with self.assertRaises(AcademicFeatureError) as context:
            service.list_members(1)
        message = str(context.exception)
        self.assertNotIn("super-secret", message)
        self.assertNotIn("second-secret", message)
        self.assertNotIn("\n", message)
        self.assertIn("[REDACTED]", message)


if __name__ == "__main__":
    unittest.main()
