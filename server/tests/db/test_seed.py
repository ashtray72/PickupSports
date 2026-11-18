"""K. Seed script: loads into an empty database, is idempotent, reproduces the Figma games."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from scripts.seed_database import seed
from server.app.crud import courts as courts_crud
from server.app.models import Court, CourtStatus, Game

DAY = date(2026, 11, 20)


async def _counts(db):
    out = {}
    for table in ("courts", "users", "games", "game_participants"):
        out[table] = await db.scalar(sa.text(f"SELECT count(*) FROM {table}"))
    return out


async def test_seed_is_idempotent(db):
    first = await seed(db, DAY)
    assert first.courts_created == 5
    assert first.games_created == 8  # 9 Figma games minus Tommy Johnston (no coordinates)
    assert any("tommy" in s.lower() for s in first.skipped_games)
    counts = await _counts(db)

    second = await seed(db, DAY)
    assert (second.courts_created, second.users_created, second.games_created) == (0, 0, 0)
    assert await _counts(db) == counts


async def test_seed_reproduces_figma_corec_court_2(db):
    await seed(db, DAY)
    rows = (
        await db.execute(
            sa.select(Game.starts_at, Game.player_cap, Game.participant_count, Game.rules_note)
            .join(Court, Court.id == Game.court_id)
            .where(Court.name == "Purdue CoRec - Court 2")
            .order_by(Game.starts_at)
        )
    ).all()
    assert len(rows) == 4
    spots = [cap - count for _start, cap, count, _rules in rows]
    assert spots == [4, 3, 0, 0]
    assert sum(1 for s in spots if s > 0) == 2


async def test_seeded_courts_are_approved_and_searchable(db):
    await seed(db, DAY)
    statuses = set((await db.execute(sa.select(Court.status))).scalars())
    assert statuses == {CourtStatus.APPROVED}
    nearby = await courts_crud.nearby_courts(db, 40.4282, -86.9222, 2500)
    assert {"Purdue CoRec - Court 2", "Pickett Park Basketball Courts"} <= {c.name for c in nearby}
