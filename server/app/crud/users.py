"""Accounts, profiles and device tokens (FR-01, SYS-13)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.crud._common import utc_now
from server.app.crud.errors import AccountConflictError, NotFoundError, NotPermittedError, NotPurdueEmailError
from server.app.models import DeviceToken, PlayerProfile, PlayerRating, User
from server.app.services import rating_engine

PURDUE_SUFFIX = "@purdue.edu"


def is_purdue_email(email: str) -> bool:
    return email.strip().lower().endswith(PURDUE_SUFFIX)


async def get_or_create_by_firebase_uid(
    session: AsyncSession,
    *,
    firebase_uid: str,
    email: str,
    email_verified: bool,
    display_name: str,
    self_assessed_level: Optional[float] = None,
) -> User:
    """
    Return the user for a verified Firebase identity, creating the account,
    its single player profile and its rating row on first sign-in (FR-01.2).
    Safe under concurrent first sign-ins (INSERT ... ON CONFLICT DO NOTHING).
    """
    if not email_verified:
        raise NotPermittedError("email address is not verified")
    if not is_purdue_email(email):
        raise NotPurdueEmailError("only @purdue.edu accounts may sign in")
    email = email.strip()

    other = await session.scalar(
        sa.select(User).where(sa.func.lower(User.email) == email.lower(), User.firebase_uid != firebase_uid)
    )
    if other is not None:
        raise AccountConflictError("email is already linked to a different account")

    inserted_id = await session.scalar(
        pg_insert(User)
        .values(firebase_uid=firebase_uid, email=email)
        .on_conflict_do_nothing(index_elements=[User.firebase_uid])
        .returning(User.id)
    )
    if inserted_id is not None:
        level = None if self_assessed_level is None else Decimal(str(self_assessed_level))
        base = rating_engine.from_display(self_assessed_level) if self_assessed_level is not None else rating_engine.DEFAULT_RATING
        session.add(PlayerProfile(user_id=inserted_id, display_name=display_name, self_assessed_level=level))
        session.add(
            PlayerRating(
                user_id=inserted_id,
                base_rating=base,
                rating=base,
                display_rating=Decimal(str(rating_engine.to_display(base))),
            )
        )
        await session.flush()

    user = await session.scalar(sa.select(User).where(User.firebase_uid == firebase_uid))
    assert user is not None
    return user


async def set_self_assessment(session: AsyncSession, user_id: uuid.UUID, level: float) -> PlayerRating:
    """
    Record the short self-assessment (SYS-04). It sets the provisional starting
    rating only while the player has no rated games.
    """
    if not (1.0 <= level <= 5.0):
        raise ValueError("self-assessed level must be between 1.0 and 5.0")
    profile = await session.get(PlayerProfile, user_id)
    rating = await session.get(PlayerRating, user_id, with_for_update=True, populate_existing=True)
    if profile is None or rating is None:
        raise NotFoundError("player not found")
    profile.self_assessed_level = Decimal(str(level))
    if rating.rated_games == 0:
        base = rating_engine.from_display(level)
        rating.base_rating = base
        rating.rating = base
        rating.display_rating = Decimal(str(rating_engine.to_display(base)))
        rating.updated_at = utc_now()
    await session.flush()
    return rating


async def register_device_token(
    session: AsyncSession, user_id: uuid.UUID, token: str, platform: str = "ios"
) -> None:
    """Upsert an FCM token; a token moving to a new account follows the new owner."""
    await session.execute(
        pg_insert(DeviceToken)
        .values(user_id=user_id, token=token, platform=platform)
        .on_conflict_do_update(
            index_elements=[DeviceToken.token],
            set_={"user_id": user_id, "platform": platform, "updated_at": sa.func.now()},
        )
    )


async def device_tokens_for_users(session: AsyncSession, user_ids: list[uuid.UUID]) -> list[str]:
    rows = await session.scalars(sa.select(DeviceToken.token).where(DeviceToken.user_id.in_(user_ids)))
    return list(rows)
