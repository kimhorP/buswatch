"""Background watcher: polls sources on a fixed interval, detects threshold transitions."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

import httpx

from buswatch.config import POLL_INTERVAL, REALERT_INTERVAL, THRESHOLD, TZ
from buswatch.db import Database
from buswatch.models import Direction, NormalizedTrip, classify_vehicle
from buswatch.sources import ALL_SOURCES
from buswatch.sources.base import SourceError

log = logging.getLogger("buswatch.watcher")


def _next_poll_time(now: datetime) -> datetime:
    """Next poll slot strictly after `now`, aligned to POLL_INTERVAL, in Asia/Phnom_Penh."""
    local = now.astimezone(TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    interval = POLL_INTERVAL.total_seconds()
    elapsed = (local - midnight).total_seconds()
    return midnight + timedelta(seconds=(int(elapsed // interval) + 1) * interval)


async def _fetch_day(
    direction: Direction, day, http: httpx.AsyncClient, company: str | None = None
) -> list[NormalizedTrip]:
    """Fetch sources for one day, one direction; optional company filter."""
    trips: list[NormalizedTrip] = []
    for source in ALL_SOURCES:
        if company is not None and source.company != company:
            continue
        try:
            trips.extend(await source.fetch(http, day, direction))
        except (SourceError, httpx.HTTPError) as exc:
            log.warning("Fetch failed: %s — %s", source.company, exc)
    return trips


async def _low_trips_for_user(
    db: Database, chat_id: int, trips: list[NormalizedTrip]
) -> list[NormalizedTrip]:
    """Detect low seats for one user and persist their history; re-alerts every 6h."""
    from datetime import UTC

    low: list[NormalizedTrip] = []
    history_rows: list[tuple[NormalizedTrip, bool]] = []
    now = datetime.now(UTC)

    for trip in trips:
        prev = await db.latest_seat_count(chat_id, trip.key)
        last_alert = await db.get_last_alert_time(chat_id, trip.key)

        should_alert = False
        if trip.seats_available <= THRESHOLD:
            if prev is None:
                should_alert = True  # first sighting, already low
            elif prev[0] > THRESHOLD and not prev[1]:
                should_alert = True  # transitioned from above threshold into low
            elif last_alert is not None:
                last_dt = datetime.fromisoformat(last_alert)
                if (now - last_dt) >= REALERT_INTERVAL:
                    should_alert = True  # re-alert after interval while still low

        history_rows.append((trip, should_alert))
        if should_alert:
            low.append(trip)

    await db.insert_history_many(chat_id, history_rows)
    return low


async def poll_once(db: Database, http: httpx.AsyncClient) -> dict[int, list[NormalizedTrip]]:
    """Poll every watched direction/date once; return {chat_id: low_seat_trips}."""
    watchers = await db.get_all_watches()
    if not watchers:
        return {}

    # Resolve each user's days: their watch_date, or today+tomorrow if unset.
    today = datetime.now(TZ).date()
    # Multiple watches per user -> user_days[chat_id] = list of (direction, days, company)
    user_days: dict[int, list[tuple[Direction, list[date], str | None]]] = {}
    for _watch_id, chat_id, direction, watch_date, company in watchers:
        if watch_date:
            days = [date.fromisoformat(watch_date)]
        else:
            days = [today, today + timedelta(days=1)]
        user_days.setdefault(chat_id, []).append((direction, days, company))

    # Fetch once per distinct (direction, day, company), shared across users.
    needed = {
        (direction, day, company)
        for chat_id_days in user_days.values()
        for direction, days, company in chat_id_days
        for day in days
    }
    fetched: dict[tuple[Direction, date, str | None], list[NormalizedTrip]] = {}
    for direction, day, company in needed:
        fetched[(direction, day, company)] = await _fetch_day(direction, day, http, company)

    # Evaluate + record history per user, against that user's own history.
    alerts: dict[int, list[NormalizedTrip]] = {}
    for chat_id, watches in user_days.items():
        # Collect all trips across this user's watches
        all_trips: list[NormalizedTrip] = []
        for direction, days, company in watches:
            trips = [t for day in days for t in fetched.get((direction, day, company), [])]
            all_trips.extend(trips)
        low = await _low_trips_for_user(db, chat_id, all_trips)
        if low:
            alerts[chat_id] = low
    return alerts


def _bus_type(trip: NormalizedTrip) -> str:
    return classify_vehicle(trip.vehicle)


def pretty_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso[:10]).strftime("%d %b %Y")
    except ValueError:
        return iso


def format_alert(trips: list[NormalizedTrip], direction: Direction) -> str:
    """One consolidated message per poll cycle."""
    lines = [f"🪑 Low seats — {direction.label}\n"]
    by_company: dict[str, list[NormalizedTrip]] = {}
    for t in sorted(trips, key=lambda t: (t.company, t.depart_time)):
        by_company.setdefault(t.company, []).append(t)

    for company, company_trips in by_company.items():
        lines.append(f"<b>{company}</b>")
        for t in company_trips:
            seats = "sold out" if t.seats_available == 0 else f"{t.seats_available} left"
            price = f"{t.price:g} {t.currency}"
            lines.append("")
            lines.append(f"• Date: {pretty_date(t.date)}")
            lines.append(f"• Time: {t.depart_time} → {t.arrive_time}")
            lines.append(f"• Bus Type: {_bus_type(t)}")
            lines.append(f"• Remaining Seats: {seats}")
            lines.append(f"• Price: {price}")
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
                watchers = await db.get_all_watches()
                # a chat can have multiple watches; show the first one's direction
                dir_by_chat = {}
                for _, chat_id, direction, _, _ in watchers:
                    if chat_id not in dir_by_chat:
                        dir_by_chat[chat_id] = direction

                alerts = await poll_once(db, http)
                total = sum(len(v) for v in alerts.values())
                log.info("Poll done: %d low-seat trips across %d users", total, len(alerts))

                for chat_id, low in alerts.items():
                    direction = dir_by_chat.get(chat_id)
                    if direction is not None:
                        await send_alert(chat_id, format_alert(low, direction))
            except Exception:
                log.exception("Poll cycle failed")
