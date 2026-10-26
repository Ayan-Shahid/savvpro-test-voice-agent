"""Verifies GoTrue access tokens. Only verified claims ever reach the database session.

Tokens arrive as `Authorization: Bearer <jwt>`; the API never reads auth cookies, so cross-site requests
cannot ride a session (CSRF does not apply), and the Next.js server forwards the user's token explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, Request

from .errors import ApiError
from .settings import Settings, get_settings

ALLOWED_CLAIMS = ("sub", "role", "aal", "session_id", "email", "app_metadata", "exp", "iat", "is_anonymous")


@dataclass(frozen=True)
class Principal:
    user_id: str
    claims: dict[str, Any]

    @property
    def is_admin(self) -> bool:
        app = self.claims.get("app_metadata") or {}
        return app.get("role") == "admin" and self.claims.get("aal") == "aal2"


def verify_token(token: str, settings: Settings) -> Principal:
    secret = settings.jwt_secret.get_secret_value()
    if not secret:
        raise ApiError(503, "auth_unavailable", "Sign-in is not configured.")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "aud", "iss"]},
            leeway=10,
        )
    except jwt.ExpiredSignatureError as e:
        raise ApiError(401, "token_expired", "Your session has expired. Please sign in again.") from e
    except jwt.PyJWTError as e:
        raise ApiError(401, "invalid_token", "Please sign in again.") from e
    if claims.get("role") != "authenticated" or claims.get("is_anonymous"):
        raise ApiError(403, "forbidden", "This account cannot use the booking service.")
    kept = {k: claims[k] for k in ALLOWED_CLAIMS if k in claims}
    kept.pop("user_metadata", None)  # editable by the user; never used for authorisation
    return Principal(str(claims["sub"]), kept)


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthenticated", "Please sign in.")
    token = authorization[7:].strip()
    if not token or len(token) > 8192:
        raise ApiError(401, "invalid_token", "Please sign in again.")
    return token


async def current_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    svc = getattr(request.app.state, "services", None)
    p = verify_token(_bearer(authorization), svc.settings if svc else get_settings())
    request.state.user_id = p.user_id
    return p


CurrentUser = Annotated[Principal, Depends(current_user)]
