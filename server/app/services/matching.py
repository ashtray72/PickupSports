"""
Game ranking for the recommended feed (FR-12 / SYS-12).

Design: D1 "Filter-and-Score" from the matching algorithm trade study.

1. Filter. The database query decides which games a player is allowed to see
   (FR-04: skill range, visibility, blocks, radius). That lives in
   server/app/crud/games.py `list_visible_games`. This module never adds a game
   back, it only drops games outside the time window and puts the rest in order.
2. Score. Each game gets a score from 0 to 1:

       score = w_skill * skill_fit + w_distance * distance_score + w_start * start_score

   skill_fit      = 1 - |player rating - roster average| / skill range width
   distance_score = 1 - distance / search radius
   start_score    = 1 - hours until start / hours in the window

   Each part is clamped to [0, 1]. For provisional players the skill weight is
   multiplied by `provisional_skill_factor`, since their rating is still a guess.
3. Sort. Highest score first. Ties go to the earlier start, then the shorter
   distance, then the lower game id, so the same inputs always give the same order.

Everything here is a pure function of its arguments, so it can be tested
without a database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

# Skill range used for all-levels games (no skill_min / skill_max).
FULL_SKILL_MIN = 1.0
FULL_SKILL_MAX = 5.0

# Scores are rounded before comparing so floating point noise does not decide ties.
SCORE_DECIMALS = 6


@dataclass(frozen=True)
class MatchSettings:
    weight_skill: float = 0.5
    weight_distance: float = 0.3
    weight_start: float = 0.2
    provisional_skill_factor: float = 0.5
    window: timedelta = timedelta(days=7)
    min_skill_width: float = 1.0


@dataclass(frozen=True)
class MatchPlayer:
    rating: float  # display rating, 1.0-5.0
    is_provisional: bool


@dataclass(frozen=True)
class MatchCandidate:
    game_id: uuid.UUID
    starts_at: datetime
    distance_m: float
    skill_min: Optional[float]  # None for all-levels games
    skill_max: Optional[float]
    roster_average: Optional[float]  # average display rating of the roster, None if empty


@dataclass(frozen=True)
class RankedGame:
    game_id: uuid.UUID
    score: float
    skill_fit: float
    distance_score: float
    start_score: float


def clamp_unit(value: float) -> float:
    """Limit a value to the range [0, 1]."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def skill_fit(
    player_rating: float,
    skill_min: Optional[float],
    skill_max: Optional[float],
    roster_average: Optional[float],
    min_width: float,
) -> float:
    """
    How close the player is to the people already in the game, relative to how
    wide the game's skill range is. 1.0 is a perfect match.
    """
    low = FULL_SKILL_MIN
    high = FULL_SKILL_MAX
    if skill_min is not None and skill_max is not None:
        low = skill_min
        high = skill_max

    width = high - low
    if width < min_width:
        width = min_width

    # With nobody on the roster yet, compare against the middle of the range.
    target = (low + high) / 2.0
    if roster_average is not None:
        target = roster_average

    gap = abs(player_rating - target)
    return clamp_unit(1.0 - gap / width)


def distance_score(distance_m: float, radius_m: float) -> float:
    """1.0 at the player's location, 0.0 at the edge of the search radius."""
    if radius_m <= 0:
        return 0.0
    return clamp_unit(1.0 - distance_m / radius_m)


def start_score(starts_at: datetime, now: datetime, window: timedelta) -> float:
    """1.0 for a game starting now (or already running), 0.0 at the end of the window."""
    window_hours = window.total_seconds() / 3600.0
    if window_hours <= 0:
        return 0.0
    hours_until_start = (starts_at - now).total_seconds() / 3600.0
    return clamp_unit(1.0 - hours_until_start / window_hours)


def score_game(
    player: MatchPlayer,
    candidate: MatchCandidate,
    *,
    now: datetime,
    radius_m: float,
    settings: MatchSettings,
) -> RankedGame:
    fit = skill_fit(
        player.rating,
        candidate.skill_min,
        candidate.skill_max,
        candidate.roster_average,
        settings.min_skill_width,
    )
    near = distance_score(candidate.distance_m, radius_m)
    soon = start_score(candidate.starts_at, now, settings.window)

    skill_weight = settings.weight_skill
    if player.is_provisional:
        skill_weight = skill_weight * settings.provisional_skill_factor

    total = skill_weight * fit + settings.weight_distance * near + settings.weight_start * soon
    return RankedGame(
        game_id=candidate.game_id,
        score=round(total, SCORE_DECIMALS),
        skill_fit=fit,
        distance_score=near,
        start_score=soon,
    )


def rank_games(
    player: MatchPlayer,
    candidates: Sequence[MatchCandidate],
    *,
    now: datetime,
    radius_m: float,
    settings: Optional[MatchSettings] = None,
) -> list[RankedGame]:
    """
    Score and sort games the player is already allowed to see. Games that start
    after the time window are left out. The result is deterministic.
    """
    if settings is None:
        settings = MatchSettings()

    window_end = now + settings.window
    by_id: dict[uuid.UUID, MatchCandidate] = {}
    ranked: list[RankedGame] = []
    for candidate in candidates:
        if candidate.starts_at > window_end:
            continue
        by_id[candidate.game_id] = candidate
        ranked.append(score_game(player, candidate, now=now, radius_m=radius_m, settings=settings))

    def sort_key(game: RankedGame) -> tuple:
        candidate = by_id[game.game_id]
        # Highest score first, then earlier start, shorter distance, lower id.
        return (-game.score, candidate.starts_at, candidate.distance_m, str(game.game_id))

    ranked.sort(key=sort_key)
    return ranked


def settings_from_config(config) -> MatchSettings:
    """Build MatchSettings from server.app.core.config.Settings."""
    return MatchSettings(
        weight_skill=config.match_weight_skill,
        weight_distance=config.match_weight_distance,
        weight_start=config.match_weight_start,
        provisional_skill_factor=config.match_provisional_skill_factor,
        window=config.match_window,
        min_skill_width=config.match_min_skill_width,
    )
