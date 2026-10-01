from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sjtu_learning_assistant.desktop_database import SCHEMA_VERSION, bootstrap_sqlite
from sjtu_learning_assistant.models import ArchiveEntry, ArchiveJob, Base


def test_0018_revision_and_bootstrap_tables():
    migration = Path(__file__).parents[1] / "migrations" / "versions" / "0018_add_cloud_archive.py"
    spec = importlib.util.spec_from_file_location("migration_0018", migration)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    assert module.revision == "0018"
    assert module.down_revision == "0017"
    assert SCHEMA_VERSION == "0018"

    with tempfile.TemporaryDirectory() as directory:
        engine = create_engine("sqlite+pysqlite:///" + str(Path(directory) / "archive.db"))
        assert bootstrap_sqlite(engine) == "0018"
        tables = set(inspect(engine).get_table_names())
        assert {
            "archive_entries", "archive_versions", "archive_authorized_roots",
            "archive_jobs", "archive_events",
        }.issubset(tables)
        engine.dispose()


def test_archive_constraints_reject_duplicate_job_and_invalid_status():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        entry = ArchiveEntry(
            id="entry", path_identity="a" * 64, volume_identity="dev:1",
            original_abs_path=None, filename="x", restore_capability="choose_location",
            source_kind="legacy", status="legacy",
        )
        session.add(entry)
        session.flush()
        session.add(ArchiveJob(
            id="one", idempotency_key="same", kind="archive", entry_id=entry.id,
            status="pending", bytes_total=0, bytes_done=0, plan_data={},
        ))
        session.commit()
        session.add(ArchiveJob(
            id="two", idempotency_key="same", kind="archive", entry_id=entry.id,
            status="pending", bytes_total=0, bytes_done=0, plan_data={},
        ))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(ArchiveJob(
            id="bad", idempotency_key="bad", kind="archive", entry_id=entry.id,
            status="unknown", bytes_total=0, bytes_done=0, plan_data={},
        ))
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()


def test_0018_upgrade_and_downgrade_only_archive_objects():
    with tempfile.TemporaryDirectory() as directory:
        engine = create_engine("sqlite+pysqlite:///" + str(Path(directory) / "cycle.db"))
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE course_files (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            connection.exec_driver_sql("INSERT INTO alembic_version (version_num) VALUES ('0017')")
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0018")
        assert "archive_entries" in inspect(engine).get_table_names()
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0017")
        tables = set(inspect(engine).get_table_names())
        assert "course_files" in tables
        assert "archive_entries" not in tables
        engine.dispose()
