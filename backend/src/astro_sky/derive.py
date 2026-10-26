"""Derived per-night facts from the 5-minute grid: twilight times and visibility windows.

Pure functions over the JSON produced by `compute.SkyCalculator`, so they run without Skyfield.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any


def _crossings(start: int, step: int, values: list[int], level: float) -> list[tuple[int, bool]]:
    """(minute, rising) for each crossing of `level` (tenths of a degree), linearly interpolated."""
    out: list[tuple[int, bool]] = []
    lv = level * 10
    for i, (a, b) in enumerate(pairwise(values)):
        if (a < lv) != (b < lv):
            frac = (lv - a) / (b - a)
            out.append((start + round((i + frac) * step), b > a))
    return out


def twilight(night: dict[str, Any]) -> dict[str, int]:
    """Sunset, dusks, dawns and sunrise in epoch minutes for a noon-to-noon night."""
    start, step, sun = night["start"], night["step"], night["sun_alt"]
    out: dict[str, int] = {}
    for name_set, name_rise, level in (
        ("sunset", "sunrise", -0.833),
        ("nautical_dusk", "nautical_dawn", -12.0),
        ("astro_dusk", "astro_dawn", -18.0),
    ):
        xs = _crossings(start, step, sun, level)
        sets = [m for m, rising in xs if not rising]
        rises = [m for m, rising in xs if rising]
        # Fallbacks keep the row valid at latitudes where a level is never reached (not at 30 N).
        out[name_set] = sets[0] if sets else start + 12 * 60
        out[name_rise] = rises[-1] if rises else start + 12 * 60
    return out


def windows(night: dict[str, Any], slug: str, min_alt: float) -> list[tuple[int, int]]:
    """Half-open [start, end) minute ranges when the target is at or above `min_alt` while the Sun is down."""
    start, step = night["start"], night["step"]
    alt = night["targets"][slug]["alt"]
    sun = night["sun_alt"]
    out: list[tuple[int, int]] = []
    cur: int | None = None
    for i, (a, s) in enumerate(zip(alt, sun, strict=True)):
        ok = a >= min_alt * 10 and s <= -8
        t = start + i * step
        if ok and cur is None:
            cur = t
        elif not ok and cur is not None:
            out.append((cur, t))
            cur = None
    if cur is not None:
        out.append((cur, start + len(alt) * step))
    return out


def transit(night: dict[str, Any], slug: str) -> tuple[int, float]:
    alt = night["targets"][slug]["alt"]
    i = max(range(len(alt)), key=lambda k: alt[k])
    return night["start"] + i * night["step"], alt[i] / 10
