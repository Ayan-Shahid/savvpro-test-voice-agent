"""Celery worker: outbox email, hold sweep, waitlist promotion (min-heap), completion and retention.

Runs as the `astro_worker` database role, which RLS lets see exactly the rows these jobs touch.
Every task is idempotent, so retries and overlapping beats are harmless.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Awaitable, Callable
from email.message import EmailMessage
from typing import Any, TypeVar

import orjson
import structlog
from celery import Celery
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from astro_api.settings import get_settings
from astro_brain.catalogue import SITE
from astro_brain.timeutil import fmt_window
from astro_brain.waitlist import Entry, WaitlistHeap

log = structlog.get_logger()
T = TypeVar("T")
settings = get_settings()


def _redis_url(db: int) -> str:
    """Same host and ACL user as ASTRO_REDIS_URL, with the password from the secret and a fixed database."""
    from urllib.parse import quote, urlsplit, urlunsplit

    u = urlsplit(settings.redis_url)
    pw = settings.redis_password.get_secret_value()
    user = u.username or ""
    auth = f"{quote(user)}:{quote(pw, safe='')}@" if pw else (f"{quote(user)}@" if user else "")
    return urlunsplit((u.scheme, f"{auth}{u.hostname}:{u.port or 6379}", f"/{db}", "", ""))


app = Celery("astro_worker", broker=_redis_url(1), backend=_redis_url(2))
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=86400,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="maintenance",
    task_routes={
        "astro_worker.tasks.send_outbox": {"queue": "email"},
        "astro_worker.tasks.promote_waitlist": {"queue": "waitlist"},
    },
    beat_schedule={
        "outbox": {"task": "astro_worker.tasks.send_outbox", "schedule": 15.0},
        "sweep-holds": {"task": "astro_worker.tasks.sweep_holds", "schedule": 60.0},
        "complete": {"task": "astro_worker.tasks.complete_past", "schedule": 600.0},
        "retention": {"task": "astro_worker.tasks.retention", "schedule": 3600.0},
    },
    broker_connection_retry_on_startup=True,
)


def _run[T](fn: Callable[[AsyncConnection], Awaitable[T]]) -> T:
    async def go() -> T:
        engine = create_async_engine(settings.worker_dsn, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                return await fn(conn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


# ---- email -----------------------------------------------------------------------------------------
def render(kind: str, payload: dict[str, Any], name: str) -> tuple[str, str]:
    if kind == "booking_confirmed":
        start = int(_epoch(payload["start"]) // 60)
        end = int(_epoch(payload["end"]) // 60)
        when = fmt_window(start, end, SITE.tz_offset_min)
        return (
            f"Booked: {payload['target']} on the {payload['telescope']}",
            f"Hi {name},\n\nYour observation is confirmed.\n\n  Target:    {payload['target']}\n"
            f"  Telescope: {payload['telescope']}\n  When:      {when} (site time, UTC+5)\n\n"
            f"Booking reference: {payload['id']}\n\nClear skies,\nAstroVoice at {SITE.name}\n",
        )
    if kind == "booking_cancelled":
        return ("Booking cancelled", f"Hi {name},\n\nYour booking {payload.get('booking_id')} has been cancelled.\n")
    if kind == "waitlist_offer":
        return (
            "A slot you wanted is free",
            f"Hi {name},\n\nA slot you were waiting for has opened up and is held for you for 30 minutes. "
            "Open AstroVoice to accept it.\n",
        )
    return ("AstroVoice update", f"Hi {name},\n\nThere is an update on your account.\n")


def _epoch(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso).timestamp()


def smtp_send(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.mail_from, to, subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
        if settings.smtp_starttls:
            s.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password.get_secret_value())
        s.send_message(msg)


@app.task(name="astro_worker.tasks.send_outbox")
def send_outbox(limit: int = 20) -> int:
    async def claim(conn: AsyncConnection) -> list[Any]:
        return list(
            (
                await conn.execute(
                    text(
                        "update astro.notifications n set status = 'sending', attempts = attempts + 1 "
                        "where n.id in (select id from astro.notifications where status in ('pending', 'failed') "
                        "and attempts < 5 order by created_at limit :l for update skip locked) "
                        "returning n.id, n.user_id, n.kind::text, n.payload"
                    ),
                    {"l": limit},
                )
            ).all()
        )

    rows = _run(claim)
    sent = 0
    for nid, uid, kind, payload in rows:
        data = payload if isinstance(payload, dict) else orjson.loads(payload)
        try:
            # The API copies the verified email claim into the payload; the worker never reads auth.users.
            email = data.get("email")
            if not email:
                raise ValueError("no recipient")
            name = _run(lambda c, u=uid: _name(c, str(u)))
            subject, body = render(kind, data, name)
            smtp_send(email, subject, body)
            _run(
                lambda c, i=nid: c.execute(
                    text(
                        "update astro.notifications set status = 'sent', sent_at = now(), "
                        "last_error = null where id = :i"
                    ),
                    {"i": i},
                )
            )
            sent += 1
        except Exception as e:  # keep going; the row retries on the next beat
            log.warning("email_failed", id=str(nid), error=str(e)[:200])
            _run(
                lambda c, i=nid, err=str(e)[:300]: c.execute(
                    text("update astro.notifications set status = 'failed', last_error = :e where id = :i"),
                    {"i": i, "e": err},
                )
            )
    return sent


async def _name(conn: AsyncConnection, uid: str) -> str:
    row = (
        await conn.execute(text("select display_name from astro.profiles where id = cast(:u as uuid)"), {"u": uid})
    ).first()
    return str(row[0]).split(" ")[0] if row else "there"


# ---- holds, waitlist, lifecycle --------------------------------------------------------------------
@app.task(name="astro_worker.tasks.sweep_holds")
def sweep_holds() -> int:
    async def go(conn: AsyncConnection) -> int:
        r = await conn.execute(
            text(
                "update astro.bookings set status = 'expired' where status = 'held' and hold_expires_at <= now() "
                "returning id"
            )
        )
        n = len(r.all())
        await conn.execute(
            text(
                "update astro.waitlist_entries set status = 'expired' where status = 'offered' "
                "and offer_expires_at <= now()"
            )
        )
        return n

    return _run(go)


@app.task(name="astro_worker.tasks.promote_waitlist")
def promote_waitlist(booking_id: str) -> str | None:
    """A booking was cancelled: offer its interval to the best-placed waiting entry that fits."""

    async def go(conn: AsyncConnection) -> str | None:
        freed = (
            await conn.execute(
                text(
                    "select telescope_id, night, lower(period), upper(period), target_id from astro.bookings "
                    "where id = cast(:i as uuid) and status = 'cancelled'"
                ),
                {"i": booking_id},
            )
        ).first()
        if freed is None:
            return None
        tel, night, fs, fe, _ = freed
        await conn.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 7))"), {"k": f"{tel}:{night}"})
        rows = (
            await conn.execute(
                text(
                    "select id, user_id, grade::text, extract(epoch from joined_at)::bigint / 60, seq, "
                    "extract(epoch from lower(wanted))::bigint / 60, extract(epoch from upper(wanted))::bigint / 60, "
                    "flex_minutes, target_id from astro.waitlist_entries where telescope_id = :t and night = :n "
                    "and status = 'waiting'"
                ),
                {"t": tel, "n": night},
            )
        ).all()
        targets = {str(r[0]): r[8] for r in rows}
        heap = WaitlistHeap(
            [Entry(str(r[0]), str(r[1]), r[2], int(r[3]), int(r[4]), int(r[5]), int(r[6]), int(r[7])) for r in rows]
        )
        found = heap.pop_until_fit(int(fs.timestamp()) // 60, int(fe.timestamp()) // 60)
        if found is None:
            return None
        entry, (s, e) = found
        from datetime import UTC, datetime

        period = (
            f"[{datetime.fromtimestamp(s * 60, UTC).isoformat()},{datetime.fromtimestamp(e * 60, UTC).isoformat()})"
        )
        bid = (
            await conn.execute(
                text(
                    "insert into astro.bookings (user_id, telescope_id, target_id, period, night, status, "
                    "hold_expires_at, source) values (cast(:u as uuid), :t, :g, cast(cast(:p as text) as tstzrange), "
                    ":n, 'held', now() + interval '30 minutes', 'waitlist') returning id"
                ),
                {"u": entry.user_id, "t": tel, "g": targets[entry.entry_id], "p": period, "n": night},
            )
        ).scalar_one()
        await conn.execute(
            text(
                "update astro.waitlist_entries set status = 'offered', offered_booking_id = :b, "
                "offer_expires_at = now() + interval '30 minutes' where id = cast(:i as uuid)"
            ),
            {"b": bid, "i": entry.entry_id},
        )
        await conn.execute(
            text(
                "insert into astro.notifications (user_id, kind, booking_id, payload, dedupe_key) values "
                "(cast(:u as uuid), 'waitlist_offer', :b, jsonb_build_object('booking_id', cast(:b as text)), "
                "'waitlist_offer:' || cast(:b as text)) on conflict do nothing"
            ),
            {"u": entry.user_id, "b": bid},
        )
        return str(bid)

    return _run(go)


@app.task(name="astro_worker.tasks.complete_past")
def complete_past() -> int:
    async def go(conn: AsyncConnection) -> int:
        r = await conn.execute(
            text(
                "update astro.bookings set status = 'completed' where status = 'confirmed' "
                "and upper(period) <= now() returning id"
            )
        )
        return len(r.all())

    return _run(go)


@app.task(name="astro_worker.tasks.retention")
def retention() -> dict[str, int]:
    async def go(conn: AsyncConnection) -> dict[str, int]:
        out = {}
        out["transcripts"] = (
            await conn.execute(
                text(
                    "update astro.call_turns set text = null, trace = null where created_at < now() - interval '30 days' "
                    "and (text is not null or trace is not null)"
                )
            )
        ).rowcount
        out["holds"] = (
            await conn.execute(
                text(
                    "delete from astro.bookings where status in ('expired') and updated_at < now() - interval '7 days'"
                )
            )
        ).rowcount
        return out

    return _run(go)
