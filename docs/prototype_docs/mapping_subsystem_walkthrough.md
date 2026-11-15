# Walkthrough: PickupSports Mapping Subsystem Prototype

## Overview
We implemented the **Mapping Subsystem** for the PickupSports prototype (Team 10, ECE 49595 Fall 2026). This module fulfills functional requirement **FR-02** and non-functional requirement **NFR-06** under Design Alternative **DA-1** (Direct Native SDK Integration with GeoJSON Endpoints).

The subsystem takes a user's location as a `(latitude, longitude)` coordinate tuple and search radius, queries the **Google Places API** for physical basketball courts, merges this data with scheduled pickup games looking for players from the internal PickupSports database (mocked for this prototype), and returns an **IETF RFC 7946-compliant GeoJSON FeatureCollection**.

---

## Changes Implemented

```text
PickupSports/
├── database/
│   └── seeds/
│       └── dummy_games.py             # Mock campus courts & dummy pickup games (Figma screens)
├── scripts/
│   └── run_court_mapping_demo.py      # Interactive CLI test harness & GeoJSON dumper
└── server/
    ├── app/
    │   ├── schemas/
    │   │   └── court.py               # RFC 7946 GeoJSON & Game Pydantic/dataclass models
    │   └── services/
    │       └── mapping_service.py     # Core Google Places API client + Haversine spatial matcher
    └── tests/
        └── test_mapping_service.py    # 10 unit tests (RFC 7946, distance, API mocks, filters)
```

---

## Key Features

1. **Location Tuple & Search Radius:**
   - Accepts user phone coordinates as `(latitude, longitude)`.
   - Default search radius set to **2500 meters** (~25-minute walk / ~1.55 miles), with caller overrides.
   - Strict physical coordinate bounds validation ($-90 \le \text{lat} \le 90$, $-180 \le \text{lon} \le 180$).

2. **Google Places API (New) Integration:**
   - Upgraded to the modern **Google Places API (New)** Text Search endpoint:
     `POST https://places.googleapis.com/v1/places:searchText`
   - Configured with required headers:
     - `Content-Type: application/json`
     - `X-Goog-Api-Key: <api_key>`
     - `X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,places.location,places.currentOpeningHours`
   - Submits `textQuery: "basketball court"` with user-centered `locationBias` circle.
   - Enhanced facility aliasing to match live Google Place names (such as `"France A. Córdova Recreational Sports Center"` and `"Tommy Johnston Neighborhood Park Basketball Court"`) directly to scheduled pickup games in the dummy database.
   - If the API key is not provided or network fails, it prints:
     ```text
     [NOTICE] Google Places API key not provided or request failed. Falling back to Purdue campus mock dataset.
     ```
     and seamlessly loads the Purdue campus court dataset.

3. **Haversine Distance & Spatial Aggregation:**
   - Computes great-circle distances using the Haversine formula:
     $$d = 2R \arcsin\left(\sqrt{\sin^2\left(\frac{\Delta\phi}{2}\right) + \cos\phi_1\cos\phi_2\sin^2\left(\frac{\Delta\lambda}{2}\right)}\right)$$
   - Sorts courts ascending by distance from user.

4. **PickupSports Dummy Database Integration (Figma Wireflow):**
   - Matches the Figma wireflow ("Flow 1: Find and Join a Game"):
     - **CoRec Court 2:** 4 scheduled games (11:00 AM Beginner with 4 spots, 11:30 AM Mid with 3 spots hosted by Mike R., and full games at 1:00 PM and 2:00 PM).
     - **Pickett Park Courts:** 4:30 PM Beginner (5 spots), 6:00 PM Advanced (2 spots).
     - **Hillenbrand Hall Courts:** 5:00 PM Advanced (1 spot).
   - Supports filtering games by skill level (`"Beginner"`, `"Mid"`, `"Advanced"`, `"All"`) and open spots (`only_looking_for_players=True`).

5. **IETF RFC 7946 GeoJSON Compliance:**
   - Serializes coordinates in standard `[longitude, latitude]` order.
   - Bundles rich metadata for mobile rendering (`court_id`, `distance_miles`, `indoor`, `hoop_count`, `active_games_count`, `games`).

---

## Verification & Test Results

### 1. Automated Unit Tests (`test_mapping_service.py`)
Executed 10 unit tests covering spatial calculations, coordinate validation, GeoJSON structure, Figma game matching, skill filtering, and Google Places API HTTP mocking:

