"""Distributed rate limiter built on atomic Redis Lua scripts.

Design notes (see docs/rate-limiting.md for the full semantics writeup):

- Every read-calculate-update-expire cycle happens inside a single Lua
  script execution, so concurrent Sentinel instances sharing the same Redis
  can never race on a bucket or window (see ADR-002, ADR-004).
- The gateway supplies `now_ms` itself (via `time.time()`), rather than
  asking Redis for the time, so unit tests can freeze/advance the clock
  deterministically and so we are not sensitive to Redis's own clock
  drifting from the gateway's.
- Redis failure behavior is explicit and configurable per policy
  (`RedisFailureMode.FAIL_OPEN` / `FAIL_CLOSED`), never silently
  swallowed. Default is FAIL_CLOSED for anything protecting a backend.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import redis.asyncio as redis
from redis.exceptions import RedisError

logger = logging.getLogger("sentinel.ratelimit")

_LUA_DIR = Path(__file__).parent / "lua"


class RedisFailureMode(str, Enum):
    """What to do when Redis is unreachable or times out mid-check.

    FAIL_CLOSED (default): treat the request as rate-limited. Protects the
    backend at the cost of availability during a Redis outage. This is the
    required default for any route protecting a real backend (Section 16).

    FAIL_OPEN: let the request through uncounted. Only appropriate for
    routes that are explicitly configured as low-risk, because it means
    quotas are not enforced for the duration of the outage.
    """

    FAIL_OPEN = "FAIL_OPEN"
    FAIL_CLOSED = "FAIL_CLOSED"


class Algorithm(str, Enum):
    TOKEN_BUCKET = "TOKEN_BUCKET"
    SLIDING_WINDOW = "SLIDING_WINDOW"


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_ms: int
    limit: int
    degraded: bool = False
    """True if this decision was made without Redis (fail-open/closed path)."""


@dataclass(frozen=True)
class TokenBucketPolicy:
    capacity: int
    refill_rate: float  # tokens per second
    cost: int = 1
    ttl_seconds: int = 3600
    failure_mode: RedisFailureMode = RedisFailureMode.FAIL_CLOSED


@dataclass(frozen=True)
class SlidingWindowPolicy:
    limit: int
    window_seconds: int
    ttl_seconds: int = 3600
    failure_mode: RedisFailureMode = RedisFailureMode.FAIL_CLOSED


class DistributedRateLimiter:
    """Async wrapper around the token-bucket and sliding-window Lua scripts.

    One instance is shared process-wide (it holds a Redis connection pool
    and pre-loaded SHA1 script digests via `redis-py`'s `Script` objects,
    which transparently handle `NOSCRIPT` retries after a Redis restart).
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client
        self._token_bucket_script = redis_client.register_script(
            (_LUA_DIR / "token_bucket.lua").read_text()
        )
        self._sliding_window_script = redis_client.register_script(
            (_LUA_DIR / "sliding_window.lua").read_text()
        )

    async def check_token_bucket(self, key: str, policy: TokenBucketPolicy) -> RateLimitDecision:
        now_ms = int(time.time() * 1000)
        try:
            allowed, remaining, retry_after_ms = await self._token_bucket_script(
                keys=[key],
                args=[
                    policy.capacity,
                    policy.refill_rate,
                    policy.cost,
                    now_ms,
                    policy.ttl_seconds,
                ],
            )
            return RateLimitDecision(
                allowed=bool(allowed),
                remaining=int(remaining),
                retry_after_ms=int(retry_after_ms),
                limit=policy.capacity,
            )
        except RedisError:
            return self._degraded_decision(key, policy.failure_mode, policy.capacity)

    async def check_sliding_window(
        self, key: str, policy: SlidingWindowPolicy
    ) -> RateLimitDecision:
        now_ms = int(time.time() * 1000)
        member_id = uuid.uuid4().hex
        try:
            allowed, count, retry_after_ms = await self._sliding_window_script(
                keys=[key],
                args=[
                    policy.limit,
                    policy.window_seconds * 1000,
                    now_ms,
                    member_id,
                    policy.ttl_seconds,
                ],
            )
            return RateLimitDecision(
                allowed=bool(allowed),
                remaining=max(0, policy.limit - int(count)),
                retry_after_ms=int(retry_after_ms),
                limit=policy.limit,
            )
        except RedisError:
            return self._degraded_decision(key, policy.failure_mode, policy.limit)

    def _degraded_decision(
        self, key: str, failure_mode: RedisFailureMode, limit: int
    ) -> RateLimitDecision:
        logger.error(
            "rate_limiter.redis_unavailable",
            extra={"key": key, "failure_mode": failure_mode.value},
        )
        allowed = failure_mode == RedisFailureMode.FAIL_OPEN
        return RateLimitDecision(
            allowed=allowed,
            remaining=0 if not allowed else limit,
            retry_after_ms=1000 if not allowed else 0,
            limit=limit,
            degraded=True,
        )
