"""
Rating storage and the replay that keeps ratings consistent with stats
(FR-10 + FR-12).

Box scores are a view, so they are always current. Ratings are computed in
Python by `rating_engine.rate_game`, so every write that changes a finalized
game's stats calls `recompute_from_game` *in the same transaction*. This module
never commits; the caller's transaction does.

Reach: a formula may use teammates' and opponents' ratings, so a change in game
G reaches everyone rated in G, then everyone in later games with those players,
and so on (transitive closure over `rating_history`, ordered by
(game_ends_at, game_id)).

Locking: the whole reach set's `player_ratings` rows are locked in user_id order
(deadlock-safe). A game finalized between "calculate reach" and "lock" could add
players we did not lock, so after locking the reach is recalculated; if it grew,
the savepoint holding the locks is rolled back and the larger set is locked
again, in order, until the set is stable.

Replay: every player starts from their state before G; window games are rated
in order, whole games at a time. A later game whose inputs are unchanged is
skipped (early stop). The result equals rating every game from scratch.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Awaitable, Callable, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.core.config import get_settings
from server.app.crud.errors import NotFoundError
from server.app.models import Game, PlayerRating, RatingHistory
from server.app.services import rating_engine
from server.app.services.rating_engine import GameInputs, PlayerGameInput, RatingState

log = logging.getLogger(__name__)

@dataclass
class RecomputeResult:
    reach: set[uuid.UUID] = field(default_factory=set)
    locked: set[uuid.UUID] = field(default_factory=set)
    recomputed_games: list[uuid.UUID] = field(default_factory=list)
    skipped_games: list[uuid.UUID] = field(default_factory=list)
    lock_rounds: int = 0
    elapsed_ms: float = 0.0


GameKey = tuple[datetime, uuid.UUID]

# Test-only pause points: "after_first_reach", "after_lock". Each hook receives
# the in-progress RecomputeResult.
_hooks: dict[str, Callable[[RecomputeResult], Awaitable[None]]] = {}


async def _run_hook(name: str, result: RecomputeResult) -> None:
    hook = _hooks.get(name)
    if hook is not None:
        await hook(result)

_KEY_GT = "(rh.game_ends_at, rh.game_id) > (CAST(:ends AS timestamptz), CAST(:gid AS uuid))"
_KEY_LT = "(rh.game_ends_at, rh.game_id) < (CAST(:ends AS timestamptz), CAST(:gid AS uuid))"


async def load_game_inputs(session: AsyncSession, game_id: uuid.UUID) -> GameInputs:
    """A game's rating inputs, read from the box-score and team-score views."""
    players = {}
    rows = await session.execute(
        sa.text(
            "SELECT user_id, team, points, fga, fgm, mean_confidence "
            "FROM player_box_scores WHERE game_id = :gid"
        ),
        {"gid": game_id},
    )
    for r in rows.all():
        players[r.user_id] = PlayerGameInput(
            user_id=r.user_id,
            team=r.team,
            points=int(r.points),
            fga=int(r.fga),
            fgm=int(r.fgm),
            mean_confidence=None if r.mean_confidence is None else float(r.mean_confidence),
        )
    scores = await session.execute(
        sa.text("SELECT team, points FROM game_team_scores WHERE game_id = :gid"), {"gid": game_id}
    )
    return GameInputs(game_id=game_id, team_scores={t: int(p) for t, p in scores.all()}, players=players)


async def _start_players(session: AsyncSession, game_id: uuid.UUID) -> set[uuid.UUID]:
    """Everyone rated in G now (per the view) or before this change (per history)."""
    rows = await session.execute(
        sa.text(
            "SELECT user_id FROM player_box_scores WHERE game_id = :gid "
            "UNION SELECT user_id FROM rating_history WHERE game_id = :gid"
        ),
        {"gid": game_id},
    )
    return {r[0] for r in rows.all()}


