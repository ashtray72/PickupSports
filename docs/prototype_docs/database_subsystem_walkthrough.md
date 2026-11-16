# Walkthrough: PickupSports Database Subsystem

## Overview
This adds the database layer for PickupSports (Team 10, ECE 49595 Fall 2026): the PostgreSQL + PostGIS schema, its migrations, and a data-access (CRUD) layer that the FastAPI backend and the CV workers will call. It covers deliverable **DEL-05** and the database side of FR-02, FR-04, FR-05, FR-06, FR-10, FR-12, FR-14, FR-15, NFR-02, NFR-04, NFR-06, NFR-07 and NFR-08.

The guiding rule: **the rules that keep data correct live in the database** (constraints, triggers, views) wherever possible, so they hold no matter who writes, whether that's the API, a CV worker, or a script. The CRUD layer adds the rules that need application logic and gives clean, typed errors.

FastAPI routes are not part of this change. They come next and only need to call `server/app/crud`.

---

## Quick start

```powershell
docker compose up -d db                                   # PostgreSQL 17 + PostGIS 3.5 (creates pickup and pickup_test)
python -m pip install -r server/requirements.txt
copy .env.example .env                                    # DATABASE_URL / TEST_DATABASE_URL defaults match docker-compose
python -m alembic -c server/alembic.ini upgrade head      # create the schema
python scripts/seed_database.py                           # Purdue courts + the Figma "Find and Join a Game" games
python -m pytest -v                                       # whole suite (database tests need the container)
```

If the database isn't running, the database tests are **skipped** with a message telling you to start it. In CI, set `REQUIRE_DB=1` to make that a failure instead.

---

## Changes Implemented

```text
PickupSports/
├── docker-compose.yml                    # PostGIS service, pickup + pickup_test databases
├── pytest.ini                            # pytest-asyncio config, markers (db, slow)
├── database/
│   ├── init.sql                          # creates pickup_test, enables PostGIS
│   └── seeds/purdue_courts.geojson       # RFC 7946 seed courts (from MOCK_CAMPUS_COURTS)
├── scripts/seed_database.py              # idempotent seed: courts, seed users, Figma games
└── server/
    ├── requirements.txt
    ├── alembic.ini, alembic/env.py
    ├── alembic/versions/0001_initial_schema.py   # tables + triggers + views (reversible)
    ├── app/core/config.py                # settings + business-rule constants; .env loader moved here
    ├── app/db/{base,session,notifications}.py
    ├── app/models/{player,court,game,recording,stat}.py
    ├── app/crud/{users,courts,games,attendance,recordings,stats,ratings,errors}.py
    ├── app/services/rating_engine.py     # rate_game() interface + placeholder formula (DEL-06 replaces it)
    └── tests/
        ├── test_rating_engine.py         # engine unit tests (no DB)
        └── db/                           # 122 database tests (sections A-L below)
```

`server/app/services/mapping_service.py` changed by one import: `load_env_file()` now lives in `core/config.py`. The mapping behavior and its 10 tests are unchanged.

---

## Schema (16 tables, 2 views)

| Area | Tables | Key rules enforced by the database |
| --- | --- | --- |
| Accounts | `users`, `player_profiles`, `device_tokens` | `@purdue.edu` only (CHECK), unique Firebase UID and case-insensitive email, exactly one profile per user (PK = user id) |
| Courts | `courts`, `court_calibrations` | `geography(Point, 4326)` with GIST index; `pending / approved / rejected` (pending rows *are* the review queue); one active calibration per court; 3x3 homography |
| Games & rosters | `games`, `game_participants`, `game_blocks` | time/cap/skill-range CHECKs; private games need an invite token; PK `(game_id, user_id)` = no duplicate participants; **roster cap** (below) |
| Attendance | `reliability_events` | one event per (user, game, kind); reliability score recalculated from these |
| Recordings | `recordings`, `processing_jobs` | pointers only (video lives in object storage); a trigger rejects recordings for games not marked as recorded (FR-14); `attempts <= 3` (NFR-08) |
| CV output | `detected_tracks`, `shot_events`, `highlight_clips` | a tag must reference a participant of that game (composite FK); one tagged track per user per game; confidence 0-1; **no face, appearance or embedding columns** (NFR-04) |
| Ratings | `player_ratings`, `rating_history` | one history row per (user, game), so a game can never be counted twice |

