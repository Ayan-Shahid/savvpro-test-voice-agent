"""Local-time arithmetic and spoken formatting for a fixed-offset site. Pure functions."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from .types import Minute

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def tz(offset_min: int) -> timezone:
    return timezone(timedelta(minutes=offset_min))


def to_local(m: Minute, offset_min: int) -> datetime:
    return datetime.fromtimestamp(m * 60, tz=UTC).astimezone(tz(offset_min))


def local_minute(d: date, hour: int, minute: int, offset_min: int) -> Minute:
    dt = datetime(d.year, d.month, d.day, tzinfo=tz(offset_min)) + timedelta(hours=hour, minutes=minute)
    return int(dt.timestamp()) // 60


def night_start(night: date, offset_min: int) -> Minute:
    """Local noon that opens the observing night named after `night`."""
    return local_minute(night, 12, 0, offset_min)


def night_of(m: Minute, offset_min: int) -> date:
    """The observing night containing instant `m` (nights run local noon to local noon)."""
    loc = to_local(m, offset_min)
    return loc.date() if loc.hour >= 12 else loc.date() - timedelta(days=1)


def at_night(night: date, hour: int, minute: int, offset_min: int) -> Minute:
    """Clock time on a night: hours before noon belong to the morning after the named date."""
    d = night if hour >= 12 else night + timedelta(days=1)
    return local_minute(d, hour, minute, offset_min)


def fmt_time(m: Minute, offset_min: int, style: str = "12h") -> str:
    loc = to_local(m, offset_min)
    if style == "24h":
        return f"{loc.hour:02d}:{loc.minute:02d}"
    if loc.hour == 0 and loc.minute == 0:
        return "midnight"
    if loc.hour == 12 and loc.minute == 0:
        return "noon"
    h = loc.hour % 12 or 12
    suffix = "AM" if loc.hour < 12 else "PM"
    return f"{h} {suffix}" if loc.minute == 0 else f"{h}:{loc.minute:02d} {suffix}"


def fmt_date(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def fmt_window(start: Minute, end: Minute, offset_min: int, style: str = "12h") -> str:
    """'Tuesday 27 October, 11 PM to 2 AM' — always names the calendar day of the start."""
    s = to_local(start, offset_min)
    return f"{fmt_date(s.date())}, {fmt_time(start, offset_min, style)} to {fmt_time(end, offset_min, style)}"


def snap_down(m: Minute, grid: int) -> Minute:
    return m - (m % grid)


def snap_up(m: Minute, grid: int) -> Minute:
    return m if m % grid == 0 else m + grid - (m % grid)
