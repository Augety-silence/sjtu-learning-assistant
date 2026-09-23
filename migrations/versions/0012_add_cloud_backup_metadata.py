"""Add cloud backup metadata to local file records.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-23
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("course_files") as batch:
        batch.add_column(sa.Column("cloud_path", sa.Text(), nullable=True))
        batch.add_column(sa.Column("cloud_size", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("cloud_backed_up_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.drop_constraint("ck_course_files_download_status", type_="check")
        batch.create_check_constraint(
            "ck_course_files_download_status",
            "download_status IN ('pending', 'downloaded', 'failed', 'cloud_only')",
        )

    with op.batch_alter_table("email_attachments") as batch:
        batch.add_column(sa.Column("cloud_path", sa.Text(), nullable=True))
        batch.add_column(sa.Column("cloud_size", sa.BigInteger(), nullable=True))
        batch.add_column(
            sa.Column("cloud_backed_up_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE course_files SET download_status = 'pending' "
            "WHERE download_status = 'cloud_only'"
        )
    )
    with op.batch_alter_table("email_attachments") as batch:
        batch.drop_column("cloud_backed_up_at")
        batch.drop_column("cloud_size")
        batch.drop_column("cloud_path")

    with op.batch_alter_table("course_files") as batch:
        batch.drop_constraint("ck_course_files_download_status", type_="check")
        batch.create_check_constraint(
            "ck_course_files_download_status",
            "download_status IN ('pending', 'downloaded', 'failed')",
        )
        batch.drop_column("cloud_backed_up_at")
        batch.drop_column("cloud_size")
        batch.drop_column("cloud_path")
