# ADR-005: Per-Instance, In-Memory Circuit Breaker State

## Context
A circuit breaker protects a failing backend by failing fast once a
failure threshold is reached, then probing recovery via HALF_OPEN. In a
multi-instance gateway, each instance observes only its own share of
traffic to a given backend.

## Decision
Circuit breaker state (`apps/gateway/circuitbreaker/breaker.py`) is
authoritative per Sentinel instance for its own traffic, not synchronized
in real time across instances via Redis.

## Alternatives Considered
- **Redis-coordinated global circuit state**: every gateway instance
  would need to consult Redis before every backend request, adding a
  network round-trip specifically to the resilience mechanism whose job
  is to reduce latency/damage under failure -- a poor tradeoff for the
  common case where the circuit is CLOSED.
- **Fully independent circuits with no coordination at all**: the choice
  made here for v1. Each instance opens its own circuit once its own
  traffic shows the backend failing; under evenly distributed traffic all
  instances tend to reach the same conclusion within one evaluation
  window of each other, which Section 37 explicitly allows ("do not
  require perfect instant consistency; document the expected propagation
  delay").

## Consequences
- Under a genuinely failing backend, different instances may open their
  circuits a few seconds apart from each other rather than instantly in
  lock-step -- acceptable and documented, not silently swallowed.
- A future Redis pub/sub broadcast of OPEN/CLOSED transitions (so one
  instance's finding fast-tracks the others) is a valid follow-up and is
  explicitly flagged as not-yet-built in the breaker module's docstring,
  rather than implied to already exist.
