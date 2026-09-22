"""Add user-controlled Canvas file archive targets.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("course_files", sa.Column("manual_category", sa.String(length=32)))
    op.add_column("course_files", sa.Column("manual_folder_id", sa.BigInteger()))
    op.add_column(
        "course_files",
        sa.Column(
            "manual_override",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_course_files_manual_folder_id_course_folders",
        "course_files",
        "course_folders",
        ["manual_folder_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_course_files_manual_category",
        "course_files",
        "manual_category IS NULL OR manual_category IN "
        "('assignments', 'courseware', 'supplementary', 'other')",
    )
    op.create_check_constraint(
        "ck_course_files_manual_override",
        "course_files",
        "(manual_override AND manual_category IS NOT NULL) OR "
        "(NOT manual_override AND manual_category IS NULL AND manual_folder_id IS NULL)",
    )
    op.create_index(
        "ix_course_files_manual_folder_id",
        "course_files",
        ["manual_folder_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_course_files_manual_folder_id", table_name="course_files")
    op.drop_constraint(
        "ck_course_files_manual_override", "course_files", type_="check"
    )
    op.drop_constraint(
        "ck_course_files_manual_category", "course_files", type_="check"
    )
    op.drop_constraint(
        "fk_course_files_manual_folder_id_course_folders",
        "course_files",
        type_="foreignkey",
    )
    op.drop_column("course_files", "manual_override")
    op.drop_column("course_files", "manual_folder_id")
    op.drop_column("course_files", "manual_category")
