"""
J. Ratings stay consistent with stats (FR-10, FR-12): same transaction,
all-or-nothing, replay == from scratch, spread to other players, early stop,
locking (reach set, calc-vs-lock race, no deadlocks) and the 14-day tag lock.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import sqlalchemy as sa

from server.app.crud import ratings, stats
from server.app.crud.errors import TagsLockedError
from server.app.models import PlayerRating, RatingHistory
from server.app.services import rating_engine
from server.app.services.rating_engine import RatingState
from server.tests.db import factories as f
from server.tests.db.factories import Shot
from server.tests.db.replay import assert_matches_from_scratch


def day(n: int):
    """Game start times on distinct, ordered days in the past."""
    return f.at(days=-30 + n)


async def _ratings(s, *users) -> dict:
    rows = (
        await s.execute(
            sa.select(PlayerRating.user_id, PlayerRating.rating, PlayerRating.rated_games).where(
                PlayerRating.user_id.in_([u.id for u in users])
            )
        )
    ).all()
    return {uid: (r, n) for uid, r, n in rows}


async def _history(s, game_id) -> dict:
    rows = (
        await s.execute(
            sa.select(RatingHistory.user_id, RatingHistory.before, RatingHistory.after, RatingHistory.rated_at).where(
                RatingHistory.game_id == game_id
            )
        )
    ).all()
    return {uid: (b, a, t) for uid, b, a, t in rows}


async def _box_users(s, game_id) -> set:
    return {line.user_id for line in await stats.box_scores(s, game_id)}


def _flagged_ids(rg, n):
    return rg.shot_ids[-n:]


# --------------------------------------------------------------------------- #
# Same transaction / all-or-nothing
# --------------------------------------------------------------------------- #

async def test_tag_changes_stats_and_ratings_atomically(committing):
    async with committing() as s, s.begin():
        a, b, b2 = await f.user(s), await f.user(s), await f.user(s)
        rg = await f.recorded_game(
            s, [(a, "A"), (b, "B")], [Shot(a, True), Shot(b, True), Shot(5, True), Shot(5, True)],
            extra_tracks=[(5, "B")], roster_only=[b2], starts_at=day(1),
        )
    async with committing() as reader:
        before_stats = await _box_users(reader, rg.game.id)
        before_ratings = await _ratings(reader, a, b, b2)
    assert b2.id not in before_stats and before_ratings[b2.id][1] == 0

    async with committing() as writer:
        await writer.begin()
        await stats.tag_track(writer, rg.tracks[5], b2.id)
        # Not committed yet: another connection sees the old stats AND the old ratings.
        async with committing() as reader:
            assert await _box_users(reader, rg.game.id) == before_stats
            assert await _ratings(reader, a, b, b2) == before_ratings
        await writer.commit()

    async with committing() as reader:
        assert b2.id in await _box_users(reader, rg.game.id)
        after = await _ratings(reader, a, b, b2)
        assert after[b2.id][1] == 1 and after != before_ratings
        await assert_matches_from_scratch(reader)


@pytest.mark.parametrize("change", ["tag", "review", "opt_out"])
async def test_rating_failure_rolls_back_the_whole_change(db, monkeypatch, change):
    a, b, b2 = await f.user(db), await f.user(db), await f.user(db)
    rg = await f.recorded_game(
        db, [(a, "A"), (b, "B")], [Shot(a, True), Shot(b, True, confidence=0.3), Shot(5, True)],
        extra_tracks=[(5, "B")], roster_only=[b2], starts_at=day(1),
    )
    stats_before = await stats.box_scores(db, rg.game.id)
    ratings_before = await _ratings(db, a, b, b2)

    def boom(states, game):
        raise RuntimeError("rating engine failed")

    monkeypatch.setattr(rating_engine, "rate_game", boom)
    with pytest.raises(RuntimeError, match="rating engine failed"):
        async with db.begin_nested():
            if change == "tag":
                await stats.tag_track(db, rg.tracks[5], b2.id)
            elif change == "review":
                await stats.review_event(db, _flagged_ids(rg, 2)[0], a.id, confirm=True)
            else:
                await stats.set_tracking_opt_out(db, rg.game.id, a.id, True)
    monkeypatch.undo()

    assert await stats.box_scores(db, rg.game.id) == stats_before
    assert await _ratings(db, a, b, b2) == ratings_before
    tagged = await db.scalar(sa.text("SELECT tagged_user_id FROM detected_tracks WHERE id = :t"), {"t": rg.tracks[5]})
    assert tagged is None


# --------------------------------------------------------------------------- #
# Replay correctness and spread
# --------------------------------------------------------------------------- #

async def test_retag_in_old_game_equals_from_scratch_replay(db):
    a1, a2, b = await f.user(db), await f.user(db), await f.user(db)
    games_ = []
    for n, (pa, pb) in enumerate([(3, 1), (1, 2), (2, 2)], start=1):
        games_.append(
            await f.recorded_game(
                db,
                [(a1, "A"), (a2, "A"), (b, "B")],
                [Shot(a1, True) for _ in range(pa)] + [Shot(b, True) for _ in range(pb)] + [Shot(7, True, confidence=0.65)],
                extra_tracks=[(7, "A")],
                starts_at=day(n),
            )
        )
    await assert_matches_from_scratch(db)
    before = await _ratings(db, a1, a2, b)

    # In G1, a2 realizes the low-confidence track 7 is them, not their current track.
    await stats.tag_track(db, games_[0].tracks[7], a2.id)
    after = await _ratings(db, a1, a2, b)
    assert after != before
    await assert_matches_from_scratch(db)


async def _chain(db):
    """G0: E vs X0.  G1: A vs B.  G2: B vs C.  G3: C vs D (in time order)."""
    e, x0, a, b, c, d = [await f.user(db) for _ in range(6)]
    g0 = await f.two_team_game(db, [e], [x0], a_points=2, b_points=1, starts_at=day(0))
    g1 = await f.two_team_game(db, [a], [b], a_points=1, b_points=0, flagged_for_b=2, starts_at=day(1))
    g2 = await f.two_team_game(db, [b], [c], a_points=2, b_points=1, starts_at=day(2))
    g3 = await f.two_team_game(db, [c], [d], a_points=1, b_points=3, starts_at=day(3))
    return (e, x0, a, b, c, d), (g0, g1, g2, g3)


async def test_change_spreads_through_later_games(db):
    (e, x0, a, b, c, d), (g0, g1, g2, g3) = await _chain(db)
    before = await _ratings(db, e, x0, a, b, c, d)
    g0_rows = await _history(db, g0.game.id)

    # Confirming one flagged basket for B turns G1 from an A win into a tie.
    await stats.review_event(db, _flagged_ids(g1, 2)[0], b.id, confirm=True)

    after = await _ratings(db, e, x0, a, b, c, d)
    for u in (a, b, c, d):
        assert after[u.id][0] != before[u.id][0], "change should reach every later opponent"
    for u in (e, x0):
        assert after[u.id] == before[u.id]
    assert await _history(db, g0.game.id) == g0_rows  # including rated_at: never rewritten
    await assert_matches_from_scratch(db)


async def test_early_stop_when_ratings_stop_changing(db, monkeypatch):
    def ignores_stats(states, game):
        return {
            u: RatingState(s.rating + 1.0, s.rd, s.volatility, s.rated_games + 1)
            for u, s in sorted(states.items())
        }

    monkeypatch.setattr(rating_engine, "rate_game", ignores_stats)
    (_e, _x0, a, b, _c, _d), (_g0, g1, g2, g3) = await _chain(db)
    g2_rows, g3_rows = await _history(db, g2.game.id), await _history(db, g3.game.id)

    captured = {}

    async def capture(result):
        captured["result"] = result

    ratings._hooks["after_lock"] = capture
    await stats.review_event(db, _flagged_ids(g1, 2)[0], b.id, confirm=True)

    result = captured["result"]
    assert result.recomputed_games == [g1.game.id]
    assert result.skipped_games == [g2.game.id, g3.game.id]
    assert await _history(db, g2.game.id) == g2_rows
    assert await _history(db, g3.game.id) == g3_rows
    await assert_matches_from_scratch(db)


# --------------------------------------------------------------------------- #
# Locking: reach set, calc-vs-lock race, no deadlocks
# --------------------------------------------------------------------------- #

async def _committed_chain(committing):
    async with committing() as s, s.begin():
        a, b, c, e, x = [await f.user(s) for _ in range(5)]
        g1 = await f.two_team_game(s, [a], [b], a_points=1, b_points=0, flagged_for_b=2, starts_at=day(1))
        g2 = await f.two_team_game(s, [b], [c], a_points=2, b_points=1, flagged_for_b=2, starts_at=day(2))
        g9 = await f.two_team_game(s, [e], [x], a_points=1, b_points=0, starts_at=day(3))
    return (a, b, c, e, x), (g1, g2, g9)


async def _locked_player_ids(committing) -> set:
    async with committing() as probe:
        await probe.begin()
        everyone = set((await probe.execute(sa.text("SELECT user_id FROM player_ratings"))).scalars())
        free = set(
            (await probe.execute(sa.text("SELECT user_id FROM player_ratings FOR UPDATE SKIP LOCKED"))).scalars()
        )
        await probe.rollback()
    return everyone - free


async def test_recompute_locks_exactly_the_reach_set(committing):
    (a, b, c, e, x), (g1, _g2, _g9) = await _committed_chain(committing)
    paused, release = asyncio.Event(), asyncio.Event()
    captured = {}

    async def pause(result):
        captured["result"] = result
        paused.set()
        await release.wait()

    ratings._hooks["after_lock"] = pause

    async def change():
        async with committing() as s, s.begin():
            await stats.review_event(s, _flagged_ids(g1, 2)[0], b.id, confirm=True)

    task = asyncio.create_task(change())
    await asyncio.wait_for(paused.wait(), 10)
    try:
        locked = await _locked_player_ids(committing)
        assert captured["result"].reach == {a.id, b.id, c.id}
        assert locked == {a.id, b.id, c.id}  # not e or x, who never played them
    finally:
        release.set()
        await asyncio.wait_for(task, 10)


async def test_game_finalized_between_reach_and_lock_is_included(committing):
    async with committing() as s, s.begin():
        a, b = await f.user(s), await f.user(s)
        g1 = await f.two_team_game(s, [a], [b], a_points=1, b_points=0, flagged_for_b=2, starts_at=day(1))
    paused, release = asyncio.Event(), asyncio.Event()
    captured = {}

    async def pause_once(result):
        if paused.is_set():
            return
        captured["result"] = result
        paused.set()
        await release.wait()

    ratings._hooks["after_first_reach"] = pause_once

    async def change():
        async with committing() as s, s.begin():
            await stats.review_event(s, _flagged_ids(g1, 2)[0], b.id, confirm=True)

    task = asyncio.create_task(change())
    await asyncio.wait_for(paused.wait(), 10)
    assert captured["result"].reach == {a.id, b.id}
    try:
        # Meanwhile a new game between B and a new player F is finalized and committed.
        async with committing() as s, s.begin():
            new_f = await f.user(s)
            await f.two_team_game(s, [b], [new_f], a_points=2, b_points=1, starts_at=day(2))
    finally:
        release.set()
    await asyncio.wait_for(task, 10)

    result = captured["result"]
    assert result.lock_rounds == 2
    assert new_f.id in result.reach and new_f.id in result.locked
    async with committing() as s:
        await assert_matches_from_scratch(s)


async def test_overlapping_recomputes_do_not_deadlock(committing):
    (_a, b, c, _e, _x), (g1, g2, _g9) = await _committed_chain(committing)

    async def confirm(rg, reviewer):
        async with committing() as s, s.begin():
            await stats.review_event(s, _flagged_ids(rg, 2)[0], reviewer.id, confirm=True)

    await asyncio.wait_for(asyncio.gather(confirm(g1, b), confirm(g2, c)), timeout=30)
    async with committing() as s:
        await assert_matches_from_scratch(s)


# --------------------------------------------------------------------------- #
# 14-day tag lock, opt-out, idempotency, provisional
# --------------------------------------------------------------------------- #

async def test_tags_lock_after_14_days(db):
    a, b, b2 = await f.user(db), await f.user(db), await f.user(db)
    rg = await f.recorded_game(
        db, [(a, "A"), (b, "B")], [Shot(a, True), Shot(b, True, confidence=0.3), Shot(5, True)],
        extra_tracks=[(5, "B")], roster_only=[b2], starts_at=day(1),
    )
    await f.set_tags_locked(db, rg.game.id, f.at(hours=-1))
    before = await stats.box_scores(db, rg.game.id)
    with pytest.raises(TagsLockedError):
        await stats.tag_track(db, rg.tracks[5], b2.id)
    with pytest.raises(TagsLockedError):
        await stats.untag_track(db, rg.tracks[a.id], a.id)
    with pytest.raises(TagsLockedError):
        await stats.review_event(db, rg.shot_ids[1], b.id, confirm=True)
    assert await stats.box_scores(db, rg.game.id) == before


async def test_finalize_sets_tag_lock_14_days_later(db):
    a, b = await f.user(db), await f.user(db)
    rg = await f.two_team_game(db, [a], [b], a_points=1, b_points=0, starts_at=day(1), finalize=False)
    finished = f.at()
    await stats.finalize_game_stats(db, rg.game.id, now=finished)
    await db.refresh(rg.game)
    assert rg.game.tags_locked_at == finished + timedelta(days=14)
    with pytest.raises(TagsLockedError):
        await stats.untag_track(db, rg.tracks[a.id], a.id, now=finished + timedelta(days=14))


async def test_opt_out_still_works_after_the_lock(db):
    a, b = await f.user(db), await f.user(db)
    rg = await f.two_team_game(db, [a], [b], a_points=2, b_points=1, starts_at=day(1))
    assert (await _ratings(db, a))[a.id][1] == 1
    await f.set_tags_locked(db, rg.game.id, f.at(days=-30))

    await stats.set_tracking_opt_out(db, rg.game.id, a.id, True)
    assert a.id not in await _box_users(db, rg.game.id)
    assert a.id not in await _history(db, rg.game.id)
    assert (await _ratings(db, a))[a.id][1] == 0
    await assert_matches_from_scratch(db)
    with pytest.raises(TagsLockedError):
        await stats.set_tracking_opt_out(db, rg.game.id, a.id, False)  # opting back in follows the lock


async def test_no_double_counting(db):
    a, b = await f.user(db), await f.user(db)
    rg = await f.two_team_game(db, [a], [b], a_points=2, b_points=1, starts_at=day(1))
    once = await _ratings(db, a, b)
    await stats.finalize_game_stats(db, rg.game.id)
    await ratings.recompute_from_game(db, rg.game.id)
    await ratings.recompute_from_game(db, rg.game.id)
    assert await _ratings(db, a, b) == once
    assert once[a.id][1] == 1
    rows = await db.scalar(
        sa.select(sa.func.count()).select_from(RatingHistory).where(RatingHistory.game_id == rg.game.id)
    )
    assert rows == 2


async def test_tagging_before_finalization_writes_no_ratings(db):
    a, b = await f.user(db), await f.user(db)
    rg = await f.two_team_game(db, [a], [b], a_points=2, b_points=1, starts_at=day(1), finalize=False)
    assert rg.game.stats_finalized_at is None
    await stats.untag_track(db, rg.tracks[a.id], a.id)
    await stats.tag_track(db, rg.tracks[a.id], a.id)
    assert await db.scalar(sa.select(sa.func.count()).select_from(RatingHistory)) == 0
    assert (await _ratings(db, a))[a.id][1] == 0


async def test_provisional_until_three_rated_games(db):
    a, b = await f.user(db), await f.user(db)
    for n in range(1, 4):
        await f.two_team_game(db, [a], [b], a_points=n, b_points=1, starts_at=day(n))
        row = await db.get(PlayerRating, a.id, populate_existing=True)
        assert row.rated_games == n
        assert row.is_provisional == (n < 3)
