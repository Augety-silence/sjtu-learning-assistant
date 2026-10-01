"""Add durable cloud archive, restore jobs, events, and authorized roots."""
from __future__ import annotations
from typing import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ARCHIVE_TABLES = {
    "archive_entries": (
        (
            "id", "path_identity", "volume_identity", "original_abs_path",
            "archive_root_snapshot", "relative_path", "filename",
            "restore_capability", "source_kind", "source_record_id", "status",
            "last_error", "retry_count", "created_at", "updated_at",
        ),
        (
            "ck_archive_entries_restore_capability",
            "ck_archive_entries_status",
            "ck_archive_entries_retry_count",
        ),
        (("path_identity",), ("source_kind", "source_record_id")),
        (),
    ),
    "archive_versions": (
        (
            "id", "entry_id", "version_number", "size", "file_type", "mtime_ns",
            "archived_at", "sha256", "cloud_provider", "cloud_remote_id",
            "cloud_remote_path", "cloud_etag", "status", "last_error",
            "retry_count", "created_at", "updated_at",
        ),
        (
            "ck_archive_versions_number", "ck_archive_versions_size",
            "ck_archive_versions_mtime", "ck_archive_versions_retry_count",
            "ck_archive_versions_status",
        ),
        (("entry_id", "version_number"), ("entry_id", "sha256")),
        (("entry_id", "archive_entries", "id", "CASCADE"),),
    ),
    "archive_authorized_roots": (
        (
            "id", "path_hash", "absolute_path", "volume_identity", "source",
            "is_active", "last_used_at", "created_at", "updated_at",
        ),
        ("ck_archive_authorized_roots_source",),
        (("path_hash",),),
        (),
    ),
    "archive_jobs": (
        (
            "id", "idempotency_key", "kind", "entry_id", "version_id",
            "authorized_root_id", "status", "bytes_total", "bytes_done",
            "conflict_policy", "target_abs_path", "plan_data", "attempt_count",
            "last_error", "started_at", "finished_at", "created_at", "updated_at",
        ),
        (
            "ck_archive_jobs_kind", "ck_archive_jobs_status",
            "ck_archive_jobs_conflict_policy", "ck_archive_jobs_progress",
            "ck_archive_jobs_attempt_count",
        ),
        (("idempotency_key",),),
        (
            ("entry_id", "archive_entries", "id", "SET NULL"),
            ("version_id", "archive_versions", "id", "SET NULL"),
            ("authorized_root_id", "archive_authorized_roots", "id", "SET NULL"),
        ),
    ),
    "archive_events": (
        (
            "id", "job_id", "entry_id", "version_id", "event_type", "status",
            "message", "details", "bytes_done", "bytes_total", "created_at",
        ),
        ("ck_archive_events_progress", "ck_archive_events_status"),
        (),
        (
            ("job_id", "archive_jobs", "id", "CASCADE"),
            ("entry_id", "archive_entries", "id", "SET NULL"),
            ("version_id", "archive_versions", "id", "SET NULL"),
        ),
    ),
}
_ARCHIVE_INDEXES = {
    "ix_archive_entries_status": ("archive_entries", ("status",)),
    "ix_archive_entries_filename": ("archive_entries", ("filename",)),
    "ix_archive_versions_entry_status": (
        "archive_versions", ("entry_id", "status")
    ),
    "ix_archive_versions_sha256": ("archive_versions", ("sha256",)),
    "ix_archive_authorized_roots_active": (
        "archive_authorized_roots", ("is_active",)
    ),
    "ix_archive_jobs_status_created": (
        "archive_jobs", ("status", "created_at")
    ),
    "ix_archive_jobs_entry": ("archive_jobs", ("entry_id",)),
    "ix_archive_jobs_version": ("archive_jobs", ("version_id",)),
    "ix_archive_events_job_created": (
        "archive_events", ("job_id", "created_at")
    ),
    "ix_archive_events_entry": ("archive_events", ("entry_id",)),
}


def _schema_error(table_name: str, detail: str) -> RuntimeError:
    return RuntimeError(
        f"0018 found incompatible existing table {table_name}: {detail}"
    )


