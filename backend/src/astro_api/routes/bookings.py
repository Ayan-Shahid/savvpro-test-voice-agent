"""Bookings, waitlist, catalogue and the schedule view."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Query, Response
from sqlalchemy import text

from astro_brain.catalogue import SITE, TARGETS, TELESCOPES
from astro_brain.timeutil import night_of

from ..auth import CurrentUser
from ..call_service import now_minute
from ..deps import Svc
from ..errors import ApiError
from ..snapshot_service import to_dt

router = APIRouter(prefix="/api", tags=["bookings"])


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


@router.get("/catalogue")
async def catalogue() -> dict[str, Any]:
    """Public reference data straight from the brain's catalogue (the same data the seed writes)."""
    return {
        "site": {
            "name": SITE.name,
            "lat": SITE.lat_deg,
            "lon": SITE.lon_deg,
            "elevation_m": SITE.elevation_m,
            "utc_offset_min": SITE.tz_offset_min,
        },
        "telescopes": [
            {
                "code": t.code,
                "name": t.name,
                "kind": t.kind,
                "min_altitude_deg": t.min_altitude_deg,
                "max_cloud_pct": t.max_cloud_pct,
                "max_pwv_mm": t.max_pwv_mm,
                "allowed": sorted(t.allowed_kinds),
            }
            for t in sorted(TELESCOPES.values(), key=lambda t: t.display_order)
        ],
        "targets": [
            {"slug": t.slug, "name": t.name, "kind": t.kind, "magnitude": t.magnitude, "ids": list(t.catalogue_ids)}
            for t in TARGETS.values()
        ],
    }


@router.get("/bookings")
async def my_bookings(
    user: CurrentUser, svc: Svc, scope: str = Query("upcoming", pattern="^(upcoming|past|all)$")
) -> dict[str, Any]:
    cond = {
        "upcoming": "upper(b.period) > now() and b.status in ('confirmed', 'held')",
        "past": "upper(b.period) <= now() or b.status in ('cancelled', 'expired', 'completed')",
        "all": "true",
    }[scope]
    async with svc.db.as_user(user.claims) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "select b.id, b.status::text as status, b.source::text as source, lower(b.period) as start, "
                        "upper(b.period) as end, b.night, t.code as telescope, t.name as telescope_name, "
                        "g.slug as target, g.name as target_name, b.weather_pending, b.created_at "
                        "from astro.bookings b join astro.telescopes t on t.id = b.telescope_id "
                        "left join astro.targets g on g.id = b.target_id "
                        f"where b.user_id = astro.uid() and ({cond}) order by lower(b.period) "
                        + ("desc " if scope == "past" else "")
                        + "limit 100"
                    )
                )
            )
            .mappings()
            .all()
        )
    return {"bookings": [{k: (str(v) if k == "id" else _iso(v)) for k, v in r.items()} for r in rows]}


@router.post("/bookings/{booking_id}/cancel")
async def cancel(booking_id: str, user: CurrentUser, svc: Svc) -> dict[str, Any]:
    async with svc.db.as_user(user.claims) as conn:
        row = (
            await conn.execute(
                text(
                    "update astro.bookings set status = 'cancelled', cancel_reason = 'cancelled on web' "
                    "where id = cast(:i as uuid) and user_id = astro.uid() and status in ('confirmed', 'held') "
                    "returning id"
                ),
                {"i": booking_id},
            )
        ).first()
        if row is None:
            raise ApiError(404, "not_found", "That booking was not found or can no longer be cancelled.")
        await conn.execute(
            text(
                "insert into astro.notifications (user_id, kind, booking_id, payload, dedupe_key) values "
                "(astro.uid(), 'booking_cancelled', cast(:i as uuid), jsonb_strip_nulls(jsonb_build_object('booking_id', :i, 'email', (select astro.jwt()) ->> 'email')), "
                "'booking_cancelled:' || :i) on conflict do nothing"
            ),
            {"i": booking_id},
        )
        await conn.execute(text("select astro.audit('cancel', 'booking', :i)"), {"i": booking_id})
    _enqueue_promotion(booking_id)
    return {"ok": True}


