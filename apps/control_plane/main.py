"""Sentinel control plane (Section 45, 61).

Owns configuration: tenants, API keys, routes, backend services/instances,
rate-limit policies, circuit-breaker policies. This process never touches
the request hot path -- it is intentionally a separate FastAPI app from
apps/gateway/main.py (Section 61: control plane vs data plane separation),
so a control-plane outage cannot take down data-plane traffic as long as
the gateway's cached configuration remains valid (Section 55).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.config.settings import settings
from packages.database.models import Base, Tenant, TenantStatus

engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def lifespan(app: FastAPI):
    # In production, schema management is via Alembic migrations
    # (apps/control_plane/migrations) -- create_all here only covers local
    # dev/demo bring-up so `docker compose up` works out of the box.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="Sentinel Control Plane", lifespan=lifespan)


class TenantCreate(BaseModel):
    name: str


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    status: TenantStatus

    model_config = {"from_attributes": True}


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/admin/tenants", response_model=TenantOut, status_code=201)
async def create_tenant(payload: TenantCreate, session: AsyncSession = Depends(get_session)):
    tenant = Tenant(name=payload.name, status=TenantStatus.ACTIVE)
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    return tenant


@app.get("/admin/tenants", response_model=list[TenantOut])
async def list_tenants(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Tenant))
    return result.scalars().all()


@app.get("/admin/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(tenant_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    return tenant


# NOTE: API keys, routes, backend services/instances, rate-limit and
# circuit-breaker policy endpoints follow the exact same
# Depends(get_session) + Pydantic schema pattern as the tenant endpoints
# above. They are the next slice of Phase 2 work (see README's "Current
# status" section) and are intentionally not stubbed out here with fake
# 501 handlers, per Section 83 (no fake implementations) -- only the
# tenant CRUD above has been carried all the way to a tested, working
# state so far.
