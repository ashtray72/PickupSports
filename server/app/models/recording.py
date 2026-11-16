"""
Recorded-video pointers and processing jobs (SYS-07, SYS-09, NFR-04, NFR-08).
The video bytes live in object storage; the database only stores where they are.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from server.app.db.base import UUID_PK, Base, pg_enum


class UploadStatus(str, enum.Enum):
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Recording(Base):
    __tablename__ = "recordings"
    __table_args__ = (
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_nonnegative"),
        sa.CheckConstraint("sha256 IS NULL OR length(sha256) = 64", name="sha256_hex_length"),
        sa.Index(
            "ix_recordings_delete_after_pending",
            "delete_after",
            postgresql_where=sa.text("raw_deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    # One recording per game (half-court, single phone).
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("games.id", ondelete="CASCADE"), unique=True
    )
    uploader_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    bucket: Mapped[str] = mapped_column(sa.String(128))
    object_key: Mapped[str] = mapped_column(sa.String(1024))
    size_bytes: Mapped[Optional[int]] = mapped_column(sa.BigInteger)
    sha256: Mapped[Optional[str]] = mapped_column(sa.String(64))
    duration_s: Mapped[Optional[float]] = mapped_column(sa.Float)
    upload_status: Mapped[UploadStatus] = mapped_column(
        pg_enum(UploadStatus, "upload_status"), server_default=UploadStatus.UPLOADING.value
    )
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
    # NFR-04 deadline for deleting the raw video from object storage.
    delete_after: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
    raw_deleted_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        sa.CheckConstraint("attempts >= 0 AND attempts <= 3", name="attempts_range"),
        sa.Index("ix_processing_jobs_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    recording_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("recordings.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"), server_default=JobStatus.QUEUED.value
    )
    attempts: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("0"))
    celery_task_id: Mapped[Optional[str]] = mapped_column(sa.String(255))
    last_error: Mapped[Optional[str]] = mapped_column(sa.Text)
    queued_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
