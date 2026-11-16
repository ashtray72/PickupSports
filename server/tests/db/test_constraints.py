"""
B. Database constraints, each tested with raw SQL that bypasses the CRUD layer,
so the rules hold for every writer (API, CV workers, scripts).
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from server.tests.db import factories as f

CHECK = "23514"
UNIQUE = "23505"
FOREIGN_KEY = "23503"


def _sqlstate(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", exc)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        code = getattr(candidate, "sqlstate", None)
        if code:
            return code
    return None


async def expect_violation(db, sql: str, params: dict | None = None, code: str = CHECK) -> None:
    with pytest.raises(sa.exc.DBAPIError) as info:
        async with db.begin_nested():
            await db.execute(sa.text(sql), params or {})
    assert _sqlstate(info.value) == code, f"expected SQLSTATE {code}, got {_sqlstate(info.value)}: {info.value}"


# --- accounts ------------------------------------------------------------- #

async def test_non_purdue_email_rejected(db):
    await expect_violation(db, "INSERT INTO users (firebase_uid, email) VALUES ('u1', 'someone@gmail.com')")


async def test_purdue_email_case_insensitive_and_unique(db):
    await db.execute(sa.text("INSERT INTO users (firebase_uid, email) VALUES ('u1', 'Pete@Purdue.edu')"))
    await expect_violation(
        db, "INSERT INTO users (firebase_uid, email) VALUES ('u2', 'pete@purdue.edu')", code=UNIQUE
    )


async def test_duplicate_firebase_uid_rejected(db):
    await db.execute(sa.text("INSERT INTO users (firebase_uid, email) VALUES ('dup', 'a@purdue.edu')"))
    await expect_violation(
        db, "INSERT INTO users (firebase_uid, email) VALUES ('dup', 'b@purdue.edu')", code=UNIQUE
    )


async def test_second_profile_for_user_rejected(db):
    u = await f.user(db)
    await expect_violation(
        db, "INSERT INTO player_profiles (user_id, display_name) VALUES (:u, 'again')", {"u": u.id}, UNIQUE
    )


# --- courts --------------------------------------------------------------- #

async def test_hoop_count_must_be_positive(db):
    await expect_violation(
        db,
        "INSERT INTO courts (name, location, hoop_count) "
        "VALUES ('x', ST_GeogFromText('SRID=4326;POINT(-86.92 40.42)'), 0)",
    )


async def test_calibration_must_be_3x3_and_one_active(db):
    c = await f.court(db)
    await expect_violation(
        db,
        "INSERT INTO court_calibrations (court_id, homography) VALUES (:c, ARRAY[1,0,0,0,1,0,0,0]::float8[])",
        {"c": c.id},
    )
    nine = "ARRAY[1,0,0,0,1,0,0,0,1]::float8[]"
    await db.execute(sa.text(f"INSERT INTO court_calibrations (court_id, homography) VALUES (:c, {nine})"), {"c": c.id})
    await expect_violation(
        db, f"INSERT INTO court_calibrations (court_id, homography) VALUES (:c, {nine})", {"c": c.id}, UNIQUE
    )


# --- games ---------------------------------------------------------------- #

async def _game_insert_sql(**overrides) -> tuple[str, dict]:
    cols = {
        "starts_at": "now() + interval '1 day'",
        "ends_at": "now() + interval '1 day 1 hour'",
        "player_cap": "10",
        "skill_min": "NULL",
        "skill_max": "NULL",
        "visibility": "'public'",
        "invite_token": "NULL",
        "scoring_rule": "'ones_twos'",
        "participant_count": "0",
    }
    cols.update(overrides)
    names = ", ".join(cols)
    values = ", ".join(cols.values())
    return f"INSERT INTO games (court_id, organizer_id, {names}) VALUES (:c, :o, {values})", {}


async def _ids(db):
    return {"c": (await f.court(db)).id, "o": (await f.user(db)).id}


@pytest.mark.parametrize(
    "overrides",
    [
        {"ends_at": "now() + interval '1 day'"},  # ends == starts
        {"ends_at": "now()"},  # ends before starts
        {"skill_min": "4.0", "skill_max": "3.0"},
        {"skill_min": "3.0"},  # only one end of the range
        {"skill_max": "3.0"},
        {"visibility": "'private'"},  # private without invite token
        {"player_cap": "1"},
        {"player_cap": "25"},
    ],
    ids=["ends_eq_starts", "ends_before_starts", "skill_min_gt_max", "only_min", "only_max",
         "private_no_invite", "cap_too_small", "cap_too_big"],
)
async def test_invalid_games_rejected(db, overrides):
    sql, _ = await _game_insert_sql(**overrides)
    await expect_violation(db, sql, await _ids(db))


async def test_game_cannot_be_inserted_with_participant_count(db):
    sql, _ = await _game_insert_sql(participant_count="5")
    await expect_violation(db, sql, await _ids(db))


async def test_participant_count_cannot_be_set_directly(db):
    g = await f.game(db)
    await expect_violation(db, "UPDATE games SET participant_count = 0 WHERE id = :g", {"g": g.id})
    await expect_violation(db, "UPDATE games SET participant_count = 99 WHERE id = :g", {"g": g.id})


async def test_duplicate_participant_rejected(db):
    g = await f.game(db)
    u = await f.user(db)
    sql = "INSERT INTO game_participants (game_id, user_id, status) VALUES (:g, :u, 'confirmed')"
    await db.execute(sa.text(sql), {"g": g.id, "u": u.id})
    await expect_violation(db, sql, {"g": g.id, "u": u.id}, UNIQUE)


async def test_raw_inserts_cannot_exceed_cap(db):
    g = await f.game(db, cap=3)  # organizer holds 1 spot
    sql = "INSERT INTO game_participants (game_id, user_id, status) VALUES (:g, :u, 'confirmed')"
    for _ in range(2):
        await db.execute(sa.text(sql), {"g": g.id, "u": (await f.user(db)).id})
    await expect_violation(db, sql, {"g": g.id, "u": (await f.user(db)).id})
    count = await db.scalar(sa.text("SELECT participant_count FROM games WHERE id = :g"), {"g": g.id})
    assert count == 3


async def test_cap_cannot_drop_below_roster(db):
    g = await f.game(db, cap=4)
    await f.add_confirmed(db, g, await f.user(db))
    await expect_violation(db, "UPDATE games SET player_cap = 1 WHERE id = :g", {"g": g.id})


async def test_participant_count_follows_status_changes(db):
    g = await f.game(db)
    u = await f.user(db)
    await db.execute(
        sa.text("INSERT INTO game_participants (game_id, user_id, status) VALUES (:g, :u, 'pending')"),
        {"g": g.id, "u": u.id},
    )
    count = lambda: db.scalar(sa.text("SELECT participant_count FROM games WHERE id = :g"), {"g": g.id})  # noqa: E731
    assert await count() == 1  # organizer only: pending doesn't take a spot
    await db.execute(sa.text("UPDATE game_participants SET status = 'confirmed' WHERE user_id = :u"), {"u": u.id})
    assert await count() == 2
    await db.execute(sa.text("UPDATE game_participants SET status = 'left' WHERE user_id = :u"), {"u": u.id})
    assert await count() == 1
    await db.execute(sa.text("UPDATE game_participants SET status = 'confirmed' WHERE user_id = :u"), {"u": u.id})
    await db.execute(sa.text("DELETE FROM game_participants WHERE user_id = :u"), {"u": u.id})
    assert await count() == 1


# --- CV output and stats -------------------------------------------------- #

async def _recorded(db):
    a, b = await f.user(db), await f.user(db)
    rg = await f.recorded_game(db, [(a, "A"), (b, "B")], extra_tracks=[(7, "A")], tag=False, finalize=False)
    return rg, a, b


async def test_track_can_only_be_tagged_to_a_participant(db):
    rg, _a, _b = await _recorded(db)
    stranger = await f.user(db)
    await expect_violation(
        db,
        "UPDATE detected_tracks SET tagged_user_id = :u, tagged_at = now() WHERE id = :t",
        {"u": stranger.id, "t": rg.tracks[7]},
        FOREIGN_KEY,
    )


async def test_user_tagged_on_at_most_one_track_per_game(db):
    rg, a, _b = await _recorded(db)
    sql = "UPDATE detected_tracks SET tagged_user_id = :u, tagged_at = now() WHERE id = :t"
    await db.execute(sa.text(sql), {"u": a.id, "t": rg.tracks[a.id]})
    await expect_violation(db, sql, {"u": a.id, "t": rg.tracks[7]}, UNIQUE)


async def test_tag_needs_timestamp(db):
    rg, a, _b = await _recorded(db)
    await expect_violation(
        db, "UPDATE detected_tracks SET tagged_user_id = :u WHERE id = :t", {"u": a.id, "t": rg.tracks[a.id]}
    )


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
async def test_shot_confidence_must_be_0_to_1(db, confidence):
    rg, a, _b = await _recorded(db)
    await expect_violation(
        db,
        "INSERT INTO shot_events (game_id, track_id, t_ms, court_x, court_y, beyond_arc, made, confidence, review_status) "
        "VALUES (:g, :t, 0, 0, 0, false, true, :c, 'auto')",
        {"g": rg.game.id, "t": rg.tracks[a.id], "c": confidence},
    )


async def test_job_attempts_capped_at_3(db):
    rg, _a, _b = await _recorded(db)
    await expect_violation(db, "UPDATE processing_jobs SET attempts = 4 WHERE id = :j", {"j": rg.job.id})


async def test_duplicate_rating_history_rejected(db):
    a, b = await f.user(db), await f.user(db)
    rg = await f.two_team_game(db, [a], [b], a_points=2, b_points=1, starts_at=f.at(days=-2))
    await expect_violation(
        db,
        "INSERT INTO rating_history (user_id, game_id, game_ends_at, before, after) "
        "VALUES (:u, :g, now(), '{}'::jsonb, '{}'::jsonb)",
        {"u": a.id, "g": rg.game.id},
        UNIQUE,
    )


async def test_recording_requires_recorded_game(db):
    g = await f.game(db, recorded=False)
    await expect_violation(
        db, "INSERT INTO recordings (game_id, bucket, object_key) VALUES (:g, 'b', 'k')", {"g": g.id}
    )


async def test_recording_allowed_for_recorded_game(db):
    g = await f.game(db, recorded=True)
    await db.execute(
        sa.text("INSERT INTO recordings (game_id, bucket, object_key) VALUES (:g, 'b', 'k')"), {"g": g.id}
    )
    assert await db.scalar(sa.text("SELECT count(*) FROM recordings WHERE game_id = :g"), {"g": g.id}) == 1


async def test_unknown_game_participant_rejected(db):
    u = await f.user(db)
    await expect_violation(
        db,
        "INSERT INTO game_participants (game_id, user_id, status) VALUES (:g, :u, 'confirmed')",
        {"g": uuid.uuid4(), "u": u.id},
        FOREIGN_KEY,
    )
