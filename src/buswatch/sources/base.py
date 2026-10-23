"""Abstract source interface + helpers shared by adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import httpx

from buswatch.models import Direction, NormalizedTrip

# A normal browser UA — identify honestly (see security.md).
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:158.0) Gecko/20100101 Firefox/158.0"

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}


class SourceError(Exception):
    """A source fetch or parse failed."""


class Source(ABC):
    """One bus company data source."""

    company: str

    @abstractmethod
    async def fetch(
        self, http: httpx.AsyncClient, day: date, direction: Direction
    ) -> list[NormalizedTrip]:
        """Return all trips for the given day and direction."""


def to_24h(raw: str) -> str:
    """Convert '06:05 AM' / '11:50 PM' to '06:05' / '23:50'. Already-24h passes through."""
    raw = raw.strip()
    if "AM" in raw or "PM" in raw:
        from datetime import datetime

        return datetime.strptime(raw, "%I:%M %p").strftime("%H:%M")
    return raw
