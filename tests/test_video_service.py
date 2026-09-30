from __future__ import annotations

import io
import unittest

from PIL import Image
from urllib.parse import parse_qs

import httpx

from sjtu_learning_assistant.video_service import (
    SJTUVideoError,
    SJTUVideoService,
    parse_remote_source_id,
)


TOKEN = ".".join(("header", "payload", "signature"))


class CanvasLaunchClient:
    def __init__(self, url: str = "https://oc.sjtu.edu.cn/lti/start") -> None:
        self.url = url
        self.calls: list[tuple[str, str, object]] = []

    def _request(self, method: str, path: str, *, params=None):
        self.calls.append((method, path, params))
        request = httpx.Request(method, f"https://oc.sjtu.edu.cn{path}")
        return httpx.Response(200, json={"url": self.url}, request=request)


class VideoServiceTests(unittest.TestCase):
    def test_ids_and_launch_url_are_strict(self) -> None:
        service = SJTUVideoService(lambda: CanvasLaunchClient())
        self.assertEqual(
            "sjtu-video:12:34", service.remote_source_id("12", 34)
        )
        self.assertEqual(("12", "34"), parse_remote_source_id("sjtu-video:12:34"))
        for value in ("0", "-1", "1/2", " 1 ", True):
            with self.subTest(value=value), self.assertRaises(SJTUVideoError):
                service.remote_source_id(value, 34)

        unsafe = SJTUVideoService(
            lambda: CanvasLaunchClient("https://evil.example/jump")
        )
        with self.assertRaisesRegex(SJTUVideoError, "不安全"):
            unsafe.list_course_videos(12)

    def test_follows_lti_form_and_maps_video_list(self) -> None:
        canvas = CanvasLaunchClient()
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/lti/start":
                return httpx.Response(
                    200,
                    text=(
                        '<form method="post" action="https://v.sjtu.edu.cn/oidc">'
                        '<input name="state" value="safe-state">'
                        '<input name="id_token" value="safe-id-token">'
                        "</form>"
                    ),
                    request=request,
                )
            if request.url.path == "/oidc":
                self.assertEqual(
                    {"state": ["safe-state"], "id_token": ["safe-id-token"]},
                    parse_qs(request.content.decode()),
                )
                return httpx.Response(
                    302,
                    headers={
                        "location": (
                            "https://v.sjtu.edu.cn/app/#/course?"
                            f"jwt_token={TOKEN}"
                        )
                    },
                    request=request,
                )
            if request.url.path.endswith("/lms/launch-context"):
                self.assertEqual(TOKEN, request.headers["jwt-token"])
                return httpx.Response(
                    200,
                    json={"data": {"canvasRecord": {"teachingClassId": "456"}}},
                    request=request,
                )
            if request.url.path.endswith("/v1/subject_vod_list_new"):
                self.assertEqual("456", request.url.params["teclIds"])
                self.assertEqual("1000", request.url.params["page.pageSize"])
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "records": [
                                {
                                    "id": 789,
                                    "subjName": "文本分析",
                                    "teclName": "教学班 A",
                                    "weekNo": "1",
                                    "week": 1,
                                    "letiNumber": "9",
                                    "courBeginTime": "2026-09-01 08:00:00",
                                    "courEndTime": "2026-09-01 09:02:03",
                                    "clroName": "东上院 100",
                                }
                            ]
                        }
                    },
                    request=request,
                )
            raise AssertionError(f"unexpected request: {request.url}")

        service = SJTUVideoService(
            lambda: canvas,
            http_client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(handler), follow_redirects=False
            ),
        )
        result = service.list_course_videos(12, course_name="文本分析")

        self.assertEqual(4, len(requests))
        self.assertEqual("sjtu-video:12:789", result[0]["source_id"])
        self.assertEqual("文本分析 · 第1周 · 周一 · 第9节", result[0]["name"])
        self.assertEqual("文本分析", result[0]["course_name"])
        self.assertEqual("教学班 A", result[0]["teaching_class"])
        self.assertEqual(1, result[0]["weekday"])
        self.assertEqual("周一", result[0]["weekday_label"])
        self.assertEqual(9, result[0]["lesson_number"])
        self.assertEqual("东上院 100", result[0]["classroom"])
        self.assertEqual(3723, result[0]["duration"])
        self.assertTrue(result[0]["supports_subtitle"])
        self.assertTrue(result[0]["supports_slides_pdf"])
        self.assertFalse(result[0]["downloadable"])
        self.assertEqual("play_remote_video", result[0]["playback"]["action"])
        self.assertNotIn("url", result[0]["playback"])
        self.assertEqual(
            (
                "GET",
                "/api/v1/courses/12/external_tools/sessionless_launch",
                {"id": "8329", "launch_type": "course_navigation"},
            ),
            canvas.calls[0],
        )


    def token_service(self, handler, **options) -> SJTUVideoService:
        canvas = CanvasLaunchClient(
            f"https://v.sjtu.edu.cn/app?jwt_token={TOKEN}"
        )
        return SJTUVideoService(
            lambda: canvas,
            http_client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(handler), follow_redirects=False
            ),
            **options,
        )

    def test_title_fallback_never_exposes_video_id(self) -> None:
        service = SJTUVideoService(lambda: CanvasLaunchClient())
        mapped = service._map_video({"id": 789}, "12", "管理会计")
        self.assertEqual("管理会计 · 课程录像", mapped["name"])
        self.assertNotIn("789", mapped["name"])
        self.assertEqual((7, "周日"), service._weekday(7))
        self.assertEqual((None, None), service._weekday(8))
        self.assertEqual(5400, service._duration_between("08:00", "09:30"))

    def test_subtitles_prefer_assembled_list_and_escape_webvtt(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                "/jy-application-resourcemanage/v1/course/ai/translate/789",
                request.url.path,
            )
            self.assertEqual("true", request.url.params["useOriginal"])
            self.assertEqual(TOKEN, request.headers["jwt-token"])
            return httpx.Response(
                200,
                json={
                    "data": {
                        "beforeAssemblyList": [
                            {"bg": 0, "ed": 500, "res": "不应选用"}
                        ],
                        "afterAssemblyList": [
                            {"bg": 3_723_004, "ed": 3_725_006, "res": "A < B & C"}
                        ],
                    }
                },
                request=request,
            )

        result = self.token_service(handler).subtitles("sjtu-video:12:789")
        self.assertEqual("ready", result["status"])
        self.assertEqual(1, result["cue_count"])
        self.assertEqual(
            "WEBVTT\n\n01:02:03.004 --> 01:02:05.006\nA &lt; B &amp; C\n",
            result["vtt"],
        )

    def test_subtitles_fall_back_and_report_empty_or_errors(self) -> None:
        payloads = [
            {
                "data": {
                    "afterAssemblyList": [],
                    "beforeAssemblyList": [{"bg": 0, "ed": 1000, "res": "原字幕"}],
                }
            },
            {"data": {"status": "processing", "afterAssemblyList": []}},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payloads.pop(0), request=request)

        service = self.token_service(handler)
        self.assertIn("原字幕", service.subtitles("sjtu-video:12:789")["vtt"])
        processing = service.subtitles("sjtu-video:12:789")
        self.assertEqual("processing", processing["status"])
        self.assertIsNone(processing["vtt"])

        def failure(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, request=request)

        with self.assertRaisesRegex(SJTUVideoError, "请求失败"):
            self.token_service(failure).subtitles("sjtu-video:12:789")

    @staticmethod
    def _jpeg(color: str) -> bytes:
        output = io.BytesIO()
        Image.new("RGB", (20, 10), color).save(output, format="JPEG")
        return output.getvalue()

    def test_default_slide_pixel_budget_supports_full_hd_lectures(self) -> None:
        service = SJTUVideoService(lambda: CanvasLaunchClient())
        self.assertGreaterEqual(service.max_slides_total_pixels, 414_720_000)

    def test_slide_metadata_enforces_host_and_page_limits(self) -> None:
        service = SJTUVideoService(lambda: CanvasLaunchClient(), max_slide_pages=1)
        with self.assertRaisesRegex(SJTUVideoError, "页数"):
            service._slide_records(
                {
                    "data": {
                        "docList": [
                            {"imageUrl": "https://live.sjtu.edu.cn/1.jpg"},
                            {"imageUrl": "https://live.sjtu.edu.cn/2.jpg"},
                        ]
                    }
                }
            )
        with self.assertRaisesRegex(SJTUVideoError, "不安全"):
            service._slide_records(
                {"data": {"docList": [{"imageUrl": "https://evil.example/1.jpg"}]}}
            )

    def test_slide_image_mime_size_and_pdf_success(self) -> None:
        jpeg = self._jpeg("red")

        def good(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=jpeg,
                headers={"content-type": "image/jpeg"},
                request=request,
            )

        service = self.token_service(good)
        image = service._download_slide_image(
            httpx.Client(transport=httpx.MockTransport(good)),
            "https://live.sjtu.edu.cn/1.jpg",
        )
        pdf = service._render_slides_pdf([image, self._jpeg("blue")])
        self.assertTrue(pdf.startswith(b"%PDF-"))

        def wrong_mime(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"not-an-image",
                headers={"content-type": "text/html"},
                request=request,
            )

        with self.assertRaisesRegex(SJTUVideoError, "类型"):
            service._download_slide_image(
                httpx.Client(transport=httpx.MockTransport(wrong_mime)),
                "https://live.sjtu.edu.cn/1.jpg",
            )

        too_small = self.token_service(good, max_slide_image_bytes=10)
        with self.assertRaisesRegex(SJTUVideoError, "大小"):
            too_small._download_slide_image(
                httpx.Client(transport=httpx.MockTransport(good)),
                "https://live.sjtu.edu.cn/1.jpg",
            )

    def test_slides_pdf_orders_images_and_rejects_partial_failure(self) -> None:
        red = self._jpeg("red")
        blue = self._jpeg("blue")
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path.endswith("/v1/course/ai/ppt"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "docList": [
                                {
                                    "imageUrl": "https://live.sjtu.edu.cn/late.jpg",
                                    "imageSeekTime": 2000,
                                },
                                {
                                    "imageUrl": "https://live.sjtu.edu.cn/early.jpg",
                                    "imageSeekTime": 1000,
                                },
                            ]
                        }
                    },
                    request=request,
                )
            if request.url.path == "/early.jpg":
                return httpx.Response(200, content=red, headers={"content-type": "image/jpeg"}, request=request)
            if request.url.path == "/late.jpg":
                return httpx.Response(200, content=blue, headers={"content-type": "image/jpeg"}, request=request)
            raise AssertionError(request.url)

        result = self.token_service(handler).slides_pdf("sjtu-video:12:789")
        self.assertEqual(["/early.jpg", "/late.jpg"], calls[-2:])
        self.assertEqual(2, result["page_count"])
        self.assertTrue(result["data"].startswith(b"%PDF-"))

    def test_playback_accepts_only_approved_https_hosts(self) -> None:
        canvas = CanvasLaunchClient(
            f"https://v.sjtu.edu.cn/app?jwt_token={TOKEN}"
        )

        def client_for(url: str) -> httpx.Client:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    json={"data": {"courseVodViewList": [{"url": url}]}},
                    request=request,
                )

            return httpx.Client(transport=httpx.MockTransport(handler))

        safe = SJTUVideoService(
            lambda: canvas,
            http_client_factory=lambda: client_for(
                "https://live.sjtu.edu.cn/recording/789.mp4?token=short-lived"
            ),
        )
        result = safe.playback("sjtu-video:12:789")
        self.assertEqual("play_remote_video", result["action"])
        self.assertEqual(result["url"], result["urls"][0])

        unsafe = SJTUVideoService(
            lambda: canvas,
            http_client_factory=lambda: client_for(
                "https://live.sjtu.edu.cn.evil.example/recording.mp4"
            ),
        )
        with self.assertRaisesRegex(SJTUVideoError, "不安全"):
            unsafe.playback("sjtu-video:12:789")


if __name__ == "__main__":
    unittest.main()
