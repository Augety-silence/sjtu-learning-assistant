"""Add safely extracted plain-text email bodies.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("emails", sa.Column("body_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("emails", "body_text")
