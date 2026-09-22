"""SQLAlchemy models for locally persisted learning data."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class CrossDialectJSON(TypeDecorator):
    """Use JSONB on PostgreSQL without importing its dialect in SQLite builds."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB

            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


JSON_TYPE = CrossDialectJSON()
PRIMARY_KEY_TYPE = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Course(TimestampMixin, Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    course_code: Mapped[str | None] = mapped_column(Text)
    term_name: Mapped[str | None] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    announcements: Mapped[list["Announcement"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    files: Mapped[list["CourseFile"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    folders: Mapped[list["CourseFolder"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    modules: Mapped[list["CourseModule"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    module_items: Mapped[list["CourseModuleItem"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class Announcement(TimestampMixin, Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    course: Mapped[Course] = relationship(back_populates="announcements")

    __table_args__ = (Index("ix_announcements_posted_at", "posted_at"),)


class Assignment(TimestampMixin, Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    points_possible: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    submission_state: Mapped[str | None] = mapped_column(String(64))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    course: Mapped[Course] = relationship(back_populates="assignments")

    __table_args__ = (Index("ix_assignments_due_at", "due_at"),)


class Email(TimestampMixin, Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    sender_name: Mapped[str | None] = mapped_column(Text)
    sender_address: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    body_preview: Mapped[str | None] = mapped_column(Text)
    is_unread: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    __table_args__ = (Index("ix_emails_sent_at", "sent_at"),)


class CourseFolder(TimestampMixin, Base):
    __tablename__ = "course_folders"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    parent_folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_folders.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int | None] = mapped_column(Integer)
    files_count: Mapped[int | None] = mapped_column(Integer)
    folders_count: Mapped[int | None] = mapped_column(Integer)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    course: Mapped[Course] = relationship(back_populates="folders")

    __table_args__ = (Index("ix_course_folders_course_id", "course_id"),)


class CourseModule(TimestampMixin, Base):
    __tablename__ = "course_modules"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int | None] = mapped_column(Integer)
    workflow_state: Mapped[str | None] = mapped_column(String(64))
    unlock_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_count: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    course: Mapped[Course] = relationship(back_populates="modules")
    items: Mapped[list["CourseModuleItem"]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_course_modules_course_id", "course_id"),)


class CourseFile(TimestampMixin, Base):
    __tablename__ = "course_files"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_folders.id", ondelete="SET NULL")
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(255))
    size: Mapped[int | None] = mapped_column(BigInteger)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    download_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )
    download_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    downloaded_size: Mapped[int | None] = mapped_column(BigInteger)
    download_sha256: Mapped[str | None] = mapped_column(String(64))
    download_error: Mapped[str | None] = mapped_column(Text)
    downloaded_source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    course: Mapped[Course] = relationship(back_populates="files")

    __table_args__ = (
        CheckConstraint(
            "download_status IN ('pending', 'downloaded', 'failed')",
            name="ck_course_files_download_status",
        ),
        CheckConstraint(
            "download_attempts >= 0",
            name="ck_course_files_download_attempts_nonnegative",
        ),
        Index("ix_course_files_folder_id", "folder_id"),
        Index("ix_course_files_download_status", "download_status"),
    )


class CourseModuleItem(TimestampMixin, Base):
    __tablename__ = "course_module_items"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    module_id: Mapped[int] = mapped_column(
        ForeignKey("course_modules.id", ondelete="CASCADE"), nullable=False
    )
    content_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_files.id", ondelete="SET NULL")
    )
    content_source_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    item_type: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int | None] = mapped_column(Integer)
    indent: Mapped[int | None] = mapped_column(Integer)
    html_url: Mapped[str | None] = mapped_column(Text)
    api_url: Mapped[str | None] = mapped_column(Text)
    external_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    course: Mapped[Course] = relationship(back_populates="module_items")
    module: Mapped[CourseModule] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_course_module_items_course_id", "course_id"),
        Index("ix_course_module_items_module_position", "module_id", "position"),
    )


class UnifiedItem(TimestampMixin, Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL")
    )
    announcement_id: Mapped[int | None] = mapped_column(
        ForeignKey("announcements.id", ondelete="CASCADE")
    )
    assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE")
    )
    email_id: Mapped[int | None] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE")
    )
    course_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_files.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    sender: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "item_type", "source_id", name="uq_items_source"),
        UniqueConstraint("announcement_id", name="uq_items_announcement_id"),
        UniqueConstraint("assignment_id", name="uq_items_assignment_id"),
        UniqueConstraint("email_id", name="uq_items_email_id"),
        UniqueConstraint("course_file_id", name="uq_items_course_file_id"),
        CheckConstraint(
            "(CASE WHEN announcement_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN assignment_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN email_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN course_file_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
            name="ck_items_single_source_record",
        ),
        Index("ix_items_due_at", "due_at"),
        Index("ix_items_occurred_at", "occurred_at"),
    )


class NotificationEvent(TimestampMixin, Base):
    __tablename__ = "notification_events"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(512), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("items.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("event_key", name="uq_notification_events_event_key"),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'suppressed')",
            name="ck_notification_events_status",
        ),
        Index("ix_notification_events_event_type_status", "event_type", "status"),
        Index("ix_notification_events_item_id", "item_id"),
    )


class SyncState(Base):
    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("source", "resource", name="uq_sync_state_source_resource"),
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_sync_runs_started_at", "started_at"),)
