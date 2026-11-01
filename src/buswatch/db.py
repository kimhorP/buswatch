"""SQLite persistence: settings + seat_history."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from buswatch.models import Direction, NormalizedTrip

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    chat_id       INTEGER PRIMARY KEY,
    direction     TEXT NOT NULL,
    company       TEXT,
    watch_date    TEXT,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    chat_id     INTEGER PRIMARY KEY,
    name        TEXT,
    username    TEXT,
    first_seen  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_history (
    id              INTEGER PRIMARY KEY,
    chat_id         INTEGER NOT NULL,
    company         TEXT NOT NULL,
    date            TEXT NOT NULL,
    direction       TEXT NOT NULL,
    depart_time     TEXT NOT NULL,
    vehicle         TEXT NOT NULL,
    seats_available INTEGER NOT NULL,
    seats_total     INTEGER,
    price           REAL,
    currency        TEXT,
    source_trip_id  TEXT,
    polled_at       TEXT NOT NULL,
    alerted         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_history_lookup
    ON seat_history (chat_id, company, date, direction, depart_time, vehicle, polled_at);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        import os

        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        # migrate pre-watch_date databases
        async with self.conn.execute("PRAGMA table_info(settings)") as cur:
            cols = {row["name"] for row in await cur.fetchall()}
        for col in ("watch_date", "company"):
            if col not in cols:
                await self.conn.execute(f"ALTER TABLE settings ADD COLUMN {col} TEXT")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected — call connect() first")
        return self._conn

    # --- settings ---

    async def get_direction(self, chat_id: int) -> Direction | None:
        async with self.conn.execute(
            "SELECT direction FROM settings WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
        return Direction(row["direction"]) if row else None

    async def set_direction(
        self,
        chat_id: int,
        direction: Direction,
        watch_date: str | None = None,
        company: str | None = None,
    ) -> None:
        """Upsert a user's watch. watch_date is ISO (YYYY-MM-DD) or None (today+tomorrow).
        company is None to watch all companies, else a company name."""
        await self.conn.execute(
            """
            INSERT INTO settings (chat_id, direction, company, watch_date, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE
                SET direction = excluded.direction,
                    company = excluded.company,
                    watch_date = excluded.watch_date,
                    updated_at = excluded.updated_at
            """,
            (chat_id, direction.value, company, watch_date, _now_iso()),
        )
        await self.conn.commit()

    async def delete_direction(self, chat_id: int) -> bool:
        """Remove a user's watch. Returns True if a row was deleted."""
        cur = await self.conn.execute("DELETE FROM settings WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    # --- users ---

    async def upsert_user(self, chat_id: int, name: str | None, username: str | None) -> None:
        """Record/refresh a user who interacted with the bot."""
        await self.conn.execute(
            """
            INSERT INTO users (chat_id, name, username, first_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE
                SET name = excluded.name, username = excluded.username
            """,
            (chat_id, name, username, _now_iso()),
        )
        await self.conn.commit()

    async def list_users(self) -> list[dict]:
        """All users with their watch (if any), for the web table."""
        async with self.conn.execute(
            """
            SELECT u.chat_id, u.name, u.username, u.first_seen,
                   s.direction, s.company, s.watch_date, s.updated_at
            FROM users u
            LEFT JOIN settings s ON s.chat_id = u.chat_id
            ORDER BY u.first_seen
            """
        ) as cur:
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def get_all_watchers(self) -> list[tuple[int, Direction, str | None, str | None]]:
        """All (chat_id, direction, watch_date, company) rows that have opted in to watching."""
        async with self.conn.execute(
            "SELECT chat_id, direction, watch_date, company FROM settings"
        ) as cur:
            rows = await cur.fetchall()
        return [
            (row["chat_id"], Direction(row["direction"]), row["watch_date"], row["company"])
            for row in rows
        ]

    # --- seat history ---

    async def latest_seat_count(
        self, chat_id: int, trip_key: tuple[str, str, str, str, str]
    ) -> tuple[int, bool] | None:
        """Most recent (seats_available, alerted) for a trip key, or None."""
        company, day, direction, depart_time, vehicle = trip_key
        async with self.conn.execute(
            """
            SELECT seats_available, alerted FROM seat_history
            WHERE chat_id = ? AND company = ? AND date = ?
              AND direction = ? AND depart_time = ? AND vehicle = ?
            ORDER BY polled_at DESC LIMIT 1
            """,
            (chat_id, company, day, direction, depart_time, vehicle),
        ) as cur:
            row = await cur.fetchone()
        return (row["seats_available"], bool(row["alerted"])) if row else None

    async def insert_history(self, chat_id: int, trip: NormalizedTrip, alerted: bool) -> None:
        await self.conn.execute(
            """
            INSERT INTO seat_history (
                chat_id, company, date, direction, depart_time, vehicle,
                seats_available, seats_total, price, currency, source_trip_id,
                polled_at, alerted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                trip.company,
                trip.date,
                trip.direction.value,
                trip.depart_time,
                trip.vehicle,
                trip.seats_available,
                trip.seats_total,
                trip.price,
                trip.currency,
                trip.source_trip_id,
                _now_iso(),
                int(alerted),
            ),
        )
        await self.conn.commit()

    async def insert_history_many(
        self, chat_id: int, rows: list[tuple[NormalizedTrip, bool]]
    ) -> None:
        await self.conn.executemany(
            """
            INSERT INTO seat_history (
                chat_id, company, date, direction, depart_time, vehicle,
                seats_available, seats_total, price, currency, source_trip_id,
                polled_at, alerted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chat_id,
                    trip.company,
                    trip.date,
                    trip.direction.value,
                    trip.depart_time,
                    trip.vehicle,
                    trip.seats_available,
                    trip.seats_total,
                    trip.price,
                    trip.currency,
                    trip.source_trip_id,
                    _now_iso(),
                    int(alerted),
                )
                for trip, alerted in rows
            ],
        )
        await self.conn.commit()