async def _reach(session: AsyncSession, start: set[uuid.UUID], key: GameKey) -> set[uuid.UUID]:
    if not start:
        return set()
    rows = await session.execute(
        sa.text(
            f"""
            WITH RECURSIVE reach(user_id) AS (
                SELECT unnest(CAST(:start AS uuid[]))
              UNION
                SELECT rh2.user_id
                FROM reach r
                JOIN rating_history rh ON rh.user_id = r.user_id AND {_KEY_GT}
                JOIN rating_history rh2 ON rh2.game_id = rh.game_id
            )
            SELECT user_id FROM reach
            """
        ),
        {"start": sorted(start), "ends": key[0], "gid": key[1]},
    )
    return {r[0] for r in rows.all()}


async def _lock_players(session: AsyncSession, user_ids: set[uuid.UUID]) -> None:
    if not user_ids:
        return
    await session.execute(
        sa.text("SELECT user_id FROM player_ratings WHERE user_id = ANY(:ids) ORDER BY user_id FOR UPDATE"),
        {"ids": sorted(user_ids)},
    )


async def _later_window_games(
    session: AsyncSession, reach: set[uuid.UUID], key: GameKey
) -> list[uuid.UUID]:
    if not reach:
        return []
    rows = await session.execute(
        sa.text(
            f"""
            SELECT DISTINCT rh.game_id, rh.game_ends_at FROM rating_history rh
            WHERE rh.user_id = ANY(:ids) AND {_KEY_GT}
            ORDER BY rh.game_ends_at, rh.game_id
            """
        ),
        {"ids": sorted(reach), "ends": key[0], "gid": key[1]},
    )
    return [r[0] for r in rows.all()]


async def _states_before(
    session: AsyncSession, user_ids: set[uuid.UUID], key: GameKey
) -> dict[uuid.UUID, RatingState]:
    """Each player's rating state just before game G (from history, else their base rating)."""
    if not user_ids:
        return {}
    ids = sorted(user_ids)
    base_rows = await session.execute(
        sa.select(PlayerRating.user_id, PlayerRating.base_rating).where(PlayerRating.user_id.in_(ids))
    )
    states = {uid: RatingState.initial(base) for uid, base in base_rows.all()}
    prev_rows = await session.execute(
        sa.text(
            f"""
            SELECT DISTINCT ON (rh.user_id) rh.user_id, rh.after FROM rating_history rh
            WHERE rh.user_id = ANY(:ids) AND {_KEY_LT}
            ORDER BY rh.user_id, rh.game_ends_at DESC, rh.game_id DESC
            """
        ),
        {"ids": ids, "ends": key[0], "gid": key[1]},
    )
    for uid, after in prev_rows.all():
        states[uid] = RatingState.from_json(after)
    return states


async def _history_rows(
    session: AsyncSession, game_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[uuid.UUID, tuple[dict, dict]]]:
    out: dict[uuid.UUID, dict[uuid.UUID, tuple[dict, dict]]] = {}
    if not game_ids:
        return out
    rows = await session.execute(
        sa.select(RatingHistory.game_id, RatingHistory.user_id, RatingHistory.before, RatingHistory.after).where(
            RatingHistory.game_id.in_(game_ids)
        )
    )
    for gid, uid, before, after in rows.all():
        out.setdefault(gid, {})[uid] = (before, after)
    return out


async def _game_ends_at(session: AsyncSession, game_id: uuid.UUID) -> datetime:
    ends = await session.scalar(sa.select(Game.ends_at).where(Game.id == game_id))
    assert ends is not None
    return ends


