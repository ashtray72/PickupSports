"""
Courts: spatial search, user submissions with a review queue, and saved
calibrations (FR-02, NFR-06, FR-07.4).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.crud._common import geog_point, utc_now, wkt_point
from server.app.crud.errors import NotFoundError, NotPermittedError
from server.app.models import Court, CourtCalibration, CourtStatus, User


@dataclass(frozen=True)
class NearbyCourt:
    court_id: uuid.UUID
    name: str
    address: Optional[str]
    latitude: float
    longitude: float
    indoor: bool
    hoop_count: int
    surface: Optional[str]
    distance_m: float


def _lat_lon_columns():
    geom = sa.cast(Court.location, Geometry)
    return sa.func.ST_Y(geom).label("latitude"), sa.func.ST_X(geom).label("longitude")


async def nearby_courts(
    session: AsyncSession, lat: float, lon: float, radius_m: float = 2500.0
) -> list[NearbyCourt]:
    """Approved courts within `radius_m` of the point, closest first (GIST-indexed)."""
    if radius_m <= 0:
        raise ValueError("radius must be positive")
    here = geog_point(lat, lon)
    distance = sa.func.ST_Distance(Court.location, here).label("distance_m")
    lat_col, lon_col = _lat_lon_columns()
    stmt = (
        sa.select(Court, lat_col, lon_col, distance)
        .where(Court.status == CourtStatus.APPROVED, sa.func.ST_DWithin(Court.location, here, radius_m))
        .order_by(distance, Court.id)
    )
    out = []
    for court, c_lat, c_lon, dist in (await session.execute(stmt)).all():
        out.append(
            NearbyCourt(
                court_id=court.id,
                name=court.name,
                address=court.address,
                latitude=float(c_lat),
                longitude=float(c_lon),
                indoor=court.indoor,
                hoop_count=court.hoop_count,
                surface=court.surface,
                distance_m=float(dist),
            )
        )
    return out


async def nearby_courts_geojson(
    session: AsyncSession, lat: float, lon: float, radius_m: float = 2500.0
) -> dict[str, Any]:
    """RFC 7946 FeatureCollection of nearby approved courts. Coordinates are [lon, lat]."""
    here = geog_point(lat, lon)
    distance = sa.func.ST_Distance(Court.location, here)
    stmt = (
        sa.select(Court, sa.func.ST_AsGeoJSON(Court.location, 6).label("geometry"), distance.label("distance_m"))
        .where(Court.status == CourtStatus.APPROVED, sa.func.ST_DWithin(Court.location, here, radius_m))
        .order_by(distance, Court.id)
    )
    features = []
    for court, geometry, dist in (await session.execute(stmt)).all():
        features.append(
            {
                "type": "Feature",
                "geometry": json.loads(geometry),
                "properties": {
                    "court_id": str(court.id),
                    "name": court.name,
                    "address": court.address,
                    "indoor": court.indoor,
                    "hoop_count": court.hoop_count,
                    "surface": court.surface,
                    "distance_km": round(float(dist) / 1000.0, 3),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


async def submit_court(
    session: AsyncSession,
    *,
    submitted_by: Optional[uuid.UUID],
    name: str,
    lat: float,
    lon: float,
    address: Optional[str] = None,
    indoor: bool = False,
    hoop_count: int = 1,
    surface: Optional[str] = None,
    layout_notes: Optional[str] = None,
    google_place_id: Optional[str] = None,
    status: CourtStatus = CourtStatus.PENDING,
) -> Court:
    """A user-submitted court enters the review queue as `pending` (FR-02.1)."""
    court = Court(
        name=name,
        address=address,
        location=wkt_point(lat, lon),
        indoor=indoor,
        hoop_count=hoop_count,
        surface=surface,
        layout_notes=layout_notes,
        google_place_id=google_place_id,
        status=status,
        submitted_by=submitted_by,
    )
    session.add(court)
    await session.flush()
    return court


async def review_queue(session: AsyncSession) -> Sequence[Court]:
    rows = await session.scalars(
        sa.select(Court).where(Court.status == CourtStatus.PENDING).order_by(Court.created_at)
    )
    return list(rows)


async def review_court(
    session: AsyncSession, court_id: uuid.UUID, reviewer_id: uuid.UUID, *, approve: bool
) -> Court:
    reviewer = await session.get(User, reviewer_id)
    if reviewer is None or not reviewer.is_moderator:
        raise NotPermittedError("only moderators can review courts")
    court = await session.get(Court, court_id, with_for_update=True, populate_existing=True)
    if court is None:
        raise NotFoundError("court not found")
    court.status = CourtStatus.APPROVED if approve else CourtStatus.REJECTED
    court.reviewed_by = reviewer_id
    court.reviewed_at = utc_now()
    await session.flush()
    return court


async def save_calibration(
    session: AsyncSession,
    court_id: uuid.UUID,
    created_by: Optional[uuid.UUID],
    homography: Sequence[float],
    rim_px: Optional[dict[str, Any]] = None,
) -> CourtCalibration:
    """Store a new calibration and make it the court's single active one."""
    if len(homography) != 9:
        raise ValueError("homography must have 9 values (row-major 3x3)")
    await session.execute(
        sa.update(CourtCalibration)
        .where(CourtCalibration.court_id == court_id, CourtCalibration.is_active)
        .values(is_active=False)
    )
    cal = CourtCalibration(
        court_id=court_id, created_by=created_by, homography=[float(v) for v in homography], rim_px=rim_px
    )
    session.add(cal)
    await session.flush()
    return cal


async def active_calibration(session: AsyncSession, court_id: uuid.UUID) -> Optional[CourtCalibration]:
    return await session.scalar(
        sa.select(CourtCalibration).where(CourtCalibration.court_id == court_id, CourtCalibration.is_active)
    )
