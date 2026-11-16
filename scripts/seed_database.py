"""
Seed the PickupSports database with Purdue / West Lafayette courts and the
Figma "Flow 1: Find and Join a Game" games from database/seeds/dummy_games.py.

    docker compose up -d db
    python -m alembic -c server/alembic.ini upgrade head
    python scripts/seed_database.py                 # games dated today
    python scripts/seed_database.py --date 2026-11-20

Idempotent: running it again creates no duplicates (courts are matched by name,
seed users by Firebase UID, games by court + organizer + start time).
Seed users have `seed:` Firebase UIDs and @purdue.edu addresses that are not
real accounts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from database.seeds.dummy_games import DUMMY_GAMES_DB  # noqa: E402
from server.app.crud import courts as courts_crud  # noqa: E402
from server.app.crud import games as games_crud  # noqa: E402
from server.app.crud import users as users_crud  # noqa: E402
from server.app.models import (  # noqa: E402
    Court,
    CourtStatus,
    Game,
    GameParticipant,
    ParticipantStatus,
    PlayerProfile,
    ScoringRule,
    User,
    Visibility,
)

COURTS_GEOJSON = os.path.join(REPO_ROOT, "database", "seeds", "purdue_courts.geojson")

try:  # West Lafayette local time; Windows needs the `tzdata` package for zoneinfo.
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("America/Indiana/Indianapolis")
except Exception:  # pragma: no cover - fallback without tz database
    LOCAL_TZ = timezone(timedelta(hours=-5), "EST")

SKILL_RANGES = {
    "Beginner": (2.0, 3.0),
    "Mid": (3.5, 4.0),
    "Advanced": (4.0, 5.0),
    "All": (None, None),
}
FILLER_PLAYERS = 12


@dataclass
class SeedSummary:
    courts_created: int = 0
    users_created: int = 0
    games_created: int = 0
    skipped_games: list[str] = field(default_factory=list)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _local_time(day: date, hhmm: str) -> datetime:
    t = datetime.strptime(hhmm.strip(), "%I:%M %p").time()
    return datetime.combine(day, t, tzinfo=LOCAL_TZ)


def _scoring_rule(rules: str) -> ScoringRule:
    return ScoringRule.ONES_TWOS if "1s" in rules else ScoringRule.TWOS_THREES


async def _seed_user(
    session: AsyncSession,
    summary: SeedSummary,
    slug: str,
    display_name: str,
    *,
    rating_display: Optional[float] = None,
    show_up_rate: Optional[float] = None,
    moderator: bool = False,
) -> User:
    uid = f"seed:{slug}"
    existed = await session.scalar(sa.select(User.id).where(User.firebase_uid == uid))
    user = await users_crud.get_or_create_by_firebase_uid(
        session,
        firebase_uid=uid,
        email=f"seed.{slug}@purdue.edu",
        email_verified=True,
        display_name=display_name,
        self_assessed_level=rating_display,
    )
    if existed is None:
        summary.users_created += 1
        if show_up_rate is not None:
            # Seed-only shortcut: real scores are recalculated from attendance events.
            await session.execute(
                sa.update(PlayerProfile)
                .where(PlayerProfile.user_id == user.id)
                .values(reliability_score=Decimal(str(show_up_rate)).quantize(Decimal("0.001")))
            )
        if moderator:
            user.is_moderator = True
        await session.flush()
    return user


async def seed(session: AsyncSession, day: Optional[date] = None) -> SeedSummary:
    """Load courts, seed users and the Figma games. Caller commits."""
    day = day or datetime.now(LOCAL_TZ).date()
    summary = SeedSummary()

    moderator = await _seed_user(session, summary, "moderator", "Seed Moderator", moderator=True)

    # Courts
    with open(COURTS_GEOJSON, encoding="utf-8") as f:
        features = json.load(f)["features"]
    court_ids: dict[str, object] = {}
    for feat in features:
        props = feat["properties"]
        lon, lat = feat["geometry"]["coordinates"]
        court_id = await session.scalar(sa.select(Court.id).where(Court.name == props["name"]))
        if court_id is None:
            court = await courts_crud.submit_court(
                session,
                submitted_by=moderator.id,
                name=props["name"],
                lat=lat,
                lon=lon,
                address=props.get("address"),
                indoor=props.get("indoor", False),
                hoop_count=props.get("hoop_count", 1),
                surface=props.get("surface"),
                status=CourtStatus.APPROVED,
            )
            court.reviewed_by = moderator.id
            court.reviewed_at = datetime.now(timezone.utc)
            court_id = court.id
            summary.courts_created += 1
        court_ids[props["seed_id"]] = court_id

    fillers = [
        await _seed_user(session, summary, f"player-{i:02d}", f"Seed Player {i:02d}", rating_display=3.5)
        for i in range(1, FILLER_PLAYERS + 1)
    ]

    # Games
    for g in DUMMY_GAMES_DB:
        court_id = court_ids.get(g.court_id)
        if court_id is None:
            summary.skipped_games.append(f"{g.game_id} ({g.court_name}: no seeded coordinates)")
            continue
        host = await _seed_user(
            session,
            summary,
            f"host-{_slug(g.host_name)}",
            g.host_name,
            rating_display=g.host_rating,
            show_up_rate=g.host_show_up_rate,
        )
        starts_at = _local_time(day, g.start_time)
        ends_at = _local_time(day, g.end_time)
        exists = await session.scalar(
            sa.select(Game.id).where(
                Game.court_id == court_id, Game.organizer_id == host.id, Game.starts_at == starts_at
            )
        )
        if exists is not None:
            continue
        skill_min, skill_max = SKILL_RANGES[g.skill_level]
        game = await games_crud.create_game(
            session,
            organizer_id=host.id,
            court_id=court_id,
            starts_at=starts_at,
            ends_at=ends_at,
            player_cap=g.total_spots,
            visibility=Visibility.PUBLIC,
            scoring_rule=_scoring_rule(g.rules),
            skill_min=skill_min,
            skill_max=skill_max,
            rules_note=g.rules,
        )
        taken = g.total_spots - g.spots_available  # includes the host
        for filler in fillers[: max(0, taken - 1)]:
            session.add(GameParticipant(game_id=game.id, user_id=filler.id, status=ParticipantStatus.CONFIRMED))
        await session.flush()
        summary.games_created += 1

    return summary


async def _main(day: Optional[date], url: Optional[str]) -> None:
    from server.app.db.session import get_sessionmaker, make_engine, make_sessionmaker

    if url:
        engine = make_engine(url)
        sessionmaker = make_sessionmaker(engine)
    else:
        engine = None
        sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        summary = await seed(session, day)
    print(
        f"[seed] courts +{summary.courts_created}, users +{summary.users_created}, "
        f"games +{summary.games_created}"
    )
    for s in summary.skipped_games:
        print(f"[seed] skipped {s}")
    if engine is not None:
        await engine.dispose()
    else:
        from server.app.db.session import get_engine

        await get_engine().dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="game date (YYYY-MM-DD), default today")
    parser.add_argument("--database-url", default=None, help="override DATABASE_URL")
    args = parser.parse_args()
    asyncio.run(_main(args.date, args.database_url))
