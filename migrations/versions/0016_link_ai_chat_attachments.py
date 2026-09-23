"""Link AI chat messages to application-managed attachments.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-23
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_message_attachments",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("managed_file_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 20",
            name="ck_ai_chat_message_attachments_position",
        ),
        sa.ForeignKeyConstraint(
            ["managed_file_id"], ["ai_managed_files.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["ai_chat_messages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("message_id", "managed_file_id"),
        sa.UniqueConstraint(
            "message_id",
            "position",
            name="uq_ai_chat_message_attachments_position",
        ),
    )
    op.create_index(
        "ix_ai_chat_message_attachments_file",
        "ai_chat_message_attachments",
        ["managed_file_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_chat_message_attachments_file",
        table_name="ai_chat_message_attachments",
    )
    op.drop_table("ai_chat_message_attachments")
