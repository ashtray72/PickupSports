"""
Games API: ranked feed (FR-04 filter + FR-12 ranking) and joining (FR-05).

Requests go through the real FastAPI app (httpx ASGI transport) against the
test database. The app opens its own sessions, so API tests use the
`committing` fixture (cross-connection visibility); data is truncated after
each test.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import sqlalchemy as sa

from server.app.api import deps
from server.app.core.config import get_settings
from server.app.crud import recommendations
from server.app.main import app
from server.app.models import Game, GameParticipant, ParticipantStatus, Visibility
from server.app.schemas.game import format_clock
from server.tests.db import factories as f

LAT, LON = f.CENTER
URL = "/api/v1/games/recommended"


def _set_dev_auth(monkeypatch, enabled: bool) -> None:
    if enabled:
        monkeypatch.setenv("ALLOW_DEV_AUTH", "1")
    else:
        monkeypatch.delenv("ALLOW_DEV_AUTH", raising=False)
    get_settings.cache_clear()


@pytest.fixture
async def api(committing, monkeypatch):
    _set_dev_auth(monkeypatch, True)
    app.dependency_overrides[deps.session_factory] = lambda: committing
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    monkeypatch.undo()
    get_settings.cache_clear()


def as_user(user) -> dict[str, str]:
    return {"X-Dev-User": user.firebase_uid}


def feed_params(**extra) -> dict:
    params = {"lat": LAT, "lng": LON}
    params.update(extra)
    return params


async def build_feed_world(committing):
    """One player at 3.5 and a mix of games, some of which they must not see."""
    async with committing() as s, s.begin():
        player = await f.user(s, rating=3.5, name="Demo Player")
        host = await f.user(s, rating=3.5, name="Host")
        near_lat, near_lon = f.offset_north(LAT, LON, 200)
        mid_lat, mid_lon = f.offset_north(LAT, LON, 2000)
        far_lat, far_lon = f.offset_north(LAT, LON, 20_000)
        near_court = await f.court(s, lat=near_lat, lon=near_lon, name="Near Court")
        mid_court = await f.court(s, lat=mid_lat, lon=mid_lon, name="Mid Court")
        far_court = await f.court(s, lat=far_lat, lon=far_lon, name="Far Court")

        games = {
            "mid_near": await f.game(s, organizer=host, court_obj=near_court, starts_at=f.at(hours=2), skill=(3.0, 4.0)),
            "all_mid": await f.game(s, organizer=host, court_obj=mid_court, starts_at=f.at(hours=3)),
            # Hidden: out of skill range, private, too far, past the 7-day window.
            "advanced": await f.game(s, organizer=host, court_obj=near_court, starts_at=f.at(hours=4), skill=(4.0, 5.0)),
            "private": await f.game(s, organizer=host, court_obj=near_court, starts_at=f.at(hours=5), visibility=Visibility.PRIVATE),
            "far": await f.game(s, organizer=host, court_obj=far_court, starts_at=f.at(hours=2)),
            "next_week": await f.game(s, organizer=host, court_obj=near_court, starts_at=f.at(days=8)),
        }
        ids = {}
        for name, game in games.items():
            ids[name] = str(game.id)
    return player, ids, near_court


# --- auth --------------------------------------------------------------------


async def test_missing_user_header_is_401(api):
    response = await api.get(URL, params=feed_params())
    assert response.status_code == 401


async def test_unknown_user_is_401(api):
    response = await api.get(URL, params=feed_params(), headers={"X-Dev-User": "seed:nobody"})
    assert response.status_code == 401


async def test_dev_user_header_is_refused_unless_dev_auth_is_enabled(api, committing, monkeypatch):
    """NFR-05.1: with ALLOW_DEV_AUTH unset, even a real user's header gets 401."""
    async with committing() as s, s.begin():
        player = await f.user(s)
        game = await f.game(s)
    _set_dev_auth(monkeypatch, False)

    feed = await api.get(URL, params=feed_params(), headers=as_user(player))
    join = await api.post(f"/api/v1/games/{game.id}/join", headers=as_user(player))
    assert feed.status_code == 401 and join.status_code == 401
    assert "ALLOW_DEV_AUTH" in feed.json()["detail"]
    async with committing() as s:
        count = await s.scalar(sa.select(Game.participant_count).where(Game.id == game.id))
    assert count == 1  # the refused join changed nothing

    _set_dev_auth(monkeypatch, True)
    assert (await api.get(URL, params=feed_params(), headers=as_user(player))).status_code == 200


