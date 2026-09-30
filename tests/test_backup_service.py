from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from unittest.mock import patch

from sqlalchemy import create_engine, select
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
from sjtu_learning_assistant.models import (
    Base,
    Course,
    CourseFile,
    CourseFolder,
    Email,
    EmailAttachment,
)
from sjtu_learning_assistant.transcript_service import ARTIFACT_FILES, V2_ARTIFACT_FILES


class FakeProvider:
    def __init__(self) -> None:
        self.remote: dict[tuple[str, ...], CloudItem] = {}
        self.payloads: dict[tuple[str, ...], bytes] = {}
        self.directories: list[tuple[str, ...]] = []
        self.simple: list[tuple[tuple[str, ...], bytes, bool]] = []
        self.multipart: list[tuple[tuple[str, ...], bytes, bool]] = []
        self.fail_paths: set[tuple[str, ...]] = set()
        self.info_requests: list[tuple[str, ...]] = []
        self.closed = False

    def ensure_directory(self, path):
        self.directories.append(tuple(path))

    def create_directory(self, path):
        self.directories.append(tuple(path))

    def exists(self, path):
        return tuple(path) in self.remote

    def get_info(self, path):
        normalized = tuple(path)
        self.info_requests.append(normalized)
        return self.remote[normalized]

    def simple_upload(self, path, source, *, overwrite=False):
        normalized = tuple(path)
        if normalized in self.fail_paths:
            raise RuntimeError("token=provider-secret https://signed.example/private?signature=secret")
        payload = source.read()
        self.simple.append((normalized, payload, overwrite))
        item = CloudItem(normalized[-1], normalized, False, len(payload))
        self.remote[normalized] = item
        self.payloads[normalized] = payload
        return item

    def multipart_upload(self, path, source, *, overwrite=False, chunk_size=4 * 1024 * 1024):
        normalized = tuple(path)
        if normalized in self.fail_paths:
            raise RuntimeError("token=provider-secret")
        payload = source.read()
        self.multipart.append((normalized, payload, overwrite))
        item = CloudItem(normalized[-1], normalized, False, len(payload))
        self.remote[normalized] = item
        self.payloads[normalized] = payload
        return item

    @contextmanager
    def download_temp(self, path):
        normalized = tuple(path)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "download"
            target.write_bytes(self.payloads[normalized])
            yield target

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
        self.transcripts = root / "transcripts"
        self.archive.mkdir()
        self.mail.mkdir()
        (self.transcripts / "videos").mkdir(parents=True)
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
            transcript_root=self.transcripts,
        )

    def _add_transcript(self, *, training_examples: bool = True) -> tuple[Path, dict[str, bytes]]:
        source_id = "sjtu-video:12:99"
        video_directory = hashlib.sha256(source_id.encode()).hexdigest()[:32]
        video_dir = self.transcripts / "videos" / video_directory
        v2_dir = video_dir / "v2"
        v2_dir.mkdir(parents=True)
        v1_payloads = {
            filename: f"v1:{kind}\n".encode()
            for kind, filename in ARTIFACT_FILES.items()
        }
        v2_payloads = {
            filename: f"v2:{kind}\n".encode()
            for kind, filename in V2_ARTIFACT_FILES.items()
            if training_examples or kind != "training_examples"
        }
        for filename, payload in v1_payloads.items():
            (video_dir / filename).write_bytes(payload)
        for filename, payload in v2_payloads.items():
            (v2_dir / filename).write_bytes(payload)
        v2_records = {
            kind: {
                "id": hashlib.sha256(kind.encode()).hexdigest()[:32],
                "path": filename,
                "sha256": hashlib.sha256(v2_payloads[filename]).hexdigest(),
                "size": len(v2_payloads[filename]),
            }
            for kind, filename in V2_ARTIFACT_FILES.items()
            if filename in v2_payloads
        }
        v2_manifest = {
            "schema_version": 1,
            "course_id": "12",
            "video_id": "99",
            "artifacts": v2_records,
        }
        (v2_dir / "manifest.json").write_text(
            json.dumps(v2_manifest, ensure_ascii=False), encoding="utf-8"
        )
        v1_manifest = {
            "schema_version": 2,
            "source": {"source_id": source_id, "type": "video_space"},
            "course": {"id": "12", "name": "课程/安全"},
            "video": {"title": "第 3 周\\课程"},
            "artifacts": {
                kind: {
                    "path": filename,
                    "sha256": hashlib.sha256(v1_payloads[filename]).hexdigest(),
                }
                for kind, filename in ARTIFACT_FILES.items()
            },
        }
        (video_dir / "manifest.json").write_text(
            json.dumps(v1_manifest, ensure_ascii=False), encoding="utf-8"
        )
        return video_dir, {**v1_payloads, **v2_payloads}

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
            {
                "canvas": 1,
                "mail": 1,
                "ready": 2,
                "cloud_only": 0,
                "missing_local": 0,
                "total": 2,
            },
            self._service().preview(),
        )
        serialized = json.dumps([item.dto() for item in candidates], ensure_ascii=False)
        self.assertNotIn(str(self.archive), serialized)
        self.assertNotIn(str(self.mail), serialized)
        self.assertNotIn("mail-secret-id", serialized)

    def test_scan_transcript_manifests_validates_hash_size_and_orders_manifests_last(self) -> None:
        _video_dir, _payloads = self._add_transcript()

        candidates = tuple(
            item for item in self._service().scan() if item.source == "transcript"
        )

        expected_names = [
            *ARTIFACT_FILES.values(),
            *V2_ARTIFACT_FILES.values(),
            "manifest.json",
            "manifest.json",
        ]
        self.assertEqual(expected_names, [item.remote_path[-1] for item in candidates])
        self.assertEqual(
            ["v1"] * len(ARTIFACT_FILES)
            + ["v2"] * (len(V2_ARTIFACT_FILES) + 1)
            + ["v1"],
            [item.remote_path[-2] for item in candidates],
        )
        self.assertTrue(all(item.ready for item in candidates))
        self.assertTrue(all(item.sha256 and item.expected_size is not None for item in candidates))
        self.assertEqual("manifest.json", candidates[-1].remote_path[-1])
        self.assertEqual("v1", candidates[-1].remote_path[-2])
        self.assertEqual("manifest.json", candidates[-2].remote_path[-1])
        self.assertEqual("v2", candidates[-2].remote_path[-2])
        self.assertTrue(
            all(
                part not in {".", ".."} and "/" not in part and "\\" not in part
                for item in candidates
                for part in item.remote_path
            )
        )

    def test_transcript_backup_is_hash_verified_idempotent_and_never_removed(self) -> None:
        video_dir, _payloads = self._add_transcript(training_examples=False)
        provider = FakeProvider()
        service = self._service(provider)
        candidates = tuple(item for item in service.scan() if item.source == "transcript")

        first = service.backup(candidates, remove_local=True)
        self.assertEqual(len(candidates), first["uploaded"])
        self.assertEqual(0, first["failed"])
        self.assertEqual(0, first["local_removed"])
        self.assertTrue((video_dir / "raw.vtt").is_file())
        self.assertTrue((video_dir / "v2" / "quality.json").is_file())
        self.assertEqual("manifest.json", provider.simple[-1][0][-1])
        self.assertEqual("v1", provider.simple[-1][0][-2])

        uploads = len(provider.simple) + len(provider.multipart)
        second = service.backup(candidates, remove_local=True)
        self.assertEqual(0, second["uploaded"])
        self.assertEqual(len(candidates), second["skipped_existing"])
        self.assertEqual(uploads, len(provider.simple) + len(provider.multipart))

        raw = next(item for item in candidates if item.remote_path[-1] == "raw.vtt")
        provider.payloads[raw.remote_path] = b"x" * raw.expected_size
        third = service.backup(candidates)
        self.assertEqual(1, third["uploaded"])
        repaired = next(call for call in provider.simple if call[0] == raw.remote_path and call[2])
        self.assertEqual((video_dir / "raw.vtt").read_bytes(), repaired[1])

    def test_transcript_failure_keeps_local_and_defers_its_manifest_until_retry(self) -> None:
        video_dir, _payloads = self._add_transcript()
        provider = FakeProvider()
        service = self._service(provider)
        candidates = tuple(item for item in service.scan() if item.source == "transcript")
        failed = next(item for item in candidates if item.remote_path[-1] == "quality.json")
        v2_manifest = next(
            item
            for item in candidates
            if item.transcript_manifest and item.remote_path[-2] == "v2"
        )
        provider.fail_paths.add(failed.remote_path)

        first = service.backup(candidates, remove_local=True)

        self.assertEqual(1, first["failed"])
        self.assertEqual(0, first["local_removed"])
        self.assertNotIn(v2_manifest.remote_path, provider.remote)
        self.assertTrue((video_dir / "v2" / "quality.json").is_file())
        self.assertTrue((video_dir / "manifest.json").is_file())

        provider.fail_paths.clear()
        second = service.backup(candidates, remove_local=True)
        self.assertEqual(0, second["failed"])
        self.assertIn(v2_manifest.remote_path, provider.remote)
        self.assertTrue((video_dir / "v2" / "quality.json").is_file())

    def test_transcript_scan_rejects_symlink_and_manifest_size_or_hash_mismatch(self) -> None:
        video_dir, _payloads = self._add_transcript()
        quality = video_dir / "v2" / "quality.json"
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_bytes(quality.read_bytes())
        quality.unlink()
        quality.symlink_to(outside)
        candidates = tuple(
            item for item in self._service().scan() if item.source == "transcript"
        )
        self.assertEqual(
            [*ARTIFACT_FILES.values(), "manifest.json"],
            [item.remote_path[-1] for item in candidates],
        )
        self.assertTrue(all(item.remote_path[-2] == "v1" for item in candidates))

        quality.unlink()
        quality.write_bytes(outside.read_bytes())
        v2_manifest_path = video_dir / "v2" / "manifest.json"
        manifest = json.loads(v2_manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"]["quality"]["size"] += 1
        v2_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        candidates = tuple(
            item for item in self._service().scan() if item.source == "transcript"
        )
        self.assertEqual(
            [*ARTIFACT_FILES.values(), "manifest.json"],
            [item.remote_path[-1] for item in candidates],
        )

        manifest["artifacts"]["quality"]["size"] -= 1
        v2_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        v1_manifest_path = video_dir / "manifest.json"
        v1_manifest = json.loads(v1_manifest_path.read_text(encoding="utf-8"))
        v1_manifest["artifacts"]["raw_vtt"]["path"] = "../raw.vtt"
        v1_manifest_path.write_text(json.dumps(v1_manifest), encoding="utf-8")
        self.assertEqual(
            (), tuple(item for item in self._service().scan() if item.source == "transcript")
        )

    def test_canvas_remote_path_matches_material_tree_placement_and_display_name(self) -> None:
        local = self.archive / "讲义.pdf"
        local.write_bytes(b"canvas")
        self._add_records(canvas_path=str(local), mail_path=None)
        now = datetime(2026, 9, 23, 3, 4, tzinfo=timezone.utc)
        with Session(self.engine) as session, session.begin():
            course = session.scalar(select(Course).where(Course.source_id == "course/1"))
            file = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "canvas-1")
            )
            course.name = "自然语言处理"
            course.term_name = "2026 秋"
            root = CourseFolder(
                source_id="folder-root",
                course_id=course.id,
                name="课程文件",
                is_active=True,
                last_seen_at=now,
                raw_data={},
            )
            session.add(root)
            session.flush()
            assignment = CourseFolder(
                source_id="folder-assignment",
                course_id=course.id,
                parent_folder_id=root.id,
                name="作业资料",
                is_active=True,
                last_seen_at=now,
                raw_data={},
            )
            session.add(assignment)
            session.flush()
            week = CourseFolder(
                source_id="folder-week",
                course_id=course.id,
                parent_folder_id=assignment.id,
                name="第 1 周",
                is_active=True,
                last_seen_at=now,
                raw_data={},
            )
            session.add(week)
            session.flush()
            file.folder_id = week.id
            file.display_name = "中文 空格#?%讲义.pdf"
            file.filename = "%E4%B8%AD%E6%96%87.pdf"

        candidate = next(
            item for item in self._service().scan() if item.source == "canvas"
        )
        self.assertEqual(
            (
                BACKUP_ROOT,
                "Canvas",
                "2026 秋",
                "自然语言处理",
                "课程作业",
                "作业资料",
                "第 1 周",
            ),
            candidate.remote_path[:-1],
        )
        self.assertEqual(
            stable_filename("中文 空格#?%讲义.pdf", "canvas-1"),
            candidate.remote_path[-1],
        )
        self.assertNotIn("%E4%B8%AD", candidate.remote_path[-1])

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

    def test_verified_upload_keeps_local_file_by_default(self) -> None:
        path = self.archive / "lecture.pdf"
        path.write_bytes(b"canvas")
        self._add_records(canvas_path=str(path), mail_path=None)
        service = self._service(FakeProvider())
        candidate = next(item for item in service.scan() if item.source == "canvas")

        result = service.backup((candidate,))

        self.assertEqual(1, result["uploaded"])
        self.assertEqual(0, result["local_removed"])
        self.assertTrue(path.exists())
        with Session(self.engine) as session:
            record = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "canvas-1")
            )
            self.assertEqual(str(path), record.local_path)
            self.assertNotEqual("cloud_only", record.download_status)
            self.assertEqual("/".join(candidate.remote_path), record.cloud_path)
            self.assertEqual(6, record.cloud_size)
            self.assertIsNotNone(record.cloud_backed_up_at)

    def test_verified_upload_persists_cloud_metadata_then_removes_local(self) -> None:
        path = self.archive / "lecture.pdf"
        path.write_bytes(b"canvas")
        self._add_records(canvas_path=str(path), mail_path=None)
        provider = FakeProvider()
        service = self._service(provider)
        candidate = next(item for item in service.scan() if item.source == "canvas")

        result = service.backup((candidate,), remove_local=True)

        self.assertEqual(1, result["uploaded"])
        self.assertEqual([candidate.remote_path], provider.info_requests)
        self.assertEqual(1, result["local_removed"])
        self.assertFalse(path.exists())
        with Session(self.engine) as session:
            record = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "canvas-1")
            )
            self.assertEqual("/".join(candidate.remote_path), record.cloud_path)
            self.assertEqual(6, record.cloud_size)
            self.assertIsNotNone(record.cloud_backed_up_at)
            self.assertIsNone(record.local_path)
            self.assertEqual("cloud_only", record.download_status)
        counts = service.preview()
        self.assertEqual(1, counts["cloud_only"])
        self.assertEqual(1, counts["missing_local"])

    def test_verified_mail_backup_clears_attachment_local_path(self) -> None:
        directory = self.mail / "message"
        directory.mkdir()
        path = directory / "附件.txt"
        path.write_bytes(b"mail")
        self._add_records(canvas_path=None, mail_path=str(path))
        service = self._service(FakeProvider())
        candidate = next(item for item in service.scan() if item.source == "mail")

        result = service.backup((candidate,), remove_local=True)

        self.assertEqual(1, result["uploaded"])
        self.assertEqual(1, result["local_removed"])
        self.assertFalse(path.exists())
        with Session(self.engine) as session:
            record = session.scalar(select(EmailAttachment))
            self.assertIsNone(record.local_path)
            self.assertEqual("/".join(candidate.remote_path), record.cloud_path)
            self.assertEqual(4, record.cloud_size)
            self.assertIsNotNone(record.cloud_backed_up_at)

    def test_existing_verified_remote_also_removes_local(self) -> None:
        path = self.archive / "lecture.pdf"
        path.write_bytes(b"canvas")
        self._add_records(canvas_path=str(path), mail_path=None)
        provider = FakeProvider()
        service = self._service(provider)
        candidate = next(item for item in service.scan() if item.source == "canvas")
        provider.remote[candidate.remote_path] = CloudItem(
            candidate.remote_path[-1], candidate.remote_path, False, 6
        )

        result = service.backup((candidate,), remove_local=True)

        self.assertEqual(1, result["skipped_existing"])
        self.assertGreaterEqual(provider.info_requests.count(candidate.remote_path), 2)
        self.assertEqual(1, result["local_removed"])
        self.assertFalse(path.exists())
        with Session(self.engine) as session:
            record = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "canvas-1")
            )
            self.assertEqual("cloud_only", record.download_status)
            self.assertIsNone(record.local_path)

    def test_remote_verification_or_database_failure_never_deletes_local(self) -> None:
        initial = self.archive / "verify.pdf"
        initial.write_bytes(b"canvas")
        self._add_records(canvas_path=str(initial), mail_path=None)
        for failure in ("verify", "database"):
            with self.subTest(failure=failure):
                path = self.archive / f"{failure}.pdf"
                path.write_bytes(b"canvas")
                with Session(self.engine) as session, session.begin():
                    record = session.scalar(
                        select(CourseFile).where(CourseFile.source_id == "canvas-1")
                    )
                    record.local_path = str(path)
                    record.download_status = "pending"
                    record.cloud_path = None
                    record.cloud_size = None
                    record.cloud_backed_up_at = None
                provider = FakeProvider()
                service = self._service(provider)
                candidate = next(
                    item for item in service.scan() if item.source == "canvas"
                )
                if failure == "verify":
                    original_get_info = provider.get_info

                    def wrong_info(remote_path):
                        item = original_get_info(remote_path)
                        return CloudItem(item.name, item.path, False, (item.size or 0) + 1)

                    provider.get_info = wrong_info
                    result = service.backup((candidate,), remove_local=True)
                else:
                    with patch.object(
                        service,
                        "_persist_cloud_metadata",
                        side_effect=RuntimeError("database unavailable"),
                    ):
                        result = service.backup((candidate,), remove_local=True)
                self.assertEqual(1, result["failed"])
                self.assertEqual(0, result["local_removed"])
                self.assertTrue(path.exists())
                with Session(self.engine) as session:
                    record = session.scalar(
                        select(CourseFile).where(CourseFile.source_id == "canvas-1")
                    )
                    self.assertEqual(str(path), record.local_path)
                    self.assertNotEqual("cloud_only", record.download_status)

    def test_delete_failure_keeps_local_path_reference(self) -> None:
        path = self.archive / "lecture.pdf"
        path.write_bytes(b"canvas")
        self._add_records(canvas_path=str(path), mail_path=None)
        service = self._service(FakeProvider())
        candidate = next(item for item in service.scan() if item.source == "canvas")
        with patch(
            "sjtu_learning_assistant.backup_service.remove_controlled_file",
            side_effect=OSError("busy"),
        ):
            result = service.backup((candidate,), remove_local=True)
        self.assertEqual(1, result["failed"])
        self.assertEqual(0, result["local_removed"])
        self.assertTrue(path.exists())
        with Session(self.engine) as session:
            record = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "canvas-1")
            )
            self.assertEqual(str(path), record.local_path)
            self.assertIsNotNone(record.cloud_path)

    def test_same_size_replacement_after_upload_is_not_deleted(self) -> None:
        path = self.archive / "lecture.pdf"
        path.write_bytes(b"canvas")
        replacement = self.archive / "replacement.pdf"
        replacement.write_bytes(b"newone")
        self._add_records(canvas_path=str(path), mail_path=None)
        provider = FakeProvider()
        service = self._service(provider)
        candidate = next(item for item in service.scan() if item.source == "canvas")
        original_get_info = provider.get_info

        def replace_before_verification(remote_path):
            os.replace(replacement, path)
            return original_get_info(remote_path)

        provider.get_info = replace_before_verification
        result = service.backup((candidate,), remove_local=True)

        self.assertEqual(1, result["failed"])
        self.assertEqual(0, result["local_removed"])
        self.assertEqual(b"newone", path.read_bytes())
        with Session(self.engine) as session:
            record = session.scalar(
                select(CourseFile).where(CourseFile.source_id == "canvas-1")
            )
            self.assertEqual(str(path), record.local_path)
            self.assertIsNotNone(record.cloud_path)

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
            transcript_root=self.transcripts,
        )
        idle = manager.status()
        self.assertEqual("idle", idle["status"])
        self.assertTrue(idle["available"])
        self.assertIsNone(idle["availability_message"])
        self.assertEqual(2, idle["counts"]["total"])
        self.assertEqual(1, idle["counts"]["missing_local"])
        self.assertIsNone(idle["progress"])
        self.assertNotIn("configured-but-never-returned", json.dumps(idle, ensure_ascii=False))

        self.assertEqual("started", manager.start(remove_local=True)["status"])
        self.assertTrue(first_provider.upload_entered.wait(1))
        self.assertEqual("already_running", manager.start(remove_local=False)["status"])
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
                "local_removed",
                "failed",
                "failures",
            },
            set(finished["last_result"]),
        )
        self.assertTrue(first_provider.closed)

        self.assertEqual("started", manager.start()["status"])
        second_finished = self._wait_for_status(manager, "finished")
        self.assertEqual(0, second_finished["last_result"]["uploaded"])
        self.assertEqual(1, second_finished["counts"]["cloud_only"])
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
            transcript_root=self.transcripts,
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
            transcript_root=self.transcripts,
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
            {"canvas", "mail", "ready", "cloud_only", "missing_local", "total"},
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
                "local_removed",
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
            transcript_root=self.transcripts,
        )
        backend_status = manager.status()
        self.assertEqual(status_fields, set(backend_status))
        self.assertEqual(count_fields, set(backend_status["counts"]))
        manager.close()


if __name__ == "__main__":
    unittest.main()
