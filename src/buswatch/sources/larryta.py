"""Larryta source — official JSON API with a CSRF handshake."""

from __future__ import annotations

import re
from datetime import date

import httpx

from buswatch.models import Direction, NormalizedTrip
from buswatch.sources.base import BROWSER_HEADERS, Source, SourceError

BASE = "https://larrytabus.com"
SCHEDULE_PAGE = f"{BASE}/schedules/phnom-penh-to-siem-reap"
AJAX = f"{BASE}/ajax/get_schedule"

# direction_id per direction, confirmed from HAR captures.
DIRECTION_IDS = {
    Direction.PP_SR: 1,
    Direction.SR_PP: 2,
}

_CSRF_RE = re.compile(r'window\.CSRF_TOKEN\s*=\s*"([a-f0-9]{64})"')


class LarrytaSource(Source):
    company = "Larryta"

    def __init__(self) -> None:
        self._csrf: str | None = None
        self._cookies: dict[str, str] | None = None

    async def _handshake(self, http: httpx.AsyncClient) -> None:
        """Fetch the schedule page to obtain bw_csrf cookie + CSRF_TOKEN."""
        resp = await http.get(SCHEDULE_PAGE, headers=BROWSER_HEADERS)
        resp.raise_for_status()

        m = _CSRF_RE.search(resp.text)
        if not m:
            raise SourceError("Larryta: CSRF token not found in schedule page")

        self._csrf = m.group(1)
        self._cookies = {"bw_csrf": self._csrf, "site_language": "en"}

    async def fetch(
        self, http: httpx.AsyncClient, day: date, direction: Direction
    ) -> list[NormalizedTrip]:
        if self._csrf is None:
            await self._handshake(http)

        import time as _time

        data = await self._post_schedule(
            http,
            direction_id=DIRECTION_IDS[direction],
            depart_date=day.isoformat(),
            timestamp_ms=int(_time.time() * 1000),
        )

        # Session may have expired — re-handshake once and retry.
        if isinstance(data, dict) and data.get("status") == 0:
            await self._handshake(http)
            data = await self._post_schedule(
                http,
                direction_id=DIRECTION_IDS[direction],
                depart_date=day.isoformat(),
                timestamp_ms=int(_time.time() * 1000),
            )

        if not isinstance(data, dict) or "response" not in data:
            raise SourceError(f"Larryta: unexpected response shape: {str(data)[:120]}")

        trips: list[NormalizedTrip] = []
        for item in data["response"].get("data", []):
            try:
                trips.append(self._parse_item(item, day, direction))
            except (KeyError, ValueError):
                continue  # skip malformed items rather than fail the whole fetch
        return trips

    async def _post_schedule(
        self,
        http: httpx.AsyncClient,
        *,
        direction_id: int,
        depart_date: str,
        timestamp_ms: int,
    ) -> dict:
        headers = {
            **BROWSER_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "X-Timestamp": str(timestamp_ms),
            "csrf-token": self._csrf or "",
        }
        body = f"direction_id={direction_id}&depart_date={depart_date}&ticket_type=k"
        resp = await http.post(
            AJAX,
            content=body,
            headers=headers,
            cookies=self._cookies,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _parse_item(item: dict, day: date, direction: Direction) -> NormalizedTrip:
        bus = item["bus_type"]
        direction_obj = item["direction"]
        return NormalizedTrip(
            company="Larryta",
            date=day.isoformat(),
            direction=direction,
            depart_time=item["sche_time"],
            arrive_time=direction_obj["arrival_time"],
            seats_available=int(item["total_available"]),
            seats_total=int(bus["seat_num"]),
            vehicle=bus["bus_name"],
            price=float(item["prices"]["unit_price"]),
            currency="KHR",
            source_trip_id=str(item["schedule_id"]),
        )