def _validate_existing_table(inspector, table_name: str) -> None:
    expected_columns, expected_checks, expected_uniques, expected_foreign_keys = (
        _ARCHIVE_TABLES[table_name]
    )
    actual_columns = tuple(
        str(column["name"]) for column in inspector.get_columns(table_name)
    )
    if actual_columns != expected_columns:
        raise _schema_error(
            table_name,
            f"columns {actual_columns!r} do not match {expected_columns!r}",
        )

    primary_key = tuple(
        str(column)
        for column in inspector.get_pk_constraint(table_name).get(
            "constrained_columns", ()
        )
    )
    if primary_key != ("id",):
        raise _schema_error(table_name, "primary key does not match")

    unique_columns = {
        tuple(str(column) for column in constraint.get("column_names", ()))
        for constraint in inspector.get_unique_constraints(table_name)
    }
    if unique_columns != set(expected_uniques):
        raise _schema_error(table_name, "unique constraints do not match")

    check_names = {
        str(constraint.get("name"))
        for constraint in inspector.get_check_constraints(table_name)
    }
    if check_names != set(expected_checks):
        raise _schema_error(
            table_name, "check constraints do not match")

    foreign_keys = {
        (
            tuple(
                str(column)
                for column in constraint.get("constrained_columns", ())
            ),
            str(constraint.get("referred_table")),
            tuple(
                str(column)
                for column in constraint.get("referred_columns", ())
            ),
            str(constraint.get("options", {}).get("ondelete") or "").upper(),
        )
        for constraint in inspector.get_foreign_keys(table_name)
    }
    normalized_foreign_keys = {
        ((column,), referred_table, (referred_column,), ondelete)
        for column, referred_table, referred_column, ondelete in expected_foreign_keys
    }
    if foreign_keys != normalized_foreign_keys:
        raise _schema_error(table_name, "foreign keys do not match")


