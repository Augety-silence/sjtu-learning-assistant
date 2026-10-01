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
    submissions: Mapped[list["Submission"]] = relationship(back_populates="course")
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
    submissions: Mapped[list["Submission"]] = relationship(back_populates="assignment")

    __table_args__ = (Index("ix_assignments_due_at", "due_at"),)


class CloudFile(TimestampMixin, Base):
    """Provider-neutral cloud file metadata; no provider implementation is imported."""

    __tablename__ = "cloud_files"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(
        String(255), default="default", server_default="default", nullable=False
    )
    remote_id: Mapped[str] = mapped_column(String(512), nullable=False)
    parent_remote_id: Mapped[str | None] = mapped_column(String(512))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(Text)
    is_directory: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    size: Mapped[int | None] = mapped_column(BigInteger)
    content_type: Mapped[str | None] = mapped_column(String(255))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    download_url: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    submissions: Mapped[list["Submission"]] = relationship(back_populates="cloud_file")

    __table_args__ = (
        UniqueConstraint("provider", "account_id", "remote_id", name="uq_cloud_files_identity"),
        Index("ix_cloud_files_parent", "provider", "account_id", "parent_remote_id"),
    )


class Submission(TimestampMixin, Base):
    """Auditable local record of a Canvas submission workflow."""

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("assignments.id", ondelete="SET NULL"))
    canvas_course_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canvas_assignment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canvas_submission_id: Mapped[str | None] = mapped_column(String(128))
    submission_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cloud_file_id: Mapped[int | None] = mapped_column(ForeignKey("cloud_files.id", ondelete="SET NULL"))
    local_filename: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    course: Mapped[Course | None] = relationship(back_populates="submissions")
    assignment: Mapped[Assignment | None] = relationship(back_populates="submissions")
    cloud_file: Mapped[CloudFile | None] = relationship(back_populates="submissions")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'submitted', 'verified', 'failed', 'requires_external_submission')",
            name="ck_submissions_status",
        ),
        Index("ix_submissions_canvas_assignment", "canvas_course_id", "canvas_assignment_id"),
    )


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
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    is_unread: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict, nullable=False)

    attachments: Mapped[list["EmailAttachment"]] = relationship(
        back_populates="email", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_emails_sent_at", "sent_at"),)


class EmailAttachment(TimestampMixin, Base):
    __tablename__ = "email_attachments"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    email_id: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), nullable=False
    )
    resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    content_id: Mapped[str | None] = mapped_column(Text)
    disposition: Mapped[str | None] = mapped_column(String(32))
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_inline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    local_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    cloud_path: Mapped[str | None] = mapped_column(Text)
    cloud_size: Mapped[int | None] = mapped_column(BigInteger)
    cloud_backed_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    email: Mapped[Email] = relationship(back_populates="attachments")

    __table_args__ = (
        UniqueConstraint("email_id", "resource_id", name="uq_email_attachment_resource"),
        Index("ix_email_attachments_email_id", "email_id"),
    )


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
    cloud_path: Mapped[str | None] = mapped_column(Text)
    cloud_size: Mapped[int | None] = mapped_column(BigInteger)
    cloud_backed_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    ai_category: Mapped[str | None] = mapped_column(String(32))
    ai_fingerprint: Mapped[str | None] = mapped_column(String(64))
    ai_model: Mapped[str | None] = mapped_column(String(64))
    ai_classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_category: Mapped[str | None] = mapped_column(String(32))
    manual_folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_folders.id", ondelete="SET NULL")
    )
    manual_override: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
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
            "download_status IN ('pending', 'downloaded', 'failed', 'cloud_only')",
            name="ck_course_files_download_status",
        ),
        CheckConstraint(
            "download_attempts >= 0",
            name="ck_course_files_download_attempts_nonnegative",
        ),
        CheckConstraint(
            "manual_category IS NULL OR manual_category IN "
            "('assignments', 'courseware', 'supplementary', 'other')",
            name="ck_course_files_manual_category",
        ),
        CheckConstraint(
            "(manual_override AND manual_category IS NOT NULL) OR "
            "(NOT manual_override AND manual_category IS NULL AND manual_folder_id IS NULL)",
            name="ck_course_files_manual_override",
        ),
        Index("ix_course_files_folder_id", "folder_id"),
        Index("ix_course_files_manual_folder_id", "manual_folder_id"),
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


