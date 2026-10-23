"""Source registry."""

from __future__ import annotations

from buswatch.sources.base import Source
from buswatch.sources.cambolink21 import Cambolink21Source
from buswatch.sources.larryta import LarrytaSource
from buswatch.sources.saly_vip import SalyVipSource

ALL_SOURCES: list[Source] = [
    LarrytaSource(),
    SalyVipSource(),
    Cambolink21Source(),
]
