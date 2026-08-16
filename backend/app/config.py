"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: PostgresDsn = Field(...)
    jwt_secret_key: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    environment: str = "development"
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Phase 4 — Heartbeats, Leases, Timeouts, Retries & Recovery
    heartbeat_interval_seconds: float = Field(default=2.0, gt=0)
    worker_stale_threshold_seconds: float = Field(default=10.0, gt=0)
    task_lease_seconds: float = Field(default=10.0, gt=0)
    task_lease_renew_interval_seconds: float = Field(default=3.0, gt=0)
    recovery_interval_seconds: float = Field(default=5.0, gt=0)
    default_task_timeout_seconds: int = Field(default=300, ge=1, le=86400)
    max_task_timeout_seconds: int = Field(default=86400, ge=1)
    default_max_retries: int = Field(default=3, ge=0)
    max_retries_limit: int = Field(default=20, ge=0)
    retry_backoff_base_seconds: float = Field(default=1.0, ge=0)
    retry_backoff_max_seconds: float = Field(default=60.0, ge=0)

    # Phase 7 — observability remains optional/non-critical.
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str | None = None
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
