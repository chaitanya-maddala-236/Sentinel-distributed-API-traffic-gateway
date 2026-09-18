# ADR-002: Redis for Distributed Rate Limiting

## Context
Rate limits must be enforced consistently across N stateless gateway
replicas. An in-process counter per replica would let a client get N times
its configured limit by spreading requests across replicas.

## Decision
Use a single shared Redis (or Redis Cluster in production) as the
authoritative store for all rate-limit state (token-bucket and
sliding-window counters).

## Alternatives Considered
- **In-memory per-instance counters**: rejected -- doesn't provide
  cross-instance correctness (Section 66's explicit distributed test
  would fail).
- **Gossip protocol between gateway instances**: adds significant
  complexity and eventual-consistency lag for a problem Redis already
  solves atomically and with much lower operational overhead.
- **A dedicated rate-limiting service (e.g. Envoy's ratelimit service)**:
  a reasonable alternative, but pulls in an extra deployable component
  before the core system is proven; revisit if request volume outgrows
  a single Redis's throughput.

## Consequences
- Redis becomes a hard dependency on the data path for any route with
  `FAIL_CLOSED` configured (see ADR-004 and docs/rate-limiting.md for the
  explicit failure-mode tradeoff this creates).
- Redis must be sized and monitored as carefully as the gateway itself.
