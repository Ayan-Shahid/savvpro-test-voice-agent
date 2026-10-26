"""Pure-ASGI middleware: request ids, security headers, origin checks, body limits and rate limits.

Caddy sets the same browser headers for pages; the API sets its own so it is safe even if reached directly.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import orjson
import structlog

from .settings import Settings

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

log = structlog.get_logger()

API_HEADERS: list[tuple[bytes, bytes]] = [
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), geolocation=(), microphone=(), payment=(), usb=()"),
]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_BODY = 64 * 1024

# (scope, limit, window seconds) by path prefix; the first match wins.
RATE_LIMITS: tuple[tuple[str, str, int, int], ...] = (
    ("/api/calls", "calls", 30, 60),
    ("/api/me", "me", 60, 60),
    ("/api/", "api", 240, 60),
)


def _header(scope: Scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k == name:
            return str(v.decode("latin-1"))
    return None


def client_ip(scope: Scope) -> str:
    # Caddy is the only thing that can reach the API and it overwrites X-Forwarded-For with the real client.
    fwd = _header(scope, b"x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    client = scope.get("client")
    return str(client[0]) if client else "unknown"


async def _reject(
    send: Send, status: int, code: str, message: str, rid: str, extra: dict[str, Any] | None = None
) -> None:
    body = orjson.dumps({"error": {"code": code, "message": message, "request_id": rid, **(extra or {})}})
    headers = [(b"content-type", b"application/json"), (b"x-request-id", rid.encode()), *API_HEADERS]
    if extra and "retry_after" in extra:
        headers.append((b"retry-after", str(extra["retry_after"]).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings, store_getter: Callable[[], Any]) -> None:
        self.app, self.settings, self.store_getter = app, settings, store_getter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        incoming = _header(scope, b"x-request-id") or ""
        rid = incoming if incoming.isascii() and 8 <= len(incoming) <= 64 else secrets.token_hex(8)
        scope.setdefault("state", {})["request_id"] = rid
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path: str = scope["path"]
        origin = _header(scope, b"origin")
        # Bearer tokens already stop CSRF; this also refuses state changes from any origin not on the list.
        if (
            method not in SAFE_METHODS
            and origin is not None
            and origin.rstrip("/") not in self.settings.allowed_origins
        ):
            await _reject(send, 403, "bad_origin", "Requests from this site are not allowed.", rid)
            return
        length = _header(scope, b"content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY):
            await _reject(send, 413, "too_large", "That request is too large.", rid)
            return

        store = self.store_getter()
        if store is not None and path.startswith("/api/") and not path.startswith("/api/health"):
            for prefix, name, limit, window in RATE_LIMITS:
                if path.startswith(prefix):
                    try:
                        wait = await store.hit(name, client_ip(scope), limit, window)
                    except Exception:  # Redis down: fail open for reads, closed for writes
                        wait = None if method in SAFE_METHODS else 5
                    if wait:
                        await _reject(
                            send,
                            429,
                            "rate_limited",
                            "Too many requests. Please wait a moment.",
                            rid,
                            {"retry_after": wait},
                        )
                        return
                    break

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > MAX_BODY:
                    raise ValueError("body too large")
            return msg

        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = [h for h in message.get("headers", []) if h[0].lower() not in {k for k, _ in API_HEADERS}]
                if not path.startswith("/api/docs"):
                    headers.extend(API_HEADERS)
                headers.append((b"x-request-id", rid.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, limited_receive, send_with_headers)
        finally:
            log.info(
                "request",
                method=method,
                path=path,
                status=status_holder["status"],
                ms=round((time.perf_counter() - started) * 1000, 1),
                request_id=rid,
            )
