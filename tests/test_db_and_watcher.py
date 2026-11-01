"""Tests for Database and watcher transition logic."""

from datetime import date

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


async def test_direction_roundtrip(db: Database):
    assert await db.get_direction(CHAT_A) is None
    await db.set_direction(CHAT_A, Direction.PP_SR)
    assert await db.get_direction(CHAT_A) is Direction.PP_SR
    # change direction
    await db.set_direction(CHAT_A, Direction.SR_PP)
    assert await db.get_direction(CHAT_A) is Direction.SR_PP


async def test_delete_direction(db: Database):
    # nothing to delete
    assert await db.delete_direction(CHAT_A) is False

    await db.set_direction(CHAT_A, Direction.PP_SR)
    await db.set_direction(CHAT_B, Direction.SR_PP)

    # only CHAT_A's row is removed
    assert await db.delete_direction(CHAT_A) is True
    assert await db.get_direction(CHAT_A) is None
    assert await db.get_direction(CHAT_B) is Direction.SR_PP

    # deleting again is a no-op
    assert await db.delete_direction(CHAT_A) is False


async def test_get_all_watchers(db: Database):
    await db.set_direction(CHAT_A, Direction.PP_SR)
    await db.set_direction(CHAT_B, Direction.SR_PP, "2026-10-26")
    watchers = {cid: (d, w, company) for cid, d, w, company in await db.get_all_watchers()}
    assert watchers == {
        CHAT_A: (Direction.PP_SR, None, None),
        CHAT_B: (Direction.SR_PP, "2026-10-26", None),
    }


async def test_watch_date_upsert(db: Database):
    # set with date, then re-set without date clears it
    await db.set_direction(CHAT_A, Direction.PP_SR, "2026-10-26")
    assert await db.get_all_watchers() == [(CHAT_A, Direction.PP_SR, "2026-10-26", None)]
    await db.set_direction(CHAT_A, Direction.SR_PP)
    assert await db.get_all_watchers() == [(CHAT_A, Direction.SR_PP, None, None)]


async def test_watch_company_filter(db: Database):
    # company=None means all companies
    await db.set_direction(CHAT_A, Direction.PP_SR, company=None)
    assert await db.get_all_watchers() == [(CHAT_A, Direction.PP_SR, None, None)]
    # company set persists
    await db.set_direction(CHAT_A, Direction.PP_SR, "2026-10-26", company="Larryta")
    assert await db.get_all_watchers() == [
        (CHAT_A, Direction.PP_SR, "2026-10-26", "Larryta")
    ]
    # switching company to None clears it
    await db.set_direction(CHAT_A, Direction.PP_SR, company=None)
    assert await db.get_all_watchers() == [(CHAT_A, Direction.PP_SR, None, None)]


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
    await db.set_direction(1, Direction.PP_SR, "2026-10-26")
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


async def test_transition_into_threshold_alerts_once(db: Database):
    # first poll: plenty of seats -> no alert
    await _low_trips_for_user(db, CHAT_A, [_trip(12)])
    # second poll: drops to <=7 -> alert
    low = await _low_trips_for_user(db, CHAT_A, [_trip(5)])
    assert len(low) == 1
    # third poll: still <=7 -> no repeat alert
    low = await _low_trips_for_user(db, CHAT_A, [_trip(4)])
    assert low == []


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
