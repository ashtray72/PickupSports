"""
Automated Unit Tests for PickupSports Mapping Subsystem.
Verifies RFC 7946 GeoJSON compliance, Google Places API client,
Haversine distance calculation, and PickupSports dummy game aggregation.
"""

from __future__ import annotations
import io
import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import requests

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from server.app.services.mapping_service import (
    haversine_distance_km,
    validate_coordinates,
    get_nearby_courts_with_games,
    KM_TO_MILES,
)
from server.app.schemas.court import (
    GameSummary,
    CourtGeoJSONFeatureCollection,
)
from database.seeds.dummy_games import (
    get_dummy_games_for_court,
    get_mock_campus_courts,
    DUMMY_GAMES_DB,
)


class TestMappingSubsystem(unittest.TestCase):
    """Test suite for the Mapping Subsystem module."""

    def setUp(self):
        # Purdue University CoRec coordinates
        self.corec_lat = 40.4282
        self.corec_lon = -86.9222
        self.corec_coords = (self.corec_lat, self.corec_lon)
        # Isolate unit tests from developer's local .env file
        self.env_patcher = patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": ""})
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def test_haversine_distance_accuracy(self):
        """Tests that Haversine distance matches expected values between known landmarks."""
        # Distance from Purdue CoRec to Pickett Park Courts (~0.35 to 0.45 miles)
        pickett_lat = 40.4258
        pickett_lon = -86.9270
        dist_km = haversine_distance_km(self.corec_lat, self.corec_lon, pickett_lat, pickett_lon)
        dist_mi = dist_km * KM_TO_MILES

        self.assertGreater(dist_mi, 0.25)
        self.assertLess(dist_mi, 0.60)

        # Distance to self must be 0
        dist_self = haversine_distance_km(self.corec_lat, self.corec_lon, self.corec_lat, self.corec_lon)
        self.assertAlmostEqual(dist_self, 0.0, places=5)

    def test_coordinate_validation(self):
        """Tests physical coordinate bounds and input validation."""
        # Valid coordinates should pass without error
        validate_coordinates(40.4282, -86.9222)
        validate_coordinates(0.0, 0.0)
        validate_coordinates(-90.0, 180.0)

        # Out-of-bounds latitude
        with self.assertRaises(ValueError):
            validate_coordinates(91.0, -86.9222)
        with self.assertRaises(ValueError):
            validate_coordinates(-90.5, 0.0)

        # Out-of-bounds longitude
        with self.assertRaises(ValueError):
            validate_coordinates(40.4282, 180.5)
        with self.assertRaises(ValueError):
            validate_coordinates(40.4282, -181.0)

        # Invalid tuple structure
        with self.assertRaises(ValueError):
            get_nearby_courts_with_games((40.4282,))  # Only 1 element
        with self.assertRaises(ValueError):
            get_nearby_courts_with_games("40.4282, -86.9222")  # String instead of tuple

    def test_radius_validation(self):
        """Tests that negative or zero search radius raises ValueError."""
        with self.assertRaises(ValueError):
            get_nearby_courts_with_games(self.corec_coords, radius_meters=0)
        with self.assertRaises(ValueError):
            get_nearby_courts_with_games(self.corec_coords, radius_meters=-500)

    def test_fallback_notice_when_api_key_missing(self):
        """Verifies that missing API key triggers the console notice and loads fallback data."""
        captured_output = io.StringIO()
        with patch("sys.stdout", captured_output), patch.dict(os.environ, {}, clear=True):
            collection = get_nearby_courts_with_games(self.corec_coords, radius_meters=2500)

        output_text = captured_output.getvalue()
        self.assertIn("[NOTICE] Google Places API key not provided or request failed", output_text)
        self.assertIn("Purdue Campus Mock Dataset", collection.metadata["data_source"])
        self.assertGreater(collection.metadata["total_courts_found"], 0)

    def test_rfc7946_geojson_structure(self):
        """Verifies that the serialized output conforms strictly to IETF RFC 7946 GeoJSON standard."""
        collection = get_nearby_courts_with_games(self.corec_coords, radius_meters=2500)
        geojson_dict = collection.to_dict()

        # Check top-level FeatureCollection keys
        self.assertEqual(geojson_dict.get("type"), "FeatureCollection")
        self.assertIn("features", geojson_dict)
        self.assertIn("metadata", geojson_dict)

        # Verify each feature
        for feature in geojson_dict["features"]:
            self.assertEqual(feature.get("type"), "Feature")
            geometry = feature.get("geometry", {})
            self.assertEqual(geometry.get("type"), "Point")

            # RFC 7946: coordinates must be [longitude, latitude]
            coords = geometry.get("coordinates")
            self.assertIsInstance(coords, list)
            self.assertEqual(len(coords), 2)
            lon, lat = coords[0], coords[1]
            self.assertGreaterEqual(lat, -90.0)
            self.assertLessEqual(lat, 90.0)
            self.assertGreaterEqual(lon, -180.0)
            self.assertLessEqual(lon, 180.0)

            # Properties must contain court metadata and games
            props = feature.get("properties", {})
            self.assertIn("court_id", props)
            self.assertIn("name", props)
            self.assertIn("distance_miles", props)
            self.assertIn("games", props)
            self.assertIsInstance(props["games"], list)

    def test_dummy_game_merging_matches_figma_wireflow(self):
        """Verifies that CoRec Court 2 games match Figma Flow 1 screens 3, 4, and 5."""
        games = get_dummy_games_for_court("Purdue CoRec - Court 2")
        self.assertEqual(len(games), 4)

        # Check the 11:30 AM Mid game hosted by Mike R. (Figma Screen 5)
        mike_game = next((g for g in games if g.start_time == "11:30 AM"), None)
        self.assertIsNotNone(mike_game)
        self.assertEqual(mike_game.skill_level, "Mid")
        self.assertEqual(mike_game.spots_available, 3)
        self.assertEqual(mike_game.host_name, "Mike R.")
        self.assertEqual(mike_game.host_show_up_rate, 0.95)
        self.assertTrue(mike_game.is_looking_for_players)

        # Check the 1:00 PM game that is full
        full_game = next((g for g in games if g.start_time == "1:00 PM"), None)
        self.assertIsNotNone(full_game)
        self.assertEqual(full_game.spots_available, 0)
        self.assertFalse(full_game.is_looking_for_players)

    def test_skill_filter_chip_interaction(self):
        """Tests skill level filtering matching Figma Screen 4 and 4b."""
        # Screen 4: Mid skill filter
        collection_mid = get_nearby_courts_with_games(
            self.corec_coords,
            radius_meters=2500,
            skill_filter="Mid",
        )
        corec_c2 = next(
            (f for f in collection_mid.features if "Court 2" in f.properties.name),
            None,
        )
        self.assertIsNotNone(corec_c2)
        for game in corec_c2.properties.games:
            self.assertEqual(game.skill_level, "Mid")

        # Screen 4b: Advanced skill filter (CoRec Court 2 has no advanced games, but Hillenbrand does)
        collection_adv = get_nearby_courts_with_games(
            self.corec_coords,
            radius_meters=2500,
            skill_filter="Advanced",
        )
        c2_adv = next(
            (f for f in collection_adv.features if "Court 2" in f.properties.name),
            None,
        )
        self.assertEqual(len(c2_adv.properties.games), 0)

        hillenbrand_adv = next(
            (f for f in collection_adv.features if "Hillenbrand" in f.properties.name),
            None,
        )
        self.assertIsNotNone(hillenbrand_adv)
        self.assertGreaterEqual(len(hillenbrand_adv.properties.games), 1)
        self.assertEqual(hillenbrand_adv.properties.games[0].skill_level, "Advanced")

    def test_only_looking_for_players_flag(self):
        """Verifies that only_looking_for_players=True omits games with 0 open spots."""
        collection_open = get_nearby_courts_with_games(
            self.corec_coords,
            radius_meters=2500,
            only_looking_for_players=True,
        )
        for feature in collection_open.features:
            for game in feature.properties.games:
                self.assertGreater(game.spots_available, 0)
                self.assertTrue(game.is_looking_for_players)

    @patch("requests.post")
    def test_google_places_api_successful_response(self, mock_post):
        """Mocks a successful Google Places API (New) HTTP response and tests parsing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "places": [
                {
                    "id": "places/ChIJmock_12345",
                    "displayName": {
                        "text": "Live Google Court",
                        "languageCode": "en",
                    },
                    "formattedAddress": "100 Stadium Way, West Lafayette, IN 47906",
                    "location": {
                        "latitude": 40.4290,
                        "longitude": -86.9230,
                    },
                    "currentOpeningHours": {"openNow": True},
                }
            ],
        }
        mock_post.return_value = mock_response

        collection = get_nearby_courts_with_games(
            self.corec_coords,
            radius_meters=2500,
            api_key="AIzaSyFakeGoogleApiKeyForTesting12345",
        )

        self.assertEqual(collection.metadata["data_source"], "Google Places API")
        self.assertEqual(len(collection.features), 1)
        court = collection.features[0].properties
        self.assertEqual(court.name, "Live Google Court")
        self.assertEqual(court.court_id, "places/ChIJmock_12345")

    @patch("requests.post")
    def test_google_places_api_network_failure_fallback(self, mock_post):
        """Mocks a Google Places API (New) network timeout and verifies graceful fallback."""
        mock_post.side_effect = requests.RequestException("Network connection failed")

        captured_output = io.StringIO()
        with patch("sys.stdout", captured_output):
            collection = get_nearby_courts_with_games(
                self.corec_coords,
                radius_meters=2500,
                api_key="AIzaSyFakeKeyThatFailsNetwork",
            )

        self.assertIn("Purdue Campus Mock Dataset", collection.metadata["data_source"])
        self.assertGreater(len(collection.features), 0)


if __name__ == "__main__":
    unittest.main()
