"""Desktop-only Canvas assignment and SJTU Pan integration.

Credentials are loaded lazily from macOS Keychain. They are never logged, returned to
JavaScript, or included in exceptions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.assignment_service import AssignmentService, SubmissionResult
from sjtu_learning_assistant.canvas_client import CanvasClient, DEFAULT_BASE_URL
from sjtu_learning_assistant.cloud_storage import SJTUCloudPanProvider
from sjtu_learning_assistant.canvas_sync import CanvasCheckError, get_saved_token
from sjtu_learning_assistant.models import Assignment, Course


class LearningServiceError(RuntimeError):
    """A safe, user-facing desktop learning integration error."""


def load_canvas_token() -> str:
    """Read the cached Canvas credential without interactive CLI fallback."""
    try:
        token = get_saved_token()
    except CanvasCheckError:
        raise LearningServiceError("无法从 macOS Keychain 读取 Canvas Access Token。") from None
    if token is None:
        raise LearningServiceError("尚未在 macOS Keychain 中配置 Canvas Access Token。")
    return token


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _submission_summary(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    attachments = value.get("attachments")
    safe_attachments = []
    if isinstance(attachments, list):
        safe_attachments = [
            {"id": item.get("id"), "name": str(item.get("filename") or item.get("display_name") or item.get("name") or "")}
            for item in attachments
            if isinstance(item, Mapping)
        ]
    return {
        "id": value.get("id"),
        "workflow_state": str(value.get("workflow_state") or "unsubmitted"),
        "submission_type": value.get("submission_type"),
        "submitted_at": value.get("submitted_at"),
        "attempt": value.get("attempt"),
        "missing": bool(value.get("missing")),
        "late": bool(value.get("late")),
        "score": value.get("score"),
        "grade": value.get("grade"),
        "attachments": safe_attachments,
    }


def _categories(assignment: Mapping[str, Any], now: datetime) -> list[str]:
    submission = assignment.get("submission")
    submission = submission if isinstance(submission, Mapping) else {}
    state = str(submission.get("workflow_state") or "unsubmitted")
    missing = bool(submission.get("missing"))
    late = bool(submission.get("late"))
    due = _parse_datetime(assignment.get("due_at"))
    local_now = now.astimezone()
    result: list[str] = []
    if due is not None:
        local_due = due.astimezone(local_now.tzinfo)
        if local_due.date() == local_now.date():
            result.append("today")
        elif local_due > local_now:
            result.append("upcoming")
        elif state not in {"submitted", "pending_review", "graded"}:
            result.append("overdue")
    if late and "overdue" not in result:
        result.append("overdue")
    if missing:
        result.append("missing")
    if state == "unsubmitted" and not missing:
        result.append("unsubmitted")
    if state == "submitted":
        result.append("submitted")
    if state == "pending_review":
        result.append("pending_review")
    if state == "graded":
        result.append("graded")
    return result


class DesktopLearningService:
    """Owns lazily created clients used by the desktop assignment UI."""

    def __init__(
        self,
        engine: Engine | None = None,
        *,
        token_loader: Callable[[], str] = load_canvas_token,
        canvas: CanvasClient | None = None,
        assignment_service: AssignmentService | None = None,
        pan_provider: SJTUCloudPanProvider | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._token_loader = token_loader
        self._canvas = canvas
        self._assignments = assignment_service
        self._pan = pan_provider
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._owns_canvas = canvas is None
        self._owns_pan = pan_provider is None
        self._courses: dict[str, str] = {}

    def _assignment_service(self) -> AssignmentService:
        if self._assignments is None:
            if self._canvas is None:
                self._canvas = CanvasClient(DEFAULT_BASE_URL, self._token_loader())
            self._assignments = AssignmentService(self._canvas, engine=self._engine)
        return self._assignments

    def _pan_provider(self) -> SJTUCloudPanProvider:
        if self._pan is None:
            # The provider reads its UserToken lazily through its existing Keychain helper.
            self._pan = SJTUCloudPanProvider()
        return self._pan

    @staticmethod
    def _native_types(assignment: Mapping[str, Any]) -> list[str]:
        values = assignment.get("submission_types")
        return [str(value) for value in values] if isinstance(values, list) else []

    def _assignment_data(self, course_id: object, course_name: str, assignment: Mapping[str, Any]) -> dict[str, Any]:
        types = self._native_types(assignment)
        can_submit = (
            not bool(assignment.get("locked_for_user"))
            and assignment.get("workflow_state") != "unpublished"
            and bool(set(types) & {"online_upload", "online_text_entry", "online_url"})
        )
        return {
            "course_id": str(course_id),
            "course_name": course_name,
            "id": str(assignment.get("id") or ""),
            "name": str(assignment.get("name") or "（无标题作业）"),
            "description": str(assignment.get("description") or ""),
            "due_at": assignment.get("due_at"),
            "unlock_at": assignment.get("unlock_at"),
            "lock_at": assignment.get("lock_at"),
            "points_possible": assignment.get("points_possible"),
            "html_url": assignment.get("html_url"),
            "submission_types": types,
            "submission": _submission_summary(assignment.get("submission")),
            "can_submit": can_submit,
            "requires_external_submission": not bool(set(types) & {"online_upload", "online_text_entry", "online_url"}),
            "categories": _categories(assignment, self._now()),
        }

    def _local_assignment_data(
        self, assignment: Assignment, course: Course
    ) -> dict[str, Any]:
        raw = dict(assignment.raw_data or {})
        raw["id"] = assignment.source_id
        raw.setdefault("name", assignment.name)
        raw.setdefault(
            "due_at", assignment.due_at.isoformat() if assignment.due_at else None
        )
        raw.setdefault("points_possible", assignment.points_possible)
        raw.setdefault("html_url", assignment.url)
        if not isinstance(raw.get("submission"), Mapping):
            raw["submission"] = {
                "workflow_state": assignment.submission_state or "unsubmitted",
                "missing": False,
                "late": False,
            }
        return self._assignment_data(course.source_id, course.name, raw)

    def _local_assignments(self, category: str) -> dict[str, Any]:
        assert self._engine is not None
        with Session(self._engine) as session:
            rows = session.execute(
                select(Assignment, Course)
                .join(Course, Course.id == Assignment.course_id)
                .where(Assignment.is_active.is_(True))
            ).all()
            items = [
                self._local_assignment_data(assignment, course)
                for assignment, course in rows
            ]
        items = [item for item in items if category in item["categories"]]
        items.sort(
            key=lambda item: (
                item.get("due_at") is None,
                item.get("due_at") or "",
                item["course_name"],
                item["name"],
            )
        )
        return {"category": category, "items": items}

    def assignments_list(self, category: str) -> dict[str, Any]:
        if self._engine is not None:
            return self._local_assignments(category)
        service = self._assignment_service()
        courses = service.canvas.courses()
        items: list[dict[str, Any]] = []
        for course in courses:
            course_id = course.get("id")
            if course_id is None:
                continue
            course_name = str(course.get("name") or course.get("course_code") or "（未命名课程）")
            self._courses[str(course_id)] = course_name
            for assignment in service.canvas.assignments(course_id):
                item = self._assignment_data(course_id, course_name, assignment)
                if category in item["categories"]:
                    items.append(item)
        items.sort(key=lambda item: (item.get("due_at") is None, item.get("due_at") or "", item["course_name"], item["name"]))
        return {"category": category, "items": items}

    def assignment_detail(self, course_id: int, assignment_id: int) -> dict[str, Any]:
        if self._engine is not None:
            with Session(self._engine) as session:
                row = session.execute(
                    select(Assignment, Course)
                    .join(Course, Course.id == Assignment.course_id)
                    .where(
                        Course.source_id == str(course_id),
                        Assignment.source_id == str(assignment_id),
                        Assignment.is_active.is_(True),
                    )
                ).first()
                if row is None:
                    raise LearningServiceError("未找到已同步的作业。")
                return self._local_assignment_data(*row)
        raw = self._assignment_service().detail(course_id, assignment_id)
        course_name = self._courses.get(str(course_id))
        if course_name is None:
            course = self._assignment_service().canvas.course(course_id)
            course_name = str(course.get("name") or course.get("course_code") or "（未命名课程）")
            self._courses[str(course_id)] = course_name
        return self._assignment_data(course_id, course_name, raw)

    def can_submit(self, course_id: int, assignment_id: int, submission_type: str | None = None) -> dict[str, Any]:
        detail = self.assignment_detail(course_id, assignment_id)
        allowed = self._assignment_service().can_submit(course_id, assignment_id, submission_type)
        return {"can_submit": allowed, "requires_external_submission": detail["requires_external_submission"], "submission_types": detail["submission_types"]}

    @staticmethod
    def _result(result: SubmissionResult) -> dict[str, Any]:
        submission = _submission_summary(result.submission)
        return {
            "verified": result.verified,
            "status": result.status,
            "message": result.message,
            "submission_type": result.submission_type,
            "submission_id": submission.get("id") if submission else None,
            "submitted_at": submission.get("submitted_at") if submission else None,
            "attempt": submission.get("attempt") if submission else None,
            "attachments": submission.get("attachments", []) if submission else [],
            "workflow_state": submission.get("workflow_state") if submission else None,
        }

    def submit_text(self, course_id: int, assignment_id: int, text: str) -> dict[str, Any]:
        return self._result(self._assignment_service().submit_text(course_id, assignment_id, text))

    def submit_url(self, course_id: int, assignment_id: int, url: str) -> dict[str, Any]:
        return self._result(self._assignment_service().submit_url(course_id, assignment_id, url))

    def submit_local_file(self, course_id: int, assignment_id: int, path: str) -> dict[str, Any]:
        return self._result(self._assignment_service().submit_local_file(course_id, assignment_id, path))

    def pan_list(self, remote_path: str, page: int, page_size: int) -> dict[str, Any]:
        result = self._pan_provider().list_directory(remote_path, page=page, page_size=page_size)
        return {
            "remote_path": "/".join(result.path),
            "page": result.page,
            "page_size": result.page_size,
            "total": result.total,
            "has_more": result.has_more,
            "items": [
                {
                    "remote_path": "/".join(item.path),
                    "name": item.name,
                    "is_directory": item.is_directory,
                    "size": item.size,
                    "modified_at": item.modified_at,
                }
                for item in result.items
            ],
        }

    def submit_cloud_file(self, course_id: int, assignment_id: int, remote_path: str) -> dict[str, Any]:
        result = self._assignment_service().submit_cloud_file(
            course_id, assignment_id, self._pan_provider(), remote_path
        )
        return self._result(result)

    def open_external_assignment(self, course_id: int, assignment_id: int) -> dict[str, Any]:
        return self._result(self._assignment_service().open_external(course_id, assignment_id))

    def close(self) -> None:
        if self._canvas is not None and self._owns_canvas:
            self._canvas.close()
        if self._pan is not None and self._owns_pan:
            self._pan.close()
