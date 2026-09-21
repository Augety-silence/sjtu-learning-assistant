"""Add Canvas files, folders, modules, and module items metadata.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "course_folders",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("course_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_folder_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("files_count", sa.Integer(), nullable=True),
        sa.Column("folders_count", sa.Integer(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_folder_id"], ["course_folders.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
    )
    op.create_index(
        "ix_course_folders_course_id", "course_folders", ["course_id"], unique=False
    )

    op.create_table(
        "course_modules",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("course_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("workflow_state", sa.String(length=64), nullable=True),
        sa.Column("unlock_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("items_count", sa.Integer(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
    )
    op.create_index(
        "ix_course_modules_course_id", "course_modules", ["course_id"], unique=False
    )

    op.add_column("course_files", sa.Column("folder_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "course_files",
        sa.Column("hidden", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "course_files",
        sa.Column("locked", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "course_files",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "course_files",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "course_files",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_course_files_folder_id",
        "course_files",
        "course_folders",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_course_files_folder_id", "course_files", ["folder_id"], unique=False
    )

    op.create_table(
        "course_module_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("course_id", sa.BigInteger(), nullable=False),
        sa.Column("module_id", sa.BigInteger(), nullable=False),
        sa.Column("content_file_id", sa.BigInteger(), nullable=True),
        sa.Column("content_source_id", sa.String(length=128), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("item_type", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("indent", sa.Integer(), nullable=True),
        sa.Column("html_url", sa.Text(), nullable=True),
        sa.Column("api_url", sa.Text(), nullable=True),
        sa.Column("external_url", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["module_id"], ["course_modules.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["content_file_id"], ["course_files.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
    )
    op.create_index(
        "ix_course_module_items_course_id",
        "course_module_items",
        ["course_id"],
        unique=False,
    )
    op.create_index(
        "ix_course_module_items_module_position",
        "course_module_items",
        ["module_id", "position"],
        unique=False,
    )

    op.add_column(
        "items",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("items", "is_active")
    op.drop_index(
        "ix_course_module_items_module_position", table_name="course_module_items"
    )
    op.drop_index("ix_course_module_items_course_id", table_name="course_module_items")
    op.drop_table("course_module_items")
    op.drop_index("ix_course_files_folder_id", table_name="course_files")
    op.drop_constraint("fk_course_files_folder_id", "course_files", type_="foreignkey")
    op.drop_column("course_files", "deactivated_at")
    op.drop_column("course_files", "last_seen_at")
    op.drop_column("course_files", "is_active")
    op.drop_column("course_files", "locked")
    op.drop_column("course_files", "hidden")
    op.drop_column("course_files", "folder_id")
    op.drop_index("ix_course_modules_course_id", table_name="course_modules")
    op.drop_table("course_modules")
    op.drop_index("ix_course_folders_course_id", table_name="course_folders")
    op.drop_table("course_folders")
