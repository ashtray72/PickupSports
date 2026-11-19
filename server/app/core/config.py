"""
Global configuration for the PickupSports backend.

Values come from environment variables (Twelve-Factor), with a lightweight
.env loader for local development. Business-rule constants that the database
layer enforces (reliability threshold, check-in geofence, retention periods)
live here so they are tuned in one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

DEFAULT_DATABASE_URL = "postgresql+asyncpg://pickup:pickup@localhost:5432/pickup"
DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://pickup:pickup@localhost:5432/pickup_test"


def load_env_file() -> None:
    """
    Lightweight .env loader without third-party dependencies.
    Reads KEY=VALUE pairs from .env in repository root into os.environ.
    Existing environment variables always win over .env values.
    """
    env_path = os.path.join(REPO_ROOT, ".env")
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


load_env_file()


@dataclass(frozen=True)
class Settings:
    database_url: str
    test_database_url: str

    # Prototype sign-in (server/app/api/deps.py): trust an `X-Dev-User` header as the
    # player's identity. Anyone can claim to be anyone with it, so it is OFF unless
    # ALLOW_DEV_AUTH=1 is set (local development only, never on a deployed server).
    allow_dev_auth: bool = False

    # SYS-06: organizers below this reliability score cannot create public games,
    # once they have at least `reliability_min_events` attendance events.
    reliability_threshold: float = 0.70
    reliability_min_events: int = 3
    # Leaving (or an organizer cancelling) closer than this to start time counts
    # as a late cancellation.
    late_cancel_window: timedelta = timedelta(hours=2)

    # FR-06: location-based check-in.
    checkin_radius_m: float = 50.0
    checkin_opens_before: timedelta = timedelta(minutes=30)
    checkin_closes_after: timedelta = timedelta(minutes=15)

    # SYS-04: players are provisional until this many rated games.
    provisional_games: int = 3

    # SYS-10: CV events below this confidence are flagged for player confirmation.
    flag_confidence_threshold: float = 0.60

    # NFR-08: processing retries.
    max_job_attempts: int = 3

    # NFR-04: raw video retention; also how long tags stay editable after stats
    # are finalized.
    raw_video_retention: timedelta = timedelta(days=14)
    tag_lock_period: timedelta = timedelta(days=14)

    # FR-12 / SYS-12: game ranking (server/app/services/matching.py).
    # Score = skill * skill fit + distance * distance score + start * start time score.
    # These are starting values from the design trade study; retune from logged joins.
    match_weight_skill: float = 0.5
    match_weight_distance: float = 0.3
    match_weight_start: float = 0.2
    # Provisional players' ratings are rough, so skill fit counts this much less.
    match_provisional_skill_factor: float = 0.5
    # Only games starting within this window are ranked.
    match_window: timedelta = timedelta(days=7)
    # Skill ranges narrower than this (in rating points) are treated as this wide,
    # so a 4.0-4.0 range does not divide by zero.
    match_min_skill_width: float = 1.0
    # Default search radius for the recommended feed (5 miles).
    match_default_radius_m: float = 8046.7


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        test_database_url=os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL),
        allow_dev_auth=os.environ.get("ALLOW_DEV_AUTH", "").strip() == "1",
    )
