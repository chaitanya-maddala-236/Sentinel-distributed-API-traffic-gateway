# ADR-009: In-Memory Config Cache Refreshed via Redis Pub/Sub

## Context
The gateway must not query Postgres on every proxied request (Section 35),
but configuration does change (a route is added, a backend is
reweighted) and instances must eventually converge on the new state.

## Decision (Phase 9, planned)
Each gateway instance holds an in-memory cache (route table, backend
table, policy table) tagged with a version number. When the control plane
commits a configuration change, it increments a version and publishes a
notification over Redis pub/sub; subscribed gateway instances then
re-fetch the changed configuration from Postgres and swap their local
cache.

## Alternatives Considered
- **Poll Postgres on an interval**: simpler, but trades immediacy for
  load on Postgres and a strictly worse worst-case propagation delay for
  the same operational complexity as pub/sub.
- **Long-poll or gRPC streaming config push from the control plane**:
  more machinery than the project's scale currently justifies; revisit if
  the pub/sub fan-out becomes a bottleneck.

## Consequences
- Documented propagation delay: after a config change, gateway instances
  converge within roughly one Redis pub/sub round-trip plus one Postgres
  refetch, not instantly (Section 37 explicitly permits this).
- **Status: not yet implemented.** `apps/gateway/bootstrap.py` is an
  explicit, clearly-labeled placeholder (a YAML file loaded at startup)
  standing in for this cache so the gateway is runnable end-to-end before
  Phase 9 lands -- it is not a substitute for the real mechanism and must
  not be mistaken for one.
