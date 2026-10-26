"""Redis keyspace (Backend Schema, Redis keyspace). Every key has a TTL; nothing here is a source of truth."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import orjson
from redis.asyncio import Redis

from astro_brain import serde
from astro_brain.types import CallState

from .errors import ApiError

STATE_TTL = 30 * 60
DISCONNECT_TTL = 60
TICKET_TTL = 30
LOCK_MS = 5000

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
"""


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, redis: Redis) -> None:
        self.r = redis

    # ---- call state ------------------------------------------------------------------------------
    async def load_state(self, call_id: str) -> CallState | None:
        raw = await self.r.get(f"av:call:{call_id}:state")
        return serde.loads(raw) if raw else None

    async def save_state(self, call_id: str, state: CallState, ttl: int = STATE_TTL) -> None:
        await self.r.set(f"av:call:{call_id}:state", serde.dumps(state), ex=ttl)

    async def expire_soon(self, call_id: str) -> None:
        await self.r.expire(f"av:call:{call_id}:state", DISCONNECT_TTL)

    async def drop_state(self, call_id: str) -> None:
        await self.r.delete(f"av:call:{call_id}:state", f"av:call:{call_id}:meta")

    async def save_meta(self, call_id: str, meta: dict[str, Any]) -> None:
        await self.r.set(f"av:call:{call_id}:meta", orjson.dumps(meta), ex=STATE_TTL)

    async def load_meta(self, call_id: str) -> dict[str, Any] | None:
        raw = await self.r.get(f"av:call:{call_id}:meta")
        return orjson.loads(raw) if raw else None

    @asynccontextmanager
    async def turn_lock(self, call_id: str) -> AsyncIterator[None]:
        """Turn mutex: two events never run step() for one call at the same time."""
        key, token = f"av:call:{call_id}:lock", secrets.token_hex(8)
        deadline = time.monotonic() + 3
        while not await self.r.set(key, token, nx=True, px=LOCK_MS):
            if time.monotonic() > deadline:
                raise ApiError(409, "call_busy", "Still working on your last request.")
            await _sleep(0.02)
        try:
            yield
        finally:
            await self.r.eval(_RELEASE, 1, key, token)  # type: ignore[misc]

    # ---- live call per user -------------------------------------------------------------------------
    async def set_live_call(self, user_id: str, call_id: str) -> None:
        await self.r.set(f"av:user:{user_id}:live_call", call_id, ex=STATE_TTL)

    async def clear_live_call(self, user_id: str, call_id: str) -> None:
        key = f"av:user:{user_id}:live_call"
        if (await self.r.get(key)) in (call_id, call_id.encode()):
            await self.r.delete(key)

    # ---- WebSocket tickets --------------------------------------------------------------------------
    async def mint_ticket(self, *, user_id: str, call_id: str, origin: str, claims: dict[str, Any]) -> str:
        ticket = secrets.token_urlsafe(32)
        key = f"av:wsticket:{_h(ticket)}"
        await self.r.hset(
            key,
            mapping={"user": user_id, "call": call_id, "origin": origin, "claims": orjson.dumps(claims).decode()},
        )  # type: ignore[misc]
        await self.r.expire(key, TICKET_TTL)
        return ticket

    async def redeem_ticket(self, ticket: str) -> dict[str, str] | None:
        """Single use: the hash is read and deleted atomically."""
        if not ticket or len(ticket) > 128:
            return None
        key = f"av:wsticket:{_h(ticket)}"
        async with self.r.pipeline(transaction=True) as p:
            p.hgetall(key)
            p.delete(key)
            data, _ = await p.execute()
        if not data:
            return None
        return {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in data.items()
        }

    # ---- hold countdown mirror ----------------------------------------------------------------------
    async def mirror_hold(self, booking_id: str, expires_epoch: int) -> None:
        await self.r.set(f"av:hold:{booking_id}", str(expires_epoch), ex=150)

    # ---- rate limiting -------------------------------------------------------------------------------
    async def hit(self, scope: str, ident: str, limit: int, window_s: int) -> int | None:
        """Fixed-window counter. Returns seconds to wait when over the limit, else None."""
        now = int(time.time())
        bucket = now // window_s
        key = f"av:rl:{scope}:{ident}:{bucket}"
        async with self.r.pipeline(transaction=True) as p:
            p.incr(key)
            p.expire(key, window_s + 1)
            count, _ = await p.execute()
        if int(count) > limit:
            return window_s - (now % window_s) or 1
        return None

    # ---- events --------------------------------------------------------------------------------------
    async def publish_user(self, user_id: str, event: dict[str, Any]) -> None:
        await self.r.publish(f"av:events:user:{user_id}", orjson.dumps(event))


async def _sleep(s: float) -> None:
    import asyncio

    await asyncio.sleep(s)
