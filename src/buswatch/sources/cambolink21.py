"""CamboLink21 source — scrapes the BookMeBus operator page (server-rendered HTML)."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote

import httpx

from buswatch.models import Direction, NormalizedTrip
from buswatch.sources.base import BROWSER_HEADERS, Source, SourceError, to_24h

OPERATOR_PAGE = "https://bookmebus.com/en/cambotra"
TARGET_OPERATOR = "Cambolink21 Express"

_SEAT_RE = re.compile(r"label-seat-avaible\">\s*(\d+)\s*<")
_PRICE_RE = re.compile(r"USD\s*([\d.]+)")
_TIME_RE = re.compile(r'<p class="time">\s*([0-9]{1,2}:[0-9]{2}\s*[AP]M)\s*</p>')
_VEHICLE_RE = re.compile(r"vehicle_type_name&quot;:&quot;([^&]+)&quot;")
_TRIP_ID_RE = re.compile(r'data-value="(\d+-\d+-\d+)"')


class Cambolink21Source(Source):
    company = "CamboLink21"

    async def fetch(
        self, http: httpx.AsyncClient, day: date, direction: Direction
    ) -> list[NormalizedTrip]:
        if direction is Direction.PP_SR:
            frm, to = "Phnom Penh", "Siem Reap"
        else:
            frm, to = "Siem Reap", "Phnom Penh"

        url = (
            f"{OPERATOR_PAGE}?utf8=%E2%9C%93"
            f"&from={quote(frm)}&to={quote(to)}"
            f"&on_date={quote(day.strftime('%d/%m/%Y'))}"
            f"&commit=Find+Trip"
        )
        resp = await http.get(url, headers=BROWSER_HEADERS)
        resp.raise_for_status()

        trips = self._parse(resp.text, day, direction)
        if not trips:
            raise SourceError(
                "CamboLink21 (BookMeBus): zero Cambolink21 trips parsed — "
                "page structure may have changed"
            )
        return trips

    def _parse(self, html: str, day: date, direction: Direction) -> list[NormalizedTrip]:
        # Scope to the results list so sidebar/footer cards don't leak in.
        start = html.find('id="search-results-list"')
        scope = html[start:] if start != -1 else html

        # Each trip is a container-search-result block; split and keep Cambolink21's.
        chunks = scope.split("container-search-result")[1:]
        trips: list[NormalizedTrip] = []
        for chunk in chunks:
            if TARGET_OPERATOR not in chunk:
                continue
            # Limit each card to the next card boundary for clean regexes.
            card = (
                chunk[: chunk.find("container-search-result")]
                if "container-search-result" in chunk
                else chunk
            )
            trip = self._parse_card(card, day, direction)
            if trip is not None:
                trips.append(trip)
        return trips

    @staticmethod
    def _parse_card(card: str, day: date, direction: Direction) -> NormalizedTrip | None:
        seat_m = _SEAT_RE.search(card)
        price_m = _PRICE_RE.search(card)
        times = _TIME_RE.findall(card)
        if not seat_m or not price_m or len(times) < 2:
            return None  # malformed card — skip

        vehicle_m = _VEHICLE_RE.search(card)
        trip_id_m = _TRIP_ID_RE.search(card)

        return NormalizedTrip(
            company="CamboLink21",
            date=day.isoformat(),
            direction=direction,
            depart_time=to_24h(times[0]),
            arrive_time=to_24h(times[1]),
            seats_available=int(seat_m.group(1)),
            seats_total=None,  # not present in the HTML card
            vehicle=vehicle_m.group(1) if vehicle_m else "Unknown",
            price=float(price_m.group(1)),
            currency="USD",
            source_trip_id=trip_id_m.group(1) if trip_id_m else None,
        )
