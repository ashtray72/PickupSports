"""G. Location-based check-in and reliability scores (FR-06, OBJ-04)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from server.app.crud import attendance, games
from server.app.crud.errors import CheckInRejectedError, NotPermittedError
from server.app.models import Attendance, Game, GameParticipant, GameStatus, PlayerProfile
from server.tests.db import factories as f

COURT_LAT, COURT_LON = 40.4258, -86.9270


async def _game_with_player(db):
    court = await f.court(db, lat=COURT_LAT, lon=COURT_LON)
    g = await f.game(db, court_obj=court, starts_at=f.at(days=1))
    p = await f.user(db)
    await games.join_game(db, g.id, p.id)
    return g, p


async def _score(db, user_id) -> Decimal:
    profile = await db.get(PlayerProfile, user_id, populate_existing=True)
    return profile.reliability_score


async def test_check_in_inside_geofence_succeeds(db):
    g, p = await _game_with_player(db)
    lat, lon = f.offset_north(COURT_LAT, COURT_LON, 40)
    part = await attendance.check_in(db, g.id, p.id, lat, lon, at=g.starts_at)
    assert part.attendance == Attendance.ATTENDED and part.checked_in_at == g.starts_at
    assert await _score(db, p.id) == Decimal("1.000")
    # Checking in twice is harmless.
    await attendance.check_in(db, g.id, p.id, lat, lon, at=g.starts_at + timedelta(minutes=1))
    assert sum((await attendance.reliability_counts(db, p.id)).values()) == 1


async def test_check_in_at_60m_rejected(db):
    g, p = await _game_with_player(db)
    lat, lon = f.offset_north(COURT_LAT, COURT_LON, 60)
    with pytest.raises(CheckInRejectedError, match="within 50 m"):
        await attendance.check_in(db, g.id, p.id, lat, lon, at=g.starts_at)


@pytest.mark.parametrize("minutes", [-31, 16])
async def test_check_in_outside_time_window_rejected(db, minutes):
    g, p = await _game_with_player(db)
    with pytest.raises(CheckInRejectedError, match="window"):
        await attendance.check_in(db, g.id, p.id, COURT_LAT, COURT_LON, at=g.starts_at + timedelta(minutes=minutes))


@pytest.mark.parametrize("minutes", [-30, 15])
async def test_check_in_at_window_edges_allowed(db, minutes):
    g, p = await _game_with_player(db)
    await attendance.check_in(db, g.id, p.id, COURT_LAT, COURT_LON, at=g.starts_at + timedelta(minutes=minutes))


async def test_non_participant_cannot_check_in(db):
    g, _p = await _game_with_player(db)
    stranger = await f.user(db)
    with pytest.raises(CheckInRejectedError, match="participants"):
        await attendance.check_in(db, g.id, stranger.id, COURT_LAT, COURT_LON, at=g.starts_at)


async def test_close_attendance_marks_no_shows(db):
    g, present = await _game_with_player(db)
    absent = await f.user(db)
    await games.join_game(db, g.id, absent.id)
    await attendance.check_in(db, g.id, present.id, COURT_LAT, COURT_LON, at=g.starts_at)
    await attendance.check_in(db, g.id, g.organizer_id, COURT_LAT, COURT_LON, at=g.starts_at)

    with pytest.raises(NotPermittedError):
        await attendance.close_attendance(db, g.id, at=g.starts_at + timedelta(minutes=5))
    no_shows = await attendance.close_attendance(db, g.id, at=g.starts_at + timedelta(minutes=20))
    assert no_shows == [absent.id]
    absent_row = await db.get(GameParticipant, (g.id, absent.id), populate_existing=True)
    assert absent_row.attendance == Attendance.NO_SHOW
    assert await _score(db, absent.id) == Decimal("0.000")
    assert await _score(db, present.id) == Decimal("1.000")
    assert (await db.get(Game, g.id, populate_existing=True)).status == GameStatus.COMPLETED


async def test_reliability_score_moves_with_history(db):
    p = await f.user(db)
    court = await f.court(db, lat=COURT_LAT, lon=COURT_LON)
    # Two attended games, one no-show, one late cancellation -> 2 / 4.
    for i in range(3):
        g = await f.game(db, court_obj=court, starts_at=f.at(days=-10 + i))
        await games.join_game(db, g.id, p.id)
        if i < 2:
            await attendance.check_in(db, g.id, p.id, COURT_LAT, COURT_LON, at=g.starts_at)
        await attendance.close_attendance(db, g.id, at=g.starts_at + timedelta(hours=1))
    assert await _score(db, p.id) == Decimal("0.667")
    soon = await f.game(db, court_obj=court, starts_at=f.at(hours=1))
    await games.join_game(db, soon.id, p.id)
    await games.leave_game(db, soon.id, p.id)
    assert await _score(db, p.id) == Decimal("0.500")
