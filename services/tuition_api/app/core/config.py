"""Runtime configuration, read from environment variables (12-factor)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql+psycopg://tuition:tuition@localhost:5432/tuition"
        )
    )
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", "dev-only-change-me-0123456789abcdef"))
    # The issuer is also the Kong JWT credential "key" (Kong reads it from the `iss` claim).
    jwt_issuer: str = field(default_factory=lambda: os.getenv("JWT_ISSUER", "tuition-auth"))
    jwt_ttl_minutes: int = field(default_factory=lambda: int(os.getenv("JWT_TTL_MINUTES", "60")))
    mobile_daily_limit: int = field(default_factory=lambda: int(os.getenv("MOBILE_DAILY_LIMIT", "3")))
    enforce_mobile_quota: bool = field(default_factory=lambda: _bool("ENFORCE_MOBILE_QUOTA", True))
    seed_demo_data: bool = field(default_factory=lambda: _bool("SEED_DEMO_DATA", True))
    timezone: str = field(default_factory=lambda: os.getenv("APP_TIMEZONE", "Europe/Istanbul"))
    max_batch_bytes: int = field(default_factory=lambda: int(os.getenv("MAX_BATCH_BYTES", str(1024 * 1024))))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
