# ADR-010: Weighted Round-Robin as the Initial Load-Balancing Algorithm

## Context
Section 27 asks for multi-instance backend routing with an initial
algorithm and an optional upgrade path.

## Decision
Implement weighted round-robin (`WeightedRoundRobinSelector`) as the only
algorithm in v1, selecting only among instances currently marked HEALTHY
and not DRAINING.

## Alternatives Considered
- **Least-connections**: a better fit under heterogeneous request cost,
  explicitly named in Section 27 as an optional later addition. Not built
  yet because weighted round-robin is sufficient to demonstrate correct
  health/draining exclusion, which is the property most worth proving
  first.
- **Random selection**: simpler, but doesn't respect configured instance
  weights (e.g. a larger instance meant to take proportionally more
  traffic).

## Consequences
- `WeightedRoundRobinSelector` rebuilds its weighted cycle whenever the
  set of healthy instances changes, so a newly-unhealthy instance is
  excluded from the very next selection rather than waiting for a full
  cycle to complete.
