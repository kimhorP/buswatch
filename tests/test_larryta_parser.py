"""Tests for Larryta response parsing using the captured HAR."""

import json
from datetime import date
from pathlib import Path

import pytest

from buswatch.models import Direction
from buswatch.sources.larryta import LarrytaSource

CAPTURE = Path(__file__).parent.parent / "captures" / "larryta.har"


def _load_captured_response() -> dict:
    har = json.loads(CAPTURE.read_text())
    body = har["log"]["entries"][0]["response"]["content"]["text"]
    return json.loads(body)


@pytest.fixture
def payload() -> dict:
    if not CAPTURE.exists():
        pytest.skip("larryta.har capture not present")
    return _load_captured_response()


def test_parse_larryta_capture(payload: dict):
    day = date(2026, 10, 21)
    data_items = payload["response"]["data"]
    trips = [LarrytaSource._parse_item(item, day, Direction.PP_SR) for item in data_items]

    assert len(trips) == 32
    first = trips[0]
    assert first.company == "Larryta"
    assert first.depart_time == "06:00"
    assert first.arrive_time == "11:30"
    assert first.seats_available == 14
    assert first.seats_total == 15
    assert first.vehicle == "Minivan 15 Seats"
    assert first.price == 56000.0
    assert first.currency == "KHR"
    assert first.source_trip_id == "639223"


def test_parse_all_trips_have_times(payload: dict):
    day = date(2026, 10, 21)
    for item in payload["response"]["data"]:
        trip = LarrytaSource._parse_item(item, day, Direction.PP_SR)
        assert ":" in trip.depart_time
        assert ":" in trip.arrive_time
        assert trip.seats_available >= 0
