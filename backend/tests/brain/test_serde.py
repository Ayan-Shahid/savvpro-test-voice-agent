"""Call state survives a Redis round trip at every turn, and the decoder refuses foreign types."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from astro_brain import serde
from astro_brain.types import TurnEvent

from .harness import new_call

SCENARIOS = sorted((Path(__file__).parent / "scenarios").glob("*.yaml"))


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_round_trip_every_turn(path: Path) -> None:
    spec = yaml.safe_load(path.read_text())
    call = new_call(spec.get("setup"), spec.get("clock", "2026-10-27T20:00"))
    assert serde.loads(serde.dumps(call.state)) == call.state
    for turn in spec["turns"]:
        if "advance_minutes" in turn:
            call.advance(turn["advance_minutes"])
        if "event" in turn:
            call.send(TurnEvent(turn["event"], now=call.now))
        elif "say" in turn:
            call.say(turn["say"], confidence=turn.get("confidence", 1.0), heard_full=turn.get("heard_full", True))
        restored = serde.loads(serde.dumps(call.state))
        assert restored == call.state
        call.state = restored


def test_decoder_rejects_unknown_types_and_versions() -> None:
    with pytest.raises(KeyError):
        serde.loads(json.dumps({"v": 1, "state": {"__t": "Popen", "args": ["sh"]}}))
    with pytest.raises(ValueError, match="version"):
        serde.loads(json.dumps({"v": 99, "state": {}}))
