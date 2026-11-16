"""
CV output, self-tagging, event review and opt-out (SYS-08/09/10, SYS-14).

Every write that can change a finalized game's stats recomputes ratings in the
same transaction (see crud/ratings.py), so no reader can see new stats next to
old ratings. All writes to tags and shot reviews must go through this module.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.core.config import get_settings
from server.app.crud import ratings
from server.app.crud._common import utc_now
from server.app.crud.errors import (
    InvalidReviewError,
    InvalidTagError,
    JobStateError,
    NotFoundError,
    NotPermittedError,
    TagsLockedError,
)
from server.app.models import (
    DetectedTrack,
    Game,
    GameParticipant,
    GameStatus,
    HighlightClip,
    JobStatus,
    ParticipantStatus,
    ProcessingJob,
    Recording,
    ReviewStatus,
    ShotEvent,
)


@dataclass(frozen=True)
class TrackIn:
    track_label: int
    team: Optional[str] = None
    thumbnail_key: Optional[str] = None


@dataclass(frozen=True)
class ShotIn:
    track_label: Optional[int]
    t_ms: int
    court_x: float
    court_y: float
    beyond_arc: bool
    made: bool
    confidence: float


@dataclass(frozen=True)
class BoxScoreLine:
    user_id: uuid.UUID
    team: Optional[str]
    fga: int
    fgm: int
    two_pa: int
    two_pm: int
    three_pa: int
    three_pm: int
    points: int


# --------------------------------------------------------------------------- #
# CV worker writes
# --------------------------------------------------------------------------- #

async def _recording_and_game(session: AsyncSession, recording_id: uuid.UUID) -> tuple[Recording, Game]:
    rec = await session.get(Recording, recording_id)
    if rec is None:
        raise NotFoundError("recording not found")
    game = await session.get(Game, rec.game_id)
    assert game is not None
    return rec, game


async def write_tracks(
    session: AsyncSession, recording_id: uuid.UUID, tracks: Sequence[TrackIn]
) -> dict[int, uuid.UUID]:
    """Store a processing run's player tracks, replacing any earlier run's output."""
    rec, game = await _recording_and_game(session, recording_id)
    if game.stats_finalized_at is not None:
        raise NotPermittedError("stats for this game are already finalized")
    await session.execute(sa.delete(ShotEvent).where(ShotEvent.game_id == game.id))
    await session.execute(sa.delete(DetectedTrack).where(DetectedTrack.recording_id == rec.id))
    objs = [
        DetectedTrack(
            game_id=game.id,
            recording_id=rec.id,
            track_label=t.track_label,
            team=t.team,
            thumbnail_key=t.thumbnail_key,
        )
        for t in tracks
    ]
    session.add_all(objs)
    await session.flush()
    return {o.track_label: o.id for o in objs}


async def write_shot_events(
    session: AsyncSession, recording_id: uuid.UUID, shots: Sequence[ShotIn]
) -> list[uuid.UUID]:
    """Store shot events. Low-confidence events are flagged and not counted until confirmed."""
    rec, game = await _recording_and_game(session, recording_id)
    if game.stats_finalized_at is not None:
        raise NotPermittedError("stats for this game are already finalized")
    labels = dict(
        (await session.execute(
            sa.select(DetectedTrack.track_label, DetectedTrack.id).where(DetectedTrack.recording_id == rec.id)
        )).all()
    )
    threshold = get_settings().flag_confidence_threshold
    objs = []
    for s in shots:
        if s.track_label is not None and s.track_label not in labels:
            raise NotFoundError(f"unknown track label {s.track_label}")
        objs.append(
            ShotEvent(
                game_id=game.id,
                track_id=labels.get(s.track_label) if s.track_label is not None else None,
                t_ms=s.t_ms,
                court_x=s.court_x,
                court_y=s.court_y,
                beyond_arc=s.beyond_arc,
                made=s.made,
                confidence=s.confidence,
                review_status=ReviewStatus.FLAGGED if s.confidence < threshold else ReviewStatus.AUTO,
            )
        )
    session.add_all(objs)
    await session.flush()
    return [o.id for o in objs]


async def add_highlight_clip(
    session: AsyncSession, shot_event_id: uuid.UUID, object_key: str, duration_ms: int
) -> HighlightClip:
    shot = await session.get(ShotEvent, shot_event_id)
    if shot is None:
        raise NotFoundError("shot event not found")
    if not shot.made:
        raise NotPermittedError("highlights are only cut for made shots")
    clip = HighlightClip(game_id=shot.game_id, shot_event_id=shot.id, object_key=object_key, duration_ms=duration_ms)
    session.add(clip)
    await session.flush()
    return clip


async def finalize_game_stats(
    session: AsyncSession, game_id: uuid.UUID, *, now: Optional[datetime] = None
) -> ratings.RecomputeResult:
    """
    Processing succeeded. In one transaction: mark the job succeeded (the job
    trigger sends `stats_ready`), set stats_finalized_at and tags_locked_at,
    set the raw-video deletion deadline, and apply the game's first ratings.
    Calling it again is harmless.
    """
    settings = get_settings()
    now = utc_now(now)
    game = await session.scalar(
        sa.select(Game).where(Game.id == game_id).with_for_update().execution_options(populate_existing=True)
    )
    if game is None:
        raise NotFoundError("game not found")
    rec = await session.scalar(sa.select(Recording).where(Recording.game_id == game_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if rec is None:
        raise JobStateError("game has no recording")
    job = await session.scalar(
        sa.select(ProcessingJob)
        .where(ProcessingJob.recording_id == rec.id)
        .order_by(ProcessingJob.queued_at.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise JobStateError("recording has no processing job")
    if job.status == JobStatus.FAILED:
        raise JobStateError("processing job failed")
    if job.status != JobStatus.SUCCEEDED:
        job.status = JobStatus.SUCCEEDED
        job.finished_at = now
        rec.delete_after = now + settings.raw_video_retention
    if game.stats_finalized_at is None:
        game.stats_finalized_at = now
        game.tags_locked_at = now + settings.tag_lock_period
        if game.status in (GameStatus.SCHEDULED, GameStatus.IN_PROGRESS):
            game.status = GameStatus.COMPLETED
    await session.flush()
    return await ratings.recompute_from_game(session, game_id)


# --------------------------------------------------------------------------- #
# Player-facing writes (tags, reviews, opt-out)
# --------------------------------------------------------------------------- #

def _check_tags_open(game: Game, now: datetime) -> None:
    if game.tags_locked_at is not None and now >= game.tags_locked_at:
        raise TagsLockedError("tags and reviews for this game are locked")


async def _after_stats_change(session: AsyncSession, game: Game) -> None:
    """Recompute ratings (same transaction) and tell viewers the stats changed."""
    await session.flush()
    if game.stats_finalized_at is not None:
        await ratings.recompute_from_game(session, game.id)
    await session.execute(
        sa.select(sa.func.pg_notify("game_events", json.dumps({"game_id": str(game.id), "kind": "stats_updated"})))
    )


async def _confirmed_participant(
    session: AsyncSession, game_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[GameParticipant]:
    part = await session.get(GameParticipant, (game_id, user_id))
    if part is None or part.status != ParticipantStatus.CONFIRMED:
        return None
    return part


async def _game(session: AsyncSession, game_id: uuid.UUID) -> Game:
    game = await session.get(Game, game_id, populate_existing=True)
    assert game is not None
    return game


async def tag_track(
    session: AsyncSession, track_id: uuid.UUID, user_id: uuid.UUID, *, now: Optional[datetime] = None
) -> DetectedTrack:
    """A player taps the thumbnail that is them (FR-10). Moves their tag if they had one."""
    now = utc_now(now)
    track = await session.get(DetectedTrack, track_id, with_for_update=True, populate_existing=True)
    if track is None:
        raise NotFoundError("track not found")
    game = await _game(session, track.game_id)
    _check_tags_open(game, now)
    part = await _confirmed_participant(session, game.id, user_id)
    if part is None:
        raise InvalidTagError("only confirmed participants can tag themselves")
    if part.tracking_opt_out:
        raise InvalidTagError("player opted out of tracking for this game")
    if track.tagged_user_id == user_id:
        return track
    if track.tagged_user_id is not None:
        raise InvalidTagError("this track is already claimed by another player")

    previous = await session.scalar(
        sa.select(DetectedTrack)
        .where(DetectedTrack.game_id == game.id, DetectedTrack.tagged_user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if previous is not None:
        previous.tagged_user_id = None
        previous.tagged_at = None
        await session.flush()
    track.tagged_user_id = user_id
    track.tagged_at = now
    await _after_stats_change(session, game)
    return track


async def untag_track(
    session: AsyncSession, track_id: uuid.UUID, user_id: uuid.UUID, *, now: Optional[datetime] = None
) -> None:
    now = utc_now(now)
    track = await session.get(DetectedTrack, track_id, with_for_update=True, populate_existing=True)
    if track is None:
        raise NotFoundError("track not found")
    game = await _game(session, track.game_id)
    _check_tags_open(game, now)
    if track.tagged_user_id != user_id:
        raise InvalidTagError("players can only remove their own tag")
    track.tagged_user_id = None
    track.tagged_at = None
    await _after_stats_change(session, game)


async def review_event(
    session: AsyncSession,
    shot_event_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    confirm: bool,
    now: Optional[datetime] = None,
) -> ShotEvent:
    """Confirm or reject a low-confidence (flagged) shot event (FR-10)."""
    now = utc_now(now)
    shot = await session.get(ShotEvent, shot_event_id, with_for_update=True, populate_existing=True)
    if shot is None:
        raise NotFoundError("shot event not found")
    game = await _game(session, shot.game_id)
    _check_tags_open(game, now)
    if await _confirmed_participant(session, game.id, user_id) is None:
        raise InvalidReviewError("only confirmed participants can review events")
    if shot.review_status == ReviewStatus.AUTO:
        raise InvalidReviewError("only flagged events need review")
    new_status = ReviewStatus.CONFIRMED if confirm else ReviewStatus.REJECTED
    if shot.review_status == new_status:
        return shot
    shot.review_status = new_status
    shot.reviewed_by = user_id
    shot.reviewed_at = now
    await _after_stats_change(session, game)
    return shot


async def set_tracking_opt_out(
    session: AsyncSession,
    game_id: uuid.UUID,
    user_id: uuid.UUID,
    opt_out: bool = True,
    *,
    now: Optional[datetime] = None,
) -> None:
    """
    Opt out of (or back into) tracking for a game (SYS-14). Opting out is never
    locked: it works for any game at any time and recomputes ratings. Opting back
    in changes stats like a tag does, so it follows the tag lock.
    """
    now = utc_now(now)
    part = await session.get(GameParticipant, (game_id, user_id), with_for_update=True, populate_existing=True)
    if part is None:
        raise NotFoundError("not a participant of this game")
    if part.tracking_opt_out == opt_out:
        return
    game = await _game(session, game_id)
    if not opt_out:
        _check_tags_open(game, now)
    part.tracking_opt_out = opt_out
    await _after_stats_change(session, game)


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

async def box_scores(session: AsyncSession, game_id: uuid.UUID) -> list[BoxScoreLine]:
    rows = await session.execute(
        sa.text(
            "SELECT user_id, team, fga, fgm, two_pa, two_pm, three_pa, three_pm, points "
            "FROM player_box_scores WHERE game_id = :gid ORDER BY points DESC, user_id"
        ),
        {"gid": game_id},
    )
    return [BoxScoreLine(**r._mapping) for r in rows.all()]


async def team_scores(session: AsyncSession, game_id: uuid.UUID) -> dict[str, int]:
    rows = await session.execute(
        sa.text("SELECT team, points FROM game_team_scores WHERE game_id = :gid"), {"gid": game_id}
    )
    return {t: int(p) for t, p in rows.all()}


async def highlight_clips(session: AsyncSession, game_id: uuid.UUID) -> list[tuple[uuid.UUID, HighlightClip]]:
    """(user_id, clip) for made, counted shots of tagged players who did not opt out."""
    rows = await session.execute(
        sa.select(DetectedTrack.tagged_user_id, HighlightClip)
        .join(ShotEvent, ShotEvent.id == HighlightClip.shot_event_id)
        .join(DetectedTrack, DetectedTrack.id == ShotEvent.track_id)
        .join(
            GameParticipant,
            sa.and_(
                GameParticipant.game_id == DetectedTrack.game_id,
                GameParticipant.user_id == DetectedTrack.tagged_user_id,
            ),
        )
        .where(
            HighlightClip.game_id == game_id,
            ShotEvent.made,
            ShotEvent.review_status.in_([ReviewStatus.AUTO, ReviewStatus.CONFIRMED]),
            sa.not_(GameParticipant.tracking_opt_out),
        )
        .order_by(ShotEvent.t_ms)
    )
    return [(uid, clip) for uid, clip in rows.all()]
