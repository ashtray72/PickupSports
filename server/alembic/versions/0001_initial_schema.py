"""initial schema: accounts, courts, games, rosters, attendance, recordings, CV stats, ratings

Tables are generated from the SQLAlchemy models in server/app/models.
Database-side rules (triggers, views, NOTIFY) are plain SQL below; they are what
keep the data correct no matter which service writes it.

Revision ID: 0001
Revises:
Create Date: 2026-11-15
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Creation order (parents first); downgrade drops in reverse.
TABLES = ['users', 'courts', 'device_tokens', 'game_blocks', 'player_profiles', 'player_ratings', 'court_calibrations', 'games', 'game_participants', 'rating_history', 'recordings', 'reliability_events', 'detected_tracks', 'processing_jobs', 'shot_events', 'highlight_clips']

ENUM_TYPES = ['court_status', 'game_visibility', 'scoring_rule', 'game_status', 'participant_status', 'attendance_status', 'reliability_kind', 'upload_status', 'job_status', 'review_status']


# --------------------------------------------------------------------------- #
# Database-side rules
# --------------------------------------------------------------------------- #

# Roster count + cap (FR-05 / NFR-02).
# games.participant_count is a deliberate denormalization: a CHECK constraint can
# only see its own row, so the count must live on the game row for the database
# to enforce `participant_count <= player_cap` itself. Only this trigger writes it.
# Because the trigger UPDATEs the game row, concurrent joins to the same game are
# serialized on that row lock, and the CHECK rejects any join past the cap - even
# from raw SQL that skips the CRUD layer's SELECT ... FOR UPDATE.
SQL_PARTICIPANT_COUNT = """
CREATE FUNCTION pickup_sync_participant_count() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    delta integer := 0;
BEGIN
    IF TG_OP = 'INSERT' THEN
        delta := (NEW.status = 'confirmed')::integer;
        IF delta <> 0 THEN
            UPDATE games SET participant_count = participant_count + delta WHERE id = NEW.game_id;
        END IF;
    ELSIF TG_OP = 'DELETE' THEN
        delta := -((OLD.status = 'confirmed')::integer);
        IF delta <> 0 THEN
            UPDATE games SET participant_count = participant_count + delta WHERE id = OLD.game_id;
        END IF;
    ELSE
        IF NEW.game_id <> OLD.game_id THEN
            RAISE EXCEPTION 'game_participants.game_id cannot change'
                USING ERRCODE = 'check_violation';
        END IF;
        delta := (NEW.status = 'confirmed')::integer - (OLD.status = 'confirmed')::integer;
        IF delta <> 0 THEN
            UPDATE games SET participant_count = participant_count + delta WHERE id = NEW.game_id;
        END IF;
    END IF;
    RETURN NULL;
END
$$;

CREATE TRIGGER trg_10_participant_count
AFTER INSERT OR UPDATE OF status, game_id OR DELETE ON game_participants
FOR EACH ROW EXECUTE FUNCTION pickup_sync_participant_count();

-- Nobody but the trigger above may write participant_count.
CREATE FUNCTION pickup_guard_participant_count() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.participant_count <> 0 THEN
            RAISE EXCEPTION 'games.participant_count is maintained by the database; insert games with 0'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.participant_count IS DISTINCT FROM OLD.participant_count AND pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'games.participant_count is maintained by the roster trigger and cannot be set directly'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_games_guard_participant_count
BEFORE INSERT OR UPDATE OF participant_count ON games
FOR EACH ROW EXECUTE FUNCTION pickup_guard_participant_count();
"""

# Live updates (NFR-07). One channel, `game_events`; the payload carries the
# game id so the WebSocket layer can fan out per game. NOTIFY is delivered only
# when the transaction commits, so rolled-back changes never reach clients.
SQL_NOTIFY = """
CREATE FUNCTION pickup_notify_roster() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    r record;
    g record;
BEGIN
    IF TG_OP = 'DELETE' THEN r := OLD; ELSE r := NEW; END IF;
    SELECT participant_count, player_cap INTO g FROM games WHERE id = r.game_id;
    PERFORM pg_notify('game_events', json_build_object(
        'game_id', r.game_id,
        'kind', 'roster_changed',
        'op', lower(TG_OP),
        'user_id', r.user_id,
        'status', CASE WHEN TG_OP = 'DELETE' THEN 'deleted' ELSE r.status::text END,
        'participant_count', g.participant_count,
        'player_cap', g.player_cap
    )::text);
    RETURN NULL;
