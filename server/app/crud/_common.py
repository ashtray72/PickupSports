"""Small helpers shared by the CRUD modules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from geoalchemy2 import Geography


def utc_now(now: Optional[datetime] = None) -> datetime:
    """`now` if given (tests pass fixed times), else the current UTC time."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    return now


def validate_coordinates(lat: float, lon: float) -> None:
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"latitude {lat} out of range [-90, 90]")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"longitude {lon} out of range [-180, 180]")


def geog_point(lat: float, lon: float) -> sa.ColumnElement:
    """SQL expression for a WGS84 geography point. Note PostGIS order is (lon, lat)."""
    validate_coordinates(lat, lon)
    return sa.cast(
        sa.func.ST_SetSRID(sa.func.ST_MakePoint(sa.literal(lon, sa.Float), sa.literal(lat, sa.Float)), 4326),
        Geography(geometry_type="POINT", srid=4326),
    )


def wkt_point(lat: float, lon: float) -> str:
    """EWKT for inserting a geography column through the ORM."""
    validate_coordinates(lat, lon)
    return f"SRID=4326;POINT({lon!r} {lat!r})"
