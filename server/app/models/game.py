"""
Games, rosters, organizer blocks and attendance/reliability events
(SYS-03, SYS-04, SYS-05, SYS-06).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from server.app.db.base import UUID_PK, Base, pg_enum


class Visibility(str, enum.Enum):
    PUBLIC = "public"
    REQUEST_TO_JOIN = "request_to_join"
    PRIVATE = "private"


class ScoringRule(str, enum.Enum):
    ONES_TWOS = "ones_twos"
    TWOS_THREES = "twos_threes"


class GameStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ParticipantStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    LEFT = "left"
    REMOVED = "removed"


class Attendance(str, enum.Enum):
    UNKNOWN = "unknown"
    ATTENDED = "attended"
    NO_SHOW = "no_show"
    LATE_CANCEL = "late_cancel"


class ReliabilityKind(str, enum.Enum):
    ATTENDED = "attended"
    NO_SHOW = "no_show"
    LATE_CANCEL = "late_cancel"
    ORGANIZER_CANCEL = "organizer_cancel"


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        sa.CheckConstraint("ends_at > starts_at", name="ends_after_starts"),
        sa.CheckConstraint("player_cap BETWEEN 2 AND 20", name="player_cap_range"),
        sa.CheckConstraint(
            "(skill_min IS NULL AND skill_max IS NULL) OR "
            "(skill_min IS NOT NULL AND skill_max IS NOT NULL AND skill_min <= skill_max "
            "AND skill_min >= 1.0 AND skill_max <= 5.0)",
            name="skill_range",
        ),
        sa.CheckConstraint(
            "visibility <> 'private' OR invite_token IS NOT NULL", name="private_needs_invite"
        ),
        # Deliberate denormalization: the roster count lives on the game row so the
        # database itself can enforce the cap with a CHECK (see walkthrough).
        sa.CheckConstraint(
            "participant_count >= 0 AND participant_count <= player_cap", name="participant_count_cap"
        ),
        sa.Index("ix_games_court_id_starts_at", "court_id", "starts_at"),
        sa.Index("ix_games_starts_at_scheduled", "starts_at", postgresql_where=sa.text("status = 'scheduled'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    court_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("courts.id", ondelete="RESTRICT")
    )
    organizer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    starts_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    player_cap: Mapped[int] = mapped_column(sa.SmallInteger)
    skill_min: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(2, 1))
    skill_max: Mapped[Optional[Decimal]] = mapped_column(sa.Numeric(2, 1))
    visibility: Mapped[Visibility] = mapped_column(pg_enum(Visibility, "game_visibility"))
    invite_token: Mapped[Optional[str]] = mapped_column(sa.String(64), unique=True)
    scoring_rule: Mapped[ScoringRule] = mapped_column(pg_enum(ScoringRule, "scoring_rule"))
    rules_note: Mapped[Optional[str]] = mapped_column(sa.String(200))
    is_recorded: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("false"))
    status: Mapped[GameStatus] = mapped_column(
        pg_enum(GameStatus, "game_status"), server_default=GameStatus.SCHEDULED.value
    )
    participant_count: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("0"))
    stats_finalized_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
    tags_locked_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class GameParticipant(Base):
    """Roster row. The composite primary key makes duplicate participants impossible."""

    __tablename__ = "game_participants"
    __table_args__ = (
        sa.CheckConstraint("team IS NULL OR team IN ('A', 'B')", name="team_values"),
        sa.Index("ix_game_participants_user_id", "user_id"),
    )

    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[ParticipantStatus] = mapped_column(pg_enum(ParticipantStatus, "participant_status"))
    team: Mapped[Optional[str]] = mapped_column(sa.String(1))
    tracking_opt_out: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("false"))
    joined_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    left_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
    checked_in_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
    attendance: Mapped[Attendance] = mapped_column(
        pg_enum(Attendance, "attendance_status"), server_default=Attendance.UNKNOWN.value
    )


class GameBlock(Base):
    """An organizer blocking a player from all of their future games (FR-15)."""

    __tablename__ = "game_blocks"

    organizer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    blocked_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class ReliabilityEvent(Base):
    """Audit trail that the reliability score is recalculated from (SYS-06)."""

    __tablename__ = "reliability_events"
    __table_args__ = (sa.UniqueConstraint("user_id", "game_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")
    )
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("games.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ReliabilityKind] = mapped_column(pg_enum(ReliabilityKind, "reliability_kind"))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
