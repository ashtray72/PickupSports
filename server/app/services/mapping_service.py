"""
Mapping Service for PickupSports.
Implements Google Places API integration, haversine spatial distance calculations,
dummy game database aggregation, and IETF RFC 7946 GeoJSON serialization.
"""

from __future__ import annotations
import math
import os
import sys
from typing import Tuple, Optional, List, Dict, Any
import requests

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from server.app.schemas.court import (
    GameSummary,
    CourtProperties,
    CourtGeoJSONFeature,
    CourtGeoJSONFeatureCollection,
)
from database.seeds.dummy_games import (
    get_dummy_games_for_court,
    get_mock_campus_courts,
)

# Constants
EARTH_RADIUS_KM = 6371.0088
KM_TO_MILES = 0.621371
# Google Places API (New) Text Search endpoint
GOOGLE_PLACES_NEW_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
# Legacy Google Places Nearby Search endpoint (fallback)
GOOGLE_PLACES_LEGACY_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"


def load_env_file() -> None:
    """
    Lightweight .env loader without third-party dependencies.
    Reads KEY=VALUE pairs from .env in repository root into os.environ.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    env_path = os.path.join(repo_root, ".env")
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception as exc:
            print(f"[DEBUG] Failed reading .env file: {exc}")


# Load environment variables on module import
load_env_file()


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculates the great-circle distance between two points on the Earth's surface
    using the Haversine formula.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * c


def validate_coordinates(lat: float, lon: float) -> None:
    """Validates that latitude and longitude fall within physical bounds."""
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"Invalid latitude: {lat}. Latitude must be between -90.0 and 90.0.")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"Invalid longitude: {lon}. Longitude must be between -180.0 and 180.0.")


def fetch_courts_from_google_places(
    lat: float,
    lon: float,
    radius_meters: int,
    api_key: str,
) -> Optional[List[Dict[str, Any]]]:
    """
    Queries Google Places API (New) Text Search for basketball courts.
    Endpoint: https://places.googleapis.com/v1/places:searchText
    Returns normalized court dicts, or None if the request failed.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.currentOpeningHours",
    }
    payload = {
        "textQuery": "basketball court",
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": float(radius_meters),
            }
        },
        "maxResultCount": 20,
    }

    try:
        response = requests.post(
            GOOGLE_PLACES_NEW_TEXT_SEARCH_URL,
            headers=headers,
            json=payload,
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            raw_places = data.get("places", [])
            normalized_courts = []
            for place in raw_places:
                loc = place.get("location", {})
                display_name = place.get("displayName", {})
                name = (
                    display_name.get("text", "Basketball Court")
                    if isinstance(display_name, dict)
                    else str(display_name or "Basketball Court")
                )
                opening = place.get("currentOpeningHours", {})
                open_now = opening.get("openNow", True) if isinstance(opening, dict) else True

                normalized_courts.append({
                    "place_id": place.get("id", ""),
                    "name": name,
                    "latitude": loc.get("latitude", 0.0),
                    "longitude": loc.get("longitude", 0.0),
                    "address": place.get("formattedAddress", ""),
                    "indoor": "indoor" in name.lower() or "corec" in name.lower(),
                    "hoop_count": 2,
                    "surface": "Hardwood" if ("indoor" in name.lower() or "corec" in name.lower()) else "Asphalt",
                    "open_now": open_now,
                })
            return normalized_courts
        else:
            print(f"[DEBUG] Google Places API (New) returned HTTP {response.status_code}: {response.text}")
            return None
    except Exception as exc:
        print(f"[DEBUG] Google Places API (New) network request exception: {exc}")
        return None


def get_nearby_courts_with_games(
    user_location: Tuple[float, float],
    radius_meters: int = 2500,
    api_key: Optional[str] = None,
    only_looking_for_players: bool = False,
    skill_filter: Optional[str] = None,
) -> CourtGeoJSONFeatureCollection:
    """
    Main Mapping Subsystem function.
    
    1. Validates user location tuple (latitude, longitude).
    2. Queries Google Places API for physical basketball courts (primary source).
    3. If API key is missing or request fails, prints notice and falls back to mock campus courts.
    4. Combines spatial court data with scheduled PickupSports dummy games.
    5. Computes Haversine distances and filters courts within radius_meters.
    6. Returns an IETF RFC 7946 GeoJSON FeatureCollection sorted by proximity.

    :param user_location: (latitude, longitude) of user's device
    :param radius_meters: Search radius in meters (default 2500m / ~25 min walk)
    :param api_key: Google Places API key (or read from GOOGLE_PLACES_API_KEY env var)
    :param only_looking_for_players: If True, only include games with open spots
    :param skill_filter: Filter games by skill level ("Beginner", "Mid", "Advanced", "All")
    :return: CourtGeoJSONFeatureCollection dataclass (convertible to dict via .to_dict())
    """
    if not isinstance(user_location, (tuple, list)) or len(user_location) != 2:
        raise ValueError("user_location must be a tuple or list of (latitude, longitude).")

    user_lat, user_lon = float(user_location[0]), float(user_location[1])
    validate_coordinates(user_lat, user_lon)

    if radius_meters <= 0:
        raise ValueError(f"radius_meters must be positive, got {radius_meters}.")

    # Resolve API key
    resolved_api_key = api_key or os.getenv("GOOGLE_PLACES_API_KEY")

    raw_courts: Optional[List[Dict[str, Any]]] = None
    data_source: str = ""

    # Primary data source: Google Places API
    if resolved_api_key:
        raw_courts = fetch_courts_from_google_places(
            lat=user_lat,
            lon=user_lon,
            radius_meters=radius_meters,
            api_key=resolved_api_key,
        )
        if raw_courts is not None:
            data_source = "Google Places API"

    # Fallback if API key missing or call unsuccessful
    if raw_courts is None:
        print("[NOTICE] Google Places API key not provided or request failed. Falling back to Purdue campus mock dataset.")
        raw_courts = get_mock_campus_courts()
        data_source = "Purdue Campus Mock Dataset (Fallback)"

    # Filter by radius and merge with dummy PickupSports game database
    features: List[CourtGeoJSONFeature] = []
    radius_km = radius_meters / 1000.0

    for court_data in raw_courts:
        court_lat = court_data["latitude"]
        court_lon = court_data["longitude"]

        dist_km = haversine_distance_km(user_lat, user_lon, court_lat, court_lon)
        if dist_km > radius_km:
            continue

        dist_miles = dist_km * KM_TO_MILES

        # Query dummy PickupSports database for games at this court
        games = get_dummy_games_for_court(
            court_name=court_data["name"],
            only_looking_for_players=only_looking_for_players,
            skill_filter=skill_filter,
        )

        active_games_count = len([g for g in games if g.is_looking_for_players])

        court_props = CourtProperties(
            court_id=court_data["place_id"],
            name=court_data["name"],
            address=court_data.get("address", ""),
            indoor=court_data.get("indoor", False),
            hoop_count=court_data.get("hoop_count", 2),
            surface=court_data.get("surface", "Asphalt"),
            distance_miles=dist_miles,
            distance_km=dist_km,
            open_now=court_data.get("open_now", True),
            active_games_count=active_games_count,
            games=games,
        )

        feature = CourtGeoJSONFeature(
            properties=court_props,
            latitude=court_lat,
            longitude=court_lon,
        )
        features.append(feature)

    # Sort courts ascending by distance (closest first)
    features.sort(key=lambda f: f.properties.distance_miles)

    collection = CourtGeoJSONFeatureCollection(
        features=features,
        metadata={
            "user_location": {"latitude": user_lat, "longitude": user_lon},
            "radius_meters": radius_meters,
            "data_source": data_source,
            "total_courts_found": len(features),
            "total_games_found": sum(len(f.properties.games) for f in features),
            "skill_filter_applied": skill_filter or "All",
        },
    )

    return collection


if __name__ == "__main__":
    # Test execution with Purdue CoRec coordinates
    corec_coords = (40.4282, -86.9222)
    print(f"Testing mapping service at Purdue CoRec coordinates: {corec_coords}")
    result = get_nearby_courts_with_games(corec_coords, radius_meters=2500)
    print(f"Data source: {result.metadata['data_source']}")
    print(f"Total courts found: {result.metadata['total_courts_found']}")
    for feat in result.features:
        p = feat.properties
        print(f"  - {p.name}: {p.distance_miles:.2f} mi away, {len(p.games)} upcoming games ({p.active_games_count} looking for players)")
