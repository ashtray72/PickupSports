"""
Court and Game Data Schemas for PickupSports Mapping Subsystem.
Compliant with IETF RFC 7946 (The GeoJSON Format).
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, List, Optional, Tuple


@dataclass
class GameSummary:
    """Represents an upcoming pickup game at a court looking for players."""
    game_id: str
    court_id: str
    court_name: str
    start_time: str
    end_time: str
    skill_level: str  # "Beginner", "Mid", "Advanced", "All"
    spots_available: int
    total_spots: int
    host_name: str
    host_show_up_rate: float  # e.g., 0.95 for 95%
    host_rating: float  # e.g., 4.2
    rules: str  # e.g., "1s & 2s", "Standard 5v5"
    status: str = "open"  # "open", "full", "looking_for_players"

    @property
    def is_looking_for_players(self) -> bool:
        return self.spots_available > 0 and self.status != "full"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_looking_for_players"] = self.is_looking_for_players
        return d


@dataclass
class CourtProperties:
    """Detailed properties for a basketball court pin."""
    court_id: str
    name: str
    address: str
    indoor: bool
    hoop_count: int
    surface: str  # "Hardwood", "Asphalt", "Polyurethane"
    distance_miles: float
    distance_km: float
    open_now: bool
    active_games_count: int
    games: List[GameSummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "court_id": self.court_id,
            "name": self.name,
            "address": self.address,
            "indoor": self.indoor,
            "hoop_count": self.hoop_count,
            "surface": self.surface,
            "distance_miles": round(self.distance_miles, 2),
            "distance_km": round(self.distance_km, 2),
            "open_now": self.open_now,
            "active_games_count": self.active_games_count,
            "games": [g.to_dict() for g in self.games],
        }


@dataclass
class CourtGeoJSONFeature:
    """IETF RFC 7946 GeoJSON Feature representing a court pin."""
    properties: CourtProperties
    latitude: float
    longitude: float
    type: str = "Feature"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "geometry": {
                "type": "Point",
                # RFC 7946 standard: coordinates must be [longitude, latitude]
                "coordinates": [round(self.longitude, 6), round(self.latitude, 6)],
            },
            "properties": self.properties.to_dict(),
        }


@dataclass
class CourtGeoJSONFeatureCollection:
    """IETF RFC 7946 GeoJSON FeatureCollection returned to client."""
    features: List[CourtGeoJSONFeature] = field(default_factory=list)
    type: str = "FeatureCollection"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "metadata": self.metadata,
            "features": [f.to_dict() for f in self.features],
        }
