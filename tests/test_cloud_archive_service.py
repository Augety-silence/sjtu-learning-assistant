from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.cloud_archive_service import CloudArchiveError, CloudArchiveService
from sjtu_learning_assistant.cloud_storage.models import CloudItem
from sjtu_learning_assistant.models import (
    AIManagedFile, ArchiveEntry, ArchiveEvent, ArchiveJob, ArchiveVersion, Base,
    CourseFile, EmailAttachment,
)


class FakeProvider:
    def __init__(self) -> None:
        self.objects = {}
        self.uploads = 0
        self.fail_upload = False

    def ensure_directory(self, _path):
        return None

    def exists(self, path):
        return tuple(path) in self.objects

    def get_info(self, path):
        data = self.objects[tuple(path)]
        return CloudItem(name=path[-1], path=tuple(path), is_directory=False, size=len(data), etag="fake-etag")

    def simple_upload(self, path, source, overwrite=False):
        if overwrite:
            raise AssertionError("归档不应覆盖")
        if self.fail_upload:
            raise OSError("fake network failure")
        data = source.read()
        key = tuple(path)
        if key in self.objects:
            raise FileExistsError(key)
        self.objects[key] = data
        self.uploads += 1
        return self.get_info(key)

    def multipart_upload(self, path, source, overwrite=False):
        return self.simple_upload(path, source, overwrite=overwrite)

    def download_stream(self, path, chunk_size=65536, start=None, end=None):
        data = self.objects[tuple(path)]
        for index in range(0, len(data), chunk_size):
            yield data[index:index + chunk_size]

    def close(self):
        return None


@pytest.fixture
def archive_env():
    with tempfile.TemporaryDirectory() as directory:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        provider = FakeProvider()
        service = CloudArchiveService(
            engine,
            provider_factory=lambda: provider,
            archive_root=Path(directory),
            multipart_threshold=4,
        )
        yield Path(directory), engine, provider, service
        engine.dispose()


def test_empty_unicode_spaces_and_same_name_different_paths(archive_env):
    root, engine, provider, service = archive_env
    first = root / "甲 目录" / "同名 文件.txt"
    second = root / "乙 目录" / "同名 文件.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"")
    second.write_bytes(b"different")

    first_job = service.archive_file(first)
    second_job = service.archive_file(second)

    assert first_job["status"] == "completed"
    assert second_job["status"] == "completed"
    with Session(engine) as session:
        entries = list(session.scalars(select(ArchiveEntry).order_by(ArchiveEntry.original_abs_path)))
        assert len(entries) == 2
        assert entries[0].path_identity != entries[1].path_identity
        assert {item.filename for item in entries} == {"同名 文件.txt"}
        versions = list(session.scalars(select(ArchiveVersion)))
        assert sorted(item.size for item in versions) == [0, 9]
    assert provider.uploads == 2


def test_content_change_creates_version_and_duplicate_is_idempotent(archive_env):
    root, engine, provider, service = archive_env
    path = root / "版本.txt"
    path.write_bytes(b"one")
    first = service.archive_file(path, idempotency_key="first")
    duplicate = service.archive_file(path, idempotency_key="first")
    path.write_bytes(b"two-two")
    second = service.archive_file(path, idempotency_key="second")

    assert first["id"] == duplicate["id"]
    assert second["id"] != first["id"]
    assert provider.uploads == 2
    with Session(engine) as session:
        versions = list(session.scalars(select(ArchiveVersion).order_by(ArchiveVersion.version_number)))
        assert [item.version_number for item in versions] == [1, 2]
        assert session.scalar(select(func.count(ArchiveJob.id))) == 2
        assert session.scalar(select(func.count(ArchiveEvent.id))) >= 4


def test_same_idempotency_key_rejects_different_content(archive_env):
    root, _engine, _provider, service = archive_env
    path = root / "a.txt"
    path.write_bytes(b"one")
    service.archive_file(path, idempotency_key="fixed")
    path.write_bytes(b"changed")
    with pytest.raises(CloudArchiveError, match="幂等键"):
        service.archive_file(path, idempotency_key="fixed")


