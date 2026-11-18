"""
CV pipeline output: detected player tracks (with self-tags), shot events and
highlight clips (SYS-08, SYS-09, SYS-10).

Box scores are NOT stored: they are the `player_box_scores` view over these
tables, so they can never be stale. Deliberately absent: any face, appearance
or embedding data (NFR-04 / SYS-21).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from server.app.db.base import UUID_PK, Base, pg_enum


class ReviewStatus(str, enum.Enum):
    AUTO = "auto"            # confident enough to count without review
    FLAGGED = "flagged"      # low confidence: not counted until a player confirms
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class DetectedTrack(Base):
    __tablename__ = "detected_tracks"
    __table_args__ = (
        sa.UniqueConstraint("recording_id", "track_label"),
        sa.CheckConstraint("team IS NULL OR team IN ('A', 'B')", name="team_values"),
        sa.CheckConstraint(
            "(tagged_user_id IS NULL) = (tagged_at IS NULL)", name="tag_fields_together"
        ),
        # A user is tagged on at most one track per game.
        sa.Index(
            "uq_detected_tracks_one_tag_per_game",
            "game_id",
            "tagged_user_id",
            unique=True,
            postgresql_where=sa.text("tagged_user_id IS NOT NULL"),
        ),
        # Only participants of the game can be tagged. (NO ACTION, not RESTRICT: it is
        # checked at statement end, so deleting a game can cascade through both tables.)
        sa.ForeignKeyConstraint(
            ["game_id", "tagged_user_id"],
            ["game_participants.game_id", "game_participants.user_id"],
            name="fk_detected_tracks_tagged_participant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("games.id", ondelete="CASCADE")
    )
    recording_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("recordings.id", ondelete="CASCADE")
    )
    track_label: Mapped[int] = mapped_column(sa.Integer)
    team: Mapped[Optional[str]] = mapped_column(sa.String(1))
    thumbnail_key: Mapped[Optional[str]] = mapped_column(sa.String(1024))
    tagged_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    tagged_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))


class ShotEvent(Base):
    __tablename__ = "shot_events"
    __table_args__ = (
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        sa.CheckConstraint("t_ms >= 0", name="t_ms_nonnegative"),
        sa.Index("ix_shot_events_game_id", "game_id"),
        sa.Index("ix_shot_events_track_id", "track_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("games.id", ondelete="CASCADE")
    )
    track_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("detected_tracks.id", ondelete="CASCADE")
    )
    t_ms: Mapped[int] = mapped_column(sa.Integer)
    court_x: Mapped[float] = mapped_column(sa.Float)
    court_y: Mapped[float] = mapped_column(sa.Float)
    beyond_arc: Mapped[bool] = mapped_column(sa.Boolean)
    made: Mapped[bool] = mapped_column(sa.Boolean)
    confidence: Mapped[float] = mapped_column(sa.Float)
    review_status: Mapped[ReviewStatus] = mapped_column(pg_enum(ReviewStatus, "review_status"))
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))


class HighlightClip(Base):
    __tablename__ = "highlight_clips"
    __table_args__ = (sa.CheckConstraint("duration_ms > 0", name="duration_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("games.id", ondelete="CASCADE"), index=True
    )
    shot_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("shot_events.id", ondelete="CASCADE"), unique=True
    )
    object_key: Mapped[str] = mapped_column(sa.String(1024))
    duration_ms: Mapped[int] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
