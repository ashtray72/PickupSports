"""
Games API, version 1.

GET  /api/v1/games/recommended   ranked feed of games the player may join (FR-04, FR-12)
POST /api/v1/games/{game_id}/join join or request to join a game (FR-05)
"""

from __future__ import annotations

import uuid
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.api.deps import current_user_id, get_session
from server.app.core.config import get_settings
from server.app.crud import games as games_crud
from server.app.crud import recommendations
from server.app.models import Game
from server.app.schemas.game import (
    METERS_PER_MILE,
    JoinGameResponse,
    RecommendedGamesResponse,
    SkillLevel,
    recommended_game_out,
)

router = APIRouter(prefix="/games", tags=["games"])

MAX_RADIUS_MILES = 25.0


@router.get("/recommended", response_model=RecommendedGamesResponse)
async def get_recommended_games(
    lat: float = Query(ge=-90, le=90, description="Player latitude"),
    lng: float = Query(ge=-180, le=180, description="Player longitude"),
    radius_mi: Optional[float] = Query(default=None, gt=0, le=MAX_RADIUS_MILES, description="Search radius in miles"),
    skill: Optional[SkillLevel] = Query(default=None, description="Skill chip; all-levels games always match"),
    court_id: Optional[uuid.UUID] = Query(default=None, description="Only games at this court (court sheet)"),
    user_id: uuid.UUID = Depends(current_user_id),
    session: AsyncSession = Depends(get_session),
) -> RecommendedGamesResponse:
    radius_m = get_settings().match_default_radius_m
    if radius_mi is not None:
        radius_m = radius_mi * METERS_PER_MILE

    player = await recommendations.match_player(session, user_id)
    results = await recommendations.recommended_games(
        session,
        user_id,
        near=(lat, lng),
        radius_m=radius_m,
        skill=skill,
        court_id=court_id,
    )
    games = []
    for item in results:
        games.append(recommended_game_out(item))
    return RecommendedGamesResponse(
        player_rating=player.rating,
        is_provisional=player.is_provisional,
        radius_miles=round(radius_m / METERS_PER_MILE, 2),
        games=games,
    )


@router.post("/{game_id}/join", response_model=JoinGameResponse)
async def join_game(
    game_id: uuid.UUID,
    user_id: uuid.UUID = Depends(current_user_id),
    session: AsyncSession = Depends(get_session),
) -> JoinGameResponse:
    """
    Public games confirm right away; request-to-join games return "pending".
    A full game returns 409 (the roster row lock in crud.games.join_game makes
    sure two players can't both take the last spot).
    """
    participant_status = await games_crud.join_game(session, game_id, user_id)
    spots_available = await session.scalar(
        sa.select(Game.player_cap - Game.participant_count).where(Game.id == game_id)
    )
    await session.commit()
    return JoinGameResponse(
        game_id=str(game_id),
        status=participant_status.value,
        spots_available=spots_available,
    )
