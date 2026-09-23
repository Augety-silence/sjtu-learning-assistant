"""Add provider-neutral cloud files and Canvas submission audit rows.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-23
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    created_at, updated_at = _timestamps()
    op.create_table(
        "cloud_files",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=255), server_default="default", nullable=False),
        sa.Column("remote_id", sa.String(length=512), nullable=False),
        sa.Column("parent_remote_id", sa.String(length=512), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("is_directory", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("download_url", sa.Text(), nullable=True),
        sa.Column("raw_data", JSON_TYPE, nullable=False),
        created_at,
        updated_at,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "account_id", "remote_id", name="uq_cloud_files_identity"),
    )
    op.create_index(
        "ix_cloud_files_parent", "cloud_files", ["provider", "account_id", "parent_remote_id"]
    )

    created_at, updated_at = _timestamps()
    op.create_table(
        "submissions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("course_id", sa.BigInteger(), nullable=True),
        sa.Column("assignment_id", sa.BigInteger(), nullable=True),
        sa.Column("canvas_course_id", sa.String(length=128), nullable=False),
        sa.Column("canvas_assignment_id", sa.String(length=128), nullable=False),
        sa.Column("canvas_submission_id", sa.String(length=128), nullable=True),
        sa.Column("submission_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("cloud_file_id", sa.BigInteger(), nullable=True),
        sa.Column("local_filename", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("raw_data", JSON_TYPE, nullable=False),
        created_at,
        updated_at,
        sa.CheckConstraint(
            "status IN ('pending', 'submitted', 'verified', 'failed', 'requires_external_submission')",
            name="ck_submissions_status",
        ),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cloud_file_id"], ["cloud_files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_submissions_canvas_assignment",
        "submissions",
        ["canvas_course_id", "canvas_assignment_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_submissions_canvas_assignment", table_name="submissions")
    op.drop_table("submissions")
    op.drop_index("ix_cloud_files_parent", table_name="cloud_files")
    op.drop_table("cloud_files")
