"""
Async PostgreSQL engine and session factory.

Unit-of-work convention used by the whole data-access layer (server/app/crud):
CRUD functions never commit. The caller owns the transaction:

    async with get_sessionmaker()() as session, session.begin():
        await crud.games.join_game(session, game_id, user_id)

That way a multi-step change (e.g. a self-tag plus the rating recompute it
causes) either commits completely or not at all.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from server.app.core.config import get_settings


def make_engine(url: str, **kwargs) -> AsyncEngine:
    kwargs.setdefault("pool_pre_ping", True)
    return create_async_engine(url, **kwargs)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    return make_engine(get_settings().database_url)


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(get_engine())


def asyncpg_dsn(sqlalchemy_url: str) -> str:
    """Convert a SQLAlchemy URL (postgresql+asyncpg://...) to a plain asyncpg DSN."""
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)
