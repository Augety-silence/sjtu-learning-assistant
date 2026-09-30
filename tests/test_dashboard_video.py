from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from unittest.mock import patch

from desktop_app import DesktopBridge
from sjtu_learning_assistant.dashboard_service import DashboardError, DashboardService
from sjtu_learning_assistant.models import Base, Course, CourseFile
from sjtu_learning_assistant.video_service import SJTUVideoError


NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


class FakeVideoService:
    def __init__(self, *, items=None, error: Exception | None = None) -> None:
        self.items = list(items or [])
        self.error = error
        self.list_calls: list[tuple[str, str | None, int]] = []
        self.play_calls: list[str] = []
        self.subtitle_calls: list[str] = []
        self.slides_calls: list[str] = []

    def list_course_videos(self, course_id, *, course_name=None, limit=1000):
        self.list_calls.append((str(course_id), course_name, limit))
        if self.error:
            raise self.error
        return list(self.items)

    def subtitles(self, source_id: str):
        self.subtitle_calls.append(source_id)
        return {
            "status": "ready",
            "message": "字幕已加载。",
            "content_type": "text/vtt; charset=utf-8",
            "vtt": "WEBVTT\n",
            "cue_count": 0,
        }

    def slides_pdf(self, source_id: str):
        self.slides_calls.append(source_id)
        return {
            "status": "created",
            "data": b"%PDF-1.4\nremote slides",
            "page_count": 2,
            "size": 22,
        }

    def playback(self, source_id: str):
        self.play_calls.append(source_id)
        return {
            "available": True,
            "transport": "remote_url",
            "action": "play_remote_video",
            "source_id": source_id,
            "url": "https://videos.sjtu.edu.cn/v/99.mp4",
            "urls": ["https://videos.sjtu.edu.cn/v/99.mp4"],
        }


class DashboardVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            course = Course(source_id="12", name="文本分析", raw_data={})
            session.add(course)
            session.flush()
            self.course_id = course.id
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    def service(self, video_service: FakeVideoService) -> DashboardService:
        return DashboardService(
            self.engine,
            archive_root=Path(self.temp.name) / "archive",
            video_service=video_service,
        )

    def add_local_video(self) -> None:
        with Session(self.engine) as session:
            session.add(
                CourseFile(
                    source_id="file-1",
                    course_id=self.course_id,
                    display_name="local.mp4",
                    filename="local.mp4",
                    content_type="video/mp4",
                    url="https://oc.sjtu.edu.cn/files/1/download",
                    is_active=True,
                    last_seen_at=NOW,
                    raw_data={},
                )
            )
            session.commit()

    def test_remote_failure_falls_back_when_local_media_exists(self) -> None:
        self.add_local_video()
        remote = FakeVideoService(error=SJTUVideoError("远端暂时失败。"))
        result = self.service(remote).course_media(self.course_id)

        self.assertEqual(["file-1"], [item["source_id"] for item in result["items"]])
        self.assertIn("本地媒体", result["warning"])
        self.assertEqual([("12", "文本分析", 1000)], remote.list_calls)

    def test_remote_failure_is_safe_error_without_local_media(self) -> None:
        remote = FakeVideoService(error=SJTUVideoError("安全远端错误。"))
        with self.assertRaisesRegex(DashboardError, "安全远端错误"):
            self.service(remote).course_media(self.course_id)

    def test_remote_and_local_media_are_merged(self) -> None:
        self.add_local_video()
        remote_item = {
            "source_id": "sjtu-video:12:99",
            "name": "远端录像",
            "media_kind": "video",
            "playback": {
                "available": True,
                "transport": "dashboard_action",
                "action": "play_remote_video",
                "source_id": "sjtu-video:12:99",
            },
            "subtitles": [],
        }
        result = self.service(FakeVideoService(items=[remote_item])).course_media(
            self.course_id
        )

        self.assertEqual(
            ["sjtu-video:12:99", "file-1"],
            [item["source_id"] for item in result["items"]],
        )
        self.assertEqual(2, result["counts"]["video"])

    def test_remote_playback_uses_video_service(self) -> None:
        remote = FakeVideoService()
        result = self.service(remote).video("sjtu-video:12:99")

        self.assertEqual("play_remote_video", result["action"])
        self.assertEqual(["sjtu-video:12:99"], remote.play_calls)

    def test_remote_subtitles_and_slides_use_strict_descriptors(self) -> None:
        remote = FakeVideoService()
        service = self.service(remote)
        subtitles = service.video_subtitles("sjtu-video:12:99")
        self.assertEqual("ready", subtitles["status"])
        self.assertEqual(["sjtu-video:12:99"], remote.subtitle_calls)

        pdf = service.video_slides_pdf("sjtu-video:12:99")
        self.assertEqual("created", pdf["status"])
        self.assertEqual("文本分析-课件.pdf", pdf["filename"])
        self.assertEqual(pdf["filename"], pdf["reveal_token"])
        self.assertNotIn("path", pdf)
        self.assertTrue((Path(self.temp.name) / "unused").is_absolute())
        export = Path(service._academic_temp.name) / pdf["filename"]
        self.assertTrue(export.read_bytes().startswith(b"%PDF-"))

        bridge = DesktopBridge(service)
        self.assertTrue(
            bridge.invoke("video_subtitles", {"source_id": "sjtu-video:12:99"})["ok"]
        )
        self.assertTrue(
            bridge.invoke("video_slides_pdf", {"source_id": "sjtu-video:12:99"})["ok"]
        )
        for action in ("video_subtitles", "video_slides_pdf"):
            response = bridge.invoke(action, {"source_id": "file-1"})
            self.assertFalse(response["ok"])

    def test_slides_pdf_cleans_partial_file_when_atomic_replace_fails(self) -> None:
        service = self.service(FakeVideoService())
        with patch("sjtu_learning_assistant.dashboard_service.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                service.video_slides_pdf("sjtu-video:12:99")
        root = Path(service._academic_temp.name)
        self.assertEqual([], list(root.glob(".slides-pdf-*")))


if __name__ == "__main__":
    unittest.main()
