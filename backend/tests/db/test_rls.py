"""Row-level security and booking integrity, exercised as anon, two users, the worker and an admin."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from astro_api.db import Database
from astro_brain.catalogue import TARGETS

from .conftest import claims, make_user, scalar

pytestmark = pytest.mark.db


def _grid(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


def _period(start: datetime, minutes: int) -> str:
    return f"[{start.isoformat()},{(start + timedelta(minutes=minutes)).isoformat()})"


async def _hold(db: Database, uid: str, tel: str, start: datetime, minutes: int = 60, expires_s: int = 120) -> str:
    async with db.as_user(claims(uid)) as conn:
        return str(
            (
                await conn.execute(
                    text(
                        "insert into astro.bookings (user_id, telescope_id, target_id, period, night, status, "
                        "hold_expires_at, source) select cast(:u as uuid), t.id, g.id, cast(cast(:p as text) as tstzrange), "
                        "cast(:n as date), 'held', now() + make_interval(secs => :x), 'voice' "
                        "from astro.telescopes t, astro.targets g where t.code = :tel and g.slug = 'saturn' "
                        "returning id"
                    ),
                    {"u": uid, "p": _period(start, minutes), "n": start.date(), "x": expires_s, "tel": tel},
                )
            ).scalar_one()
        )


FUTURE = _grid(datetime.now(UTC) + timedelta(days=2, hours=1))


async def test_profile_settings_and_allocation_created_by_trigger(seeded: dict[str, Any], api_db: Database) -> None:
    demo = seeded["people"]["demo"]
    assert await scalar(api_db, claims(demo), "select display_name from astro.profiles") == "Demo Observer"
    assert await scalar(api_db, claims(demo), "select time_format from astro.user_settings") == "12h"
    assert await scalar(api_db, claims(demo), "select grade::text from astro.allocations") == "B"


async def test_anon_reads_reference_but_not_personal_data(seeded: dict[str, Any], api_db: Database) -> None:
    assert await scalar(api_db, None, "select count(*) from astro.telescopes") == 3
    assert await scalar(api_db, None, "select count(*) from astro.targets") == len(TARGETS)
    with pytest.raises(ProgrammingError, match="permission denied"):
        await scalar(api_db, None, "select count(*) from astro.bookings")


async def test_users_see_only_their_own_rows(seeded: dict[str, Any], api_db: Database) -> None:
    p = seeded["people"]
    assert await scalar(api_db, claims(p["demo"]), "select count(*) from astro.bookings") == 1
    assert await scalar(api_db, claims(p["hana.brisk"]), "select count(*) from astro.bookings") == 3
    assert await scalar(api_db, claims(p["demo"]), "select count(*) from astro.profiles") == 1
    assert await scalar(api_db, claims(p["demo"]), "select count(*) from astro.waitlist_entries") == 0
    # Another user's booking cannot be cancelled: the policy hides it, so zero rows change.
    async with api_db.as_user(claims(p["juno.tarr"])) as conn:
        res = await conn.execute(text("update astro.bookings set status = 'cancelled'"))
        assert res.rowcount == 0


async def test_user_cannot_forge_rows_for_someone_else(seeded: dict[str, Any], api_db: Database) -> None:
    p = seeded["people"]
    with pytest.raises(DBAPIError, match="row-level security"):
        async with api_db.as_user(claims(p["demo"])) as conn:
            await conn.execute(
                text(
                    "insert into astro.bookings (user_id, telescope_id, target_id, period, night, status, "
                    "hold_expires_at, source) select cast(:u as uuid), t.id, g.id, cast(cast(:p as text) as tstzrange), "
                    "current_date, 'held', now() + interval '2 minutes', 'voice' from astro.telescopes t, "
                    "astro.targets g where t.code = 'OPT-25' and g.slug = 'm31'"
                ),
                {"u": p["juno.tarr"], "p": _period(FUTURE, 60)},
            )
    with pytest.raises(DBAPIError, match="row-level security"):
        async with api_db.as_user(claims(p["demo"])) as conn:
            await conn.execute(
                text(
                    "insert into astro.bookings (user_id, telescope_id, target_id, period, night, status, "
                    "source) select cast(:u as uuid), t.id, g.id, cast(cast(:p as text) as tstzrange), current_date, "
                    "'confirmed', 'admin' from astro.telescopes t, astro.targets g "
                    "where t.code = 'OPT-25' and g.slug = 'm31'"
                ),
                {"u": p["demo"], "p": _period(FUTURE, 60)},
            )


async def test_overlapping_holds_are_impossible(seeded: dict[str, Any], api_db: Database) -> None:
    p = seeded["people"]
    start = FUTURE + timedelta(hours=3)
    await _hold(api_db, p["ivo.penlow"], "DSR-100", start, 60)
    with pytest.raises(DBAPIError) as exc:
        await _hold(api_db, p["kit.wrenfield"], "DSR-100", start + timedelta(minutes=30), 60)
    assert "no_telescope_overlap" in str(exc.value)
    # Touching is not overlapping: [start+60, start+120) starts exactly where the first ends.
    await _hold(api_db, p["kit.wrenfield"], "DSR-100", start + timedelta(minutes=60), 60)
    # One person cannot hold two telescopes at once.
    with pytest.raises(DBAPIError) as exc2:
        await _hold(api_db, p["ivo.penlow"], "OPT-25", start + timedelta(minutes=15), 30)
    assert "no_person_overlap" in str(exc2.value)


async def test_confirm_rules_expiry_allocation_and_transitions(seeded: dict[str, Any], api_db: Database) -> None:
    p = seeded["people"]
    start = FUTURE + timedelta(hours=8)
    lapsed = await _hold(api_db, p["gil.sorrel"], "SMM-12", start, 60, expires_s=-1)
    with pytest.raises(DBAPIError, match=r"AV001|expired"):
        async with api_db.as_user(claims(p["gil.sorrel"])) as conn:
            await conn.execute(text("update astro.bookings set status = 'confirmed' where id = :i"), {"i": lapsed})
    # The lapsed hold no longer blocks once expire_lapsed_holds has run for that slot.
    async with api_db.as_user(claims(p["juno.tarr"])) as conn:
        n = await conn.execute(
            text(
                "select astro.expire_lapsed_holds(t.id, cast(cast(:p as text) as tstzrange)) from astro.telescopes t "
                "where t.code = 'SMM-12'"
            ),
            {"p": _period(start, 60)},
        )
        assert n.scalar_one() == 1
    juno = await _hold(api_db, p["juno.tarr"], "SMM-12", start, 60)
    async with api_db.as_user(claims(p["juno.tarr"])) as conn:
        await conn.execute(text("update astro.bookings set status = 'confirmed' where id = :i"), {"i": juno})
    with pytest.raises(DBAPIError, match=r"AV003|illegal"):
        async with api_db.as_user(claims(p["juno.tarr"])) as conn:
            await conn.execute(text("update astro.bookings set status = 'held' where id = :i"), {"i": juno})
    # Hana (grade C, 10 h) already used 7.5 h: a 3 h confirmation must fail at the database.
    hana = await _hold(api_db, p["hana.brisk"], "OPT-25", start + timedelta(days=1), 180)
    with pytest.raises(DBAPIError, match=r"AV002|allocation"):
        async with api_db.as_user(claims(p["hana.brisk"])) as conn:
            await conn.execute(text("update astro.bookings set status = 'confirmed' where id = :i"), {"i": hana})


async def test_busy_intervals_hide_who_and_what(seeded: dict[str, Any], api_db: Database) -> None:
    p = seeded["people"]
    async with api_db.as_user(claims(p["demo"])) as conn:
        rows = (
            await conn.execute(
                text(
                    "select telescope, kind, booking_id, mine from astro.busy_intervals(now() - interval '1 day', "
                    "now() + interval '30 days')"
                )
            )
        ).all()
    assert any(r.mine and r.booking_id for r in rows)  # the demo's own EC-49 booking
    assert all(r.booking_id is None for r in rows if not r.mine)
    assert {r.telescope for r in rows} >= {"DSR-100", "SMM-12", "OPT-25"}


async def test_worker_can_expire_and_users_cannot(
    seeded: dict[str, Any], api_db: Database, worker_db: Database
) -> None:
    p = seeded["people"]
    start = FUTURE + timedelta(days=3)
    hid = await _hold(api_db, p["fen.okoro"], "OPT-25", start, 30)
    with pytest.raises(DBAPIError, match=r"AV003|illegal"):
        async with api_db.as_user(claims(p["fen.okoro"])) as conn:
            await conn.execute(text("update astro.bookings set status = 'expired' where id = :i"), {"i": hid})
    async with worker_db.as_service() as conn:
        r = await conn.execute(text("update astro.bookings set status = 'expired' where id = :i"), {"i": hid})
        assert r.rowcount == 1


async def test_waitlist_position_and_admin(seeded: dict[str, Any], api_db: Database, pg: dict[str, Any]) -> None:
    p = seeded["people"]
    async with api_db.as_user(claims(p["esme.harrow"])) as conn:
        eid = (await conn.execute(text("select id from astro.waitlist_entries"))).scalar_one()
        # Grade A beats grade B even though B joined first.
        assert (await conn.execute(text("select astro.waitlist_position(:e)"), {"e": eid})).scalar_one() == 2
    admin = make_user(pg, "staff@astro.example", "Staff Member", '{"role": "admin"}')
    assert await scalar(api_db, claims(admin, admin=True), "select count(*) from astro.bookings") >= 6
    # app_metadata says admin but without MFA (aal1) there is no admin power.
    weak = claims(admin)
    weak["app_metadata"] = {"role": "admin"}
    assert await scalar(api_db, weak, "select count(*) from astro.bookings") == 0
    # user_metadata is user-editable and never grants anything.
    forged = claims(p["demo"])
    forged["user_metadata"] = {"role": "admin"}
    assert await scalar(api_db, forged, "select count(*) from astro.bookings") == 1


async def test_no_identity_leaks_between_pooled_transactions(seeded: dict[str, Any], api_db: Database) -> None:
    p = seeded["people"]
    for _ in range(5):
        assert await scalar(api_db, claims(p["demo"]), "select count(*) from astro.bookings") == 1
        assert await scalar(api_db, None, "select current_setting('request.jwt.claims', true)") in ("", None)
        assert await scalar(api_db, None, "select current_user") == "anon"


async def test_oauth_users_get_their_provider_name(
    seeded: dict[str, Any], api_db: Database, pg: dict[str, Any]
) -> None:
    import uuid as _uuid

    from .conftest import _psql

    uid = str(_uuid.uuid4())
    _psql(
        pg["super"],
        "-c",
        f"insert into auth.users (id, email, raw_user_meta_data) values ('{uid}', "
        f'\'g{uid[:6]}@astro.example\', \'{{"full_name": "Grace Example", "iss": "https://accounts.google.com"}}\')',
    )
    assert await scalar(api_db, claims(uid), "select display_name from astro.profiles") == "Grace Example"
