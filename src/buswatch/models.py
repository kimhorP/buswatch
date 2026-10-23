"""Core data models shared across sources, watcher, and bot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Direction(StrEnum):
    """Watched / queried direction on the PP <-> SR corridor."""

    PP_SR = "PP_SR"  # Phnom Penh -> Siem Reap
    SR_PP = "SR_PP"  # Siem Reap -> Phnom Penh

    @property
    def label(self) -> str:
        return "Phnom Penh → Siem Reap" if self is Direction.PP_SR else "Siem Reap → Phnom Penh"


@dataclass(frozen=True, slots=True)
class NormalizedTrip:
    """One departure as reported by a source, normalized to a common shape."""

    company: str
    date: str  # ISO YYYY-MM-DD
    direction: Direction
    depart_time: str  # HH:MM 24h, local UTC+7
    arrive_time: str  # HH:MM 24h, local UTC+7
    seats_available: int
    seats_total: int | None
    vehicle: str
    price: float
    currency: str  # "KHR" | "USD"
    source_trip_id: str | None = None

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        """Stable identity for history comparison, source-agnostic."""
        return (self.company, self.date, self.direction.value, self.depart_time, self.vehicle)


def classify_vehicle(vehicle_name: str) -> str:
    """Group a free-form vehicle name into Minivan / Night bus / Bus."""
    name = vehicle_name.lower()
    if "minivan" in name or "van" in name:
        return "Minivan"
    if "sleep" in name or "night" in name:
        return "Night bus"
    return "Bus"
