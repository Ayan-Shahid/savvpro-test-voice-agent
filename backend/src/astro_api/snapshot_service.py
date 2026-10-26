"""Builds the brain's DataSnapshot for one turn from Postgres, as the calling user.

The sky and weather part changes rarely and is cached in-process by data version; the schedule and the
caller's own context are read fresh every turn (inside the same RLS transaction as the effects).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from astro_brain.catalogue import SITE
from astro_brain.snapshot import DataSnapshot, build_snapshot
from astro_brain.timeutil import night_of
from astro_brain.types import Busy, OwnBooking, Policy, UserContext, WeatherHour

POLICY_FIELDS = {f.name for f in dataclasses.fields(Policy)}


def to_min(dt: datetime) -> int:
    return int(dt.timestamp()) // 60


def to_dt(m: int) -> datetime:
    return datetime.fromtimestamp(m * 60, UTC)


VERSION_TTL_S = 5.0


class SnapshotService:
    """Caches the expensive, rarely-changing part of the snapshot (sky grids + weather) per process.

    Cache-stampede protection:
    - single flight: one coroutine rebuilds a missing version; the others wait on the same lock and then
      reuse its result instead of each parsing ~2 MB of sky data;
    - stale-while-revalidate: while a new version is being built, turns that already have an older base
      keep using it instead of queueing behind the rebuild;
    - the data-version probe itself is cached for a few seconds, so a burst of turns costs one query.
    """

    def __init__(self) -> None:
        self._base: dict[tuple[str, date], DataSnapshot] = {}
        self._lock = asyncio.Lock()
        self._version_cache: tuple[float, str] | None = None
        self.rebuilds = 0  # observable in tests

    async def _version(self, conn: AsyncConnection) -> str:
        now = time.monotonic()
        if self._version_cache and now - self._version_cache[0] < VERSION_TTL_S:
            return self._version_cache[1]
        row = (
            await conn.execute(
                text(
                    "select coalesce(max(computed_at)::text, 'none'), count(*), "
                    "(select coalesce(max(hour_start)::text, 'none') || count(*) from astro.weather_hours), "
                    "(select coalesce(max(updated_at)::text, '') from astro.policies) from astro.nights"
                )
            )
        ).one()
        version = hashlib.blake2b("|".join(map(str, row)).encode(), digest_size=6).hexdigest()
        self._version_cache = (now, version)
        return version

    async def _load_base(self, conn: AsyncConnection, today: date, policy: Policy) -> DataSnapshot:
        version = await self._version(conn)
        key = (version, today)
        if key in self._base:
            return self._base[key]
        stale = next(iter(self._base.values()), None)
        if stale is not None and stale.anchor == today and self._lock.locked():
            return stale  # someone is already rebuilding; serve the previous version meanwhile
        async with self._lock:
            if key in self._base:  # built while we waited
                return self._base[key]
            base = await self._build_base(conn, today, policy)
            self._base = {key: base}  # keep only the newest version
            self.rebuilds += 1
            return base

    async def _build_base(self, conn: AsyncConnection, today: date, policy: Policy) -> DataSnapshot:
        rows = (
            await conn.execute(
                text("select grid from astro.nights where night between :a and :b order by night"),
                {"a": today - timedelta(days=1), "b": today + timedelta(days=policy.horizon_nights + 1)},
            )
        ).scalars()
        nights = [r if isinstance(r, dict) else orjson.loads(r) for r in rows]
        wrows = (
            await conn.execute(
                text(
                    "select hour_start, cloud_pct, cloud_type::text, pwv_mm, rain_pct, wind_kmh "
                    "from astro.weather_hours where hour_start >= :a order by hour_start"
                ),
                {"a": datetime.combine(today - timedelta(days=1), datetime.min.time(), UTC)},
            )
        ).all()
        weather = [WeatherHour(to_min(r[0]), int(r[1]), r[2], float(r[3]), int(r[4]), int(r[5])) for r in wrows]
        return build_snapshot(nights, anchor=today, weather=weather, policy=policy)

    async def policy(self, conn: AsyncConnection) -> Policy:
        rows = (await conn.execute(text("select key, value from astro.policies"))).all()
        values = {k: v for k, v in rows if k in POLICY_FIELDS}
        return Policy(**values)

    async def build(self, conn: AsyncConnection, now_min: int) -> DataSnapshot:
        today = night_of(now_min, SITE.tz_offset_min)
        policy = await self.policy(conn)
        base = await self._load_base(conn, today, policy)
        busy = await self.busy(conn, now_min, policy)
        user = await self.user_context(conn, now_min)
        h = hashlib.blake2b(repr(busy).encode() + repr(user).encode(), digest_size=4).hexdigest()
        return dataclasses.replace(base, busy=busy, user=user, version=f"{base.version}-{h}", _trees={})

    async def busy(self, conn: AsyncConnection, now_min: int, policy: Policy) -> tuple[Busy, ...]:
        rows = (
            await conn.execute(
                text("select telescope, starts_at, ends_at, kind, booking_id, mine from astro.busy_intervals(:a, :b)"),
                {"a": to_dt(now_min - 24 * 60), "b": to_dt(now_min + (policy.horizon_nights + 2) * 24 * 60)},
            )
        ).all()
        return tuple(
            Busy(r[0], to_min(r[1]), to_min(r[2]), r[3], mine=bool(r[5]), booking_id=str(r[4]) if r[4] else None)
            for r in rows
        )

    async def user_context(self, conn: AsyncConnection, now_min: int) -> UserContext:
        prof = (
            await conn.execute(
                text(
                    "select p.id, p.display_name, s.time_format, a.grade::text, a.hours_granted, "
                    "coalesce((select sum(extract(epoch from upper(b.period) - lower(b.period)) / 60) "
                    "  from astro.bookings b join astro.semesters m on b.night between m.starts_on and m.ends_on "
                    "  where m.code = a.semester_code and b.kind = 'observation' "
                    "  and b.status in ('confirmed', 'completed')), 0) "
                    "from astro.profiles p join astro.user_settings s on s.user_id = p.id "
                    "left join astro.allocations a on a.user_id = p.id and a.semester_code = "
                    "  (select code from astro.semesters where current_date between starts_on and ends_on "
                    "   order by starts_on desc limit 1) "
                    "where p.id = astro.uid()"
                )
            )
        ).first()
        if prof is None:
            return UserContext()
        own_rows = (
            await conn.execute(
                text(
                    "select b.id, t.code, g.slug, lower(b.period), upper(b.period), b.night "
                    "from astro.bookings b join astro.telescopes t on t.id = b.telescope_id "
                    "join astro.targets g on g.id = b.target_id "
                    "where b.user_id = astro.uid() and b.status = 'confirmed' and upper(b.period) > :now "
                    "order by lower(b.period)"
                ),
                {"now": to_dt(now_min)},
            )
        ).all()
        own = tuple(OwnBooking(str(r[0]), r[1], r[2], to_min(r[3]), to_min(r[4]), r[5]) for r in own_rows)
        granted = float(prof[4] or 0) * 60
        used = float(prof[5] or 0)
        return UserContext(
            user_id=str(prof[0]),
            display_name=str(prof[1]).split(" ")[0],
            grade=prof[3] or "C",
            remaining_minutes=max(0, int(granted - used)),
            own=own,
            time_format="24h" if prof[2] == "24h" else "12h",
        )


def describe(snap: DataSnapshot) -> dict[str, Any]:
    """Small summary for the Brain Inspector."""
    return {
        "version": snap.version,
        "nights": len(snap.nights),
        "weather_hours": len(snap.weather),
        "busy": len(snap.busy),
        "remaining_minutes": snap.user.remaining_minutes,
        "grade": snap.user.grade,
    }
