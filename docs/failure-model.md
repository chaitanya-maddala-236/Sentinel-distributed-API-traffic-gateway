# Sentinel Failure Model

This document states, explicitly, what Sentinel is designed to do when
each dependency or subsystem fails. Nothing here is aspirational -- where
a behavior is not yet implemented, it is marked **(planned)** rather than
described as if it already works, per the project's no-fake-implementation
rule.

## Failure Matrix

| Failure                        | Expected behavior                                                                 | Status |
|---------------------------------|------------------------------------------------------------------------------------|--------|
| Redis down                     | Per-policy configurable `FAIL_OPEN` / `FAIL_CLOSED`; default `FAIL_CLOSED` for protected routes | Implemented (`packages/redis/client.py`) |
| Redis slow / times out mid-check | Treated identically to Redis down (the `RedisError` catch covers timeouts) | Implemented |
| PostgreSQL down                | Cached gateway config continues serving; control-plane writes fail clearly (503 from control plane) | Bootstrap loader implemented; real Postgres-backed cache is **(planned, Phase 9)** |
| Backend instance down          | Health checker marks it UNHEALTHY after `failure_threshold` consecutive failed checks; router excludes it | Implemented (`apps/gateway/health/checker.py`, `apps/gateway/routing/router.py`) |
| Backend instance slow          | Per-attempt timeout bounded by `min(remaining_deadline, route.timeout_ms)`; backpressure/queueing bounded separately | Implemented |
| Backend returns 502/503/504    | Counted as a circuit-breaker failure; retried if idempotent and deadline allows | Implemented |
| Backend returns 4xx             | NOT counted as a circuit failure by default; NOT retried by default | Implemented |
| Sustained backend failure       | Circuit opens once `minimum_requests` reached and failure rate crosses `failure_threshold`; subsequent requests fail fast with 503 `CIRCUIT_OPEN` | Implemented |
| Backend recovers                | After `open_duration_seconds`, circuit moves to HALF_OPEN and admits up to `half_open_max_requests` probes; any probe failure reopens immediately, enough successes close it | Implemented |
| Gateway instance overloaded     | Bounded concurrency gates (global/tenant/route/backend) reject with 503 once their `queue_timeout_ms` wait expires; no unbounded queueing | Implemented |
| Gateway instance crashes        | Other replicas behind Nginx continue serving; in-flight requests on the crashed instance are lost (no cross-instance request replay) | Nginx round-robin implemented; formal crash-recovery chaos test is **(planned, Phase 14)** |
| Config update                   | Gateway instances refresh their cache within one pub/sub notification + refetch | **(planned, Phase 9)** -- today, config only changes via a full gateway restart re-reading `infra/docker/routes.yaml` |
| Traffic spike beyond configured limit | 429 with `Retry-After`/`X-RateLimit-*` headers from the rate limiter; if concurrency is also exhausted, 503 from backpressure | Implemented |
| Rate-spike combined with gateway saturation | Priority-aware load shedding (`LoadShedder`) sheds LOW before NORMAL before HIGH | Logic implemented (`apps/gateway/backpressure/semaphores.py`); not yet wired into the request pipeline's priority field end-to-end -- **(partially planned)** |

## What "FAIL_CLOSED" and "FAIL_OPEN" actually mean here

When Redis is unreachable during a rate-limit check:

- **FAIL_CLOSED** (default for any route protecting a real backend): the
  request is treated as rate-limited (`allowed=False`) and rejected with
  429. This protects the backend at the direct cost of availability
  during the outage -- during a Redis outage, FAIL_CLOSED routes stop
  serving traffic entirely.
- **FAIL_OPEN** (opt-in, for explicitly low-risk routes only): the
  request is let through uncounted. Quotas are simply not enforced for
  the duration of the outage.

Both paths set `RateLimitDecision.degraded = True` and log
`rate_limiter.redis_unavailable` so this condition is visible in metrics
and logs rather than silently indistinguishable from a normal allow/deny.

## What Sentinel does NOT claim

- It does not claim perfectly precise global rate-limit enforcement under
  every failure scenario -- see docs/rate-limiting.md for the exact
  consistency model.
- It does not claim instant cross-instance circuit-breaker synchronization
  (see ADR-005) -- each instance is authoritative for its own traffic.
- It does not claim the Phase 9 config cache exists yet; the YAML
  bootstrap loader is a clearly-labeled stand-in.
- It does not claim any load-test or chaos-test numbers that have not
  actually been run against a live stack -- see docs/performance.md,
  which is intentionally left as a template with no fabricated figures
  until real benchmarks are executed against a deployed environment (this
  sandbox has no Docker/network access to run that stack -- see the
  README's "Current status" section).
