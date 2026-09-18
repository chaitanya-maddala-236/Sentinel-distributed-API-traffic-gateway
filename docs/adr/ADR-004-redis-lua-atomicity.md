# ADR-004: Redis Lua Scripts for Atomic Rate-Limit Operations

## Context
A naive GET -> compute -> SET rate limiter races under concurrency: two
gateway instances (or two concurrent requests on the same instance) can
both read the same token count, both compute "allowed", and both write
back, letting more requests through than the configured limit.

## Decision
Implement both the token-bucket and sliding-window checks as single Redis
Lua scripts (`packages/redis/lua/token_bucket.lua`,
`packages/redis/lua/sliding_window.lua`) that perform the entire
read-calculate-update-expire cycle atomically, since Redis executes a Lua
script to completion without interleaving other commands.

## Alternatives Considered
- **Redis transactions (MULTI/EXEC) with WATCH**: works but requires
  optimistic-lock retry loops in application code on every contended
  write, adding latency and complexity the Lua approach avoids entirely.
- **Redis 7 server-side functions**: functionally similar to Lua scripts
  with a different deployment/versioning model; Lua via `EVALSHA` (which
  `redis-py`'s `register_script` handles transparently, including
  `NOSCRIPT` retries after a Redis restart) was chosen for broader Redis
  version compatibility.

## Consequences
- The clock is passed into the script by the caller (`now_ms`) rather
  than read via `redis.call("TIME")`, specifically so tests can freeze
  and advance time deterministically and so the gateway's own clock is
  the one source of truth (documented in the Lua scripts themselves).
- Lua scripts are harder to unit-test in isolation than plain Python;
  they are covered by integration tests (Phase 14) against a real Redis
  rather than pure unit tests.
