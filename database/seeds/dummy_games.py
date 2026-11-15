"""
Dummy PickupSports Database and Mock Campus Basketball Court Seeds.
Matches the Figma wireflow screens for Purdue CoRec and West Lafayette courts.
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional
import sys
import os

# Ensure server package can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from server.app.schemas.court import GameSummary

# Realistic Purdue & West Lafayette basketball courts (mock fallback dataset)
MOCK_CAMPUS_COURTS: List[Dict[str, Any]] = [
    {
        "place_id": "mock_place_corec_c2",
        "name": "Purdue CoRec - Court 2",
        "latitude": 40.4284,
        "longitude": -86.9215,
        "address": "355 N Martin Jischke Dr, West Lafayette, IN 47906",
        "indoor": True,
        "hoop_count": 2,
        "surface": "Hardwood",
        "open_now": True,
    },
    {
        "place_id": "mock_place_corec_c4",
        "name": "Purdue CoRec - Court 4",
        "latitude": 40.4286,
        "longitude": -86.9212,
        "address": "355 N Martin Jischke Dr, West Lafayette, IN 47906",
        "indoor": True,
        "hoop_count": 2,
        "surface": "Hardwood",
        "open_now": True,
    },
    {
        "place_id": "mock_place_pickett_1",
        "name": "Pickett Park Basketball Courts",
        "latitude": 40.4258,
        "longitude": -86.9270,
        "address": "Stadium Ave & McCormick Rd, West Lafayette, IN 47906",
        "indoor": False,
        "hoop_count": 4,
        "surface": "Asphalt",
        "open_now": True,
    },
    {
        "place_id": "mock_place_hillenbrand",
        "name": "Hillenbrand Hall Courts",
        "latitude": 40.4262,
        "longitude": -86.9238,
        "address": "1301 Third Street, West Lafayette, IN 47906",
        "indoor": False,
        "hoop_count": 2,
        "surface": "Asphalt",
        "open_now": True,
    },
    {
        "place_id": "mock_place_blackbird",
        "name": "Blackbird Farms Basketball Court",
        "latitude": 40.4435,
        "longitude": -86.9312,
        "address": "2250 E Blackbird Dr, West Lafayette, IN 47906",
        "indoor": False,
        "hoop_count": 2,
        "surface": "Asphalt",
        "open_now": True,
    },
]

# Active pickup games in the PickupSports database
# Specifically mirrors Figma Flow 1 Screen 3, 4, and 5:
# CoRec Court 2 has:
# - 11:00 AM Beginner (4 spots)
# - 11:30 AM Mid (3 spots, Mike R. host, 95% show-up)
# - 1:00 PM Mid (Full)
# - 2:00 PM Mid (Full)
DUMMY_GAMES_DB: List[GameSummary] = [
    # CoRec Court 2 Games
    GameSummary(
        game_id="gm_corec_001",
        court_id="mock_place_corec_c2",
        court_name="Purdue CoRec - Court 2",
        start_time="11:00 AM",
        end_time="12:00 PM",
        skill_level="Beginner",
        spots_available=4,
        total_spots=10,
        host_name="Alex T.",
        host_show_up_rate=0.90,
        host_rating=3.8,
        rules="1s & 2s to 15",
        status="open",
    ),
    GameSummary(
        game_id="gm_corec_002",
        court_id="mock_place_corec_c2",
        court_name="Purdue CoRec - Court 2",
        start_time="11:30 AM",
        end_time="12:30 PM",
        skill_level="Mid",
        spots_available=3,
        total_spots=10,
        host_name="Mike R.",
        host_show_up_rate=0.95,
        host_rating=4.2,
        rules="1s & 2s, half court",
        status="open",
    ),
    GameSummary(
        game_id="gm_corec_003",
        court_id="mock_place_corec_c2",
        court_name="Purdue CoRec - Court 2",
        start_time="1:00 PM",
        end_time="2:00 PM",
        skill_level="Mid",
        spots_available=0,
        total_spots=10,
        host_name="David L.",
        host_show_up_rate=0.88,
        host_rating=4.0,
        rules="1s & 2s",
        status="full",
    ),
    GameSummary(
        game_id="gm_corec_004",
        court_id="mock_place_corec_c2",
        court_name="Purdue CoRec - Court 2",
        start_time="2:00 PM",
        end_time="3:00 PM",
        skill_level="Mid",
        spots_available=0,
        total_spots=10,
        host_name="Marcus W.",
        host_show_up_rate=0.92,
        host_rating=4.1,
        rules="Standard 5v5",
        status="full",
    ),

    # CoRec Court 4 Games (Referenced in Figma Screen 4b: "Nearby courts with advanced games")
    GameSummary(
        game_id="gm_corec_005",
        court_id="mock_place_corec_c4",
        court_name="Purdue CoRec - Court 4",
        start_time="12:00 PM",
        end_time="1:30 PM",
        skill_level="Advanced",
        spots_available=2,
        total_spots=10,
        host_name="Chris P.",
        host_show_up_rate=0.98,
        host_rating=4.7,
        rules="Full court, 2s & 3s",
        status="open",
    ),

    # Pickett Park Games
    GameSummary(
        game_id="gm_pickett_001",
        court_id="mock_place_pickett_1",
        court_name="Pickett Park Basketball Courts",
        start_time="4:30 PM",
        end_time="5:45 PM",
        skill_level="Beginner",
        spots_available=5,
        total_spots=10,
        host_name="Sarah K.",
        host_show_up_rate=0.94,
        host_rating=3.9,
        rules="Casual 3v3 half court",
        status="open",
    ),
    GameSummary(
        game_id="gm_pickett_002",
        court_id="mock_place_pickett_1",
        court_name="Pickett Park Basketball Courts",
        start_time="6:00 PM",
        end_time="7:30 PM",
        skill_level="Advanced",
        spots_available=2,
        total_spots=10,
        host_name="Jordan B.",
        host_show_up_rate=0.96,
        host_rating=4.8,
        rules="Full court run",
        status="open",
    ),

    # Hillenbrand Courts (Referenced in Figma Screen 4b)
    GameSummary(
        game_id="gm_hillenbrand_001",
        court_id="mock_place_hillenbrand",
        court_name="Hillenbrand Hall Courts",
        start_time="5:00 PM",
        end_time="6:00 PM",
        skill_level="Advanced",
        spots_available=1,
        total_spots=6,
        host_name="Trevon M.",
        host_show_up_rate=0.91,
        host_rating=4.5,
        rules="3v3 half court",
        status="open",
    ),

    # Tommy Johnston Park (Live Google Places query match)
    GameSummary(
        game_id="gm_tommy_001",
        court_id="live_tommy_johnston",
        court_name="Tommy Johnston Neighborhood Park Basketball Court",
        start_time="5:30 PM",
        end_time="7:00 PM",
        skill_level="Mid",
        spots_available=4,
        total_spots=10,
        host_name="Chris D.",
        host_show_up_rate=0.93,
        host_rating=4.3,
        rules="Half court 4v4",
        status="open",
    ),
]


def get_mock_campus_courts() -> List[Dict[str, Any]]:
    """Returns the mock Purdue campus court locations."""
    return list(MOCK_CAMPUS_COURTS)


def get_dummy_games_for_court(
    court_name: str,
    only_looking_for_players: bool = False,
    skill_filter: Optional[str] = None,
) -> List[GameSummary]:
    """
    Retrieves scheduled games for a court by name matching.
    Supports real Google Places names (e.g., 'France A. Córdova Recreational Sports Center')
    and campus aliases.
    Optionally filters for games with open spots and/or specific skill levels.
    """
    def facility_key(name: str) -> str:
        s = name.lower().replace("-", " ")
        if any(k in s for k in ("corec", "córdova", "cordova", "recreational sports center", "rec sports")):
            return "corec"
        if "pickett" in s:
            return "pickett"
        if "hillenbrand" in s:
            return "hillenbrand"
        if "blackbird" in s:
            return "blackbird"
        if "tommy johnston" in s or "johnston" in s:
            return "tommy_johnston"
        return s

    q_raw = court_name.lower().replace("-", " ")
    q_fac = facility_key(court_name)
    results = []

    for game in DUMMY_GAMES_DB:
        c_raw = game.court_name.lower().replace("-", " ")
        c_fac = facility_key(game.court_name)

        # Distinguish specific courts if specified (Court 2 vs Court 4)
        if "court 2" in q_raw and "court 2" not in c_raw:
            continue
        if "court 4" in q_raw and "court 4" not in c_raw:
            continue

        is_match = (
            q_fac == c_fac
            or q_raw in c_raw
            or c_raw in q_raw
        )

        if not is_match:
            continue

        if only_looking_for_players and not game.is_looking_for_players:
            continue

        if skill_filter and skill_filter.lower() != "all":
            if game.skill_level.lower() != skill_filter.lower():
                continue

        results.append(game)

    return results
