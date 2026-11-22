from __future__ import annotations

import tempfile
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from sjtu_learning_assistant.subtitle_sync import SubtitleSync
from sjtu_learning_assistant.transcript_service import TranscriptService
from sjtu_learning_assistant.video_service import SJTUVideoService, SJTUVideoError

VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n当堂老师讲了什么\n"


class SubtitlePrefetchTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "transcripts"
        self.fetch = Mock(return_value={"status": "ready", "vtt": VTT})
        self.ai = Mock(side_effect=AssertionError("原始同步不应使用 AI"))
        self.clock = datetime.now(timezone.utc)
        self.service = self.make_service()

    def make_service(self):
        return TranscriptService(self.root, subtitle_fetcher=self.fetch, ai_context_provider=self.ai,
                                 enable_phase1=False, autostart_worker=False, sleeper=lambda _: None,
                                 now_provider=lambda: self.clock)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    @staticmethod
    def video(index=1, course="12"):
        return {"source_id": f"sjtu-video:{course}:{index}", "title": f"第{index}节"}

    def queue(self, videos=None, retry=False):
        return self.service.prefetch(course_id="12", course_name="课程", videos=videos or [self.video()], retry=retry)

    def test_unopened_videos_download_without_models_or_playback(self):
        self.assertEqual(2, self.queue([self.video(1), self.video(2)]))
        self.assertEqual(0, self.queue([self.video(1), self.video(2)]))
        self.service.run_pending()  # ordinary AI worker does not claim raw batches
        self.fetch.assert_not_called()
        self.service.run_pending(source_only=True)
        self.assertEqual(2, self.fetch.call_count)
        self.ai.assert_not_called()
        self.assertIsNone(self.service._foreground_source)
        self.assertEqual(VTT, self.service.cached_subtitles("sjtu-video:12:2")["vtt"])
        stored = self.service._stored_learning_input("sjtu-video:12:2")
        self.assertTrue((stored[0] / "original.md").is_file())
        self.assertTrue((stored[0] / "lesson.md").is_file())
        self.assertEqual("pending", stored[1]["lesson_notes"]["status"])
        self.assertTrue(all(job["stage"] == "raw_saved" for job in self.service.jobs()["items"]))

    def test_batches_over_100_and_completed_materials_untouched(self):
        videos = [self.video(index) for index in range(1, 102)]
        self.assertEqual(101, self.queue(videos))
        self.assertEqual([1, 100], sorted(len(batch["jobs"]) for batch in self.service._all_batches()))
        self.service.run_pending(source_only=True)
        directory = self.service._stored_learning_input("sjtu-video:12:1")[0]
        (directory / "lesson.md").write_text("人工笔记", encoding="utf-8")
        before = {file.name: file.read_bytes() for file in directory.iterdir() if file.is_file()}
        self.assertEqual(0, self.queue(videos, retry=True))
        self.service.run_pending(source_only=True)
        self.assertEqual(before, {file.name: file.read_bytes() for file in directory.iterdir() if file.is_file()})
        self.ai.assert_not_called()

    def test_remote_processing_and_network_retry_resume(self):
        self.fetch.side_effect = [{"status": "processing"}, {"status": "ready", "vtt": VTT}]
        self.queue()
        self.service.run_pending(source_only=True)
        self.assertEqual("waiting_remote", self.service._all_batches()[0]["status"])
        self.service.run_pending(source_only=True)
        self.assertEqual(1, self.fetch.call_count)
        self.clock += timedelta(minutes=10)
        self.service.run_pending(source_only=True)
        self.assertEqual("completed", self.service._all_batches()[0]["status"])
        self.fetch.side_effect = ConnectionError("临时连接失败")
        self.queue([self.video(3)])
        self.service.run_pending(source_only=True)
        batch = next(batch for batch in self.service._all_batches() if batch["jobs"][0]["source_id"] == "sjtu-video:12:3")
        self.assertEqual("waiting_remote", batch["status"])
        self.fetch.side_effect = None
        self.clock += timedelta(minutes=10)
        self.service.run_pending(source_only=True)
        self.assertIsNotNone(self.service.cached_subtitles("sjtu-video:12:3"))
        self.ai.assert_not_called()

    def test_restart_recovers_inflight_raw_jobs(self):
        self.queue()
        batch = self.service._all_batches()[0]
        batch["status"] = "fetching"
        batch["jobs"][0]["status"] = "fetching"
        batch["owner"] = {"pid": -1}
        self.service._write_json(self.service._batch_path(batch["id"]), batch)
        self.service.close()
        with patch.object(TranscriptService, "_owner_alive", return_value=False):
            self.service = self.make_service()
        self.assertEqual("queued", self.service._all_batches()[0]["status"])
        self.service.run_pending(source_only=True)
        self.assertIsNotNone(self.service.cached_subtitles("sjtu-video:12:1"))

    def test_corrupt_cache_is_refetched(self):
        self.queue()
        self.service.run_pending(source_only=True)
        directory = self.service._stored_learning_input("sjtu-video:12:1")[0]
        (directory / "raw.vtt").write_text("corrupt", encoding="utf-8")
        self.assertIsNone(self.service.cached_subtitles("sjtu-video:12:1"))
        self.queue(retry=True)
        self.service.run_pending(source_only=True)
        self.assertEqual(VTT, self.service.cached_subtitles("sjtu-video:12:1")["vtt"])

    def test_discovery_includes_history_and_isolates_inaccessible_courses(self):
        courses = [("12", "本学期"), ("13", "无录像权限"), ("14", "往期课程")]
        def videos(course, _):
            if course == "13":
                raise PermissionError()
            return [{"source_id": f"sjtu-video:{course}:1", "name": "视频平台返回的课堂名称"}]
        sync = SubtitleSync(lambda: courses, videos, lambda: self.service)
        sync.run_once()
        self.service.run_pending(source_only=True)
        self.assertEqual(3, sync.status()["checked"])
        self.assertEqual(1, sync.status()["unavailable"])
        self.assertIsNotNone(self.service.cached_subtitles("sjtu-video:14:1"))
        self.assertEqual(2, self.fetch.call_count)
        sync.run_once()
        self.assertEqual(0, sync.status()["queued"])
        sync.close()

    def test_full_course_video_discovery_reads_more_than_first_page(self):
        remote = SJTUVideoService(lambda: None)
        remote._http_client = lambda: nullcontext(object())
        remote._launch_jwt = Mock(return_value="test-token")
        remote._api_json = Mock(side_effect=[
            {"data": {"canvasRecord": {"teachingClassId": "456"}}},
            {"data": {"records": [{"id": index} for index in range(1, 1001)]}},
            {"data": {"records": [{"id": 1001}, {"id": 1002}]}},
        ])
        items = remote.list_course_videos("12", course_name="课程", limit=10000)
        self.assertEqual(1002, len(items))
        self.assertEqual(2, remote._api_json.call_args.kwargs["params"]["page.pageIndex"])
        self.assertEqual("sjtu-video:12:1002", items[-1]["source_id"])

    def test_duplicate_remote_page_does_not_masquerade_as_complete(self):
        remote = SJTUVideoService(lambda: None)
        remote._http_client = lambda: nullcontext(object())
        remote._launch_jwt = Mock(return_value="test-token")
        records = {"data": {"records": [{"id": index} for index in range(1, 1001)]}}
        remote._api_json = Mock(side_effect=[{"data": {"canvasRecord": {"teachingClassId": "456"}}}, records, records])
        with self.assertRaises(SJTUVideoError):
            remote.list_course_videos("12", limit=10000)