def _adopt_existing_archive_schema() -> bool:
    inspector = sa.inspect(op.get_bind())
    present = set(_ARCHIVE_TABLES).intersection(inspector.get_table_names())
    if not present:
        return False
    if present != set(_ARCHIVE_TABLES):
        missing = sorted(set(_ARCHIVE_TABLES).difference(present))
        raise RuntimeError(
            f"0018 found a partial archive schema; missing tables: {missing!r}"
        )

    for table_name in _ARCHIVE_TABLES:
        _validate_existing_table(inspector, table_name)

    for index_name, (table_name, expected_columns) in _ARCHIVE_INDEXES.items():
        indexes = {
            str(index.get("name")): index
            for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        }
        existing = indexes.get(index_name)
        if existing is None:
            op.create_index(index_name, table_name, expected_columns)
            continue
        actual_columns = tuple(
            str(column) for column in existing.get("column_names", ())
        )
        if actual_columns != expected_columns or bool(existing.get("unique")):
            raise _schema_error(table_name, f"index {index_name} does not match")
    return True


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    if _adopt_existing_archive_schema():
        return

    op.create_table(
        "archive_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("path_identity", sa.String(length=64), nullable=False),
        sa.Column("volume_identity", sa.String(length=255), nullable=False),
        sa.Column("original_abs_path", sa.Text(), nullable=True),
        sa.Column("archive_root_snapshot", sa.Text(), nullable=True),
        sa.Column("relative_path", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("restore_capability", sa.String(length=32), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_record_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        _created_at(), _updated_at(),
        sa.CheckConstraint("restore_capability IN (\x27original_path\x27, \x27managed_location\x27, \x27choose_location\x27)", name="ck_archive_entries_restore_capability"),
        sa.CheckConstraint("status IN (\x27active\x27, \x27archived\x27, \x27failed\x27, \x27legacy\x27)", name="ck_archive_entries_status"),
        sa.CheckConstraint("retry_count >= 0", name="ck_archive_entries_retry_count"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path_identity"),
        sa.UniqueConstraint("source_kind", "source_record_id", name="uq_archive_entries_legacy_source"),
    )
    op.create_index("ix_archive_entries_status", "archive_entries", ("status",))
    op.create_index("ix_archive_entries_filename", "archive_entries", ("filename",))
    op.create_table(
        "archive_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("entry_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("file_type", sa.String(length=255), nullable=True),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("cloud_provider", sa.String(length=64), nullable=True),
        sa.Column("cloud_remote_id", sa.String(length=512), nullable=True),
        sa.Column("cloud_remote_path", sa.Text(), nullable=True),
        sa.Column("cloud_etag", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        _created_at(), _updated_at(),
        sa.CheckConstraint("version_number > 0", name="ck_archive_versions_number"),
        sa.CheckConstraint("size >= 0", name="ck_archive_versions_size"),
        sa.CheckConstraint("mtime_ns >= 0", name="ck_archive_versions_mtime"),
        sa.CheckConstraint("retry_count >= 0", name="ck_archive_versions_retry_count"),
        sa.CheckConstraint("status IN (\x27pending\x27, \x27uploading\x27, \x27archived\x27, \x27failed\x27, \x27needs_reconcile\x27, \x27needs_verification\x27, \x27unavailable\x27)", name="ck_archive_versions_status"),
        sa.ForeignKeyConstraint(("entry_id",), ("archive_entries.id",), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "version_number", name="uq_archive_versions_number"),
        sa.UniqueConstraint("entry_id", "sha256", name="uq_archive_versions_hash"),
    )
    op.create_index("ix_archive_versions_entry_status", "archive_versions", ("entry_id", "status"))
    op.create_index("ix_archive_versions_sha256", "archive_versions", ("sha256",))
    op.create_table(
        "archive_authorized_roots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("path_hash", sa.String(length=64), nullable=False),
        sa.Column("absolute_path", sa.Text(), nullable=False),
        sa.Column("volume_identity", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(), _updated_at(),
        sa.CheckConstraint("source IN (\x27native_picker\x27, \x27archive_source\x27, \x27both\x27)", name="ck_archive_authorized_roots_source"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path_hash"),
    )
    op.create_index("ix_archive_authorized_roots_active", "archive_authorized_roots", ("is_active",))
    op.create_table(
        "archive_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("entry_id", sa.String(length=36), nullable=True),
        sa.Column("version_id", sa.String(length=36), nullable=True),
        sa.Column("authorized_root_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("bytes_total", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("bytes_done", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("conflict_policy", sa.String(length=16), nullable=True),
        sa.Column("target_abs_path", sa.Text(), nullable=True),
        sa.Column("plan_data", sa.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(), _updated_at(),
        sa.CheckConstraint("kind IN (\x27archive\x27, \x27restore\x27, \x27reconcile\x27)", name="ck_archive_jobs_kind"),
        sa.CheckConstraint("status IN (\x27planned\x27, \x27pending\x27, \x27running\x27, \x27uploading\x27, \x27verifying\x27, \x27downloading\x27, \x27completed\x27, \x27skipped\x27, \x27compare\x27, \x27failed\x27, \x27interrupted\x27, \x27needs_reconcile\x27, \x27needs_verification\x27)", name="ck_archive_jobs_status"),
        sa.CheckConstraint("conflict_policy IS NULL OR conflict_policy IN (\x27skip\x27, \x27save_as\x27, \x27overwrite\x27, \x27compare\x27)", name="ck_archive_jobs_conflict_policy"),
        sa.CheckConstraint("bytes_total >= 0 AND bytes_done >= 0 AND bytes_done <= bytes_total", name="ck_archive_jobs_progress"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_archive_jobs_attempt_count"),
        sa.ForeignKeyConstraint(("entry_id",), ("archive_entries.id",), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(("version_id",), ("archive_versions.id",), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(("authorized_root_id",), ("archive_authorized_roots.id",), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_archive_jobs_status_created", "archive_jobs", ("status", "created_at"))
    op.create_index("ix_archive_jobs_entry", "archive_jobs", ("entry_id",))
    op.create_index("ix_archive_jobs_version", "archive_jobs", ("version_id",))
    op.create_table(
        "archive_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("entry_id", sa.String(length=36), nullable=True),
        sa.Column("version_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("bytes_done", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("bytes_total", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("bytes_done >= 0 AND bytes_total >= 0", name="ck_archive_events_progress"),
        sa.CheckConstraint("status IN (\x27planned\x27, \x27pending\x27, \x27running\x27, \x27uploading\x27, \x27verifying\x27, \x27downloading\x27, \x27completed\x27, \x27skipped\x27, \x27compare\x27, \x27failed\x27, \x27interrupted\x27, \x27needs_reconcile\x27, \x27needs_verification\x27)", name="ck_archive_events_status"),
        sa.ForeignKeyConstraint(("job_id",), ("archive_jobs.id",), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(("entry_id",), ("archive_entries.id",), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(("version_id",), ("archive_versions.id",), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_archive_events_job_created", "archive_events", ("job_id", "created_at"))
    op.create_index("ix_archive_events_entry", "archive_events", ("entry_id",))


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return
    if any(
        str(index.get("name")) == index_name
        for index in inspector.get_indexes(table_name)
    ):
        op.drop_index(index_name, table_name=table_name)


def _drop_table_if_exists(table_name: str) -> None:
    if sa.inspect(op.get_bind()).has_table(table_name):
        op.drop_table(table_name)


def downgrade() -> None:
    for index_name, (table_name, _columns) in reversed(
        tuple(_ARCHIVE_INDEXES.items())
    ):
        _drop_index_if_exists(index_name, table_name)
    for table_name in reversed(tuple(_ARCHIVE_TABLES)):
        _drop_table_if_exists(table_name)
