"""Test data builders. Each takes a session and flushes (never commits)."""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.crud import courts as courts_crud
from server.app.crud import games as games_crud
from server.app.crud import recordings as recordings_crud
from server.app.crud import stats as stats_crud
from server.app.crud import users as users_crud
from server.app.models import (
    Court,
    CourtStatus,
    Game,
    GameParticipant,
    ParticipantStatus,
    ProcessingJob,
    Recording,
    ScoringRule,
    User,
    Visibility,
)

CENTER = (40.4282, -86.9222)  # Purdue campus center, as in the mapping demo
_seq = itertools.count(1)


def at(hours: float = 0, days: float = 0) -> datetime:
    """A fixed-ish reference time offset from now (UTC)."""
    return datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=hours, days=days)


def offset_north(lat: float, lon: float, meters: float) -> tuple[float, float]:
    """A point `meters` due north (1 degree of latitude ~ 111,195 m on the WGS84 mean sphere)."""
    return lat + meters / 111_195.0, lon


async def user(
    s: AsyncSession, *, rating: Optional[float] = 3.0, name: Optional[str] = None, moderator: bool = False
) -> User:
    n = next(_seq)
    u = await users_crud.get_or_create_by_firebase_uid(
        s,
        firebase_uid=f"test:{uuid.uuid4()}",
        email=f"test{n}.{uuid.uuid4().hex[:8]}@purdue.edu",
        email_verified=True,
        display_name=name or f"Player {n}",
        self_assessed_level=rating,
    )
    if moderator:
        u.is_moderator = True
        await s.flush()
    return u


async def court(
    s: AsyncSession,
    *,
    lat: float = 40.4284,
    lon: float = -86.9215,
    name: Optional[str] = None,
    status: CourtStatus = CourtStatus.APPROVED,
) -> Court:
    return await courts_crud.submit_court(
        s, submitted_by=None, name=name or f"Court {next(_seq)}", lat=lat, lon=lon, hoop_count=2, status=status
    )


async def game(
    s: AsyncSession,
    *,
    organizer: Optional[User] = None,
    court_obj: Optional[Court] = None,
    starts_at: Optional[datetime] = None,
    duration: timedelta = timedelta(hours=1),
    cap: int = 10,
    visibility: Visibility = Visibility.PUBLIC,
    skill: Optional[tuple[float, float]] = None,
    recorded: bool = False,
    scoring: ScoringRule = ScoringRule.ONES_TWOS,
) -> Game:
    organizer = organizer or await user(s)
    court_obj = court_obj or await court(s)
    starts_at = starts_at or at(days=1)
    return await games_crud.create_game(
        s,
        organizer_id=organizer.id,
        court_id=court_obj.id,
        starts_at=starts_at,
        ends_at=starts_at + duration,
        player_cap=cap,
        visibility=visibility,
        scoring_rule=scoring,
        skill_min=None if skill is None else skill[0],
        skill_max=None if skill is None else skill[1],
        is_recorded=recorded,
    )


async def add_confirmed(s: AsyncSession, g: Game, u: User) -> None:
    """Put a player on the roster directly (bypasses join rules; the DB cap still applies)."""
    s.add(GameParticipant(game_id=g.id, user_id=u.id, status=ParticipantStatus.CONFIRMED))
    await s.flush()


@dataclass(frozen=True)
class Shot:
    shooter: object  # a User (tagged track) or an int track label (e.g. an untagged track)
    made: bool
    beyond_arc: bool = False
    confidence: float = 0.9


@dataclass
class RecordedGame:
    game: Game
    recording: Recording
    job: ProcessingJob
    tracks: dict[object, uuid.UUID] = field(default_factory=dict)  # user id or label -> track id
    shot_ids: list[uuid.UUID] = field(default_factory=list)


async def recorded_game(
    s: AsyncSession,
    players: Sequence[tuple[User, str]],
    shots: Sequence[Shot] = (),
    *,
    starts_at: Optional[datetime] = None,
    scoring: ScoringRule = ScoringRule.ONES_TWOS,
    extra_tracks: Sequence[tuple[int, str]] = (),
    roster_only: Sequence[User] = (),
    tag: bool = True,
    finalize: bool = True,
    court_obj: Optional[Court] = None,
) -> RecordedGame:
    """
    A recorded game with one track per player (label = position in `players`),
    optional extra untagged tracks, shots, self-tags, and finalized stats.
    The first player is the organizer. `roster_only` players are confirmed but
    have no track.
    """
    organizer = players[0][0]
    g = await game(
        s,
        organizer=organizer,
        court_obj=court_obj,
        starts_at=starts_at or at(days=-1),
        visibility=Visibility.PRIVATE,
        recorded=True,
        scoring=scoring,
        cap=20,
    )
    for u, _team in players[1:]:
        await add_confirmed(s, g, u)
    for u in roster_only:
        await add_confirmed(s, g, u)

    rec = await recordings_crud.create_recording(s, g.id, organizer.id, bucket="test", object_key=f"{g.id}.mp4")
    await recordings_crud.mark_uploaded(s, rec.id, size_bytes=1024, sha256="a" * 64, duration_s=3600)
    job = await recordings_crud.enqueue_job(s, rec.id)

    label_of = {u.id: i for i, (u, _team) in enumerate(players)}
    track_specs = [stats_crud.TrackIn(i, team) for i, (_u, team) in enumerate(players)]
    track_specs += [stats_crud.TrackIn(label, team) for label, team in extra_tracks]
    by_label = await stats_crud.write_tracks(s, rec.id, track_specs)

    shot_ins = []
    for i, sh in enumerate(shots):
        label = sh.shooter if isinstance(sh.shooter, int) else label_of[sh.shooter.id]
        shot_ins.append(stats_crud.ShotIn(label, 1000 * (i + 1), 10.0, 5.0, sh.beyond_arc, sh.made, sh.confidence))
    shot_ids = await stats_crud.write_shot_events(s, rec.id, shot_ins)

    tracks: dict[object, uuid.UUID] = {}
    for u, _team in players:
        tracks[u.id] = by_label[label_of[u.id]]
    for label, _team in extra_tracks:
        tracks[label] = by_label[label]
    if tag:
        for u, _team in players:
            await stats_crud.tag_track(s, tracks[u.id], u.id)
    if finalize:
        await stats_crud.finalize_game_stats(s, g.id)
    return RecordedGame(game=g, recording=rec, job=job, tracks=tracks, shot_ids=shot_ids)


async def two_team_game(
    s: AsyncSession,
    team_a: Sequence[User],
    team_b: Sequence[User],
    *,
    a_points: int,
    b_points: int,
    starts_at: datetime,
    flagged_for_b: int = 0,
    **kwargs,
) -> RecordedGame:
    """
    A finalized game where team A's first player made `a_points` inside shots and
    team B's first player `b_points` (1s & 2s scoring, so 1 point each).
    `flagged_for_b` adds low-confidence made shots for B that don't count yet.
    """
    players = [(u, "A") for u in team_a] + [(u, "B") for u in team_b]
    shots = [Shot(team_a[0], True) for _ in range(a_points)]
    shots += [Shot(team_b[0], True) for _ in range(b_points)]
    shots += [Shot(team_b[0], True, confidence=0.3) for _ in range(flagged_for_b)]
    return await recorded_game(s, players, shots, starts_at=starts_at, **kwargs)


async def set_tags_locked(s: AsyncSession, game_id: uuid.UUID, when: datetime) -> None:
    await s.execute(sa.update(Game).where(Game.id == game_id).values(tags_locked_at=when))
    await s.flush()
