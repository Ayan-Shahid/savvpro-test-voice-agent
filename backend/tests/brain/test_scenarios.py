"""Replays every YAML script in scenarios/: one EC case per file (PRD, Edge-case catalogue)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from astro_brain.types import TurnEvent

from .harness import new_call

SCENARIOS = sorted((Path(__file__).parent / "scenarios").glob("*.yaml"))


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return data


@pytest.mark.parametrize("path", SCENARIOS, ids=[p.stem for p in SCENARIOS])
def test_scenario(path: Path) -> None:
    sc = _load(path)
    call = new_call(sc.get("setup"), sc.get("clock", "2026-10-27T20:00"))
    for i, turn in enumerate(sc["turns"], start=1):
        if "advance_minutes" in turn:
            call.advance(turn["advance_minutes"])
        if "event" in turn:
            r = call.send(TurnEvent(turn["event"], now=call.now))
        else:
            r = call.say(turn["say"], confidence=turn.get("confidence", 1.0), heard_full=turn.get("heard_full", True))
        exp = turn.get("expect", {})
        where = f"{path.stem} turn {i}: {turn.get('say', turn.get('event'))!r} -> {r.reply.text!r}"
        if "state" in exp:
            assert call.state.state == exp["state"], where
        if "intent" in exp:
            assert r.trace["intent"] == exp["intent"], where
        for frag in exp.get("reply_contains", []):
            assert frag.lower() in r.reply.text.lower(), f"missing {frag!r}; {where}"
        for frag in exp.get("reply_excludes", []):
            assert frag.lower() not in r.reply.text.lower(), f"unexpected {frag!r}; {where}"
        draft = call.state.draft
        for k, v in exp.get("draft", {}).items():
            got = getattr(draft, k)
            got = got.isoformat() if hasattr(got, "isoformat") else got
            assert got == v, f"draft.{k}={got!r} != {v!r}; {where}"
        if "effects" in exp:
            assert [e.kind for e in r.effects] == exp["effects"], where
        if "stack_depth" in exp:
            assert len(call.state.stack) == exp["stack_depth"], where
        if "pending" in exp:
            assert (call.state.pending or {}).get("type") == exp["pending"], where
        if "check_failed" in exp:
            fails = [c["reason"] for c in r.trace["checks"] if c["status"] == "fail"]
            assert exp["check_failed"] in fails, f"{fails}; {where}"
