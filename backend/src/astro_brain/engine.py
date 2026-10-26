"""The brain: `step(state, event, snapshot) -> StepResult`. Pure, deterministic, no I/O.

Each turn: guard -> parse -> decide intent (with state priors) -> apply slot changes (pushing a stack
frame first) -> validate -> plan alternatives -> choose the next state -> template the reply -> return
effects for the call service to execute.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from rapidfuzz import fuzz

from . import stack as stk
from .nlp.parser import ParseResult, parse
from .nlp.timegrammar import TimeRef
from .planner import alternatives, other_capable, valid_runs
from .resolve import infer_meridiems, resolve_night, resolve_time
from .responses import Voice
from .rules.pipeline import RULES, Ctx, capability, first_failure, never_visible, run
from .snapshot import DataSnapshot
from .timeutil import at_night, night_of, night_start, to_local
from .types import CallState, CheckResult, Draft, Effect, Minute, Reply, State, StepResult, TurnEvent

GRID = 15
SUGGESTABLE = ("saturn", "m31", "m45", "jupiter", "m42", "moon", "cyg-a", "m33")


class _Turn:
    """Mutable scratchpad for one step; the inputs and the returned state stay immutable."""

    def __init__(self, state: CallState, event: TurnEvent, snap: DataSnapshot) -> None:
        self.state = state
        self.event = event
        self.snap = snap
        self.now: Minute = event.now
        self.v = Voice(snap, event.now)
        self.effects: list[Effect] = []
        self.notes: list[str] = []
        self.checks: list[CheckResult] = []
        self.offers_trace: list[dict[str, Any]] = []
        self.intent = event.kind.upper()
        self.parse: ParseResult | None = None

    # ---- helpers --------------------------------------------------------------------------------
    @property
    def current_night(self) -> date:
        return night_of(self.now, self.snap.offset)

    def release_hold(self) -> None:
        if self.state.hold_id:
            self.effects.append(Effect("release", {"hold_id": self.state.hold_id}))
            self.state = self.state.with_(hold_id=None, hold_expires=None)

    def label(self, d: Draft) -> str:
        parts = []
        if d.target:
            parts.append(self.v.Target(d.target))
        if d.telescope:
            parts.append(f"on {self.v.tel(d.telescope)}")
        if d.night:
            parts.append(f"for {self.v.night_word(d.night)}")
        return " ".join(parts) or "an empty request"

    def question(self) -> str:
        p = self.state.pending or {}
        match p.get("type"):
            case "slot":
                return {
                    "target": "Which object would you like to observe?",
                    "telescope": "Which telescope would you like?",
                    "night": "Which night would you like?",
                    "start": "What time would you like to start?",
                    "end": "Until what time?",
                }[p["slot"]]
            case "confirm":
                return "Shall I book it?"
            case "proposal" | "which_telescope":
                return "Which would you like?"
            case "did_you_mean_target" | "suggest_telescope" | "did_you_say" | "cancel_confirm" | "waitlist_offer":
                return "Is that right?" if p["type"] != "cancel_confirm" else "Shall I cancel it?"
            case "meridiem":
                return "Is that in the morning or the evening?"
            case "anything_else":
                return "Is there anything else I can help with?"
        return "What would you like to observe?"

    def ask(self, state: State, pending: dict[str, Any] | None, text: str) -> str:
        self.state = self.state.with_(state=state, pending=pending)
        return text

    def ctx(self, d: Draft, start: Minute, end: Minute) -> Ctx:
        assert d.target and d.telescope
        return Ctx(
            d,
            self.snap.targets[d.target],
            self.snap.telescopes[d.telescope],
            start,
            end,
            self.now,
            self.snap,
            self.state.hold_id,
        )

    def suggestions(self) -> list[str]:
        night = self.current_night
        if night not in self.snap.nights:
            return ["saturn", "m31"]
        probe = at_night(night, 22, 0, self.snap.offset)
        scored = []
        for slug in SUGGESTABLE:
            ss = self.snap.samples(slug, night, probe, probe + 5) if (slug, night) in self.snap.sky else []
            if ss and ss[0]["alt"] > 30 and ss[0]["sun_sep"] > 45:
                scored.append((-ss[0]["alt"], slug))
        return [s for _a, s in sorted(scored)[:2]] or ["saturn", "m31"]

    # ---- slot application ----------------------------------------------------------------------------
    def apply(self, p: ParseResult) -> tuple[Draft, str | None]:
        """Merge parsed values into the draft. Returns (draft, clarification needed or None)."""
        d = self.state.draft
        offset = self.snap.offset
        if p.pick is not None and self.state.offers:
            idx = p.pick - 1 if p.pick > 0 else len(self.state.offers) - 1
            if 0 <= idx < len(self.state.offers):
                d = self.state.offers[idx]
        if p.target:
            d = d.with_(target=p.target)
        if p.telescope:
            d = d.with_(telescope=p.telescope)
        elif d.telescope in p.negated_telescopes:
            d = d.with_(telescope=None)
        if p.night is not None:
            n, err = resolve_night(p.night, self.now, offset)
            if err == "past":
                return d, "past_night"
            if n is not None:
                if d.night is not None and d.start is not None:
                    delta = (n - d.night).days * 24 * 60
                    d = d.with_(start=d.start + delta, end=d.end + delta if d.end is not None else None)
                d = d.with_(night=n)
        if p.same_time_next and d.night is not None:
            d = d.with_(
                night=d.night + timedelta(days=1),
                start=d.start + 1440 if d.start is not None else None,
                end=d.end + 1440 if d.end is not None else None,
            )
        start_ref, end_ref = infer_meridiems(p.start, p.end)
        tel = self.snap.telescopes.get(d.telescope or "")
        explicit_night = p.night is not None or d.night is not None
        night = d.night or self.current_night
        if start_ref is not None:
            s = resolve_time(start_ref, night, tel, offset)
            if s is None and d.start is not None:
                # ambiguous am/pm: take the reading closest to the time already on the table
                options = [
                    resolve_time(TimeRef(start_ref.hour, start_ref.minute, m), night, None, offset)
                    for m in ("am", "pm")
                ]
                s = min((o for o in options if o is not None), key=lambda o: abs(o - (d.start or 0)))
                if end_ref is not None and end_ref.meridiem is None:
                    loc_h = to_local(s, offset).hour
                    end_ref = TimeRef(
                        end_ref.hour,
                        end_ref.minute,
                        "pm" if loc_h >= 12 and end_ref.hour % 12 >= start_ref.hour % 12 else "am",
                    )
            if s is None:
                self.state = self.state.with_(
                    pending={"type": "meridiem", "start": _ref(start_ref), "end": _ref(end_ref) if end_ref else None}
                )
                return d.with_(night=night), "meridiem"
            s = self._snap(s, "start")
            if s < self.now and not explicit_night:
                night = night + timedelta(days=1)
                s += 1440
                self.notes.append("I've taken that as this coming evening.")
            old_len = (d.end - d.start) if (d.start is not None and d.end is not None) else None
            d = d.with_(night=night, start=s, end=None, duration=None)
            if end_ref is not None:
                e = resolve_time(end_ref, night, tel, offset)
                if e is None:
                    e = resolve_time(
                        TimeRef(end_ref.hour, end_ref.minute, "pm" if end_ref.hour >= 6 else "am"), night, None, offset
                    )
                assert e is not None
                d = d.with_(end=self._snap(e, "end"))
            elif p.duration:
                d = d.with_(end=s + p.duration)
            elif old_len is not None:
                d = d.with_(end=s + old_len)
        elif end_ref is not None and d.start is not None:
            e = resolve_time(end_ref, d.night or night, tel, offset)
            if e is None:
                e = resolve_time(
                    TimeRef(end_ref.hour, end_ref.minute, "pm" if end_ref.hour >= 6 else "am"),
                    d.night or night,
                    None,
                    offset,
                )
            assert e is not None
            d = d.with_(end=self._snap(e, "end"))
        elif p.duration and d.start is not None:
            d = d.with_(end=d.start + p.duration)
        if p.shift and d.start is not None:
            new_start = d.start + p.shift
            d = d.with_(
                start=new_start, end=d.end + p.shift if d.end is not None else None, night=night_of(new_start, offset)
            )
        if p.extend and d.end is not None:
            d = d.with_(end=d.end + p.extend)
        if d.start is not None and d.end is not None and d.end <= d.start:
            return d, "end_before_start"
        return d, None

    def _snap(self, m: Minute, which: str) -> Minute:
        r = m % GRID
        if r == 0:
            return m
        snapped = m - r if r < GRID / 2 else m + GRID - r
        if which == "start":
            self.notes.append(f"I've rounded that to {self.v.t(snapped)}.")
        return snapped

    # ---- the central decision ------------------------------------------------------------------------
    def advance(self) -> str:
        d = self.state.draft
        held_key = (self.state.pending or {}).get("held")
        reply = self._advance(d)
        # release a hold that no longer matches what is being confirmed
        if (
            self.state.hold_id
            and not (self.state.state == "CONFIRMING" and (self.state.pending or {}).get("held") == held_key == _key(d))
            and not any(e.kind == "hold" for e in self.effects)
        ):
            self.release_hold()
        return reply

    def _advance(self, d: Draft) -> str:
        v = self.v
        if d.target is None:
            sug = self.suggestions()
            hint = f" {v.Target(sug[0])} and {v.target(sug[1])} are well placed tonight." if len(sug) > 1 else ""
            return self.ask(
                "COLLECTING", {"type": "slot", "slot": "target"}, f"Which object would you like to observe?{hint}"
            )
        target = self.snap.targets[d.target]
        if d.telescope is None:
            capable = other_capable(self.snap, d.target, exclude="")
            if not capable:
                return self.ask(
                    "COLLECTING",
                    {"type": "slot", "slot": "target"},
                    f"None of our telescopes can observe {v.target(d.target)}. What else would you like?",
                )
            if len(capable) == 1:
                return self.ask(
                    "CLARIFYING",
                    {"type": "suggest_telescope", "options": capable},
                    f"{v.Target(d.target)} needs {v.tel(capable[0])}. Shall I use it?",
                )
            names = [self.snap.telescopes[c].name for c in capable]
            listing = " or the ".join([", the ".join(names[:-1]), names[-1]]) if len(names) > 1 else names[0]
            return self.ask(
                "COLLECTING",
                {"type": "which_telescope", "options": capable},
                f"Which telescope would you like: the {listing}?",
            )
        cap = capability(self.ctx(d, 0, 1))
        if cap.status == "fail":
            self.checks.append(cap)
            capable = other_capable(self.snap, d.target, exclude=d.telescope)
            if capable:
                tel_names = " or ".join(v.tel(c) for c in capable)
                return self.ask(
                    "CLARIFYING",
                    {"type": "which_telescope", "options": capable},
                    f"{v.explain(cap, d)} {v.Target(d.target)} works with {tel_names}. Which would you like?",
                )
            return self.ask(
                "COLLECTING",
                {"type": "slot", "slot": "target"},
                f"{v.explain(cap, d)} What else would you like to observe?",
            )
        nv = never_visible(self.ctx(d, 0, 1))
        if nv.status == "fail":
            self.checks.append(nv)
            sug = [s for s in self.suggestions() if self.snap.targets[s].kind == target.kind] or self.suggestions()
            self.state = self.state.with_(draft=d.with_(target=None))
            return self.ask(
                "COLLECTING",
                {"type": "slot", "slot": "target"},
                f"{v.explain(nv, d)} You could try {v.target(sug[0])} instead. What would you like?",
            )
        if d.night is None:
            return self.ask("COLLECTING", {"type": "slot", "slot": "night"}, "Which night would you like?")
        if d.night < self.current_night:
            self.state = self.state.with_(draft=d.with_(night=None, start=None, end=None))
            return self.ask(
                "COLLECTING",
                {"type": "slot", "slot": "night"},
                "That night has already passed. Which night would you like?",
            )
        if (d.night - self.current_night).days > self.snap.policy.horizon_nights:
            self.state = self.state.with_(draft=d.with_(night=None, start=None, end=None))
            last = self.current_night + timedelta(days=self.snap.policy.horizon_nights)
            return self.ask(
                "COLLECTING",
                {"type": "slot", "slot": "night"},
                f"I can only book up to {self.snap.policy.horizon_nights} nights ahead, until "
                f"{v.night_word(last)}. Which night would you like?",
            )
        if d.start is None:
            return self._no_start(d)
        if d.end is None:
            probe = run(self.ctx(d, d.start, d.start + 30), RULES)
            self.checks.extend(probe)
            fail = _first_non_length(probe)
            if fail is not None:
                return self._propose(d, fail)
            runs = valid_runs(self.snap, d.target, d.telescope, d.night, self.now, self.state.hold_id).runs
            until = next((e for s, e in runs if s <= d.start < e), None)
            hint = f" {v.Target(d.target)} stays observable until {v.t(until)}." if until else ""
            return self.ask("COLLECTING", {"type": "slot", "slot": "end"}, f"Until what time?{hint}")
        results = run(self.ctx(d, d.start, d.end), RULES)
        self.checks.extend(results)
        fail = first_failure(results)
        if fail is not None:
            return self._propose(d, fail)
        warns = " ".join(v.explain(r, d) for r in results if r.status == "warn")
        key = _key(d)
        held = (self.state.pending or {}).get("held") == key and self.state.hold_id is not None
        if not held:
            if self.state.hold_id:
                self.release_hold()
            self.effects.append(Effect("hold", {"draft": _draft_dict(d)}))
        readback = f"That's {v.tel(d.telescope)} for {v.target(d.target)}, {v.when(d)}."
        if warns:
            readback += f" {warns}"
        return self.ask("CONFIRMING", {"type": "confirm", "held": key}, f"{readback} Shall I book it?")

    def _no_start(self, d: Draft) -> str:
        assert d.target and d.telescope and d.night
        v = self.v
        info = valid_runs(self.snap, d.target, d.telescope, d.night, self.now, self.state.hold_id)
        if info.runs:
            s, e = max(info.runs, key=lambda r: (r[1] - r[0], -r[0]))
            return self.ask(
                "COLLECTING",
                {"type": "slot", "slot": "start"},
                f"{v.Target(d.target)} is well placed {v.night_word(d.night)} from "
                f"{v.span(s, e)}. What time would you like to start?",
            )
        if info.blocking:
            self.checks.append(info.blocking)
        reason = (
            v.explain(info.blocking, d)
            if info.blocking
            else f"{v.Target(d.target)} can't be observed with {v.tel(d.telescope)} {v.night_word(d.night)}."
        )
        offers: list[tuple[Draft, str]] = []
        for code in other_capable(self.snap, d.target, exclude=d.telescope):
            r = valid_runs(self.snap, d.target, code, d.night, self.now).runs
            if r:
                s, e = max(r, key=lambda x: (x[1] - x[0], -x[0]))
                offers.append((d.with_(telescope=code, start=s, end=min(e, s + 240)), "other_telescope_run"))
                break
        for ahead in range(1, self.snap.policy.forecast_nights + 1):
            nd = d.night + timedelta(days=ahead)
            if not self.snap.has_sky(nd):
                break
            r = valid_runs(self.snap, d.target, d.telescope, nd, self.now).runs
            if r:
                s, e = max(r, key=lambda x: (x[1] - x[0], -x[0]))
                offers.append((d.with_(night=nd, start=s, end=min(e, s + 240)), "later_night"))
                break
        return self._offer(d, reason, offers)

    def _propose(self, d: Draft, fail: CheckResult) -> str:
        v = self.v
        reason = v.explain(fail, d)
        if fail.reason == "allocation_exhausted":
            left = int(fail.params["left"])
            assert d.start is not None
            offers = [(d.with_(end=d.start + (left - left % GRID)), "same_night")] if left >= 30 else []
            return self._offer(d, reason, offers)
        if fail.rule == "policy" and fail.reason in {"too_long", "too_short"}:
            assert d.start is not None
            length = 240 if fail.reason == "too_long" else 30
            return self._offer(d, reason, [(d.with_(end=d.start + length), "same_night")])
        if fail.rule == "policy" and fail.reason in {"past", "too_soon"}:
            self.state = self.state.with_(draft=d.with_(start=None, end=None))
            return self.ask(
                "COLLECTING", {"type": "slot", "slot": "start"}, f"{reason} What time would you like to start?"
            )
        found = alternatives(self.snap, d, self.now, self.state.hold_id)
        pairs = [(o.draft, o.why) for o in found]
        text = self._offer(d, reason, pairs)
        if fail.reason == "taken":
            self.state = self.state.with_(pending={**(self.state.pending or {}), "waitlist": True})
            text = text.replace("Would that work?", "Would that work, or shall I add you to the waitlist?")
            text = text.replace("Which would you like?", "Which would you like, or shall I add you to the waitlist?")
            if not pairs:
                text = f"{reason} Shall I add you to the waitlist for it?"
        return text

    def _offer(self, d: Draft, reason: str, offers: list[tuple[Draft, str]]) -> str:
        self.offers_trace = [{"draft": _draft_dict(o), "why": why} for o, why in offers]
        self.state = self.state.with_(offers=tuple(o for o, _ in offers))
        if not offers:
            return self.ask(
                "COLLECTING",
                {"type": "slot", "slot": "night"},
                f"{reason} I couldn't find another time in the next week. Which other night or target would you like?",
            )
        text = self.v.offers(d, offers)
        pending: dict[str, Any] = {"type": "proposal"}
        held = (self.state.pending or {}).get("waitlist")
        if held:
            pending["waitlist"] = True
        return self.ask("PROPOSING", pending, f"{reason} {text}")


def _first_non_length(results: list[CheckResult]) -> CheckResult | None:
    return next(
        (r for r in results if r.status == "fail" and r.reason not in {"too_short", "allocation_exhausted"}), None
    )


def _ref(r: TimeRef | None) -> dict[str, Any] | None:
    return None if r is None else {"hour": r.hour, "minute": r.minute, "meridiem": r.meridiem, "special": r.special}


def _key(d: Draft) -> str:
    return f"{d.target}|{d.telescope}|{d.night}|{d.start}|{d.end}"


def _draft_dict(d: Draft) -> dict[str, Any]:
    return {
        "target": d.target,
        "telescope": d.telescope,
        "night": d.night.isoformat() if d.night else None,
        "start": d.start,
        "end": d.end,
    }


def draft_from_dict(x: dict[str, Any]) -> Draft:
    return Draft(
        x.get("target"),
        x.get("telescope"),
        date.fromisoformat(x["night"]) if x.get("night") else None,
        x.get("start"),
        x.get("end"),
    )


# ---- intent decision ------------------------------------------------------------------------------------
def decide_intent(p: ParseResult, state: CallState) -> str:
    f = p.flags
    pend = (state.pending or {}).get("type")
    yes, no = "yes" in f, "no" in f
    slots = p.has_slots or bool(p.target_candidates or p.telescope_candidates)
    if "undo" in f:
        return "UNDO"
    if "start_over" in f:
        return "START_OVER"
    if "goodbye" in f and not slots:
        return "GOODBYE"
    if "repeat" in f and not slots:
        return "REPEAT"
    if "help" in f and not slots:
        return "HELP"
    if "pause" in f and not slots:
        return "PAUSE"
    if p.forbidden:
        return "FORBIDDEN"
    if "cancel" in f and ("booking_ref" in f or (p.night and state.state in {"GREETING", "BOOKED"})):
        return "CANCEL_BOOKING"
    if "cancel" in f and not slots:
        return "CANCEL"
    if "list" in f:
        return "LIST_BOOKINGS"
    if "waitlist" in f and pend in {"proposal", "waitlist_offer"}:
        return "JOIN_WAITLIST"
    if pend == "did_you_say":
        return "ACCEPT_HEARD" if yes and not slots else ("REJECT_HEARD" if no and not slots else "CHANGE_SLOT")
    if pend == "meridiem":
        words = set(p.tokens)
        if words & {"morning", "am"} or words & {"evening", "night", "pm", "afternoon", "tonight"}:
            return "MERIDIEM"
    if pend == "confirm":
        if yes and not slots:
            return "CONFIRM"
        if slots:
            return "CHANGE_SLOT"
        if no:
            return "DENY"
    if pend == "proposal":
        if p.pick is not None or slots:
            return "CHANGE_SLOT"
        if yes:
            return "PICK_FIRST"
        if no:
            return "DENY"
    if pend in {"did_you_mean_target", "suggest_telescope", "waitlist_offer"}:
        if yes and not slots:
            return "ACCEPT_SUGGESTION"
        if no and not slots:
            return "REJECT_SUGGESTION"
    if pend == "cancel_confirm":
        if yes:
            return "CONFIRM_CANCEL"
        if no:
            return "DENY"
    if pend == "anything_else" and not slots:
        if no:
            return "GOODBYE"
        if yes:
            return "BOOK"
    if p.forbidden:
        return "FORBIDDEN"
    if "weather" in f and not (p.target or p.telescope or p.start):
        return "CHECK_WEATHER"
    if "visibility" in f and p.target and not p.telescope and not p.start:
        return "CHECK_VISIBILITY"
    if "availability" in f and not p.target and not p.start:
        return "CHECK_AVAILABILITY"
    if "oos" in f and not slots:
        return "OUT_OF_SCOPE"
    if slots or p.unknown_target:
        empty = state.draft == Draft() or state.state in {"BOOKED", "GREETING"}
        return "BOOK" if empty else "CHANGE_SLOT"
    if "book" in f:
        return "BOOK"
    return "NO_MATCH"


# ---- public entry point -----------------------------------------------------------------------------------
def step(state: CallState, event: TurnEvent, snap: DataSnapshot) -> StepResult:
    t = _Turn(state.with_(turn=state.turn + 1), event, snap)
    before = state.state
    reply = _dispatch(t)
    if reply:
        t.state = t.state.with_(last_reply=reply)
    trace = {
        "turn": t.state.turn,
        "event": event.kind,
        "state": [before, t.state.state],
        "intent": t.intent,
        "parse": _parse_trace(t.parse),
        "draft": _draft_dict(t.state.draft),
        "checks": [{"rule": c.rule, "status": c.status, "reason": c.reason, "params": c.params} for c in t.checks],
        "offers": t.offers_trace,
        "effects": [{"kind": e.kind, **{k: v for k, v in e.data.items() if k != "draft"}} for e in t.effects],
        "stack": [f.label for f in t.state.stack],
        "notes": t.notes,
        "snapshot": snap.version,
    }
    return StepResult(t.state, Reply(reply, question=reply.endswith("?")), tuple(t.effects), trace)


def _parse_trace(p: ParseResult | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "target": p.target,
        "telescope": p.telescope,
        "night": p.night.text if p.night else None,
        "start": _ref(p.start),
        "end": _ref(p.end),
        "duration": p.duration,
        "shift": p.shift,
        "pick": p.pick,
        "flags": sorted(p.flags),
        "candidates": p.target_candidates,
        "telescope_candidates": p.telescope_candidates,
        "unknown": p.unknown_target,
        "self_repair": p.self_repair,
    }


def _prefix(t: _Turn, text: str) -> str:
    return " ".join([*t.notes, text]) if t.notes else text


def _dispatch(t: _Turn) -> str:
    ev = t.event
    match ev.kind:
        case "start":
            t.intent = "GREETING"
            name = t.snap.user.display_name
            return t.ask(
                "GREETING",
                None,
                f"Hello {name}, this is the Meridian Ridge Observatory booking line. What would you like to observe?",
            )
        case "hangup":
            t.intent = "HANGUP"
            t.release_hold()
            t.effects.append(Effect("end_call", {"outcome": "abandoned" if not t.state.booked else "booked"}))
            t.state = t.state.with_(state="ENDED", pending=None)
            return ""
        case "silence":
            return _silence(t)
        case "undo":
            return _undo(t, None)
        case "hold_taken":
            t.intent = "HOLD_TAKEN"
            t.state = t.state.with_(hold_id=ev.data["hold_id"], hold_expires=ev.data.get("expires"))
            return ""
        case "hold_failed":
            t.intent = "HOLD_FAILED"
            t.state = t.state.with_(hold_id=None, hold_expires=None, pending=None)
            return "Sorry, someone has just taken that slot. " + t.advance()
    return _utterance(t)


def _silence(t: _Turn) -> str:
    t.intent = "SILENCE"
    n = t.state.no_input + 1
    t.state = t.state.with_(no_input=n)
    if n == 1:
        return f"Are you still there? {t.question()}"
    if n == 2:
        return f"You can say something like: book the reflector for the Orion Nebula on Friday at 11 PM. {t.question()}"
    t.release_hold()
    t.effects.append(Effect("end_call", {"outcome": "abandoned" if not t.state.booked else "booked"}))
    t.state = t.state.with_(state="ENDED", pending=None)
    return "I'll end the call now. Clear skies!"


def _utterance(t: _Turn) -> str:
    ev = t.event
    text = ev.text.strip()
    st = t.state
    if st.last_reply and len(text) >= 25 and fuzz.partial_ratio(text.lower(), st.last_reply.lower()) >= 85:
        t.intent = "ECHO_IGNORED"
        return ""
    t.state = t.state.with_(no_input=0)
    expect_target = (st.pending or {}).get("slot") == "target"
    p = parse(text, expect_target=expect_target)
    t.parse = p
    if ev.confidence < 0.55 and p.has_slots and (st.pending or {}).get("type") != "did_you_say":
        t.intent = "LOW_CONFIDENCE"
        heard = t.v.target(p.target) if p.target else f"“{text}”"
        return t.ask(st.state, {"type": "did_you_say", "text": text, "prev": st.pending}, f"Did you say {heard}?")
    intent = decide_intent(p, st)
    t.intent = intent
    if intent not in {"NO_MATCH", "OUT_OF_SCOPE"}:
        t.state = t.state.with_(no_match=0)
    return _handle(t, intent, p)


def _handle(t: _Turn, intent: str, p: ParseResult) -> str:
    v = t.v
    st = t.state
    match intent:
        case "UNDO":
            return _undo(t, p)
        case "GOODBYE":
            t.release_hold()
            t.effects.append(Effect("end_call", {"outcome": "booked" if st.booked else "info_only"}))
            t.state = t.state.with_(state="ENDED", pending=None)
            return "Thanks for calling the observatory. Clear skies!"
        case "START_OVER":
            t.state = stk.push(t.state, t.label(st.draft))
            t.release_hold()
            t.state = t.state.with_(draft=Draft(), offers=())
            return "Okay, let's start again. " + t.advance()
        case "REPEAT":
            return st.last_reply or t.question()
        case "HELP":
            return (
                "I can book the Optical Refractor, the Deep-Space Reflector or the Sub-Millimeter Radio Dish. "
                f"Tell me the object, the night and the time. {t.question()}"
            )
        case "PAUSE":
            return "Sure, take your time."
        case "CANCEL":
            if st.draft != Draft():
                t.state = stk.push(t.state, t.label(st.draft))
            t.release_hold()
            t.state = t.state.with_(draft=Draft(), offers=())
            return t.ask(
                "GREETING",
                {"type": "anything_else"},
                "Okay, I've dropped that request. Is there anything else I can help with?",
            )
        case "CANCEL_BOOKING":
            return _cancel_booking(t, p)
        case "CONFIRM_CANCEL":
            bid = (st.pending or {}).get("booking_id")
            t.effects.append(Effect("cancel_booking", {"booking_id": bid}))
            return t.ask("GREETING", {"type": "anything_else"}, "Done, it's cancelled. Is there anything else?")
        case "LIST_BOOKINGS":
            own = sorted(t.snap.user.own, key=lambda b: b.start)[:3]
            if not own:
                return f"You don't have any upcoming bookings. {t.question()}"
            items = "; ".join(
                f"{v.tel(b.telescope)} for {v.target(b.target)}, "
                f"{v.when(Draft(b.target, b.telescope, b.night, b.start, b.end))}"
                for b in own
            )
            return f"You have {items}. {t.question()}"
        case "CHECK_WEATHER":
            return _weather(t, p) + " " + t.question()
        case "CHECK_VISIBILITY":
            return _visibility(t, p) + " " + t.question()
        case "CHECK_AVAILABILITY":
            return _availability(t, p) + " " + t.question()
        case "FORBIDDEN":
            return t.ask(
                "COLLECTING",
                {"type": "slot", "slot": "target"},
                "I can't point any of our telescopes at the Sun; it's never safe. What else would you like to observe?",
            )
        case "OUT_OF_SCOPE" | "NO_MATCH":
            return _no_match(t, intent)
        case "JOIN_WAITLIST":
            d = st.draft
            t.effects.append(Effect("join_waitlist", {"draft": _draft_dict(d)}))
            t.release_hold()
            t.state = t.state.with_(offers=())
            return t.ask(
                "GREETING",
                {"type": "anything_else"},
                f"You're on the waitlist for {v.when(d)}. If it frees up, I'll email you an offer to "
                "accept within 30 minutes. Is there anything else?",
            )
        case "CONFIRM":
            return _confirm(t)
        case "DENY":
            t.release_hold()
            return t.ask("COLLECTING", None, "No problem. What would you like to change?")
        case "PICK_FIRST":
            p.pick = 1
            return _change(t, p)
        case "ACCEPT_SUGGESTION":
            pend = st.pending or {}
            if pend.get("type") == "waitlist_offer":
                return _handle(t, "JOIN_WAITLIST", p)
            opts = pend.get("options") or []
            if pend.get("type") == "did_you_mean_target" and opts:
                p.targets = []
                from .nlp.parser import Mention

                p.targets.append(Mention(opts[0], -1, -1, "confirmed"))
            elif pend.get("type") == "suggest_telescope" and opts:
                from .nlp.parser import Mention

                p.telescopes.append(Mention(opts[0], -1, -1, "confirmed"))
            return _change(t, p)
        case "REJECT_SUGGESTION":
            pend = st.pending or {}
            if pend.get("type") == "suggest_telescope":
                return t.ask(
                    "COLLECTING",
                    {"type": "slot", "slot": "target"},
                    "Okay. Which other object would you like to observe?",
                )
            return t.ask("COLLECTING", {"type": "slot", "slot": "target"}, "Sorry about that. Which object, then?")
        case "ACCEPT_HEARD":
            prev = (st.pending or {}).get("prev")
            t.state = t.state.with_(pending=prev)
            again = parse((st.pending or {}).get("text", ""))
            t.parse = again
            return _handle(t, decide_intent(again, t.state), again)
        case "REJECT_HEARD":
            t.state = t.state.with_(pending=(st.pending or {}).get("prev"))
            return f"Sorry, could you say that again? {t.question()}"
        case "MERIDIEM":
            pend = st.pending or {}
            words = set(p.tokens)
            mer = "am" if words & {"morning", "am"} else "pm"

            def fix(x: dict[str, Any] | None) -> TimeRef | None:
                return None if x is None else TimeRef(x["hour"], x["minute"], mer, x["special"])

            p2 = parse("")
            p2.start, p2.end = fix(pend.get("start")), fix(pend.get("end"))
            t.state = t.state.with_(pending=None)
            return _change(t, p2)
        case "BOOK" | "CHANGE_SLOT":
            if intent == "BOOK" and st.state in {"BOOKED", "GREETING"} and st.draft != Draft():
                t.state = t.state.with_(draft=Draft(), offers=())
            return _change(t, p)
    return _no_match(t, "NO_MATCH")


def _change(t: _Turn, p: ParseResult) -> str:
    v = t.v
    st = t.state
    if (st.pending or {}).get("type") in {"which_telescope", "suggest_telescope"} and p.pick and not p.telescope:
        opts = (st.pending or {}).get("options") or []
        if 0 < p.pick <= len(opts):
            from .nlp.parser import Mention

            p.telescopes.append(Mention(opts[p.pick - 1], -1, -1, "pick"))
            p.pick = None
    draft, problem = t.apply(p)
    if draft != st.draft:
        t.state = stk.push(t.state, t.label(st.draft))
        t.state = t.state.with_(draft=draft, offers=() if p.pick is None else t.state.offers)
    if problem == "past_night":
        return t.ask(
            "COLLECTING",
            {"type": "slot", "slot": "night"},
            "That night has already passed. Which night would you like?",
        )
    if problem == "meridiem":
        return _prefix(t, t.ask("CLARIFYING", t.state.pending, "Is that in the morning or the evening?"))
    if problem == "end_before_start":
        return t.ask("COLLECTING", {"type": "slot", "slot": "end"}, "That end time is before the start. Until when?")
    if p.telescope_candidates and not p.telescope:
        opts = p.telescope_candidates
        return t.ask(
            "CLARIFYING",
            {"type": "which_telescope", "options": opts},
            f"Did you mean {v.tel(opts[0])} or {v.tel(opts[1])}?",
        )
    if p.target_candidates and not p.target:
        opts = [s for s, _ in p.target_candidates]
        if len(opts) == 1:
            return t.ask(
                "CLARIFYING", {"type": "did_you_mean_target", "options": opts}, f"Did you mean {v.target(opts[0])}?"
            )
        return t.ask(
            "CLARIFYING",
            {"type": "did_you_mean_target", "options": opts},
            f"Did you mean {v.target(opts[0])} or {v.target(opts[1])}?",
        )
    if p.unknown_target and not p.target and t.state.draft.target is None:
        sug = t.suggestions()
        return t.ask(
            "COLLECTING",
            {"type": "slot", "slot": "target"},
            f"I couldn't find that in our catalogue. Tonight you could try {v.target(sug[0])}"
            f"{' or ' + v.target(sug[1]) if len(sug) > 1 else ''}. What would you like to observe?",
        )
    prefix = ""
    if (
        st.draft.target
        and st.draft != t.state.draft
        and st.state not in {"GREETING", "BOOKED"}
        and p.target
        and p.telescope
        and st.draft.target != p.target
    ):
        prefix = f"Done: {v.target(p.target)} on {v.tel(p.telescope)}" + (
            f", still {v.night_word(t.state.draft.night)}."
            if t.state.draft.night == st.draft.night and t.state.draft.night
            else "."
        )
    return _prefix(t, (prefix + " " if prefix else "") + t.advance())


def _confirm(t: _Turn) -> str:
    st = t.state
    d = st.draft
    if not t.event.heard_full:
        return t.ask("CONFIRMING", st.pending, f"Just to be sure, that's {t.v.when(d)}. Shall I book it?")
    effects: list[Effect] = []
    if st.hold_expires is not None and t.now > st.hold_expires:
        assert d.start is not None and d.end is not None
        results = run(t.ctx(d, d.start, d.end), RULES)
        t.checks.extend(results)
        fail = first_failure(results)
        if fail is not None:
            t.state = t.state.with_(hold_id=None, hold_expires=None)
            return "The hold on that slot lapsed while we talked. " + t._propose(d, fail)
        effects.append(Effect("hold", {"draft": _draft_dict(d)}))
    effects.append(Effect("commit", {"hold_id": st.hold_id, "draft": _draft_dict(d)}))
    t.effects.extend(effects)
    t.state = stk.push_barrier(t.state, f"booked: {t.label(d)}")
    t.state = t.state.with_(booked=(*st.booked, st.hold_id or "pending"), hold_id=None, hold_expires=None, offers=())
    return t.ask(
        "BOOKED",
        {"type": "anything_else"},
        "Booked. Your confirmation, calendar invite and observing pass are on their way. Is there anything else?",
    )


def _undo(t: _Turn, p: ParseResult | None) -> str:
    t.intent = "UNDO"
    st = t.state
    if p is not None and p.target:
        res = stk.pop_to(st, lambda f: f.draft.target == p.target)
        if res.frame is None and not res.blocked_by_barrier:
            return f"I don't have an earlier request for {t.v.target(p.target)}. {t.question()}"
    else:
        steps = p.go_back_steps if p is not None else 1
        res = stk.pop(st)
        for _ in range(steps - 1):
            if res.frame is None:
                break
            nxt = stk.pop(res.state)
            if nxt.frame is None:
                break
            res = stk.PopResult(nxt.state, nxt.frame, res.compensations + nxt.compensations)
    if res.blocked_by_barrier:
        last = st.booked[-1] if st.booked else None
        return t.ask(
            "CANCELLING",
            {"type": "cancel_confirm", "booking_id": last},
            "Your booking is confirmed. Do you want to cancel it?",
        )
    if res.frame is None:
        return f"There's nothing to undo yet. {t.question()}"
    t.effects.extend(e for e in res.compensations if e.kind == "release")
    t.state = res.state
    label = t.label(res.state.draft)
    return f"Back to {label}. " + t.advance()


def _cancel_booking(t: _Turn, p: ParseResult) -> str:
    v = t.v
    own = list(t.snap.user.own)
    if p.night is not None:
        n, _ = resolve_night(p.night, t.now, t.snap.offset)
        own = [b for b in own if b.night == n]
    if p.target:
        own = [b for b in own if b.target == p.target]
    if not own:
        return f"I can't find a booking that matches. {t.question()}"
    if len(own) > 1:
        return (
            "You have more than one booking that matches: "
            + "; ".join(f"{v.target(b.target)} on {v.tel(b.telescope)} at {v.t(b.start)}" for b in own[:3])
            + ". Which one?"
        )
    b = own[0]
    return t.ask(
        "CANCELLING",
        {"type": "cancel_confirm", "booking_id": b.booking_id},
        f"That's {v.tel(b.telescope)} for {v.target(b.target)}, "
        f"{v.when(Draft(b.target, b.telescope, b.night, b.start, b.end))}. Shall I cancel it?",
    )


def _no_match(t: _Turn, intent: str) -> str:
    n = t.state.no_match + 1
    t.state = t.state.with_(no_match=n)
    if intent == "OUT_OF_SCOPE":
        return f"I can only book telescope time at the observatory. {t.question()}"
    if n == 1:
        return f"Sorry, I didn't catch that. {t.question()}"
    if n == 2:
        return f"Let's take it one step at a time. {t.question()}"
    if n == 3:
        return f"If speaking isn't working, you can type in the box below instead. {t.question()}"
    t.release_hold()
    t.effects.append(Effect("end_call", {"outcome": "error"}))
    t.state = t.state.with_(state="ENDED", pending=None)
    return "I'm sorry I couldn't help this time. Goodbye."


def _night_for(t: _Turn, p: ParseResult) -> date:
    if p.night is not None:
        n, _ = resolve_night(p.night, t.now, t.snap.offset)
        if n is not None:
            return n
    return t.state.draft.night or t.current_night


def _weather(t: _Turn, p: ParseResult) -> str:
    n = _night_for(t, p)
    s = at_night(n, 20, 0, t.snap.offset)
    hours = t.snap.weather_for(s, s + 8 * 60)
    word = t.v.night_word(n)
    if hours is None:
        return f"I don't have a forecast for {word} yet."
    cloud = max(h.cloud_pct for h in hours)
    pwv = max(h.pwv_mm for h in hours)
    rain = max(h.rain_pct for h in hours)
    sky = "clear" if cloud < 25 else ("partly cloudy" if cloud < 60 else "cloudy")
    return (
        f"{word[0].upper() + word[1:]} looks {sky}: up to {cloud}% cloud, water vapour {pwv} mm, "
        f"{rain}% chance of rain."
    )


def _crossing(ts: list[dict[str, float]], level: float) -> Minute | None:
    for a, b in itertools.pairwise(ts):
        if a["alt"] < level <= b["alt"]:
            frac = (level - a["alt"]) / (b["alt"] - a["alt"])
            return int(a["t"] + frac * (b["t"] - a["t"]))
    return None


def _visibility(t: _Turn, p: ParseResult) -> str:
    assert p.target
    n = _night_for(t, p)
    v = t.v
    if (p.target, n) not in t.snap.sky:
        return f"I don't have sky data for {v.night_word(n)}."
    start = night_start(n, t.snap.offset)
    ts = t.snap.samples(p.target, n, start, start + 24 * 60)
    dark = [x for x in ts if x["sun_alt"] < -12]
    if not dark or max(x["alt"] for x in dark) < 0:
        return f"{v.Target(p.target)} isn't above the horizon during darkness {v.night_word(n)}."
    rise = _crossing(ts, 0.0)
    high = _crossing(ts, 20.0)
    parts = [f"{v.Target(p.target)}"]
    if rise and to_local(rise, t.snap.offset).hour not in range(7, 17):
        parts.append(f"rises at {v.t(rise)}")
    if high:
        parts.append(f"is above 20° from {v.t(high)}")
    if len(parts) == 1:
        parts.append(f"is up all evening {v.night_word(n)}")
        return " ".join(parts) + "."
    return parts[0] + " " + " and ".join(parts[1:]) + f" {v.night_word(n)}."


def _availability(t: _Turn, p: ParseResult) -> str:
    code = p.telescope or t.state.draft.telescope or "DSR-100"
    n = _night_for(t, p)
    v = t.v
    lo, hi = at_night(n, 18, 0, t.snap.offset), at_night(n, 6, 0, t.snap.offset)
    gaps = t.snap.tree(code).free_gaps(lo, hi, 30)
    if not gaps:
        return f"{v.tel(code)[0].upper() + v.tel(code)[1:]} is fully booked {v.night_word(n)}."
    spans = ", and ".join(f"{v.t(a)} to {v.t(b)}" for a, b in gaps[:3])
    return f"{v.tel(code)[0].upper() + v.tel(code)[1:]} is free {v.night_word(n)} from {spans}."


def apply_effect_result(state: CallState, kind: str, **data: Any) -> CallState:
    """Fold a call-service outcome back into the state without a reply (e.g. the hold id)."""
    if kind == "hold_taken":
        return state.with_(hold_id=data["hold_id"], hold_expires=data.get("expires"))
    if kind == "committed":
        booked = tuple(data["booking_id"] if b == "pending" else b for b in state.booked)
        return state.with_(booked=booked)
    return replace(state)
