from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sjtu_learning_assistant.backup_service import (
    BACKUP_ROOT,
    BackupCandidate,
    BackupManager,
    BackupService,
    safe_path_component,
    stable_filename,
)
from sjtu_learning_assistant.cloud_storage import CloudItem
from sjtu_learning_assistant.models import Base, Course, CourseFile, Email, EmailAttachment


class FakeProvider:
    def __init__(self) -> None:
        self.remote: dict[tuple[str, ...], CloudItem] = {}
        self.directories: list[tuple[str, ...]] = []
        self.simple: list[tuple[tuple[str, ...], bytes, bool]] = []
        self.multipart: list[tuple[tuple[str, ...], bytes, bool]] = []
        self.fail_paths: set[tuple[str, ...]] = set()
        self.closed = False

    def ensure_directory(self, path):
        self.directories.append(tuple(path))

    def create_directory(self, path):
        self.directories.append(tuple(path))

    def exists(self, path):
        return tuple(path) in self.remote

    def get_info(self, path):
        return self.remote[tuple(path)]

    def simple_upload(self, path, source, *, overwrite=False):
        normalized = tuple(path)
        if normalized in self.fail_paths:
            raise RuntimeError("token=provider-secret https://signed.example/private?signature=secret")
        payload = source.read()
        self.simple.append((normalized, payload, overwrite))
        item = CloudItem(normalized[-1], normalized, False, len(payload))
        self.remote[normalized] = item
        return item

    def multipart_upload(self, path, source, *, overwrite=False, chunk_size=4 * 1024 * 1024):
        normalized = tuple(path)
        if normalized in self.fail_paths:
            raise RuntimeError("token=provider-secret")
        payload = source.read()
        self.multipart.append((normalized, payload, overwrite))
        item = CloudItem(normalized[-1], normalized, False, len(payload))
        self.remote[normalized] = item
        return item

    def close(self):
        self.closed = True


class BlockingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.upload_entered = threading.Event()
        self.release = threading.Event()

    def simple_upload(self, path, source, *, overwrite=False):
        self.upload_entered.set()
        self.release.wait(2)
        return super().simple_upload(path, source, overwrite=overwrite)


class BackupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.archive = root / "archive"
        self.mail = root / "mail-attachments"
        self.archive.mkdir()
        self.mail.mkdir()
        self.engine = create_engine(f"sqlite+pysqlite:///{root / 'backup.db'}")
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def _add_records(self, *, canvas_path: str | None, mail_path: str | None) -> None:
        now = datetime(2026, 9, 23, 3, 4, tzinfo=timezone.utc)
        with Session(self.engine) as session:
            course = Course(
                source_id="course/1",
                name="Course / Name",
                course_code="CS\\101",
                term_name="2026/秋\x01",
                raw_data={},
            )
            session.add(course)
            session.flush()
            session.add_all(
                [
                    CourseFile(
                        source_id="canvas-1",
                        course_id=course.id,
                        display_name="lecture.pdf",
                        filename="lecture.pdf",
                        local_path=canvas_path,
                        is_active=True,
                        last_seen_at=now,
                        raw_data={},
                    ),
                    CourseFile(
                        source_id="canvas-inactive",
                        course_id=course.id,
                        display_name="old.pdf",
                        local_path=canvas_path,
                        is_active=False,
                        last_seen_at=now,
                        raw_data={},
                    ),
                ]
            )
            email = Email(
                source_id="mail-secret-id",
                subject="subject",
                sent_at=now,
                raw_data={},
            )
            session.add(email)
            session.flush()
            session.add(
                EmailAttachment(
                    email_id=email.id,
                    resource_id="attachment/1",
                    filename="report/../final.txt",
                    content_type="text/plain",
                    size=4,
                    local_path=mail_path,
                )
            )
            session.commit()

    def _service(self, provider=None, *, threshold=8 * 1024 * 1024) -> BackupService:
        return BackupService(
            self.engine,
            provider,
            archive_root=self.archive,
            mail_attachments_root=self.mail,
            mail_account="student/name@example.edu",
            multipart_threshold=threshold,
        )

    def test_path_cleaning_and_filename_disambiguation_are_stable(self) -> None:
        traversal = safe_path_component("../..\x00")
        self.assertNotIn("/", traversal)
        self.assertNotIn("\\", traversal)
        self.assertNotIn("\x00", traversal)
        cleaned = safe_path_component(" a/b\\c\x01 " + "x" * 300)
        self.assertNotIn("/", cleaned)
        self.assertNotIn("\\", cleaned)
        self.assertTrue(all(ord(character) >= 32 for character in cleaned))
        self.assertLessEqual(len(cleaned), 120)
        first = stable_filename("same/report.pdf", "record-one")
        self.assertEqual(first, stable_filename("same/report.pdf", "record-one"))
        self.assertNotEqual(first, stable_filename("same/report.pdf", "record-two"))
        self.assertTrue(first.endswith(".pdf"))

    def test_scan_all_active_canvas_and_all_mail_with_safe_remote_paths(self) -> None:
        canvas_file = self.archive / "lecture.pdf"
        mail_directory = self.mail / "message"
        mail_directory.mkdir()
        mail_file = mail_directory / "attachment.txt"
        canvas_file.write_bytes(b"canvas")
        mail_file.write_bytes(b"mail")
        self._add_records(canvas_path=str(canvas_file), mail_path=str(mail_file))

        candidates = self._service().scan()
        self.assertEqual(2, len(candidates))
        canvas = next(item for item in candidates if item.source == "canvas")
        mail = next(item for item in candidates if item.source == "mail")
        self.assertEqual((BACKUP_ROOT, "Canvas"), canvas.remote_path[:2])
        self.assertEqual((BACKUP_ROOT, "Mail"), mail.remote_path[:2])
        self.assertEqual("2026-09", mail.remote_path[3])
        self.assertTrue(all(part not in {".", ".."} and "/" not in part and "\\" not in part for item in candidates for part in item.remote_path))
        self.assertEqual(
            {"canvas": 1, "mail": 1, "ready": 2, "missing_local": 0, "total": 2},
            self._service().preview(),
        )
        serialized = json.dumps([item.dto() for item in candidates], ensure_ascii=False)
        self.assertNotIn(str(self.archive), serialized)
        self.assertNotIn(str(self.mail), serialized)
        self.assertNotIn("mail-secret-id", serialized)

    def test_missing_and_symlink_files_are_skipped_missing_local(self) -> None:
        outside = Path(self.temporary.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        link = self.archive / "link.txt"
        link.symlink_to(outside)
        self._add_records(canvas_path=str(link), mail_path=str(self.mail / "missing.txt"))
        provider = FakeProvider()

        candidates = self._service(provider).scan()
        self.assertTrue(all(not candidate.ready for candidate in candidates))
        result = self._service(provider).backup(candidates)
        self.assertEqual(2, result["skipped_missing_local"])
        self.assertEqual(0, result["failed"])
        self.assertFalse(provider.simple or provider.multipart)

    def test_existing_same_size_overwrite_simple_and_multipart(self) -> None:
        files: list[Path] = []
        for name, payload in (
            ("same.bin", b"same"),
            ("overwrite.bin", b"newer"),
            ("new.bin", b"new"),
            ("large.bin", b"0123456789"),
        ):
            path = self.archive / name
            path.write_bytes(payload)
            files.append(path)
        candidates = tuple(
            BackupCandidate(
                key=f"item-{index}",
                source="canvas",
                remote_path=(BACKUP_ROOT, "Canvas", "term", "course", path.name),
                local_path=str(path),
                local_root=self.archive,
                ready=True,
            )
            for index, path in enumerate(files)
        )
        provider = FakeProvider()
        provider.remote[candidates[0].remote_path] = CloudItem("same.bin", candidates[0].remote_path, False, 4)
        provider.remote[candidates[1].remote_path] = CloudItem("overwrite.bin", candidates[1].remote_path, False, 1)

        result = self._service(provider, threshold=8).backup(candidates)
        self.assertEqual(1, result["skipped_existing"])
        self.assertEqual(3, result["uploaded"])
        self.assertEqual(2, len(provider.simple))
        self.assertEqual(1, len(provider.multipart))
        self.assertNotIn("items", result)
        self.assertEqual([], result["failures"])
        overwrite_call = next(call for call in provider.simple if call[0] == candidates[1].remote_path)
        self.assertTrue(overwrite_call[2])
        self.assertEqual(candidates[3].remote_path, provider.multipart[0][0])
        self.assertGreaterEqual(len(provider.directories), 1)

    def test_single_file_failure_isolated_and_result_has_no_secrets_or_paths(self) -> None:
        first = self.archive / "first.txt"
        second = self.archive / "second.txt"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")
        candidates = tuple(
            BackupCandidate(
                key=f"safe-{index}",
                source="canvas",
                remote_path=(BACKUP_ROOT, "Canvas", "term", "course", path.name),
                local_path=str(path),
                local_root=self.archive,
                ready=True,
            )
            for index, path in enumerate((first, second))
        )
        provider = FakeProvider()
        provider.fail_paths.add(candidates[0].remote_path)

        result = self._service(provider).backup(candidates)
        self.assertEqual(1, result["failed"])
        self.assertEqual(1, result["uploaded"])
        self.assertNotIn("items", result)
        self.assertEqual(1, len(result["failures"]))
        failure = result["failures"][0]
        self.assertEqual({"source", "name", "remote_path", "error"}, set(failure))
        self.assertEqual("canvas", failure["source"])
        self.assertEqual(candidates[0].remote_path[-1], failure["name"])
        self.assertEqual("/".join(candidates[0].remote_path), failure["remote_path"])
        self.assertIsInstance(failure["remote_path"], str)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(str(self.archive), serialized)
        self.assertNotIn("provider-secret", serialized)
        self.assertNotIn("signed.example", serialized)
        self.assertNotIn("signature", serialized)

    def test_file_replaced_after_preview_is_missing_not_failed(self) -> None:
        path = self.archive / "gone.txt"
        path.write_text("gone", encoding="utf-8")
        candidate = BackupCandidate(
            key="gone",
            source="canvas",
            remote_path=(BACKUP_ROOT, "Canvas", "term", "course", "gone.txt"),
            local_path=str(path),
            local_root=self.archive,
            ready=True,
        )
        path.unlink()
        result = self._service(FakeProvider()).backup((candidate,))
        self.assertEqual(1, result["skipped_missing_local"])
        self.assertEqual(0, result["failed"])

    def _wait_for_status(self, manager: BackupManager, expected: str, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = manager.status()
            if status["status"] == expected:
                return status
            time.sleep(0.01)
        self.fail(f"backup manager did not reach {expected}")

    def test_manager_idle_running_finished_progress_and_second_start(self) -> None:
        canvas_file = self.archive / "lecture.pdf"
        canvas_file.write_bytes(b"canvas")
        self._add_records(canvas_path=str(canvas_file), mail_path=None)
        first_provider = BlockingProvider()
        providers = [first_provider, FakeProvider()]
        manager = BackupManager(
            self.engine,
            archive_root=self.archive,
            mail_attachments_root=self.mail,
            mail_account="student",
            provider_factory=lambda: providers.pop(0),
            token_loader=lambda: "configured-but-never-returned",
        )
        idle = manager.status()
        self.assertEqual("idle", idle["status"])
        self.assertTrue(idle["available"])
        self.assertIsNone(idle["availability_message"])
        self.assertEqual(2, idle["counts"]["total"])
        self.assertEqual(1, idle["counts"]["missing_local"])
        self.assertIsNone(idle["progress"])
        self.assertNotIn("configured-but-never-returned", json.dumps(idle, ensure_ascii=False))

        self.assertEqual("started", manager.start()["status"])
        self.assertTrue(first_provider.upload_entered.wait(1))
        self.assertEqual("already_running", manager.start()["status"])
        running = manager.status()
        self.assertEqual("running", running["status"])
        self.assertEqual(0, running["progress"]["done"])
        self.assertEqual(2, running["progress"]["total"])
        self.assertEqual(Path(running["progress"]["current_name"]).name, running["progress"]["current_name"])
        self.assertNotIn(str(self.archive), running["progress"]["current_name"])

        first_provider.release.set()
        finished = self._wait_for_status(manager, "finished")
        self.assertEqual(2, finished["progress"]["done"])
        self.assertEqual(2, finished["progress"]["total"])
        self.assertEqual(
            {
                "started_at",
                "finished_at",
                "uploaded",
                "skipped_existing",
                "skipped_missing_local",
                "failed",
                "failures",
            },
            set(finished["last_result"]),
        )
        self.assertTrue(first_provider.closed)

        self.assertEqual("started", manager.start()["status"])
        second_finished = self._wait_for_status(manager, "finished")
        self.assertEqual(1, second_finished["last_result"]["uploaded"])
        manager.close(timeout=1)
        self.assertIn(manager.status()["status"], {"idle", "running", "finished"})

    def test_status_without_pan_token_is_safe_and_does_not_construct_provider(self) -> None:
        provider_factory_called = False

        def provider_factory():
            nonlocal provider_factory_called
            provider_factory_called = True
            raise AssertionError("status must not construct provider")

        manager = BackupManager(
            self.engine,
            archive_root=self.archive,
            mail_attachments_root=self.mail,
            provider_factory=provider_factory,
            token_loader=lambda: None,
        )
        status = manager.status()
        self.assertFalse(status["available"])
        self.assertIsInstance(status["availability_message"], str)
        self.assertNotIn("token_loader", json.dumps(status, ensure_ascii=False))
        self.assertFalse(provider_factory_called)
        manager.close()

    def test_manager_fatal_error_uses_complete_sanitized_result_contract(self) -> None:
        sensitive_value = "runtime-token-secret"
        manager = BackupManager(
            self.engine,
            archive_root=self.archive,
            mail_attachments_root=self.mail,
            provider_factory=lambda: (_ for _ in ()).throw(
                RuntimeError(f"token={sensitive_value}")
            ),
            token_loader=lambda: "configured",
        )
        self.assertTrue(manager.status()["available"])
        self.assertEqual("started", manager.start()["status"])
        finished = self._wait_for_status(manager, "finished")
        result = finished["last_result"]
        self.assertGreaterEqual(result["failed"], 1)
        self.assertEqual(1, len(result["failures"]))
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(sensitive_value, serialized)
        self.assertNotIn("items", result)
        manager.close()

    def test_backend_status_dto_mirrors_frontend_typescript_contract(self) -> None:
        types_path = Path(__file__).parents[1] / "dashboard-web" / "src" / "lib" / "types.ts"
        source = types_path.read_text(encoding="utf-8")

        def fields(interface: str) -> set[str]:
            match = re.search(rf"export interface {interface} \{{(?P<body>.*?)\n\}}", source, re.S)
            self.assertIsNotNone(match, f"missing TypeScript interface {interface}")
            return set(re.findall(r"^\s*(\w+)\??:", match.group("body"), re.M))

        status_fields = fields("BackupStatus")
        count_fields = fields("BackupCounts")
        progress_fields = fields("BackupProgress")
        result_fields = fields("BackupResult")
        failure_fields = fields("BackupFailure")
        self.assertEqual(
            {"status", "available", "availability_message", "counts", "progress", "last_result"},
            status_fields,
        )
        self.assertEqual(
            {"canvas", "mail", "ready", "missing_local", "total"},
            count_fields,
        )
        self.assertEqual({"done", "total", "current_name"}, progress_fields)
        self.assertEqual(
            {
                "started_at",
                "finished_at",
                "uploaded",
                "skipped_existing",
                "skipped_missing_local",
                "failed",
                "failures",
            },
            result_fields,
        )
        self.assertEqual({"source", "name", "remote_path", "error"}, failure_fields)
        status_body = re.search(r"export interface BackupStatus \{(?P<body>.*?)\n\}", source, re.S)
        self.assertIsNotNone(status_body)
        self.assertIn('status: "idle" | "running" | "finished";', status_body.group("body"))

        manager = BackupManager(
            self.engine,
            archive_root=self.archive,
            mail_attachments_root=self.mail,
            token_loader=lambda: None,
        )
        backend_status = manager.status()
        self.assertEqual(status_fields, set(backend_status))
        self.assertEqual(count_fields, set(backend_status["counts"]))
        manager.close()


if __name__ == "__main__":
    unittest.main()
