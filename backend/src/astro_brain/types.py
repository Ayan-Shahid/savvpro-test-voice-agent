"""Immutable value types shared across the brain.

All instants are integer UTC minutes since the Unix epoch ("epoch minutes"); all dates are
observatory-local `datetime.date`. Nothing here performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Literal

Minute = int  # epoch minutes, UTC

State = Literal[
    "GREETING", "COLLECTING", "CLARIFYING", "PROPOSING", "CONFIRMING", "BOOKED", "WAITLIST", "CANCELLING", "ENDED"
]
Status = Literal["pass", "warn", "fail", "skip"]


@dataclass(frozen=True, slots=True)
class Site:
    slug: str
    name: str
    lat_deg: float
    lon_deg: float
    elevation_m: int
    tz_name: str
    tz_offset_min: int  # fixed offset; the fictional site has no daylight saving


@dataclass(frozen=True, slots=True)
class Telescope:
    code: str
    name: str
    kind: str
    min_altitude_deg: float
    sun_max_alt_bright: float | None
    sun_max_alt_faint: float | None
    sun_avoid_deg: float
    moon_avoid_deg: float
    max_cloud_pct: int | None
    max_pwv_mm: float | None
    max_rain_pct: int
    max_wind_kmh: int
    allowed_kinds: frozenset[str]
    max_magnitude_extended: float | None
    display_order: int
    min_minutes: int = 30
    max_minutes: int = 240

    @property
    def needs_darkness(self) -> bool:
        return self.sun_max_alt_bright is not None


@dataclass(frozen=True, slots=True)
class Target:
    slug: str
    name: str
    kind: str
    ephemeris_body: str | None
    ra_hours: float | None
    dec_deg: float | None
    magnitude: float | None
    catalogue_ids: tuple[str, ...]
    radio_ok: bool | None
    spoken_name: str


@dataclass(frozen=True, slots=True)
class NightSky:
    """Sky for one observing night: samples every 5 minutes from local noon to the next local noon."""

    night: date
    start: Minute  # local noon of `night`, as epoch minutes
    step: int
    sun_alt: tuple[int, ...]  # tenths of a degree
    moon_alt: tuple[int, ...]
    moon_illum_pct: int


@dataclass(frozen=True, slots=True)
class TargetSky:
    alt: tuple[int, ...]  # tenths of a degree, aligned with NightSky samples
    moon_sep: tuple[int, ...]
    sun_sep: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WeatherHour:
    start: Minute
    cloud_pct: int
    cloud_type: str
    pwv_mm: float
    rain_pct: int
    wind_kmh: int


@dataclass(frozen=True, slots=True)
class Busy:
    telescope: str
    start: Minute
    end: Minute
    kind: Literal["observation", "maintenance", "hold"]
    mine: bool = False
    booking_id: str | None = None


@dataclass(frozen=True, slots=True)
class Policy:
    lead_minutes: int = 30
    horizon_nights: int = 30
    grid_minutes: int = 15
    min_minutes: int = 30
    max_minutes: int = 240
    hold_seconds: int = 120
    forecast_nights: int = 7
    max_future_bookings: int = 6


@dataclass(frozen=True, slots=True)
class OwnBooking:
    booking_id: str
    telescope: str
    target: str
    start: Minute
    end: Minute
    night: date


@dataclass(frozen=True, slots=True)
class UserContext:
    user_id: str = "anonymous"
    display_name: str = "there"
    grade: str = "B"
    remaining_minutes: int = 20 * 60
    own: tuple[OwnBooking, ...] = ()
    time_format: Literal["12h", "24h"] = "12h"


@dataclass(frozen=True, slots=True)
class Draft:
    """The booking being assembled. Every field is optional until confirmation."""

    target: str | None = None
    telescope: str | None = None
    night: date | None = None
    start: Minute | None = None
    end: Minute | None = None
    duration: int | None = None  # minutes, when the caller gave a length but no end

    def with_(self, **kw: Any) -> Draft:
        return replace(self, **kw)

    def label(self, targets: dict[str, Target], telescopes: dict[str, Telescope], fmt: Any) -> str:
        parts = [
            targets[self.target].name if self.target else "?",
            self.telescope or "?",
            fmt(self.night) if self.night else "?",
        ]
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class CheckResult:
    rule: str
    status: Status
    reason: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Effect:
    kind: Literal["hold", "release", "commit", "cancel_booking", "join_waitlist", "end_call"]
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Frame:
    seq: int
    draft: Draft
    state: State
    pending: dict[str, Any] | None
    hold_id: str | None
    label: str
    barrier: bool = False


@dataclass(frozen=True, slots=True)
class CallState:
    state: State = "GREETING"
    draft: Draft = Draft()
    stack: tuple[Frame, ...] = ()
    seq: int = 0
    turn: int = 0
    pending: dict[str, Any] | None = None
    offers: tuple[Draft, ...] = ()
    hold_id: str | None = None
    hold_expires: Minute | None = None
    booked: tuple[str, ...] = ()
    no_input: int = 0
    no_match: int = 0
    last_reply: str = ""
    heard_full: bool = True

    def with_(self, **kw: Any) -> CallState:
        return replace(self, **kw)


@dataclass(frozen=True, slots=True)
class TurnEvent:
    kind: Literal["utterance", "silence", "undo", "hangup", "hold_failed", "hold_taken", "start"]
    text: str = ""
    confidence: float = 1.0
    now: Minute = 0
    heard_full: bool = True  # False when barge-in cut the previous reply short
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    question: bool = True


@dataclass(frozen=True, slots=True)
class StepResult:
    state: CallState
    reply: Reply
    effects: tuple[Effect, ...]
    trace: dict[str, Any]
