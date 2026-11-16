"""
Courts (SYS-02) and saved camera calibrations (SYS-07 / FR-07.4).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from geoalchemy2 import Geography
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from server.app.db.base import UUID_PK, Base, pg_enum


class CourtStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Court(Base):
    """
    A basketball court. User submissions start as `pending`; only `approved`
    courts are returned by public queries. The pending rows *are* the review queue.
    """

    __tablename__ = "courts"
    __table_args__ = (
        sa.CheckConstraint("hoop_count > 0", name="hoop_count_positive"),
        # Explicit GIST index (instead of GeoAlchemy's implicit one) so Alembic sees it.
        sa.Index("ix_courts_location", "location", postgresql_using="gist"),
        sa.Index(
            "ix_courts_location_approved",
            "location",
            postgresql_using="gist",
            postgresql_where=sa.text("status = 'approved'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    name: Mapped[str] = mapped_column(sa.String(200))
    address: Mapped[Optional[str]] = mapped_column(sa.String(300))
    location: Mapped[Any] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    )
    indoor: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("false"))
    hoop_count: Mapped[int] = mapped_column(sa.SmallInteger, server_default=sa.text("1"))
    surface: Mapped[Optional[str]] = mapped_column(sa.String(40))
    layout_notes: Mapped[Optional[str]] = mapped_column(sa.Text)
    google_place_id: Mapped[Optional[str]] = mapped_column(sa.String(256), unique=True)
    status: Mapped[CourtStatus] = mapped_column(
        pg_enum(CourtStatus, "court_status"), server_default=CourtStatus.PENDING.value
    )
    submitted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class CourtCalibration(Base):
    """A camera-to-court homography saved for reuse at the same court (FR-07.4)."""

    __tablename__ = "court_calibrations"
    __table_args__ = (
        sa.CheckConstraint("cardinality(homography) = 9", name="homography_3x3"),
        sa.Index(
            "uq_court_calibrations_one_active",
            "court_id",
            unique=True,
            postgresql_where=sa.text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    court_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("courts.id", ondelete="CASCADE"), index=True
    )
    # Row-major 3x3 matrix mapping image pixels to court-diagram feet.
    homography: Mapped[list[float]] = mapped_column(ARRAY(sa.Float))
    rim_px: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("true"))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
