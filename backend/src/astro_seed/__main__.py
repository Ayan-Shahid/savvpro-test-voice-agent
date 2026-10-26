"""python -m astro_seed [--anchor YYYY-MM-DD] [--nights 60] [--catalogue-only] [--reset]

Builds the demo world around an anchor date A (default: today at the site). People are fake (fixed,
obviously fictional names; emails on the reserved `astro.example` domain), targets and sky are real,
weather is simulated with seeded scenario pins. Safe to run twice.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import jwt
import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from astro_api.settings import Settings, get_settings
from astro_brain.catalogue import SITE, TARGET_ALIASES, TARGETS, TELESCOPES
from astro_brain.timeutil import at_night, night_of
from astro_brain.types import Policy
from astro_brain.weather import seed_for, simulate
from astro_sky import derive

ALL_KINDS = (
    "moon",
    "planet",
    "star",
    "double_star",
    "star_cluster",
    "galaxy",
    "nebula",
    "supernova_remnant",
    "radio_source",
    "molecular_cloud",
)
HOURS = {"A": 30, "B": 20, "C": 10}
# Twelve fictional people. Deliberately invented names; nothing here refers to a real person.
PEOPLE: tuple[tuple[str, str, str], ...] = (
    ("demo", "Demo Observer", "B"),
    ("ada.quill", "Ada Quill", "A"),
    ("bram.tolver", "Bram Tolver", "A"),
    ("cyra.vale", "Cyra Vale", "A"),
    ("dov.marrow", "Dov Marrow", "B"),
    ("esme.harrow", "Esme Harrow", "B"),
    ("fen.okoro", "Fen Okafor-Lune", "B"),
    ("gil.sorrel", "Gil Sorrel", "B"),
    ("hana.brisk", "Hana Brisk", "C"),
    ("ivo.penlow", "Ivo Penlow", "C"),
    ("juno.tarr", "Juno Tarr", "C"),
    ("kit.wrenfield", "Kit Wrenfield", "C"),
)
EMAIL_DOMAIN = "astro.example"

CreateUser = Callable[[str, str, str, str], Awaitable[str]]  # (email, password, name, grade) -> user id


def _ts(minute: int) -> datetime:
    return datetime.fromtimestamp(minute * 60, UTC)


def _range(a: int, b: int) -> str:
    return f"[{_ts(a).isoformat()},{_ts(b).isoformat()})"


def site_today(offset_min: int = SITE.tz_offset_min) -> date:
    return night_of(int(datetime.now(UTC).timestamp()) // 60, offset_min)


def semester_for(d: date) -> tuple[str, date, date]:
    if d.month >= 8:
        return f"{d.year}B", date(d.year, 8, 1), date(d.year + 1, 1, 31)
    if d.month == 1:
        return f"{d.year - 1}B", date(d.year - 1, 8, 1), date(d.year, 1, 31)
    return f"{d.year}A", date(d.year, 2, 1), date(d.year, 7, 31)


# ---- GoTrue admin API -------------------------------------------------------------------------------
class GoTrueAdmin:
    """Creates users through GoTrue so real auth.users rows (and our profile trigger) exist."""

    def __init__(self, settings: Settings) -> None:
        token = jwt.encode(
            {"role": "service_role", "iss": settings.jwt_issuer, "exp": datetime.now(UTC) + timedelta(minutes=10)},
            settings.jwt_secret.get_secret_value(),
            algorithm="HS256",
        )
        self.http = httpx.AsyncClient(
            base_url=settings.gotrue_url, headers={"Authorization": f"Bearer {token}"}, timeout=15
        )

    async def create(self, email: str, password: str, name: str, grade: str) -> str:
        r = await self.http.post(
            "/admin/users",
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"display_name": name},
                "app_metadata": {"seed": True},
            },
        )
        if r.status_code in (200, 201):
            return str(r.json()["id"])
        if r.status_code == 422:  # already registered: find it
            page = 1
            while True:
                lst = await self.http.get("/admin/users", params={"page": page, "per_page": 100})
                lst.raise_for_status()
                users = lst.json().get("users", [])
                for u in users:
                    if u.get("email") == email:
                        return str(u["id"])
                if len(users) < 100:
                    break
                page += 1
        r.raise_for_status()
        raise RuntimeError(f"could not create {email}")

    async def aclose(self) -> None:
        await self.http.aclose()


# ---- seeding ---------------------------------------------------------------------------------------------
async def seed_observatory(conn: AsyncConnection, anchor: date) -> dict[str, int]:
    s = SITE
    await conn.execute(
        text(
            "insert into astro.sites (slug, name, lat_deg, lon_deg, elevation_m, tz) values "
            "(:slug, :name, :lat, :lon, :el, :tz) on conflict (slug) do update set name = excluded.name"
        ),
        {"slug": s.slug, "name": s.name, "lat": s.lat_deg, "lon": s.lon_deg, "el": s.elevation_m, "tz": s.tz_name},
    )
    site_id = (await conn.execute(text("select id from astro.sites where slug = :s"), {"s": s.slug})).scalar_one()
    for t in TELESCOPES.values():
        await conn.execute(
            text(
                "insert into astro.telescopes (site_id, code, name, kind, min_altitude_deg, sun_max_alt_bright, "
                "sun_max_alt_faint, sun_avoid_deg, moon_avoid_deg, max_cloud_pct, max_pwv_mm, max_rain_pct, "
                "max_wind_kmh, min_minutes, max_minutes, display_order) values (:site, :code, :name, "
                "cast(:kind as astro.telescope_kind), :minalt, :b, :f, :sun, :moon, :cloud, :pwv, :rain, :wind, "
                ":mn, :mx, :ord) on conflict (code) do update set name = excluded.name, "
                "min_altitude_deg = excluded.min_altitude_deg, max_cloud_pct = excluded.max_cloud_pct, "
                "max_pwv_mm = excluded.max_pwv_mm, max_rain_pct = excluded.max_rain_pct, "
                "max_wind_kmh = excluded.max_wind_kmh"
            ),
            {
                "site": site_id,
                "code": t.code,
                "name": t.name,
                "kind": t.kind,
                "minalt": t.min_altitude_deg,
                "b": t.sun_max_alt_bright,
                "f": t.sun_max_alt_faint,
                "sun": t.sun_avoid_deg,
                "moon": t.moon_avoid_deg,
                "cloud": t.max_cloud_pct,
                "pwv": t.max_pwv_mm,
                "rain": t.max_rain_pct,
                "wind": t.max_wind_kmh,
                "mn": t.min_minutes,
                "mx": t.max_minutes,
                "ord": t.display_order,
            },
        )
    tel_ids = {r[1]: r[0] for r in (await conn.execute(text("select id, code from astro.telescopes"))).all()}
    for code, t in TELESCOPES.items():
        for kind in ALL_KINDS:
            mag = t.max_magnitude_extended if kind in ("star_cluster", "galaxy", "nebula") else None
            await conn.execute(
                text(
                    "insert into astro.telescope_capabilities values (:t, cast(:k as astro.target_kind), :a, :m) "
                    "on conflict (telescope_id, target_kind) do update set allowed = excluded.allowed, "
                    "max_magnitude = excluded.max_magnitude"
                ),
                {"t": tel_ids[code], "k": kind, "a": kind in t.allowed_kinds, "m": mag},
            )
    for key, value in asdict(Policy()).items():
        await conn.execute(
            text(
                "insert into astro.policies (key, value) values (:k, cast(:v as jsonb)) "
                "on conflict (key) do update set value = excluded.value"
            ),
            {"k": key, "v": orjson.dumps(value).decode()},
        )
    code, starts, ends = semester_for(anchor)
    await conn.execute(
        text("insert into astro.semesters values (:c, :s, :e) on conflict (code) do nothing"),
        {"c": code, "s": starts, "e": ends},
    )
    return {"site": site_id, **tel_ids}


async def seed_catalogue(conn: AsyncConnection) -> dict[str, str]:
    for t in TARGETS.values():
        await conn.execute(
            text(
                "insert into astro.targets (slug, name, kind, ephemeris_body, ra_hours, dec_deg, magnitude, "
                "catalogue_ids) values (:slug, :name, cast(:kind as astro.target_kind), :body, :ra, :dec, :mag, "
                ":ids) on conflict (slug) do update set name = excluded.name, magnitude = excluded.magnitude"
            ),
            {
                "slug": t.slug,
                "name": t.name,
                "kind": t.kind,
                "body": t.ephemeris_body,
                "ra": t.ra_hours,
                "dec": t.dec_deg,
                "mag": t.magnitude,
                "ids": list(t.catalogue_ids),
            },
        )
    ids = {r[1]: str(r[0]) for r in (await conn.execute(text("select id, slug from astro.targets"))).all()}
    for alias, (slug, kind) in TARGET_ALIASES.items():
        await conn.execute(
            text(
                "insert into astro.target_aliases values (:a, cast(:t as uuid), cast(:k as astro.alias_kind)) "
                "on conflict (alias) do update set target_id = excluded.target_id"
            ),
            {"a": alias.lower(), "t": ids[slug], "k": kind},
        )
    return ids


async def seed_sky(conn: AsyncConnection, site_id: int, target_ids: dict[str, str], first: date, count: int) -> int:
    from astro_sky.compute import SkyCalculator  # needs the `sky` extra

    calc = SkyCalculator()
    for night in calc.nights(first, count):
        tw = derive.twilight(night)
        await conn.execute(
            text(
                "insert into astro.nights (site_id, night, sunset, nautical_dusk, astro_dusk, astro_dawn, "
                "nautical_dawn, sunrise, moon_illum_pct, grid) values (:s, :n, :a, :b, :c, :d, :e, :f, :m, "
                "cast(:g as jsonb)) on conflict (site_id, night) do update set grid = excluded.grid, "
                "moon_illum_pct = excluded.moon_illum_pct, computed_at = now()"
            ),
            {
                "s": site_id,
                "n": date.fromisoformat(night["night"]),
                **{
                    k: _ts(tw[v])
                    for k, v in zip(
                        "abcdef",
                        ("sunset", "nautical_dusk", "astro_dusk", "astro_dawn", "nautical_dawn", "sunrise"),
                        strict=True,
                    )
                },
                "m": night["moon_illum_pct"],
                "g": orjson.dumps(night).decode(),
            },
        )
        for slug in TARGETS:
            for min_alt in (15, 20):
                wins = derive.windows(night, slug, min_alt)
                tt, talt = derive.transit(night, slug)
                multirange = "{" + ",".join(_range(a, b) for a, b in wins) + "}"
                await conn.execute(
                    text(
                        "insert into astro.visibility_windows values (cast(:t as uuid), :n, :m, "
                        "cast(cast(:w as text) as tstzmultirange), :tt, :ta, :ss) "
                        "on conflict (target_id, night, min_alt_deg) "
                        "do update set windows = excluded.windows"
                    ),
                    {
                        "t": target_ids[slug],
                        "n": date.fromisoformat(night["night"]),
                        "m": min_alt,
                        "w": multirange,
                        "tt": _ts(tt),
                        "ta": talt,
                        "ss": min(night["targets"][slug]["sun_sep"]) / 10,
                    },
                )
    return count


async def seed_weather(conn: AsyncConnection, site_id: int, anchor: date, nights: int = 8) -> int:
    first = anchor - timedelta(days=1)
    hours = simulate(SITE.slug, first, nights, SITE.tz_offset_min, anchor)
    for h in hours:
        n = night_of(h.start, SITE.tz_offset_min)
        offset = (n - anchor).days
        await conn.execute(
            text(
                "insert into astro.weather_hours values (:s, :h, :c, cast(:t as astro.cloud_type), :p, :r, :w, "
                ":seed, :sc) on conflict (site_id, hour_start) do update set cloud_pct = excluded.cloud_pct, "
                "cloud_type = excluded.cloud_type, pwv_mm = excluded.pwv_mm, rain_pct = excluded.rain_pct, "
                "wind_kmh = excluded.wind_kmh, scenario = excluded.scenario"
            ),
            {
                "s": site_id,
                "h": _ts(h.start),
                "c": h.cloud_pct,
                "t": h.cloud_type,
                "p": h.pwv_mm,
                "r": h.rain_pct,
                "w": h.wind_kmh,
                "seed": seed_for(SITE.slug, n) & 0x7FFFFFFFFFFFFFFF,
                "sc": f"A+{offset}" if offset in (0, 1, 2, 3, 5) else None,
            },
        )
    return len(hours)


async def seed_people(conn: AsyncConnection, create: CreateUser, anchor: date, demo_password: str) -> dict[str, str]:
    code = semester_for(anchor)[0]
    ids: dict[str, str] = {}
    for handle, name, grade in PEOPLE:
        password = demo_password if handle == "demo" else secrets.token_urlsafe(18)
        uid = await create(f"{handle}@{EMAIL_DOMAIN}", password, name, grade)
        ids[handle] = uid
        await conn.execute(
            text(
                "insert into astro.allocations (user_id, semester_code, grade, hours_granted) values "
                "(cast(:u as uuid), :c, cast(:g as astro.allocation_grade), :h) on conflict (user_id, semester_code) "
                "do update set grade = excluded.grade, hours_granted = excluded.hours_granted"
            ),
            {"u": uid, "c": code, "g": grade, "h": HOURS[grade]},
        )
    return ids


async def plant_schedule(
    conn: AsyncConnection, anchor: date, tel: dict[str, int], targets: dict[str, str], people: dict[str, str]
) -> int:
    off = SITE.tz_offset_min
    await conn.execute(
        text("delete from astro.waitlist_entries where user_id in (cast(:a as uuid), cast(:b as uuid))"),
        {"a": people["esme.harrow"], "b": people["bram.tolver"]},
    )
    await conn.execute(text("delete from astro.bookings where idempotency_key like 'seed:%'"))

    def at(d: int, h: int, m: int = 0) -> int:
        return at_night(anchor + timedelta(days=d), h, m, off)

    rows: list[dict[str, Any]] = [
        # EC-41: the reflector is taken tonight 23:00-01:00 by a grade B user.
        {
            "key": "seed:ec41",
            "user": people["dov.marrow"],
            "tel": "DSR-100",
            "target": "m42",
            "s": at(0, 23),
            "e": at(0, 1),
            "night": anchor,
            "status": "confirmed",
            "kind": "observation",
        },
        # Half-open boundary with EC-13: the dish 02:00-04:00 (early tomorrow morning, tonight's night).
        {
            "key": "seed:dish-0200",
            "user": people["ada.quill"],
            "tel": "SMM-12",
            "target": "sgr-a",
            "s": at(0, 2),
            "e": at(0, 4),
            "night": anchor,
            "status": "confirmed",
            "kind": "observation",
        },
        # EC-49: the demo account's own dish booking to cancel by voice.
        {
            "key": "seed:ec49",
            "user": people["demo"],
            "tel": "SMM-12",
            "target": "cyg-a",
            "s": at(3, 19),
            "e": at(3, 20),
            "night": anchor + timedelta(days=3),
            "status": "confirmed",
            "kind": "observation",
        },
        # EC-44: refractor maintenance for a whole night.
        {
            "key": "seed:ec44",
            "user": None,
            "tel": "OPT-25",
            "target": None,
            "s": at(6, 12),
            "e": at(7, 12),
            "night": anchor + timedelta(days=6),
            "status": "confirmed",
            "kind": "maintenance",
        },
    ]
    # EC-45: a grade C user with 7.5 h already used this semester (three completed 2.5 h sessions).
    for i, d in enumerate((-12, -9, -6)):
        rows.append(
            {
                "key": f"seed:ec45-{i}",
                "user": people["hana.brisk"],
                "tel": "DSR-100",
                "target": "m31",
                "s": at(d, 21),
                "e": at(d, 21) + 150,
                "night": anchor + timedelta(days=d),
                "status": "completed",
                "kind": "observation",
            }
        )
    for r in rows:
        await conn.execute(
            text(
                "insert into astro.bookings (kind, user_id, telescope_id, target_id, period, night, status, source, "
                "idempotency_key, confirmed_at) values (cast(:kind as astro.booking_kind), cast(:user as uuid), :tel, "
                "cast(:target as uuid), cast(cast(:period as text) as tstzrange), :night, "
                "cast(:status as astro.booking_status), "
                "cast(:source as astro.booking_source), :key, now())"
            ),
            {
                "kind": r["kind"],
                "user": r["user"],
                "tel": tel[r["tel"]],
                "target": targets[r["target"]] if r["target"] else None,
                "period": _range(r["s"], r["e"]),
                "night": r["night"],
                "status": r["status"],
                "source": "admin" if r["kind"] == "maintenance" else "web",
                "key": r["key"],
            },
        )
    # EC-50: two people waiting for the reflector slot; the grade B user joined first, then a grade A user.
    for handle, grade, joined_min in (("esme.harrow", "B", 90), ("bram.tolver", "A", 30)):
        await conn.execute(
            text(
                "insert into astro.waitlist_entries (user_id, telescope_id, target_id, night, wanted, grade, "
                "joined_at) values (cast(:u as uuid), :t, cast(:tg as uuid), :n, cast(cast(:w as text) as tstzrange), "
                "cast(:g as astro.allocation_grade), now() - make_interval(mins => :j)) "
                "on conflict do nothing"
            ),
            {
                "u": people[handle],
                "t": tel["DSR-100"],
                "tg": targets["m42"],
                "n": anchor,
                "w": _range(at(0, 23), at(0, 1)),
                "g": grade,
                "j": joined_min,
            },
        )
    return len(rows)


async def run(
    settings: Settings,
    *,
    anchor: date,
    nights: int,
    catalogue_only: bool,
    reset: bool,
    create_user: CreateUser | None = None,
    dsn: str | None = None,
) -> dict[str, Any]:
    if reset and settings.env == "production":
        raise SystemExit("--reset refuses to run in production")
    engine = create_async_engine(dsn or settings.migration_dsn)
    admin: GoTrueAdmin | None = None
    report: dict[str, Any] = {"anchor": anchor.isoformat()}
    try:
        async with engine.begin() as conn:
            await conn.execute(text("set local search_path = astro, public"))
            if reset:
                await conn.execute(
                    text(
                        "truncate astro.waitlist_entries, astro.bookings, astro.call_turns, astro.calls, "
                        "astro.notifications, astro.reports cascade"
                    )
                )
            ids = await seed_observatory(conn, anchor)
            targets = await seed_catalogue(conn)
            report["targets"] = len(targets)
            report["nights"] = await seed_sky(conn, ids["site"], targets, anchor - timedelta(days=1), nights)
            report["weather_hours"] = await seed_weather(conn, ids["site"], anchor)
            if not catalogue_only:
                if create_user is None:
                    admin = GoTrueAdmin(settings)
                    create_user = admin.create
                demo_pw = os.environ.get("ASTRO_SEED_DEMO_PASSWORD") or _read_secret("seed_demo_password")
                if not demo_pw:
                    demo_pw = secrets.token_urlsafe(18)
                    report["demo_password"] = demo_pw if settings.env == "development" else "(random, not shown)"
                people = await seed_people(conn, create_user, anchor, demo_pw)
                report["people"] = len(people)
                report["bookings"] = await plant_schedule(conn, anchor, ids, targets, people)
    finally:
        if admin:
            await admin.aclose()
        await engine.dispose()
    return report


def _read_secret(name: str) -> str:
    try:
        with open(f"/run/secrets/{name}") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="astro_seed")
    p.add_argument("--anchor", type=date.fromisoformat, default=None)
    p.add_argument("--nights", type=int, default=60)
    p.add_argument("--catalogue-only", action="store_true")
    p.add_argument("--reset", action="store_true")
    a = p.parse_args(argv)
    s = get_settings()
    anchor = a.anchor or s.anchor_date or site_today()
    catalogue_only = a.catalogue_only or s.env == "production"
    report = asyncio.run(run(s, anchor=anchor, nights=a.nights, catalogue_only=catalogue_only, reset=a.reset))
    sys.stdout.write(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode() + "\n")


if __name__ == "__main__":
    main()
