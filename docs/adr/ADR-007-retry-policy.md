# ADR-007: Deadline-Bounded, Idempotency-Aware Retries

## Context
Retries can turn a transient backend blip into a self-inflicted traffic
amplification event if done carelessly (Section 24, 57).

## Decision
- Retries are only attempted for idempotent methods (GET/HEAD/PUT/DELETE/
  OPTIONS) by default; POST/PATCH require `allow_non_idempotent=True` on
  the route's retry policy, which routes should only set when protected
  by an idempotency-key contract with the backend.
- All retry attempts for one request share a single global deadline
  (`DeadlineTracker`); an attempt never gets a fresh full timeout.
- Backoff uses full jitter (`uniform(0, base * 2^(attempt-1))`), not a
  fixed delay, to avoid synchronized retry storms across clients that
  failed at the same moment.

## Alternatives Considered
- **Fixed-delay retries**: simpler but produces exactly the synchronized
  retry storm this design exists to avoid.
- **Per-attempt fresh timeouts**: rejected because a request budgeted for
  2 seconds total could otherwise consume 2 seconds per attempt across
  `max_attempts` attempts, silently multiplying the worst-case latency
  the caller was promised.

## Consequences
- Callers must set `total_deadline_ms` thoughtfully relative to
  `route.timeout_ms` and `max_attempts`, since the deadline -- not the
  attempt count -- is often what actually stops retrying.
