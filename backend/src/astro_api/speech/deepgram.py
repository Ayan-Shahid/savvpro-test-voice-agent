"""Deepgram adapters: Flux streaming STT (/v2/listen) and Flux TTS (/v2/speak) with an Aura-2 fallback.

Speech is only the ears and mouth. Transcripts go to the deterministic brain; nothing here decides anything.
Without an API key the service reports `enabled = False` and the app runs in text mode.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import orjson
import structlog
import websockets

from ..settings import Settings

log = structlog.get_logger()
API = "api.deepgram.com"


@dataclass(frozen=True)
class FinalTranscript:
    text: str
    confidence: float
    turn_index: int


class Deepgram:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.key = settings.deepgram_api_key.get_secret_value()
        self._http: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.key}"}

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5))
        return self._http

    async def aclose(self) -> None:
        if self._http:
            await self._http.aclose()

    # ---- TTS -------------------------------------------------------------------------------------
    async def speak(self, text: str, *, speed: float = 1.0) -> tuple[bytes, str]:
        """Returns (mp3 bytes, model used). Tries Flux TTS, then Aura-2."""
        if not self.enabled:
            raise RuntimeError("speech disabled")
        opt = {"mip_opt_out": "true"} if self.settings.deepgram_mip_opt_out else {}
        attempts = (
            (
                f"https://{API}/v2/speak",
                {"model": self.settings.deepgram_tts_model, "encoding": "mp3", "speed": f"{speed:.2f}", **opt},
            ),
            (f"https://{API}/v1/speak", {"model": self.settings.deepgram_tts_fallback, "encoding": "mp3", **opt}),
        )
        last: Exception | None = None
        for url, params in attempts:
            try:
                r = await self.http.post(url, params=params, json={"text": text}, headers=self._headers())
                if r.status_code == 200 and r.content:
                    return r.content, params["model"]
                last = RuntimeError(f"{r.status_code} {r.text[:200]}")
            except httpx.HTTPError as e:
                last = e
            log.warning("tts_attempt_failed", model=params["model"], error=str(last)[:200])
        raise RuntimeError(f"tts failed: {last}")

    # ---- STT -------------------------------------------------------------------------------------
    def listen_url(self, sample_rate: int = 16000) -> str:
        q: dict[str, Any] = {
            "model": self.settings.deepgram_stt_model,
            "encoding": "linear16",
            "sample_rate": sample_rate,
            "eot_threshold": 0.8,
            "eot_timeout_ms": 4000,
        }
        if self.settings.deepgram_mip_opt_out:
            q["mip_opt_out"] = "true"
        return f"wss://{API}/v2/listen?{urlencode(q)}"

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        on_final: Callable[[FinalTranscript], Awaitable[None]],
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
        sample_rate: int = 16000,
    ) -> None:
        """Pump audio to Flux and report turns. `on_event` gets StartOfTurn/Update for captions and barge-in."""
        async with websockets.connect(
            self.listen_url(sample_rate), additional_headers=self._headers(), max_size=2**20, open_timeout=8
        ) as ws:

            async def pump() -> None:
                async for chunk in audio:
                    await ws.send(chunk)
                with contextlib.suppress(websockets.ConnectionClosed):
                    await ws.send(orjson.dumps({"type": "CloseStream"}).decode())

            sender = asyncio.create_task(pump())
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    msg = orjson.loads(raw)
                    if msg.get("type") != "TurnInfo":
                        if msg.get("type") == "Error":
                            log.warning("flux_error", msg=str(msg)[:300])
                        continue
                    ev = msg.get("event")
                    transcript = (msg.get("transcript") or "").strip()
                    if ev == "EndOfTurn" and transcript:
                        words = msg.get("words") or []
                        conf = sum(float(w.get("confidence", 0)) for w in words) / len(words) if words else 0.9
                        await on_final(FinalTranscript(transcript, round(conf, 3), int(msg.get("turn_index", 0))))
                    else:
                        await on_event({"event": ev, "transcript": transcript})
            finally:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await sender
