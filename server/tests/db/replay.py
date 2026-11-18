"""
Independent from-scratch rating replay, used as the reference result:
rate every finalized game in (ends_at, id) order from each player's base rating.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.crud.ratings import load_game_inputs
from server.app.services import rating_engine
from server.app.services.rating_engine import RatingState


async def from_scratch(session: AsyncSession):
    games = (
        await session.execute(
            sa.text("SELECT id FROM games WHERE stats_finalized_at IS NOT NULL ORDER BY ends_at, id")
        )
    ).scalars().all()
    base = dict((await session.execute(sa.text("SELECT user_id, base_rating FROM player_ratings"))).all())
    current = {uid: RatingState.initial(b) for uid, b in base.items()}
    history: dict[tuple[uuid.UUID, uuid.UUID], tuple[RatingState, RatingState]] = {}
    for gid in games:
        inputs = await load_game_inputs(session, gid)
        new = rating_engine.rate_game({u: current[u] for u in inputs.players}, inputs)
        for u in inputs.players:
            history[(u, gid)] = (current[u], new[u])
        current.update(new)
    return current, history


async def stored(session: AsyncSession):
    ratings = {
        uid: RatingState(rating=r, rd=rd, volatility=v, rated_games=n)
        for uid, r, rd, v, n in (
            await session.execute(sa.text("SELECT user_id, rating, rd, volatility, rated_games FROM player_ratings"))
        ).all()
    }
    history = {
        (uid, gid): (RatingState.from_json(b), RatingState.from_json(a))
        for uid, gid, b, a in (
            await session.execute(sa.text("SELECT user_id, game_id, before, after FROM rating_history"))
        ).all()
    }
    return ratings, history


async def assert_matches_from_scratch(session: AsyncSession) -> None:
    exp_ratings, exp_history = await from_scratch(session)
    got_ratings, got_history = await stored(session)
    assert got_history == exp_history, "rating_history differs from a from-scratch replay"
    assert got_ratings == exp_ratings, "player_ratings differs from a from-scratch replay"
