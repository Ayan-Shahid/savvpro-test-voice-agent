"""Scenario harness: builds a snapshot from the committed sky fixture and replays scripted calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from astro_brain.engine import apply_effect_result, step
from astro_brain.snapshot import DataSnapshot, build_snapshot, load_sky_json
from astro_brain.timeutil import at_night, local_minute
from astro_brain.types import Busy, CallState, OwnBooking, StepResult, TurnEvent, UserContext

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sky_2026-10-26_14n.json.gz"
ANCHOR = date(2026, 10, 27)
OFFSET = 300


@cache
def sky() -> tuple[dict[str, Any], ...]:
    return tuple(load_sky_json(FIXTURE))


def hm(night: date, hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return at_night(night, h, m, OFFSET)


def make_snapshot(setup: dict[str, Any] | None = None) -> DataSnapshot:
    setup = setup or {}
    busy = []
    for b in setup.get("busy", []):
        n = date.fromisoformat(b["night"])
        busy.append(
            Busy(
                b["telescope"], hm(n, b["start"]), hm(n, b["end"]), b.get("kind", "observation"), booking_id=b.get("id")
            )
        )
    own = []
    for b in setup.get("own", []):
        n = date.fromisoformat(b["night"])
        own.append(OwnBooking(b["id"], b["telescope"], b["target"], hm(n, b["start"]), hm(n, b["end"]), n))
        busy.append(
            Busy(b["telescope"], hm(n, b["start"]), hm(n, b["end"]), "observation", mine=True, booking_id=b["id"])
        )
    u = setup.get("user", {})
    user = UserContext(
        user_id="demo",
        display_name=u.get("name", "Sam"),
        grade=u.get("grade", "B"),
        remaining_minutes=u.get("remaining_minutes", 20 * 60),
        own=tuple(own),
    )
    return build_snapshot(sky(), anchor=ANCHOR, busy=busy, user=user)


def clock(text: str) -> int:
    dt = datetime.fromisoformat(text)
    return local_minute(dt.date(), dt.hour, dt.minute, OFFSET)


@dataclass
class Call:
    snap: DataSnapshot
    now: int
    state: CallState = field(default_factory=CallState)
    holds: int = 0
    log: list[StepResult] = field(default_factory=list)

    def send(self, event: TurnEvent) -> StepResult:
        r = step(self.state, event, self.snap)
        self.state = r.state
        for e in r.effects:
            if e.kind == "hold":
                self.holds += 1
                self.state = apply_effect_result(
                    self.state, "hold_taken", hold_id=f"hold-{self.holds}", expires=self.now + 2
                )
            if e.kind == "commit":
                self.state = apply_effect_result(self.state, "committed", booking_id=f"bk-{self.holds}")
        self.log.append(r)
        return r

    def say(self, text: str, **kw: Any) -> StepResult:
        return self.send(TurnEvent("utterance", text=text, now=self.now, **kw))

    def advance(self, minutes: int) -> None:
        self.now += minutes


def new_call(setup: dict[str, Any] | None = None, at: str = "2026-10-27T20:00") -> Call:
    c = Call(make_snapshot(setup), clock(at))
    c.send(TurnEvent("start", now=c.now))
    return c


def minutes_after(night: date, hhmm: str, days: int = 0) -> int:
    return hm(night + timedelta(days=days), hhmm)
