"""Saly VIP source — public JSON API, no auth required."""

from __future__ import annotations

from datetime import date

import httpx

from buswatch.models import Direction, NormalizedTrip
from buswatch.sources.base import BROWSER_HEADERS, Source, SourceError, to_24h

API = "https://salyvip.com/api/schedule/v1/list"

# location ids, confirmed from HAR captures.
LOCATION_IDS = {
    "Phnom Penh": 13,
    "Siem Reap": 18,
}


class SalyVipSource(Source):
    company = "SalyVIP"

    async def fetch(
        self, http: httpx.AsyncClient, day: date, direction: Direction
    ) -> list[NormalizedTrip]:
        if direction is Direction.PP_SR:
            from_loc, to_loc = LOCATION_IDS["Phnom Penh"], LOCATION_IDS["Siem Reap"]
        else:
            from_loc, to_loc = LOCATION_IDS["Siem Reap"], LOCATION_IDS["Phnom Penh"]

        payload = {
            "date": day.strftime("%d-%m-%Y"),
            "fromLocation": from_loc,
            "toLocation": to_loc,
            "serviceTypeIds": [1],
            "includeAllAvailable": True,
        }
        headers = {
            **BROWSER_HEADERS,
            "Content-Type": "application/json",
            "x-platform": "web",
            "x-os-type": "web",
        }
        resp = await http.post(API, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            raise SourceError(f"SalyVIP: request failed: {data.get('message')}")

        trips: list[NormalizedTrip] = []
        for item in data.get("data", []):
            try:
                trips.append(self._parse_item(item, day, direction))
            except (KeyError, ValueError, IndexError, TypeError):
                continue
        return trips

    @staticmethod
    def _parse_item(item: dict, day: date, direction: Direction) -> NormalizedTrip:
        # prices[0].prices[0] is the Adult fare; discount may sit beside amount.
        fare = item["prices"][0]["prices"][0]
        amount = float(fare["amount"])
        discount = fare.get("discount")
        if discount:
            amount = amount - float(discount)

        return NormalizedTrip(
            company="SalyVIP",
            date=day.isoformat(),
            direction=direction,
            depart_time=to_24h(item["departureAt"]),
            arrive_time=to_24h(item["arrivalAt"]),
            seats_available=int(item["availableSeats"]),
            seats_total=int(item["totalSeats"]),
            vehicle=item["vehicle"]["name"].strip(),
            price=amount,
            currency="USD",
            source_trip_id=str(item["id"]),
        )
