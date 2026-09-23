"""Add local agent presets and safe execution traces.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-23
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_sessions",
        sa.Column("preset_id", sa.String(length=32), server_default="general", nullable=False),
    )
    op.create_table(
        "ai_agent_traces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("preset_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("tool_runs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('completed', 'max_steps', 'timeout', 'failed')",
            name="ck_ai_agent_traces_status",
        ),
        sa.CheckConstraint("steps >= 0 AND steps <= 6", name="ck_ai_agent_traces_steps"),
        sa.ForeignKeyConstraint(["session_id"], ["ai_chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_traces_session", "ai_agent_traces", ["session_id", "created_at"]
    )
    op.add_column(
        "ai_chat_messages", sa.Column("trace_id", sa.String(length=36), nullable=True)
    )
    op.create_foreign_key(
        "fk_ai_chat_messages_trace",
        "ai_chat_messages",
        "ai_agent_traces",
        ["trace_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_ai_chat_messages_trace", "ai_chat_messages", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_chat_messages_trace", table_name="ai_chat_messages")
    op.drop_constraint("fk_ai_chat_messages_trace", "ai_chat_messages", type_="foreignkey")
    op.drop_column("ai_chat_messages", "trace_id")
    op.drop_index("ix_ai_agent_traces_session", table_name="ai_agent_traces")
    op.drop_table("ai_agent_traces")
    op.drop_column("ai_chat_sessions", "preset_id")
