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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    course_code: Mapped[str | None] = mapped_column(Text)
    term_name: Mapped[str | None] = mapped_column(Text)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    announcements: Mapped[list["Announcement"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    files: Mapped[list["CourseFile"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class Announcement(TimestampMixin, Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    course: Mapped[Course] = relationship(back_populates="announcements")

    __table_args__ = (Index("ix_announcements_posted_at", "posted_at"),)


class Assignment(TimestampMixin, Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
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
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    course: Mapped[Course] = relationship(back_populates="assignments")

    __table_args__ = (Index("ix_assignments_due_at", "due_at"),)


class Email(TimestampMixin, Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    sender_name: Mapped[str | None] = mapped_column(Text)
    sender_address: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    body_preview: Mapped[str | None] = mapped_column(Text)
    is_unread: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (Index("ix_emails_sent_at", "sent_at"),)


class CourseFile(TimestampMixin, Base):
    __tablename__ = "course_files"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(255))
    size: Mapped[int | None] = mapped_column(BigInteger)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    course: Mapped[Course] = relationship(back_populates="files")


class UnifiedItem(TimestampMixin, Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
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
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("source", "item_type", "source_id", name="uq_items_source"),
        UniqueConstraint("announcement_id", name="uq_items_announcement_id"),
        UniqueConstraint("assignment_id", name="uq_items_assignment_id"),
        UniqueConstraint("email_id", name="uq_items_email_id"),
        UniqueConstraint("course_file_id", name="uq_items_course_file_id"),
        CheckConstraint(
            "num_nonnulls(announcement_id, assignment_id, email_id, course_file_id) <= 1",
            name="ck_items_single_source_record",
        ),
        Index("ix_items_due_at", "due_at"),
        Index("ix_items_occurred_at", "occurred_at"),
    )


class SyncState(Base):
    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
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

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
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
