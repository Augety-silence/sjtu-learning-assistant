from __future__ import annotations

import unittest

import httpx

from test_canvas import CanvasCheckError, fetch_active_courses, normalize_base_url


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

    def test_rejects_unauthorized_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"errors": []}, request=request)

        with httpx.Client(
            base_url="https://oc.sjtu.edu.cn",
            transport=httpx.MockTransport(handler),
        ) as client:
            with self.assertRaisesRegex(CanvasCheckError, "401"):
                fetch_active_courses(client)

    def test_base_url_must_use_https(self) -> None:
        with self.assertRaises(CanvasCheckError):
            normalize_base_url("http://oc.sjtu.edu.cn")


if __name__ == "__main__":
    unittest.main()