def test_network_failure_then_retry(archive_env):
    root, engine, provider, service = archive_env
    path = root / "network.txt"
    path.write_bytes(b"network")
    provider.fail_upload = True
    with pytest.raises(CloudArchiveError):
        service.archive_file(path)
    with Session(engine) as session:
        job = session.scalar(select(ArchiveJob))
        assert job.status == "failed"
        job_id = job.id
        assert job.bytes_done == 0
    jobs = service.archive_jobs()["items"]
    assert next(item for item in jobs if item["id"] == job_id)["retryable"] is True
    provider.fail_upload = False
    result = service.retry(job_id)
    assert result["status"] == "completed"


def test_uploaded_but_db_update_failed_can_reconcile(archive_env):
    root, engine, provider, service = archive_env
    path = root / "reconcile.txt"
    path.write_bytes(b"cloud already has this")

    def fail_after_upload(_job, _item):
        raise RuntimeError("forced db failure")

    service.after_upload_hook = fail_after_upload
    with pytest.raises(CloudArchiveError, match="reconcile"):
        service.archive_file(path)
    with Session(engine) as session:
        job = session.scalar(select(ArchiveJob))
        assert job.status == "needs_reconcile"
        job_id = job.id
    assert provider.uploads == 1
    service.after_upload_hook = None
    result = service.reconcile(job_id)
    assert result["items"][0]["status"] == "needs_verification"
    assert provider.uploads == 1
    with Session(engine) as session:
        version = session.scalar(select(ArchiveVersion))
        assert version.status == "needs_verification"


def test_manager_initialization_marks_running_jobs_interrupted(archive_env):
    _root, engine, provider, _service = archive_env
    with Session(engine) as session:
        entry = ArchiveEntry(
            id="entry", path_identity="a" * 64, volume_identity="dev:1",
            original_abs_path=None, filename="x", restore_capability="choose_location",
            source_kind="legacy", status="legacy",
        )
        version = ArchiveVersion(
            id="version", entry=entry, version_number=1, size=1, mtime_ns=0,
            sha256="b" * 64, status="pending",
        )
        job = ArchiveJob(
            id="job", idempotency_key="running-job", kind="archive", entry=entry,
            version=version, status="running", bytes_total=1, bytes_done=0, plan_data={},
        )
        session.add(job)
        session.commit()
    CloudArchiveService(engine, provider_factory=lambda: provider)
    with Session(engine) as session:
        job = session.get(ArchiveJob, "job")
        assert job.status == "interrupted"
        assert session.scalar(select(func.count(ArchiveEvent.id)).where(ArchiveEvent.job_id == "job")) == 1


def test_explicit_legacy_mapping_never_treats_local_path_as_original(archive_env):
    _root, engine, _provider, service = archive_env
    now = datetime.now(timezone.utc)
    digest = "c" * 64
    with Session(engine) as session:
        session.add(CourseFile(
            source_id="course-source", course_id=999, display_name="课程.pdf", filename="课程.pdf",
            size=4, local_path="/must/not/be/original", cloud_path="legacy/course.pdf",
            cloud_size=4, cloud_backed_up_at=now, download_sha256=digest,
            download_status="cloud_only", last_seen_at=now, raw_data={},
        ))
        session.add(EmailAttachment(
            email_id=999, resource_id="mail-resource", filename="邮件.txt", content_type="text/plain",
            size=5, local_path="/must/not/be/original", sha256=digest,
            cloud_path="legacy/mail.txt", cloud_size=5, cloud_backed_up_at=now,
        ))
        session.add(AIManagedFile(
            name="AI.txt", size=2, sha256="d" * 64, status="cloud_only",
            controlled_relpath="ai/AI.txt", cloud_path="legacy/ai.txt", cloud_size=2,
            cloud_uploaded_at=now,
        ))
        session.commit()
    assert service.map_legacy_records() == {
        "course_file": 1, "email_attachment": 1, "ai_managed_file": 1,
    }
    assert service.map_legacy_records() == {
        "course_file": 0, "email_attachment": 0, "ai_managed_file": 0,
    }
    with Session(engine) as session:
        entries = list(session.scalars(select(ArchiveEntry).where(ArchiveEntry.source_kind != "user_file")))
        assert len(entries) == 3
        assert all(entry.original_abs_path is None for entry in entries)
        capabilities = {entry.source_kind: entry.restore_capability for entry in entries}
        assert capabilities == {
            "course_file": "choose_location",
            "email_attachment": "choose_location",
            "ai_managed_file": "managed_location",
        }


