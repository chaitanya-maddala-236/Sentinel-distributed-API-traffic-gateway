# ADR-001: PostgreSQL as the Control-Plane Store

## Context
Sentinel needs a durable, relational store for tenants, API keys, routes,
backend services/instances, and policies. This data has referential
integrity requirements (a route must point at a real backend service; an
API key must belong to a real tenant) and needs transactional writes from
the admin API.

## Decision
Use PostgreSQL as the single source of truth for all control-plane
configuration.

## Alternatives Considered
- **A NoSQL document store**: weaker referential integrity guarantees,
  and the schema in Section 8 is inherently relational.
- **Storing config directly in Redis**: Redis is used for ephemeral,
  high-throughput state (Section 6/9), not durable relational config; it
  lacks transactions across multiple related writes and has no schema
  migration story.
- **Config files (YAML/JSON) checked into git**: rejected for production
  because it can't support a multi-tenant admin API making live changes,
  though it is used as a *temporary bootstrap* mechanism
  (`infra/docker/routes.yaml`) until Phase 9's cache is built.

## Consequences
- Requires Alembic migrations and a control-plane process that owns the
  Postgres connection pool.
- The gateway must never query Postgres on the request hot path (see
  ADR-009); it can only be treated as available for control-plane writes,
  not for the data plane.
