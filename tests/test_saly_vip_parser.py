"""Tests for Saly VIP response parsing using the captured HAR."""

import json
from datetime import date
from pathlib import Path

import pytest

from buswatch.models import Direction
from buswatch.sources.saly_vip import SalyVipSource

CAPTURE = Path(__file__).parent.parent / "captures" / "salyvip.har"


def _load_captured_items() -> list[dict]:
    har = json.loads(CAPTURE.read_text())
    body = har["log"]["entries"][0]["response"]["content"]["text"]
    return json.loads(body)["data"]


@pytest.fixture
def items() -> list[dict]:
    if not CAPTURE.exists():
        pytest.skip("salyvip.har capture not present")
    return _load_captured_items()


def test_parse_saly_capture(items: list[dict]):
    source = SalyVipSource()
    day = date(2026, 10, 20)
    trips = [source._parse_item(item, day, Direction.PP_SR) for item in items]

    assert len(trips) == 14
    first = trips[0]
    assert first.company == "SalyVIP"
    assert first.depart_time == "08:35"
    assert first.arrive_time == "14:35"
    assert first.seats_available == 9
    assert first.seats_total == 15
    assert first.currency == "USD"
    # price 12.5 with no discount
    assert first.price == pytest.approx(12.5)


def test_parse_discount_applied(items: list[dict]):
    source = SalyVipSource()
    day = date(2026, 10, 20)
    # find a trip with a discount (the sleeping bus entries have discount 6.5)
    discounted = [i for i in items if i["prices"][0]["prices"][0].get("discount")]
    assert discounted, "expected at least one discounted fare in capture"
    trip = source._parse_item(discounted[0], day, Direction.PP_SR)
    fare = discounted[0]["prices"][0]["prices"][0]
    assert trip.price == pytest.approx(fare["amount"] - fare["discount"])


def test_parse_crossdate_arrival(items: list[dict]):
    source = SalyVipSource()
    day = date(2026, 10, 20)
    # 10:30 PM departure arrives 04:30 AM next day — arrival time already correct
    night = [i for i in items if i["departureAt"] == "10:30 PM"]
    assert night
    trip = source._parse_item(night[0], day, Direction.PP_SR)
    assert trip.depart_time == "22:30"
    assert trip.arrive_time == "04:30"
