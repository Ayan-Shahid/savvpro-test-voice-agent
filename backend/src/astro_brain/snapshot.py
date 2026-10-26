"""DataSnapshot: everything the brain may know about the world for one turn, frozen.

Built by the API from Postgres/Redis (or by tests from fixtures). The brain never fetches anything.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .catalogue import SITE, TARGETS, TELESCOPES
from .interval_tree import IntervalTree
from .timeutil import night_of
from .types import (
    Busy,
    Minute,
    NightSky,
    Policy,
    Site,
    Target,
    TargetSky,
    Telescope,
    UserContext,
    WeatherHour,
)
from .weather import simulate


@dataclass(frozen=True)
class DataSnapshot:
    version: str
    anchor: date
    site: Site
    telescopes: dict[str, Telescope]
    targets: dict[str, Target]
    nights: dict[date, NightSky]
    sky: dict[tuple[str, date], TargetSky]
    weather: dict[Minute, WeatherHour]
    busy: tuple[Busy, ...]
    policy: Policy = field(default_factory=Policy)
    user: UserContext = field(default_factory=UserContext)
    _trees: dict[str, IntervalTree] = field(default_factory=dict, compare=False, repr=False)

    # ---- time helpers -------------------------------------------------------------------------
    @property
    def offset(self) -> int:
        return self.site.tz_offset_min

    def night_of(self, m: Minute) -> date:
        return night_of(m, self.offset)

    def has_sky(self, night: date) -> bool:
        return night in self.nights

    # ---- sampled sky ---------------------------------------------------------------------------
    def _indices(self, night: date, start: Minute, end: Minute) -> range:
        n = self.nights[night]
        lo = max(0, (start - n.start + n.step - 1) // n.step)
        hi = min(len(n.sun_alt), (end - n.start + n.step - 1) // n.step)
        return range(lo, max(lo, hi))

    def samples(self, target: str, night: date, start: Minute, end: Minute) -> list[dict[str, float]]:
        n = self.nights[night]
        ts = self.sky[(target, night)]
        return [
            {
                "t": n.start + i * n.step,
                "alt": ts.alt[i] / 10,
                "moon_sep": ts.moon_sep[i] / 10,
                "sun_sep": ts.sun_sep[i] / 10,
                "sun_alt": n.sun_alt[i] / 10,
                "moon_alt": n.moon_alt[i] / 10,
            }
            for i in self._indices(night, start, end)
        ]

    def max_alt(self, target: str) -> float:
        best = -90.0
        for (slug, _night), ts in self.sky.items():
            if slug == target:
                best = max(best, max(ts.alt) / 10)
        return best

    # ---- weather ---------------------------------------------------------------------------------
    def weather_for(self, start: Minute, end: Minute) -> list[WeatherHour] | None:
        """Hours overlapping [start, end); None when any hour is beyond the forecast."""
        out: list[WeatherHour] = []
        h = start - (start % 60)
        while h < end:
            w = self.weather.get(h)
            if w is None:
                return None
            out.append(w)
            h += 60
        return out

    # ---- schedule ----------------------------------------------------------------------------------
    def tree(self, telescope: str) -> IntervalTree:
        if telescope not in self._trees:
            t = IntervalTree()
            for b in self.busy:
                if b.telescope == telescope:
                    t.insert(b.start, b.end, b)
            self._trees[telescope] = t
        return self._trees[telescope]

    def conflicts(self, telescope: str, start: Minute, end: Minute) -> list[Busy]:
        return [p for _s, _e, p in self.tree(telescope).overlaps(start, end)]


def load_sky_json(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as fh:
        data: list[dict[str, Any]] = json.load(fh)
    return data


def build_snapshot(
    sky_nights: Iterable[dict[str, Any]],
    *,
    anchor: date,
    busy: Iterable[Busy] = (),
    user: UserContext | None = None,
    policy: Policy | None = None,
    weather: Iterable[WeatherHour] | None = None,
    site: Site = SITE,
) -> DataSnapshot:
    nights: dict[date, NightSky] = {}
    sky: dict[tuple[str, date], TargetSky] = {}
    for raw in sky_nights:
        d = date.fromisoformat(raw["night"])
        nights[d] = NightSky(
            d, raw["start"], raw["step"], tuple(raw["sun_alt"]), tuple(raw["moon_alt"]), raw["moon_illum_pct"]
        )
        for slug, t in raw["targets"].items():
            sky[(slug, d)] = TargetSky(tuple(t["alt"]), tuple(t["moon_sep"]), tuple(t["sun_sep"]))
    pol = policy or Policy()
    if weather is None:
        first = min(nights) if nights else anchor
        weather = simulate(
            site.slug, max(first, anchor - timedelta(days=1)), pol.forecast_nights + 1, site.tz_offset_min, anchor
        )
    wmap = {w.start: w for w in weather}
    busy_t = tuple(sorted(busy, key=lambda b: (b.telescope, b.start, b.end)))
    h = hashlib.blake2b(digest_size=6)
    h.update(anchor.isoformat().encode())
    h.update(repr(sorted(nights)).encode())
    h.update(repr(busy_t).encode())
    return DataSnapshot(
        version=f"snap-{anchor.isoformat()}-{h.hexdigest()}",
        anchor=anchor,
        site=site,
        telescopes=dict(TELESCOPES),
        targets=dict(TARGETS),
        nights=nights,
        sky=sky,
        weather=wmap,
        busy=busy_t,
        policy=pol,
        user=user or UserContext(),
    )
