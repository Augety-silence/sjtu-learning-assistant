"""High-integrity Canvas assignment workflows.

Submission POSTs are intentionally never retried. If transport failure makes a POST
outcome uncertain, the service performs a read-after-write verification and either
accepts the observed submission or reports an uncertain result to the caller.
"""
from __future__ import annotations

import html
import re
import sys
import webbrowser
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import Engine

from sjtu_learning_assistant.canvas_client import CanvasClient, CanvasError, CanvasNetworkError
from sjtu_learning_assistant.repository import record_submission

NATIVE_TYPES = frozenset({"online_upload", "online_text_entry", "online_url"})
_SUBMITTED_STATES = frozenset({"submitted", "pending_review", "graded"})
_WHITESPACE = re.compile(r"\s+")


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _normalized_body(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parser = _VisibleTextParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        visible = value
    else:
        visible = " ".join(parser.parts)
    return _WHITESPACE.sub(" ", html.unescape(visible)).strip()


def _normalized_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        if not parsed.scheme or not host:
            return value.strip()
        port = parsed.port
    except ValueError:
        return value.strip()
    default_port = (parsed.scheme.lower() == "https" and port == 443) or (
        parsed.scheme.lower() == "http" and port == 80
    )
    netloc = host.lower()
    if parsed.username or parsed.password:
        return value.strip()
    if port and not default_port:
        netloc = f"{netloc}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, parsed.fragment))


class AssignmentServiceError(RuntimeError):
    pass


class SubmissionVerificationError(AssignmentServiceError):
    pass


class SubmissionOutcomeUncertain(AssignmentServiceError):
    """A submission POST may have succeeded but its result could not be verified."""


class TemporaryFileProvider(Protocol):
    def download_temp(self, remote_id: Any) -> Any: ...


@dataclass(frozen=True)
class SubmissionResult:
    status: str
    submission_type: str | None
    submission: dict[str, Any] | None = None
    assignment: dict[str, Any] | None = None
    message: str | None = None

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    @property
    def requires_external_submission(self) -> bool:
        return self.status == "requires_external_submission"

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


class AssignmentService:
    def __init__(self, canvas: CanvasClient, *, engine: Engine | None = None) -> None:
        self.canvas = canvas
        self.engine = engine

    def pending(self, course_id: int | str | None = None) -> list[dict[str, Any]]:
        courses = [{"id": course_id}] if course_id is not None else self.canvas.courses()
        pending: list[dict[str, Any]] = []
        for course in courses:
            current_course_id = course.get("id")
            if current_course_id is None:
                continue
            for assignment in self.canvas.assignments(current_course_id):
                submission = assignment.get("submission")
                state = submission.get("workflow_state") if isinstance(submission, dict) else None
                if state not in _SUBMITTED_STATES:
                    pending.append(assignment)
        return pending

    list_pending = pending

    def detail(self, course_id: int | str, assignment_id: int | str) -> dict[str, Any]:
        return self.canvas.assignment(course_id, assignment_id)

    get_detail = detail

    def status(self, course_id: int | str, assignment_id: int | str) -> dict[str, Any]:
        return self.canvas.submission(course_id, assignment_id)

    get_status = status

    @staticmethod
    def _submission_types(assignment: Mapping[str, Any]) -> tuple[str, ...]:
        values = assignment.get("submission_types") or ()
        return tuple(str(item) for item in values) if isinstance(values, (list, tuple)) else ()

    def can_submit(
        self,
        course_id: int | str,
        assignment_id: int | str,
        submission_type: str | None = None,
    ) -> bool:
        assignment = self.detail(course_id, assignment_id)
        if assignment.get("locked_for_user") or assignment.get("workflow_state") == "unpublished":
            return False
        available = self._submission_types(assignment)
        if not set(available) & NATIVE_TYPES:
            return False
        return submission_type in available if submission_type else True

    def _prepare(
        self, course_id: int | str, assignment_id: int | str, requested_type: str
    ) -> tuple[dict[str, Any], SubmissionResult | None]:
        assignment = self.detail(course_id, assignment_id)
        available = self._submission_types(assignment)
        if requested_type not in NATIVE_TYPES or requested_type not in available:
            return assignment, SubmissionResult(
                status="requires_external_submission",
                submission_type=requested_type,
                assignment=assignment,
                message="该作业类型需要在 Canvas 页面中完成提交。",
            )
        if assignment.get("locked_for_user") or assignment.get("workflow_state") == "unpublished":
            raise AssignmentServiceError("该作业当前不可提交。")
        return assignment, None

    @staticmethod
    def _attachment_ids(submission: Mapping[str, Any]) -> set[str]:
        identifiers: set[str] = set()
        for key in ("attachments", "media_object_attachments"):
            attachments = submission.get(key) or ()
            if isinstance(attachments, (list, tuple)):
                identifiers.update(
                    str(item["id"])
                    for item in attachments
                    if isinstance(item, Mapping)
                    and item.get("id") is not None
                    and not isinstance(item.get("id"), bool)
                )
        for key in ("attachment_ids", "file_ids"):
            values = submission.get(key) or ()
            if isinstance(values, (list, tuple)):
                identifiers.update(
                    str(value) for value in values if value is not None and not isinstance(value, bool)
                )
        return identifiers

    def verify(
        self,
        course_id: int | str,
        assignment_id: int | str,
        submission_type: str,
        *,
        body: str | None = None,
        url: str | None = None,
        file_ids: list[int | str] | tuple[int | str, ...] | None = None,
    ) -> dict[str, Any] | None:
        """Return the matching server submission, or ``None`` on any mismatch."""
        submission = self.status(course_id, assignment_id)
        if submission.get("workflow_state") not in _SUBMITTED_STATES:
            return None
        observed_type = submission.get("submission_type")
        if observed_type != submission_type:
            return None
        if (
            submission_type == "online_text_entry"
            and _normalized_body(submission.get("body")) != _normalized_body(body)
        ):
            return None
        if (
            submission_type == "online_url"
            and _normalized_url(submission.get("url")) != _normalized_url(url)
        ):
            return None
        if submission_type == "online_upload":
            expected = {str(item) for item in file_ids or ()}
            if not expected or not expected.issubset(self._attachment_ids(submission)):
                return None
        return submission

    verify_submission = verify

    def _audit(
        self,
        course_id: int | str,
        assignment_id: int | str,
        submission_type: str,
        status: str,
        *,
        submission: Mapping[str, Any] | None = None,
        local_filename: str | None = None,
        cloud_file_id: int | None = None,
        error: str | None = None,
    ) -> None:
        if self.engine is None:
            return
        record_submission(
            self.engine,
            canvas_course_id=course_id,
            canvas_assignment_id=assignment_id,
            submission_type=submission_type,
            status=status,
            canvas_submission_id=submission.get("id") if submission else None,
            cloud_file_id=cloud_file_id,
            local_filename=local_filename,
            raw_data=dict(submission or {}),
            error=error,
        )

    def _submit_and_verify(
        self,
        course_id: int | str,
        assignment_id: int | str,
        assignment: dict[str, Any],
        submission_type: str,
        *,
        body: str | None = None,
        url: str | None = None,
        file_ids: list[int | str] | None = None,
        local_filename: str | None = None,
        cloud_file_id: int | None = None,
    ) -> SubmissionResult:
        try:
            self.canvas.submit_assignment(
                course_id,
                assignment_id,
                submission_type,
                body=body,
                url=url,
                file_ids=file_ids,
            )
        except CanvasNetworkError as exc:
            if not exc.operation_uncertain:
                self._audit(
                    course_id, assignment_id, submission_type, "failed",
                    local_filename=local_filename, cloud_file_id=cloud_file_id, error=str(exc),
                )
                raise
            try:
                observed = self.verify(
                    course_id, assignment_id, submission_type, body=body, url=url, file_ids=file_ids
                )
            except CanvasError as verify_exc:
                self._audit(
                    course_id, assignment_id, submission_type, "failed",
                    local_filename=local_filename, cloud_file_id=cloud_file_id,
                    error="提交结果不确定且验证失败",
                )
                raise SubmissionOutcomeUncertain(
                    "提交请求后网络中断，且无法验证服务器结果；为避免重复提交，未重试。"
                ) from verify_exc
            if observed is None:
                self._audit(
                    course_id, assignment_id, submission_type, "failed",
                    local_filename=local_filename, cloud_file_id=cloud_file_id,
                    error="提交结果不确定且服务器状态不匹配",
                )
                raise SubmissionOutcomeUncertain(
                    "提交请求后网络中断，服务器状态未能确认；为避免重复提交，未重试。"
                ) from exc
            self._audit(
                course_id, assignment_id, submission_type, "verified",
                submission=observed, local_filename=local_filename, cloud_file_id=cloud_file_id,
            )
            return SubmissionResult("verified", submission_type, observed, assignment)

        observed = self.verify(
            course_id, assignment_id, submission_type, body=body, url=url, file_ids=file_ids
        )
        if observed is None:
            self._audit(
                course_id, assignment_id, submission_type, "failed",
                local_filename=local_filename, cloud_file_id=cloud_file_id,
                error="Canvas POST 成功但 GET self 验证不匹配",
            )
            raise SubmissionVerificationError("Canvas 返回提交成功，但 GET self 严格验证失败。")
        self._audit(
            course_id, assignment_id, submission_type, "verified",
            submission=observed, local_filename=local_filename, cloud_file_id=cloud_file_id,
        )
        return SubmissionResult("verified", submission_type, observed, assignment)

    def submit_text(
        self, course_id: int | str, assignment_id: int | str, text: str
    ) -> SubmissionResult:
        assignment, external = self._prepare(course_id, assignment_id, "online_text_entry")
        if external:
            return external
        return self._submit_and_verify(
            course_id, assignment_id, assignment, "online_text_entry", body=text
        )

    text = submit_text

    def submit_url(
        self, course_id: int | str, assignment_id: int | str, url: str
    ) -> SubmissionResult:
        assignment, external = self._prepare(course_id, assignment_id, "online_url")
        if external:
            return external
        return self._submit_and_verify(
            course_id, assignment_id, assignment, "online_url", url=url
        )

    url = submit_url

    @staticmethod
    def _uploaded_file_id(uploaded: Mapping[str, Any]) -> int | str:
        candidates: list[Any] = [uploaded]
        for key in ("attachment", "file"):
            nested = uploaded.get(key)
            if isinstance(nested, Mapping):
                candidates.append(nested)
        attachments = uploaded.get("attachments")
        if isinstance(attachments, (list, tuple)) and len(attachments) == 1:
            candidates.append(attachments[0])
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            file_id = candidate.get("id")
            if file_id is None or isinstance(file_id, bool):
                continue
            if isinstance(file_id, str) and not file_id.strip():
                continue
            return file_id
        raise AssignmentServiceError("Canvas 文件上传响应缺少有效文件 ID。")

    def submit_local_file(
        self, course_id: int | str, assignment_id: int | str, file_path: str | Path
    ) -> SubmissionResult:
        assignment, external = self._prepare(course_id, assignment_id, "online_upload")
        if external:
            return external
        path = Path(file_path)
        upload = self.canvas.request_submission_file_upload(course_id, assignment_id, path)
        uploaded = self.canvas.upload_file(upload, path)
        file_id = self._uploaded_file_id(uploaded)
        return self._submit_and_verify(
            course_id,
            assignment_id,
            assignment,
            "online_upload",
            file_ids=[file_id],
            local_filename=path.name,
        )

    local_file = submit_local_file

    def submit_cloud_file(
        self,
        course_id: int | str,
        assignment_id: int | str,
        provider: TemporaryFileProvider,
        remote_id: str,
        *,
        cloud_file_id: int | None = None,
    ) -> SubmissionResult:
        assignment, external = self._prepare(course_id, assignment_id, "online_upload")
        if external:
            return external
        temporary: Path | None = None
        download_owner: Any = None
        owner_entered = False
        try:
            download_owner = provider.download_temp(remote_id)
            if isinstance(download_owner, (str, Path)):
                temporary = Path(download_owner)
            elif hasattr(download_owner, "__enter__"):
                temporary = Path(download_owner.__enter__())
                owner_entered = True
            elif hasattr(download_owner, "path"):
                temporary = Path(download_owner.path)
            else:
                temporary = Path(download_owner)
            if not temporary.is_file():
                raise AssignmentServiceError("云盘 Provider 未生成可上传的临时文件。")
            upload = self.canvas.request_submission_file_upload(course_id, assignment_id, temporary)
            uploaded = self.canvas.upload_file(upload, temporary)
            file_id = self._uploaded_file_id(uploaded)
            return self._submit_and_verify(
                course_id,
                assignment_id,
                assignment,
                "online_upload",
                file_ids=[file_id],
                local_filename=temporary.name,
                cloud_file_id=cloud_file_id,
            )
        finally:
            if owner_entered and hasattr(download_owner, "__exit__"):
                try:
                    download_owner.__exit__(*sys.exc_info())
                except OSError:
                    pass
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # Cleanup is best-effort only after submission outcome has been preserved.
                    pass

    cloud_file = submit_cloud_file

    def open_external(self, course_id: int | str, assignment_id: int | str) -> SubmissionResult:
        assignment = self.detail(course_id, assignment_id)
        target = assignment.get("html_url")
        if not target:
            raise AssignmentServiceError("Canvas 作业没有可打开的外部页面。")
        webbrowser.open(str(target))
        available = self._submission_types(assignment)
        external_type = next((item for item in available if item not in NATIVE_TYPES), None)
        return SubmissionResult(
            "requires_external_submission", external_type, assignment=assignment,
            message="已打开 Canvas 作业页面。",
        )
