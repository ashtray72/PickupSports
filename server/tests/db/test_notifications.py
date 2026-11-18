"""F. Live signals over LISTEN/NOTIFY (NFR-07)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

import pytest

from server.app.crud import games, recordings, stats
from server.app.db.notifications import GameEventListener
from server.tests.db import factories as f
from server.tests.db.conftest import TEST_DSN

LATENCY_LIMIT_S = 2.0


async def wait_for(listener: GameEventListener, game_id, kind, timeout=LATENCY_LIMIT_S, **match):
    """Next event for this game and kind (skipping unrelated events) or TimeoutError."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"no {kind} event for {game_id} within {timeout}s")
        event = await listener.get(timeout=remaining)
        if event.get("game_id") == str(game_id) and event.get("kind") == kind and all(
            event.get(k) == v for k, v in match.items()
        ):
            return event


async def _setup(committing, cap=10):
    async with committing() as s, s.begin():
        org = await f.user(s)
        g = await f.game(s, organizer=org, cap=cap)
        players = [await f.user(s) for _ in range(3)]
    return org, g, players


async def test_join_leave_and_edit_are_pushed_within_2s(committing):
    org, g, (p1, _p2, _p3) = await _setup(committing)
    async with GameEventListener(TEST_DSN) as listener:
        async with committing() as s, s.begin():
            await games.join_game(s, g.id, p1.id)
        t0 = time.monotonic()
        event = await wait_for(listener, g.id, "roster_changed", user_id=str(p1.id))
        assert time.monotonic() - t0 < LATENCY_LIMIT_S
        assert event["status"] == "confirmed" and event["participant_count"] == 2 and event["player_cap"] == 10

        async with committing() as s, s.begin():
            await games.leave_game(s, g.id, p1.id)
        event = await wait_for(listener, g.id, "roster_changed", user_id=str(p1.id), status="left")
        assert event["participant_count"] == 1

        new_start = g.starts_at + timedelta(hours=1)
        async with committing() as s, s.begin():
            await games.update_game(s, g.id, org.id, starts_at=new_start, ends_at=new_start + timedelta(hours=1))
        event = await wait_for(listener, g.id, "game_updated")
        assert datetime.fromisoformat(event["starts_at"]) == new_start


async def test_ten_listeners_all_receive_the_update(committing):
    _org, g, (p1, _p2, _p3) = await _setup(committing)
    listeners = [GameEventListener(TEST_DSN) for _ in range(10)]
    for listener in listeners:
        await listener.__aenter__()
    try:
        async with committing() as s, s.begin():
            await games.join_game(s, g.id, p1.id)
        events = await asyncio.gather(
            *(wait_for(listener, g.id, "roster_changed", user_id=str(p1.id)) for listener in listeners)
        )
        assert all(e["participant_count"] == 2 for e in events)
    finally:
        for listener in listeners:
            await listener.__aexit__(None, None, None)


async def test_rolled_back_join_sends_nothing(committing):
    _org, g, (p1, _p2, _p3) = await _setup(committing)
    async with GameEventListener(TEST_DSN) as listener:
        async with committing() as s:
            await s.begin()
            await games.join_game(s, g.id, p1.id)
            await s.rollback()
        with pytest.raises(asyncio.TimeoutError):
            await wait_for(listener, g.id, "roster_changed", timeout=0.75)


async def test_stats_ready_and_processing_failed_events(committing):
    async with committing() as s, s.begin():
        a, b = await f.user(s), await f.user(s)
        rg = await f.two_team_game(s, [a], [b], a_points=2, b_points=1, starts_at=f.at(days=-1), finalize=False)
    async with GameEventListener(TEST_DSN) as listener:
        async with committing() as s, s.begin():
            await stats.finalize_game_stats(s, rg.game.id)
        await wait_for(listener, rg.game.id, "stats_ready")

        async with committing() as s, s.begin():
            await stats.tag_track(s, rg.tracks[a.id], a.id)  # already tagged: no change, no event
            await stats.untag_track(s, rg.tracks[a.id], a.id)
        await wait_for(listener, rg.game.id, "stats_updated")

    # A second recorded game whose processing fails 3 times notifies the organizer.
    async with committing() as s, s.begin():
        c, d = await f.user(s), await f.user(s)
        other = await f.two_team_game(s, [c], [d], a_points=1, b_points=0, starts_at=f.at(days=-1), finalize=False)
    async with GameEventListener(TEST_DSN) as listener:
        for _ in range(3):
            async with committing() as s, s.begin():
                await recordings.record_job_failure(s, other.job.id, "GPU worker crashed")
        event = await wait_for(listener, other.game.id, "processing_failed")
        assert event["attempts"] == 3
