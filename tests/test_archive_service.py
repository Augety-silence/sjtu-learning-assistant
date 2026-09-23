from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from sjtu_learning_assistant.archive_service import (
    MAX_DOWNLOAD_BYTES,
    ArchiveError,
    ArchiveFileContext,
    ArchiveService,
    add_source_id_suffix,
    canonical_folder_chain,
    derive_current_term,
    ensure_within_root,
    normalize_term,
    sanitize_component,
)


class ArchivePathAndTermTests(unittest.TestCase):
    def test_derives_current_term_from_date_without_hard_coding(self) -> None:
        self.assertEqual("2026-2027 Fall", derive_current_term(date(2026, 9, 21)))
        self.assertEqual("2025-2026 Spring", derive_current_term(date(2026, 2, 1)))

    def test_validates_term_format(self) -> None:
        self.assertEqual("2026-2027 Fall", normalize_term(" 2026-2027   fall "))
        with self.assertRaises(ArchiveError):
            normalize_term("2026 Fall")
        with self.assertRaises(ArchiveError):
            normalize_term("2026-2028 Fall")

    def test_sanitizes_components_and_enforces_root_boundary(self) -> None:
        self.assertEqual("_.._evil_.pdf", sanitize_component("/../evil\\.pdf", fallback="x"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            self.assertEqual(
                root.absolute() / "course" / "file.pdf",
                ensure_within_root(root, root / "course" / "file.pdf"),
            )
            with self.assertRaises(ArchiveError):
                ensure_within_root(root, root / ".." / "outside")

    def test_same_name_gets_source_id_suffix(self) -> None:
        self.assertEqual("lecture [42].pdf", add_source_id_suffix("lecture.pdf", "42"))

    def test_folder_chain_removes_pseudo_roots_categories_and_repeated_blocks(self) -> None:
        self.assertEqual(
            ("单元 a", "章节 b"),
            canonical_folder_chain(
                (
                    "文本分析与大模型",
                    "文本分析与大模型-AI3601",
                    "AI-3601",
                    "course ware",
                    "课 件",
                    "单元 A",
                    "章节 B",
                    "单元-A",
                    "章节　B",
                ),
                course_name="文本分析与大模型 (AI3601)",
                course_code="AI3601",
                category="courseware",
            ),
        )
        self.assertEqual(
            ("week 1",),
            canonical_folder_chain(
                ("Ｗｅｅｋ　１", "week-1", " week_1 "),
                course_name="文本分析",
                course_code=None,
                category="other",
            ),
        )

    def test_folder_id_parent_chain_is_reconstructed(self) -> None:
        root_folder = SimpleNamespace(id=1, parent_folder_id=None, name="course files")
        week = SimpleNamespace(id=2, parent_folder_id=1, name="第一周")
        slides = SimpleNamespace(id=3, parent_folder_id=2, name="课件")
        self.assertEqual(
            ("第一周", "课件"),
            ArchiveService._folder_chain(3, {1: root_folder, 2: week, 3: slides}),
        )


class ArchiveDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.api_requests: list[httpx.Request] = []
        self.redirect_url = "https://cdn.example.edu/file/42"
        self.download_requests: list[httpx.Request] = []

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def context(source_id: str = "42", name: str = "lecture.pdf") -> ArchiveFileContext:
        return ArchiveFileContext(
            source_id=source_id,
            course_name="文本/分析",
            term_name="2026-2027 Fall",
            display_name=name,
            expected_size=7,
            source_updated_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
            local_path=None,
            download_status="pending",
            download_attempts=0,
            downloaded_size=None,
            downloaded_sha256=None,
            downloaded_source_updated_at=None,
            folder_names=("course files", "第一周"),
        )

    def make_service(self, *, failures: int = 0) -> ArchiveService:
        state = {"failures": failures}

        def api_handler(request: httpx.Request) -> httpx.Response:
            self.api_requests.append(request)
            return httpx.Response(
                200,
                json={"url": "https://objects.example.edu/file/42"},
                request=request,
            )

        def download_handler(request: httpx.Request) -> httpx.Response:
            self.download_requests.append(request)
            if state["failures"]:
                state["failures"] -= 1
                raise httpx.ConnectError("temporary", request=request)
            if request.url.host == "objects.example.edu":
                return httpx.Response(
                    302,
                    headers=dict(Location=self.redirect_url),
                    request=request,
                )
            return httpx.Response(200, content=b"content", request=request)

        api_client = httpx.Client(
            base_url="https://oc.sjtu.edu.cn",
            headers={"Authorization": "Bearer secret"},
            transport=httpx.MockTransport(api_handler),
        )

        def download_factory() -> httpx.Client:
            return httpx.Client(
                headers={"Authorization": "must-be-stripped"},
                follow_redirects=True,
                transport=httpx.MockTransport(download_handler),
            )

        service = ArchiveService(
            SimpleNamespace(),
            api_client,
            archive_root=self.root,
            current_term="2026-2027 Fall",
            download_client_factory=download_factory,
            sleeper=lambda _delay: None,
        )
        self.addCleanup(api_client.close)
        service.attempts = list()
        service._record_attempt = service.attempts.append
        service.statuses = []
        service._write_status = lambda *args, **kwargs: service.statuses.append(
            (args, kwargs)
        )
        return service

    def test_uses_authenticated_file_api_and_unauthenticated_stream_client(self) -> None:
        service = self.make_service()
        context = self.context()
        target = service._planned_paths([context])[context.source_id]
        result = service._download_one(context, target)

        self.assertEqual("downloaded", result.status)
        self.assertEqual(b"content", target.read_bytes())
        self.assertEqual(hashlib.sha256(b"content").hexdigest(), result.sha256)
        self.assertEqual("Bearer secret", self.api_requests[0].headers["Authorization"])
        self.assertEqual("/api/v1/files/42", self.api_requests[0].url.path)
        self.assertEqual(
            ["objects.example.edu", "cdn.example.edu"],
            [request.url.host for request in self.download_requests],
        )
        for request in self.download_requests:
            self.assertNotIn("Authorization", request.headers)
        client = service.download_client_factory()
        try:
            self.assertTrue(client.follow_redirects)
        finally:
            client.close()
        self.assertFalse(list(target.parent.glob(".sjtu-download-*")))

    def test_rejects_https_redirect_to_http_before_second_request(self) -> None:
        self.redirect_url = "http://cdn.example.edu/file/42"
        service = self.make_service()
        with self.assertRaisesRegex(ArchiveError, "HTTPS"):
            service._stream_to_temporary(
                "https://objects.example.edu/file/42", self.root
            )
        self.assertEqual(1, len(self.download_requests))
        request = next(iter(self.download_requests))
        self.assertEqual("objects.example.edu", request.url.host)
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(tuple(), tuple(self.root.iterdir()))

    def test_redirect_count_is_limited(self) -> None:
        self.redirect_url = "https://objects.example.edu/file/42"
        service = self.make_service()
        with self.assertRaisesRegex(ArchiveError, "重定向次数"):
            service._stream_to_temporary(
                "https://objects.example.edu/file/42", self.root
            )
        self.assertEqual(6, len(self.download_requests))
        self.assertEqual(tuple(), tuple(self.root.iterdir()))

    def test_target_symlink_is_unlinked_without_touching_its_target(self) -> None:
        service = self.make_service()
        context = self.context()
        target = service._planned_paths((context,)).get(context.source_id)
        target.parent.mkdir(parents=True)
        outside = self.root.parent / ("outside-" + self.root.name)
        outside.write_bytes(b"outside")
        self.addCleanup(outside.unlink, missing_ok=True)
        target.symlink_to(outside)

        result = service._download_one(context, target)

        self.assertEqual("downloaded", result.status)
        self.assertFalse(target.is_symlink())
        self.assertEqual(b"content", target.read_bytes())
        self.assertEqual(b"outside", outside.read_bytes())

    def test_versions_directory_symlink_is_rejected_without_external_copy(self) -> None:
        service = self.make_service()
        context = self.context()
        target = service._planned_paths((context,)).get(context.source_id)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")
        with tempfile.TemporaryDirectory() as outside_name:
            outside = Path(outside_name)
            (target.parent / ".versions").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ArchiveError, "symlink"):
                service._download_one(context, target)
            self.assertEqual(tuple(), tuple(outside.iterdir()))
        self.assertEqual(b"old", target.read_bytes())

    def test_retries_three_times_and_isolates_failure(self) -> None:
        service = self.make_service(failures=3)
        good = self.context("43", "good.pdf")
        bad = self.context("42", "bad.pdf")
        original = service._download_one

        def mixed(context, target):
            if context.source_id == "42":
                return original(context, target)
            return SimpleNamespace(source_id="43", status="unchanged")

        service._download_one = mixed
        results = service._download_contexts([bad, good])
        self.assertEqual(["failed", "unchanged"], [item.status for item in results])
        self.assertEqual(3, len(self.download_requests))
        self.assertEqual(("42", "42", "42"), tuple(service.attempts))
        self.assertEqual("failed", service.statuses[-1][1]["status"])

    def test_existing_file_requires_matching_sha256(self) -> None:
        service = self.make_service()
        context = self.context()
        target = service._planned_paths([context])[context.source_id]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"content")
        matching = ArchiveFileContext(
            **{
                **context.__dict__,
                "local_path": str(target),
                "download_status": "downloaded",
                "downloaded_size": 7,
                "downloaded_sha256": hashlib.sha256(b"content").hexdigest(),
                "downloaded_source_updated_at": context.source_updated_at,
            }
        )
        self.assertTrue(service._is_unchanged(matching, target))
        target.write_bytes(b"changed")
        self.assertFalse(service._is_unchanged(matching, target))

    def test_downloaded_matching_file_is_idempotently_unchanged(self) -> None:
        service = self.make_service()
        context = self.context()
        target = service._planned_paths((context,)).get(context.source_id)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"content")
        matching = replace(
            context,
            local_path=str(target),
            download_status="downloaded",
            downloaded_size=7,
            downloaded_sha256=hashlib.sha256(b"content").hexdigest(),
            downloaded_source_updated_at=context.source_updated_at,
        )

        result = service._download_one(matching, target)

        self.assertEqual("unchanged", result.status)
        self.assertEqual(tuple(), tuple(service.attempts))
        self.assertEqual(tuple(), tuple(self.api_requests))

    def test_status_write_failure_does_not_stop_later_file(self) -> None:
        service = self.make_service(failures=3)
        contexts = [self.context("42", "bad.pdf"), self.context("43", "next.pdf")]
        original_download = service._download_one

        def download(context, target):
            if context.source_id == "42":
                return original_download(context, target)
            return SimpleNamespace(source_id="43", status="unchanged")

        service._download_one = download
        service._write_status = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("database unavailable")
        )
        results = service._download_contexts(contexts)
        self.assertEqual(["failed", "unchanged"], [item.status for item in results])
        self.assertIn("状态写回失败", results[0].error)

    def test_rejects_oversize_before_network(self) -> None:
        service = self.make_service()
        context = self.context()
        context = ArchiveFileContext(
            **{**context.__dict__, "expected_size": MAX_DOWNLOAD_BYTES + 1}
        )
        target = service._planned_paths([context])[context.source_id]
        with self.assertRaisesRegex(ArchiveError, "500 MiB"):
            service._download_one(context, target)
        self.assertEqual([], self.api_requests)

    def test_same_name_collisions_are_suffixed_and_folder_tree_is_used(self) -> None:
        service = self.make_service()
        first = replace(
            self.context("42"),
            category="assignments",
            folder_names=("course files", "Week 1"),
        )
        second = replace(
            self.context("43"),
            category="assignments",
            folder_names=("course files", "Ｗｅｅｋ－１"),
        )
        planned = service._planned_paths([first, second])
        self.assertEqual("lecture [42].pdf", planned["42"].name)
        self.assertEqual("lecture [43].pdf", planned["43"].name)
        self.assertIn("2026-2027 Fall", planned["42"].parts)
        self.assertIn("文本_分析", planned["42"].parts)
        self.assertIn("week 1", planned["42"].parts)

    def test_only_assignments_keep_canvas_folders_and_flat_collisions_are_suffixed(self) -> None:
        service = self.make_service()
        assignment = replace(
            self.context("assignment", "task.pdf"),
            category="assignments",
            folder_names=("Week 1", "Deep"),
        )
        courseware = replace(
            self.context("slides", "same.pdf"),
            category="courseware",
            folder_names=("course files", "Week 1", "Deep"),
        )
        other = replace(
            self.context("reference", "same.pdf"),
            category="supplementary",
            folder_names=("course files", "References", "Deep"),
        )
        duplicate = replace(
            self.context("notes", "same.pdf"),
            category="other",
            folder_names=("course files", "Notes"),
        )

        planned = service._planned_paths([assignment, courseware, other, duplicate])

        self.assertEqual(
            ("2026-2027 Fall", "文本_分析", "课程作业", "week 1", "deep", "task.pdf"),
            planned["assignment"].relative_to(self.root).parts,
        )
        self.assertEqual(
            ("2026-2027 Fall", "文本_分析", "课件", "same.pdf"),
            planned["slides"].relative_to(self.root).parts,
        )
        self.assertEqual("same [reference].pdf", planned["reference"].name)
        self.assertEqual("same [notes].pdf", planned["notes"].name)
        self.assertEqual("其他", planned["reference"].parent.name)
        self.assertNotIn("References", planned["reference"].parts)

    def test_same_source_id_keeps_one_unsuffixed_target(self) -> None:
        service = self.make_service()
        first = self.context("42")
        duplicate = replace(first, folder_names=("different",))
        planned = service._planned_paths((first, duplicate))
        self.assertEqual("lecture.pdf", planned.get("42").name)

    def test_old_local_path_outside_new_root_is_never_touched(self) -> None:
        service = self.make_service()
        outside = self.root.parent / f"outside-{self.root.name}.pdf"
        outside.write_bytes(b"outside")
        self.addCleanup(outside.unlink, missing_ok=True)
        context = ArchiveFileContext(
            **{**self.context().__dict__, "local_path": str(outside)}
        )
        target = service._planned_paths([context])[context.source_id]
        service._download_one(context, target)
        self.assertEqual(b"outside", outside.read_bytes())
        self.assertEqual(b"content", target.read_bytes())

    def test_changed_file_preserves_old_version_before_atomic_replace(self) -> None:
        service = self.make_service()
        context = self.context()
        target = service._planned_paths([context])[context.source_id]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old")
        context = replace(context, local_path=str(target))
        result = service._download_one(context, target)
        self.assertEqual("downloaded", result.status)
        self.assertEqual(b"content", target.read_bytes())
        versions = list((target.parent / ".versions").iterdir())
        self.assertEqual(1, len(versions))
        self.assertEqual(b"old", versions[0].read_bytes())

    def test_auto_term_ambiguity_is_printable_safe_skip(self) -> None:
        service = self.make_service()
        service.current_term = None
        service.requested_term = False
        match_sets = (tuple(), ("2026-2027 Fall", "2026-2027  Fall"))
        for matches in match_sets:
            with patch.object(
                service, "_matching_term_names", return_value=list(matches)
            ), patch.object(service, "_load_contexts") as load_contexts:
                summary = service.archive_current_term()
            self.assertTrue(summary.skipped)
            self.assertIsNotNone(summary.message)
            self.assertIn("安全跳过", summary.message)
            load_contexts.assert_not_called()

    def test_explicit_missing_term_fails_without_download(self) -> None:
        service = self.make_service()
        with patch.object(
            service, "_matching_term_names", return_value=list()
        ), patch.object(service, "_load_contexts") as load_contexts:
            with self.assertRaisesRegex(ArchiveError, "指定学期不存在"):
                service.archive_current_term()
            load_contexts.assert_not_called()

    def test_attempt_counter_is_cumulative_and_success_does_not_reset_it(self) -> None:
        record = SimpleNamespace(
            download_attempts=2, download_status="failed", download_error="old"
        )

        class FakeSession:
            def __init__(self, _engine):
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def begin(self):
                return self

            def scalar(self, _statement):
                return record

        service = self.make_service()
        with patch("sjtu_learning_assistant.archive_service.Session", FakeSession):
            ArchiveService._record_attempt(service, "42")
            self.assertEqual(3, record.download_attempts)
            self.assertEqual("pending", record.download_status)
            self.assertIsNone(record.download_error)
            ArchiveService._write_status(
                service,
                "42",
                status="downloaded",
                local_path="safe.pdf",
                size=7,
                sha256=hashlib.sha256(b"content").hexdigest(),
                source_updated_at=self.context().source_updated_at,
            )
        self.assertEqual(3, record.download_attempts)
        self.assertEqual("downloaded", record.download_status)

    def test_current_term_auto_scope_and_explicit_cross_term_api(self) -> None:
        service = self.make_service()
        current = self.context("42")
        old = ArchiveFileContext(
            **{**current.__dict__, "source_id": "41", "term_name": "2025-2026 Fall"}
        )
        service._load_contexts = lambda **kwargs: (
            [current] if kwargs.get("term_name") == "2026-2027 Fall" else [old]
        )
        with patch.object(
            service, "_matching_term_names", return_value=["2026-2027 Fall"]
        ), patch.object(service, "_download_contexts", return_value=[]):
            summary = service.archive_current_term()
        self.assertEqual("2026-2027 Fall", summary.term_name)
        with patch.object(service, "_planned_paths", return_value={"41": self.root / "old.pdf"}), patch.object(
            service,
            "_download_one",
            return_value=SimpleNamespace(source_id="41", status="unchanged"),
        ):
            result = service.download_file_by_source_id("41")
        self.assertEqual("41", result.source_id)


if __name__ == "__main__":
    unittest.main()
