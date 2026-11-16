"""
Live game events from PostgreSQL LISTEN/NOTIFY (NFR-07).

Database triggers publish JSON payloads on the `game_events` channel whenever a
roster changes, a game's details change, stats become ready or change, or
processing fails. Payloads always carry `game_id` and `kind`:

    roster_changed     {user_id, status, op, participant_count, player_cap}
    game_updated       {status, starts_at, ends_at, participant_count, player_cap}
    stats_ready        {job_id, attempts}
    processing_failed  {job_id, attempts}
    stats_updated      {}

The future WebSocket layer holds one listener per server process and fans each
event out to the clients viewing that game. Uses a dedicated asyncpg
connection (LISTEN can't run on a pooled connection).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Optional

import asyncpg

from server.app.core.config import get_settings
from server.app.db.session import asyncpg_dsn

CHANNEL = "game_events"


class GameEventListener:
    """
    async with GameEventListener() as listener:
        event = await listener.get(timeout=2.0)
    """

    def __init__(self, dsn: Optional[str] = None, channel: str = CHANNEL) -> None:
        self._dsn = dsn or asyncpg_dsn(get_settings().database_url)
        self._channel = channel
        self._conn: Optional[asyncpg.Connection] = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        self._queue.put_nowait(json.loads(payload))

    async def __aenter__(self) -> "GameEventListener":
        self._conn = await asyncpg.connect(asyncpg_dsn(self._dsn))
        await self._conn.add_listener(self._channel, self._on_notify)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._conn is not None:
            try:
                await self._conn.remove_listener(self._channel, self._on_notify)
            finally:
                await self._conn.close()
                self._conn = None

    async def get(self, timeout: Optional[float] = None) -> dict[str, Any]:
        """Next event; raises asyncio.TimeoutError if none arrives in `timeout` seconds."""
        return await asyncio.wait_for(self._queue.get(), timeout)

    def pending(self) -> list[dict[str, Any]]:
        """Drain events that have already arrived, without waiting."""
        out = []
        while not self._queue.empty():
            out.append(self._queue.get_nowait())
        return out


async def listen_game_events(dsn: Optional[str] = None) -> AsyncIterator[dict[str, Any]]:
    """Yield game events forever (for a long-running WebSocket fan-out task)."""
    async with GameEventListener(dsn) as listener:
        while True:
            yield await listener.get()
