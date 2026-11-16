"""
Recording pointers, processing jobs with retries, and raw-video retention
(SYS-07, FR-14, NFR-04, NFR-08). The video itself lives in object storage.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.core.config import get_settings
from server.app.crud._common import utc_now
from server.app.crud.errors import JobStateError, NotFoundError, NotPermittedError
from server.app.models import (
    Game,
    GameParticipant,
    JobStatus,
    ParticipantStatus,
    ProcessingJob,
    Recording,
    UploadStatus,
)


async def create_recording(
    session: AsyncSession, game_id: uuid.UUID, uploader_id: uuid.UUID, *, bucket: str, object_key: str
) -> Recording:
    """
    Register the upload target for a recorded game. Only games marked as
    recorded at creation may have a recording (FR-14); the database trigger
    enforces this too.
    """
    game = await session.get(Game, game_id)
    if game is None:
        raise NotFoundError("game not found")
    if not game.is_recorded:
        raise NotPermittedError("this game was not marked as recorded")
    part = await session.get(GameParticipant, (game_id, uploader_id))
    if part is None or part.status != ParticipantStatus.CONFIRMED:
        raise NotPermittedError("only participants can upload the recording")
    rec = Recording(game_id=game_id, uploader_id=uploader_id, bucket=bucket, object_key=object_key)
    session.add(rec)
    await session.flush()
    return rec


async def mark_uploaded(
    session: AsyncSession,
    recording_id: uuid.UUID,
    *,
    size_bytes: int,
    sha256: str,
    duration_s: Optional[float] = None,
    now: Optional[datetime] = None,
) -> Recording:
    """
    The resumable upload finished and the checksum was verified. The raw video
    gets an upper-bound deletion deadline right away so it can never be kept
    indefinitely, even if processing never succeeds (NFR-04).
    """
    now = utc_now(now)
    rec = await session.get(Recording, recording_id, with_for_update=True, populate_existing=True)
    if rec is None:
        raise NotFoundError("recording not found")
    rec.upload_status = UploadStatus.UPLOADED
    rec.size_bytes = size_bytes
    rec.sha256 = sha256.lower()
    rec.duration_s = duration_s
    rec.uploaded_at = now
    rec.delete_after = now + get_settings().raw_video_retention
    await session.flush()
    return rec


async def enqueue_job(session: AsyncSession, recording_id: uuid.UUID) -> ProcessingJob:
    """Queue a processing job (returns the active one if there already is one)."""
    rec = await session.get(Recording, recording_id, with_for_update=True, populate_existing=True)
    if rec is None:
        raise NotFoundError("recording not found")
    if rec.upload_status != UploadStatus.UPLOADED:
        raise JobStateError("recording has not finished uploading")
    active = await session.scalar(
        sa.select(ProcessingJob).where(
            ProcessingJob.recording_id == recording_id,
            ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.SUCCEEDED]),
        )
    )
    if active is not None:
        return active
    job = ProcessingJob(recording_id=recording_id)
    session.add(job)
    await session.flush()
    return job


async def _lock_job(session: AsyncSession, job_id: uuid.UUID) -> ProcessingJob:
    job = await session.get(ProcessingJob, job_id, with_for_update=True, populate_existing=True)
    if job is None:
        raise NotFoundError("job not found")
    return job


async def mark_job_running(
    session: AsyncSession, job_id: uuid.UUID, *, celery_task_id: Optional[str] = None, now: Optional[datetime] = None
) -> ProcessingJob:
    job = await _lock_job(session, job_id)
    if job.status != JobStatus.QUEUED:
        raise JobStateError(f"job is {job.status.value}")
    job.status = JobStatus.RUNNING
    job.celery_task_id = celery_task_id
    job.started_at = utc_now(now)
    await session.flush()
    return job


async def record_job_failure(
    session: AsyncSession, job_id: uuid.UUID, error: str, *, now: Optional[datetime] = None
) -> ProcessingJob:
    """
    A worker failed (NFR-08). The job goes back to the queue until it has
    failed `max_job_attempts` times; then it is marked failed (which notifies the
    organizer) and the raw video is kept so nothing is silently lost.
    """
    job = await _lock_job(session, job_id)
    if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
        raise JobStateError(f"job is already {job.status.value}")
    job.attempts += 1
    job.last_error = error
    if job.attempts >= get_settings().max_job_attempts:
        job.status = JobStatus.FAILED
        job.finished_at = utc_now(now)
    else:
        job.status = JobStatus.QUEUED
        job.started_at = None
    await session.flush()
    return job


async def due_for_deletion(session: AsyncSession, *, now: Optional[datetime] = None) -> list[Recording]:
    """Raw videos past their retention deadline that are still in object storage."""
    rows = await session.scalars(
        sa.select(Recording)
        .where(Recording.raw_deleted_at.is_(None), Recording.delete_after <= utc_now(now))
        .order_by(Recording.delete_after)
    )
    return list(rows)


async def mark_raw_deleted(session: AsyncSession, recording_id: uuid.UUID, *, now: Optional[datetime] = None) -> None:
    await session.execute(
        sa.update(Recording).where(Recording.id == recording_id).values(raw_deleted_at=utc_now(now))
    )
