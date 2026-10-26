"""The call loop around the brain: load state, build the snapshot, run `step()`, carry out its effects.

The brain decides; this module only executes. Each turn is one database transaction acting as the caller, so
RLS applies to every effect, and the exclusion constraints have the final say on double bookings. When the
database refuses a hold (someone else took the slot a moment ago), the refusal goes back into the brain as a
`hold_failed` event and the brain chooses what to say next.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import orjson
import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from astro_brain.catalogue import SITE
from astro_brain.engine import apply_effect_result, draft_from_dict, step
from astro_brain.timeutil import night_of
from astro_brain.types import CallState, Draft, Effect, StepResult, TurnEvent

from .db import Database
from .errors import ApiError, sqlstate
from .settings import Settings
from .snapshot_service import SnapshotService, describe, to_dt
from .store import Store

log = structlog.get_logger()
HOLD_SECONDS = 120


@dataclass
class TurnOutput:
    reply: str
    state: str
    question: bool
    ended: bool
    trace: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)


def now_minute(settings: Settings) -> int:
    if settings.frozen_now and not settings.is_prod_like:
        return int(datetime.fromisoformat(settings.frozen_now).replace(tzinfo=UTC).timestamp()) // 60
    return int(time.time()) // 60


def _draft(x: Any) -> Draft:
    return x if isinstance(x, Draft) else draft_from_dict(x)


class CallService:
    def __init__(self, db: Database, store: Store, snapshots: SnapshotService, settings: Settings) -> None:
        self.db, self.store, self.snapshots, self.settings = db, store, snapshots, settings
        self._ids: dict[str, dict[str, Any]] = {}

    # ---- lifecycle ------------------------------------------------------------------------------
    async def start(self, claims: dict[str, Any], channel: str, stt: str, tts: str) -> tuple[str, TurnOutput]:
        async with self.db.as_user(claims) as conn:
            # One live call per user (EC-52): end any stale call first, the unique index guards races.
            stale = (
                (
                    await conn.execute(
                        text("select id from astro.calls where user_id = astro.uid() and status <> 'ended'")
                    )
                )
                .scalars()
                .all()
            )
            for cid in stale:
                await self._end(conn, str(cid), "abandoned", "superseded")
                await self.store.drop_state(str(cid))
            snap = await self.snapshots.build(conn, now_minute(self.settings))
            call_id = str(
                (
                    await conn.execute(
                        text(
                            "insert into astro.calls (user_id, channel, status, stt_provider, tts_provider, "
                            "snapshot_version) values (astro.uid(), cast(:ch as astro.call_channel), 'active', "
                            ":stt, :tts, :v) returning id"
                        ),
                        {"ch": channel, "stt": stt, "tts": tts, "v": snap.version},
                    )
                ).scalar_one()
            )
            keep = (await conn.execute(text("select keep_transcripts from astro.user_settings"))).scalar()
            meta = {"user_id": claims["sub"], "claims": claims, "keep": bool(keep), "seq": 0}
            out = await self._run(
                conn, call_id, CallState(), TurnEvent("start", now=now_minute(self.settings)), snap, meta
            )
        await self.store.save_meta(call_id, meta)
        await self.store.set_live_call(claims["sub"], call_id)
        return call_id, out

    async def turn(self, call_id: str, event: TurnEvent) -> TurnOutput:
        meta = await self.store.load_meta(call_id)
        if meta is None:
            raise ApiError(410, "call_gone", "This call has ended. Start a new one.")
        async with self.store.turn_lock(call_id):
            state = await self.store.load_state(call_id)
            if state is None:
                raise ApiError(410, "call_gone", "This call has ended. Start a new one.")
            if state.state == "ENDED":
                raise ApiError(409, "call_ended", "This call has ended.")
            ev = TurnEvent(
                event.kind,
                text=event.text[:500],
                confidence=event.confidence,
                now=now_minute(self.settings),
                heard_full=event.heard_full,
                data=event.data,
            )
            async with self.db.as_user(meta["claims"]) as conn:
                snap = await self.snapshots.build(conn, ev.now)
                out = await self._run(conn, call_id, state, ev, snap, meta)
            await self.store.save_meta(call_id, meta)
        return out

    async def disconnect(self, call_id: str) -> None:
        """Socket dropped: release any hold now; keep state for a 60-second reconnect window."""
        meta = await self.store.load_meta(call_id)
        if meta is None:
            return
        try:
            out = await self.turn(call_id, TurnEvent("hangup"))
            log.info("call_disconnected", call=call_id, state=out.state)
        except ApiError:
            pass
        await self.store.expire_soon(call_id)
        await self.store.clear_live_call(meta["user_id"], call_id)

    # ---- one turn -----------------------------------------------------------------------------------
    async def _run(
        self, conn: AsyncConnection, call_id: str, state: CallState, ev: TurnEvent, snap: Any, meta: dict[str, Any]
    ) -> TurnOutput:
        t0 = time.perf_counter()
        result = step(state, ev, snap)
        brain_ms = (time.perf_counter() - t0) * 1000
        events: list[dict[str, Any]] = []
        replies = [result]
        state, retry = await self._apply(conn, call_id, result, events, ev.now)
        if retry is not None:
            # The database refused a hold: tell the brain, on fresh data, and let it decide what to say.
            snap = await self.snapshots.build(conn, ev.now)
            second = step(state, TurnEvent("hold_failed", now=ev.now, data={"reason": retry}), snap)
            replies.append(second)
            state, _ = await self._apply(conn, call_id, second, events, ev.now)
        final = replies[-1]
        reply_text = final.reply.text
        trace = dict(final.trace)
        trace["latency_ms"] = {"brain": round(brain_ms, 2), "turn": round((time.perf_counter() - t0) * 1000, 2)}
        trace["data"] = describe(snap)
        if len(replies) > 1:
            trace["retried_after"] = replies[0].trace.get("effects")
        await self._log_turns(conn, call_id, meta, ev, state, result, final, trace)
        await self.store.save_state(call_id, state)
        if state.state == "ENDED":
            await self.store.clear_live_call(meta["user_id"], call_id)
        return TurnOutput(reply_text, state.state, final.reply.question, state.state == "ENDED", trace, events)

    async def _apply(
        self, conn: AsyncConnection, call_id: str, r: StepResult, events: list[dict[str, Any]], now: int
    ) -> tuple[CallState, str | None]:
        state = r.state
        last_hold: str | None = None
        for e in r.effects:
            try:
                async with conn.begin_nested():
                    state, last_hold = await self._effect(conn, call_id, state, e, last_hold, events, now)
            except DBAPIError as err:
                code = sqlstate(err)
                if e.kind in ("hold", "commit") and code in ("23P01", "AV001", "AV002"):
                    log.info("effect_refused", kind=e.kind, sqlstate=code)
                    state = state.with_(hold_id=None, hold_expires=None)
                    return state, {"23P01": "slot_conflict", "AV001": "hold_expired", "AV002": "allocation"}[code]
                raise
        return state, None

    async def _effect(
        self,
        conn: AsyncConnection,
        call_id: str,
        state: CallState,
        e: Effect,
        last_hold: str | None,
        events: list[dict[str, Any]],
        now: int,
    ) -> tuple[CallState, str | None]:
        match e.kind:
            case "hold":
                d = _draft(e.data["draft"])
                hid, expires = await self._hold(conn, call_id, d)
                # The brain's clock may be frozen for demos, so its expiry is relative to the brain's "now".
                state = apply_effect_result(state, "hold_taken", hold_id=hid, expires=now + HOLD_SECONDS // 60)
                await self.store.mirror_hold(hid, int(expires.timestamp()))
                events.append({"type": "hold", "booking_id": hid, "expires_at": expires.isoformat()})
                return state, hid
            case "release":
                hid = e.data.get("hold_id")
                if hid:
                    await conn.execute(
                        text(
                            "update astro.bookings set status = 'cancelled', cancel_reason = 'released' "
                            "where id = cast(:i as uuid) and status = 'held'"
                        ),
                        {"i": hid},
                    )
                    events.append({"type": "hold_released", "booking_id": hid})
                return state, last_hold
            case "commit":
                hid = e.data.get("hold_id") or last_hold
                if not hid:
                    raise ApiError(409, "no_hold", "There is no hold to confirm.")
                booking = await self._commit(conn, call_id, hid)
                state = apply_effect_result(state, "committed", booking_id=hid)
                events.append({"type": "booked", "booking": booking})
                return state, None
            case "cancel_booking":
                bid = e.data["booking_id"]
                await conn.execute(
                    text(
                        "update astro.bookings set status = 'cancelled', cancel_reason = 'cancelled by voice' "
                        "where id = cast(:i as uuid) and status = 'confirmed'"
                    ),
                    {"i": bid},
                )
                await self._notify(conn, "booking_cancelled", bid, {"booking_id": bid})
                await conn.execute(text("select astro.audit('cancel', 'booking', :i)"), {"i": bid})
                events.append({"type": "cancelled", "booking_id": bid})
                return state, last_hold
            case "join_waitlist":
                d = _draft(e.data["draft"])
                wid = await self._join_waitlist(conn, d)
                events.append({"type": "waitlisted", "entry_id": wid})
                return state, last_hold
            case "end_call":
                await self._end(conn, call_id, e.data.get("outcome", "info_only"), "caller")
                events.append({"type": "ended", "outcome": e.data.get("outcome")})
                return state, last_hold
        return state, last_hold

    # ---- effect implementations ------------------------------------------------------------------------
    async def _ids_for(self, conn: AsyncConnection) -> dict[str, Any]:
        if not self._ids:
            tel = (await conn.execute(text("select code, id from astro.telescopes"))).all()
            tgt = (await conn.execute(text("select slug, id from astro.targets"))).all()
            self._ids = {"tel": dict(tel), "tgt": {k: str(v) for k, v in tgt}}
        return self._ids

    async def _hold(self, conn: AsyncConnection, call_id: str, d: Draft) -> tuple[str, datetime]:
        assert d.start is not None and d.end is not None and d.telescope and d.target
        ids = await self._ids_for(conn)
        period = f"[{to_dt(d.start).isoformat()},{to_dt(d.end).isoformat()})"
        await conn.execute(
            text("select astro.expire_lapsed_holds(cast(:t as smallint), cast(cast(:p as text) as tstzrange))"),
            {"t": ids["tel"][d.telescope], "p": period},
        )
        row = (
            await conn.execute(
                text(
                    "insert into astro.bookings (user_id, telescope_id, target_id, period, night, status, "
                    "hold_expires_at, source, call_id) values (astro.uid(), :t, cast(:g as uuid), "
                    "cast(cast(:p as text) as tstzrange), :n, 'held', now() + make_interval(secs => :s), 'voice', "
                    "cast(:c as uuid)) returning id, hold_expires_at"
                ),
                {
                    "t": ids["tel"][d.telescope],
                    "g": ids["tgt"][d.target],
                    "p": period,
                    "n": d.night or night_of(d.start, SITE.tz_offset_min),
                    "s": HOLD_SECONDS,
                    "c": call_id,
                },
            )
        ).one()
        return str(row[0]), row[1]

    async def _commit(self, conn: AsyncConnection, call_id: str, hid: str) -> dict[str, Any]:
        row = (
            await conn.execute(
                text(
                    "update astro.bookings set status = 'confirmed' where id = cast(:i as uuid) and status = 'held' "
                    "returning id, lower(period), upper(period), night, telescope_id, target_id"
                ),
                {"i": hid},
            )
        ).first()
        if row is None:
            raise ApiError(409, "hold_missing", "That hold is no longer there.")
        await conn.execute(
            text("update astro.calls set booking_id = cast(:b as uuid) where id = cast(:c as uuid)"),
            {"b": hid, "c": call_id},
        )
        tel, tgt = (
            await conn.execute(
                text(
                    "select t.code, t.name, g.slug, g.name from astro.telescopes t, astro.targets g "
                    "where t.id = :t and g.id = :g"
                ),
                {"t": row[4], "g": row[5]},
            )
        ).one()[1::2]
        booking = {
            "id": hid,
            "start": row[1].isoformat(),
            "end": row[2].isoformat(),
            "night": row[3].isoformat(),
            "telescope": tel,
            "target": tgt,
        }
        if (await conn.execute(text("select email_confirmations from astro.user_settings"))).scalar():
            await self._notify(conn, "booking_confirmed", hid, booking)
        await conn.execute(text("select astro.audit('confirm', 'booking', :i)"), {"i": hid})
        return booking

    async def _join_waitlist(self, conn: AsyncConnection, d: Draft) -> str:
        assert d.start is not None and d.end is not None and d.telescope and d.target
        ids = await self._ids_for(conn)
        row = (
            await conn.execute(
                text(
                    "insert into astro.waitlist_entries (user_id, telescope_id, target_id, night, wanted, grade, "
                    "flex_minutes) select astro.uid(), :t, cast(:g as uuid), :n, cast(cast(:p as text) as tstzrange), "
                    "coalesce((select a.grade from astro.allocations a where a.user_id = astro.uid() "
                    "order by a.semester_code desc limit 1), 'C'), 30 "
                    "on conflict do nothing returning id"
                ),
                {
                    "t": ids["tel"][d.telescope],
                    "g": ids["tgt"][d.target],
                    "n": d.night or night_of(d.start, SITE.tz_offset_min),
                    "p": f"[{to_dt(d.start).isoformat()},{to_dt(d.end).isoformat()})",
                },
            )
        ).first()
        return str(row[0]) if row else "existing"

    async def _notify(self, conn: AsyncConnection, kind: str, booking_id: str, payload: dict[str, Any]) -> None:
        await conn.execute(
            text(
                "insert into astro.notifications (user_id, kind, booking_id, payload, dedupe_key) values "
                "(astro.uid(), cast(:k as astro.notification_kind), cast(:b as uuid), cast(:p as jsonb) || "
                "jsonb_strip_nulls(jsonb_build_object('email', (select astro.jwt()) ->> 'email')), :d) "
                "on conflict (dedupe_key) do nothing"
            ),
            {"k": kind, "b": booking_id, "p": orjson.dumps(payload).decode(), "d": f"{kind}:{booking_id}"},
        )

    async def _end(self, conn: AsyncConnection, call_id: str, outcome: str, reason: str) -> None:
        await conn.execute(
            text(
                "update astro.calls set status = 'ended', ended_at = now(), "
                "outcome = cast(:o as astro.call_outcome), end_reason = :r "
                "where id = cast(:c as uuid) and status <> 'ended'"
            ),
            {
                "o": outcome
                if outcome in ("booked", "cancelled_booking", "info_only", "abandoned", "error")
                else "info_only",
                "r": reason,
                "c": call_id,
            },
        )

    async def _log_turns(
        self,
        conn: AsyncConnection,
        call_id: str,
        meta: dict[str, Any],
        ev: TurnEvent,
        state: CallState,
        first: StepResult,
        final: StepResult,
        trace: dict[str, Any],
    ) -> None:
        meta["seq"] = seq = int(meta.get("seq", 0)) + 1
        keep = meta.get("keep", True)
        before = first.trace["state"][0]
        common = {"c": call_id, "s": seq, "b": before, "a": state.state}
        stmt = text(
            "insert into astro.call_turns (call_id, seq, role, user_id, text, stt_confidence, intent, state_before, "
            "state_after, trace, latency_ms) values (cast(:c as uuid), :s, cast(:role as astro.turn_role), "
            "astro.uid(), :t, :conf, :i, :b, :a, cast(:tr as jsonb), cast(:l as jsonb))"
        )
        if ev.kind == "utterance":
            await conn.execute(
                stmt,
                {
                    **common,
                    "role": "caller",
                    "t": ev.text if keep else None,
                    "conf": ev.confidence,
                    "i": final.trace.get("intent"),
                    "tr": None,
                    "l": "{}",
                },
            )
        await conn.execute(
            stmt,
            {
                **common,
                "role": "assistant",
                "t": final.reply.text if keep else None,
                "conf": None,
                "i": final.trace.get("intent"),
                "tr": orjson.dumps(trace).decode() if keep else None,
                "l": orjson.dumps(trace.get("latency_ms", {})).decode(),
            },
        )
        await conn.execute(
            text("update astro.calls set turns = :n where id = cast(:c as uuid)"), {"n": seq, "c": call_id}
        )
