"""The call-history stack (LIFO) behind "undo" and "go back".

A frame is the state *before* a slot-changing turn. Popping restores it and reports which side
effects must be reversed (a hold taken since). A commit pushes a barrier frame that undo cannot cross.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .types import CallState, Effect, Frame

MAX_DEPTH = 50


def push(state: CallState, label: str) -> CallState:
    frame = Frame(
        seq=state.seq + 1,
        draft=state.draft,
        state=state.state,
        pending=state.pending,
        hold_id=state.hold_id,
        label=label,
    )
    stack = (*state.stack, frame)[-MAX_DEPTH:]
    return state.with_(stack=stack, seq=frame.seq)


def push_barrier(state: CallState, label: str) -> CallState:
    frame = Frame(
        seq=state.seq + 1, draft=state.draft, state=state.state, pending=None, hold_id=None, label=label, barrier=True
    )
    return state.with_(stack=(*state.stack, frame)[-MAX_DEPTH:], seq=frame.seq)


@dataclass(frozen=True)
class PopResult:
    state: CallState
    frame: Frame | None
    compensations: tuple[Effect, ...]
    blocked_by_barrier: bool = False


def _restore(state: CallState, frame: Frame, remaining: tuple[Frame, ...]) -> PopResult:
    comps: list[Effect] = []
    if state.hold_id and state.hold_id != frame.hold_id:
        comps.append(Effect("release", {"hold_id": state.hold_id}))
    restored = state.with_(
        draft=frame.draft,
        state=frame.state,
        pending=frame.pending,
        stack=remaining,
        hold_id=None,
        hold_expires=None,
        offers=(),
    )
    if frame.hold_id is not None:
        # the restored configuration was held: ask for the hold again rather than assume it survived
        comps.append(Effect("hold", {"draft": frame.draft, "restore": True}))
    return PopResult(restored, frame, tuple(comps))


def pop(state: CallState) -> PopResult:
    if not state.stack:
        return PopResult(state, None, ())
    top = state.stack[-1]
    if top.barrier:
        return PopResult(state, None, (), blocked_by_barrier=True)
    return _restore(state, top, state.stack[:-1])


def pop_to(state: CallState, match: Callable[[Frame], bool]) -> PopResult:
    """Pop back to the most recent frame satisfying `match` (e.g. 'the Andromeda one')."""
    for i in range(len(state.stack) - 1, -1, -1):
        f = state.stack[i]
        if f.barrier:
            return PopResult(state, None, (), blocked_by_barrier=True)
        if match(f):
            return _restore(state, f, state.stack[:i])
    return PopResult(state, None, ())
