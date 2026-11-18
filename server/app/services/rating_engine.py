"""
Rating engine interface (SYS-12 / FR-12).

The real formulas belong to deliverable DEL-06. The database layer only needs
this contract:

    rate_game(states, game) -> new_states

* `states` holds the pre-game RatingState of every rated player in the game.
* `game` holds that game's verified inputs (team scores and each rated player's
  confidence-weighted box score line).
* It must be a pure, deterministic function of its arguments: the replay in
  server/app/crud/ratings.py relies on that to make "recompute after a retag"
  equal "rate everything again from scratch".
* It rates a whole game at once because a formula may use teammates' and
  opponents' ratings (this placeholder does).

Placeholder formula until DEL-06 replaces it: team-average Elo on the game
result, with each player's K-factor scaled by the mean confidence of their
counted shots, and RD shrinking per rated game.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_VOLATILITY = 0.06

ELO_K = 32.0
MIN_RD = 50.0
RD_DECAY = 0.9


def to_display(rating: float) -> float:
    """Internal rating -> the 1.0-5.0 scale shown in the app (1500 -> 3.0)."""
    return round(min(5.0, max(1.0, 3.0 + (rating - DEFAULT_RATING) / 250.0)), 1)


def from_display(display: float) -> float:
    """1.0-5.0 self-assessment -> internal rating (3.0 -> 1500)."""
    return DEFAULT_RATING + (float(display) - 3.0) * 250.0


@dataclass(frozen=True)
class RatingState:
    rating: float = DEFAULT_RATING
    rd: float = DEFAULT_RD
    volatility: float = DEFAULT_VOLATILITY
    rated_games: int = 0

    @property
    def display_rating(self) -> float:
        return to_display(self.rating)

    def to_json(self) -> dict[str, Any]:
        return {
            "rating": self.rating,
            "rd": self.rd,
            "volatility": self.volatility,
            "rated_games": self.rated_games,
            "display_rating": self.display_rating,  # informational only
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "RatingState":
        return cls(
            rating=float(data["rating"]),
            rd=float(data["rd"]),
            volatility=float(data["volatility"]),
            rated_games=int(data["rated_games"]),
        )

    @classmethod
    def initial(cls, base_rating: float) -> "RatingState":
        return cls(rating=float(base_rating))


@dataclass(frozen=True)
class PlayerGameInput:
    user_id: uuid.UUID
    team: Optional[str]
    points: int
    fga: int
    fgm: int
    mean_confidence: Optional[float]  # None when the player has no counted shots


@dataclass(frozen=True)
class GameInputs:
    game_id: uuid.UUID
    team_scores: Mapping[str, int] = field(default_factory=dict)
    players: Mapping[uuid.UUID, PlayerGameInput] = field(default_factory=dict)


def rate_game(states: Mapping[uuid.UUID, RatingState], game: GameInputs) -> dict[uuid.UUID, RatingState]:
    """Placeholder team-average Elo. Deterministic: iterates in sorted id order."""
    player_ids = sorted(game.players)
    missing = [uid for uid in player_ids if uid not in states]
    if missing:
        raise ValueError(f"rate_game: no rating state for players {missing}")

    by_team: dict[str, list[uuid.UUID]] = {}
    for uid in player_ids:
        team = game.players[uid].team
        if team is not None:
            by_team.setdefault(team, []).append(uid)

    actual: dict[str, float] = {}
    expected: dict[str, float] = {}
    teams = sorted(by_team)
    if len(teams) == 2 and all(t in game.team_scores for t in teams):
        avg = {t: sum(states[u].rating for u in by_team[t]) / len(by_team[t]) for t in teams}
        a, b = teams
        sa, sb = game.team_scores[a], game.team_scores[b]
        actual[a] = 1.0 if sa > sb else 0.5 if sa == sb else 0.0
        actual[b] = 1.0 - actual[a]
        expected[a] = 1.0 / (1.0 + 10.0 ** ((avg[b] - avg[a]) / 400.0))
        expected[b] = 1.0 - expected[a]

    out: dict[uuid.UUID, RatingState] = {}
    for uid in player_ids:
        prev = states[uid]
        player = game.players[uid]
        rating = prev.rating
        if player.team in actual:
            weight = player.mean_confidence if player.mean_confidence is not None else 1.0
            rating = prev.rating + ELO_K * weight * (actual[player.team] - expected[player.team])
        out[uid] = RatingState(
            rating=rating,
            rd=max(MIN_RD, prev.rd * RD_DECAY),
            volatility=prev.volatility,
            rated_games=prev.rated_games + 1,
        )
    return out
