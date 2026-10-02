from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.cloud_archive_service import CloudArchiveService, encode_remote_path
from sjtu_learning_assistant.cloud_storage.models import CloudItem
from sjtu_learning_assistant.models import ArchiveEntry, ArchiveJob, ArchiveVersion, Base
from sjtu_learning_assistant.restore_service import RestoreError, RestoreService


class FakeProvider:
    def __init__(self) -> None:
        self.objects = {}
        self.download_error = False

    def ensure_directory(self, _path):
        return None

    def exists(self, path):
        return tuple(path) in self.objects

    def get_info(self, path):
        data = self.objects[tuple(path)]
        return CloudItem(name=path[-1], path=tuple(path), is_directory=False, size=len(data), etag="etag")

    def simple_upload(self, path, source, overwrite=False):
        key = tuple(path)
        if key in self.objects and not overwrite:
            raise FileExistsError(key)
        self.objects[key] = source.read()
        return self.get_info(key)

    def multipart_upload(self, path, source, overwrite=False):
        return self.simple_upload(path, source, overwrite=overwrite)

    def download_stream(self, path, chunk_size=65536, start=None, end=None):
        data = self.objects[tuple(path)]
        midpoint = max(1, len(data) // 2)
        yield data[:midpoint]
        if self.download_error:
            raise OSError("fake interrupted download")
        if midpoint < len(data):
            yield data[midpoint:]

    def close(self):
        return None


@pytest.fixture
def restore_env():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        provider = FakeProvider()
        archive = CloudArchiveService(
            engine,
            provider_factory=lambda: provider,
            archive_root=root,
        )
        restore = RestoreService(engine, provider_factory=lambda: provider, mark_interrupted=False)
        yield root, engine, provider, archive, restore
        engine.dispose()


def _archive_file(root, archive, name="文 件.txt", data=b"new-content"):
    source_dir = root / "source"
    source_dir.mkdir(exist_ok=True)
    source = source_dir / name
    source.write_bytes(data)
    archive.archive_file(source)
    detail = archive.archive_list()["items"][0]
    return source, detail["id"]


def test_legacy_without_original_path_requires_picker_and_rejects_bad_names(restore_env):
    root, engine, provider, _archive, restore = restore_env
    remote = ("legacy", "file")
    provider.objects[remote] = b"legacy"
    with Session(engine) as session:
        entry = ArchiveEntry(
            id="legacy", path_identity="a" * 64, volume_identity="legacy",
            original_abs_path=None, filename="legacy.txt", restore_capability="choose_location",
            source_kind="course_file", source_record_id="1", status="legacy",
        )
        session.add(entry)
        session.add(ArchiveVersion(
            id="legacy-version", entry=entry, version_number=1, size=6, mtime_ns=0,
            sha256=hashlib.sha256(b"legacy").hexdigest(), cloud_remote_path=encode_remote_path(remote),
            status="archived",
        ))
        session.commit()
    with pytest.raises(RestoreError, match="选择恢复位置"):
        restore.plan("legacy", mode="original")
    authorized = restore.authorize_root(root / "target") if (root / "target").exists() else None
    if authorized is None:
        (root / "target").mkdir()
        authorized = restore.authorize_root(root / "target")
    plan = restore.plan("legacy", mode="choose_location", authorized_root_id=authorized["id"])
    assert Path(plan["target"]).parent == root / "target"

    with Session(engine) as session:
        session.get(ArchiveEntry, "legacy").filename = "../escape"
        session.commit()
    with pytest.raises(RestoreError, match="文件名"):
        restore.plan("legacy", mode="choose_location", authorized_root_id=authorized["id"])
    with Session(engine) as session:
        session.get(ArchiveEntry, "legacy").filename = "/tmp/evil"
        session.commit()
    with pytest.raises(RestoreError, match="文件名"):
        restore.plan("legacy", mode="choose_location", authorized_root_id=authorized["id"])


@pytest.mark.parametrize("policy", ("skip", "compare", "overwrite", "save_as"))
def test_conflict_policies(restore_env, policy):
    root, _engine, _provider, archive, restore = restore_env
    source, entry_id = _archive_file(root, archive, name=f"{policy}.txt")
    target_root = root / "restore"
    target_root.mkdir()
    target = target_root / source.name
    target.write_bytes(b"old")
    authorized = restore.authorize_root(target_root)
    plan = restore.plan(entry_id, mode="choose_location", authorized_root_id=authorized["id"])
    result = restore.execute(plan["job"]["id"], policy)
    if policy == "skip":
        assert result["status"] == "skipped"
        assert target.read_bytes() == b"old"
    elif policy == "compare":
        assert result["status"] == "compare"
        assert target.read_bytes() == b"old"
        assert result["comparison"]["existing"]["sha256"] == hashlib.sha256(b"old").hexdigest()
    elif policy == "overwrite":
        assert result["status"] == "completed"
        assert target.read_bytes() == b"new-content"
    else:
        assert result["status"] == "completed"
        assert target.read_bytes() == b"old"
        assert Path(result["target"]).read_bytes() == b"new-content"
        assert Path(result["target"]) != target


def test_download_interruption_and_hash_failure_leave_no_target_or_temp(restore_env):
    root, engine, provider, archive, restore = restore_env
    _source, entry_id = _archive_file(root, archive)
    target_root = root / "target"
    target_root.mkdir()
    authorized = restore.authorize_root(target_root)

    provider.download_error = True
    plan = restore.plan(entry_id, mode="choose_location", authorized_root_id=authorized["id"])
    with pytest.raises(RestoreError):
        restore.execute(plan["job"]["id"], "overwrite")
    assert not (target_root / "文 件.txt").exists()
    assert not list(target_root.glob(".sjtu-restore-*"))
    with Session(engine) as session:
        assert session.get(ArchiveJob, plan["job"]["id"]).status == "failed"

    provider.download_error = False
    remote_key = next(iter(provider.objects))
    provider.objects[remote_key] = b"bad-content"
    plan = restore.plan(entry_id, mode="choose_location", authorized_root_id=authorized["id"])
    with pytest.raises(RestoreError, match="完整性"):
        restore.execute(plan["job"]["id"], "overwrite")
    assert not (target_root / "文 件.txt").exists()
    assert not list(target_root.glob(".sjtu-restore-*"))


def test_missing_parents_require_confirmation(restore_env):
    root, _engine, _provider, archive, restore = restore_env
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    source = nested / "nested.txt"
    source.write_bytes(b"nested")
    archive.archive_file(source)
    entry_id = archive.archive_list()["items"][0]["id"]
    restore.authorize_root(root)
    source.unlink()
    nested.rmdir()
    nested.parent.rmdir()
    plan = restore.plan(entry_id, mode="original")
    assert plan["requires_directory_confirmation"] is True
    with pytest.raises(RestoreError, match="用户确认"):
        restore.execute(plan["job"]["id"], "overwrite", confirm_create_dirs=False)
    result = restore.execute(plan["job"]["id"], "overwrite", confirm_create_dirs=True)
    assert result["status"] == "completed"
    assert source.read_bytes() == b"nested"


def test_symlink_parent_is_rejected(restore_env):
    root, _engine, _provider, archive, restore = restore_env
    nested = root / "inside"
    nested.mkdir()
    source = nested / "file.txt"
    source.write_bytes(b"safe")
    archive.archive_file(source)
    entry_id = archive.archive_list()["items"][0]["id"]
    restore.authorize_root(root)
    source.unlink()
    nested.rmdir()
    outside = root / "outside"
    outside.mkdir()
    nested.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RestoreError, match="符号链接"):
        restore.plan(entry_id, mode="original")


