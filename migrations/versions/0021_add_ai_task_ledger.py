"""Add unified AI task, event and token usage ledger."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {
        name
        for name in ("ai_tasks", "ai_task_events", "ai_usage_records")
        if inspector.has_table(name)
    }
    if existing == {"ai_tasks", "ai_task_events", "ai_usage_records"}:
        # Fresh desktop databases are created from current SQLAlchemy metadata
        # before Alembic is stamped. A deliberately reset migration marker must
        # therefore be safe to replay without dropping persisted task history.
        return
    if existing:
        raise RuntimeError("AI task ledger schema is only partially installed")
    op.create_table(
        "ai_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("page_id", sa.String(32), nullable=False),
        sa.Column("capability_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(64)),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error_text", sa.Text()),
        sa.Column("input_token_budget", sa.Integer(), nullable=False),
        sa.Column("output_token_budget", sa.Integer(), nullable=False),
        sa.Column("max_model_calls", sa.Integer(), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("charged_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unknown_usage_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_ai_tasks_status",
        ),
        sa.CheckConstraint("input_token_budget > 0", name="ck_ai_tasks_input_budget"),
        sa.CheckConstraint("output_token_budget > 0", name="ck_ai_tasks_output_budget"),
        sa.CheckConstraint("max_model_calls > 0", name="ck_ai_tasks_max_calls"),
    )
    op.create_index("ix_ai_tasks_page_created", "ai_tasks", ["page_id", "created_at"])
    op.create_index("ix_ai_tasks_status_updated", "ai_tasks", ["status", "updated_at"])
    op.create_table(
        "ai_task_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("ai_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_id", "sequence", name="uq_ai_task_events_sequence"),
    )
    op.create_index("ix_ai_task_events_task", "ai_task_events", ["task_id", "sequence"])
    op.create_table(
        "ai_usage_records",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("ai_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_index", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="openai-compatible"),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("charged_output_tokens", sa.Integer(), nullable=False),
        sa.Column("usage_status", sa.String(16), nullable=False),
        sa.Column("finish_reason", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_id", "call_index", name="uq_ai_usage_task_call"),
        sa.CheckConstraint("usage_status IN ('reported', 'partial', 'unknown')", name="ck_ai_usage_status"),
    )
    op.create_index("ix_ai_usage_task", "ai_usage_records", ["task_id", "call_index"])
    op.create_index("ix_ai_usage_created", "ai_usage_records", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_usage_records")
    op.drop_table("ai_task_events")
    op.drop_table("ai_tasks")
