"""
Game creation, skill-based visibility and roster management
(FR-03, FR-04, FR-05, FR-15, NFR-02).

The roster cap is enforced twice:
  1. here, by locking the game row (SELECT ... FOR UPDATE) before checking the
     count, which gives a clean GameFullError; and
  2. in the database, by the participant-count trigger plus the
     `participant_count <= player_cap` CHECK, which holds even for writers that
     bypass this module.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from server.app.core.config import get_settings
from server.app.crud import attendance
from server.app.crud._common import geog_point, utc_now
from server.app.crud.errors import (
    BlockedError,
    GameFullError,
    GameNotJoinableError,
    NotFoundError,
    NotPermittedError,
    ReliabilityTooLowError,
    SkillOutOfRangeError,
)
from server.app.models import (
    Attendance,
    Court,
    CourtStatus,
    Game,
    GameBlock,
    GameParticipant,
    GameStatus,
    ParticipantStatus,
    PlayerProfile,
    PlayerRating,
    ReliabilityKind,
    ScoringRule,
    Visibility,
)


def skill_label(skill_min: Optional[Decimal], skill_max: Optional[Decimal]) -> str:
    """Figma chip label for a skill range: All / Beginner / Mid / Advanced."""
    if skill_min is None or skill_max is None:
        return "All"
    if skill_max <= Decimal("3.0"):
        return "Beginner"
    if skill_min >= Decimal("4.0"):
        return "Advanced"
    return "Mid"


def _dec(v: Optional[float]) -> Optional[Decimal]:
    return None if v is None else Decimal(str(v))


async def create_game(
    session: AsyncSession,
    *,
    organizer_id: uuid.UUID,
    court_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    player_cap: int,
    visibility: Visibility,
    scoring_rule: ScoringRule,
    skill_min: Optional[float] = None,
    skill_max: Optional[float] = None,
    is_recorded: bool = False,
    rules_note: Optional[str] = None,
) -> Game:
    """
    Create a game (FR-03). The organizer is added as the first confirmed
    participant. Public games require a reliability score at or above the
    threshold once the organizer has enough attendance history (SYS-06).
    """
    settings = get_settings()
    court = await session.get(Court, court_id)
    if court is None or court.status != CourtStatus.APPROVED:
        raise NotFoundError("court not found or not approved")

    if visibility == Visibility.PUBLIC:
        counts = await attendance.reliability_counts(session, organizer_id)
        profile = await session.get(PlayerProfile, organizer_id)
        if profile is None:
            raise NotFoundError("organizer not found")
        if (
            sum(counts.values()) >= settings.reliability_min_events
            and float(profile.reliability_score) < settings.reliability_threshold
        ):
            raise ReliabilityTooLowError(
                f"reliability {profile.reliability_score} is below {settings.reliability_threshold}; "
                "private games are still allowed"
            )

    game = Game(
        organizer_id=organizer_id,
        court_id=court_id,
        starts_at=starts_at,
        ends_at=ends_at,
        player_cap=player_cap,
        skill_min=_dec(skill_min),
        skill_max=_dec(skill_max),
        visibility=visibility,
        invite_token=secrets.token_urlsafe(16) if visibility == Visibility.PRIVATE else None,
        scoring_rule=scoring_rule,
        is_recorded=is_recorded,
        rules_note=rules_note,
    )
    session.add(game)
    await session.flush()
    session.add(GameParticipant(game_id=game.id, user_id=organizer_id, status=ParticipantStatus.CONFIRMED))
    await session.flush()
    await session.refresh(game)
    return game


async def _display_rating(session: AsyncSession, user_id: uuid.UUID) -> Decimal:
    rating = await session.get(PlayerRating, user_id)
    if rating is None:
        raise NotFoundError("player not found")
    return rating.display_rating


async def _is_blocked(session: AsyncSession, organizer_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return (await session.get(GameBlock, (organizer_id, user_id))) is not None


async def _lock_game(session: AsyncSession, game_id: uuid.UUID) -> Game:
    game = await session.scalar(
        sa.select(Game).where(Game.id == game_id).with_for_update().execution_options(populate_existing=True)
    )
    if game is None:
        raise NotFoundError("game not found")
    return game


async def join_game(
    session: AsyncSession,
    game_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    invite_token: Optional[str] = None,
) -> ParticipantStatus:
    """
    Join (public), request to join (request-to-join), or join through an invite
    link (private). Returns the resulting participant status. Calling it again
    for a player already on the roster is a no-op.
    """
    game = await _lock_game(session, game_id)
    if game.status != GameStatus.SCHEDULED:
        raise GameNotJoinableError(f"game is {game.status.value}")

    if game.visibility == Visibility.PRIVATE:
        if invite_token is None or not secrets.compare_digest(invite_token, game.invite_token or ""):
            raise NotPermittedError("private games can only be joined through their invite link")
    if await _is_blocked(session, game.organizer_id, user_id):
        raise BlockedError("the organizer has blocked this player")
    if game.visibility != Visibility.PRIVATE and game.skill_min is not None:
        rating = await _display_rating(session, user_id)
        if not (game.skill_min <= rating <= game.skill_max):
            raise SkillOutOfRangeError(f"rating {rating} is outside {game.skill_min}-{game.skill_max}")

    part = await session.get(GameParticipant, (game_id, user_id), with_for_update=True, populate_existing=True)
    if part is not None and part.status in (ParticipantStatus.CONFIRMED, ParticipantStatus.PENDING):
        return part.status
    if part is not None and part.status == ParticipantStatus.REMOVED:
        raise NotPermittedError("the organizer removed this player from the game")

    target = (
        ParticipantStatus.PENDING if game.visibility == Visibility.REQUEST_TO_JOIN else ParticipantStatus.CONFIRMED
    )
    if target == ParticipantStatus.CONFIRMED and game.participant_count >= game.player_cap:
        raise GameFullError("game is full")

    if part is None:
        session.add(GameParticipant(game_id=game_id, user_id=user_id, status=target))
    else:  # rejoining after leaving
        part.status = target
        part.left_at = None
        part.joined_at = utc_now()
        part.attendance = Attendance.UNKNOWN
    await session.flush()
    return target


async def join_by_invite(
    session: AsyncSession, invite_token: str, user_id: uuid.UUID
) -> tuple[uuid.UUID, ParticipantStatus]:
    game_id = await session.scalar(sa.select(Game.id).where(Game.invite_token == invite_token))
    if game_id is None:
        raise NotFoundError("invite link is not valid")
    return game_id, await join_game(session, game_id, user_id, invite_token=invite_token)


async def leave_game(
    session: AsyncSession, game_id: uuid.UUID, user_id: uuid.UUID, *, now: Optional[datetime] = None
) -> None:
    """Leave a game. Leaving a confirmed spot shortly before start is a late cancellation."""
    settings = get_settings()
    now = utc_now(now)
    game = await _lock_game(session, game_id)
    if user_id == game.organizer_id:
        raise NotPermittedError("organizers cancel the game instead of leaving it")
    part = await session.get(GameParticipant, (game_id, user_id), with_for_update=True, populate_existing=True)
    if part is None or part.status not in (ParticipantStatus.CONFIRMED, ParticipantStatus.PENDING):
        raise NotFoundError("not on this game's roster")
    was_confirmed = part.status == ParticipantStatus.CONFIRMED
    part.status = ParticipantStatus.LEFT
    part.left_at = now
    late = was_confirmed and game.status == GameStatus.SCHEDULED and game.starts_at - now < settings.late_cancel_window
    if late:
        part.attendance = Attendance.LATE_CANCEL
    await session.flush()
    if late:
        await attendance.add_reliability_event(session, user_id, game_id, ReliabilityKind.LATE_CANCEL)
        await attendance.recompute_reliability(session, user_id)


async def _require_organizer(session: AsyncSession, game_id: uuid.UUID, organizer_id: uuid.UUID) -> Game:
    game = await _lock_game(session, game_id)
    if game.organizer_id != organizer_id:
        raise NotPermittedError("only the organizer can do this")
    return game


async def approve_request(
    session: AsyncSession, game_id: uuid.UUID, organizer_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Approve a pending request (FR-15). Rechecks the cap: pending rows don't hold spots."""
    game = await _require_organizer(session, game_id, organizer_id)
    part = await session.get(GameParticipant, (game_id, user_id), with_for_update=True, populate_existing=True)
    if part is None or part.status != ParticipantStatus.PENDING:
        raise NotFoundError("no pending request from this player")
    if await _is_blocked(session, organizer_id, user_id):
        raise BlockedError("this player is blocked")
    if game.participant_count >= game.player_cap:
        raise GameFullError("game is full")
    part.status = ParticipantStatus.CONFIRMED
    await session.flush()