def test_unwritable_target_is_rejected_when_platform_enforces_permissions(restore_env):
    root, _engine, _provider, archive, restore = restore_env
    _source, entry_id = _archive_file(root, archive)
    target_root = root / "readonly"
    target_root.mkdir()
    authorized = restore.authorize_root(target_root)
    plan = restore.plan(entry_id, mode="choose_location", authorized_root_id=authorized["id"])
    target_root.chmod(0o555)
    try:
        if os.access(target_root, os.W_OK):
            pytest.skip("当前运行身份绕过目录写权限")
        with pytest.raises(RestoreError, match="不可写"):
            restore.execute(plan["job"]["id"], "overwrite")
    finally:
        target_root.chmod(0o755)


def test_execute_rejects_tampered_parent_escape_and_absolute_target(restore_env):
    root, engine, _provider, archive, restore = restore_env
    _source, entry_id = _archive_file(root, archive)
    target_root = root / "authorized"
    target_root.mkdir()
    authorized = restore.authorize_root(target_root)
    plan = restore.plan(entry_id, mode="choose_location", authorized_root_id=authorized["id"])
    job_id = plan["job"]["id"]
    for hostile in (str(target_root / ".." / "escape.txt"), "/tmp/absolute-escape.txt"):
        with Session(engine) as session:
            session.get(ArchiveJob, job_id).target_abs_path = hostile
            session.commit()
        with pytest.raises(RestoreError, match="授权根"):
            restore.execute(job_id, "overwrite")


def test_original_restore_requires_explicit_picker_authorization(restore_env):
    root, _engine, _provider, archive, restore = restore_env
    source, entry_id = _archive_file(root, archive, name="permission.txt")
    source.unlink()
    with pytest.raises(RestoreError, match="授权"):
        restore.plan(entry_id, mode="original")
    restore.authorize_root(source.parent)
    plan = restore.plan(entry_id, mode="original")
    assert Path(plan["target"]) == source


def test_restore_retry_revalidates_persisted_plan_and_conflict(restore_env):
    root, engine, provider, archive, restore = restore_env
    _source, entry_id = _archive_file(root, archive, name="retry.txt")
    target_root = root / "retry-target"
    target_root.mkdir()
    authorized = restore.authorize_root(target_root)
    plan = restore.plan(entry_id, mode="choose_location", authorized_root_id=authorized["id"])
    provider.download_error = True
    with pytest.raises(RestoreError):
        restore.execute(plan["job"]["id"], "overwrite")
    jobs = archive.archive_jobs()["items"]
    retry_job = next(item for item in jobs if item["id"] == plan["job"]["id"])
    assert retry_job["retryable"] is True
    provider.download_error = False
    result = restore.retry(plan["job"]["id"])
    assert result["status"] == "completed"
    assert (target_root / "retry.txt").read_bytes() == b"new-content"

    with Session(engine) as session:
        job = session.get(ArchiveJob, plan["job"]["id"])
        job.status = "failed"
        job.plan_data = {}
        session.commit()
    jobs = archive.archive_jobs()["items"]
    retry_job = next(item for item in jobs if item["id"] == plan["job"]["id"])
    assert retry_job["retryable"] is False
    with pytest.raises(RestoreError, match="持久化计划"):
        restore.retry(plan["job"]["id"])