def test_progress_is_throttled_but_terminal_state_is_always_persisted(archive_env):
    root, engine, _provider, service = archive_env
    path = root / "large.bin"
    path.write_bytes(b"x" * (12 * 1024 * 1024))
    result = service.archive_file(path)
    assert result["status"] == "completed"
    with Session(engine) as session:
        events = list(session.scalars(select(ArchiveEvent).order_by(ArchiveEvent.created_at)))
        progress = [item for item in events if item.event_type == "upload_progress"]
        assert [item.bytes_done for item in progress] == [5 * 1024 * 1024, 10 * 1024 * 1024]
        assert events[-1].event_type == "upload_completed"
        assert events[-1].bytes_done == 12 * 1024 * 1024


def test_archive_does_not_auto_authorize_source_parent(archive_env):
    root, engine, _provider, service = archive_env
    path = root / "not-authorized.txt"
    path.write_bytes(b"safe")
    service.archive_file(path)
    with Session(engine) as session:
        entry = session.scalar(select(ArchiveEntry))
        assert entry.restore_capability == "choose_location"
        from sjtu_learning_assistant.models import ArchiveAuthorizedRoot
        assert session.scalar(select(func.count(ArchiveAuthorizedRoot.id))) == 0


def test_archive_list_global_query_sort_cursor_and_current_version(archive_env):
    root, _engine, _provider, service = archive_env
    for name, data in (("beta.txt", b"22"), ("alpha.txt", b"1"), ("gamma.txt", b"333")):
        path = root / name
        path.write_bytes(data)
        service.archive_file(path)
    first = service.archive_list(limit=1, query="a", sort="size_desc")
    assert first["total"] == 3
    assert first["items"][0]["filename"] == "gamma.txt"
    assert first["items"][0]["size"] == 3
    assert first["items"][0]["size_bytes"] == 3
    assert first["items"][0]["version_status"] == "archived"
    assert first["items"][0]["cloud_path"]
    assert first["items"][0]["archived_at"]
    assert first["items"][0]["sha256"]
    assert first["items"][0]["status"] == "archived"
    assert first["items"][0]["restore_capability"] == "choose_location"
    second = service.archive_list(limit=1, query="a", sort="size_desc", cursor=first["next_cursor"])
    assert second["items"][0]["filename"] == "beta.txt"


def test_event_details_and_public_errors_are_redacted(archive_env, monkeypatch):
    _root, engine, _provider, _service = archive_env
    home = Path.home()
    with Session(engine) as session:
        entry = ArchiveEntry(
            id="redact-entry", path_identity="e" * 64, volume_identity="dev:1",
            original_abs_path=str(home / "private.txt"), filename="private.txt",
            restore_capability="choose_location", source_kind="user_file", status="active",
        )
        version = ArchiveVersion(
            id="redact-version", entry=entry, version_number=1, size=1, mtime_ns=0,
            sha256="f" * 64, status="pending",
        )
        job = ArchiveJob(
            id="redact-job", idempotency_key="redact", kind="archive", entry=entry,
            version=version, status="pending", bytes_total=1, bytes_done=0, plan_data={},
        )
        session.add(job)
        session.flush()
        from sjtu_learning_assistant.cloud_archive_service import add_event
        add_event(session, job, "created", "pending", details={
            "path": str(home / "secret"), "token": "top-secret",
            "nested": {"cookie": "session-secret"},
        })
        session.commit()
    result = _service.archive_job_events("redact-job")["items"][0]["details"]
    assert result["path"].startswith("~/")
    assert result["token"] == "<redacted>"
    assert result["nested"]["cookie"] == "<redacted>"
    assert str(home) not in str(result)
    assert "top-secret" not in str(result)
    error = CloudArchiveError(f"failed at {home}/secret token=abc")
    assert str(home) not in str(error)
    assert "abc" not in str(error)
