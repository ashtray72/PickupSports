"""Accounts and profiles (FR-01.1, FR-01.2) and device tokens (SYS-13)."""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from server.app.crud import users
from server.app.crud.errors import AccountConflictError, NotPermittedError, NotPurdueEmailError
from server.app.models import PlayerProfile, PlayerRating, User
from server.tests.db import factories as f


async def _sign_in(db, uid="fb-1", email="pete@purdue.edu", verified=True, level=None):
    return await users.get_or_create_by_firebase_uid(
        db, firebase_uid=uid, email=email, email_verified=verified, display_name="Pete", self_assessed_level=level
    )


async def test_first_sign_in_creates_one_profile_and_rating(db):
    u = await _sign_in(db, level=3.8)
    again = await _sign_in(db, level=3.8)
    assert again.id == u.id
    assert await db.scalar(sa.select(sa.func.count()).select_from(User).where(User.firebase_uid == "fb-1")) == 1
    assert await db.scalar(sa.select(sa.func.count()).select_from(PlayerProfile).where(PlayerProfile.user_id == u.id)) == 1
    rating = await db.get(PlayerRating, u.id)
    assert rating.display_rating == Decimal("3.8") and rating.is_provisional


async def test_non_purdue_or_unverified_rejected(db):
    with pytest.raises(NotPurdueEmailError):
        await _sign_in(db, email="pete@gmail.com")
    with pytest.raises(NotPermittedError):
        await _sign_in(db, verified=False)


async def test_email_owned_by_another_account(db):
    await _sign_in(db, uid="fb-1", email="pete@purdue.edu")
    with pytest.raises(AccountConflictError):
        await _sign_in(db, uid="fb-2", email="PETE@purdue.edu")


async def test_self_assessment_sets_provisional_rating_only_before_rated_games(db):
    u = await f.user(db, rating=None)
    rating = await users.set_self_assessment(db, u.id, 4.4)
    assert rating.display_rating == Decimal("4.4")
    await db.execute(sa.update(PlayerRating).where(PlayerRating.user_id == u.id).values(rated_games=1))
    rating = await users.set_self_assessment(db, u.id, 2.0)
    await db.refresh(rating)
    assert rating.display_rating == Decimal("4.4")
    with pytest.raises(ValueError):
        await users.set_self_assessment(db, u.id, 6.0)


async def test_device_token_upsert_follows_new_owner(db):
    a, b = await f.user(db), await f.user(db)
    await users.register_device_token(db, a.id, "tok-1")
    await users.register_device_token(db, b.id, "tok-1")
    assert await users.device_tokens_for_users(db, [a.id]) == []
    assert await users.device_tokens_for_users(db, [b.id]) == ["tok-1"]
