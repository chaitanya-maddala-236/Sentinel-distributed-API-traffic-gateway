# ADR-008: Separate Control-Plane and Data-Plane Processes

## Context
Section 61 requires an architectural split between configuration
management and request handling.

## Decision
`apps/control_plane/main.py` and `apps/gateway/main.py` are two separate
FastAPI applications, independently deployable and scalable, communicating
only through PostgreSQL (writes) and Redis pub/sub (config-change
notifications, Phase 9).

## Alternatives Considered
- **A single FastAPI app serving both admin and proxy routes**: simpler
  to deploy initially, but couples the availability and resource profile
  of latency-sensitive proxy traffic to the admin API's load (e.g. a
  large "list all routes" query blocking the event loop that is also
  serving proxy traffic).

## Consequences
- Two Dockerfiles, two deployments, and a documented dependency direction
  (gateway depends on cached config; control plane depends on Postgres
  directly) that Section 55 requires: a control-plane/Postgres outage
  must not take down data-plane traffic serving from cache.