async def remove_player(
    session: AsyncSession, game_id: uuid.UUID, organizer_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await _require_organizer(session, game_id, organizer_id)
    if user_id == organizer_id:
        raise NotPermittedError("organizers cannot remove themselves")
    part = await session.get(GameParticipant, (game_id, user_id), with_for_update=True, populate_existing=True)
    if part is None or part.status not in (ParticipantStatus.CONFIRMED, ParticipantStatus.PENDING):
        raise NotFoundError("not on this game's roster")
    part.status = ParticipantStatus.REMOVED
    part.left_at = utc_now()
    await session.flush()


async def block_player(
    session: AsyncSession, organizer_id: uuid.UUID, user_id: uuid.UUID, *, game_id: Optional[uuid.UUID] = None
) -> None:
    """Block a player from all of this organizer's future games (and remove them from `game_id`)."""
    if organizer_id == user_id:
        raise NotPermittedError("cannot block yourself")
    if await session.get(GameBlock, (organizer_id, user_id)) is None:
        session.add(GameBlock(organizer_id=organizer_id, blocked_user_id=user_id))
        await session.flush()
    if game_id is not None:
        part = await session.get(GameParticipant, (game_id, user_id))
        if part is not None and part.status in (ParticipantStatus.CONFIRMED, ParticipantStatus.PENDING):
            await remove_player(session, game_id, organizer_id, user_id)


async def update_game(
    session: AsyncSession,
    game_id: uuid.UUID,
    organizer_id: uuid.UUID,
    *,
    starts_at: Optional[datetime] = None,
    ends_at: Optional[datetime] = None,
    rules_note: Optional[str] = None,
    player_cap: Optional[int] = None,
) -> Game:
    """Edit game details. Connected clients are told through the games NOTIFY trigger."""
    game = await _require_organizer(session, game_id, organizer_id)
    if game.stats_finalized_at is not None:
        raise NotPermittedError("finished games can't be edited")
    if starts_at is not None:
        game.starts_at = starts_at
    if ends_at is not None:
        game.ends_at = ends_at
    if rules_note is not None:
        game.rules_note = rules_note
    if player_cap is not None:
        if player_cap < game.participant_count:
            raise NotPermittedError("cap can't go below the current roster size")
        game.player_cap = player_cap
    await session.flush()
    return game


async def cancel_game(
    session: AsyncSession, game_id: uuid.UUID, organizer_id: uuid.UUID, *, now: Optional[datetime] = None
) -> None:
    """Cancel a game. Cancelling shortly before start counts against the organizer's reliability."""
    settings = get_settings()
    now = utc_now(now)
    game = await _require_organizer(session, game_id, organizer_id)
    if game.status != GameStatus.SCHEDULED:
        raise GameNotJoinableError(f"game is {game.status.value}")
    game.status = GameStatus.CANCELLED
    await session.flush()
    if game.starts_at - now < settings.late_cancel_window:
        await attendance.add_reliability_event(session, organizer_id, game_id, ReliabilityKind.ORGANIZER_CANCEL)
        await attendance.recompute_reliability(session, organizer_id)


@dataclass(frozen=True)
class VisibleGame:
    """Everything the court sheet / GameSummary needs, straight from the database."""

    game_id: uuid.UUID
    court_id: uuid.UUID
    court_name: str
    starts_at: datetime
    ends_at: datetime
    skill_min: Optional[Decimal]
    skill_max: Optional[Decimal]
    skill_label: str
    visibility: Visibility
    player_cap: int
    participant_count: int
    spots_available: int
    organizer_id: uuid.UUID
    host_name: str
    host_show_up_rate: float
    host_rating: float
    rules_note: Optional[str]
    scoring_rule: ScoringRule
    is_recorded: bool
    distance_m: Optional[float]


async def list_visible_games(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    near: Optional[tuple[float, float]] = None,
    radius_m: float = 2500.0,
    now: Optional[datetime] = None,
    court_id: Optional[uuid.UUID] = None,
) -> list[VisibleGame]:
    """
    Upcoming games this player may see (FR-04): public and request-to-join games
    whose skill range includes the player's rating (all-levels games always
    qualify), never private games, and never games from an organizer who blocked
    them. Provisional players are filtered by their provisional rating.
    """
    now = utc_now(now)
    rating = await _display_rating(session, user_id)
    organizer_profile = aliased(PlayerProfile)
    organizer_rating = aliased(PlayerRating)

    distance = sa.func.ST_Distance(Court.location, geog_point(*near)) if near else sa.null()
    stmt = (
        sa.select(Game, Court.name, organizer_profile, organizer_rating.display_rating, distance.label("distance_m"))
        .join(Court, Court.id == Game.court_id)
        .join(organizer_profile, organizer_profile.user_id == Game.organizer_id)
        .join(organizer_rating, organizer_rating.user_id == Game.organizer_id)
        .where(
            Court.status == CourtStatus.APPROVED,
            Game.status == GameStatus.SCHEDULED,
            Game.ends_at > now,
            Game.visibility.in_([Visibility.PUBLIC, Visibility.REQUEST_TO_JOIN]),
            sa.or_(Game.skill_min.is_(None), sa.and_(Game.skill_min <= rating, Game.skill_max >= rating)),
            ~sa.exists().where(GameBlock.organizer_id == Game.organizer_id, GameBlock.blocked_user_id == user_id),
        )
        .order_by(Game.starts_at, Game.id)
        # participant_count is changed by a database trigger, so never trust a copy
        # already loaded in this session.
        .execution_options(populate_existing=True)
    )
    if near:
        stmt = stmt.where(sa.func.ST_DWithin(Court.location, geog_point(*near), radius_m))
    if court_id is not None:
        stmt = stmt.where(Game.court_id == court_id)

    out = []
    for game, court_name, host, host_rating, dist in (await session.execute(stmt)).all():
        out.append(
            VisibleGame(
                game_id=game.id,
                court_id=game.court_id,
                court_name=court_name,
                starts_at=game.starts_at,
                ends_at=game.ends_at,
                skill_min=game.skill_min,
                skill_max=game.skill_max,
                skill_label=skill_label(game.skill_min, game.skill_max),
                visibility=game.visibility,
                player_cap=game.player_cap,
                participant_count=game.participant_count,
                spots_available=game.player_cap - game.participant_count,
                organizer_id=game.organizer_id,
                host_name=host.display_name,
                host_show_up_rate=float(host.reliability_score),
                host_rating=float(host_rating),
                rules_note=game.rules_note,
                scoring_rule=game.scoring_rule,
                is_recorded=game.is_recorded,
                distance_m=None if dist is None else float(dist),
            )
        )
    return out


async def roster(session: AsyncSession, game_id: uuid.UUID) -> list[GameParticipant]:
    rows = await session.scalars(
        sa.select(GameParticipant)
        .where(GameParticipant.game_id == game_id)
        .order_by(GameParticipant.joined_at, GameParticipant.user_id)
    )
    return list(rows)
