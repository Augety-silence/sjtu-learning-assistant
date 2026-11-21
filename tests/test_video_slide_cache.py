from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import test_video_service
from sjtu_learning_assistant.video_service import SJTUVideoError
from sjtu_learning_assistant.video_slide_cache import VideoSlideCache


class VideoCheckpointTests(unittest.TestCase):
    def test_resume_after_failure_and_restart_without_reloading_valid_pages(self):
        helper = test_video_service.VideoServiceTests()
        calls = []
        failing = True
        signature = "first"
        def handler(request):
            nonlocal failing
            if request.url.path.endswith("/v1/course/ai/ppt"):
                return httpx.Response(200, json={"data": {"docList": [
                    {"imageUrl": f"https://live.sjtu.edu.cn/{i}.jpg?sign={signature}", "imageSeekTime": i * 1000} for i in (1, 2)]}}, request=request)
            calls.append(request.url.path)
            if request.url.path == "/2.jpg" and failing:
                return httpx.Response(503, request=request)
            return httpx.Response(200, content=helper._jpeg("red"), headers={"content-type": "image/jpeg"}, request=request)
        with tempfile.TemporaryDirectory() as tmp:
            service = helper.token_service(handler, slide_cache_root=Path(tmp), image_retries=0)
            with self.assertRaises(SJTUVideoError):
                service.slides_pdf("sjtu-video:12:789")
            self.assertEqual(["/1.jpg", "/2.jpg"], calls)
            failing = False
            signature = "rotated"
            service = helper.token_service(handler, slide_cache_root=Path(tmp), image_retries=0)
            result = service.slides_pdf("sjtu-video:12:789")
            self.assertTrue(result["data"].startswith(b"%PDF-"))
            self.assertEqual(["/1.jpg", "/2.jpg", "/2.jpg"], calls)
            self.assertEqual(result["data"], service.slides_pdf("sjtu-video:12:789")["data"])
            self.assertEqual(3, len(calls))
            self.assertFalse(list(Path(tmp).rglob(".page-*")))
            self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in Path(tmp).rglob("*.image")))

    def test_revision_and_source_isolation_checksum_and_symlink_fail_closed(self):
        records = [{"index": 0, "url": "https://live.sjtu.edu.cn/1.jpg", "order_time": 1}]
        with tempfile.TemporaryDirectory() as tmp:
            cache = VideoSlideCache(Path(tmp), "sjtu-video:12:789", records)
            cache.write("0.image", b"first")
            self.assertEqual(b"first", cache.read("0.image", 20))
            (cache.directory / "0.image").write_bytes(b"corrupted")
            self.assertIsNone(cache.read("0.image", 20))
            other = VideoSlideCache(Path(tmp), "sjtu-video:12:790", records)
            self.assertIsNone(other.read("0.image", 20))
            changed = VideoSlideCache(Path(tmp), "sjtu-video:12:789", [{**records[0], "url": "https://live.sjtu.edu.cn/new.jpg"}])
            self.assertNotEqual(cache.directory, changed.directory)
            (cache.directory / "link.image").symlink_to(cache.directory / "0.image")
            with self.assertRaises(SJTUVideoError):
                cache.read("link.image", 20)
            with patch("sjtu_learning_assistant.video_slide_cache.os.replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    cache.write("new.image", b"new")
            self.assertFalse(list(cache.directory.glob(".page-*")))


if __name__ == "__main__":
    unittest.main()