**Views:** `player_box_scores` and `game_team_scores`. Box scores are computed from shot events and tags, so a self-tag, confirmation or opt-out shows up instantly with no recompute step. Untagged and opted-out people can never appear in box scores (SYS-14). Flagged (low-confidence) events are not counted until a player confirms them. Team scores are the game score, so they include every counted basket, tagged or not; they contain no per-person data.

### The roster cap, and a deliberate denormalization
Our 3NF design dropped `current_player_count` because it can be calculated from the roster. **`games.participant_count` brings it back on purpose:** a CHECK constraint can only see its own row, so the count has to live on the game row for the database to enforce `participant_count <= player_cap` itself. A trigger on `game_participants` is the only thing allowed to write it (a second trigger rejects any direct write), so it can't drift from the roster. Because that trigger updates the game row, simultaneous joins to the same game queue up on the row lock, and the CHECK rejects anything past the cap. This holds even for raw SQL that skips the CRUD layer. The CRUD layer also locks the game row first (`SELECT ... FOR UPDATE`) so the common case gets a clean `GameFullError`.

### Live updates (NFR-07)
Triggers call `pg_notify('game_events', <json>)` when a roster changes, game details change, stats become ready or change, or processing fails for good. NOTIFY is only delivered on commit, so rolled-back changes never reach clients. `server/app/db/notifications.py` provides `GameEventListener` / `listen_game_events()` for the future WebSocket layer.

### Stats and ratings stay consistent (FR-10 + FR-12)
Every write that can change a finalized game's stats (`tag_track`, `untag_track`, `review_event`, `set_tracking_opt_out`, `finalize_game_stats`) recomputes ratings **in the same transaction**. Either the stat change and the new ratings both commit, or neither does.

- **Reach:** the formula may use teammates' and opponents' ratings, so a change in game G reaches everyone in G, then everyone in later games with those players, and so on until the set stops growing.
- **Locking:** the reach set's `player_ratings` rows are locked in `user_id` order, which prevents deadlocks. After locking, the reach is recalculated; if a game was finalized in between and added players, the locks are released (savepoint rollback) and the larger set is locked again in order.
- **Replay:** games are re-rated in time order from each player's state before G. A later game whose inputs didn't change is skipped (early stop). The result always equals rating every game from scratch; the tests check exactly that.
- **14-day tag lock:** `games.tags_locked_at` = finalization + 14 days. After that, tags and event reviews are rejected with `TagsLockedError`, which keeps recomputes small. **Opting out is never locked** (SYS-14 / NFR-04 have no time limit); it still recomputes ratings.
- The rating formula is a placeholder (team-average Elo weighted by CV confidence) behind `rate_game(states, game_inputs)` in `rating_engine.py`. DEL-06 can replace it with no schema change.

### Retention (NFR-04 / NFR-08)
A recording gets `delete_after = uploaded + 14 days` when the upload finishes, and `processing finished + 14 days` when processing succeeds. A job retries up to 3 times; after the third failure it's marked failed (the organizer is notified via `processing_failed`) and the raw video is kept until its deadline. `crud.recordings.due_for_deletion()` lists what the cleanup job should remove.

---

## Defaults chosen (easy to change in `core/config.py`)
- Pending join requests don't hold a spot; approving one rechecks the cap. The organizer counts as one of the players.
- Public games need reliability >= 0.70, applied once the organizer has at least 3 attendance events. Private games are always allowed.
- Check-in: within 50 m of the court, from 30 minutes before start to 15 minutes after.
- Leaving (or cancelling as organizer) less than 2 hours before start is a late cancellation.
- Shot events below 0.60 confidence are flagged for confirmation.
- Players are provisional until 3 rated games.
- Basketball only, so there's no `sport` column yet (SYS-19); adding one is a single migration.
- Moderators (`users.is_moderator`) approve or reject submitted courts.

---

