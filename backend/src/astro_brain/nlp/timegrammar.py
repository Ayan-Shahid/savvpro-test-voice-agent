"""Recursive-descent grammar for nights, clock times, windows, durations and relative edits.

Works on normalised tokens, skipping tokens already claimed by the lexicon. Produces symbolic
references (`TimeRef`, `NightRef`); turning them into instants needs the night and the telescope,
which the brain resolves (see `resolve.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
WEEKDAY_ABBR = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?$")
EVENING_WORDS = {"evening", "night", "tonight"}


@dataclass(frozen=True, slots=True)
class TimeRef:
    hour: int  # 0..23 as spoken (1..12 when meridiem is unknown)
    minute: int = 0
    meridiem: str | None = None  # "am" | "pm" | None
    special: str | None = None  # "midnight" | "noon"

    @property
    def is_24h(self) -> bool:
        return self.hour > 12 or self.hour == 0


@dataclass(frozen=True, slots=True)
class NightRef:
    kind: str  # "offset" | "weekday" | "date" | "past"
    offset: int = 0
    weekday: int = 0
    next_week: bool = False
    month: int = 0
    day: int = 0
    text: str = ""


@dataclass
class TimeParse:
    night: NightRef | None = None
    start: TimeRef | None = None
    end: TimeRef | None = None
    duration: int | None = None
    shift: int | None = None  # minutes, relative edit of the current window
    extend: int | None = None
    same_time_next: bool = False
    used: set[int] = field(default_factory=set)


class _Cursor:
    def __init__(self, tokens: list[str], claimed: list[bool]) -> None:
        self.toks = tokens
        self.claimed = claimed

    def tok(self, i: int) -> str:
        if 0 <= i < len(self.toks) and not self.claimed[i]:
            return self.toks[i]
        return ""


def _int(s: str) -> int | None:
    return int(s) if s.isdigit() else None


def parse_duration(c: _Cursor, i: int) -> tuple[int, int] | None:
    """Returns (minutes, next index)."""
    t = c.tok(i)
    if t == "half" and c.tok(i + 1) == "an" and c.tok(i + 2) in {"hour", "hr"}:
        return 30, i + 3
    n: float | None = None
    j = i
    if t in {"an", "a"} and c.tok(i + 1) in {"hour", "hr"}:
        n, j = 1, i + 1
    elif (v := _int(t)) is not None:
        n, j = v, i + 1
    if n is None:
        return None
    unit = c.tok(j)
    if unit in {"hour", "hours", "hr", "hrs"}:
        minutes = int(n * 60)
        if c.tok(j + 1) == "and" and c.tok(j + 2) == "a" and c.tok(j + 3) == "half":
            return minutes + 30, j + 4
        return minutes, j + 1
    if unit == "and" and c.tok(j + 1) == "a" and c.tok(j + 2) == "half" and c.tok(j + 3) in {"hour", "hours"}:
        return int(n * 60) + 30, j + 4
    if unit in {"minutes", "minute", "mins", "min"}:
        return int(n), j + 1
    return None


def parse_clock(c: _Cursor, i: int) -> tuple[TimeRef, int] | None:
    t = c.tok(i)
    if t == "midnight":
        return TimeRef(0, 0, None, "midnight"), i + 1
    if t in {"noon", "midday"}:
        return TimeRef(12, 0, None, "noon"), i + 1
    if t == "a" and c.tok(i + 1) == "quarter":
        return parse_clock(c, i + 1)
    if t in {"half", "quarter"} and c.tok(i + 1) in {"past", "after", "to", "before"}:
        rel = c.tok(i + 1)
        tgt = parse_clock(c, i + 2)
        if tgt is None:
            return None
        base, j = tgt
        add = 30 if t == "half" else 15
        if rel in {"to", "before"}:
            if base.special == "midnight":
                return TimeRef(23, 60 - add, "pm"), j
            h = base.hour - 1 if base.hour > 0 else 23
            if base.hour == 1 and base.meridiem is None:
                h = 12  # "quarter to one" = 12:45
            return TimeRef(h, 60 - add, base.meridiem), j
        return TimeRef(base.hour, add, base.meridiem), j
    m = _CLOCK.match(t)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    j = i + 1
    nxt = c.tok(j)
    if (
        m.group(2) is None
        and nxt.isdigit()
        and len(nxt) == 2
        and 0 <= int(nxt) <= 59
        and hour <= 12
        and c.tok(j + 1) not in {"hours", "hour", "minutes", "mins"}
    ):
        minute = int(nxt)  # "eleven thirty" -> 11 30
        j += 1
    mer: str | None = None
    if c.tok(j) in {"am", "pm"}:
        mer = c.tok(j)
        j += 1
    elif c.tok(j) == "in" and c.tok(j + 1) == "the" and c.tok(j + 2) in {"morning", "evening", "afternoon"}:
        mer = "am" if c.tok(j + 2) == "morning" else "pm"
        j += 3
    elif c.tok(j) == "at" and c.tok(j + 1) == "night":
        mer = "pm" if hour >= 6 else "am"
        j += 2
    if mer and hour > 12:
        mer = None
    return TimeRef(hour, minute, mer), j


def _night_at(c: _Cursor, i: int) -> tuple[NightRef, int] | None:
    t = c.tok(i)
    if t in {"tonight", "tonite"}:
        return NightRef("offset", 0, text="tonight"), i + 1
    if t == "this" and c.tok(i + 1) in {"evening", "night", "afternoon"}:
        return NightRef("offset", 0, text="tonight"), i + 2
    if t in {"last", "yesterday"} and (t == "yesterday" or c.tok(i + 1) in {"night", "evening"}):
        return NightRef("past", text="last night"), i + (1 if t == "yesterday" else 2)
    if t == "the" and c.tok(i + 1) == "day" and c.tok(i + 2) == "after" and c.tok(i + 3) == "tomorrow":
        return NightRef("offset", 2, text="the day after tomorrow"), i + 4
    if t == "tomorrow":
        j = i + 1
        if c.tok(j) in {"night", "evening"}:
            j += 1
        return NightRef("offset", 1, text="tomorrow"), j
    nxt = False
    j = i
    if t in {"this", "next", "on", "coming"}:
        nxt = t == "next"
        j = i + 1
    w = c.tok(j)
    if w in WEEKDAYS or (w in WEEKDAY_ABBR and (j > i or c.tok(j + 1) == "night")):
        wd = WEEKDAYS.get(w, WEEKDAY_ABBR.get(w, 0))
        k = j + 1
        if c.tok(k) in {"night", "evening"}:
            k += 1
        return NightRef("weekday", weekday=wd, next_week=nxt, text=w), k
    # dates: "october 30 th", "30 th october", "30 of october", "the 30 th", "30 slash 10"
    if (t in MONTHS and t not in {"may", "mar", "sun", "sat"}) or (t in {"may", "mar"} and _int(c.tok(i + 1))):
        d = _int(c.tok(i + 1))
        if d and 1 <= d <= 31:
            k = i + 2
            if c.tok(k) in {"th", "st", "nd", "rd"}:
                k += 1
            return NightRef("date", month=MONTHS[t], day=d, text=f"{t} {d}"), k
    d = _int(t)
    if d is not None and 1 <= d <= 31:
        k = i + 1
        has_ord = False
        if c.tok(k) in {"th", "st", "nd", "rd"}:
            k += 1
            has_ord = True
        if c.tok(k) == "of":
            k += 1
        if c.tok(k) in MONTHS:
            return NightRef("date", month=MONTHS[c.tok(k)], day=d, text=f"{d} {c.tok(k)}"), k + 1
        if c.tok(i + 1) == "slash" and _int(c.tok(i + 2)):
            mo = int(c.tok(i + 2))
            if 1 <= mo <= 12:
                return NightRef("date", month=mo, day=d, text=f"{d}/{mo}"), i + 3
        if has_ord and c.tok(i - 1) == "the":
            return NightRef("date", month=0, day=d, text=f"the {d}"), k
    return None


def parse_times(tokens: list[str], claimed: list[bool]) -> TimeParse:
    c = _Cursor(tokens, claimed)
    out = TimeParse()
    i = 0
    n = len(tokens)

    def mark(a: int, b: int) -> None:
        out.used.update(range(a, b))

    while i < n:
        if claimed[i]:
            i += 1
            continue
        t = c.tok(i)
        # relative edits of the current window
        if t == "same" and c.tok(i + 1) == "time" and c.tok(i + 2) == "tomorrow":
            out.same_time_next = True
            mark(i, i + 3)
            i += 3
            continue
        dur = parse_duration(c, i)
        if dur and c.tok(dur[1]) in {"later", "earlier"}:
            out.shift = dur[0] if c.tok(dur[1]) == "later" else -dur[0]
            mark(i, dur[1] + 1)
            i = dur[1] + 1
            continue
        if (
            t in {"push", "move", "shift"}
            and c.tok(i + 1) in {"it", "that"}
            and c.tok(i + 2) in {"back", "later", "forward", "earlier"}
        ):
            d2 = parse_duration(c, i + 4) if c.tok(i + 3) == "by" else None
            minutes = d2[0] if d2 else 60
            out.shift = minutes if c.tok(i + 2) in {"back", "later"} else -minutes
            end_i = d2[1] if d2 else i + 3
            mark(i, end_i)
            i = end_i
            continue
        if t in {"extend", "lengthen"}:
            j = i + 1
            if c.tok(j) in {"it", "that"}:
                j += 1
            if c.tok(j) == "by":
                j += 1
            d2 = parse_duration(c, j)
            if d2:
                out.extend = d2[0]
                mark(i, d2[1])
                i = d2[1]
                continue
        night = _night_at(c, i)
        if night and out.night is None:
            out.night = night[0]
            mark(i, night[1])
            i = night[1]
            continue
        # "for two hours starting at 10"
        if t == "for":
            d2 = parse_duration(c, i + 1)
            if d2:
                out.duration = d2[0]
                j = d2[1]
                if c.tok(j) in {"starting", "beginning", "from"}:
                    k = j + 1 + (1 if c.tok(j + 1) == "at" else 0)
                    clk = parse_clock(c, k)
                    if clk:
                        out.start = clk[0]
                        mark(i, clk[1])
                        i = clk[1]
                        continue
                mark(i, j)
                i = j
                continue
        # windows: (from|between)? CLOCK (to|till|until|and|through) CLOCK
        k = i + 1 if t in {"from", "between", "at", "around", "about", "starting", "start"} else i
        if t == "starting" and c.tok(k) == "at":
            k += 1
        clk = parse_clock(c, k)
        if clk is not None and (k > i or _CLOCK.match(t) or t in {"midnight", "noon", "half", "quarter", "a"}):
            first, j = clk
            if c.tok(j) in {"to", "till", "until", "and", "through", "thru"}:
                second = parse_clock(c, j + 1)
                if second is not None:
                    out.start, out.end = first, second[0]
                    mark(i, second[1])
                    i = second[1]
                    continue
            # a bare number is only a time if a cue word or a meridiem marks it
            if k > i or first.meridiem or first.special or ":" in t or c.tok(i - 1) in {"at", "from"}:
                if c.tok(j) == "for":
                    d2 = parse_duration(c, j + 1)
                    if d2:
                        out.duration = d2[0]
                        out.start = first
                        mark(i, d2[1])
                        i = d2[1]
                        continue
                out.start = first
                mark(i, j)
                i = j
                continue
        if t in {"until", "till", "to"} and out.end is None:
            clk2 = parse_clock(c, i + 1)
            if clk2 is not None and (clk2[0].meridiem or clk2[0].special or out.start is not None or t != "to"):
                out.end = clk2[0]
                mark(i, clk2[1])
                i = clk2[1]
                continue
        i += 1
    return out
