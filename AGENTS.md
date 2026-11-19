# AGENTS.md: PickupSports

Instructions for AI coding agents (Claude, Gemini, Codex) working in this repo.
`CLAUDE.md` and `GEMINI.md` import this file; edit only this one.

The team develops on **Windows (PowerShell)** and **macOS (zsh)**. Where commands differ, both are
given; check which OS you're on before running anything. Paths in shared commands use forward
slashes, which work on both.

## Project
PickupSports (Purdue ECE 49595, Team 10) is an iOS app for pickup basketball: find and join
games on a court map, record a game with one phone, and get per-player stats from a CV pipeline.

- Planned stack: React Native (iOS) client, FastAPI backend, PostgreSQL + PostGIS, Celery/Redis GPU worker.
- **Exists today:** `server/` (FastAPI app, mapping service, database layer, matching),
  `client/` (Expo nearby-games screen), `database/` (seeds), `scripts/`. `cv_pipeline/` is not created yet.
- Requirements use IDs: TOP-xx, SYS-xx, FR-xx, NFR-xx (see `docs/semester1/`). Reference them in
  code comments, test docstrings and PRs.
- Deeper docs: `docs/semester1/dev_process.md` (process), `docs/prototype_docs/*_walkthrough.md` (subsystems).

## Setup

