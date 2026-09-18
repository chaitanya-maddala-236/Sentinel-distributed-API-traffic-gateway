"""Controlled retries with a single global deadline (Section 24-26).

Key rule: retries share one overall deadline for the whole request, not a
fresh timeout per attempt. If attempt 1 consumes 1.7s of a 2s budget, we
must not start attempt 2 with another full 2s timeout — it gets whatever
is left, and if that's <= 0 we stop and surface the failure.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import Enum


class FailureKind(str, Enum):
    CONNECTION_ERROR = "CONNECTION_ERROR"
    TIMEOUT = "TIMEOUT"
    HTTP_502 = "HTTP_502"
    HTTP_503 = "HTTP_503"
    HTTP_504 = "HTTP_504"
    HTTP_4XX = "HTTP_4XX"  # not retried by default
    HTTP_OTHER_5XX = "HTTP_OTHER_5XX"
    SUCCESS = "SUCCESS"


_DEFAULT_RETRYABLE = frozenset(
    {
        FailureKind.CONNECTION_ERROR,
        FailureKind.TIMEOUT,
        FailureKind.HTTP_502,
        FailureKind.HTTP_503,
        FailureKind.HTTP_504,
    }
)

_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    base_backoff_ms: int = 100
    total_deadline_ms: int = 2000
    retryable_failures: frozenset[FailureKind] = _DEFAULT_RETRYABLE
    # Non-idempotent methods (POST, PATCH) are only retried if the caller
    # explicitly marks the route as idempotency-safe (e.g. it requires an
    # Idempotency-Key header the backend de-dupes on).
    allow_non_idempotent: bool = False


class RetryBudgetExhausted(Exception):
    """Raised when the global deadline is exhausted before another attempt
    could be made — distinct from a normal final failure so callers/metrics
    can tell 'ran out of attempts' apart from 'ran out of time'."""


def is_retryable(
    method: str,
    failure_kind: FailureKind,
    policy: RetryPolicy,
    *,
    circuit_is_open: bool,
) -> bool:
    if circuit_is_open:
        return False
    if failure_kind not in policy.retryable_failures:
        return False
    if method.upper() not in _IDEMPOTENT_METHODS and not policy.allow_non_idempotent:
        return False
    return True


def compute_backoff_ms(attempt: int, base_backoff_ms: int) -> float:
    """Exponential backoff with full jitter: uniform(0, base * 2^(attempt-1)).

    Full jitter (rather than a fixed jitter offset) is used specifically to
    avoid synchronized retry storms across many clients that failed at the
    same moment (Section 25, Section 57).
    """
    ceiling = base_backoff_ms * (2 ** (attempt - 1))
    return random.uniform(0, ceiling)  # noqa: S311 -- not security-sensitive


class DeadlineTracker:
    """Tracks the remaining time in a request's global deadline across
    multiple retry attempts, using a monotonic clock."""

    def __init__(self, total_deadline_ms: int, clock: callable = None) -> None:
        import time

        self._clock = clock or time.monotonic
        self._start = self._clock()
        self._total_deadline_s = total_deadline_ms / 1000.0

    def remaining_seconds(self) -> float:
        elapsed = self._clock() - self._start
        return max(0.0, self._total_deadline_s - elapsed)

    def has_budget_for(self, planned_backoff_ms: float) -> bool:
        return self.remaining_seconds() > (planned_backoff_ms / 1000.0)


async def sleep_backoff(delay_ms: float) -> None:
    await asyncio.sleep(delay_ms / 1000.0)


def classify_http_status(status_code: int) -> FailureKind:
    if status_code == 502:
        return FailureKind.HTTP_502
    if status_code == 503:
        return FailureKind.HTTP_503
    if status_code == 504:
        return FailureKind.HTTP_504
    if 400 <= status_code < 500:
        return FailureKind.HTTP_4XX
    if 500 <= status_code < 600:
        return FailureKind.HTTP_OTHER_5XX
    return FailureKind.SUCCESS
