"""I. Recordings, processing retries and privacy (FR-14, NFR-04, NFR-08, OBJ-06)."""

from __future__ import annotations

import pathlib
import re
from datetime import timedelta

import pytest
import sqlalchemy as sa

from server.app.crud import recordings, stats
from server.app.crud.errors import JobStateError, NotPermittedError
from server.app.models import JobStatus, Recording
from server.tests.db import factories as f

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


async def _uploaded(db):
    org = await f.user(db)
    g = await f.game(db, organizer=org, recorded=True)
    rec = await recordings.create_recording(db, g.id, org.id, bucket="raw", object_key="g.mp4")
    return g, org, rec


async def test_unrecorded_game_cannot_get_a_recording(db):
    org = await f.user(db)
    g = await f.game(db, organizer=org, recorded=False)
    with pytest.raises(NotPermittedError, match="recorded"):
        await recordings.create_recording(db, g.id, org.id, bucket="raw", object_key="x.mp4")
    assert await db.scalar(sa.select(sa.func.count()).select_from(Recording).where(Recording.game_id == g.id)) == 0


async def test_only_participants_upload(db):
    g, _org, _rec = await _uploaded(db)
    stranger = await f.user(db)
    with pytest.raises(NotPermittedError):
        await recordings.create_recording(db, g.id, stranger.id, bucket="raw", object_key="y.mp4")


async def test_job_requires_finished_upload(db):
    _g, _org, rec = await _uploaded(db)
    with pytest.raises(JobStateError):
        await recordings.enqueue_job(db, rec.id)
    await recordings.mark_uploaded(db, rec.id, size_bytes=3 * 1024**3, sha256="B" * 64)
    job = await recordings.enqueue_job(db, rec.id)
    assert (await recordings.enqueue_job(db, rec.id)).id == job.id  # one active job


async def test_retries_then_fails_and_keeps_raw_video(db):
    _g, _org, rec = await _uploaded(db)
    t0 = f.at()
    await recordings.mark_uploaded(db, rec.id, size_bytes=1, sha256="c" * 64, now=t0)
    job = await recordings.enqueue_job(db, rec.id)
    for attempt in (1, 2):
        await recordings.mark_job_running(db, job.id, celery_task_id=f"task-{attempt}")
        job = await recordings.record_job_failure(db, job.id, "worker died")
        assert (job.status, job.attempts) == (JobStatus.QUEUED, attempt)
    await recordings.mark_job_running(db, job.id)
    job = await recordings.record_job_failure(db, job.id, "worker died again")
    assert (job.status, job.attempts) == (JobStatus.FAILED, 3)
    with pytest.raises(JobStateError):
        await recordings.record_job_failure(db, job.id, "fourth")
    with pytest.raises(JobStateError):
        await stats.finalize_game_stats(db, rec.game_id)
    await db.refresh(rec)
    assert rec.raw_deleted_at is None
    assert rec not in await recordings.due_for_deletion(db, now=t0 + timedelta(days=13))


async def test_raw_video_deadline_is_14_days_after_processing(db):
    a, b = await f.user(db), await f.user(db)
    rg = await f.two_team_game(db, [a], [b], a_points=1, b_points=0, starts_at=f.at(days=-2), finalize=False)
    uploaded = (await db.get(Recording, rg.recording.id)).uploaded_at
    finished = uploaded + timedelta(days=1)
    await stats.finalize_game_stats(db, rg.game.id, now=finished)
    rec = await db.get(Recording, rg.recording.id, populate_existing=True)
    assert rec.delete_after == finished + timedelta(days=14)

    assert rec not in await recordings.due_for_deletion(db, now=finished + timedelta(days=13, hours=23))
    assert rec in await recordings.due_for_deletion(db, now=finished + timedelta(days=14))
    await recordings.mark_raw_deleted(db, rec.id, now=finished + timedelta(days=14))
    assert rec not in await recordings.due_for_deletion(db, now=finished + timedelta(days=30))


async def test_upload_without_processing_still_has_a_deadline(db):
    _g, _org, rec = await _uploaded(db)
    t0 = f.at()
    await recordings.mark_uploaded(db, rec.id, size_bytes=1, sha256="d" * 64, now=t0)
    assert rec in await recordings.due_for_deletion(db, now=t0 + timedelta(days=14))


async def test_schema_stores_no_face_or_appearance_data(db):
    cols = (
        await db.execute(
            sa.text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name <> 'spatial_ref_sys'"
            )
        )
    ).all()
    assert cols, "schema scan returned no columns"
    # Whole name parts only, so e.g. courts.surface doesn't match "face".
    pattern = re.compile(r"(^|_)(faces?|embeddings?|appearance|biometrics?|reid)(_|$)", re.IGNORECASE)
    offenders = [f"{t}.{c}" for t, c in cols if pattern.search(c) or pattern.search(t)]
    assert not offenders, offenders


def test_no_facial_recognition_libraries_in_server_code():
    pattern = re.compile(r"^\s*(import|from)\s+(face_recognition|facenet|insightface|deepface)\b", re.MULTILINE)
    hits = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "server").rglob("*.py")
        if "tests" not in p.parts and pattern.search(p.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not hits, hits