# --- recommended feed --------------------------------------------------------


async def test_feed_shows_only_eligible_games_best_match_first(api, committing):
    player, ids, _ = await build_feed_world(committing)
    response = await api.get(URL, params=feed_params(), headers=as_user(player))
    assert response.status_code == 200
    body = response.json()

    returned = [g["game_id"] for g in body["games"]]
    # Nearer and sooner, with the same skill fit, so mid_near wins.
    assert returned == [ids["mid_near"], ids["all_mid"]]

    scores = [g["match_score"] for g in body["games"]]
    assert scores == sorted(scores, reverse=True)
    assert body["player_rating"] == 3.5
    assert body["radius_miles"] == 5.0


async def test_feed_game_fields_match_client_shape(api, committing):
    player, ids, near_court = await build_feed_world(committing)
    body = (await api.get(URL, params=feed_params(), headers=as_user(player))).json()
    first = body["games"][0]

    assert first["court_id"] == str(near_court.id)
    assert first["court_name"] == "Near Court"
    assert first["skill_level"] == "Mid"
    assert first["total_spots"] == 10
    assert first["spots_available"] == 9  # organizer is on the roster
    assert first["status"] == "open"
    assert first["distance_miles"] == pytest.approx(0.12, abs=0.01)
    assert first["host_name"] == "Host"
    assert set(first["score_breakdown"]) == {"skill_fit", "distance", "start_time"}

    async with committing() as s:
        game = await s.get(Game, first["game_id"])
        assert first["start_time"] == format_clock(game.starts_at)


@pytest.mark.parametrize(
    "skill, expected",
    [
        ("Mid", ["mid_near", "all_mid"]),
        ("Advanced", ["all_mid"]),  # the Advanced game is outside the player's range
        ("Beginner", ["all_mid"]),
        ("All", ["mid_near", "all_mid"]),
    ],
)
async def test_skill_chip_filter_keeps_all_levels_games(api, committing, skill, expected):
    player, ids, _ = await build_feed_world(committing)
    response = await api.get(URL, params=feed_params(skill=skill), headers=as_user(player))
    returned = [g["game_id"] for g in response.json()["games"]]
    expected_ids = []
    for name in expected:
        expected_ids.append(ids[name])
    assert returned == expected_ids


async def test_unknown_skill_chip_is_422(api, committing):
    player, _, _ = await build_feed_world(committing)
    response = await api.get(URL, params=feed_params(skill="Pro"), headers=as_user(player))
    assert response.status_code == 422


async def test_court_filter_returns_that_courts_games(api, committing):
    player, ids, near_court = await build_feed_world(committing)
    response = await api.get(URL, params=feed_params(court_id=str(near_court.id)), headers=as_user(player))
    returned = [g["game_id"] for g in response.json()["games"]]
    assert returned == [ids["mid_near"]]


async def test_wider_radius_includes_far_game(api, committing):
    player, ids, _ = await build_feed_world(committing)
    response = await api.get(URL, params=feed_params(radius_mi=15), headers=as_user(player))
    returned = [g["game_id"] for g in response.json()["games"]]
    assert ids["far"] in returned


