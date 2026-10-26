"""Reply templates. Deterministic: the same situation always produces the same words."""

from __future__ import annotations

from datetime import date

from .snapshot import DataSnapshot
from .timeutil import fmt_date, fmt_time, night_of, snap_up, to_local
from .types import CheckResult, Draft, Minute


class Voice:
    def __init__(self, snap: DataSnapshot, now: Minute) -> None:
        self.snap = snap
        self.now = now
        self.style = snap.user.time_format

    def t(self, m: Minute) -> str:
        return fmt_time(m, self.snap.offset, self.style)

    def span(self, start: Minute, end: Minute) -> str:
        a, b = self.t(start), self.t(end)
        if self.style == "12h" and a[-2:] == b[-2:] and a[-2:] in {"AM", "PM"}:
            a = a[:-3]
        return f"{a} to {b}"

    def night_word(self, night: date) -> str:
        current = night_of(self.now, self.snap.offset)
        delta = (night - current).days
        if delta == 0:
            return "tonight"
        if delta == 1:
            return "tomorrow night"
        if 1 < delta < 7:
            return f"{fmt_date(night).split()[0]} night"
        return f"the night of {fmt_date(night)}"

    def when(self, d: Draft) -> str:
        assert d.start is not None and d.night is not None
        loc = to_local(d.start, self.snap.offset)
        current = night_of(self.now, self.snap.offset)
        if loc.hour < 12:
            day = f"early {fmt_date(loc.date())}"
        elif d.night == current:
            day = f"tonight, {fmt_date(d.night)}"
        else:
            day = fmt_date(d.night)
        if d.end is None:
            return f"{day}, from {self.t(d.start)}"
        return f"{day}, {self.span(d.start, d.end)}"

    def target(self, slug: str | None) -> str:
        return self.snap.targets[slug].spoken_name if slug else "that object"

    def Target(self, slug: str | None) -> str:
        name = self.target(slug)
        return name[0].upper() + name[1:]

    def tel(self, code: str | None) -> str:
        return f"the {self.snap.telescopes[code].name}" if code else "the telescope"

    def duration(self, minutes: int) -> str:
        h, m = divmod(max(0, minutes), 60)
        parts = []
        if h:
            parts.append(f"{h} hour{'s' if h != 1 else ''}")
        if m:
            parts.append(f"{m} minutes")
        return " and ".join(parts) or "no time"

    def explain(self, r: CheckResult, d: Draft) -> str:
        p = r.params
        tgt, tel = self.Target(d.target), self.tel(d.telescope)
        match r.reason:
            case "past":
                return "That time has already passed."
            case "too_soon":
                return f"That's too soon; the earliest I can start is {self.t(snap_up(int(p['earliest']), 15))}."
            case "too_far":
                return f"I can only book up to {p['horizon_nights']} nights ahead."
            case "too_long":
                return "Bookings can be at most 4 hours."
            case "too_short":
                return "Bookings need to be at least 30 minutes."
            case "end_before_start":
                return "That end time is before the start."
            case "allocation_exhausted":
                return f"You have {self.duration(int(p['left']))} of observing time left this semester."
            case "not_capable":
                return f"{tel[0].upper() + tel[1:]} can't observe {self.target(d.target)}."
            case "too_faint":
                return f"{tgt} is too faint for {tel}."
            case "never_rises":
                return f"{tgt} never rises at our latitude."
            case "never_high":
                return f"{tgt} never gets higher than {round(float(p['peak']))}° here."
            case "too_close_to_sun":
                return (
                    f"{tgt} is only {round(float(p['sep']))}° from the Sun, and {tel} has to stay "
                    f"{round(float(p['limit']))}° away."
                )
            case "is_sun":
                return "I can't point any of our telescopes at the Sun; it's never safe."
            case "below_horizon":
                return f"{tgt} is below the horizon at {self.t(int(p['at']))}."
            case "too_low":
                return f"{tgt} is only {round(float(p['alt']))}° up at {self.t(int(p['at']))}, too low for {tel}."
            case "not_dark":
                return (
                    f"{tel[0].upper() + tel[1:]} needs a dark sky, and it isn't dark enough at {self.t(int(p['at']))}."
                )
            case "moon_glare":
                return (
                    f"The Moon is {p['illum']}% lit and only {p['sep']}° from {self.target(d.target)}, "
                    f"so it would wash it out."
                )
            case "moon_in_beam":
                return f"The Moon is too close to {self.target(d.target)} for the dish."
            case "moon_bright":
                return f"The Moon will be {p['illum']}% lit about {p['sep']}° away."
            case "cloud":
                night = self.night_word(d.night) if d.night else "That night"
                return f"{night[0].upper() + night[1:]} is forecast {p['cloud']}% cloud, too much for {tel}."
            case "pwv":
                return f"There's too much water vapour then ({p['pwv']} mm), and the dish needs under {p['limit']} mm."
            case "rain":
                return f"There's a {p['rain']}% chance of rain then."
            case "wind":
                return f"Wind is forecast at {p['wind']} kilometres an hour, too strong for {tel}."
            case "weather_pending":
                return "The forecast doesn't reach that far yet, so I'll re-check the weather daily."
            case "maintenance":
                return f"{tel[0].upper() + tel[1:]} is down for maintenance then."
            case "taken":
                busy = f"{self.t(int(p['start']))} to {self.t(int(p['end']))}"
                return f"{tel[0].upper() + tel[1:]} is already booked from {busy}."
            case "self_overlap":
                return (
                    f"You already have {self.tel(str(p['telescope']))} booked from {self.t(int(p['start']))} "
                    f"to {self.t(int(p['end']))}, and you can't be at two telescopes at once."
                )
            case _:
                return "That doesn't work."

    def offer_phrase(self, base: Draft, o: Draft, why: str) -> str:
        if why == "other_telescope":
            return f"{self.tel(o.telescope)} can still observe {self.target(o.target)} then"
        assert o.start is not None and o.night is not None
        span = self.span(o.start, o.end) if o.end is not None else f"from {self.t(o.start)}"
        if why == "other_telescope_run":
            return f"{self.tel(o.telescope)} could observe it {self.night_word(o.night)}, {span}"
        if why == "later_night":
            return f"{self.night_word(o.night)} works, {span}"
        return f"I can do {span} {self.night_word(o.night)}"

    def offers(self, base: Draft, offers: list[tuple[Draft, str]]) -> str:
        if not offers:
            return ""
        phrases = [self.offer_phrase(base, d, why) for d, why in offers]
        first = phrases[0][0].upper() + phrases[0][1:]
        if len(phrases) == 1:
            return f"{first}. Would that work?"
        rest = ", or ".join(phrases[1:])
        return f"{first}, or {rest}. Which would you like?"
