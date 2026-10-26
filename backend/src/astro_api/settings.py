"""Configuration. Plain values come from the environment; secrets come from files in /run/secrets
(Docker secrets, decrypted from SOPS at deploy time). Nothing secret has a default."""

from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["development", "test", "staging", "production"]
SECRETS_DIR = Path("/run/secrets")


SECRET_FIELDS = (
    "db_api_password",
    "db_owner_password",
    "db_worker_password",
    "jwt_secret",
    "deepgram_api_key",
    "smtp_password",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASTRO_", extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _secret_files(cls, data: object) -> object:
        """Fill secret fields from /run/secrets/<name> (Docker secrets). Explicit values win."""
        if not isinstance(data, dict) or not SECRETS_DIR.is_dir():
            return data
        files = {name: name for name in SECRET_FIELDS}
        files["redis_password"] = os.environ.get("ASTRO_REDIS_PASSWORD_SECRET", "redis_api_password")
        for field, fname in files.items():
            path = SECRETS_DIR / fname
            if field not in data and f"ASTRO_{field.upper()}" not in os.environ and path.is_file():
                data[field] = path.read_text().strip()
        return data

    env: Env = "development"
    public_origin: str = "http://localhost:8080"
    # Extra origins allowed to open the WebSocket and call the API with credentials (exact match only).
    extra_origins: list[str] = Field(default_factory=list)

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "astrovoice"
    db_api_password: SecretStr = SecretStr("")
    db_owner_password: SecretStr = SecretStr("")
    db_worker_password: SecretStr = SecretStr("")
    db_pool_size: int = 10

    redis_url: str = "redis://localhost:6379/0"
    redis_password: SecretStr = SecretStr("")

    gotrue_url: str = "http://localhost:9999"
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "http://localhost:8080/auth/v1"
    jwt_audience: str = "authenticated"

    deepgram_api_key: SecretStr = SecretStr("")
    deepgram_stt_model: str = "flux-general-en"
    deepgram_tts_model: str = "flux-haley-en"  # Flux TTS (/v2/speak); Aura-2 is the fallback below
    deepgram_tts_fallback: str = "aura-2-thalia-en"
    deepgram_mip_opt_out: bool = True

    # Demo anchor date for seeded scenarios; None means "today at the site".
    anchor_date: date | None = None
    # Freeze the brain's clock for demos and tests (ISO datetime, UTC). Never set in production.
    frozen_now: str | None = None

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_starttls: bool = False
    mail_from: str = "AstroVoice <bookings@ayandev.app>"

    @field_validator("extra_origins", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    # ---- derived -----------------------------------------------------------------------------
    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset([self.public_origin.rstrip("/"), *(o.rstrip("/") for o in self.extra_origins)])

    @property
    def is_prod_like(self) -> bool:
        return self.env in ("staging", "production")

    def _dsn(self, user: str, password: SecretStr) -> str:
        pw = quote(password.get_secret_value(), safe="")
        auth = f"{user}:{pw}@" if pw else f"{user}@"
        return f"postgresql+asyncpg://{auth}{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def api_dsn(self) -> str:
        return self._dsn("astro_api", self.db_api_password)

    @property
    def worker_dsn(self) -> str:
        return self._dsn("astro_worker", self.db_worker_password)

    @property
    def migration_dsn(self) -> str:
        return self._dsn("astro_owner", self.db_owner_password)

    def validate_for_runtime(self) -> None:
        """Refuse to start a prod-like environment with missing or weak secrets."""
        if not self.is_prod_like:
            return
        problems = []
        if len(self.jwt_secret.get_secret_value()) < 32:
            problems.append("jwt_secret must be at least 32 characters")
        if not self.db_api_password.get_secret_value():
            problems.append("db_api_password is empty")
        if not self.public_origin.startswith("https://"):
            problems.append("public_origin must be https")
        if self.frozen_now:
            problems.append("frozen_now must not be set outside development")
        if problems:
            raise RuntimeError("unsafe configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
