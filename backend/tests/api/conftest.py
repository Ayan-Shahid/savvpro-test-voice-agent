"""API tests run the real app (TestClient, real lifespan) against the seeded test database and Redis."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from astro_api.main import create_app
from astro_api.settings import Settings

from ..db.conftest import pg, seeded  # noqa: F401  (re-exported fixtures)

SECRET = "test-secret-" + "x" * 40
ISSUER = "http://testserver/auth/v1"
ORIGIN = "http://testserver"
REDIS = os.environ.get("ASTRO_TEST_REDIS", "")


def token(uid: str, email: str = "demo@astro.example", **extra: Any) -> str:
    claims = {
        "sub": uid,
        "role": "authenticated",
        "aud": "authenticated",
        "iss": ISSUER,
        "email": email,
        "aal": "aal1",
        "session_id": "00000000-0000-0000-0000-000000000001",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        **extra,
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


@pytest.fixture(scope="session")
def settings(pg: dict[str, Any]) -> Settings:  # noqa: F811
    if not REDIS:
        pytest.skip("ASTRO_TEST_REDIS not set")
    return Settings(
        env="test",
        public_origin=ORIGIN,
        db_host=pg["host"],
        db_port=int(pg["port"]),
        db_name=pg["name"],
        redis_url=REDIS,
        db_api_password=os.environ.get("ASTRO_TEST_ROLE_PASSWORD", ""),  # type: ignore[arg-type]
        jwt_secret=SECRET,  # type: ignore[arg-type]
        jwt_issuer=ISSUER,
        frozen_now="2026-10-27T15:00:00",  # 20:00 at the site, the scenarios' clock
    )


@pytest.fixture(scope="session")
def client(settings: Settings, seeded: dict[str, Any]) -> Iterator[TestClient]:  # noqa: F811
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as c:
        yield c


def auth(uid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(uid)}", "Origin": ORIGIN}


def claims_for(uid: str) -> dict[str, Any]:
    return {"sub": uid, "role": "authenticated", "aal": "aal1"}
