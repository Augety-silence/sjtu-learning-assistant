"""Add explicit unified-item source relationships.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("items", sa.Column("announcement_id", sa.BigInteger(), nullable=True))
    op.add_column("items", sa.Column("assignment_id", sa.BigInteger(), nullable=True))
    op.add_column("items", sa.Column("email_id", sa.BigInteger(), nullable=True))
    op.add_column("items", sa.Column("course_file_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_items_announcement_id",
        "items",
        "announcements",
        ["announcement_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_items_assignment_id",
        "items",
        "assignments",
        ["assignment_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_items_email_id",
        "items",
        "emails",
        ["email_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_items_course_file_id",
        "items",
        "course_files",
        ["course_file_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_items_announcement_id", "items", ["announcement_id"]
    )
    op.create_unique_constraint("uq_items_assignment_id", "items", ["assignment_id"])
    op.create_unique_constraint("uq_items_email_id", "items", ["email_id"])
    op.create_unique_constraint("uq_items_course_file_id", "items", ["course_file_id"])
    op.create_check_constraint(
        "ck_items_single_source_record",
        "items",
        "num_nonnulls(announcement_id, assignment_id, email_id, course_file_id) <= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_items_single_source_record", "items", type_="check")
    op.drop_constraint("uq_items_course_file_id", "items", type_="unique")
    op.drop_constraint("uq_items_email_id", "items", type_="unique")
    op.drop_constraint("uq_items_assignment_id", "items", type_="unique")
    op.drop_constraint("uq_items_announcement_id", "items", type_="unique")
    op.drop_constraint("fk_items_course_file_id", "items", type_="foreignkey")
    op.drop_constraint("fk_items_email_id", "items", type_="foreignkey")
    op.drop_constraint("fk_items_assignment_id", "items", type_="foreignkey")
    op.drop_constraint("fk_items_announcement_id", "items", type_="foreignkey")
    op.drop_column("items", "course_file_id")
    op.drop_column("items", "email_id")
    op.drop_column("items", "assignment_id")
    op.drop_column("items", "announcement_id")
