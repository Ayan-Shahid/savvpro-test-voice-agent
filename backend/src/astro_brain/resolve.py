"""Turn symbolic night/time references into instants, applying the house rules for times."""

from __future__ import annotations

from datetime import date, timedelta

from .nlp.timegrammar import NightRef, TimeRef
from .timeutil import at_night, night_of, to_local
from .types import Minute, Telescope


def resolve_night(ref: NightRef, now: Minute, offset: int) -> tuple[date | None, str | None]:
    current = night_of(now, offset)
    today = to_local(now, offset).date()
    if ref.kind == "past":
        return None, "past"
    if ref.kind == "offset":
        return current + timedelta(days=ref.offset), None
    if ref.kind == "weekday":
        delta = (ref.weekday - today.weekday()) % 7
        d = today + timedelta(days=delta)
        if ref.next_week:
            d += timedelta(days=7)
        return max(d, current), None
    if ref.kind == "date":
        month = ref.month or today.month
        year = today.year
        try:
            d = date(year, month, ref.day)
        except ValueError:
            return None, "invalid_date"
        if d < current:
            if ref.month:
                d = date(year + 1, month, ref.day)
            else:
                nm = month % 12 + 1
                ny = year + (1 if month == 12 else 0)
                try:
                    d = date(ny, nm, ref.day)
                except ValueError:
                    return None, "invalid_date"
        return d, None
    return None, "unknown"


def _hour24(ref: TimeRef, daytime_ok: bool) -> int | None:
    """None means 'ask am or pm'."""
    if ref.special == "midnight":
        return 0
    if ref.special == "noon":
        return 12
    if ref.meridiem == "pm":
        return ref.hour % 12 + 12
    if ref.meridiem == "am":
        return ref.hour % 12
    if ref.hour > 12 or ref.hour == 0:
        return ref.hour
    if daytime_ok:
        return None
    if 6 <= ref.hour <= 11:
        return ref.hour + 12
    if ref.hour == 12:
        return 0
    return ref.hour  # 1..5 -> after midnight


def infer_meridiems(start: TimeRef | None, end: TimeRef | None) -> tuple[TimeRef | None, TimeRef | None]:
    """'from 2 to 4 AM' -> both AM; 'from 11 to 2 AM' -> 11 PM."""
    if start and end and start.meridiem is None and start.special is None and end.meridiem and start.hour <= 12:
        same = start.hour % 12 <= end.hour % 12
        mer = end.meridiem if same else ("pm" if end.meridiem == "am" else "am")
        start = TimeRef(start.hour, start.minute, mer)
    if start and end and end.meridiem is None and end.special is None and start.meridiem and end.hour <= 12:
        mer = "am" if start.meridiem == "pm" and end.hour % 12 < start.hour % 12 else start.meridiem
        end = TimeRef(end.hour, end.minute, mer)
    return start, end


def resolve_time(ref: TimeRef, night: date, telescope: Telescope | None, offset: int) -> Minute | None:
    daytime_ok = telescope is not None and not telescope.needs_darkness
    h = _hour24(ref, daytime_ok)
    if h is None:
        return None
    return at_night(night, h, ref.minute, offset)
