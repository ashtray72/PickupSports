"""
E. Roster consistency under concurrent joins (NFR-02, OBJ-07).

Each join runs in its own session/connection and commits for real.
"""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from server.app.crud import games
from server.app.crud.errors import GameFullError
from server.app.models import Game, GameParticipant, ParticipantStatus
from server.tests.db import factories as f

JOINERS = 50
CAP = 10
REPEATS = 20


async def _setup(committing, n_users: int):
    async with committing() as s, s.begin():
        org = await f.user(s)
        court = await f.court(s)
        users = [await f.user(s) for _ in range(n_users)]
    return org, court, users


async def _new_game(committing, org, court, cap=CAP):
    async with committing() as s, s.begin():
        g = await f.game(s, organizer=org, court_obj=court, cap=cap)
    return g.id


async def _join(committing, game_id, user_id):
    try:
        async with committing() as s, s.begin():
            return await games.join_game(s, game_id, user_id)
    except GameFullError:
        return "full"


async def _roster_counts(committing, game_id):
    async with committing() as s:
        confirmed = await s.scalar(
            sa.select(sa.func.count()).select_from(GameParticipant).where(
                GameParticipant.game_id == game_id, GameParticipant.status == ParticipantStatus.CONFIRMED
            )
        )
        rows = await s.scalar(
            sa.select(sa.func.count()).select_from(GameParticipant).where(GameParticipant.game_id == game_id)
        )
        distinct = await s.scalar(
            sa.select(sa.func.count(sa.distinct(GameParticipant.user_id))).where(GameParticipant.game_id == game_id)
        )
        count_col = await s.scalar(sa.select(Game.participant_count).where(Game.id == game_id))
    return confirmed, rows, distinct, count_col


@pytest.mark.slow
async def test_50_simultaneous_joins_never_exceed_cap_20_runs(committing):
    org, court, users = await _setup(committing, JOINERS)
    for run in range(REPEATS):
        game_id = await _new_game(committing, org, court)
        results = await asyncio.gather(*(_join(committing, game_id, u.id) for u in users))
        joined = [r for r in results if r == ParticipantStatus.CONFIRMED]
        full = [r for r in results if r == "full"]
        # The organizer holds one of the 10 spots.
        assert len(joined) == CAP - 1, f"run {run}"
        assert len(full) == JOINERS - (CAP - 1), f"run {run}"
        confirmed, rows, distinct, count_col = await _roster_counts(committing, game_id)
        assert confirmed == CAP and count_col == CAP, f"run {run}"
        assert rows == distinct == CAP, f"run {run}: duplicate participants"


async def test_same_user_joining_concurrently_gets_one_row(committing):
    org, court, users = await _setup(committing, 1)
    game_id = await _new_game(committing, org, court)
    results = await asyncio.gather(*(_join(committing, game_id, users[0].id) for _ in range(5)))
    assert set(results) == {ParticipantStatus.CONFIRMED}
    confirmed, rows, distinct, count_col = await _roster_counts(committing, game_id)
    assert (confirmed, rows, distinct, count_col) == (2, 2, 2, 2)


async def _raw_insert(committing, game_id, user_id):
    try:
        async with committing() as s, s.begin():
            await s.execute(
                sa.text("INSERT INTO game_participants (game_id, user_id, status) VALUES (:g, :u, 'confirmed')"),
                {"g": game_id, "u": user_id},
            )
        return "ok"
    except sa.exc.DBAPIError as exc:
        assert "participant_count_cap" in str(exc), exc
        return "rejected"


@pytest.mark.slow
async def test_cap_holds_for_raw_sql_without_locking(committing):
    """No SELECT ... FOR UPDATE here: the trigger + CHECK alone must hold the cap."""
    org, court, users = await _setup(committing, JOINERS)
    for run in range(5):
        game_id = await _new_game(committing, org, court)
        results = await asyncio.gather(*(_raw_insert(committing, game_id, u.id) for u in users))
        assert results.count("ok") == CAP - 1, f"run {run}"
        confirmed, rows, distinct, count_col = await _roster_counts(committing, game_id)
        assert confirmed == count_col == rows == CAP, f"run {run}"