class AIManagedFile(TimestampMixin, Base):
    """A deduplicated copy owned by the application, never the user's source file."""

    __tablename__ = "ai_managed_files"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="local")
    controlled_relpath: Mapped[str | None] = mapped_column(Text)
    cloud_provider: Mapped[str | None] = mapped_column(String(64))
    cloud_remote_id: Mapped[str | None] = mapped_column(String(512))
    cloud_path: Mapped[str | None] = mapped_column(Text)
    cloud_size: Mapped[int | None] = mapped_column(BigInteger)
    cloud_sha256: Mapped[str | None] = mapped_column(String(64))
    cloud_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    derivative: Mapped["AIFileDerivative | None"] = relationship(
        back_populates="managed_file", cascade="all, delete-orphan", uselist=False
    )
    message_links: Mapped[list["AIChatMessageAttachment"]] = relationship(
        back_populates="managed_file"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('local', 'cloud_only', 'failed')",
            name="ck_ai_managed_files_status",
        ),
        CheckConstraint("size >= 0", name="ck_ai_managed_files_size_nonnegative"),
        Index("ix_ai_managed_files_status", "status"),
    )


class AIFileDerivative(TimestampMixin, Base):
    """Searchable, bounded text derivative for an application-managed file."""

    __tablename__ = "ai_file_derivatives"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    managed_file_id: Mapped[int] = mapped_column(
        ForeignKey("ai_managed_files.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    summary: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    text_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    text: Mapped[str | None] = mapped_column(Text)
    extractor: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)

    managed_file: Mapped[AIManagedFile] = relationship(back_populates="derivative")

    __table_args__ = (
        CheckConstraint(
            "text_status IN ('pending', 'ready', 'unsupported', 'failed', 'unavailable')",
            name="ck_ai_file_derivatives_text_status",
        ),
        Index("ix_ai_file_derivatives_text_status", "text_status"),
    )


class AIChatSession(TimestampMixin, Base):
    __tablename__ = "ai_chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    thinking_depth: Mapped[str] = mapped_column(String(16), nullable=False)

    preset_id: Mapped[str] = mapped_column(
        String(32), default="general", server_default="general", nullable=False
    )

    messages: Mapped[list["AIChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    traces: Mapped[list["AIAgentTrace"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "thinking_depth IN ('quick', 'standard', 'deep')",
            name="ck_ai_chat_sessions_thinking_depth",
        ),
        Index("ix_ai_chat_sessions_updated_at", "updated_at"),
    )


class AIChatMessage(Base):
    __tablename__ = "ai_chat_messages"

    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning_content: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_agent_traces.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[AIChatSession] = relationship(back_populates="messages")
    trace: Mapped["AIAgentTrace | None"] = relationship(back_populates="messages")
    attachment_links: Mapped[list["AIChatMessageAttachment"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="AIChatMessageAttachment.position",
    )

    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_ai_chat_messages_sequence"),
        CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_ai_chat_messages_role"
        ),
        Index("ix_ai_chat_messages_session", "session_id", "sequence"),
        Index("ix_ai_chat_messages_trace", "trace_id"),
    )


class AIChatMessageAttachment(Base):
    """Stable ordered link from a user chat message to a managed attachment."""

    __tablename__ = "ai_chat_message_attachments"

    message_id: Mapped[int] = mapped_column(
        ForeignKey("ai_chat_messages.id", ondelete="CASCADE"), primary_key=True
    )
    managed_file_id: Mapped[int] = mapped_column(
        ForeignKey("ai_managed_files.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    message: Mapped[AIChatMessage] = relationship(back_populates="attachment_links")
    managed_file: Mapped[AIManagedFile] = relationship(back_populates="message_links")

    __table_args__ = (
        UniqueConstraint(
            "message_id", "position", name="uq_ai_chat_message_attachments_position"
        ),
        CheckConstraint(
            "position >= 0 AND position < 20",
            name="ck_ai_chat_message_attachments_position",
        ),
        Index("ix_ai_chat_message_attachments_file", "managed_file_id"),
    )


class AIAgentTrace(Base):
    __tablename__ = "ai_agent_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("ai_chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    preset_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    steps: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_runs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_TYPE, default=list, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[AIChatSession] = relationship(back_populates="traces")
    messages: Mapped[list[AIChatMessage]] = relationship(back_populates="trace")

    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'max_steps', 'timeout', 'failed')",
            name="ck_ai_agent_traces_status",
        ),
        CheckConstraint("steps >= 0 AND steps <= 6", name="ck_ai_agent_traces_steps"),
        Index("ix_ai_agent_traces_session", "session_id", "created_at"),
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


