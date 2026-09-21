"""Add CourseFile archive download state.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "course_files",
        sa.Column(
            "download_status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column(
        "course_files",
        sa.Column(
            "download_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "course_files",
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "course_files",
        sa.Column("downloaded_size", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "course_files",
        sa.Column("download_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "course_files",
        sa.Column("download_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "course_files",
        sa.Column(
            "downloaded_source_updated_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_check_constraint(
        "ck_course_files_download_status",
        "course_files",
        "download_status IN ('pending', 'downloaded', 'failed')",
    )
    op.create_check_constraint(
        "ck_course_files_download_attempts_nonnegative",
        "course_files",
        "download_attempts >= 0",
    )
    op.create_index(
        "ix_course_files_download_status",
        "course_files",
        ["download_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_course_files_download_status", table_name="course_files")
    op.drop_constraint(
        "ck_course_files_download_attempts_nonnegative",
        "course_files",
        type_="check",
    )
    op.drop_constraint(
        "ck_course_files_download_status", "course_files", type_="check"
    )
    op.drop_column("course_files", "downloaded_source_updated_at")
    op.drop_column("course_files", "download_error")
    op.drop_column("course_files", "download_sha256")
    op.drop_column("course_files", "downloaded_size")
    op.drop_column("course_files", "downloaded_at")
    op.drop_column("course_files", "download_attempts")
    op.drop_column("course_files", "download_status")
