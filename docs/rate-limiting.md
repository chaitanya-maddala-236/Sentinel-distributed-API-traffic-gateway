# Rate Limiting: Semantics and Guarantees

Section 14 requires this to be documented explicitly rather than left
implicit. This is the contract Sentinel's rate limiter actually provides
-- not an idealized one.

## Unit
Rate limits are expressed as a capacity/refill-rate pair (token bucket) or
a request-count/window pair (sliding window). Cost per request defaults to
`1` token but is configurable per policy (`TokenBucketPolicy.cost`) so
expensive endpoints can be weighted higher.

## Burst behavior
- **Token bucket** deliberately allows bursts up to `capacity` tokens
  in one instant, then throttles to `refill_rate` per second thereafter.
  This is a feature, not a bug (see ADR-003) -- it accommodates clients
  that send occasional bursts (e.g. a page load firing several requests
  at once) without needing a separately-configured burst allowance.
- **Sliding window** does not allow any burst beyond the configured
  `limit` within any rolling `window_seconds` -- it is the stricter,
  no-burst option for abuse-sensitive routes.

## Clock source
Both Lua scripts take `now_ms` as an argument supplied by the calling
gateway process (`int(time.time() * 1000)`), not from Redis's own clock.
This means:
- Rate-limit accuracy depends on the gateway host's clock, not Redis's.
- Multiple gateway instances with clocks that have drifted from each
  other by more than a few milliseconds could see slightly inconsistent
  token-refill calculations for the *same* bucket. In practice, NTP-synced
  hosts keep this well under one refill tick's worth of drift; this is
  called out here rather than silently assumed away.

## Redis failure behavior
See docs/failure-model.md's "FAIL_CLOSED / FAIL_OPEN" section. Default is
`FAIL_CLOSED`. This is a deliberate availability-vs-protection tradeoff,
not an oversight.

## Distributed consistency expectations
The token-bucket and sliding-window Lua scripts are atomic *per key, per
Redis node*. That means:
- Two gateway instances checking the same key at the same time will never
  both be allowed past the enforced count due to a race -- the atomicity
  guarantee holds regardless of how many gateway instances share the
  Redis.
- If Redis itself is a cluster with the key's slot briefly unavailable
  during a failover, requests during that window fall back to the
  configured failure mode (see above) -- they do not silently succeed
  with stale data.
- This is exactly the property exercised by the distributed rate-limit
  test described in Section 66 (multiple Sentinel instances, one Redis,
  verify aggregate behavior matches the configured limit) -- that test is
  an integration test requiring a real Redis and is listed as **(planned,
  Phase 14)** in this repository since this sandbox cannot run one; the
  Lua scripts' atomicity is what makes the test expected to pass once run.

## Retry behavior
A 429 response includes `Retry-After` (seconds), computed from the Lua
script's `retry_after_ms` return value -- an estimate of when enough
tokens/window-space will exist, not a fixed constant. Clients should honor
it rather than retrying immediately.

## 429 response contract
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 2
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
```
```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Rate limit exceeded"
  }
}
```
(The `request_id` field described in Section 13's example is added by the
pipeline as the `X-Request-ID` header; see `apps/gateway/pipeline/pipeline.py`.)
