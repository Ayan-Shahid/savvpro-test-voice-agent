"""End-to-end through HTTP and WebSocket: auth, headers, origin checks, and real calls that book."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .conftest import ORIGIN, auth, token

pytestmark = pytest.mark.db


def test_health_and_security_headers(client: TestClient) -> None:
    r = client.get("/api/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    for h in (
        "x-content-type-options",
        "x-frame-options",
        "content-security-policy",
        "referrer-policy",
        "cache-control",
        "x-request-id",
    ):
        assert h in r.headers
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_auth_is_required_and_verified(client: TestClient, seeded: dict[str, Any]) -> None:
    assert client.get("/api/me").status_code == 401
    bad = token(seeded["people"]["demo"]) + "x"
    r = client.get("/api/me", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"
    expired = token(seeded["people"]["demo"], exp=1)
    assert (
        client.get("/api/me", headers={"Authorization": f"Bearer {expired}"}).json()["error"]["code"] == "token_expired"
    )


def test_cross_origin_writes_are_refused(client: TestClient, seeded: dict[str, Any]) -> None:
    h = {**auth(seeded["people"]["demo"]), "Origin": "https://evil.example"}
    r = client.patch("/api/me/settings", json={"captions": False}, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"


def test_me_settings_and_validation(client: TestClient, seeded: dict[str, Any]) -> None:
    h = auth(seeded["people"]["demo"])
    me = client.get("/api/me", headers=h).json()
    assert me["display_name"] == "Demo Observer" and me["allocation"]["grade"] == "B"
    assert client.patch("/api/me/settings", json={"time_format": "24h"}, headers=h).status_code == 200
    assert client.get("/api/me", headers=h).json()["settings"]["time_format"] == "24h"
    client.patch("/api/me/settings", json={"time_format": "12h"}, headers=h)
    r = client.patch("/api/me/settings", json={"speech_rate": 3, "role": "admin"}, headers=h)
    assert r.status_code == 422
    fields = {f["field"] for f in r.json()["error"]["fields"]}
    assert {"speech_rate", "role"} <= fields


def test_users_cannot_see_each_others_data(client: TestClient, seeded: dict[str, Any]) -> None:
    p = seeded["people"]
    demo = client.get("/api/bookings", headers=auth(p["demo"])).json()["bookings"]
    juno = client.get("/api/bookings", headers=auth(p["juno.tarr"])).json()["bookings"]
    assert {b["id"] for b in demo}.isdisjoint({b["id"] for b in juno})
    r = client.post(f"/api/bookings/{demo[0]['id']}/cancel", headers=auth(p["juno.tarr"]))
    assert r.status_code == 404


def test_text_call_books_through_the_database(client: TestClient, seeded: dict[str, Any]) -> None:
    h = auth(seeded["people"]["kit.wrenfield"])
    start = client.post("/api/calls", json={"channel": "text"}, headers=h)
    assert start.status_code == 201, start.text
    call = start.json()
    assert call["state"] == "COLLECTING" or call["reply"]
    cid = call["call_id"]

    def say(t: str) -> dict[str, Any]:
        r = client.post(f"/api/calls/{cid}/turn", json={"text": t}, headers=h)
        assert r.status_code == 200, r.text
        return r.json()

    r1 = say("Book the refractor for Saturn on Thursday from 8 to 10 PM")
    assert r1["state"] == "CONFIRMING", r1["reply"]
    assert any(e["type"] == "hold" for e in r1["events"])
    r2 = say("Yes")
    assert r2["state"] == "BOOKED", r2["reply"]
    booked = [e for e in r2["events"] if e["type"] == "booked"]
    assert booked and booked[0]["booking"]["telescope"] == "Optical Refractor"
    mine = client.get("/api/bookings", headers=h).json()["bookings"]
    assert any(b["id"] == booked[0]["booking"]["id"] and b["status"] == "confirmed" for b in mine)
    # Another user's call for the same slot sees it as taken and is offered something else.
    h2 = auth(seeded["people"]["gil.sorrel"])
    c2 = client.post("/api/calls", json={"channel": "text"}, headers=h2).json()
    r3 = client.post(
        f"/api/calls/{c2['call_id']}/turn",
        headers=h2,
        json={"text": "Book the refractor for Saturn on Thursday from 8 to 10 PM"},
    ).json()
    assert r3["state"] == "PROPOSING", r3["reply"]
    assert "already booked" in r3["reply"]
    # The turn log keeps the brain's trace for the inspector.
    turns = client.get(f"/api/calls/{cid}/turns", headers=h).json()["turns"]
    assistant = [t for t in turns if t["role"] == "assistant"]
    assert assistant[-1]["state_after"] == "BOOKED"
    assert any(t["trace"]["checks"] for t in assistant)  # the proposal turn recorded every rule result


def test_other_users_cannot_drive_my_call(client: TestClient, seeded: dict[str, Any]) -> None:
    h = auth(seeded["people"]["ivo.penlow"])
    cid = client.post("/api/calls", json={"channel": "text"}, headers=h).json()["call_id"]
    r = client.post(
        f"/api/calls/{cid}/turn", json={"text": "cancel my booking"}, headers=auth(seeded["people"]["juno.tarr"])
    )
    assert r.status_code == 404


def test_websocket_ticket_is_single_use_and_origin_bound(client: TestClient, seeded: dict[str, Any]) -> None:
    h = auth(seeded["people"]["esme.harrow"])
    call = client.post("/api/calls", json={"channel": "text"}, headers=h).json()
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(f"/ws/call?ticket={call['ticket']}", headers={"origin": "https://evil.example"}) as ws,
    ):
        ws.receive_json()
    with client.websocket_connect(f"/ws/call?ticket={call['ticket']}", headers={"origin": ORIGIN}) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "text", "text": "What's the weather like tomorrow night?"})
        msg = ws.receive_json()
        assert msg["type"] == "reply" and "85% cloud" in msg["reply"], msg["reply"]
        ws.send_json({"type": "hangup"})
        while (m := ws.receive_json())["type"] != "reply":
            pass
        assert m["ended"]
    with pytest.raises(WebSocketDisconnect):  # the ticket was used up
        with client.websocket_connect(f"/ws/call?ticket={call['ticket']}", headers={"origin": ORIGIN}) as ws:
            ws.receive_json()


def test_rate_limit_returns_429_with_retry_after(client: TestClient, seeded: dict[str, Any]) -> None:
    h = {**auth(seeded["people"]["hana.brisk"]), "X-Forwarded-For": "203.0.113.77"}
    codes = [client.post("/api/calls", json={"channel": "text"}, headers=h).status_code for _ in range(32)]
    assert 429 in codes
    r = client.post("/api/calls", json={"channel": "text"}, headers=h)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_snapshot_cache_rebuilds_once_under_a_burst(client: TestClient, seeded: dict[str, Any]) -> None:
    """Cache stampede: 20 simultaneous turns on a cold cache trigger exactly one sky/weather rebuild."""
    import asyncio

    svc = client.app.state.services  # type: ignore[attr-defined]
    svc.snapshots._base.clear()
    svc.snapshots._version_cache = None
    before = svc.snapshots.rebuilds
    from .conftest import claims_for

    async def burst() -> None:
        async def one() -> None:
            async with svc.db.as_user(claims_for(seeded["people"]["demo"])) as conn:
                await svc.snapshots.build(conn, 29_850_000)

        await asyncio.gather(*(one() for _ in range(20)))

    client.portal.call(burst)  # type: ignore[attr-defined]
    assert svc.snapshots.rebuilds - before == 1
