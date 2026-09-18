"""Centralized, validated configuration (Section 82: validate configuration).

All tunables that Section 4-60 call out as configurable (timeouts,
concurrency limits, failure modes, etc.) have an explicit env-backed
default here rather than being hard-coded inline in the pipeline.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTINEL_", env_file=".env")

    # --- Core infra ---
    postgres_dsn: str = "postgresql+asyncpg://sentinel:sentinel@postgres:5432/sentinel"
    redis_url: str = "redis://redis:6379/0"
    instance_id: str = Field(default="sentinel-1")

    # --- HTTP client / proxy ---
    connect_timeout_s: float = 2.0
    read_timeout_s: float = 5.0
    write_timeout_s: float = 5.0
    pool_timeout_s: float = 2.0
    max_connections: int = 500
    max_keepalive_connections: int = 100
    max_body_size_bytes: int = 10 * 1024 * 1024  # 10 MB

    # --- Backpressure (Section 17-19) ---
    max_global_concurrency: int = 1000
    max_tenant_concurrency: int = 100
    max_route_concurrency: int = 200
    max_backend_concurrency: int = 100
    max_queue_size: int = 2000
    queue_timeout_ms: int = 500

    # --- Load shedding (Section 18) ---
    load_shed_watermark_normal: int = 900
    load_shed_watermark_low: int = 700

    # --- Circuit breaker defaults (Section 22-23, overridable per policy) ---
    cb_failure_threshold: float = 0.5
    cb_minimum_requests: int = 20
    cb_open_duration_s: float = 10.0
    cb_half_open_requests: int = 3

    # --- Retry defaults (Section 24-26) ---
    retry_max_attempts: int = 2
    retry_base_backoff_ms: int = 100
    retry_total_deadline_ms: int = 2000

    # --- Health checks (Section 28) ---
    health_check_interval_s: float = 5.0
    health_check_timeout_s: float = 1.0
    health_check_failure_threshold: int = 3
    health_check_success_threshold: int = 2
    drain_deadline_s: float = 30.0

    # --- Rate-limit failure mode default (Section 16) ---
    rate_limit_default_fail_mode: str = "FAIL_CLOSED"

    # --- Shutdown (Section 53) ---
    graceful_shutdown_timeout_s: float = 30.0

    # --- Observability ---
    otel_exporter_endpoint: str | None = None
    log_level: str = "INFO"


settings = Settings()
