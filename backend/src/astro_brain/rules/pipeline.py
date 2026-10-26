"""The rule pipeline. Fixed order; every rule reports pass/warn/fail/skip with a reason code and params.

Order (PRD, Domain rules): policy -> allocation -> capability -> never visible -> Sun safety -> altitude
-> darkness -> Moon -> weather -> maintenance -> availability -> caller overlap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..catalogue import BRIGHT_KINDS, DEEP_SKY_KINDS
from ..snapshot import DataSnapshot
from ..timeutil import night_of
from ..types import CheckResult, Draft, Minute, Target, Telescope


@dataclass(frozen=True)
class Ctx:
    draft: Draft
    target: Target
    telescope: Telescope
    start: Minute
    end: Minute
    now: Minute
    snap: DataSnapshot
    ignore_hold: str | None = None


def _ok(rule: str, **params: object) -> CheckResult:
    return CheckResult(rule, "pass", "", dict(params))


def policy(c: Ctx) -> CheckResult:
    p = c.snap.policy
    length = c.end - c.start
    if c.end <= c.start:
        return CheckResult("policy", "fail", "end_before_start")
    if c.start < c.now:
        return CheckResult("policy", "fail", "past")
    if c.start < c.now + p.lead_minutes:
        return CheckResult("policy", "fail", "too_soon", {"earliest": c.now + p.lead_minutes})
    last_night = night_of(c.now, c.snap.offset).toordinal() + p.horizon_nights
    if night_of(c.start, c.snap.offset).toordinal() > last_night:
        return CheckResult("policy", "fail", "too_far", {"horizon_nights": p.horizon_nights})
    if length > p.max_minutes:
        return CheckResult("policy", "fail", "too_long", {"max": p.max_minutes})
    if length < p.min_minutes:
        return CheckResult("policy", "fail", "too_short", {"min": p.min_minutes})
    return _ok("policy")


def allocation(c: Ctx) -> CheckResult:
    left = c.snap.user.remaining_minutes
    if c.end - c.start > left:
        return CheckResult("allocation", "fail", "allocation_exhausted", {"left": left})
    return _ok("allocation", left=left)


def capability(c: Ctx) -> CheckResult:
    t, s = c.target, c.telescope
    allowed = t.kind in s.allowed_kinds
    if s.kind == "submm_dish" and t.radio_ok is not None:
        allowed = t.radio_ok
    elif t.kind == "nebula" and s.kind == "submm_dish":
        allowed = bool(t.radio_ok)
    if (
        allowed
        and s.max_magnitude_extended is not None
        and t.kind in DEEP_SKY_KINDS
        and (t.magnitude or 99) > s.max_magnitude_extended
    ):
        return CheckResult("capability", "fail", "too_faint", {"mag": t.magnitude})
    if not allowed:
        return CheckResult("capability", "fail", "not_capable")
    return _ok("capability")


def never_visible(c: Ctx) -> CheckResult:
    t, s = c.target, c.telescope
    if t.dec_deg is not None:
        peak = 90 - abs(c.snap.site.lat_deg - t.dec_deg)
        if peak < s.min_altitude_deg:
            reason = "never_rises" if peak < 0 else "never_high"
            return CheckResult("never_visible", "fail", reason, {"peak": round(peak, 1)})
    return _ok("never_visible")


def _samples(c: Ctx) -> list[dict[str, float]] | None:
    night = c.snap.night_of(c.start)
    if not c.snap.has_sky(night) or (c.target.slug, night) not in c.snap.sky:
        return None
    return c.snap.samples(c.target.slug, night, c.start, c.end)


def sun_safety(c: Ctx) -> CheckResult:
    if c.target.slug == "sun":
        return CheckResult("sun_safety", "fail", "is_sun")
    ss = _samples(c)
    if ss is None:
        return CheckResult("sun_safety", "skip", "no_sky")
    worst = min(ss, key=lambda s: s["sun_sep"], default=None)
    if worst and any(s["sun_sep"] < c.telescope.sun_avoid_deg and s["sun_alt"] > -12 for s in ss):
        return CheckResult(
            "sun_safety",
            "fail",
            "too_close_to_sun",
            {"sep": round(worst["sun_sep"], 1), "limit": c.telescope.sun_avoid_deg},
        )
    return _ok("sun_safety")


def altitude(c: Ctx) -> CheckResult:
    ss = _samples(c)
    if ss is None:
        return CheckResult("altitude", "skip", "no_sky")
    low = [s for s in ss if s["alt"] < c.telescope.min_altitude_deg]
    if low:
        first = low[0]
        reason = "below_horizon" if first["alt"] < 0 else "too_low"
        return CheckResult(
            "altitude",
            "fail",
            reason,
            {"at": int(first["t"]), "alt": round(first["alt"], 1), "min": c.telescope.min_altitude_deg},
        )
    return _ok("altitude", min_alt=round(min(s["alt"] for s in ss), 1) if ss else None)


def darkness(c: Ctx) -> CheckResult:
    s = c.telescope
    if not s.needs_darkness:
        return CheckResult("darkness", "skip", "not_required")
    limit = s.sun_max_alt_bright if c.target.kind in BRIGHT_KINDS else s.sun_max_alt_faint
    assert limit is not None
    ss = _samples(c)
    if ss is None:
        return CheckResult("darkness", "skip", "no_sky")
    bright = [x for x in ss if x["sun_alt"] > limit]
    if bright:
        return CheckResult("darkness", "fail", "not_dark", {"at": int(bright[0]["t"]), "limit": limit})
    return _ok("darkness", limit=limit)


def moon(c: Ctx) -> CheckResult:
    ss = _samples(c)
    if ss is None:
        return CheckResult("moon", "skip", "no_sky")
    night = c.snap.nights[c.snap.night_of(c.start)]
    illum = night.moon_illum_pct
    up = [x for x in ss if x["moon_alt"] > 0]
    if c.telescope.kind == "submm_dish":
        near = [x for x in ss if x["moon_sep"] < c.telescope.moon_avoid_deg]
        if near and c.target.slug != "moon":
            return CheckResult("moon", "fail", "moon_in_beam", {"sep": round(near[0]["moon_sep"], 1)})
        return _ok("moon")
    if c.target.kind not in DEEP_SKY_KINDS or not up:
        return _ok("moon")
    sep = min(x["moon_sep"] for x in up)
    params = {"illum": illum, "sep": round(sep)}
    if (illum >= 75 and sep < 45) or sep < 10:
        return CheckResult("moon", "fail", "moon_glare", params)
    if illum >= 50 and sep < 60:
        return CheckResult("moon", "warn", "moon_bright", params)
    return _ok("moon", **params)


def weather(c: Ctx) -> CheckResult:
    hours = c.snap.weather_for(c.start, c.end)
    s = c.telescope
    if hours is None:
        return CheckResult("weather", "warn", "weather_pending")
    for h in hours:
        if s.max_cloud_pct is not None and h.cloud_pct > s.max_cloud_pct:
            return CheckResult(
                "weather",
                "fail",
                "cloud",
                {"cloud": h.cloud_pct, "limit": s.max_cloud_pct, "type": h.cloud_type, "pwv": h.pwv_mm},
            )
        if s.max_pwv_mm is not None and h.pwv_mm > s.max_pwv_mm:
            return CheckResult("weather", "fail", "pwv", {"pwv": h.pwv_mm, "limit": s.max_pwv_mm, "rain": h.rain_pct})
        if h.rain_pct > s.max_rain_pct:
            return CheckResult("weather", "fail", "rain", {"rain": h.rain_pct, "limit": s.max_rain_pct})
        if h.wind_kmh > s.max_wind_kmh:
            return CheckResult("weather", "fail", "wind", {"wind": h.wind_kmh, "limit": s.max_wind_kmh})
    worst = max(hours, key=lambda h: h.cloud_pct)
    return _ok("weather", cloud=worst.cloud_pct, pwv=worst.pwv_mm)


def maintenance(c: Ctx) -> CheckResult:
    for b in c.snap.conflicts(c.telescope.code, c.start, c.end):
        if b.kind == "maintenance":
            return CheckResult("maintenance", "fail", "maintenance", {"start": b.start, "end": b.end})
    return _ok("maintenance")


def _is_own_hold(booking_id: str | None, c: Ctx) -> bool:
    """The caller's current hold never conflicts with itself. Anonymous rows (id None) always count."""
    return c.ignore_hold is not None and booking_id == c.ignore_hold


