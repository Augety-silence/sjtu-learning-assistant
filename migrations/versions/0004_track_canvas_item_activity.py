"""Add active-state tracking for Canvas announcements and assignments.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in ("announcements", "assignments"):
        op.add_column(
            table_name,
            sa.Column(
                "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
            ),
        )
        op.add_column(
            table_name,
            sa.Column(
                "last_seen_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
        op.add_column(
            table_name,
            sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for table_name in ("assignments", "announcements"):
        op.drop_column(table_name, "deactivated_at")
        op.drop_column(table_name, "last_seen_at")
        op.drop_column(table_name, "is_active")
