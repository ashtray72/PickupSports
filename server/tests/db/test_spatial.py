"""C. Spatial queries (FR-02, NFR-06): radius, ordering, review status, GIST index, GeoJSON order."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from server.app.crud import courts as courts_crud
from server.app.crud.errors import NotPermittedError
from server.app.models import CourtStatus
from server.app.services.mapping_service import haversine_distance_km
from server.tests.db import factories as f

LAT, LON = f.CENTER


async def _ring_of_courts(db):
    made = {}
    for meters, status in [
        (300, CourtStatus.APPROVED),
        (1000, CourtStatus.APPROVED),
        (2000, CourtStatus.APPROVED),
        (3000, CourtStatus.APPROVED),  # outside the 2500 m default radius
        (200, CourtStatus.PENDING),
        (400, CourtStatus.REJECTED),
    ]:
        lat, lon = f.offset_north(LAT, LON, meters)
        made[(meters, status)] = await f.court(db, lat=lat, lon=lon, name=f"{status.value}-{meters}m", status=status)
    return made


async def test_nearby_returns_only_approved_within_radius_closest_first(db):
    await _ring_of_courts(db)
    found = await courts_crud.nearby_courts(db, LAT, LON, 2500)
    assert [c.name for c in found] == ["approved-300m", "approved-1000m", "approved-2000m"]
    assert all(found[i].distance_m <= found[i + 1].distance_m for i in range(len(found) - 1))


async def test_pending_and_rejected_never_appear(db):
    await _ring_of_courts(db)
    names = {c.name for c in await courts_crud.nearby_courts(db, LAT, LON, 50_000)}
    assert "pending-200m" not in names and "rejected-400m" not in names
    assert "approved-3000m" in names


async def test_distances_match_haversine_within_half_percent(db):
    await _ring_of_courts(db)
    for c in await courts_crud.nearby_courts(db, LAT, LON, 50_000):
        expected_m = haversine_distance_km(LAT, LON, c.latitude, c.longitude) * 1000.0
        assert c.distance_m == pytest.approx(expected_m, rel=0.005)


async def test_spatial_query_uses_gist_index(db):
    await _ring_of_courts(db)
    await db.execute(sa.text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        (
            await db.execute(
                sa.text(
                    "EXPLAIN SELECT id FROM courts WHERE status = 'approved' AND "
                    "ST_DWithin(location, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, 2500)"
                ),
                {"lat": LAT, "lon": LON},
            )
        ).scalars()
    )
    assert "ix_courts_location" in plan, plan


async def test_geojson_coordinates_are_lon_lat(db):
    lat, lon = 40.4258, -86.9270
    await f.court(db, lat=lat, lon=lon, name="Pickett")
    fc = await courts_crud.nearby_courts_geojson(db, lat, lon, 100)
    assert fc["type"] == "FeatureCollection"
    feature = fc["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == pytest.approx([lon, lat], abs=1e-6)
    assert feature["properties"]["name"] == "Pickett"


async def test_invalid_coordinates_and_radius_rejected(db):
    with pytest.raises(ValueError):
        await courts_crud.nearby_courts(db, 91, 0)
    with pytest.raises(ValueError):
        await courts_crud.nearby_courts(db, 0, -181)
    with pytest.raises(ValueError):
        await courts_crud.nearby_courts(db, LAT, LON, 0)


async def test_review_queue_and_moderation(db):
    submitter = await f.user(db)
    mod = await f.user(db, moderator=True)
    lat, lon = f.offset_north(LAT, LON, 100)
    court = await courts_crud.submit_court(db, submitted_by=submitter.id, name="New court", lat=lat, lon=lon)
    assert court.status == CourtStatus.PENDING
    assert court.id in {c.id for c in await courts_crud.review_queue(db)}
    assert not await courts_crud.nearby_courts(db, LAT, LON, 500)

    with pytest.raises(NotPermittedError):
        await courts_crud.review_court(db, court.id, submitter.id, approve=True)
    await courts_crud.review_court(db, court.id, mod.id, approve=True)
    assert [c.court_id for c in await courts_crud.nearby_courts(db, LAT, LON, 500)] == [court.id]
    assert court.id not in {c.id for c in await courts_crud.review_queue(db)}


async def test_calibration_reuse_keeps_one_active(db):
    court = await f.court(db)
    u = await f.user(db)
    first = await courts_crud.save_calibration(db, court.id, u.id, [1, 0, 0, 0, 1, 0, 0, 0, 1])
    assert (await courts_crud.active_calibration(db, court.id)).id == first.id
    second = await courts_crud.save_calibration(db, court.id, u.id, [2, 0, 0, 0, 2, 0, 0, 0, 1], {"x": 1, "y": 2})
    await db.refresh(first)
    assert first.is_active is False
    assert (await courts_crud.active_calibration(db, court.id)).id == second.id
    with pytest.raises(ValueError):
        await courts_crud.save_calibration(db, court.id, u.id, [1, 2, 3])
