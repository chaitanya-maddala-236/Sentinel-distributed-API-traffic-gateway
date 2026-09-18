# Sentinel Architecture

## Control plane vs data plane (Section 61, ADR-008)

```
                 CONTROL PLANE                          DATA PLANE
        ┌──────────────────────────┐          ┌──────────────────────────────┐
        │  apps/control_plane      │          │  apps/gateway (N replicas)   │
        │  - tenants, API keys     │          │  - request pipeline          │
        │  - routes, backends      │  writes  │  - rate limiting             │
        │  - rate/circuit policies │─────────▶│  - circuit breaking           │
        │  - RBAC admin API        │  Postgres│  - retries                    │
        └──────────────────────────┘          │  - proxying                   │
                     │                          └──────────────────────────────┘
                     │ config version bump                 │        │
                     ▼                                      ▼        ▼
              ┌─────────────┐                         Redis (shared)  Backends
              │ PostgreSQL  │◀────────────────────────(rate limits,
              └─────────────┘   Redis pub/sub notify    circuit coord* )
                                 (Phase 9, planned)

* circuit-breaker state is per-instance today (ADR-005); Redis is only
  used for rate-limit counters and the planned config-change pub/sub.
```

The gateway never queries PostgreSQL on the request hot path (Section 35,
55). Until Phase 9's cache lands, it reads route/backend configuration
once at startup from `infra/docker/routes.yaml` (see
`apps/gateway/bootstrap.py`, explicitly labeled as a placeholder).

## The request pipeline (Section 5)

Implemented in `apps/gateway/pipeline/pipeline.py`:

```
Request
  │
  ▼
Request size validation ───▶ 413 if body > max_body_size_bytes
  │
  ▼
Route matching ───▶ 404 if no route matches (tenant, method, path)
  │
  ▼
Rate limit (Redis Lua token bucket) ───▶ 429 if denied
  │
  ▼
Concurrency / backpressure (4 bounded gates) ───▶ 503 if no slot in time
  │
  ▼
┌─ per attempt, up to max_attempts, bounded by one global deadline ──┐
│  Circuit breaker check ───▶ 503 CIRCUIT_OPEN if open               │
│  Backend selection (weighted round robin, healthy only)            │
│    ───▶ 503 NO_HEALTHY_BACKEND if none                             │
│  Proxy forward (bounded per-attempt timeout)                       │
│  Response classification (success / retryable / non-retryable)     │
│  If retryable and deadline allows: backoff with full jitter, retry │
└──────────────────────────────────────────────────────────────────┘
  │
  ▼
Metrics + structured log
  │
  ▼
Response
```

Each arrow above corresponds to one `PipelineResult.stage_failed` value,
so a failure's cause is always attributable to a named stage in logs and
tests, not inferred after the fact.

## Why the gateway is stateless enough to scale horizontally (Section 79)

- Rate-limit state lives in Redis, not gateway process memory.
- Circuit-breaker state is process-local by design (ADR-005) -- this is
  the one piece of "state" that differs per instance, and it is a
  deliberate, documented tradeoff rather than an accidental one.
- Concurrency-gate state (semaphores) is process-local and *must* be --
  it exists specifically to bound what one process can do to itself and
  its immediate downstream, not to coordinate globally.
- No sticky sessions are required for ordinary HTTP traffic; Nginx
  round-robins across `gateway-1`/`gateway-2` in `docker-compose.yml`.

## Current implementation status

See the README's "Current status / what's built vs. planned" section for
the authoritative, up-to-date phase-by-phase breakdown -- it is kept in
one place to avoid this document and the README drifting out of sync.
