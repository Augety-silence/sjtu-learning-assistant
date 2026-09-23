"""Authenticated, pagination-safe Canvas REST client.

The client deliberately separates Canvas API requests from Canvas' pre-signed file
upload requests: bearer credentials are only ever sent to the configured origin.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

import httpx

DEFAULT_BASE_URL = "https://oc.sjtu.edu.cn"
DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 100


class CanvasError(RuntimeError):
    """Base error for Canvas operations."""


class CanvasNetworkError(CanvasError):
    """A transport failure; ``operation_uncertain`` means the server may have acted."""

    def __init__(self, message: str, *, operation_uncertain: bool = False) -> None:
        super().__init__(message)
        self.operation_uncertain = operation_uncertain


class CanvasProtocolError(CanvasError):
    """Canvas returned an invalid or unsafe response."""


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise CanvasProtocolError("Canvas base URL 必须是有效的 HTTPS 站点地址。")
    return value


class CanvasClient:
    """Small synchronous Canvas API client suitable for dependency injection.

    ``client`` is accepted primarily for applications/tests that manage an httpx
    transport themselves. It must use the same base origin as ``base_url``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        resolved_base_url = base_url or (str(client.base_url) if client is not None else DEFAULT_BASE_URL)
        self.base_url = normalize_base_url(resolved_base_url)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token or ''}",
                "Accept": "application/json",
                "User-Agent": "SJTU-Learning-Assistant/CanvasClient",
            },
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
        )
        if client is not None and token:
            self._client.headers["Authorization"] = f"Bearer {token}"
        self._assert_same_origin(str(self._client.base_url))

    def __enter__(self) -> "CanvasClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _origin(url: str) -> tuple[str, str | None, int | None]:
        parsed = urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme, parsed.hostname, port

    def _assert_same_origin(self, url: str) -> None:
        absolute = str(self._client.base_url.join(url))
        parsed = urlparse(absolute)
        if (
            parsed.username is not None
            or parsed.password is not None
            or self._origin(absolute) != self._origin(self.base_url)
        ):
            raise CanvasProtocolError("Canvas 链接指向其他站点，已停止以保护访问令牌。")

    def _request(
        self,
        method: str,
        url: str,
        *,
        uncertain_on_error: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        self._assert_same_origin(url)
        try:
            response = self._client.request(method, url, follow_redirects=False, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise CanvasNetworkError(
                "Canvas API 网络请求失败。", operation_uncertain=uncertain_on_error
            ) from exc
        except httpx.HTTPError as exc:
            raise CanvasNetworkError(
                "Canvas API 请求失败。", operation_uncertain=uncertain_on_error
            ) from exc
        if response.status_code == 401:
            raise CanvasError("Canvas Access Token 无效或已过期（HTTP 401）。")
        if response.status_code == 403:
            raise CanvasError("当前 Token 没有访问此 Canvas 资源的权限（HTTP 403）。")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CanvasError(f"Canvas API 返回 HTTP {response.status_code}。") from exc
        return response

    @staticmethod
    def _json(response: httpx.Response, expected: type = dict) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise CanvasProtocolError("Canvas API 返回的不是有效 JSON。") from exc
        if not isinstance(payload, expected):
            raise CanvasProtocolError("Canvas API 返回了无法识别的数据格式。")
        return payload

    def _get_object(self, url: str, *, params: Any = None) -> dict[str, Any]:
        return self._json(self._request("GET", url, params=params), dict)

    def _paginate(
        self, url: str, *, params: Iterable[tuple[str, str]] | Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        next_url: str | None = url
        next_params = params
        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for _ in range(MAX_PAGES):
            if next_url is None:
                return records
            response = self._request("GET", next_url, params=next_params)
            page = self._json(response, list)
            if not all(isinstance(item, dict) for item in page):
                raise CanvasProtocolError("Canvas 列表包含无法识别的数据。")
            for item in page:
                identity = str(item.get("id", ""))
                if not identity or identity not in seen_ids:
                    records.append(item)
                    if identity:
                        seen_ids.add(identity)
            link = response.links.get("next")
            next_url = link.get("url") if link else None
            if next_url:
                self._assert_same_origin(next_url)
            next_params = None
        raise CanvasProtocolError(f"分页超过安全上限 {MAX_PAGES} 页。")

    def courses(self) -> list[dict[str, Any]]:
        return self._paginate(
            "/api/v1/courses",
            params=[("enrollment_state", "active"), ("per_page", str(DEFAULT_PAGE_SIZE)), ("include[]", "term")],
        )

    list_courses = courses
    get_courses = courses

    def course(self, course_id: int | str) -> dict[str, Any]:
        return self._get_object(f"/api/v1/courses/{course_id}")

    get_course = course

    def assignments(self, course_id: int | str) -> list[dict[str, Any]]:
        return self._paginate(
            f"/api/v1/courses/{course_id}/assignments",
            params=[("per_page", str(DEFAULT_PAGE_SIZE)), ("order_by", "due_at"), ("include[]", "submission")],
        )

    list_assignments = assignments
    get_assignments = assignments

    def assignment(self, course_id: int | str, assignment_id: int | str) -> dict[str, Any]:
        return self._get_object(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}",
            params=[("include[]", "submission")],
        )

    get_assignment = assignment

    def planner(self, *, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [("per_page", str(DEFAULT_PAGE_SIZE))]
        if start_date:
            params.append(("start_date", start_date))
        if end_date:
            params.append(("end_date", end_date))
        return self._paginate("/api/v1/planner/items", params=params)

    get_planner_items = planner

    def modules(self, course_id: int | str) -> list[dict[str, Any]]:
        return self._paginate(f"/api/v1/courses/{course_id}/modules", params={"per_page": DEFAULT_PAGE_SIZE})

    list_modules = modules
    get_modules = modules

    def module_items(self, course_id: int | str, module_id: int | str) -> list[dict[str, Any]]:
        return self._paginate(
            f"/api/v1/courses/{course_id}/modules/{module_id}/items",
            params={"per_page": DEFAULT_PAGE_SIZE},
        )

    list_module_items = module_items
    get_module_items = module_items

    def files(self, course_id: int | str) -> list[dict[str, Any]]:
        return self._paginate(f"/api/v1/courses/{course_id}/files", params={"per_page": DEFAULT_PAGE_SIZE})

    list_files = files
    get_files = files

    def file(self, file_id: int | str) -> dict[str, Any]:
        return self._get_object(f"/api/v1/files/{file_id}")

    get_file = file

    def pages(self, course_id: int | str) -> list[dict[str, Any]]:
        return self._paginate(f"/api/v1/courses/{course_id}/pages", params={"per_page": DEFAULT_PAGE_SIZE})

    list_pages = pages
    get_pages = pages

    def page(self, course_id: int | str, page_url: str) -> dict[str, Any]:
        return self._get_object(f"/api/v1/courses/{course_id}/pages/{page_url}")

    get_page = page

    def announcements(self, course_id: int | str) -> list[dict[str, Any]]:
        return self._paginate(
            "/api/v1/announcements",
            params=[("context_codes[]", f"course_{course_id}"), ("per_page", str(DEFAULT_PAGE_SIZE))],
        )

    list_announcements = announcements
    get_announcements = announcements

    def submission(self, course_id: int | str, assignment_id: int | str) -> dict[str, Any]:
        return self._get_object(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self"
        )

    get_submission = submission

    def request_submission_file_upload(
        self,
        course_id: int | str,
        assignment_id: int | str,
        file_path: str | Path,
        *,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        path = Path(file_path)
        if not path.is_file():
            raise CanvasError(f"待上传文件不存在：{path}")
        payload = {
            "name": path.name,
            "size": str(path.stat().st_size),
            "content_type": content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        }
        response = self._request(
            "POST",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self/files",
            data=payload,
        )
        return self._json(response, dict)

    def upload_file(self, upload: Mapping[str, Any], file_path: str | Path) -> dict[str, Any]:
        """Upload bytes to Canvas' returned URL without forwarding bearer auth."""
        upload_url = str(upload.get("upload_url") or "")
        parsed = urlparse(upload_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise CanvasProtocolError("Canvas 返回了不安全的文件上传地址。")
        upload_params = upload.get("upload_params") or {}
        if not isinstance(upload_params, Mapping):
            raise CanvasProtocolError("Canvas 文件上传参数格式无效。")
        path = Path(file_path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        # Build through the injected client so MockTransport remains testable, then
        # explicitly strip all Canvas credentials before sending cross-origin.
        with path.open("rb") as stream:
            request = self._client.build_request(
                "POST", upload_url, data=dict(upload_params), files={"file": (path.name, stream, content_type)}
            )
            request.headers.pop("Authorization", None)
            request.headers.pop("Cookie", None)
            request.headers.pop("Proxy-Authorization", None)
            try:
                response = self._client.send(request, follow_redirects=False)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
                raise CanvasNetworkError("Canvas 文件上传失败。") from exc
        for _ in range(5):
            if not response.is_redirect:
                break
            location = response.headers.get("location")
            if not location:
                raise CanvasProtocolError("Canvas 文件上传重定向缺少 Location。")
            target = str(response.url.join(location))
            # Resolve relative redirects against the upload response origin first.
            # A relative redirect on an upload host must never be reinterpreted as
            # a Canvas URL and receive the bearer token.
            self._assert_same_origin(target)
            response = self._request("GET", target)
        else:
            raise CanvasProtocolError("Canvas 文件上传重定向次数超过安全上限。")
        if response.is_redirect:
            raise CanvasProtocolError("Canvas 文件上传重定向次数超过安全上限。")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CanvasError(f"Canvas 文件上传返回 HTTP {response.status_code}。") from exc
        return self._json(response, dict)

    upload_submission_file = upload_file

    def submit_assignment(
        self,
        course_id: int | str,
        assignment_id: int | str,
        submission_type: str,
        *,
        body: str | None = None,
        url: str | None = None,
        file_ids: Iterable[int | str] | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        data: list[tuple[str, str]] = [("submission[submission_type]", submission_type)]
        if body is not None:
            data.append(("submission[body]", body))
        if url is not None:
            data.append(("submission[url]", url))
        for file_id in file_ids or ():
            data.append(("submission[file_ids][]", str(file_id)))
        if comment:
            data.append(("comment[text_comment]", comment))
        response = self._request(
            "POST",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions",
            data=data,
            uncertain_on_error=True,
        )
        return self._json(response, dict)
