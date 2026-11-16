"""
Location-based check-in and reliability scores (FR-06, SYS-06).

Reliability = attended / (attended + no_show + late_cancel + organizer_cancel),
and 1.0 for a player with no attendance events yet.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.core.config import get_settings
from server.app.crud._common import geog_point, utc_now
from server.app.crud.errors import CheckInRejectedError, NotFoundError, NotPermittedError
from server.app.models import (
    Attendance,
    Court,
    Game,
    GameParticipant,
    GameStatus,
    ParticipantStatus,
    PlayerProfile,
    ReliabilityEvent,
    ReliabilityKind,
)


async def add_reliability_event(
    session: AsyncSession, user_id: uuid.UUID, game_id: uuid.UUID, kind: ReliabilityKind
) -> None:
    await session.execute(
        pg_insert(ReliabilityEvent)
        .values(user_id=user_id, game_id=game_id, kind=kind)
        .on_conflict_do_nothing(index_elements=["user_id", "game_id", "kind"])
    )


async def reliability_counts(session: AsyncSession, user_id: uuid.UUID) -> dict[ReliabilityKind, int]:
    rows = await session.execute(
        sa.select(ReliabilityEvent.kind, sa.func.count())
        .where(ReliabilityEvent.user_id == user_id)
        .group_by(ReliabilityEvent.kind)
    )
    counts = {k: 0 for k in ReliabilityKind}
    for kind, n in rows.all():
        counts[kind] = int(n)
    return counts


async def recompute_reliability(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    counts = await reliability_counts(session, user_id)
    denom = sum(counts.values())
    score = Decimal("1.000") if denom == 0 else (
        Decimal(counts[ReliabilityKind.ATTENDED]) / Decimal(denom)
    ).quantize(Decimal("0.001"))
    await session.execute(
        sa.update(PlayerProfile).where(PlayerProfile.user_id == user_id).values(reliability_score=score)
    )
    return score


async def check_in(
    session: AsyncSession,
    game_id: uuid.UUID,
    user_id: uuid.UUID,
    lat: float,
    lon: float,
    *,
    at: Optional[datetime] = None,
) -> GameParticipant:
    """
    Check a confirmed participant in if they are within the geofence of the
    court during the check-in window. Records an `attended` reliability event.
    """
    settings = get_settings()
    at = utc_now(at)
    game = await session.get(Game, game_id, populate_existing=True)
    if game is None:
        raise NotFoundError("game not found")
    part = await session.get(GameParticipant, (game_id, user_id), with_for_update=True, populate_existing=True)
    if part is None or part.status != ParticipantStatus.CONFIRMED:
        raise CheckInRejectedError("only confirmed participants can check in")
    if game.status == GameStatus.CANCELLED:
        raise CheckInRejectedError("game was cancelled")
    opens = game.starts_at - settings.checkin_opens_before
    closes = game.starts_at + settings.checkin_closes_after
    if not (opens <= at <= closes):
        raise CheckInRejectedError("outside the check-in window")

    distance = await session.scalar(
        sa.select(sa.func.ST_Distance(Court.location, geog_point(lat, lon))).where(Court.id == game.court_id)
    )
    if distance is None or float(distance) > settings.checkin_radius_m:
        raise CheckInRejectedError(
            f"{float(distance or 0):.0f} m from the court; must be within {settings.checkin_radius_m:.0f} m"
        )

    if part.checked_in_at is None:
        part.checked_in_at = at
        part.attendance = Attendance.ATTENDED
        await session.flush()
        await add_reliability_event(session, user_id, game_id, ReliabilityKind.ATTENDED)
        await recompute_reliability(session, user_id)
    return part


async def close_attendance(
    session: AsyncSession, game_id: uuid.UUID, *, at: Optional[datetime] = None
) -> list[uuid.UUID]:
    """
    After the check-in window closes: confirmed players who never checked in
    are no-shows. Marks the game completed. Returns the no-show user ids.
    """
    settings = get_settings()
    at = utc_now(at)
    game = await session.get(Game, game_id, with_for_update=True, populate_existing=True)
    if game is None:
        raise NotFoundError("game not found")
    if game.status == GameStatus.CANCELLED:
        raise NotPermittedError("game was cancelled")
    if at < game.starts_at + settings.checkin_closes_after:
        raise NotPermittedError("check-in window is still open")

    rows = await session.scalars(
        sa.select(GameParticipant)
        .where(
            GameParticipant.game_id == game_id,
            GameParticipant.status == ParticipantStatus.CONFIRMED,
            GameParticipant.checked_in_at.is_(None),
            GameParticipant.attendance == Attendance.UNKNOWN,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    no_shows = []
    for part in rows:
        part.attendance = Attendance.NO_SHOW
        no_shows.append(part.user_id)
    await session.flush()
    for uid in no_shows:
        await add_reliability_event(session, uid, game_id, ReliabilityKind.NO_SHOW)
        await recompute_reliability(session, uid)
    if game.status in (GameStatus.SCHEDULED, GameStatus.IN_PROGRESS):
        game.status = GameStatus.COMPLETED
    await session.flush()
    return no_shows