END
$$;

-- Named to sort after trg_10_participant_count so the payload carries the new count.
CREATE TRIGGER trg_20_participant_notify
AFTER INSERT OR UPDATE OR DELETE ON game_participants
FOR EACH ROW EXECUTE FUNCTION pickup_notify_roster();

CREATE FUNCTION pickup_notify_game_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('game_events', json_build_object(
        'game_id', NEW.id,
        'kind', 'game_updated',
        'status', NEW.status,
        'starts_at', NEW.starts_at,
        'ends_at', NEW.ends_at,
        'participant_count', NEW.participant_count,
        'player_cap', NEW.player_cap
    )::text);
    RETURN NULL;
END
$$;

-- Only details players care about; participant_count changes are already
-- announced by the roster trigger.
CREATE TRIGGER trg_games_notify
AFTER UPDATE ON games
FOR EACH ROW
WHEN ((OLD.court_id, OLD.starts_at, OLD.ends_at, OLD.player_cap, OLD.skill_min, OLD.skill_max,
       OLD.visibility, OLD.scoring_rule, OLD.rules_note, OLD.is_recorded, OLD.status)
      IS DISTINCT FROM
      (NEW.court_id, NEW.starts_at, NEW.ends_at, NEW.player_cap, NEW.skill_min, NEW.skill_max,
       NEW.visibility, NEW.scoring_rule, NEW.rules_note, NEW.is_recorded, NEW.status))
EXECUTE FUNCTION pickup_notify_game_changed();

-- stats_ready (SYS-13) and processing_failed (NFR-08: organizer is notified).
CREATE FUNCTION pickup_notify_job() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    gid uuid;
BEGIN
    SELECT game_id INTO gid FROM recordings WHERE id = NEW.recording_id;
    PERFORM pg_notify('game_events', json_build_object(
        'game_id', gid,
        'kind', CASE WHEN NEW.status = 'succeeded' THEN 'stats_ready' ELSE 'processing_failed' END,
        'job_id', NEW.id,
        'attempts', NEW.attempts
    )::text);
    RETURN NULL;
END
$$;

CREATE TRIGGER trg_processing_jobs_notify
AFTER UPDATE OF status ON processing_jobs
FOR EACH ROW
WHEN (NEW.status IN ('succeeded', 'failed') AND OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION pickup_notify_job();
"""

# FR-14 / SYS-14: only games marked as recorded at creation can have a recording.
SQL_RECORDING_CONSENT = """
CREATE FUNCTION pickup_recording_requires_recorded_game() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM games WHERE id = NEW.game_id AND is_recorded) THEN
        RAISE EXCEPTION 'game % is not marked as recorded; recordings are not allowed (FR-14)', NEW.game_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_recordings_require_recorded_game
BEFORE INSERT OR UPDATE OF game_id ON recordings
FOR EACH ROW EXECUTE FUNCTION pickup_recording_requires_recorded_game();
"""

# Box scores are views, so a self-tag, confirmation or opt-out changes them
# instantly with no recompute step (FR-10). Untagged and opted-out people can
# never appear (SYS-14). Flagged and rejected events are not counted.
SQL_VIEWS = """
CREATE FUNCTION pickup_shot_points(rule scoring_rule, beyond_arc boolean) RETURNS integer
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN rule = 'ones_twos' THEN CASE WHEN beyond_arc THEN 2 ELSE 1 END
        ELSE CASE WHEN beyond_arc THEN 3 ELSE 2 END
    END
$$;

CREATE VIEW player_box_scores AS
SELECT
    t.game_id,
    t.tagged_user_id AS user_id,
    t.id AS track_id,
    t.team,
    count(s.id)::integer                                            AS fga,
    count(s.id) FILTER (WHERE s.made)::integer                      AS fgm,
    count(s.id) FILTER (WHERE NOT s.beyond_arc)::integer            AS two_pa,
    count(s.id) FILTER (WHERE NOT s.beyond_arc AND s.made)::integer AS two_pm,
    count(s.id) FILTER (WHERE s.beyond_arc)::integer                AS three_pa,
    count(s.id) FILTER (WHERE s.beyond_arc AND s.made)::integer     AS three_pm,
    COALESCE(sum(pickup_shot_points(g.scoring_rule, s.beyond_arc)) FILTER (WHERE s.made), 0)::integer AS points,
    avg(s.confidence)                                               AS mean_confidence
