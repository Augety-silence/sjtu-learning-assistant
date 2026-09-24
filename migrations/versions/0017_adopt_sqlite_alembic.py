"""Adopt Alembic as the authoritative schema version for desktop SQLite.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-24
"""
from __future__ import annotations

from typing import Sequence

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
