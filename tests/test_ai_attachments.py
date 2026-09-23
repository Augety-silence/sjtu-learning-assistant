from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine

from sjtu_learning_assistant.ai_attachments import (
    AIFileError,
    AIManagedFileService,
    MAX_EXTRACT_CHARS,
)
from sjtu_learning_assistant.backup_service import BackupService
from sjtu_learning_assistant.cloud_storage import CloudItem
from sjtu_learning_assistant.models import Base


class MemoryCloudProvider:
    def __init__(self, temporary_root: Path) -> None:
        self.temporary_root = temporary_root
        self.payloads: dict[tuple[str, ...], bytes] = {}
        self.closed = False

    def ensure_directory(self, _path) -> None:
        pass

    def exists(self, path) -> bool:
        return tuple(path) in self.payloads

    def get_info(self, path) -> CloudItem:
        key = tuple(path)
        payload = self.payloads[key]
        return CloudItem(key[-1], key, False, len(payload), etag="verified-etag")

    def simple_upload(self, path, source, *, overwrite=False) -> CloudItem:
        key = tuple(path)
        self.payloads[key] = source.read()
        return self.get_info(key)

    multipart_upload = simple_upload

    @contextlib.contextmanager
    def download_temp(self, path, directory=None):
        root = Path(directory) if directory is not None else self.temporary_root
        root.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix="cloud-download-", dir=root)
        os.close(descriptor)
        downloaded = Path(filename)
        downloaded.write_bytes(self.payloads[tuple(path)])
        try:
            yield downloaded
        finally:
            downloaded.unlink(missing_ok=True)

    def close(self) -> None:
        self.closed = True


class AIManagedFileServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "archive"
        self.engine = create_engine(f"sqlite+pysqlite:///{self.root / 'app.db'}")
        Base.metadata.create_all(self.engine)
        self.service = AIManagedFileService(self.engine, archive_root=self.archive)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def test_ingest_keeps_source_deduplicates_and_returns_safe_summary_dto(self) -> None:
        source = self.root / "用户原始笔记.txt"
        content = "机器学习课程复习重点：梯度下降、线性回归与考试安排。"
        source.write_text(content, encoding="utf-8")

        first = self.service.ingest(source)
        second = self.service.ingest(source)

        self.assertTrue(source.exists())
        self.assertEqual(content, source.read_text(encoding="utf-8"))
        self.assertEqual(first["id"], second["id"])
        self.assertIn("机器学习", first["summary"])
        self.assertTrue(first["tags"])
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("controlled_relpath", first)
        self.assertNotIn("cloud_path", first)
        self.assertEqual(1, len(list((self.archive / ".ai_attachments" / "objects").rglob(first["sha256"]))))

    def test_symlink_source_and_controlled_path_escape_are_rejected(self) -> None:
        source = self.root / "outside.txt"
        source.write_text("do not follow", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("当前文件系统不支持符号链接")
        with self.assertRaises(AIFileError):
            self.service.ingest(link)

        item = self.service.ingest(source)
        from sqlalchemy.orm import Session
        from sjtu_learning_assistant.models import AIManagedFile

        with Session(self.engine) as session, session.begin():
            row = session.get(AIManagedFile, item["id"])
            assert row is not None
            row.controlled_relpath = "../outside.txt"
        with self.assertRaisesRegex(AIFileError, "越界"):
            self.service.resolve_controlled_copy(item["id"])

    def test_text_extraction_and_read_are_bounded(self) -> None:
        source = self.root / "large.txt"
        source.write_text("甲" * (MAX_EXTRACT_CHARS + 5000), encoding="utf-8")
        item = self.service.ingest(source)
        excerpt = self.service.read_text(item["id"], max_chars=321)
        self.assertEqual(321, len(excerpt["text"]))
        self.assertTrue(excerpt["truncated"])
        self.assertEqual(321, excerpt["returned_chars"])
        with self.assertRaises(AIFileError):
            self.service.read_text(item["id"], max_chars=12_001)

    def test_cloud_only_read_verifies_hash_and_restore_then_reveal(self) -> None:
        source = self.root / "cloud.txt"
        payload = b"verified cloud attachment"
        source.write_bytes(payload)
        provider = MemoryCloudProvider(self.root / "downloads")
        service = AIManagedFileService(
            self.engine,
            archive_root=self.archive,
            provider_factory=lambda: provider,
        )
        item = service.ingest(source)
        controlled = service.resolve_controlled_copy(item["id"])
        remote_path = ("backup", "cloud.txt")
        provider.payloads[remote_path] = payload

        cloud = service.mark_cloud_only(
            item["id"],
            cloud_path="/".join(remote_path),
            cloud_size=len(payload),
            cloud_sha256=hashlib.sha256(payload).hexdigest(),
        )
        self.assertEqual("cloud_only", cloud["status"])
        self.assertFalse(controlled.exists())
        self.assertTrue(source.exists(), "绝不能删除用户原始文件")
        self.assertEqual("verified cloud attachment", service.read_text(item["id"])["text"])

        provider.payloads[remote_path] = b"tampered cloud attachment"
        unavailable = service.read_text(item["id"])
        self.assertEqual("unavailable", unavailable["status"])
        with self.assertRaisesRegex(AIFileError, "校验失败"):
            service.restore(item["id"])

        provider.payloads[remote_path] = payload
        commands: list[list[str]] = []
        revealed = service.reveal(
            item["id"],
            lambda command, **_kwargs: commands.append(command) or SimpleNamespace(returncode=0),
        )
        self.assertEqual("revealed", revealed["status"])
        self.assertEqual(["/usr/bin/open", "-R"], commands[0][:2])
        self.assertTrue(service.resolve_controlled_copy(item["id"]).exists())
        self.assertNotIn(str(self.root), json.dumps(revealed, ensure_ascii=False))

    def test_backup_verifies_download_and_only_removes_controlled_copy(self) -> None:
        source = self.root / "source-owned.txt"
        payload = b"backup me safely"
        source.write_bytes(payload)
        item = self.service.ingest(source)
        controlled = self.service.resolve_controlled_copy(item["id"])
        provider = MemoryCloudProvider(self.root / "downloads")
        backup = BackupService(
            self.engine,
            provider,
            archive_root=self.archive,
            mail_attachments_root=self.root / "mail",
            ai_file_service=self.service,
        )

        result = backup.backup()

        self.assertEqual(1, result["uploaded"])
        self.assertEqual(1, result["local_removed"])
        self.assertEqual(0, result["failed"])
        self.assertFalse(controlled.exists())
        self.assertTrue(source.exists())
        stored = self.service.get(item["id"])
        self.assertEqual("cloud_only", stored["status"])
        self.assertTrue(stored["cloud_ready"])


if __name__ == "__main__":
    unittest.main()
