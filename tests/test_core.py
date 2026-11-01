"""Tests for pure logic: models, time conversion, watcher scheduling, vehicle grouping."""

from datetime import datetime
from zoneinfo import ZoneInfo

from buswatch.models import Direction, NormalizedTrip, classify_vehicle
from buswatch.sources.base import to_24h
from buswatch.watcher import _next_poll_time, format_alert

TZ = ZoneInfo("Asia/Phnom_Penh")


def test_to_24h_am_pm():
    assert to_24h("06:05 AM") == "06:05"
    assert to_24h("11:50 PM") == "23:50"
    assert to_24h("12:00 AM") == "00:00"
    assert to_24h("12:30 PM") == "12:30"


def test_to_24h_passthrough_24h():
    assert to_24h("06:05") == "06:05"
    assert to_24h("23:58") == "23:58"


def test_classify_vehicle():
    assert classify_vehicle("Minivan 15 Seats") == "Minivan"
    assert classify_vehicle("VIP VAN") == "Minivan"
    assert classify_vehicle("Sleeping bus 34 beds") == "Night bus"
    assert classify_vehicle("Night Bus 30 Bed") == "Night bus"
    assert classify_vehicle("Luxury sleeping bus") == "Night bus"
    assert classify_vehicle("Air Bus 35 Seat New") == "Bus"
    assert classify_vehicle("Unknown") == "Bus"


def test_direction_labels():
    assert Direction.PP_SR.label == "Phnom Penh → Siem Reap"
    assert Direction.SR_PP.label == "Siem Reap → Phnom Penh"


def test_trip_key():
    t = NormalizedTrip(
        company="Larryta",
        date="2026-10-22",
        direction=Direction.PP_SR,
        depart_time="06:00",
        arrive_time="11:30",
        seats_available=5,
        seats_total=15,
        vehicle="Minivan 15 Seats",
        price=56000,
        currency="KHR",
    )
    assert t.key == ("Larryta", "2026-10-22", "PP_SR", "06:00", "Minivan 15 Seats")


def test_next_poll_time_before_first_slot():
    # 08:00 Phnom Penh -> today 09:00
    now = datetime(2026, 10, 22, 8, 0, tzinfo=TZ)
    nxt = _next_poll_time(now)
    assert (nxt.hour, nxt.minute) == (9, 0)
    assert nxt.day == 22


def test_next_poll_time_between_slots():
    # 10:00 -> next hour 11:00
    now = datetime(2026, 10, 22, 10, 0, tzinfo=TZ)
    nxt = _next_poll_time(now)
    assert (nxt.hour, nxt.minute) == (11, 0)
    assert nxt.day == 22


def test_next_poll_time_after_last_slot():
    # 23:00 -> next hour 00:00 next day
    now = datetime(2026, 10, 22, 23, 0, tzinfo=TZ)
    nxt = _next_poll_time(now)
    assert nxt.day == 23
    assert (nxt.hour, nxt.minute) == (0, 0)


def test_next_poll_time_uses_phnom_penh_tz():
    # 17:00 UTC = 00:00 next day Phnom Penh (UTC+7) -> 01:00
    now = datetime(2026, 10, 22, 17, 0, tzinfo=ZoneInfo("UTC"))
    nxt = _next_poll_time(now)
    assert nxt.tzinfo == TZ
    assert (nxt.day, nxt.hour, nxt.minute) == (23, 1, 0)


def _trip(seats: int, company: str = "Larryta", depart: str = "06:00") -> NormalizedTrip:
    return NormalizedTrip(
        company=company,
        date="2026-10-22",
        direction=Direction.PP_SR,
        depart_time=depart,
        arrive_time="11:30",
        seats_available=seats,
        seats_total=15,
        vehicle="Minivan 15 Seats",
        price=14.0,
        currency="USD",
    )


def test_format_alert_groups_by_company():
    trips = [
        _trip(3, "Larryta", "06:00"),
        _trip(0, "CamboLink21", "07:05"),
        _trip(5, "Larryta", "23:00"),
    ]
    text = format_alert(trips, Direction.PP_SR)
    assert "Phnom Penh → Siem Reap" in text
    assert "<b>Larryta</b>" in text
    assert "<b>CamboLink21</b>" in text
    assert "sold out" in text
    assert "3 left" in text
    # Larryta trips sorted by depart time
    assert text.index("06:00") < text.index("23:00")