### 1. Create and activate the virtual environment (once per terminal session)
**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```
If activation is blocked by execution policy, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Or skip activation and replace `python` below
with `.venv\Scripts\python`.

**macOS (zsh)**
```bash
python3 -m venv .venv
source .venv/bin/activate
```
Use `python3` only to create the venv; once it's activated, `python` points to the venv.
Without activation, replace `python` below with `.venv/bin/python`.

### 2. Install, configure, start the database (same on both once the venv is active)
```bash
python -m pip install -r server/requirements.txt
docker compose up -d db                                   # PostgreSQL 17 + PostGIS 3.5
python -m alembic -c server/alembic.ini upgrade head
python scripts/seed_database.py                           # Purdue courts + Figma demo games (idempotent)
```
Copy the env template (never commit `.env`):
- Windows: `copy .env.example .env`
- macOS: `cp .env.example .env`

## Tests
Database tests **skip** when Docker isn't running unless `REQUIRE_DB=1` is set. Always set it, and
don't treat a run with skips as passing.

**Windows (PowerShell)**
```powershell
$env:REQUIRE_DB = "1"
python -m pytest -q
python -m pytest -q --cov=server/app/crud --cov-report=term-missing
```
**macOS (zsh)**
```bash
export REQUIRE_DB=1
python -m pytest -q
python -m pytest -q --cov=server/app/crud --cov-report=term-missing
```
- All tests must pass before a PR. Keep `server/app/crud` coverage at 80% or higher.
- Tests never call real external services (Google Places, Firebase, FCM). Mock them.

## Team workflow
- Branch from **`prototype`** (not `develop`, for now): `feature|fix|refactor/<short-desc>-<firstname>`.
- PR title `FirstName:Task-Name`, target `prototype`, description from `dev_process.md` §4.1. Squash and merge.
- Never push directly to `main`, `develop` or `prototype`. Never fork the repo.
- No secrets in code. Keys come from environment variables (`.env.example` lists them).
- Course rule: every deliverable doc ends with a **GenAI Usage Statement**. New subsystems get
  `docs/prototype_docs/<name>_plan.md` and `<name>_walkthrough.md`.

## Mapping subsystem (owner: Ashton)
Files: `server/app/services/mapping_service.py`, `server/app/schemas/court.py`,
`database/seeds/dummy_games.py`, `scripts/run_court_mapping_demo.py`, `server/tests/test_mapping_service.py`.
```bash
python scripts/run_court_mapping_demo.py                       # Purdue campus center
python scripts/run_court_mapping_demo.py --skill Mid
python scripts/run_court_mapping_demo.py --lat 40.4258 --lon -86.9270 --format json
# other flags: --radius <meters>, --api-key <key>, --open-only
```
- Uses Google Places API (New) Text Search with `GOOGLE_PLACES_API_KEY` from `.env`. To set it for one
  terminal session instead: `$env:GOOGLE_PLACES_API_KEY = "..."` (Windows) or
  `export GOOGLE_PLACES_API_KEY=...` (macOS).
- With no key or on a network error it prints `[NOTICE] Google Places API key not provided or request
  failed. Falling back to Purdue campus mock dataset.` and uses mock courts. Tests check that exact
  text; don't reword it.
- Tests patch `requests.post` and clear the API key. New tests must do the same.
- GeoJSON coordinates are **`[longitude, latitude]`** (RFC 7946). Default radius 2500 m.
- Campus alias matching in `dummy_games.py` links real Place names (e.g. "France A. Córdova
  Recreational Sports Center") to mock games; changing it can break the Figma-flow tests.
- **Current state:** games still come from in-memory `dummy_games.py`, not the database. The planned
  next step is to read from `crud.courts.nearby_courts()` and `crud.games.list_visible_games()`, which
  already return every `GameSummary` field. Database courts link to Places via `courts.google_place_id`.

## Matching subsystem and games API (owner: Soham)
Files: `server/app/services/matching.py` (pure ranking), `server/app/crud/recommendations.py` (feed glue),
`server/app/main.py`, `server/app/api/`, `server/app/schemas/game.py`, `client/src/api/games.ts`,
`server/tests/test_matching.py`, `server/tests/db/test_games_api.py`.
Design: `docs/prototype_docs/matching_subsystem_walkthrough.md`.
```bash
python -m uvicorn server.app.main:app --reload --host 0.0.0.0 --port 8000   # docs at /docs
```
- Eligibility (FR-04) stays in `crud.games.list_visible_games`. The ranker only orders and drops
  games past the 7-day window; it must never add a game back.
- Ranking weights live in `core/config.py` (`match_*`). Change them there, not in `matching.py`.
- `rank_games` must stay deterministic (ties: start time, distance, game id). Tests depend on it.
- Routes that write call `session.commit()` before returning. Domain errors map to HTTP codes in
  `main.py` (`GameFullError` -> 409, `NotPermittedError` -> 403, `NotFoundError` -> 404).
- `X-Dev-User` auth in `api/deps.py` is a prototype stand-in. Replace it with Firebase token
  verification (FR-01.1 / NFR-05.1); don't build features that rely on it.
- API tests use the `committing` fixture because the app opens its own sessions.

## Database subsystem (owner: Donovan)
Files: `server/app/models/` (tables), `server/alembic/versions/` (migrations), `server/app/crud/`
(data access), `server/app/db/` (session, LISTEN/NOTIFY), `server/app/services/rating_engine.py`,
`server/tests/db/`. Design: `docs/prototype_docs/database_subsystem_walkthrough.md`.

Rules that must not be broken:
- **Never edit an applied migration** (e.g. `0001_initial_schema.py`). Change the model, then:
  `python -m alembic -c server/alembic.ini revision --autogenerate -m "<change>"`.
  The `alembic check` test must stay clean.
- **Integrity rules live in the database** (constraints, triggers, views), not only in Python.
- **Never write `games.participant_count`.** A trigger maintains it (deliberate denormalization so a CHECK
  can enforce the roster cap); direct writes are rejected.
- **CRUD functions never commit.** The caller owns the transaction (`async with session.begin(): ...`).
- **Tags, shot reviews and opt-outs go through `crud/stats.py` only.** They recompute ratings in the same
  transaction; writing those columns elsewhere makes stats and ratings disagree.
- Lock rows with `with_for_update=True, populate_existing=True`. Triggers change rows the ORM has cached.
- **No face, appearance or embedding data, ever** (NFR-04 / SYS-21). A test scans the schema for it.
- Rating formulas belong behind `rate_game(states, game_inputs)` in `rating_engine.py` (DEL-06).
  Don't change the replay or locking in `crud/ratings.py` to change a formula.
- Migration SQL with several statements runs through `_execute_script()`; asyncpg can't run multi-statement SQL.

Testing conventions:
- New CRUD behavior gets a test in `server/tests/db/`, using builders in `factories.py`.
- Use the `db` fixture (rolled back) by default; use `committing` only for concurrency, NOTIFY or
  cross-connection checks.
- Rating changes must pass `assert_matches_from_scratch` (`server/tests/db/replay.py`).

Inspect the database (same on both): `docker exec -it pickupsports-db psql -U pickup -d pickup -c "\dt"`

## Platform quirks

### Windows
- Docker Desktop needs **WSL 2** (`wsl --install` as admin, then reboot).
- `zoneinfo` needs the `tzdata` package; without it the seed script falls back to fixed EST
  (wrong during daylight saving time). Fix: `python -m pip install tzdata`.
- Git may warn "LF will be replaced by CRLF". `.gitattributes` keeps text files as LF in the repo
  and in working copies; don't commit whole-file line-ending changes.
- Use `$env:NAME = "value"` for environment variables, not `export` or `set`.

### macOS
- Use `python3` / `pip3` outside the venv; plain `python` may not exist or may be the wrong version.
- **Apple Silicon (M1–M4):** if Docker warns the `postgis/postgis` image doesn't match your platform,
  it's running under emulation. That works, just slower. Turn on Docker Desktop → Settings → General →
  "Use Rosetta for x86/amd64 emulation".
- **Port 5432 already in use:** a local Postgres (Postgres.app or `brew services`) is running. Stop it
  (`brew services stop postgresql`) or quit Postgres.app before `docker compose up`.
- The iOS client (when `client/` exists) only builds and runs in the simulator on macOS with Xcode.
  Windows teammates can't run iOS builds locally.

### Both
- Use forward slashes in paths inside commands and code (`server/alembic.ini`); Python and Git
  accept them on both systems.
- Never hardcode OS-specific paths (like `C:\Users\...`) in code, tests or docs. Use paths relative to
  the repo root.
