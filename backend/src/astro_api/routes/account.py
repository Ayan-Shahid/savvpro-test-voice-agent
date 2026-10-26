"""The signed-in person: profile, settings, sessions and account deletion. All queries run under RLS."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
import jwt
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from ..auth import CurrentUser
from ..deps import Svc
from ..errors import ApiError

router = APIRouter(prefix="/api/me", tags=["account"])
VOICES = ("flux-haley-en", "aura-2-thalia-en", "aura-2-orion-en", "aura-2-andromeda-en")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProfileIn(Strict):
    display_name: str = Field(min_length=1, max_length=80)
    affiliation: str | None = Field(default=None, max_length=120)

    @field_validator("display_name", "affiliation")
    @classmethod
    def _printable(cls, v: str | None) -> str | None:
        if v is not None and any(ord(c) < 32 for c in v):
            raise ValueError("must not contain control characters")
        return v


class SettingsIn(Strict):
    voice: Literal[VOICES] | None = None  # type: ignore[valid-type]
    speech_rate: float | None = Field(default=None, ge=0.85, le=1.15)
    input_mode: Literal["hands_free", "push_to_talk"] | None = None
    barge_in: bool | None = None
    captions: bool | None = None
    reduced_motion: Literal["system", "on", "off"] | None = None
    high_contrast: bool | None = None
    time_format: Literal["12h", "24h"] | None = None
    date_order: Literal["DMY", "MDY"] | None = None
    email_confirmations: bool | None = None
    keep_transcripts: bool | None = None


SETTINGS_COLS = (
    "voice, speech_rate, input_mode, barge_in, captions, reduced_motion, high_contrast, time_format, "
    "date_order, email_confirmations, keep_transcripts"
)


@router.get("")
async def me(user: CurrentUser, svc: Svc) -> dict[str, object]:
    async with svc.db.as_user(user.claims) as conn:
        prof = (
            (await conn.execute(text("select id, display_name, affiliation, created_at from astro.profiles")))
            .mappings()
            .first()
        )
        if prof is None:
            raise ApiError(404, "no_profile", "Your profile is still being created. Try again in a moment.")
        settings = (await conn.execute(text(f"select {SETTINGS_COLS} from astro.user_settings"))).mappings().one()
        alloc = (
            (
                await conn.execute(
                    text(
                        "select a.semester_code, a.grade::text as grade, a.hours_granted, "
                        "coalesce((select sum(extract(epoch from upper(b.period) - lower(b.period)) / 3600) "
                        " from astro.bookings b join astro.semesters s on s.code = a.semester_code "
                        " where b.night between s.starts_on and s.ends_on and b.kind = 'observation' "
                        " and b.status in ('confirmed', 'completed')), 0) as hours_used "
                        "from astro.allocations a order by a.semester_code desc limit 1"
                    )
                )
            )
            .mappings()
            .first()
        )
    return {
        "id": str(prof["id"]),
        "email": user.claims.get("email"),
        "display_name": prof["display_name"],
        "affiliation": prof["affiliation"],
        "member_since": prof["created_at"].isoformat(),
        "is_admin": user.is_admin,
        "settings": {k: (float(v) if k == "speech_rate" else v) for k, v in settings.items()},
        "allocation": None
        if alloc is None
        else {
            "semester": alloc["semester_code"],
            "grade": alloc["grade"],
            "hours_granted": float(alloc["hours_granted"]),
            "hours_used": round(float(alloc["hours_used"]), 2),
        },
    }


@router.patch("/profile")
async def update_profile(body: ProfileIn, user: CurrentUser, svc: Svc) -> dict[str, object]:
    async with svc.db.as_user(user.claims) as conn:
        await conn.execute(
            text("update astro.profiles set display_name = :n, affiliation = :a where id = astro.uid()"),
            {"n": body.display_name, "a": body.affiliation or None},
        )
    return {"ok": True}


@router.patch("/settings")
async def update_settings(body: SettingsIn, user: CurrentUser, svc: Svc) -> dict[str, object]:
    values = body.model_dump(exclude_none=True)
    if not values:
        return {"ok": True, "changed": []}
    # Column names come from the model's fixed field list, never from the request.
    assignments = ", ".join(f"{k} = :{k}" for k in values)
    async with svc.db.as_user(user.claims) as conn:
        await conn.execute(text(f"update astro.user_settings set {assignments} where user_id = astro.uid()"), values)
    return {"ok": True, "changed": sorted(values)}


@router.get("/sessions")
async def sessions(user: CurrentUser, svc: Svc) -> dict[str, object]:
    async with svc.db.as_user(user.claims) as conn:
        rows = (await conn.execute(text("select * from astro.my_sessions()"))).mappings().all()
    current = user.claims.get("session_id")
    return {
        "sessions": [
            {
                "id": str(r["id"]),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "last_active": r["updated_at"].isoformat() if r["updated_at"] else None,
                "user_agent": (r["user_agent"] or "")[:200],
                "ip": r["ip"],
                "mfa": r["aal"] == "aal2",
                "current": str(r["id"]) == current,
            }
            for r in rows
        ]
    }


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke(session_id: str, user: CurrentUser, svc: Svc) -> Response:
    if session_id == user.claims.get("session_id"):
        raise ApiError(400, "current_session", "Use sign out to end this session.")
    async with svc.db.as_user(user.claims) as conn:
        ok = (await conn.execute(text("select astro.revoke_session(cast(:i as uuid))"), {"i": session_id})).scalar()
    if not ok:
        raise ApiError(404, "not_found", "That session no longer exists.")
    return Response(status_code=204)


class DeleteIn(Strict):
    confirm: Literal["DELETE"]


@router.post("/delete", status_code=202)
async def delete_account(body: DeleteIn, user: CurrentUser, svc: Svc) -> dict[str, object]:
    """Cancel future bookings (which frees slots for the waitlist), then delete the GoTrue user; the cascade
    from auth.users removes everything personal."""
    async with svc.db.as_user(user.claims) as conn:
        await conn.execute(
            text(
                "update astro.bookings set status = 'cancelled', cancel_reason = 'account deleted' "
                "where user_id = astro.uid() and status in ('held', 'confirmed') and lower(period) > now()"
            )
        )
        await conn.execute(text("select astro.audit('delete_account', 'profile', :i)"), {"i": user.user_id})
    s = svc.settings
    token = jwt.encode(
        {"role": "service_role", "iss": s.jwt_issuer, "exp": datetime.now(UTC) + timedelta(minutes=2)},
        s.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    async with httpx.AsyncClient(base_url=s.gotrue_url, timeout=10) as http:
        r = await http.delete(f"/admin/users/{user.user_id}", headers={"Authorization": f"Bearer {token}"})
    if r.status_code not in (200, 204, 404):
        raise ApiError(502, "auth_unavailable", "We could not delete your account just now. Please try again.")
    return {"ok": True}
