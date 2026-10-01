"""Add provider-neutral timetable cache and import audit tables."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps():
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    table_names = {
        "canonical_courses",
        "timetable_provider_connections",
        "timetable_courses",
        "timetable_sessions",
        "timetable_import_runs",
        "timetable_import_audit",
    }
    present = table_names.intersection(sa.inspect(op.get_bind()).get_table_names())
    if present == table_names:
        return
    if present:
        raise RuntimeError(
            f"0019 found a partial timetable schema: {sorted(present)!r}"
        )
    op.create_table(
        "canonical_courses",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("course_code", sa.String(128)),
        sa.Column("normalized_name", sa.String(512), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("mapping_status", sa.String(24), nullable=False),
        *timestamps(),
        sa.UniqueConstraint(
            "course_code", "normalized_name", name="uq_canonical_course_identity"
        ),
        sa.CheckConstraint(
            "mapping_status IN ('mapped', 'pending')",
            name="ck_canonical_course_mapping_status",
        ),
    )
    op.create_table(
        "timetable_provider_connections",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("configuration", sa.JSON(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "state IN ('awaiting_configuration', 'ready', 'error')",
            name="ck_timetable_provider_state",
        ),
    )
    op.create_table(
        "timetable_courses",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column(
            "provider_connection_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("timetable_provider_connections.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "canonical_course_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("canonical_courses.id", ondelete="SET NULL"),
        ),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("course_code", sa.String(128)),
        sa.Column("course_name", sa.Text(), nullable=False),
        sa.Column("term", sa.String(128)),
        sa.Column("mapping_status", sa.String(24), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("source", "source_id", name="uq_timetable_course_source"),
        sa.CheckConstraint(
            "mapping_status IN ('mapped', 'pending')",
            name="ck_timetable_course_mapping_status",
        ),
    )
    op.create_table(
        "timetable_sessions",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column(
            "timetable_course_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("timetable_courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(512), nullable=False),
        sa.Column("week", sa.Integer()),
        sa.Column("day", sa.Integer()),
        sa.Column("period", sa.Integer()),
        sa.Column("duration", sa.Integer()),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finish_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classroom", sa.Text()),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint(
            "timetable_course_id", "source_id", name="uq_timetable_session_source"
        ),
        sa.CheckConstraint(
            "week IS NULL OR week > 0", name="ck_timetable_session_week"
        ),
        sa.CheckConstraint(
            "day IS NULL OR (day >= 1 AND day <= 7)", name="ck_timetable_session_day"
        ),
        sa.CheckConstraint(
            "duration IS NULL OR duration > 0", name="ck_timetable_session_duration"
        ),
        sa.CheckConstraint("finish_at > start_at", name="ck_timetable_session_times"),
    )
    op.create_index(
        "ix_timetable_sessions_range", "timetable_sessions", ("start_at", "finish_at")
    )
    op.create_table(
        "timetable_import_runs",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("source_format", sa.String(32), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("imported_courses", sa.Integer(), nullable=False),
        sa.Column("imported_sessions", sa.Integer(), nullable=False),
        sa.Column("updated_sessions", sa.Integer(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "status IN ('committed', 'failed')", name="ck_timetable_import_status"
        ),
    )
    op.create_table(
        "timetable_import_audit",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column(
            "import_run_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("timetable_import_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(512), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('inserted', 'updated', 'unchanged')",
            name="ck_timetable_audit_action",
        ),
    )
    op.create_index(
        "ix_timetable_audit_run", "timetable_import_audit", ("import_run_id",)
    )


def downgrade() -> None:
    op.drop_index("ix_timetable_audit_run", table_name="timetable_import_audit")
    op.drop_table("timetable_import_audit")
    op.drop_table("timetable_import_runs")
    op.drop_index("ix_timetable_sessions_range", table_name="timetable_sessions")
    op.drop_table("timetable_sessions")
    op.drop_table("timetable_courses")
    op.drop_table("timetable_provider_connections")
    op.drop_table("canonical_courses")