## Verification & Test Results

```powershell
docker compose up -d db
$env:REQUIRE_DB = "1"
python -m pytest -q --cov=server/app/crud --cov-report=term-missing
```

**Result: 139 passed** (122 database tests + 7 rating-engine unit tests + the existing 10 mapping tests), on two consecutive full runs. **Line coverage of `server/app/crud`: 95%** (target was 80%).

| Section | What it proves |
| --- | --- |
| A. Migrations | upgrade on an empty DB; `downgrade base` then `upgrade head` leaves no tables, functions or enum types behind; `alembic check` finds no drift between models and migration |
| B. Constraints | every CHECK / UNIQUE / FK / trigger rule rejects bad rows written with **raw SQL** (Purdue email, game times, skill ranges, private invite, duplicate participants, direct `participant_count` writes, cap overflow, tags to non-participants, confidence, attempts, duplicate rating history, recordings for unrecorded games) |
| C. Spatial | only approved courts, within radius, closest first; distances within 0.5% of the mapping service's haversine; the GIST index is used; GeoJSON is `[lon, lat]`; review queue and moderator approval |
| D. Games & rosters | join / full / leave / rejoin; request-to-join and approval; blocks; private invite links; TC-FR04 skill visibility (2.5 / 3.8 / 4.6 players); provisional players; reliability-gated public games; cancellations; court-sheet fields (`spots_available`, host show-up rate and rating, skill label) |
| E. Concurrency | 50 simultaneous joins to a 10-cap game, 20 times: exactly 10 on the roster, no duplicates, every extra join gets "game full"; the same holds for raw SQL with no locking; one user joining 5 times at once gets one row |
| F. Live signals | join, leave and start-time edit reach a listener within 2 s with the right count; 10 listeners all get it; a rolled-back join sends nothing; `stats_ready`, `stats_updated`, `processing_failed` |
| G. Attendance | check-in at 40 m succeeds, 60 m fails; window edges; non-participants rejected; no-shows marked after the window; reliability scores 1.0 / 0.667 / 0.5 as history accumulates |
| H. Stats & tagging | box scores and team scores under both scoring rules; retag moves stats instantly; untagged and opted-out excluded (also from highlight clips); flagged events count only once confirmed |
| I. Recordings & privacy | retries then failure with raw video kept; 14-day deletion deadlines; schema scan finds no face/appearance/embedding columns; no facial-recognition libraries in `server/` |
| J. Ratings | same-transaction visibility from a second connection; a failing rating engine rolls back the tag too; retag in an old game == from-scratch replay; change spreads A→B→C→D but not to unrelated players; early stop; exactly the reach set is locked; the calculate-vs-lock race picks up a newly finalized game; overlapping recomputes don't deadlock; 14-day tag lock; opt-out works after the lock; no double counting; provisional after 3 games |
| K. Seeds | idempotent; CoRec Court 2 has 4 games with 4 / 3 / 0 / 0 spots, as in Figma |
| L. Performance | with all seeded courts + 200 games, p95 over 50 runs: `nearby_courts` **2.5 ms**, `list_visible_games` **13 ms** (limit 100 ms, the database's share of NFR-06's 2 s map load) |

### Notes for reviewers
- The "locks exactly the reach set" test probes from a second connection with `FOR UPDATE SKIP LOCKED`, because PostgreSQL doesn't list individual row locks in `pg_locks`.
- The Tommy Johnston game from the mock data isn't seeded: the mock data has no coordinates for that court.
- `rating_history.rated_at` uses `clock_timestamp()` so the tests can tell exactly which rows a recompute rewrote, even inside one transaction.
- CRUD functions never commit. The caller owns the transaction (`async with session.begin(): ...`), which is what makes multi-step changes all-or-nothing.

---

## GenAI Usage Statement
Generative AI (Claude Code) was used to help design the schema and test plan, write the models, migration, data-access layer, tests and this walkthrough, and run the test suite. Donovan reviewed the plan and directed design changes (same-transaction rating updates, transitive replay, the 14-day tag lock with an unlocked opt-out, the recheck after locking, and documenting the participant-count denormalization).
