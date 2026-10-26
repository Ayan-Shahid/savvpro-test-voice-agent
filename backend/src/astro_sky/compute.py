"""Precompute the sky for a range of observing nights with Skyfield on JPL DE421.

Output is plain JSON-able data (tenths of degrees on a 5-minute grid from local noon to local noon),
consumed by `astro_brain.snapshot`. Deterministic: same inputs, same numbers.
"""

from __future__ import annotations

import warnings
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import skyfield_data
from skyfield import almanac
from skyfield.api import Loader, Star, wgs84

from astro_brain.catalogue import SITE, TARGETS
from astro_brain.types import Site

STEP_MIN = 5
SAMPLES = 24 * 60 // STEP_MIN  # 288 per night

warnings.filterwarnings("ignore", module="skyfield_data")


class SkyCalculator:
    def __init__(self, site: Site = SITE) -> None:
        self.site = site
        load = Loader(skyfield_data.get_skyfield_data_path(), expire=False, verbose=False)
        self.ts = load.timescale(builtin=True)
        self.eph = load("de421.bsp")
        self.earth = self.eph["earth"]
        self.topos = wgs84.latlon(site.lat_deg, site.lon_deg, elevation_m=site.elevation_m)
        self.observer = self.earth + self.topos
        self.tz = timezone(timedelta(minutes=site.tz_offset_min))

    def _body(self, slug: str) -> Any:
        t = TARGETS[slug]
        if t.ephemeris_body:
            return self.eph[t.ephemeris_body]
        return Star(ra_hours=t.ra_hours, dec_degrees=t.dec_deg)

    def night(self, night: date) -> dict[str, Any]:
        start_local = datetime(night.year, night.month, night.day, 12, 0, tzinfo=self.tz)
        start_utc = start_local.astimezone(UTC)
        start_min = int(start_utc.timestamp()) // 60
        minutes = np.arange(SAMPLES) * STEP_MIN
        t = self.ts.utc(start_utc.year, start_utc.month, start_utc.day, start_utc.hour, start_utc.minute + minutes)
        obs = self.observer.at(t)
        sun = obs.observe(self.eph["sun"]).apparent()
        moon = obs.observe(self.eph["moon"]).apparent()
        sun_alt = sun.altaz()[0].degrees
        moon_alt = moon.altaz()[0].degrees
        mid = self.ts.from_datetime((start_local + timedelta(hours=12)).astimezone(UTC))
        illum = float(almanac.fraction_illuminated(self.eph, "moon", mid)) * 100
        targets: dict[str, Any] = {}
        for slug in TARGETS:
            if slug == "moon":
                alt = moon_alt
                msep = np.zeros(SAMPLES)
                ssep = moon.separation_from(sun).degrees
            else:
                app = obs.observe(self._body(slug)).apparent()
                alt = app.altaz()[0].degrees
                msep = app.separation_from(moon).degrees
                ssep = app.separation_from(sun).degrees
            targets[slug] = {
                "alt": [round(v * 10) for v in alt],
                "moon_sep": [round(v * 10) for v in msep],
                "sun_sep": [round(v * 10) for v in ssep],
            }
        return {
            "night": night.isoformat(),
            "start": start_min,
            "step": STEP_MIN,
            "sun_alt": [round(v * 10) for v in sun_alt],
            "moon_alt": [round(v * 10) for v in moon_alt],
            "moon_illum_pct": round(illum),
            "targets": targets,
        }

    def nights(self, first: date, count: int) -> list[dict[str, Any]]:
        return [self.night(first + timedelta(days=i)) for i in range(count)]
