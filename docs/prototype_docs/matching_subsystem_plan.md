# Implementation Plan: Matching Subsystem and Games API

## Goal
Give the "Finding and Joining a Game" flow a real backend:

1. Rank the games a player is allowed to see, best match first (FR-12 / SYS-12).
2. Serve that feed and a join action over HTTP so the app stops reading mock data (FR-04, FR-05).
3. Let the app filter the feed by skill level, and fall back to demo data when the server is down.

## Design decisions
- **Algorithm:** D1 Filter-and-Score from the matching Design Alternatives trade study (57/60).
  The database query filters, a pure Python function scores and sorts. D2 (auto-match queue) and
  D3 (learned ranker) need player volume or training data we won't have at launch.
- **Filter stays in the database.** `crud.games.list_visible_games` (Donovan) already applies FR-04.
  The ranker can only reorder games and drop ones past the 7-day window.
- **Weights in config.** `match_weight_skill/distance/start` = 0.5 / 0.3 / 0.2 in `core/config.py`.
- **Framework:** FastAPI, as planned in the SDP and `dev_process.md`. Uvicorn runs it locally.
- **Auth for the prototype:** an `X-Dev-User` header with a Firebase UID, until Firebase token
  verification is built.
- **Branch:** `feature/game-matching-soham` off `prototype`.

## Work items
| Item | Files | Verified by |
| ---- | ----- | ----------- |
| Pure ranker | `server/app/services/matching.py` | `server/tests/test_matching.py` (27 tests) |
| Feed glue (filter, skill chip, roster averages) | `server/app/crud/recommendations.py` | `server/tests/db/test_games_api.py` |
| FastAPI app, routes, schemas | `server/app/main.py`, `server/app/api/`, `server/app/schemas/game.py` | `server/tests/db/test_games_api.py` (20 tests) |
| Demo player in the seed | `scripts/seed_database.py` | `server/tests/db/test_seed.py` |
| App API client, skill chips, fallback | `client/src/api/games.ts`, `client/src/screens/NearbyGamesScreen.tsx` | `client/__tests__/NearbyGamesFeed.test.tsx` (10 tests) |

## Out of scope for this branch
Join button and confirm step in the app, waitlist, device location, Firebase auth, map pins.

## GenAI Usage Statement
Claude (Anthropic) drafted this plan from the D1 design chosen in the matching algorithm Design
Alternatives exercise and the existing repository code.