async def recompute_from_game(session: AsyncSession, game_id: uuid.UUID) -> RecomputeResult:
    """Recompute ratings for game G and everything it can reach. Caller owns the transaction."""
    t0 = time.perf_counter()
    result = RecomputeResult()
    await session.flush()
    row = (
        await session.execute(sa.select(Game.ends_at, Game.stats_finalized_at).where(Game.id == game_id))
    ).one_or_none()
    if row is None:
        raise NotFoundError("game not found")
    ends_at, finalized_at = row
    if finalized_at is None:
        return result  # ratings start when stats are finalized
    key: GameKey = (ends_at, game_id)

    # 1. Reach, then lock it; recheck after locking until stable.
    reach = await _reach(session, await _start_players(session, game_id), key)
    result.reach = set(reach)
    await _run_hook("after_first_reach", result)
    locked = set(reach)
    while True:
        result.lock_rounds += 1
        savepoint = await session.begin_nested()
        await _lock_players(session, locked)
        reach = await _reach(session, await _start_players(session, game_id), key)
        if reach <= locked:
            await savepoint.commit()  # RELEASE SAVEPOINT keeps the row locks
            break
        await savepoint.rollback()  # releases the locks so we can re-lock in order
        locked |= reach
    result.reach, result.locked = reach, locked
    await _run_hook("after_lock", result)

    # 2. Replay the window in order.
    window = [game_id] + await _later_window_games(session, reach, key)
    current = await _states_before(session, reach, key)
    history = await _history_rows(session, window)

    for idx, gid in enumerate(window):
        inputs = await load_game_inputs(session, gid)
        rows = history.get(gid, {})
        participants = set(inputs.players)
        unknown = participants - current.keys()
        if unknown:
            raise RuntimeError(f"players {sorted(unknown)} in game {gid} are outside the locked reach set")

        if idx > 0 and set(rows) == participants and all(
            RatingState.from_json(rows[u][0]) == current[u] for u in participants
        ):
            for u in participants:
                current[u] = RatingState.from_json(rows[u][1])
            result.skipped_games.append(gid)
            continue

        new_states = rating_engine.rate_game({u: current[u] for u in participants}, inputs)
        removed = set(rows) - participants
        if removed:
            await session.execute(
                sa.delete(RatingHistory).where(RatingHistory.game_id == gid, RatingHistory.user_id.in_(removed))
            )
        game_ends = ends_at if gid == game_id else await _game_ends_at(session, gid)
        for u in sorted(participants):
            stmt = pg_insert(RatingHistory).values(
                user_id=u,
                game_id=gid,
                game_ends_at=game_ends,
                before=current[u].to_json(),
                after=new_states[u].to_json(),
                # clock_timestamp(), not now(): shows exactly which rows a recompute rewrote,
                # even inside one transaction.
                rated_at=sa.func.clock_timestamp(),
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[RatingHistory.user_id, RatingHistory.game_id],
                    set_={
                        "before": stmt.excluded.before,
                        "after": stmt.excluded.after,
                        "game_ends_at": stmt.excluded.game_ends_at,
                        "rated_at": stmt.excluded.rated_at,
                    },
                )
            )
        current.update(new_states)
        result.recomputed_games.append(gid)

    # 3. Store each reached player's final state.
    provisional_games = get_settings().provisional_games
    for u in sorted(reach):
        s = current[u]
        await session.execute(
            sa.update(PlayerRating)
            .where(PlayerRating.user_id == u)
            .values(
                rating=s.rating,
                rd=s.rd,
                volatility=s.volatility,
                display_rating=Decimal(str(s.display_rating)),
                rated_games=s.rated_games,
                is_provisional=s.rated_games < provisional_games,
                updated_at=sa.func.now(),
            )
        )

    result.elapsed_ms = (time.perf_counter() - t0) * 1000.0
    log.info(
        "rating recompute from game %s: reach=%d games=%d skipped=%d rounds=%d %.1f ms",
        game_id, len(reach), len(result.recomputed_games), len(result.skipped_games),
        result.lock_rounds, result.elapsed_ms,
    )
    return result


async def current_rating(session: AsyncSession, user_id: uuid.UUID) -> Optional[PlayerRating]:
    return await session.get(PlayerRating, user_id, populate_existing=True)
