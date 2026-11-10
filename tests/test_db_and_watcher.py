"""Tests for Database and watcher transition logic."""

from datetime import UTC, date, datetime, timedelta

import pytest

from buswatch.config import THRESHOLD
from buswatch.db import Database
from buswatch.models import Direction, NormalizedTrip
from buswatch.watcher import _low_trips_for_user

CHAT_A = 111
CHAT_B = 222


def _trip(seats: int, depart: str = "06:00", company: str = "Larryta") -> NormalizedTrip:
    return NormalizedTrip(
        company=company,
        date=date(2026, 10, 22).isoformat(),
        direction=Direction.PP_SR,
        depart_time=depart,
        arrive_time="11:30",
        seats_available=seats,
        seats_total=15,
        vehicle="Minivan 15 Seats",
        price=14.0,
        currency="USD",
        source_trip_id="x",
    )


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.connect()
    yield database
    await database.close()


async def test_add_and_get_watches(db: Database):
    assert await db.get_watches(CHAT_A) == []
    await db.add_watch(CHAT_A, Direction.PP_SR)
    watches = await db.get_watches(CHAT_A)
    assert len(watches) == 1
    assert Direction(watches[0]["direction"]) is Direction.PP_SR
    assert watches[0]["watch_date"] is None
    assert watches[0]["company"] is None


async def test_multiple_watches_per_user(db: Database):
    # each add_watch creates a new row instead of replacing the old one
    await db.add_watch(CHAT_A, Direction.PP_SR, "2026-10-26")
    await db.add_watch(CHAT_A, Direction.SR_PP, "2026-10-27")
    watches = await db.get_watches(CHAT_A)
    assert len(watches) == 2
    assert watches[0]["direction"] == "PP_SR"
    assert watches[0]["watch_date"] == "2026-10-26"
    assert watches[1]["direction"] == "SR_PP"
    assert watches[1]["watch_date"] == "2026-10-27"


async def test_delete_single_watch(db: Database):
    id_a = await db.add_watch(CHAT_A, Direction.PP_SR)
    await db.add_watch(CHAT_A, Direction.SR_PP)

    assert await db.delete_watch(id_a) is True
    watches = await db.get_watches(CHAT_A)
    assert len(watches) == 1
    assert watches[0]["direction"] == "SR_PP"

    # deleting the same id again is a no-op
    assert await db.delete_watch(id_a) is False


async def test_delete_all_watches(db: Database):
    # nothing to delete
    assert await db.delete_all_watches(CHAT_A) is False

    await db.add_watch(CHAT_A, Direction.PP_SR)
    await db.add_watch(CHAT_A, Direction.SR_PP, "2026-10-26")
    await db.add_watch(CHAT_B, Direction.SR_PP)

    # only CHAT_A's rows are removed
    assert await db.delete_all_watches(CHAT_A) is True
    assert await db.get_watches(CHAT_A) == []
    assert len(await db.get_watches(CHAT_B)) == 1

    # deleting again is a no-op
    assert await db.delete_all_watches(CHAT_A) is False


async def test_get_all_watches(db: Database):
    await db.add_watch(CHAT_A, Direction.PP_SR)
    await db.add_watch(CHAT_B, Direction.SR_PP, "2026-10-26")
    rows = await db.get_all_watches()
    assert len(rows) == 2
    by_chat = {cid: (d, w, co) for _id, cid, d, w, co in rows}
    assert by_chat == {
        CHAT_A: (Direction.PP_SR, None, None),
        CHAT_B: (Direction.SR_PP, "2026-10-26", None),
    }


async def test_watch_company_filter(db: Database):
    # company=None means all companies
    await db.add_watch(CHAT_A, Direction.PP_SR)
    # company set persists
    await db.add_watch(CHAT_A, Direction.PP_SR, "2026-10-26", company="Larryta")
    rows = await db.get_all_watches()
    assert len(rows) == 2
    companies = [co for _id, _c, _d, _w, co in rows]
    assert None in companies
    assert "Larryta" in companies


async def test_users_upsert_and_list(db: Database):
    await db.upsert_user(1, "Alice", "alice")
    await db.upsert_user(2, "Bob", None)
    # re-upsert refreshes name/username, keeps first_seen
    await db.upsert_user(1, "Alice2", "alice2")

    users = await db.list_users()
    assert len(users) == 2
    by_id = {u["chat_id"]: u for u in users}
    assert by_id[1]["name"] == "Alice2"
    assert by_id[1]["username"] == "alice2"
    assert by_id[1]["direction"] is None
    assert by_id[2]["name"] == "Bob"

    # joining a watch shows direction + date
    await db.add_watch(1, Direction.PP_SR, "2026-10-26")
    users = await db.list_users()
    row = next(u for u in users if u["chat_id"] == 1)
    assert row["direction"] == "PP_SR"
    assert row["watch_date"] == "2026-10-26"


async def test_first_sighting_below_threshold_alerts(db: Database):
    # no prior history, seats already low -> alert
    low = await _low_trips_for_user(db, CHAT_A, [_trip(3)])
    assert len(low) == 1
    assert low[0].seats_available == 3


async def test_first_sighting_above_threshold_no_alert(db: Database):
    low = await _low_trips_for_user(db, CHAT_A, [_trip(12)])
    assert low == []


async def test_no_repeat_alert_within_realert_interval(db: Database):
    # first poll: plenty of seats -> no alert
    await _low_trips_for_user(db, CHAT_A, [_trip(12)])
    # second poll: drops to <=7 -> alert
    low = await _low_trips_for_user(db, CHAT_A, [_trip(5)])
    assert len(low) == 1
    # third poll (same cycle): still <=7, within 6h -> no repeat alert
    low = await _low_trips_for_user(db, CHAT_A, [_trip(4)])
    assert low == []


async def test_realert_after_interval(db: Database):
    # simulate an alert sent 7 hours ago (REALERT_INTERVAL is 6h)
    trip = _trip(3)
    await db.insert_history(CHAT_A, trip, True)
    old = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    await db.conn.execute(
        "UPDATE seat_history SET polled_at = ? WHERE chat_id = ?", (old, CHAT_A)
    )
    await db.conn.commit()

    # still <=7 and interval elapsed -> alerts again
    low = await _low_trips_for_user(db, CHAT_A, [_trip(3)])
    assert len(low) == 1


async def test_user_histories_are_independent(db: Database):
    # A saw a drop already (alerted); B's first sighting is the same low trip -> B alerts
    await _low_trips_for_user(db, CHAT_A, [_trip(12)])
    await _low_trips_for_user(db, CHAT_A, [_trip(3)])  # A alerted

    low_b = await _low_trips_for_user(db, CHAT_B, [_trip(3)])
    assert len(low_b) == 1  # B has no history -> first-sighting alert


async def test_threshold_boundary(db: Database):
    # exactly at threshold alerts
    low = await _low_trips_for_user(db, CHAT_A, [_trip(THRESHOLD)])
    assert len(low) == 1
    # above threshold does not
    low = await _low_trips_for_user(db, CHAT_B, [_trip(THRESHOLD + 1)])
    assert low == []


async def test_history_rows_written(db: Database):
    trip = _trip(10)
    await _low_trips_for_user(db, CHAT_A, [trip])
    prev = await db.latest_seat_count(CHAT_A, trip.key)
    assert prev is not None
    seats, alerted = prev
    assert seats == 10
    assert alerted is False
