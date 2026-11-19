"""
Recommended games feed (FR-04 filter + FR-12 ranking).

Glue between the database and the pure ranking in server/app/services/matching.py:
  1. `games.list_visible_games` returns only games this player may see (FR-04).
  2. An optional skill chip filter narrows that list (the "filter by skill level"
     step of the Find and Join flow). All-levels games always stay.
  3. Each remaining game gets its roster's average rating, and
     `matching.rank_games` puts them in order.
Like the rest of the CRUD layer, nothing here commits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.core.config import get_settings
from server.app.crud._common import utc_now
from server.app.crud.errors import NotFoundError
from server.app.crud.games import VisibleGame, list_visible_games
from server.app.models import GameParticipant, ParticipantStatus, PlayerRating
from server.app.services import matching
from server.app.services.matching import MatchCandidate, MatchPlayer, RankedGame

SKILL_CHIPS = ("Beginner", "Mid", "Advanced", "All")


@dataclass(frozen=True)
class RecommendedGame:
    game: VisibleGame
    ranking: RankedGame


async def match_player(session: AsyncSession, user_id: uuid.UUID) -> MatchPlayer:
    rating = await session.get(PlayerRating, user_id)
    if rating is None:
        raise NotFoundError("player not found")
    return MatchPlayer(rating=float(rating.display_rating), is_provisional=rating.is_provisional)


async def roster_average_ratings(
    session: AsyncSession,
    game_ids: Sequence[uuid.UUID],
    exclude_user_id: uuid.UUID,
) -> dict[uuid.UUID, float]:
    """
    Average display rating of each game's confirmed players, leaving out the
    player asking (so joining a game does not change how well it fits them).
    Games with nobody else confirmed are missing from the result.
    """
    if not game_ids:
        return {}
    stmt = (
        sa.select(GameParticipant.game_id, sa.func.avg(PlayerRating.display_rating))
        .join(PlayerRating, PlayerRating.user_id == GameParticipant.user_id)
        .where(
            GameParticipant.game_id.in_(game_ids),
            GameParticipant.status == ParticipantStatus.CONFIRMED,
            GameParticipant.user_id != exclude_user_id,
        )
        .group_by(GameParticipant.game_id)
    )
    averages = {}
    for game_id, average in (await session.execute(stmt)).all():
        averages[game_id] = float(average)
    return averages


def filter_by_skill_chip(games: Sequence[VisibleGame], skill: Optional[str]) -> list[VisibleGame]:
    """Keep games with the chosen label. All-levels games match every chip."""
    if skill is None or skill == "All":
        return list(games)
    if skill not in SKILL_CHIPS:
        raise ValueError(f"unknown skill level {skill!r}")
    kept = []
    for game in games:
        if game.skill_label == skill or game.skill_label == "All":
            kept.append(game)
    return kept


async def recommended_games(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    near: tuple[float, float],
    radius_m: Optional[float] = None,
    skill: Optional[str] = None,
    court_id: Optional[uuid.UUID] = None,
    now: Optional[datetime] = None,
    player: Optional[MatchPlayer] = None,
) -> list[RecommendedGame]:
    """
    Games the player may see near `near`, best match first. Pass `player` if the
    caller already loaded it (saves a query); otherwise it is looked up here.
    """
    settings = get_settings()
    now = utc_now(now)
    if radius_m is None:
        radius_m = settings.match_default_radius_m

    if player is None:
        player = await match_player(session, user_id)
    visible = await list_visible_games(session, user_id, near=near, radius_m=radius_m, now=now, court_id=court_id)
    visible = filter_by_skill_chip(visible, skill)

    game_ids = [game.game_id for game in visible]
    averages = await roster_average_ratings(session, game_ids, exclude_user_id=user_id)

    candidates = []
    by_id = {}
    for game in visible:
        skill_min = None
        skill_max = None
        if game.skill_min is not None and game.skill_max is not None:
            skill_min = float(game.skill_min)
            skill_max = float(game.skill_max)
        distance_m = 0.0
        if game.distance_m is not None:
            distance_m = game.distance_m
        candidates.append(
            MatchCandidate(
                game_id=game.game_id,
                starts_at=game.starts_at,
                distance_m=distance_m,
                skill_min=skill_min,
                skill_max=skill_max,
                roster_average=averages.get(game.game_id),
            )
        )
        by_id[game.game_id] = game

    ranked = matching.rank_games(
        player,
        candidates,
        now=now,
        radius_m=radius_m,
        settings=matching.settings_from_config(settings),
    )
    results = []
    for ranking in ranked:
        results.append(RecommendedGame(game=by_id[ranking.game_id], ranking=ranking))
    return results
