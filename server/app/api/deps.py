"""
FastAPI dependencies: database sessions and the current user.

Prototype auth: requests identify the player with an `X-Dev-User` header
holding a Firebase UID (seed users look like `seed:demo-player`). This stands in
for verifying a Firebase ID token (FR-01.1 / NFR-05.1). It only works when
ALLOW_DEV_AUTH=1 is set; otherwise every request is rejected with 401. Replace
`current_user_id` with real token verification before anything is deployed.
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator, Optional

import sqlalchemy as sa
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.app.core.config import get_settings
from server.app.db.session import get_sessionmaker
from server.app.models import User


def session_factory() -> async_sessionmaker[AsyncSession]:
    """Overridden in tests to point at the test database."""
    return get_sessionmaker()


async def get_session(
    factory: async_sessionmaker[AsyncSession] = Depends(session_factory),
) -> AsyncIterator[AsyncSession]:
    """
    One session per request. Routes that write call `session.commit()` themselves
    before returning; anything not committed is rolled back when the session closes.
    """
    async with factory() as session:
        yield session


async def current_user_id(
    x_dev_user: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> uuid.UUID:
    if not get_settings().allow_dev_auth:
        # NFR-05.1: never accept an unverified identity unless explicitly enabled.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="sign-in is not configured (set ALLOW_DEV_AUTH=1 for local development)",
        )
    if not x_dev_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing X-Dev-User header")
    user_id = await session.scalar(sa.select(User.id).where(User.firebase_uid == x_dev_user))
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user")
    return user_id
