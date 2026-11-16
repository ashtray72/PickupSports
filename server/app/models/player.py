"""
Accounts, player profiles, push-notification device tokens and ratings.
(SYS-01 sign-in, SYS-06 reliability score, SYS-12 ratings, SYS-13 push.)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from server.app.db.base import UUID_PK, Base


class User(Base):
    """A Firebase-authenticated Purdue account (FR-01)."""

    __tablename__ = "users"
    __table_args__ = (
        sa.CheckConstraint("lower(email) LIKE '%@purdue.edu'", name="purdue_email"),
        sa.Index("uq_users_lower_email", sa.text("lower(email)"), unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    firebase_uid: Mapped[str] = mapped_column(sa.String(128), unique=True)
    email: Mapped[str] = mapped_column(sa.String(320))
    # Court review queue moderators (FR-02.1).
    is_moderator: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("false"))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class PlayerProfile(Base):
    """Exactly one profile per user (FR-01.2): the primary key is the user id."""

    __tablename__ = "player_profiles"
    __table_args__ = (
        sa.CheckConstraint("reliability_score >= 0 AND reliability_score <= 1", name="reliability_range"),
        sa.CheckConstraint(
            "self_assessed_level IS NULL OR (self_assessed_level >= 1.0 AND self_assessed_level <= 5.0)",
            name="self_assessed_range",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str] = mapped_column(sa.String(80))
    self_assessed_level: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(2, 1))
    reliability_score: Mapped[Decimal] = mapped_column(sa.Numeric(4, 3), server_default=sa.text("1.000"))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class DeviceToken(Base):
    """FCM registration token for push notifications (SYS-13)."""

    __tablename__ = "device_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(sa.String(4096), unique=True)
    platform: Mapped[str] = mapped_column(sa.String(16), server_default="ios")
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class PlayerRating(Base):
    """
    Current basketball rating (SYS-12). `rating`/`rd`/`volatility` are the engine's
    internal state; `display_rating` is the 1.0-5.0 value shown in the app and
    compared against game skill ranges (SYS-04). `base_rating` is the provisional
    starting point from the self-assessment; replays start from it.
    """

    __tablename__ = "player_ratings"
    __table_args__ = (
        sa.CheckConstraint("display_rating >= 1.0 AND display_rating <= 5.0", name="display_range"),
        sa.CheckConstraint("rated_games >= 0", name="rated_games_nonnegative"),
        sa.CheckConstraint("rd > 0", name="rd_positive"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    base_rating: Mapped[float] = mapped_column(sa.Float, server_default=sa.text("1500"))
    rating: Mapped[float] = mapped_column(sa.Float, server_default=sa.text("1500"))
    rd: Mapped[float] = mapped_column(sa.Float, server_default=sa.text("350"))
    volatility: Mapped[float] = mapped_column(sa.Float, server_default=sa.text("0.06"))
    display_rating: Mapped[Decimal] = mapped_column(sa.Numeric(2, 1), server_default=sa.text("3.0"))
    is_provisional: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("true"))
    rated_games: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class RatingHistory(Base):
    """
    One row per player per finalized game. A recompute rewrites the row in place;
    the unique constraint makes double-counting a game impossible.
    """

    __tablename__ = "rating_history"
    __table_args__ = (
        sa.UniqueConstraint("user_id", "game_id"),
        sa.Index("ix_rating_history_user_order", "user_id", "game_ends_at", "game_id"),
        sa.Index("ix_rating_history_game_id", "game_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")
    )
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("games.id", ondelete="CASCADE")
    )
    # Copy of games.ends_at so replays can be ordered without a join.
    game_ends_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    before: Mapped[dict[str, Any]] = mapped_column(JSONB)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
