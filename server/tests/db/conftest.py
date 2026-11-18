"""
Fixtures for the database test suite (needs PostgreSQL + PostGIS):

    docker compose up -d db
    python -m pytest server/tests -v

* The test database is migrated from scratch once per session (downgrade base,
  upgrade head) using TEST_DATABASE_URL.
* `db`: an AsyncSession inside a transaction that is rolled back after the test.
  CRUD calls that open savepoints work normally inside it.
* `committing`: a sessionmaker for tests that need real commits (concurrency,
  LISTEN/NOTIFY, cross-connection visibility). Tables are truncated afterwards.

If the database is unreachable these tests are skipped with a clear message,
unless REQUIRE_DB=1 (set in CI), in which case they fail.
"""

from __future__ import annotations

import asyncio
import os
import pathlib

import asyncpg
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from server.app.core.config import get_settings
from server.app.crud import ratings
from server.app.db.session import asyncpg_dsn, make_engine, make_sessionmaker

TEST_URL = get_settings().test_database_url
TEST_DSN = asyncpg_dsn(TEST_URL)
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DB_TEST_DIR = pathlib.Path(__file__).resolve().parent

APP_TABLES = [
    "highlight_clips", "shot_events", "detected_tracks", "processing_jobs", "recordings",
    "rating_history", "reliability_events", "game_blocks", "game_participants", "games",
    "court_calibrations", "courts", "device_tokens", "player_ratings", "player_profiles", "users",
]


def _probe_database() -> str | None:
    async def probe() -> None:
        conn = await asyncpg.connect(TEST_DSN, timeout=3)
        await conn.close()

    try:
        asyncio.run(probe())
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


_DB_ERROR: str | None = None
_DB_PROBED = False


def pytest_collection_modifyitems(config, items):
    global _DB_ERROR, _DB_PROBED
    db_items = [i for i in items if DB_TEST_DIR in pathlib.Path(str(i.fspath)).resolve().parents]
    if not db_items:
        return
    for item in db_items:
        item.add_marker(pytest.mark.db)
    if not _DB_PROBED:
        _DB_ERROR = _probe_database()
        _DB_PROBED = True
    if _DB_ERROR is None:
        return
    message = (
        f"PostgreSQL test database not reachable at {TEST_DSN} ({_DB_ERROR}). "
        "Start it with `docker compose up -d db`."
    )
    if os.environ.get("REQUIRE_DB") == "1":
        raise pytest.UsageError(message)
    skip = pytest.mark.skip(reason=message)
    for item in db_items:
        item.add_marker(skip)


def alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "server" / "alembic.ini"))
    cfg.attributes["url"] = TEST_URL
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def alembic_cfg() -> Config:
    return alembic_config()


@pytest.fixture(scope="session", autouse=True)
def migrated_database(alembic_cfg):
    """Fresh schema for the whole session."""
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
    yield


@pytest.fixture(scope="session")
async def engine():
    eng = make_engine(TEST_URL, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
async def pooled_engine():
    """For concurrency tests: enough pooled connections for 50+ simultaneous sessions."""
    eng = make_engine(TEST_URL, pool_size=60, max_overflow=0, pool_timeout=30)
    yield eng
    await eng.dispose()


async def _truncate(eng) -> None:
    async with eng.begin() as conn:
        await conn.execute(sa.text(f"TRUNCATE {', '.join(APP_TABLES)} CASCADE"))


@pytest.fixture
async def db(engine):
    """Session in a transaction that is always rolled back."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()


@pytest.fixture
async def committing(pooled_engine):
    """Sessionmaker whose commits are real; all tables are truncated afterwards."""
    await _truncate(pooled_engine)
    try:
        yield make_sessionmaker(pooled_engine)
    finally:
        await _truncate(pooled_engine)


@pytest.fixture(autouse=True)
def _clear_rating_hooks():
    ratings._hooks.clear()
    yield
    ratings._hooks.clear()
