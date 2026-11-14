"""Small helpers: money formatting, distances and a clock that a simulation can move forward."""
from __future__ import annotations

import math
from datetime import datetime, timedelta


def usd(cents: int | float) -> str:
    return f"${cents / 100:,.2f}"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class SimClock:
    """Simulated time. Scenes start at 2:07am on Saturday, November 14 2026."""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 11, 14, 2, 7, 0)

    def now(self) -> datetime:
        return self._now

    def advance(self, minutes: float) -> None:
        self._now += timedelta(minutes=minutes)

    def set(self, when: datetime) -> None:
        self._now = when


def tou_period(when: datetime) -> tuple[str, float]:
    """Illustrative time-of-use bands shaped like Ontario's (off-peak nights and weekends).

    The multipliers are demo values, not Ontario's published rates.
    """
    hour = when.hour
    if when.weekday() >= 5 or hour >= 19 or hour < 7:
        return "off-peak", 1.0
    if 11 <= hour < 17:
        return "mid-peak", 1.25
    return "on-peak", 1.5