def availability(c: Ctx) -> CheckResult:
    for b in c.snap.conflicts(c.telescope.code, c.start, c.end):
        if b.kind != "maintenance" and not _is_own_hold(b.booking_id, c) and not b.mine:
            return CheckResult("availability", "fail", "taken", {"start": b.start, "end": b.end})
    return _ok("availability")


def caller_overlap(c: Ctx) -> CheckResult:
    for b in c.snap.user.own:
        if not _is_own_hold(b.booking_id, c) and b.start < c.end and c.start < b.end:
            return CheckResult(
                "caller_overlap", "fail", "self_overlap", {"telescope": b.telescope, "start": b.start, "end": b.end}
            )
    return _ok("caller_overlap")


RULES: tuple[Callable[[Ctx], CheckResult], ...] = (
    policy,
    allocation,
    capability,
    never_visible,
    sun_safety,
    altitude,
    darkness,
    moon,
    weather,
    maintenance,
    availability,
    caller_overlap,
)
SKY_RULES = (
    capability,
    never_visible,
    sun_safety,
    altitude,
    darkness,
    moon,
    weather,
    maintenance,
    availability,
    caller_overlap,
)


def run(c: Ctx, rules: tuple[Callable[[Ctx], CheckResult], ...] = RULES) -> list[CheckResult]:
    return [r(c) for r in rules]


def first_failure(results: list[CheckResult]) -> CheckResult | None:
    return next((r for r in results if r.status == "fail"), None)
