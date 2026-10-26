"""Deterministic simulated weather. Same site + date + scenario pins => same forecast, forever.

Python's `hash()` is salted per process, so seeds come from BLAKE2b instead.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, timedelta

from .timeutil import night_start
from .types import WeatherHour

# Demo scenario pins, keyed by offset in nights from the seed anchor (see Backend Schema, seed data).
SCENARIOS: dict[int, dict[str, float | int | str]] = {
    0: {"cloud_pct": 10, "cloud_type": "clear", "pwv_mm": 3.0, "rain_pct": 0, "wind_kmh": 12},
    1: {"cloud_pct": 85, "cloud_type": "cirrus", "pwv_mm": 2.6, "rain_pct": 5, "wind_kmh": 15},
    2: {"cloud_pct": 8, "cloud_type": "clear", "pwv_mm": 3.2, "rain_pct": 0, "wind_kmh": 10},
    3: {"cloud_pct": 12, "cloud_type": "clear", "pwv_mm": 3.4, "rain_pct": 0, "wind_kmh": 14},
    5: {"cloud_pct": 95, "cloud_type": "stratus", "pwv_mm": 7.2, "rain_pct": 60, "wind_kmh": 25},
}


def seed_for(site_slug: str, night: date) -> int:
    digest = hashlib.blake2b(f"{site_slug}:{night.isoformat()}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def simulate_night(site_slug: str, night: date, offset_min: int, anchor: date | None = None) -> list[WeatherHour]:
    """24 hourly values from local noon of `night` to the next local noon."""
    start = night_start(night, offset_min)
    pin = SCENARIOS.get((night - anchor).days) if anchor is not None else None
    hours: list[WeatherHour] = []
    if pin is not None:
        for h in range(24):
            hours.append(
                WeatherHour(
                    start + h * 60,
                    int(pin["cloud_pct"]),
                    str(pin["cloud_type"]),
                    float(pin["pwv_mm"]),
                    int(pin["rain_pct"]),
                    int(pin["wind_kmh"]),
                )
            )
        return hours
    rng = random.Random(seed_for(site_slug, night))  # noqa: S311 - simulation, not security
    regime = rng.random()
    base_cloud = 8 if regime < 0.6 else (35 if regime < 0.85 else 80)
    base_pwv = 2.0 + rng.random() * 4.0
    cloud = float(base_cloud)
    for h in range(24):
        cloud = max(0.0, min(100.0, 0.7 * cloud + 0.3 * base_cloud + rng.uniform(-8, 8)))
        ctype = "clear" if cloud < 15 else ("cirrus" if cloud < 60 else "cumulus")
        rain = 0 if cloud < 60 else int(rng.uniform(0, 40))
        hours.append(
            WeatherHour(
                start + h * 60,
                round(cloud),
                ctype,
                round(base_pwv + rng.uniform(-0.5, 0.5), 1),
                rain,
                int(rng.uniform(5, 30)),
            )
        )
    return hours


def simulate(site_slug: str, first: date, nights: int, offset_min: int, anchor: date | None) -> list[WeatherHour]:
    out: list[WeatherHour] = []
    for i in range(nights):
        out.extend(simulate_night(site_slug, first + timedelta(days=i), offset_min, anchor))
    return out
