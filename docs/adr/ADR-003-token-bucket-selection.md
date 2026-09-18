# ADR-003: Token Bucket as the Primary Rate-Limiting Algorithm

## Context
Section 9 requires at least two algorithms. We need a default that is
cheap to evaluate, allows reasonable burst tolerance, and is well
understood operationally.

## Decision
Token bucket is the default algorithm for API-key and tenant-level
limits; sliding-window-log is offered for routes that need hard,
precise caps with no burst allowance (e.g. abuse-sensitive endpoints).

## Alternatives Considered
- **Fixed window counters**: simplest to implement but allows up to 2x
  the configured rate at window boundaries (a client can burst at the
  end of one window and the start of the next). Listed in the schema
  (Section 8) as a supported algorithm value for completeness/future
  use, but not implemented as a first-class path in this version because
  its boundary-burst behavior makes it a worse default than token bucket
  or sliding window for the abuse-protection use cases this project
  targets.
- **Leaky bucket (queue-based)**: smooths bursts into a constant output
  rate, which is a good fit for outbound rate shaping but a poor fit for
  admission control at a gateway, where we want to reject over-limit
  requests immediately rather than queue and delay them.

## Consequences
- Every rate-limit policy row (Section 8) carries an `algorithm` enum so
  a tenant/route can pick token bucket or sliding window per policy.
- Burst behavior must be documented per policy (see
  docs/rate-limiting.md) since token bucket's burst allowance is a
  deliberate tradeoff, not a bug.
