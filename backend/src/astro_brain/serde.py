"""CallState <-> JSON, for keeping a live call in Redis between turns.

Only the brain's own dataclasses can be revived (a fixed allow-list), so a tampered Redis value cannot make
the decoder construct arbitrary objects.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from typing import Any

from .types import CallState, Draft, Frame

VERSION = 1
_TYPES: dict[str, type] = {"CallState": CallState, "Draft": Draft, "Frame": Frame}


def _enc(v: Any) -> Any:
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        name = type(v).__name__
        if name not in _TYPES:
            raise TypeError(f"cannot encode {name}")
        return {"__t": name, **{f.name: _enc(getattr(v, f.name)) for f in dataclasses.fields(v)}}
    if isinstance(v, date):
        return {"__d": v.isoformat()}
    if isinstance(v, tuple):
        return {"__tuple": [_enc(x) for x in v]}
    if isinstance(v, list):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _enc(x) for k, x in v.items()}
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    raise TypeError(f"cannot encode {type(v).__name__}")


def _dec(v: Any) -> Any:
    if isinstance(v, list):
        return [_dec(x) for x in v]
    if isinstance(v, dict):
        if "__t" in v:
            cls = _TYPES[v["__t"]]
            names = {f.name for f in dataclasses.fields(cls)}
            return cls(**{k: _dec(x) for k, x in v.items() if k in names})
        if "__d" in v:
            return date.fromisoformat(v["__d"])
        if "__tuple" in v:
            return tuple(_dec(x) for x in v["__tuple"])
        return {k: _dec(x) for k, x in v.items()}
    return v


def dumps(state: CallState) -> str:
    return json.dumps({"v": VERSION, "state": _enc(state)}, separators=(",", ":"), sort_keys=True)


def loads(raw: str | bytes) -> CallState:
    data = json.loads(raw)
    if data.get("v") != VERSION:
        raise ValueError("unsupported call state version")
    state = _dec(data["state"])
    if not isinstance(state, CallState):
        raise ValueError("not a CallState")
    return state
