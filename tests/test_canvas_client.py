from __future__ import annotations

import unittest

import httpx

from test_canvas import (
    CanvasCheckError,
    connect_and_fetch_all,
    fetch_active_courses,
    fetch_course_announcements,
    fetch_course_assignments,
    normalize_base_url,
)


class CanvasClientTests(unittest.TestCase):
    def test_fetches_all_pages_and_deduplicates_courses(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.params.get("page") == "2":
                return httpx.Response(
                    200,
                    json=[
                        {"id": 2, "name": "Course B duplicate"},
                        {"id": 3, "name": "Course C"},
                    ],
                    request=request,
                )
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "name": "Course A"},
                    {"id": 2, "name": "Course B"},
                ],
                headers={
                    "Link": '<https://oc.sjtu.edu.cn/api/v1/courses?page=2&per_page=100>; rel="next"'
                },
                request=request,
            )

        with httpx.Client(
            base_url="https://oc.sjtu.edu.cn",
            transport=httpx.MockTransport(handler),
        ) as client:
            courses = fetch_active_courses(client)

        self.assertEqual([1, 2, 3], [course["id"] for course in courses])
        self.assertEqual(2, len(requests))
        self.assertEqual("active", requests[0].url.params["enrollment_state"])
        self.assertEqual("100", requests[0].url.params["per_page"])
        self.assertEqual("2", requests[1].url.params["page"])

    def test_fetches_announcements_with_course_context(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                json=[{"id": 11, "title": "Welcome"}],
                request=request,
            )

        with httpx.Client(
            base_url="https://oc.sjtu.edu.cn",
            transport=httpx.MockTransport(handler),
        ) as client:
            announcements = fetch_course_announcements(client, 95040)

        self.assertEqual(11, announcements[0]["id"])
        self.assertEqual("course_95040", captured[0].url.params["context_codes[]"])
        self.assertEqual("/api/v1/announcements", captured[0].url.path)

    def test_fetches_assignments_with_submission(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                json=[{"id": 21, "name": "Homework 1"}],
                request=request,
            )

        with httpx.Client(
            base_url="https://oc.sjtu.edu.cn",
            transport=httpx.MockTransport(handler),
        ) as client:
            assignments = fetch_course_assignments(client, 95040)

        self.assertEqual(21, assignments[0]["id"])
        self.assertEqual("/api/v1/courses/95040/assignments", captured[0].url.path)
        self.assertEqual("submission", captured[0].url.params["include[]"])

    def test_partial_course_error_does_not_skip_other_endpoint(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/courses":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 95040,
                            "name": "文本分析与大模型",
                            "course_code": "MGTS3605",
                            "term": {"name": "2026-2027 Fall"},
                        }
                    ],
                    request=request,
                )
            if request.url.path == "/api/v1/announcements":
                return httpx.Response(403, json={"errors": []}, request=request)
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 21,
                        "name": "Homework 1",
                        "due_at": "2026-09-22T15:59:00Z",
                    }
                ],
                request=request,
            )

        transport = httpx.MockTransport(handler)
        original_builder = __import__("test_canvas").build_http_client

        def mock_builder(base_url: str, token: str, timeout: float) -> httpx.Client:
            return httpx.Client(base_url=base_url, transport=transport)

        module = __import__("test_canvas")
        module.build_http_client = mock_builder
        try:
            contents = connect_and_fetch_all(
                "https://oc.sjtu.edu.cn", "test-token", 20
            )
        finally:
            module.build_http_client = original_builder

        self.assertEqual(1, len(contents))
        self.assertEqual([], contents[0].announcements)
        self.assertEqual(1, len(contents[0].assignments))
        self.assertIn("公告读取失败", contents[0].errors[0])

    def test_rejects_unauthorized_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"errors": []}, request=request)

        with httpx.Client(
            base_url="https://oc.sjtu.edu.cn",
            transport=httpx.MockTransport(handler),
        ) as client:
            with self.assertRaisesRegex(CanvasCheckError, "401"):
                fetch_active_courses(client)

    def test_rejects_cross_origin_pagination_link(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[{"id": 1}],
                headers={
                    "Link": '<https://example.com/steal-token?page=2>; rel="next"'
                },
                request=request,
            )

        with httpx.Client(
            base_url="https://oc.sjtu.edu.cn",
            transport=httpx.MockTransport(handler),
        ) as client:
            with self.assertRaisesRegex(CanvasCheckError, "保护 Token"):
                fetch_active_courses(client)

    def test_base_url_must_use_https(self) -> None:
        with self.assertRaises(CanvasCheckError):
            normalize_base_url("http://oc.sjtu.edu.cn")


if __name__ == "__main__":
    unittest.main()