class ArchiveEntry(TimestampMixin, Base):
    # Stable identity for one original path, independent of later content versions.

    __tablename__ = "archive_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    path_identity: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    volume_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    original_abs_path: Mapped[str | None] = mapped_column(Text)
    archive_root_snapshot: Mapped[str | None] = mapped_column(Text)
    relative_path: Mapped[str | None] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    restore_capability: Mapped[str] = mapped_column(String(32), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="user_file")
    source_record_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    last_error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    versions: Mapped[list["ArchiveVersion"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="ArchiveVersion.version_number"
    )
    jobs: Mapped[list["ArchiveJob"]] = relationship(back_populates="entry")

    __table_args__ = (
        UniqueConstraint("source_kind", "source_record_id", name="uq_archive_entries_legacy_source"),
        CheckConstraint(
            "restore_capability IN ('original_path', 'managed_location', 'choose_location')",
            name="ck_archive_entries_restore_capability",
        ),
        CheckConstraint(
            "status IN ('active', 'archived', 'failed', 'legacy')",
            name="ck_archive_entries_status",
        ),
        CheckConstraint("retry_count >= 0", name="ck_archive_entries_retry_count"),
        Index("ix_archive_entries_status", "status"),
        Index("ix_archive_entries_filename", "filename"),
    )


class ArchiveVersion(TimestampMixin, Base):
    __tablename__ = "archive_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entry_id: Mapped[str] = mapped_column(
        ForeignKey("archive_entries.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(255))
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    cloud_provider: Mapped[str | None] = mapped_column(String(64))
    cloud_remote_id: Mapped[str | None] = mapped_column(String(512))
    cloud_remote_path: Mapped[str | None] = mapped_column(Text)
    cloud_etag: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    last_error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    entry: Mapped[ArchiveEntry] = relationship(back_populates="versions")
    jobs: Mapped[list["ArchiveJob"]] = relationship(back_populates="version")

    __table_args__ = (
        UniqueConstraint("entry_id", "version_number", name="uq_archive_versions_number"),
        UniqueConstraint("entry_id", "sha256", name="uq_archive_versions_hash"),
        CheckConstraint("version_number > 0", name="ck_archive_versions_number"),
        CheckConstraint("size >= 0", name="ck_archive_versions_size"),
        CheckConstraint("mtime_ns >= 0", name="ck_archive_versions_mtime"),
        CheckConstraint("retry_count >= 0", name="ck_archive_versions_retry_count"),
        CheckConstraint(
            "status IN ('pending', 'uploading', 'archived', 'failed', 'needs_reconcile', 'needs_verification', 'unavailable')",
            name="ck_archive_versions_status",
        ),
        Index("ix_archive_versions_entry_status", "entry_id", "status"),
        Index("ix_archive_versions_sha256", "sha256"),
    )


class ArchiveAuthorizedRoot(TimestampMixin, Base):
    __tablename__ = "archive_authorized_roots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    path_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    absolute_path: Mapped[str] = mapped_column(Text, nullable=False)
    volume_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    jobs: Mapped[list["ArchiveJob"]] = relationship(back_populates="authorized_root")

    __table_args__ = (
        CheckConstraint(
            "source IN ('native_picker', 'archive_source', 'both')",
            name="ck_archive_authorized_roots_source",
        ),
        Index("ix_archive_authorized_roots_active", "is_active"),
    )


class ArchiveJob(TimestampMixin, Base):
    __tablename__ = "archive_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_id: Mapped[str | None] = mapped_column(
        ForeignKey("archive_entries.id", ondelete="SET NULL")
    )
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("archive_versions.id", ondelete="SET NULL")
    )
    authorized_root_id: Mapped[str | None] = mapped_column(
        ForeignKey("archive_authorized_roots.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    bytes_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    bytes_done: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    conflict_policy: Mapped[str | None] = mapped_column(String(16))
    target_abs_path: Mapped[str | None] = mapped_column(Text)
    plan_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    entry: Mapped[ArchiveEntry | None] = relationship(back_populates="jobs")
    version: Mapped[ArchiveVersion | None] = relationship(back_populates="jobs")
    authorized_root: Mapped[ArchiveAuthorizedRoot | None] = relationship(back_populates="jobs")
    events: Mapped[list["ArchiveEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ArchiveEvent.created_at"
    )

    __table_args__ = (
        CheckConstraint("kind IN ('archive', 'restore', 'reconcile')", name="ck_archive_jobs_kind"),
        CheckConstraint(
            "status IN ('planned', 'pending', 'running', 'uploading', 'verifying', 'downloading', 'completed', 'skipped', 'compare', 'failed', 'interrupted', 'needs_reconcile', 'needs_verification')",
            name="ck_archive_jobs_status",
        ),
        CheckConstraint(
            "conflict_policy IS NULL OR conflict_policy IN ('skip', 'save_as', 'overwrite', 'compare')",
            name="ck_archive_jobs_conflict_policy",
        ),
        CheckConstraint(
            "bytes_total >= 0 AND bytes_done >= 0 AND bytes_done <= bytes_total",
            name="ck_archive_jobs_progress",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_archive_jobs_attempt_count"),
        Index("ix_archive_jobs_status_created", "status", "created_at"),
        Index("ix_archive_jobs_entry", "entry_id"),
        Index("ix_archive_jobs_version", "version_id"),
    )


class ArchiveEvent(Base):
    __tablename__ = "archive_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("archive_jobs.id", ondelete="CASCADE"), nullable=False
    )
    entry_id: Mapped[str | None] = mapped_column(
        ForeignKey("archive_entries.id", ondelete="SET NULL")
    )
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("archive_versions.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    bytes_done: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    bytes_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped[ArchiveJob] = relationship(back_populates="events")

    __table_args__ = (
        CheckConstraint("bytes_done >= 0 AND bytes_total >= 0", name="ck_archive_events_progress"),
        CheckConstraint(
            "status IN ('planned', 'pending', 'running', 'uploading', 'verifying', 'downloading', 'completed', 'skipped', 'compare', 'failed', 'interrupted', 'needs_reconcile', 'needs_verification')",
            name="ck_archive_events_status",
        ),
        Index("ix_archive_events_job_created", "job_id", "created_at"),
        Index("ix_archive_events_entry", "entry_id"),
    )


class CanonicalCourse(TimestampMixin, Base):
    __tablename__ = "canonical_courses"
    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    course_code: Mapped[str | None] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    mapping_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    __table_args__ = (
        UniqueConstraint("course_code", "normalized_name", name="uq_canonical_course_identity"),
        CheckConstraint("mapping_status IN (\x27mapped\x27, \x27pending\x27)", name="ck_canonical_course_mapping_status"),
    )


class TimetableProviderConnection(TimestampMixin, Base):
    __tablename__ = "timetable_provider_connections"
    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    __table_args__ = (
        CheckConstraint("state IN (\x27awaiting_configuration\x27, \x27ready\x27, \x27error\x27)", name="ck_timetable_provider_state"),
    )


class TimetableCourse(TimestampMixin, Base):
    __tablename__ = "timetable_courses"
    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    provider_connection_id: Mapped[int | None] = mapped_column(ForeignKey("timetable_provider_connections.id", ondelete="SET NULL"))
    canonical_course_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_courses.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    course_code: Mapped[str | None] = mapped_column(String(128))
    course_name: Mapped[str] = mapped_column(Text, nullable=False)
    term: Mapped[str | None] = mapped_column(String(128))
    mapping_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_timetable_course_source"),
        CheckConstraint("mapping_status IN (\x27mapped\x27, \x27pending\x27)", name="ck_timetable_course_mapping_status"),
    )


class TimetableSession(TimestampMixin, Base):
    __tablename__ = "timetable_sessions"
    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    timetable_course_id: Mapped[int] = mapped_column(ForeignKey("timetable_courses.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    week: Mapped[int | None] = mapped_column(Integer)
    day: Mapped[int | None] = mapped_column(Integer)
    period: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[int | None] = mapped_column(Integer)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    classroom: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    __table_args__ = (
        UniqueConstraint("timetable_course_id", "source_id", name="uq_timetable_session_source"),
        CheckConstraint("week IS NULL OR week > 0", name="ck_timetable_session_week"),
        CheckConstraint("day IS NULL OR (day >= 1 AND day <= 7)", name="ck_timetable_session_day"),
        CheckConstraint("duration IS NULL OR duration > 0", name="ck_timetable_session_duration"),
        CheckConstraint("finish_at > start_at", name="ck_timetable_session_times"),
        Index("ix_timetable_sessions_range", "start_at", "finish_at"),
    )


class TimetableImportRun(TimestampMixin, Base):
    __tablename__ = "timetable_import_runs"
    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    source_format: Mapped[str] = mapped_column(String(32), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    imported_courses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_courses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list[Any]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    __table_args__ = (CheckConstraint("status IN (\x27committed\x27, \x27failed\x27)", name="ck_timetable_import_status"),)


class TimetableImportAudit(Base):
    __tablename__ = "timetable_import_audit"
    id: Mapped[int] = mapped_column(PRIMARY_KEY_TYPE, Identity(), primary_key=True)
    import_run_id: Mapped[int] = mapped_column(ForeignKey("timetable_import_runs.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        CheckConstraint("action IN (\x27inserted\x27, \x27updated\x27, \x27unchanged\x27, \x27deleted\x27)", name="ck_timetable_audit_action"),
        Index("ix_timetable_audit_run", "import_run_id"),
    )
