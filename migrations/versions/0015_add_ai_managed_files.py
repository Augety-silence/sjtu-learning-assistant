"""Add application-managed AI files and searchable text derivatives.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-23
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_managed_files",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("controlled_relpath", sa.Text(), nullable=True),
        sa.Column("cloud_provider", sa.String(length=64), nullable=True),
        sa.Column("cloud_remote_id", sa.String(length=512), nullable=True),
        sa.Column("cloud_path", sa.Text(), nullable=True),
        sa.Column("cloud_size", sa.BigInteger(), nullable=True),
        sa.Column("cloud_sha256", sa.String(length=64), nullable=True),
        sa.Column("cloud_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('local', 'cloud_only', 'failed')", name="ck_ai_managed_files_status"),
        sa.CheckConstraint("size >= 0", name="ck_ai_managed_files_size_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"),
    )
    op.create_index("ix_ai_managed_files_status", "ai_managed_files", ["status"])
    op.create_table(
        "ai_file_derivatives",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("managed_file_id", sa.BigInteger(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("text_status", sa.String(length=24), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("extractor", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "text_status IN ('pending', 'ready', 'unsupported', 'failed', 'unavailable')",
            name="ck_ai_file_derivatives_text_status",
        ),
        sa.ForeignKeyConstraint(["managed_file_id"], ["ai_managed_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("managed_file_id"),
    )
    op.create_index(
        "ix_ai_file_derivatives_text_status", "ai_file_derivatives", ["text_status"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_file_derivatives_text_status", table_name="ai_file_derivatives")
    op.drop_table("ai_file_derivatives")
    op.drop_index("ix_ai_managed_files_status", table_name="ai_managed_files")
    op.drop_table("ai_managed_files")
