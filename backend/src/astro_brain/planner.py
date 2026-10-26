"""Alternative search: valid windows are found slot by slot (15-minute grid) and ranked deterministically.

Ranking key (lower is better): (slots changed, |start shift|, minutes lost, nights ahead, telescope order).
An offer is only ever made if every sky, weather and schedule rule passes for it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from .rules.pipeline import SKY_RULES, Ctx, first_failure, run
from .snapshot import DataSnapshot
from .timeutil import night_start
from .types import CheckResult, Draft, Minute

GRID = 15


@dataclass(frozen=True)
class Runs:
    runs: tuple[tuple[Minute, Minute], ...]
    blocking: CheckResult | None  # most common failure among slots where the target is up


def slot_valid(
    snap: DataSnapshot, target: str, telescope: str, start: Minute, end: Minute, now: Minute, ignore_hold: str | None
) -> list[CheckResult]:
    ctx = Ctx(
        Draft(target, telescope), snap.targets[target], snap.telescopes[telescope], start, end, now, snap, ignore_hold
    )
    return run(ctx, SKY_RULES)


def valid_runs(
    snap: DataSnapshot, target: str, telescope: str, night: date, now: Minute, ignore_hold: str | None = None
) -> Runs:
    if not snap.has_sky(night):
        return Runs((), None)
    base = night_start(night, snap.offset)
    earliest = now + snap.policy.lead_minutes
    runs: list[tuple[Minute, Minute]] = []
    fails: Counter[tuple[str, str]] = Counter()
    first_fail: dict[tuple[str, str], CheckResult] = {}
    cur: Minute | None = None
    for k in range(24 * 60 // GRID):
        s = base + k * GRID
        e = s + GRID
        ok = s >= earliest
        if ok:
            res = slot_valid(snap, target, telescope, s, e, now, ignore_hold)
            f = first_failure(res)
            if f is not None:
                ok = False
                if f.rule != "altitude" or f.reason == "too_low":
                    key = (f.rule, f.reason)
                    fails[key] += 1
                    first_fail.setdefault(key, f)
                elif not any(x.rule != "altitude" and x.status == "fail" for x in res):
                    fails[("altitude", f.reason)] += 0  # keep altitude as a fallback reason
                    first_fail.setdefault(("altitude", f.reason), f)
        if ok and cur is None:
            cur = s
        if not ok and cur is not None:
            runs.append((cur, s))
            cur = None
    if cur is not None:
        runs.append((cur, base + 24 * 60))
    blocking = None
    if not runs:
        if fails:
            key = sorted(fails.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            blocking = first_fail[key]
        elif first_fail:
            blocking = next(iter(first_fail.values()))
    return Runs(tuple(runs), blocking)


def _best_in_runs(
    runs: tuple[tuple[Minute, Minute], ...],
    want_start: Minute,
    length: int | None,
) -> tuple[Minute, Minute] | None:
    """Nearest window of `length` (or the whole run, capped at 240) to `want_start`."""
    best: tuple[int, Minute, Minute] | None = None
    for rs, re_ in runs:
        if length is None:
            s = min(max(want_start, rs), re_ - 30)
            if s < rs:
                continue
            e = min(re_, s + 240)
            cand = (abs(s - want_start), s, e)
        else:
            if re_ - rs < length:
                continue
            s = min(max(want_start, rs), re_ - length)
            cand = (abs(s - want_start), s, s + length)
        if best is None or cand < best:
            best = cand
    return (best[1], best[2]) if best else None


@dataclass(frozen=True)
class Offer:
    draft: Draft
    key: tuple[int, int, int, int, int]  # (changed, nights ahead, shift, lost, telescope)
    why: str


def alternatives(
    snap: DataSnapshot, draft: Draft, now: Minute, ignore_hold: str | None = None, limit: int = 3
) -> list[Offer]:
    assert draft.target and draft.telescope and draft.night and draft.start is not None
    tel_order = {c: t.display_order for c, t in snap.telescopes.items()}
    length = (draft.end - draft.start) if draft.end is not None else None
    offers: list[Offer] = []

    def add(d: Draft, why: str, changed: int, nights_ahead: int) -> None:
        assert d.start is not None and draft.start is not None
        lost = 0 if length is None or d.end is None else max(0, length - (d.end - d.start))
        shift = abs(d.start - draft.start - nights_ahead * 24 * 60)
        key = (changed, nights_ahead, shift, lost, tel_order[d.telescope or ""])
        if all(o.draft != d for o in offers):
            offers.append(Offer(d, key, why))

    # 1. same telescope, same night
    runs = valid_runs(snap, draft.target, draft.telescope, draft.night, now, ignore_hold).runs
    best = _best_in_runs(runs, draft.start, length)
    if best:
        add(draft.with_(start=best[0], end=best[1]), "same_night", 1, 0)
    if length is not None and draft.end is not None:
        pieces = [(max(s, draft.start), min(e, draft.end)) for s, e in runs]
        pieces = [(s, e) for s, e in pieces if e - s >= 30]
        if pieces:
            s, e = max(pieces, key=lambda p: (p[1] - p[0], -p[0]))
            add(draft.with_(start=s, end=e), "trimmed", 1, 0)
    # 2. same time, another capable telescope (an hour assumed when no end was given)
    probe_end = draft.end if draft.end is not None else draft.start + 60
    for code in sorted(snap.telescopes, key=lambda c: tel_order[c]):
        if code == draft.telescope:
            continue
        res = slot_valid(snap, draft.target, code, draft.start, probe_end, now, ignore_hold)
        if first_failure(res) is None:
            add(draft.with_(telescope=code), "other_telescope", 1, 0)
    # 3. same clock time, later nights within the forecast
    for ahead in range(1, snap.policy.forecast_nights + 1):
        nd = draft.night + timedelta(days=ahead)
        if not snap.has_sky(nd):
            break
        shift = ahead * 24 * 60
        if draft.end is not None:
            s, e = draft.start + shift, draft.end + shift
            if first_failure(slot_valid(snap, draft.target, draft.telescope, s, e, now, ignore_hold)) is None:
                add(draft.with_(night=nd, start=s, end=e), "later_night", 1, ahead)
                break
        else:
            nr = valid_runs(snap, draft.target, draft.telescope, nd, now, ignore_hold).runs
            b = _best_in_runs(nr, draft.start + shift, None)
            if b:
                add(draft.with_(night=nd, start=b[0], end=b[1]), "later_night", 1, ahead)
                break
    offers.sort(key=lambda o: o.key)
    return offers[:limit]


def other_capable(snap: DataSnapshot, target: str, exclude: str) -> list[str]:
    from .rules.pipeline import capability

    out = []
    for code in sorted(snap.telescopes, key=lambda c: snap.telescopes[c].display_order):
        if code == exclude:
            continue
        ctx = Ctx(Draft(), snap.targets[target], snap.telescopes[code], 0, 1, 0, snap)
        if capability(ctx).status == "pass":
            out.append(code)
    return out
