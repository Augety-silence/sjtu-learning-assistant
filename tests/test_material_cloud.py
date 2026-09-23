from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.cloud_storage import CloudItem
from sjtu_learning_assistant.cloud_storage.base import TemporaryDownload
from sjtu_learning_assistant.dashboard_service import DashboardError, DashboardService
from sjtu_learning_assistant.models import Base, Course, CourseFile

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


class FakeCloudProvider:
    def __init__(
        self,
        remote: dict[tuple[str, ...], bytes],
        *,
        fail_move: bool = False,
        wrong_size_after_move: bool = False,
    ):
        self.remote = remote
        self.fail_move = fail_move
        self.wrong_size_after_move = wrong_size_after_move
        self.moves: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        self.closed = False

    def ensure_directory(self, _path):
        return None

    def get_info(self, path):
        normalized = tuple(path)
        payload = self.remote[normalized]
        size = len(payload)
        if (
            self.wrong_size_after_move
            and len(self.moves) == 1
            and normalized == self.moves[-1][1]
        ):
            size += 1
        return CloudItem(normalized[-1], normalized, False, size)

    def download_stream(self, path, **_kwargs):
        yield self.remote[tuple(path)]

    def download_temp(self, path, *, directory=None):
        target = Path(directory) / "downloaded.part"
        target.write_bytes(self.remote[tuple(path)])
        return TemporaryDownload(target)

    def move(self, source, destination, *, overwrite=False):
        if self.fail_move:
            raise RuntimeError("remote unavailable")
        source_path = tuple(source)
        destination_path = tuple(destination)
        if destination_path in self.remote and not overwrite:
            raise RuntimeError("conflict")
        payload = self.remote.pop(source_path)
        self.remote[destination_path] = payload
        self.moves.append((source_path, destination_path))
        return CloudItem(
            destination_path[-1], destination_path, False, len(payload)
        )

    def close(self):
        self.closed = True


class MaterialCloudTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.archive = root / "archive"
        self.archive.mkdir()
        self.engine = create_engine(f"sqlite+pysqlite:///{root / 'material.db'}")
        Base.metadata.create_all(self.engine)
        self.old_path = (
            "SJTU Learning Assistant",
            "Canvas",
            "2026 Fall",
            "自然语言处理",
            "课件",
            "讲义--old.pdf",
        )
        with Session(self.engine) as session, session.begin():
            course = Course(
                source_id="course-1",
                name="自然语言处理",
                term_name="2026 Fall",
                raw_data={},
            )
            session.add(course)
            session.flush()
            session.add(
                CourseFile(
                    source_id="file-1",
                    course_id=course.id,
                    display_name="讲义.pdf",
                    filename="讲义.pdf",
                    content_type="application/pdf",
                    size=3,
                    local_path=None,
                    cloud_path="/".join(self.old_path),
                    cloud_size=3,
                    cloud_backed_up_at=NOW,
                    download_status="cloud_only",
                    ai_category="courseware",
                    is_active=True,
                    last_seen_at=NOW,
                    raw_data={},
                )
            )
        self.commands: list[list[str]] = []

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def service(self, provider: FakeCloudProvider) -> DashboardService:
        return DashboardService(
            self.engine,
            archive_root=self.archive,
            cloud_provider_factory=lambda: provider,
            command_runner=lambda command, **_kwargs: self.commands.append(list(command))
            or SimpleNamespace(returncode=0),
        )

    def record(self) -> CourseFile:
        with Session(self.engine) as session:
            return session.scalar(
                select(CourseFile).where(CourseFile.source_id == "file-1")
            )

    def test_cloud_pdf_preview_is_bounded_dto_without_path_or_url(self) -> None:
        provider = FakeCloudProvider({self.old_path: b"pdf"})
        service = self.service(provider)
        try:
            result = service.material_preview("file-1")
        finally:
            service.close()
        self.assertEqual("pdf", result["kind"])
        self.assertEqual("讲义.pdf", result["name"])
        self.assertEqual("data:application/pdf;base64,cGRm", result["data_url"])
        self.assertNotIn("cloud_path", result)
        self.assertNotIn("token", str(result).casefold())
        self.assertTrue(provider.closed)

    def test_cloud_office_open_uses_managed_temp_and_shutdown_cleans_it(self) -> None:
        with Session(self.engine) as session, session.begin():
            record = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "file-1")
            )
            record.display_name = "课件.docx"
            record.filename = "课件.docx"
            record.content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        provider = FakeCloudProvider({self.old_path: b"pdf"})
        service = self.service(provider)
        temporary_root = Path(service._material_temp.name)
        result = service.open_material("file-1")
        opened_path = Path(self.commands[-1][-1])
        self.assertEqual("opened", result["status"])
        self.assertTrue(opened_path.is_file())
        self.assertEqual(temporary_root, opened_path.parent)
        service.close()
        self.assertFalse(temporary_root.exists())

    def test_cloud_move_failure_keeps_cloud_path_and_manual_state(self) -> None:
        provider = FakeCloudProvider({self.old_path: b"pdf"}, fail_move=True)
        service = self.service(provider)
        try:
            with self.assertRaisesRegex(DashboardError, "原归档状态已保留"):
                service.move_material("file-1", "category:course-1:assignments")
        finally:
            service.close()
        record = self.record()
        self.assertEqual("/".join(self.old_path), record.cloud_path)
        self.assertFalse(record.manual_override)
        self.assertIsNone(record.manual_category)
        self.assertIsNone(record.local_path)

    def test_cloud_move_verification_failure_rolls_remote_path_back(self) -> None:
        provider = FakeCloudProvider(
            {self.old_path: b"pdf"}, wrong_size_after_move=True
        )
        service = self.service(provider)
        try:
            with self.assertRaisesRegex(DashboardError, "校验失败"):
                service.move_material("file-1", "category:course-1:assignments")
        finally:
            service.close()
        record = self.record()
        self.assertEqual("/".join(self.old_path), record.cloud_path)
        self.assertFalse(record.manual_override)
        self.assertIn(self.old_path, provider.remote)
        self.assertEqual(2, len(provider.moves))
        self.assertEqual(provider.moves[0], tuple(reversed(provider.moves[1])))

    def test_database_failure_after_cloud_move_rolls_remote_path_back(self) -> None:
        provider = FakeCloudProvider({self.old_path: b"pdf"})
        service = self.service(provider)
        try:
            with patch(
                "sjtu_learning_assistant.archive_service.ArchiveService.move_file_by_source_id",
                side_effect=RuntimeError("database unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                    service.move_material(
                        "file-1", "category:course-1:assignments"
                    )
        finally:
            service.close()
        record = self.record()
        self.assertEqual("/".join(self.old_path), record.cloud_path)
        self.assertFalse(record.manual_override)
        self.assertIn(self.old_path, provider.remote)
        self.assertEqual(2, len(provider.moves))

    def test_cloud_move_verifies_target_then_commits_cloud_and_manual_state(self) -> None:
        provider = FakeCloudProvider({self.old_path: b"pdf"})
        service = self.service(provider)
        try:
            result = service.move_material(
                "file-1", "category:course-1:assignments"
            )
        finally:
            service.close()
        self.assertEqual("saved", result["status"])
        record = self.record()
        self.assertTrue(record.manual_override)
        self.assertEqual("assignments", record.manual_category)
        self.assertEqual("cloud_only", record.download_status)
        self.assertNotEqual("/".join(self.old_path), record.cloud_path)
        self.assertIn("/课程作业/", record.cloud_path)
        self.assertEqual(1, len(provider.moves))


if __name__ == "__main__":
    unittest.main()
