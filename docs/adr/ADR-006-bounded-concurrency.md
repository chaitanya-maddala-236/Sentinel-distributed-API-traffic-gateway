# ADR-006: Bounded Async Semaphores for All Concurrency Limits

## Context
Sections 17-19 require global, tenant, route, and backend concurrency
limits, plus a bound on how long a request may wait for a slot.

## Decision
Every concurrency limit is a `BoundedConcurrencyGate` wrapping
`asyncio.Semaphore`, acquired with `asyncio.wait_for(..., timeout=queue_timeout_ms)`.
A request that cannot get a slot in time is rejected with 503 rather than
queued indefinitely.

## Alternatives Considered
- **Unbounded `asyncio.Queue` per scope**: rejected outright per Section
  19 -- an unbounded queue converts overload into unbounded added
  latency instead of preventing it, which is the opposite of the goal.
- **Token-bucket-style admission control instead of semaphores**: rate
  limiting already handles "how many requests per second"; concurrency
  limits answer a different question ("how many requests in flight at
  once"), which a semaphore models directly and simply.

## Consequences
- Four gates are checked per request (global, tenant, route, backend);
  whichever is exhausted first determines the rejection reason recorded
  in `PipelineResult.stage_failed` for observability.
- Gate registries (`_tenant_gates`, `_route_gates`, `_backend_gates`) grow
  unboundedly with the number of distinct tenants/routes/backends seen;
  acceptable at the scale this project targets, but flagged here as a
  memory-growth consideration for very large multi-tenant deployments.