async def test_closer_skill_match_outranks_slightly_nearer_game(api, committing):
    async with committing() as s, s.begin():
        player = await f.user(s, rating=3.5)
        even_host = await f.user(s, rating=3.5)
        strong_host = await f.user(s, rating=4.5)
        court_a = await f.court(s, lat=f.offset_north(LAT, LON, 300)[0], lon=LON)
        court_b = await f.court(s, lat=f.offset_north(LAT, LON, 400)[0], lon=LON)
        mismatched = await f.game(s, organizer=strong_host, court_obj=court_a, starts_at=f.at(hours=2), skill=(3.0, 5.0))
        for _ in range(4):
            await f.add_confirmed(s, mismatched, await f.user(s, rating=4.5))
        even = await f.game(s, organizer=even_host, court_obj=court_b, starts_at=f.at(hours=2), skill=(3.0, 5.0))
    body = (await api.get(URL, params=feed_params(), headers=as_user(player))).json()
    returned = [g["game_id"] for g in body["games"]]
    assert returned == [str(even.id), str(mismatched.id)]


async def test_roster_average_leaves_out_the_player_and_pending_requests(db):
    player = await f.user(db, rating=4.0)
    host = await f.user(db, rating=3.0)
    game = await f.game(db, organizer=host, visibility=Visibility.REQUEST_TO_JOIN)
    await f.add_confirmed(db, game, player)
    pending = await f.user(db, rating=5.0)
    db.add(GameParticipant(game_id=game.id, user_id=pending.id, status=ParticipantStatus.PENDING))
    await db.flush()
    averages = await recommendations.roster_average_ratings(db, [game.id], exclude_user_id=player.id)
    assert averages == {game.id: 3.0}


async def test_skill_chip_filter_rejects_unknown_label():
    with pytest.raises(ValueError):
        recommendations.filter_by_skill_chip([], "Pro")


# --- join --------------------------------------------------------------------


async def test_join_open_game_confirms_and_takes_a_spot(api, committing):
    async with committing() as s, s.begin():
        player = await f.user(s, rating=3.5)
        game = await f.game(s, cap=4)
    response = await api.post(f"/api/v1/games/{game.id}/join", headers=as_user(player))
    assert response.status_code == 200
    assert response.json() == {"game_id": str(game.id), "status": "confirmed", "spots_available": 2}

    # Joining again is a no-op.
    again = await api.post(f"/api/v1/games/{game.id}/join", headers=as_user(player))
    assert again.status_code == 200
    assert again.json()["spots_available"] == 2


async def test_join_request_to_join_game_is_pending(api, committing):
    async with committing() as s, s.begin():
        player = await f.user(s)
        game = await f.game(s, visibility=Visibility.REQUEST_TO_JOIN)
    response = await api.post(f"/api/v1/games/{game.id}/join", headers=as_user(player))
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


async def test_join_full_game_is_409(api, committing):
    async with committing() as s, s.begin():
        player = await f.user(s)
        game = await f.game(s, cap=2)
        await f.add_confirmed(s, game, await f.user(s))
    response = await api.post(f"/api/v1/games/{game.id}/join", headers=as_user(player))
    assert response.status_code == 409
    assert response.json()["detail"] == "game is full"


async def test_join_outside_skill_range_is_403(api, committing):
    async with committing() as s, s.begin():
        player = await f.user(s, rating=2.0)
        game = await f.game(s, skill=(4.0, 5.0), organizer=await f.user(s, rating=4.5))
    response = await api.post(f"/api/v1/games/{game.id}/join", headers=as_user(player))
    assert response.status_code == 403


async def test_join_unknown_game_is_404(api, committing):
    async with committing() as s, s.begin():
        player = await f.user(s)
    response = await api.post("/api/v1/games/00000000-0000-0000-0000-000000000000/join", headers=as_user(player))
    assert response.status_code == 404


async def test_only_one_player_gets_the_last_spot(api, committing):
    async with committing() as s, s.begin():
        game = await f.game(s, cap=3)
        await f.add_confirmed(s, game, await f.user(s))  # 1 spot left
        racers = [await f.user(s) for _ in range(8)]

    requests = []
    for racer in racers:
        requests.append(api.post(f"/api/v1/games/{game.id}/join", headers=as_user(racer)))
    responses = await asyncio.gather(*requests)

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] + [409] * 7
    async with committing() as s:
        count = await s.scalar(sa.select(Game.participant_count).where(Game.id == game.id))
    assert count == 3
