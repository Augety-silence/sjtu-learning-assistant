"""Add rich email bodies and attachment metadata.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("emails", sa.Column("body_html", sa.Text(), nullable=True))
    op.create_table(
        "email_attachments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("email_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.String(length=512), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("content_id", sa.Text(), nullable=True),
        sa.Column("disposition", sa.String(length=32), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("is_inline", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["email_id"], ["emails.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "email_id", "resource_id", name="uq_email_attachment_resource"
        ),
    )
    op.create_index(
        "ix_email_attachments_email_id",
        "email_attachments",
        ["email_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_email_attachments_email_id", table_name="email_attachments")
    op.drop_table("email_attachments")
    op.drop_column("emails", "body_html")
