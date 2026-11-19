"""
Request/response models for the games API (server/app/api/v1/games.py).

Field names match the client's GameSummary type (client/src/types/court.ts)
where they overlap, so the app can render a response without reshaping it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel

from server.app.crud.recommendations import RecommendedGame

try:  # West Lafayette local time; Windows needs the `tzdata` package for zoneinfo.
    from zoneinfo import ZoneInfo

    CAMPUS_TZ = ZoneInfo("America/Indiana/Indianapolis")
except Exception:  # pragma: no cover - fallback without tz database
    CAMPUS_TZ = timezone(timedelta(hours=-5), "EST")

METERS_PER_MILE = 1609.344

SkillLevel = Literal["Beginner", "Mid", "Advanced", "All"]


class ScoreBreakdown(BaseModel):
    skill_fit: float
    distance: float
    start_time: float


class RecommendedGameOut(BaseModel):
    game_id: str
    court_id: str
    court_name: str
    starts_at: datetime
    ends_at: datetime
    start_time: str  # campus local time, e.g. "11:30 AM"
    end_time: str
    skill_level: str
    spots_available: int
    total_spots: int
    status: Literal["open", "full"]
    visibility: str
    distance_miles: float
    host_name: str
    host_rating: float
    match_score: float
    score_breakdown: ScoreBreakdown


class RecommendedGamesResponse(BaseModel):
    player_rating: float
    is_provisional: bool
    radius_miles: float
    games: list[RecommendedGameOut]


class JoinGameResponse(BaseModel):
    game_id: str
    status: Literal["confirmed", "pending"]
    spots_available: int


def format_clock(moment: datetime) -> str:
    """'11:30 AM' style time in campus local time."""
    local = moment.astimezone(CAMPUS_TZ)
    hour = local.hour % 12
    if hour == 0:
        hour = 12
    suffix = "AM"
    if local.hour >= 12:
        suffix = "PM"
    return f"{hour}:{local.minute:02d} {suffix}"


def recommended_game_out(item: RecommendedGame) -> RecommendedGameOut:
    game = item.game
    ranking = item.ranking

    status = "open"
    if game.spots_available <= 0:
        status = "full"

    distance_miles = 0.0
    if game.distance_m is not None:
        distance_miles = round(game.distance_m / METERS_PER_MILE, 2)

    return RecommendedGameOut(
        game_id=str(game.game_id),
        court_id=str(game.court_id),
        court_name=game.court_name,
        starts_at=game.starts_at,
        ends_at=game.ends_at,
        start_time=format_clock(game.starts_at),
        end_time=format_clock(game.ends_at),
        skill_level=game.skill_label,
        spots_available=game.spots_available,
        total_spots=game.player_cap,
        status=status,
        visibility=game.visibility.value,
        distance_miles=distance_miles,
        host_name=game.host_name,
        host_rating=game.host_rating,
        match_score=round(ranking.score, 3),
        score_breakdown=ScoreBreakdown(
            skill_fit=round(ranking.skill_fit, 3),
            distance=round(ranking.distance_score, 3),
            start_time=round(ranking.start_score, 3),
        ),
    )
