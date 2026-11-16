"""Unit tests for the placeholder rating engine (no database needed)."""

from __future__ import annotations

import uuid

import pytest

from server.app.services import rating_engine as re_
from server.app.services.rating_engine import GameInputs, PlayerGameInput, RatingState


def _game(team_scores, players):
    return GameInputs(
        game_id=uuid.uuid4(),
        team_scores=team_scores,
        players={p.user_id: p for p in players},
    )


def _p(team, conf=0.9):
    return PlayerGameInput(user_id=uuid.uuid4(), team=team, points=1, fga=1, fgm=1, mean_confidence=conf)


def test_display_scale_round_trip():
    assert re_.to_display(1500) == 3.0
    assert re_.from_display(4.2) == pytest.approx(1800)
    assert re_.to_display(re_.from_display(4.2)) == 4.2
    assert re_.to_display(-10_000) == 1.0 and re_.to_display(10_000) == 5.0


def test_winners_gain_losers_lose_and_counts_increment():
    a, b = _p("A"), _p("B")
    game = _game({"A": 11, "B": 7}, [a, b])
    out = re_.rate_game({a.user_id: RatingState(), b.user_id: RatingState()}, game)
    assert out[a.user_id].rating > 1500 > out[b.user_id].rating
    assert out[a.user_id].rating - 1500 == pytest.approx(1500 - out[b.user_id].rating)
    assert out[a.user_id].rated_games == 1 and out[a.user_id].rd < 350


def test_confidence_scales_the_change():
    a_hi, a_lo, b = _p("A", 1.0), _p("A", 0.5), _p("B")
    states = {p.user_id: RatingState() for p in (a_hi, a_lo, b)}
    out = re_.rate_game(states, _game({"A": 3, "B": 1}, [a_hi, a_lo, b]))
    assert out[a_hi.user_id].rating - 1500 == pytest.approx(2 * (out[a_lo.user_id].rating - 1500))


def test_opponent_rating_matters():
    a, b = _p("A"), _p("B")
    game = _game({"A": 2, "B": 1}, [a, b])
    vs_weak = re_.rate_game({a.user_id: RatingState(), b.user_id: RatingState(rating=1200)}, game)
    vs_strong = re_.rate_game({a.user_id: RatingState(), b.user_id: RatingState(rating=1800)}, game)
    assert vs_strong[a.user_id].rating > vs_weak[a.user_id].rating


def test_no_result_without_two_rated_teams():
    a = _p("A")
    out = re_.rate_game({a.user_id: RatingState()}, _game({"A": 5}, [a]))
    assert out[a.user_id].rating == 1500 and out[a.user_id].rated_games == 1


def test_deterministic_and_requires_every_state():
    a, b = _p("A"), _p("B")
    game = _game({"A": 1, "B": 1}, [a, b])
    states = {a.user_id: RatingState(rating=1600), b.user_id: RatingState()}
    assert re_.rate_game(states, game) == re_.rate_game(dict(reversed(list(states.items()))), game)
    with pytest.raises(ValueError):
        re_.rate_game({a.user_id: RatingState()}, game)


def test_json_round_trip_is_exact():
    s = RatingState(rating=1512.3456789012345, rd=315.0, volatility=0.06, rated_games=2)
    assert RatingState.from_json(s.to_json()) == s
