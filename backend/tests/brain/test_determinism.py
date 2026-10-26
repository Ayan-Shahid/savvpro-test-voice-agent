"""Same inputs, same clock, same data -> identical outputs, including across processes with random hash seeds."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .harness import new_call

SCRIPT = [
    "I'd like the refractor on Friday for Saturn",
    "Actually, switch the target to the Orion Nebula and make it the Reflector instead",
    "Undo that.",
    "No, Orion was right. Reflector for Orion, 11 to 1.",
    "Yes.",
]


def transcript() -> list[tuple[str, str, list[str]]]:
    call = new_call()
    out = []
    for line in SCRIPT:
        r = call.say(line)
        out.append((r.reply.text, call.state.state, [e.kind for e in r.effects]))
    return out


def test_replay_in_process_is_identical() -> None:
    first = transcript()
    for _ in range(20):
        assert transcript() == first


def test_replay_across_hash_seeds_is_identical() -> None:
    code = (
        "import json, sys; sys.path[:0] = ['src', '.']; "
        "from tests.brain.test_determinism import transcript; print(json.dumps(transcript()))"
    )
    root = Path(__file__).parents[2]
    outputs = set()
    for seed in ("1", "42", "random", "777"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        res = subprocess.run(
            [sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, check=True
        )
        outputs.add(res.stdout.strip())
    assert len(outputs) == 1
    assert json.loads(outputs.pop())[-1][1] == "BOOKED"
