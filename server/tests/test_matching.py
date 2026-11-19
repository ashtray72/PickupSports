"""
Unit tests for the game ranking (FR-12 / SYS-12). No database needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from server.app.core.config import get_settings
from server.app.services import matching
from server.app.services.matching import MatchCandidate, MatchPlayer, MatchSettings

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
RADIUS_M = 8000.0
SETTINGS = MatchSettings()


def make_candidate(
    *,
    hours: float = 2.0,
    distance_m: float = 1000.0,
    skill: tuple[float, float] | None = (3.0, 4.0),
    roster_average: float | None = 3.5,
    game_id: uuid.UUID | None = None,
) -> MatchCandidate:
    skill_min = None
    skill_max = None
    if skill is not None:
        skill_min = skill[0]
        skill_max = skill[1]
    if game_id is None:
        game_id = uuid.uuid4()
    return MatchCandidate(
        game_id=game_id,
        starts_at=NOW + timedelta(hours=hours),
        distance_m=distance_m,
        skill_min=skill_min,
        skill_max=skill_max,
        roster_average=roster_average,
    )


def ranked_ids(player: MatchPlayer, candidates: list[MatchCandidate]) -> list[uuid.UUID]:
    ranked = matching.rank_games(player, candidates, now=NOW, radius_m=RADIUS_M, settings=SETTINGS)
    return [game.game_id for game in ranked]


ESTABLISHED = MatchPlayer(rating=3.5, is_provisional=False)


# --- skill fit ---------------------------------------------------------------


def test_skill_fit_is_perfect_when_rating_matches_roster():
    assert matching.skill_fit(3.5, 3.0, 4.0, 3.5, 1.0) == 1.0


def test_skill_fit_drops_with_gap_relative_to_range_width():
    # Gap 0.5 in a 1.0-wide range -> 0.5. Same gap in a 2.0-wide range -> 0.75.
    assert matching.skill_fit(4.0, 3.0, 4.0, 3.5, 1.0) == pytest.approx(0.5)
    assert matching.skill_fit(4.0, 2.0, 4.0, 3.5, 1.0) == pytest.approx(0.75)


def test_skill_fit_uses_middle_of_range_for_empty_roster():
    assert matching.skill_fit(3.5, 3.0, 4.0, None, 1.0) == 1.0


def test_all_levels_game_uses_full_rating_scale():
    # No range: width is 1.0-5.0 = 4.0, middle is 3.0.
    assert matching.skill_fit(4.0, None, None, None, 1.0) == pytest.approx(0.75)


def test_narrow_range_uses_minimum_width_instead_of_dividing_by_zero():
    assert matching.skill_fit(4.0, 4.0, 4.0, 3.5, 1.0) == pytest.approx(0.5)


def test_skill_fit_never_goes_negative():
    assert matching.skill_fit(5.0, 2.0, 3.0, 2.0, 1.0) == 0.0


# --- distance and start time -------------------------------------------------


@pytest.mark.parametrize(
    "distance_m, expected",
    [(0.0, 1.0), (4000.0, 0.5), (8000.0, 0.0), (9000.0, 0.0)],
)
def test_distance_score(distance_m, expected):
    assert matching.distance_score(distance_m, RADIUS_M) == pytest.approx(expected)


@pytest.mark.parametrize(
    "hours, expected",
    [(-0.5, 1.0), (0.0, 1.0), (84.0, 0.5), (168.0, 0.0)],
)
def test_start_score(hours, expected):
    starts_at = NOW + timedelta(hours=hours)
    assert matching.start_score(starts_at, NOW, timedelta(days=7)) == pytest.approx(expected)


def test_score_is_weighted_sum_of_parts():
    candidate = make_candidate(hours=84.0, distance_m=4000.0, skill=(3.0, 4.0), roster_average=3.5)
    result = matching.score_game(ESTABLISHED, candidate, now=NOW, radius_m=RADIUS_M, settings=SETTINGS)
    # 0.5 * 1.0 + 0.3 * 0.5 + 0.2 * 0.5
    assert result.score == pytest.approx(0.75)
    assert result.skill_fit == 1.0
    assert result.distance_score == pytest.approx(0.5)
    assert result.start_score == pytest.approx(0.5)


def test_provisional_player_skill_fit_counts_half():
    candidate = make_candidate(hours=84.0, distance_m=4000.0, roster_average=3.5)
    provisional = MatchPlayer(rating=3.5, is_provisional=True)
    result = matching.score_game(provisional, candidate, now=NOW, radius_m=RADIUS_M, settings=SETTINGS)
    # 0.25 * 1.0 + 0.3 * 0.5 + 0.2 * 0.5
    assert result.score == pytest.approx(0.5)


# --- ranking -----------------------------------------------------------------


def test_better_skill_match_ranks_first_when_other_factors_are_equal():
    close = make_candidate(roster_average=3.5)
    far_off = make_candidate(roster_average=4.0)
    assert ranked_ids(ESTABLISHED, [far_off, close]) == [close.game_id, far_off.game_id]


def test_closer_game_ranks_first_when_other_factors_are_equal():
    near = make_candidate(distance_m=500.0)
    far = make_candidate(distance_m=5000.0)
    assert ranked_ids(ESTABLISHED, [far, near]) == [near.game_id, far.game_id]


def test_sooner_game_ranks_first_when_other_factors_are_equal():
    soon = make_candidate(hours=1.0)
    later = make_candidate(hours=48.0)
    assert ranked_ids(ESTABLISHED, [later, soon]) == [soon.game_id, later.game_id]


def test_games_outside_the_window_are_dropped():
    inside = make_candidate(hours=24.0)
    outside = make_candidate(hours=24.0 * 8)
    assert ranked_ids(ESTABLISHED, [inside, outside]) == [inside.game_id]


def test_identical_scores_break_tie_by_lower_id():
    first_id = uuid.UUID(int=1)
    second_id = uuid.UUID(int=2)
    a = make_candidate(game_id=second_id)
    b = make_candidate(game_id=first_id)
    ranked = matching.rank_games(ESTABLISHED, [a, b], now=NOW, radius_m=RADIUS_M, settings=SETTINGS)
    assert ranked[0].score == ranked[1].score
    assert [g.game_id for g in ranked] == [first_id, second_id]


def test_equal_scores_break_tie_by_shorter_distance():
    # a: perfect skill fit, halfway to the radius -> 0.5 + 0.15
    # b: skill fit 0.7, right here               -> 0.35 + 0.3
    a = make_candidate(game_id=uuid.UUID(int=1), distance_m=4000.0, roster_average=3.5)
    b = make_candidate(game_id=uuid.UUID(int=2), distance_m=0.0, roster_average=3.8)
    ranked = matching.rank_games(ESTABLISHED, [a, b], now=NOW, radius_m=RADIUS_M, settings=SETTINGS)
    assert ranked[0].score == ranked[1].score
    assert [g.game_id for g in ranked] == [b.game_id, a.game_id]


def test_equal_scores_break_tie_by_earlier_start():
    # Both are at distance 0 (+0.3 each).
    # a: perfect skill fit, starts in 84 h -> 0.5 + 0.1
    # b: skill fit 0.8, starts now        -> 0.4 + 0.2
    a = make_candidate(game_id=uuid.UUID(int=1), hours=84.0, distance_m=0.0, roster_average=3.5)
    b = make_candidate(game_id=uuid.UUID(int=2), hours=0.0, distance_m=0.0, roster_average=3.7)
    ranked = matching.rank_games(ESTABLISHED, [a, b], now=NOW, radius_m=RADIUS_M, settings=SETTINGS)
    assert ranked[0].score == ranked[1].score
    assert [g.game_id for g in ranked] == [b.game_id, a.game_id]


def test_ranking_is_deterministic_regardless_of_input_order():
    games = [make_candidate(hours=h, distance_m=d) for h, d in [(1, 3000), (5, 200), (30, 900), (2, 7000)]]
    expected = ranked_ids(ESTABLISHED, games)
    assert ranked_ids(ESTABLISHED, list(reversed(games))) == expected


def test_empty_input_returns_empty_list():
    assert matching.rank_games(ESTABLISHED, [], now=NOW, radius_m=RADIUS_M) == []


def test_ranking_200_games_is_fast():
    # NFR-06.1 gives the whole map load 2 s; ranking should be a tiny part of it.
    import time

    games = [make_candidate(hours=i % 160, distance_m=(i * 37) % 8000) for i in range(200)]
    started = time.perf_counter()
    matching.rank_games(ESTABLISHED, games, now=NOW, radius_m=RADIUS_M)
    assert time.perf_counter() - started < 0.05


def test_settings_come_from_config():
    built = matching.settings_from_config(get_settings())
    assert built.weight_skill + built.weight_distance + built.weight_start == pytest.approx(1.0)
    assert built.window == timedelta(days=7)
