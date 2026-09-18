"""SQLAlchemy 2.x models for the Sentinel control plane (Section 8).

These tables are the source of truth for configuration. The gateway (data
plane) never queries Postgres on the request hot path (Section 35, 55) --
it reads through the in-memory config cache described in
apps/gateway/config_cache.py (Phase 9, not yet built), which is refreshed
via Redis pub/sub notifications when a row here changes.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TenantStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class ApiKeyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class BackendInstanceStatus(str, enum.Enum):
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"
    DRAINING = "DRAINING"
    UNKNOWN = "UNKNOWN"


class RateLimitAlgorithm(str, enum.Enum):
    TOKEN_BUCKET = "TOKEN_BUCKET"
    SLIDING_WINDOW = "SLIDING_WINDOW"
    FIXED_WINDOW = "FIXED_WINDOW"


class RateLimitScope(str, enum.Enum):
    TENANT = "TENANT"
    API_KEY = "API_KEY"
    ROUTE = "ROUTE"
    IP = "IP"
    COMPOSITE = "COMPOSITE"


class Priority(str, enum.Enum):
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        SAEnum(TenantStatus, name="tenant_status"), default=TenantStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="tenant")
    routes: Mapped[list["Route"]] = relationship(back_populates="tenant")
    backend_services: Mapped[list["BackendService"]] = relationship(back_populates="tenant")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # Only a hash is ever stored -- never the plaintext key (Section 8, 38).
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ApiKeyStatus] = mapped_column(
        SAEnum(ApiKeyStatus, name="api_key_status"), default=ApiKeyStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="api_keys")


class BackendService(Base):
    __tablename__ = "backend_services"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    health_check_path: Mapped[str] = mapped_column(String(255), default="/health")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    tenant: Mapped[Tenant] = relationship(back_populates="backend_services")
    instances: Mapped[list["BackendInstance"]] = relationship(back_populates="service")

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_backend_service_name"),)


class BackendInstance(Base):
    __tablename__ = "backend_instances"

    id: Mapped[uuid.UUID] = _uuid_pk()
    backend_service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backend_services.id"), nullable=False
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[BackendInstanceStatus] = mapped_column(
        SAEnum(BackendInstanceStatus, name="backend_instance_status"),
        default=BackendInstanceStatus.UNKNOWN,
    )
    active_connections: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_health_check: Mapped[datetime | None] = mapped_column(nullable=True)

    service: Mapped[BackendService] = relationship(back_populates="instances")


class RateLimitPolicy(Base):
    __tablename__ = "rate_limit_policies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    algorithm: Mapped[RateLimitAlgorithm] = mapped_column(
        SAEnum(RateLimitAlgorithm, name="rate_limit_algorithm")
    )
    requests_per_second: Mapped[float] = mapped_column(nullable=False)
    burst_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, default=60)
    scope: Mapped[RateLimitScope] = mapped_column(SAEnum(RateLimitScope, name="rate_limit_scope"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class CircuitBreakerPolicy(Base):
    __tablename__ = "circuit_breaker_policies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    failure_threshold: Mapped[float] = mapped_column(default=0.5)
    minimum_requests: Mapped[int] = mapped_column(Integer, default=20)
    open_duration_ms: Mapped[int] = mapped_column(Integer, default=10000)
    half_open_requests: Mapped[int] = mapped_column(Integer, default=3)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path_pattern: Mapped[str] = mapped_column(String(512), nullable=False)
    http_methods: Mapped[list[str]] = mapped_column(ARRAY(String))
    backend_service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backend_services.id"), nullable=False
    )
    timeout_ms: Mapped[int] = mapped_column(Integer, default=2000)
    retry_policy_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    rate_limit_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rate_limit_policies.id"), nullable=True
    )
    priority: Mapped[Priority] = mapped_column(
        SAEnum(Priority, name="route_priority"), default=Priority.NORMAL
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    tenant: Mapped[Tenant] = relationship(back_populates="routes")

    __table_args__ = (
        UniqueConstraint("tenant_id", "path_pattern", name="uq_route_tenant_path"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(255))
    resource_type: Mapped[str] = mapped_column(String(255))
    resource_id: Mapped[str] = mapped_column(String(255))
    event_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
