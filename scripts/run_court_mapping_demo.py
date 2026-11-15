#!/usr/bin/env python3
"""
PickupSports Mapping Subsystem - CLI Demonstration & Test Harness.
Simulates Figma Wireflow ("Flow 1: Find and Join a Game") on Windows.
"""

from __future__ import annotations
import argparse
import json
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from server.app.services.mapping_service import get_nearby_courts_with_games


def print_banner():
    banner = r"""
  ==============================================================
    PickupSports - Mapping Subsystem Prototype (Team 10)
    Figma Flow 1: "Find and Join a Game" (Windows Test Harness)
  ==============================================================
    """
    print(banner)


def render_summary(geojson_collection):
    meta = geojson_collection.metadata
    user_lat = meta["user_location"]["latitude"]
    user_lon = meta["user_location"]["longitude"]
    radius_m = meta["radius_meters"]
    source = meta["data_source"]

    print(f"[*] User Location: ({user_lat:.4f}, {user_lon:.4f})")
    print(f"[*] Search Radius: {radius_m}m (~{radius_m / 1609.34:.2f} miles, ~{radius_m / 100:.0f} min walk)")
    print(f"[*] Data Source:   {source}")
    print(f"[*] Skill Filter:  {meta.get('skill_filter_applied', 'All')}")
    print(f"[*] Courts Found:  {meta['total_courts_found']}")
    print("-" * 62)

    if not geojson_collection.features:
        print("  [!] No basketball courts found within the specified radius.")
        print("-" * 62)
        return

    for idx, feature in enumerate(geojson_collection.features, 1):
        p = feature.properties
        coords = feature.to_dict()["geometry"]["coordinates"]
        lon, lat = coords[0], coords[1]
        court_type = "Indoor (Hardwood)" if p.indoor else f"Outdoor ({p.surface})"

        print(f"\n[PIN #{idx}] {p.name}")
        print(f"  Coordinates:  [{lat:.4f}, {lon:.4f}]")
        print(f"  Distance:     {p.distance_miles:.2f} mi ({p.distance_km:.2f} km) away")
        print(f"  Facility:     {court_type} | {p.hoop_count} Hoops")
        print(f"  Address:      {p.address}")
        print(f"  Games Today:  {len(p.games)} upcoming ({p.active_games_count} looking for players)")

        if p.games:
            print("  +-------------------------------------------------------------+")
            print("  | Upcoming Games (Figma Court Sheet)                          |")
            print("  +-------------------------------------------------------------+")
            for g in p.games:
                spot_status = f"{g.spots_available} spots left" if g.spots_available > 0 else "FULL"
                host_info = f"Host: {g.host_name} ({int(g.host_show_up_rate * 100)}% show-up, {g.host_rating}*)"
                print(f"  | * {g.start_time} - {g.end_time} | Skill: {g.skill_level:<9} | [{spot_status:<14}]")
                print(f"  |   Rules: {g.rules:<18} | {host_info}")
            print("  +-------------------------------------------------------------+")
        else:
            print("  | (No upcoming pickup games currently scheduled at this court)")

    print("\n" + "=" * 62)


def main():
    parser = argparse.ArgumentParser(
        description="PickupSports Mapping Subsystem Prototype - CLI Demonstration"
    )
    parser.add_argument(
        "--lat",
        type=float,
        default=40.4282,
        help="User latitude (default: 40.4282 - Purdue CoRec)",
    )
    parser.add_argument(
        "--lon",
        type=float,
        default=-86.9222,
        help="User longitude (default: -86.9222 - Purdue CoRec)",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=2500,
        help="Search radius in meters (default: 2500m / ~25 min walk)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="Google Places API Key (optional, can also use GOOGLE_PLACES_API_KEY env var)",
    )
    parser.add_argument(
        "--skill",
        type=str,
        default=None,
        choices=["All", "Beginner", "Mid", "Advanced"],
        help="Filter games by skill level (mirrors Figma skill filter chips)",
    )
    parser.add_argument(
        "--open-only",
        action="store_true",
        help="Only include games that are actively looking for players (spots available > 0)",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="summary",
        choices=["summary", "json"],
        help="Output format: human-readable summary card or RFC 7946 GeoJSON",
    )

    args = parser.parse_args()

    # Query mapping service
    user_coords = (args.lat, args.lon)
    result = get_nearby_courts_with_games(
        user_location=user_coords,
        radius_meters=args.radius,
        api_key=args.api_key,
        only_looking_for_players=args.open_only,
        skill_filter=args.skill,
    )

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print_banner()
        render_summary(result)


if __name__ == "__main__":
    main()
