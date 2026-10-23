"""Background watcher: polls sources at fixed times, detects threshold transitions."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

import httpx

from buswatch.config import POLL_TIMES, THRESHOLD, TZ
from buswatch.db import Database
from buswatch.models import Direction, NormalizedTrip
from buswatch.sources import ALL_SOURCES
from buswatch.sources.base import SourceError

log = logging.getLogger("buswatch.watcher")


def _next_poll_time(now: datetime) -> datetime:
    """Next scheduled poll datetime after `now`, in Asia/Phnom_Penh."""
    local = now.astimezone(TZ)
    today_slots = [
        local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0) for t in POLL_TIMES
    ]
    for slot in today_slots:
        if slot > local:
            return slot
    # else: first slot tomorrow
    tomorrow = local + timedelta(days=1)
    first = POLL_TIMES[0]
    return tomorrow.replace(hour=first.hour, minute=first.minute, second=0, microsecond=0)


async def _fetch_day(direction: Direction, day, http: httpx.AsyncClient) -> list[NormalizedTrip]:
    """Fetch every source for one day, one direction."""
    trips: list[NormalizedTrip] = []
    for source in ALL_SOURCES:
        try:
            trips.extend(await source.fetch(http, day, direction))
        except (SourceError, httpx.HTTPError) as exc:
            log.warning("Fetch failed: %s — %s", source.company, exc)
    return trips


async def _low_trips_for_user(
    db: Database, chat_id: int, trips: list[NormalizedTrip]
) -> list[NormalizedTrip]:
    """Detect threshold crossings for one user and persist their history."""
    low: list[NormalizedTrip] = []
    history_rows: list[tuple[NormalizedTrip, bool]] = []

    for trip in trips:
        prev = await db.latest_seat_count(chat_id, trip.key)
        should_alert = trip.seats_available <= THRESHOLD and (
            prev is None or (prev[0] > THRESHOLD and not prev[1])
        )
        history_rows.append((trip, should_alert))
        if should_alert:
            low.append(trip)

    await db.insert_history_many(chat_id, history_rows)
    return low


async def poll_once(db: Database, http: httpx.AsyncClient) -> dict[int, list[NormalizedTrip]]:
    """Poll every watched direction/date once; return {chat_id: low_seat_trips}."""
    watchers = await db.get_all_watchers()
    if not watchers:
        return {}

    # Resolve each user's days: their watch_date, or today+tomorrow if unset.
    today = datetime.now(TZ).date()
    user_days: dict[int, tuple[Direction, list[date]]] = {}
    for chat_id, direction, watch_date in watchers:
        if watch_date:
            days = [date.fromisoformat(watch_date)]
        else:
            days = [today, today + timedelta(days=1)]
        user_days[chat_id] = (direction, days)

    # Fetch once per distinct (direction, day), shared across users.
    needed = {(direction, day) for direction, days in user_days.values() for day in days}
    fetched: dict[tuple[Direction, date], list[NormalizedTrip]] = {}
    for direction, day in needed:
        fetched[(direction, day)] = await _fetch_day(direction, day, http)

    # Evaluate + record history per user, against that user's own history.
    alerts: dict[int, list[NormalizedTrip]] = {}
    for chat_id, (direction, days) in user_days.items():
        trips = [t for day in days for t in fetched.get((direction, day), [])]
        low = await _low_trips_for_user(db, chat_id, trips)
        if low:
            alerts[chat_id] = low
    return alerts


def format_alert(trips: list[NormalizedTrip], direction: Direction) -> str:
    """One consolidated message per poll cycle."""
    lines = [f"🪑 Low seats — {direction.label} ({datetime.now(TZ):%d %b})\n"]
    by_company: dict[str, list[NormalizedTrip]] = {}
    for t in sorted(trips, key=lambda t: (t.company, t.depart_time)):
        by_company.setdefault(t.company, []).append(t)

    for company, company_trips in by_company.items():
        lines.append(f"<b>{company}</b>")
        for t in company_trips:
            seats = "sold out" if t.seats_available == 0 else f"{t.seats_available} left"
            price = f"{t.price:g} {t.currency}"
            lines.append(f"  {t.depart_time} → {t.arrive_time} · {t.vehicle} · {seats} · {price}")
        lines.append("")

    return "\n".join(lines).strip()


async def watcher_loop(db: Database, send_alert) -> None:
    """Run forever: sleep until next slot, poll, send consolidated alerts per user.

    `send_alert` is an async callable: (chat_id: int, text: str) -> None.
    """
    async with httpx.AsyncClient(timeout=30) as http:
        while True:
            now = datetime.now(TZ)
            target = _next_poll_time(now)
            wait = (target - now).total_seconds()
            log.info("Next poll at %s (sleeping %.0fs)", target.isoformat(), wait)
            await asyncio.sleep(max(wait, 1))

            try:
                # direction per user, for message formatting
                watchers = await db.get_all_watchers()
                dir_by_chat = {chat_id: direction for chat_id, direction, _ in watchers}

                alerts = await poll_once(db, http)
                total = sum(len(v) for v in alerts.values())
                log.info("Poll done: %d low-seat trips across %d users", total, len(alerts))

                for chat_id, low in alerts.items():
                    direction = dir_by_chat.get(chat_id)
                    if direction is not None:
                        await send_alert(chat_id, format_alert(low, direction))
            except Exception:
                log.exception("Poll cycle failed")
