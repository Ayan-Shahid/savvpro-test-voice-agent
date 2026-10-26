"""Calls: start one over REST, then talk over a WebSocket (voice or text), or over REST in text mode.

WebSocket auth uses a single-use ticket minted by POST /api/calls (30 s TTL, bound to the user, the call and
the page's Origin), so the access token never appears in a URL.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

import orjson
import structlog
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from astro_brain.types import TurnEvent

from ..auth import CurrentUser
from ..call_service import TurnOutput
from ..deps import Services, Svc
from ..errors import ApiError
from ..speech.deepgram import FinalTranscript

router = APIRouter(tags=["calls"])
log = structlog.get_logger()
MAX_CALL_S = 30 * 60
IDLE_S = 5 * 60
MAX_FRAME = 64 * 1024


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["voice", "text"] = "text"


class TurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["utterance", "undo", "silence", "hangup"] = "utterance"
    text: str = Field(default="", max_length=500)
    heard_full: bool = True


def _out(o: TurnOutput) -> dict[str, Any]:
    return {
        "reply": o.reply,
        "state": o.state,
        "question": o.question,
        "ended": o.ended,
        "trace": o.trace,
        "events": o.events,
    }


@router.post("/api/calls", status_code=201)
async def start_call(body: StartIn, request: Request, user: CurrentUser, svc: Svc) -> dict[str, Any]:
    origin = (request.headers.get("origin") or svc.settings.public_origin).rstrip("/")
    if origin not in svc.settings.allowed_origins:
        raise ApiError(403, "bad_origin", "Calls can only be started from AstroVoice.")
    voice = body.channel == "voice" and svc.speech.enabled
    call_id, out = await svc.calls.start(
        user.claims,
        "voice" if voice else "text",
        "deepgram-flux" if voice else "none",
        svc.settings.deepgram_tts_model if voice else "none",
    )
    ticket = await svc.store.mint_ticket(user_id=user.user_id, call_id=call_id, origin=origin, claims=user.claims)
    return {"call_id": call_id, "ticket": ticket, "ws_path": "/ws/call", "voice": voice, **_out(out)}


@router.post("/api/calls/{call_id}/turn")
async def rest_turn(call_id: str, body: TurnIn, user: CurrentUser, svc: Svc) -> dict[str, Any]:
    meta = await svc.store.load_meta(call_id)
    if meta is None or meta["user_id"] != user.user_id:
        raise ApiError(404, "not_found", "That call was not found.")
    out = await svc.calls.turn(call_id, TurnEvent(body.kind, text=body.text, heard_full=body.heard_full))
    return _out(out)


@router.get("/api/calls")
async def history(user: CurrentUser, svc: Svc) -> dict[str, Any]:
    async with svc.db.as_user(user.claims) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "select id, channel::text as channel, status::text as status, outcome::text as outcome, "
                        "started_at, ended_at, turns, booking_id from astro.calls order by started_at desc limit 50"
                    )
                )
            )
            .mappings()
            .all()
        )
    return {
        "calls": [
            {
                k: (str(v) if k in ("id", "booking_id") and v else (v.isoformat() if hasattr(v, "isoformat") else v))
                for k, v in r.items()
            }
            for r in rows
        ]
    }


@router.get("/api/calls/{call_id}/turns")
async def turns(call_id: str, user: CurrentUser, svc: Svc) -> dict[str, Any]:
    async with svc.db.as_user(user.claims) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "select seq, role::text as role, text, intent, state_before, state_after, trace, latency_ms, "
                        "created_at from astro.call_turns where call_id = cast(:c as uuid) order by seq, role"
                    ),
                    {"c": call_id},
                )
            )
            .mappings()
            .all()
        )
    out = []
    for r in rows:
        d = dict(r)
        for k in ("trace", "latency_ms"):
            if isinstance(d[k], str):
                d[k] = orjson.loads(d[k])
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return {"turns": out}


# ---- WebSocket ------------------------------------------------------------------------------------------
class CallSocket:
    def __init__(self, ws: WebSocket, svc: Services, call_id: str) -> None:
        self.ws, self.svc, self.call_id = ws, svc, call_id
        self.heard_full = True
        self.audio: asyncio.Queue[bytes | None] | None = None
        self.stt_task: asyncio.Task[None] | None = None
        self.send_lock = asyncio.Lock()
        self.ended = False
        self.speed = 1.0

    async def send_json(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.ws.send_text(orjson.dumps(payload).decode())

    async def send_turn(self, out: TurnOutput, *, speak: bool) -> None:
        await self.send_json({"type": "reply", **_out(out)})
        for ev in out.events:
            await self.send_json(ev)
        if speak and self.svc.speech.enabled and out.reply:
            try:
                audio, model = await self.svc.speech.speak(out.reply, speed=self.speed)
                async with self.send_lock:
                    await self.ws.send_text(
                        orjson.dumps({"type": "audio", "format": "mp3", "model": model, "bytes": len(audio)}).decode()
                    )
                    await self.ws.send_bytes(audio)
            except Exception as e:
                log.warning("tts_failed", error=str(e)[:200])
                await self.send_json({"type": "tts_unavailable"})
        if out.ended:
            self.ended = True

    async def handle_event(self, kind: str, text_: str = "", confidence: float = 1.0, *, speak: bool) -> None:
        ev = TurnEvent(kind, text=text_, confidence=confidence, heard_full=self.heard_full)  # type: ignore[arg-type]
        self.heard_full = True
        try:
            out = await self.svc.calls.turn(self.call_id, ev)
        except ApiError as e:
            await self.send_json({"type": "error", "code": e.code, "message": e.message})
            if e.code in ("call_gone", "call_ended"):
                self.ended = True
            return
        await self.send_turn(out, speak=speak)

    async def start_audio(self, sample_rate: int) -> None:
        if not self.svc.speech.enabled:
            await self.send_json(
                {"type": "error", "code": "voice_unavailable", "message": "Voice is not configured here. Type instead."}
            )
            return
        if self.stt_task:
            return
        self.audio = asyncio.Queue(maxsize=200)
        queue = self.audio

        async def chunks() -> AsyncIterator[bytes]:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        async def on_final(t: FinalTranscript) -> None:
            await self.send_json({"type": "transcript", "text": t.text, "final": True, "confidence": t.confidence})
            await self.handle_event("utterance", t.text, t.confidence, speak=True)

        async def on_event(e: dict[str, Any]) -> None:
            if e["event"] == "StartOfTurn":
                await self.send_json({"type": "user_speaking"})
            elif e["transcript"]:
                await self.send_json({"type": "transcript", "text": e["transcript"], "final": False})

        async def run() -> None:
            try:
                await self.svc.speech.stream(chunks(), on_final, on_event, sample_rate=sample_rate)
            except Exception as e:
                log.warning("stt_failed", error=str(e)[:200])
                await self.send_json(
                    {
                        "type": "error",
                        "code": "stt_failed",
                        "message": "Speech recognition dropped. You can keep typing.",
                    }
                )
            finally:
                self.stt_task = None

        self.stt_task = asyncio.create_task(run())

    async def stop_audio(self) -> None:
        if self.audio:
            with contextlib.suppress(asyncio.QueueFull):
                self.audio.put_nowait(None)
        self.audio = None

    async def on_message(self, msg: dict[str, Any]) -> None:
        if "bytes" in msg and msg["bytes"] is not None:
            data: bytes = msg["bytes"]
            if self.audio is not None and len(data) <= MAX_FRAME:
                with contextlib.suppress(asyncio.QueueFull):
                    self.audio.put_nowait(data)
            return
        raw = msg.get("text")
        if not raw or len(raw) > 4096:
            return
        try:
            m = orjson.loads(raw)
        except orjson.JSONDecodeError:
            return
        kind = m.get("type")
        if kind == "text":
            t = str(m.get("text", ""))[:500].strip()
            if t:
                await self.handle_event("utterance", t, speak=bool(m.get("speak")))
        elif kind in ("undo", "silence", "hangup"):
            await self.handle_event(kind, speak=bool(m.get("speak")))
        elif kind == "barge_in":
            self.heard_full = False
        elif kind == "audio_start":
            rate = int(m.get("sample_rate", 16000))
            await self.start_audio(rate if rate in (16000, 24000, 48000) else 16000)
        elif kind == "audio_stop":
            await self.stop_audio()
        elif kind == "speed":
            self.speed = min(1.15, max(0.85, float(m.get("value", 1.0))))
        elif kind == "ping":
            await self.send_json({"type": "pong"})


@router.websocket("/ws/call")
async def ws_call(ws: WebSocket, ticket: str = "") -> None:
    svc: Services = ws.app.state.services
    origin = (ws.headers.get("origin") or "").rstrip("/")
    if origin not in svc.settings.allowed_origins:
        await ws.close(code=1008, reason="origin")
        return
    data = await svc.store.redeem_ticket(ticket)
    if data is None or data.get("origin") != origin:
        await ws.close(code=1008, reason="ticket")
        return
    call_id = data["call"]
    await ws.accept()
    sock = CallSocket(ws, svc, call_id)
    state = await svc.store.load_state(call_id)
    await sock.send_json(
        {"type": "ready", "call_id": call_id, "voice": svc.speech.enabled, "state": state.state if state else None}
    )
    started = time.monotonic()
    try:
        while not sock.ended:
            remaining = MAX_CALL_S - (time.monotonic() - started)
            if remaining <= 0:
                await sock.handle_event("hangup", speak=False)
                break
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=min(IDLE_S, remaining))
            except TimeoutError:
                await sock.handle_event("hangup", speak=False)
                break
            if msg["type"] == "websocket.disconnect":
                break
            await sock.on_message(msg)
    except WebSocketDisconnect:
        pass
    finally:
        await sock.stop_audio()
        if not sock.ended:
            await svc.calls.disconnect(call_id)
        with contextlib.suppress(Exception):
            await ws.close()
