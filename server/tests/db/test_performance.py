"""
L. Performance smoke test (NFR-06, database share of the 2 s map load).

Seeds all Purdue / West Lafayette courts plus 200 upcoming games, then times
50 runs each of the map queries. The 95th percentile must be under 100 ms.
"""

from __future__ import annotations

import random
import statistics
import time
from datetime import timedelta

import pytest

from scripts.seed_database import seed
from server.app.crud import courts as courts_crud
from server.app.crud import games as games_crud
from server.app.models import Court, ScoringRule, Visibility
from server.tests.db import factories as f

RUNS = 50
P95_LIMIT_MS = 100.0
LAT, LON = f.CENTER


def p95(samples_ms: list[float]) -> float:
    return statistics.quantiles(samples_ms, n=20)[-1]


@pytest.mark.slow
async def test_map_queries_p95_under_100ms(committing):
    rng = random.Random(49595)
    async with committing() as s, s.begin():
        await seed(s)
        court_ids = [c.id for c in (await s.execute(Court.__table__.select())).all()]
        organizers = [await f.user(s, rating=rng.choice([2.5, 3.5, 4.2])) for _ in range(20)]
        ranges = [None, (2.0, 3.0), (3.5, 4.0), (4.0, 5.0)]
        for i in range(200):
            start = f.at(hours=rng.randint(1, 24 * 7))
            rng_range = rng.choice(ranges)
            await games_crud.create_game(
                s,
                organizer_id=rng.choice(organizers).id,
                court_id=rng.choice(court_ids),
                starts_at=start,
                ends_at=start + timedelta(minutes=rng.choice([60, 75, 90])),
                player_cap=rng.choice([6, 8, 10]),
                visibility=rng.choice([Visibility.PUBLIC, Visibility.REQUEST_TO_JOIN, Visibility.PRIVATE]),
                scoring_rule=ScoringRule.ONES_TWOS,
                skill_min=None if rng_range is None else rng_range[0],
                skill_max=None if rng_range is None else rng_range[1],
            )
        viewer = await f.user(s, rating=3.8)

    async with committing() as s:
        await courts_crud.nearby_courts(s, LAT, LON)  # warm up connection and plan caches
        await games_crud.list_visible_games(s, viewer.id, near=(LAT, LON))
        timings = {"nearby_courts": [], "list_visible_games": []}
        for _ in range(RUNS):
            t0 = time.perf_counter()
            await courts_crud.nearby_courts(s, LAT, LON, 2500)
            timings["nearby_courts"].append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            visible = await games_crud.list_visible_games(s, viewer.id, near=(LAT, LON), radius_m=2500)
            timings["list_visible_games"].append((time.perf_counter() - t0) * 1000)
        assert visible, "expected some visible games in the seeded data"

    report = {name: round(p95(ms), 1) for name, ms in timings.items()}
    print(f"\n[perf] p95 ms over {RUNS} runs: {report}")
    for name, value in report.items():
        assert value < P95_LIMIT_MS, f"{name} p95 {value} ms >= {P95_LIMIT_MS} ms"
