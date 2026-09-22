"""Add incremental AI classification cache for Canvas files.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("course_files", sa.Column("ai_category", sa.String(length=32)))
    op.add_column("course_files", sa.Column("ai_fingerprint", sa.String(length=64)))
    op.add_column("course_files", sa.Column("ai_model", sa.String(length=64)))
    op.add_column(
        "course_files", sa.Column("ai_classified_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.drop_column("course_files", "ai_classified_at")
    op.drop_column("course_files", "ai_model")
    op.drop_column("course_files", "ai_fingerprint")
    op.drop_column("course_files", "ai_category")