```powershell
& "C:\Users\ashto\AppData\Local\Python\bin\python.exe" -m unittest discover -s server/tests -p "test_*.py" -v
```

**Results:**
```text
test_coordinate_validation (test_mapping_service.TestMappingSubsystem) ... ok
test_dummy_game_merging_matches_figma_wireflow (test_mapping_service.TestMappingSubsystem) ... ok
test_fallback_notice_when_api_key_missing (test_mapping_service.TestMappingSubsystem) ... ok
test_google_places_api_network_failure_fallback (test_mapping_service.TestMappingSubsystem) ... ok
test_google_places_api_successful_response (test_mapping_service.TestMappingSubsystem) ... ok
test_haversine_distance_accuracy (test_mapping_service.TestMappingSubsystem) ... ok
test_only_looking_for_players_flag (test_mapping_service.TestMappingSubsystem) ... ok
test_radius_validation (test_mapping_service.TestMappingSubsystem) ... ok
test_rfc7946_geojson_structure (test_mapping_service.TestMappingSubsystem) ... ok
test_skill_filter_chip_interaction (test_mapping_service.TestMappingSubsystem) ... ok

----------------------------------------------------------------------
Ran 10 tests in 0.009s

OK
```

---

### 2. CLI Demonstration & Test Harness (`run_court_mapping_demo.py`)

#### Live Google Places API (New) Query
```powershell
& "C:\Users\ashto\AppData\Local\Python\bin\python.exe" scripts/run_court_mapping_demo.py
```
```text
  ==============================================================
    PickupSports - Mapping Subsystem Prototype (Team 10)
    Figma Flow 1: "Find and Join a Game" (Windows Test Harness)
  ==============================================================
    
[*] User Location: (40.4282, -86.9222)
[*] Search Radius: 2500m (~1.55 miles, ~25 min walk)
[*] Data Source:   Google Places API
[*] Skill Filter:  All
[*] Courts Found:  7
--------------------------------------------------------------

[PIN #1] France A. Córdova Recreational Sports Center
  Coordinates:  [40.4282, -86.9224]
  Distance:     0.01 mi (0.01 km) away
  Facility:     Outdoor (Asphalt) | 2 Hoops
  Address:      355 N Martin Jischke Dr, West Lafayette, IN 47906, USA
  Games Today:  5 upcoming (3 looking for players)
  +-------------------------------------------------------------+
  | Upcoming Games (Figma Court Sheet)                          |
  +-------------------------------------------------------------+
  | * 11:00 AM - 12:00 PM | Skill: Beginner  | [4 spots left  ]
  |   Rules: 1s & 2s to 15      | Host: Alex T. (90% show-up, 3.8*)
  | * 11:30 AM - 12:30 PM | Skill: Mid       | [3 spots left  ]
  |   Rules: 1s & 2s, half court | Host: Mike R. (95% show-up, 4.2*)
  | * 1:00 PM - 2:00 PM | Skill: Mid       | [FULL          ]
  |   Rules: 1s & 2s            | Host: David L. (88% show-up, 4.0*)
  | * 2:00 PM - 3:00 PM | Skill: Mid       | [FULL          ]
  |   Rules: Standard 5v5       | Host: Marcus W. (92% show-up, 4.1*)
  | * 12:00 PM - 1:30 PM | Skill: Advanced  | [2 spots left  ]
  |   Rules: Full court, 2s & 3s | Host: Chris P. (98% show-up, 4.7*)
  +-------------------------------------------------------------+

[PIN #2] TREC (Turf Recreation Exercise Center)
[PIN #3] Loeb Fountain
[PIN #4] Mackey Arena
[PIN #5] Tommy Johnston Neighborhood Park Basketball Court
[PIN #6] Faith West Community Center
[PIN #7] Lyboult Sports Park
```

#### Skill Filter View (Figma Screen 4: Mid Level Games)
```powershell
& "C:\Users\ashto\AppData\Local\Python\bin\python.exe" scripts/run_court_mapping_demo.py --skill Mid
```
Only displays Mid level games (11:30 AM, 1:00 PM, 2:00 PM) at CoRec.

#### RFC 7946 GeoJSON View
```powershell
& "C:\Users\ashto\AppData\Local\Python\bin\python.exe" scripts/run_court_mapping_demo.py --format json
```
Emits standard GeoJSON FeatureCollection ready for consumption by mobile map markers and bottom sheets.