FROM detected_tracks t
JOIN game_participants gp ON gp.game_id = t.game_id AND gp.user_id = t.tagged_user_id
JOIN games g ON g.id = t.game_id
LEFT JOIN shot_events s ON s.track_id = t.id AND s.review_status IN ('auto', 'confirmed')
WHERE t.tagged_user_id IS NOT NULL
  AND NOT gp.tracking_opt_out
GROUP BY t.game_id, t.tagged_user_id, t.id, t.team;

-- Team totals are the game score, so they count every counted shot by a team's
-- tracks, tagged or not. They contain no per-person data.
CREATE VIEW game_team_scores AS
SELECT
    t.game_id,
    t.team,
    COALESCE(sum(pickup_shot_points(g.scoring_rule, s.beyond_arc)) FILTER (WHERE s.made), 0)::integer AS points
FROM detected_tracks t
JOIN games g ON g.id = t.game_id
LEFT JOIN shot_events s ON s.track_id = t.id AND s.review_status IN ('auto', 'confirmed')
WHERE t.team IS NOT NULL
GROUP BY t.game_id, t.team;
"""

SQL_DROP_RULES = """
DROP VIEW IF EXISTS game_team_scores;
DROP VIEW IF EXISTS player_box_scores;
DROP FUNCTION IF EXISTS pickup_shot_points(scoring_rule, boolean);
DROP FUNCTION IF EXISTS pickup_recording_requires_recorded_game() CASCADE;
DROP FUNCTION IF EXISTS pickup_notify_job() CASCADE;
DROP FUNCTION IF EXISTS pickup_notify_game_changed() CASCADE;
DROP FUNCTION IF EXISTS pickup_notify_roster() CASCADE;
DROP FUNCTION IF EXISTS pickup_guard_participant_count() CASCADE;
DROP FUNCTION IF EXISTS pickup_sync_participant_count() CASCADE;
"""


def _statements(sql: str) -> list[str]:
    """Split a SQL script on top-level semicolons, ignoring semicolons inside $$ bodies,
    '-- comments' and 'quoted strings'. asyncpg cannot run several commands in one
    prepared statement."""
    out, buf, in_dollar = [], [], False
    i = 0
    while i < len(sql):
        if sql.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        ch = sql[i]
        if not in_dollar and sql.startswith("--", i):
            end = sql.find("\n", i)
            end = len(sql) if end == -1 else end
            buf.append(sql[i:end])
            i = end
            continue
        if not in_dollar and ch == "'":
            end = i + 1
            while end < len(sql):
                if sql[end] == "'" and sql.startswith("''", end):
                    end += 2
                    continue
                if sql[end] == "'":
                    break
                end += 1
            buf.append(sql[i : end + 1])
            i = end + 1
            continue
        if ch == ";" and not in_dollar:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    cleaned = []
    for stmt in out:
        has_code = any(line.strip() and not line.strip().startswith("--") for line in stmt.splitlines())
        if has_code:
            cleaned.append(stmt.strip())
    return cleaned


def _execute_script(sql: str) -> None:
    for stmt in _statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('firebase_uid', sa.String(length=128), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('is_moderator', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("lower(email) LIKE '%@purdue.edu'", name=op.f('ck_users_purdue_email')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('firebase_uid', name=op.f('uq_users_firebase_uid'))
    )
    op.create_index('uq_users_lower_email', 'users', [sa.literal_column('lower(email)')], unique=True)
    op.create_table('courts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('address', sa.String(length=300), nullable=True),
    sa.Column('location', Geography(geometry_type='POINT', srid=4326, spatial_index=False), nullable=False),
    sa.Column('indoor', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('hoop_count', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
    sa.Column('surface', sa.String(length=40), nullable=True),
    sa.Column('layout_notes', sa.Text(), nullable=True),
    sa.Column('google_place_id', sa.String(length=256), nullable=True),
    sa.Column('status', sa.Enum('pending', 'approved', 'rejected', name='court_status'), server_default='pending', nullable=False),
    sa.Column('submitted_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('hoop_count > 0', name=op.f('ck_courts_hoop_count_positive')),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_courts_reviewed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['submitted_by'], ['users.id'], name=op.f('fk_courts_submitted_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_courts')),
    sa.UniqueConstraint('google_place_id', name=op.f('uq_courts_google_place_id'))
    )
    op.create_index('ix_courts_location', 'courts', ['location'], unique=False, postgresql_using='gist')
    op.create_index('ix_courts_location_approved', 'courts', ['location'], unique=False, postgresql_using='gist', postgresql_where=sa.text("status = 'approved'"))
    op.create_table('device_tokens',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token', sa.String(length=4096), nullable=False),
    sa.Column('platform', sa.String(length=16), server_default='ios', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_device_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_device_tokens')),
    sa.UniqueConstraint('token', name=op.f('uq_device_tokens_token'))
    )
    op.create_index(op.f('ix_device_tokens_user_id'), 'device_tokens', ['user_id'], unique=False)
    op.create_table('game_blocks',
    sa.Column('organizer_id', sa.UUID(), nullable=False),
    sa.Column('blocked_user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['blocked_user_id'], ['users.id'], name=op.f('fk_game_blocks_blocked_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organizer_id'], ['users.id'], name=op.f('fk_game_blocks_organizer_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('organizer_id', 'blocked_user_id', name=op.f('pk_game_blocks'))
    )
    op.create_table('player_profiles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('display_name', sa.String(length=80), nullable=False),
    sa.Column('self_assessed_level', sa.Numeric(precision=2, scale=1), nullable=True),
    sa.Column('reliability_score', sa.Numeric(precision=4, scale=3), server_default=sa.text('1.000'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('reliability_score >= 0 AND reliability_score <= 1', name=op.f('ck_player_profiles_reliability_range')),
    sa.CheckConstraint('self_assessed_level IS NULL OR (self_assessed_level >= 1.0 AND self_assessed_level <= 5.0)', name=op.f('ck_player_profiles_self_assessed_range')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_player_profiles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_player_profiles'))
    )
    op.create_table('player_ratings',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('base_rating', sa.Float(), server_default=sa.text('1500'), nullable=False),
    sa.Column('rating', sa.Float(), server_default=sa.text('1500'), nullable=False),
    sa.Column('rd', sa.Float(), server_default=sa.text('350'), nullable=False),
    sa.Column('volatility', sa.Float(), server_default=sa.text('0.06'), nullable=False),
    sa.Column('display_rating', sa.Numeric(precision=2, scale=1), server_default=sa.text('3.0'), nullable=False),
    sa.Column('is_provisional', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('rated_games', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('display_rating >= 1.0 AND display_rating <= 5.0', name=op.f('ck_player_ratings_display_range')),
    sa.CheckConstraint('rated_games >= 0', name=op.f('ck_player_ratings_rated_games_nonnegative')),
    sa.CheckConstraint('rd > 0', name=op.f('ck_player_ratings_rd_positive')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_player_ratings_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_player_ratings'))
    )
    op.create_table('court_calibrations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('court_id', sa.UUID(), nullable=False),
    sa.Column('homography', postgresql.ARRAY(sa.Float()), nullable=False),
    sa.Column('rim_px', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('cardinality(homography) = 9', name=op.f('ck_court_calibrations_homography_3x3')),
    sa.ForeignKeyConstraint(['court_id'], ['courts.id'], name=op.f('fk_court_calibrations_court_id_courts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_court_calibrations_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_court_calibrations'))
    )
    op.create_index(op.f('ix_court_calibrations_court_id'), 'court_calibrations', ['court_id'], unique=False)
    op.create_index('uq_court_calibrations_one_active', 'court_calibrations', ['court_id'], unique=True, postgresql_where=sa.text('is_active'))
    op.create_table('games',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('court_id', sa.UUID(), nullable=False),
    sa.Column('organizer_id', sa.UUID(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('player_cap', sa.SmallInteger(), nullable=False),
    sa.Column('skill_min', sa.Numeric(precision=2, scale=1), nullable=True),
    sa.Column('skill_max', sa.Numeric(precision=2, scale=1), nullable=True),
    sa.Column('visibility', sa.Enum('public', 'request_to_join', 'private', name='game_visibility'), nullable=False),
    sa.Column('invite_token', sa.String(length=64), nullable=True),
    sa.Column('scoring_rule', sa.Enum('ones_twos', 'twos_threes', name='scoring_rule'), nullable=False),
    sa.Column('rules_note', sa.String(length=200), nullable=True),
    sa.Column('is_recorded', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('status', sa.Enum('scheduled', 'in_progress', 'completed', 'cancelled', name='game_status'), server_default='scheduled', nullable=False),
    sa.Column('participant_count', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('stats_finalized_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tags_locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("visibility <> 'private' OR invite_token IS NOT NULL", name=op.f('ck_games_private_needs_invite')),
    sa.CheckConstraint('(skill_min IS NULL AND skill_max IS NULL) OR (skill_min IS NOT NULL AND skill_max IS NOT NULL AND skill_min <= skill_max AND skill_min >= 1.0 AND skill_max <= 5.0)', name=op.f('ck_games_skill_range')),
    sa.CheckConstraint('ends_at > starts_at', name=op.f('ck_games_ends_after_starts')),
    sa.CheckConstraint('participant_count >= 0 AND participant_count <= player_cap', name=op.f('ck_games_participant_count_cap')),
    sa.CheckConstraint('player_cap BETWEEN 2 AND 20', name=op.f('ck_games_player_cap_range')),
    sa.ForeignKeyConstraint(['court_id'], ['courts.id'], name=op.f('fk_games_court_id_courts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organizer_id'], ['users.id'], name=op.f('fk_games_organizer_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_games')),
    sa.UniqueConstraint('invite_token', name=op.f('uq_games_invite_token'))
    )
    op.create_index('ix_games_court_id_starts_at', 'games', ['court_id', 'starts_at'], unique=False)
    op.create_index(op.f('ix_games_organizer_id'), 'games', ['organizer_id'], unique=False)
    op.create_index('ix_games_starts_at_scheduled', 'games', ['starts_at'], unique=False, postgresql_where=sa.text("status = 'scheduled'"))
    op.create_table('game_participants',
    sa.Column('game_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'confirmed', 'left', 'removed', name='participant_status'), nullable=False),
    sa.Column('team', sa.String(length=1), nullable=True),
    sa.Column('tracking_opt_out', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('checked_in_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attendance', sa.Enum('unknown', 'attended', 'no_show', 'late_cancel', name='attendance_status'), server_default='unknown', nullable=False),
    sa.CheckConstraint("team IS NULL OR team IN ('A', 'B')", name=op.f('ck_game_participants_team_values')),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_game_participants_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_game_participants_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('game_id', 'user_id', name=op.f('pk_game_participants'))
    )
    op.create_index('ix_game_participants_user_id', 'game_participants', ['user_id'], unique=False)
    op.create_table('rating_history',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('game_id', sa.UUID(), nullable=False),
    sa.Column('game_ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_rating_history_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_rating_history_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rating_history')),
    sa.UniqueConstraint('user_id', 'game_id', name=op.f('uq_rating_history_user_id_game_id'))
    )
    op.create_index('ix_rating_history_game_id', 'rating_history', ['game_id'], unique=False)
    op.create_index('ix_rating_history_user_order', 'rating_history', ['user_id', 'game_ends_at', 'game_id'], unique=False)
    op.create_table('recordings',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('game_id', sa.UUID(), nullable=False),
    sa.Column('uploader_id', sa.UUID(), nullable=True),
    sa.Column('bucket', sa.String(length=128), nullable=False),
    sa.Column('object_key', sa.String(length=1024), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('duration_s', sa.Float(), nullable=True),
    sa.Column('upload_status', sa.Enum('uploading', 'uploaded', 'failed', name='upload_status'), server_default='uploading', nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delete_after', sa.DateTime(timezone=True), nullable=True),
    sa.Column('raw_deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('sha256 IS NULL OR length(sha256) = 64', name=op.f('ck_recordings_sha256_hex_length')),
    sa.CheckConstraint('size_bytes IS NULL OR size_bytes >= 0', name=op.f('ck_recordings_size_nonnegative')),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_recordings_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['uploader_id'], ['users.id'], name=op.f('fk_recordings_uploader_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recordings')),
    sa.UniqueConstraint('game_id', name=op.f('uq_recordings_game_id'))
    )
    op.create_index('ix_recordings_delete_after_pending', 'recordings', ['delete_after'], unique=False, postgresql_where=sa.text('raw_deleted_at IS NULL'))
    op.create_table('reliability_events',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('game_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Enum('attended', 'no_show', 'late_cancel', 'organizer_cancel', name='reliability_kind'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_reliability_events_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_reliability_events_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reliability_events')),
    sa.UniqueConstraint('user_id', 'game_id', 'kind', name=op.f('uq_reliability_events_user_id_game_id_kind'))
    )
    op.create_index(op.f('ix_reliability_events_game_id'), 'reliability_events', ['game_id'], unique=False)
    op.create_table('detected_tracks',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('game_id', sa.UUID(), nullable=False),
    sa.Column('recording_id', sa.UUID(), nullable=False),
    sa.Column('track_label', sa.Integer(), nullable=False),
    sa.Column('team', sa.String(length=1), nullable=True),
    sa.Column('thumbnail_key', sa.String(length=1024), nullable=True),
    sa.Column('tagged_user_id', sa.UUID(), nullable=True),
    sa.Column('tagged_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("team IS NULL OR team IN ('A', 'B')", name=op.f('ck_detected_tracks_team_values')),
    sa.CheckConstraint('(tagged_user_id IS NULL) = (tagged_at IS NULL)', name=op.f('ck_detected_tracks_tag_fields_together')),
    sa.ForeignKeyConstraint(['game_id', 'tagged_user_id'], ['game_participants.game_id', 'game_participants.user_id'], name='fk_detected_tracks_tagged_participant'),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_detected_tracks_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['recording_id'], ['recordings.id'], name=op.f('fk_detected_tracks_recording_id_recordings'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_detected_tracks')),
    sa.UniqueConstraint('recording_id', 'track_label', name=op.f('uq_detected_tracks_recording_id_track_label'))
    )
    op.create_index('uq_detected_tracks_one_tag_per_game', 'detected_tracks', ['game_id', 'tagged_user_id'], unique=True, postgresql_where=sa.text('tagged_user_id IS NOT NULL'))
    op.create_table('processing_jobs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('recording_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'succeeded', 'failed', name='job_status'), server_default='queued', nullable=False),
    sa.Column('attempts', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('celery_task_id', sa.String(length=255), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('queued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('attempts >= 0 AND attempts <= 3', name=op.f('ck_processing_jobs_attempts_range')),
    sa.ForeignKeyConstraint(['recording_id'], ['recordings.id'], name=op.f('fk_processing_jobs_recording_id_recordings'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_processing_jobs'))
    )
    op.create_index(op.f('ix_processing_jobs_recording_id'), 'processing_jobs', ['recording_id'], unique=False)
    op.create_index('ix_processing_jobs_status', 'processing_jobs', ['status'], unique=False)
    op.create_table('shot_events',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('game_id', sa.UUID(), nullable=False),
    sa.Column('track_id', sa.UUID(), nullable=True),
    sa.Column('t_ms', sa.Integer(), nullable=False),
    sa.Column('court_x', sa.Float(), nullable=False),
    sa.Column('court_y', sa.Float(), nullable=False),
    sa.Column('beyond_arc', sa.Boolean(), nullable=False),
    sa.Column('made', sa.Boolean(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('review_status', sa.Enum('auto', 'flagged', 'confirmed', 'rejected', name='review_status'), nullable=False),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name=op.f('ck_shot_events_confidence_range')),
    sa.CheckConstraint('t_ms >= 0', name=op.f('ck_shot_events_t_ms_nonnegative')),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_shot_events_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_shot_events_reviewed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['track_id'], ['detected_tracks.id'], name=op.f('fk_shot_events_track_id_detected_tracks'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shot_events'))
    )
    op.create_index('ix_shot_events_game_id', 'shot_events', ['game_id'], unique=False)
    op.create_index('ix_shot_events_track_id', 'shot_events', ['track_id'], unique=False)
    op.create_table('highlight_clips',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('game_id', sa.UUID(), nullable=False),
    sa.Column('shot_event_id', sa.UUID(), nullable=False),
    sa.Column('object_key', sa.String(length=1024), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('duration_ms > 0', name=op.f('ck_highlight_clips_duration_positive')),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], name=op.f('fk_highlight_clips_game_id_games'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['shot_event_id'], ['shot_events.id'], name=op.f('fk_highlight_clips_shot_event_id_shot_events'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_highlight_clips')),
    sa.UniqueConstraint('shot_event_id', name=op.f('uq_highlight_clips_shot_event_id'))
    )
    op.create_index(op.f('ix_highlight_clips_game_id'), 'highlight_clips', ['game_id'], unique=False)

    _execute_script(SQL_PARTICIPANT_COUNT)
    _execute_script(SQL_NOTIFY)
    _execute_script(SQL_RECORDING_CONSENT)
    _execute_script(SQL_VIEWS)


def downgrade() -> None:
    _execute_script(SQL_DROP_RULES)
    for table in reversed(TABLES):
        op.drop_table(table)
    for enum_name in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