def _enqueue_promotion(booking_id: str) -> None:
    try:
        from astro_worker.tasks import promote_waitlist

        promote_waitlist.delay(booking_id)
    except Exception:  # the worker's periodic sweep promotes anyway
        return


@router.get("/waitlist")
async def my_waitlist(user: CurrentUser, svc: Svc) -> dict[str, Any]:
    async with svc.db.as_user(user.claims) as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "select w.id, w.status::text as status, w.night, lower(w.wanted) as start, "
                        "upper(w.wanted) as end, "
                        "t.code as telescope, g.name as target_name, w.grade::text as grade, w.joined_at, "
                        "astro.waitlist_position(w.id) as position "
                        "from astro.waitlist_entries w join astro.telescopes t on t.id = w.telescope_id "
                        "join astro.targets g on g.id = w.target_id where w.status in ('waiting', 'offered') "
                        "order by w.night"
                    )
                )
            )
            .mappings()
            .all()
        )
    return {"entries": [{k: (str(v) if k == "id" else _iso(v)) for k, v in r.items()} for r in rows]}


@router.delete("/waitlist/{entry_id}", status_code=204)
async def leave_waitlist(entry_id: str, user: CurrentUser, svc: Svc) -> Response:
    async with svc.db.as_user(user.claims) as conn:
        r = await conn.execute(
            text(
                "update astro.waitlist_entries set status = 'cancelled' where id = cast(:i as uuid) "
                "and status = 'waiting'"
            ),
            {"i": entry_id},
        )
    if r.rowcount == 0:
        raise ApiError(404, "not_found", "That waitlist entry was not found.")
    return Response(status_code=204)


@router.get("/schedule")
async def schedule(user: CurrentUser, svc: Svc, night: date | None = None) -> dict[str, Any]:
    """Busy intervals for one night (others' rows anonymised by astro.busy_intervals) plus sky facts."""
    today = night_of(now_minute(svc.settings), SITE.tz_offset_min)
    n = night or today
    if not (today - timedelta(days=1) <= n <= today + timedelta(days=31)):
        raise ApiError(400, "out_of_range", "Pick a night within the next 30 days.")
    from astro_brain.timeutil import night_start

    start = night_start(n, SITE.tz_offset_min)
    async with svc.db.as_user(user.claims) as conn:
        busy = (
            (
                await conn.execute(
                    text("select * from astro.busy_intervals(:a, :b)"),
                    {"a": to_dt(start), "b": to_dt(start + 24 * 60)},
                )
            )
            .mappings()
            .all()
        )
        sky = (
            (
                await conn.execute(
                    text(
                        "select sunset, astro_dusk, astro_dawn, sunrise, moon_illum_pct from astro.nights "
                        "where night = :n"
                    ),
                    {"n": n},
                )
            )
            .mappings()
            .first()
        )
        weather = (
            (
                await conn.execute(
                    text(
                        "select hour_start, cloud_pct, cloud_type::text as cloud_type, pwv_mm, rain_pct, wind_kmh "
                        "from astro.weather_hours where hour_start >= :a and hour_start < :b order by hour_start"
                    ),
                    {"a": to_dt(start), "b": to_dt(start + 24 * 60)},
                )
            )
            .mappings()
            .all()
        )
    return {
        "night": n.isoformat(),
        "busy": [{k: (str(v) if k == "booking_id" and v else _iso(v)) for k, v in r.items()} for r in busy],
        "sky": None if sky is None else {k: (float(v) if k == "moon_illum_pct" else _iso(v)) for k, v in sky.items()},
        "weather": [{k: (float(v) if k == "pwv_mm" else _iso(v)) for k, v in r.items()} for r in weather],
    }
