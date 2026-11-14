"""The fleet owner's spending policy. All money is in integer US cents."""
from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class SpendPolicy:
    max_per_session_cents: int = 4000
    max_per_day_cents: int = 12000
    max_price_per_kwh_cents: int = 45
    min_charger_reputation: float = 4.0
    chunk_kwh: float = 2.0
    max_distance_to_charger_m: float = 15.0
    max_sessions_per_hour: int = 3
    meter_tolerance: float = 0.97
    reserve_kwh: float = 6.0
    opportunistic_soc: float = 0.70
    search_radius_km: float = 8.0
    payment_request_ttl_s: int = 120
    kill_switch: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "SpendPolicy":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
