"""Tests for dd-mm-yy date parsing and the web users table page."""

from datetime import date

from buswatch.bot import _format_user_date, _parse_user_date
from buswatch.web import render_users_page


def test_parse_valid_date():
    assert _parse_user_date("20-10-26") == date(2026, 10, 20)
    assert _parse_user_date("01-01-27") == date(2027, 1, 1)
    assert _parse_user_date(" 20-10-26 ") == date(2026, 10, 20)


def test_parse_invalid_dates():
    assert _parse_user_date("2026-10-20") is None
    assert _parse_user_date("20/10/26") is None
    assert _parse_user_date("32-10-26") is None
    assert _parse_user_date("garbage") is None
    assert _parse_user_date("") is None


def test_format_roundtrip():
    d = date(2026, 10, 20)
    assert _parse_user_date(_format_user_date(d)) == d


def test_render_users_page():
    users = [
        {
            "chat_id": 111,
            "name": "Alice",
            "username": "alice",
            "first_seen": "2026-10-19T10:00:00+00:00",
            "direction": "PP_SR",
            "watch_date": "2026-10-26",
            "updated_at": "2026-10-19T10:00:00+00:00",
        },
        {
            "chat_id": 222,
            "name": "Bob",
            "username": None,
            "first_seen": "2026-10-19T11:00:00+00:00",
            "direction": None,
            "watch_date": None,
            "updated_at": None,
        },
    ]
    page = render_users_page(users)
    assert "BusWatch users (2)" in page
    assert "Alice" in page
    assert "Phnom Penh → Siem Reap" in page
    assert "2026-10-26" in page
    assert "Bob" in page
    assert "—" in page  # placeholder for missing values


def test_render_escapes_html():
    page = render_users_page(
        [
            {
                "chat_id": 3,
                "name": "<script>alert(1)</script>",
                "username": "x",
                "first_seen": "t",
                "direction": None,
                "watch_date": None,
                "updated_at": None,
            }
        ]
    )
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
