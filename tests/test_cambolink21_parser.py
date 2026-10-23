"""Tests for the CamboLink21 (BookMeBus) HTML parser using the captured page."""

from datetime import date
from pathlib import Path

import pytest

from buswatch.models import Direction
from buswatch.sources.cambolink21 import Cambolink21Source

CAPTURE = Path(__file__).parent.parent / "captures" / "cambolink.har"


def _load_captured_html() -> str:
    import json

    har = json.loads(CAPTURE.read_text())
    return har["log"]["entries"][0]["response"]["content"]["text"]


@pytest.fixture
def html() -> str:
    if not CAPTURE.exists():
        pytest.skip("cambolink.har capture not present")
    return _load_captured_html()


def test_parse_cambolink_capture(html: str):
    source = Cambolink21Source()
    trips = source._parse(html, date(2026, 10, 26), Direction.PP_SR)

    # capture is PP -> SR for 26/10/2026 with ~40 Cambolink21 trips
    assert len(trips) == 40

    first = trips[0]
    assert first.company == "CamboLink21"
    assert first.direction is Direction.PP_SR
    assert first.date == "2026-10-26"
    assert first.depart_time == "06:05"
    assert first.arrive_time == "12:05"
    assert first.vehicle == "VIP VAN"
    assert first.price == 13.25
    assert first.currency == "USD"
    assert first.seats_available > 0
    assert first.source_trip_id is not None


def test_parse_skips_other_operators(html: str):
    source = Cambolink21Source()
    trips = source._parse(html, date(2026, 10, 26), Direction.PP_SR)
    # every parsed trip must be CamboLink21 — not Larryta/Saly/etc.
    assert all(t.company == "CamboLink21" for t in trips)


def test_parse_malformed_html_raises_nothing():
    source = Cambolink21Source()
    # garbage / empty page should parse to zero trips, not crash
    trips = source._parse(
        "<html><body>nothing here</body></html>", date(2026, 1, 1), Direction.PP_SR
    )
    assert trips == []
    assert source._parse("", date(2026, 1, 1), Direction.PP_SR) == []
