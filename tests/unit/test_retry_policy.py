"""Unit tests for apps/gateway/retry/policy.py (Section 64: retryable
failure, non-retryable failure, deadline exhaustion, jitter, idempotency).
Pure stdlib -- no external services required.
"""

from __future__ import annotations

from apps.gateway.retry.policy import (
    DeadlineTracker,
    FailureKind,
    RetryPolicy,
    classify_http_status,
    compute_backoff_ms,
    is_retryable,
)


def test_get_timeout_is_retryable():
    policy = RetryPolicy()
    assert is_retryable("GET", FailureKind.TIMEOUT, policy, circuit_is_open=False)


def test_post_not_retried_by_default():
    policy = RetryPolicy()
    assert not is_retryable("POST", FailureKind.HTTP_503, policy, circuit_is_open=False)


def test_post_retried_when_explicitly_allowed():
    policy = RetryPolicy(allow_non_idempotent=True)
    assert is_retryable("POST", FailureKind.HTTP_503, policy, circuit_is_open=False)


def test_4xx_not_retryable():
    policy = RetryPolicy()
    assert not is_retryable("GET", FailureKind.HTTP_4XX, policy, circuit_is_open=False)


def test_open_circuit_blocks_retry_even_if_otherwise_retryable():
    policy = RetryPolicy()
    assert not is_retryable("GET", FailureKind.TIMEOUT, policy, circuit_is_open=True)


def test_classify_http_status():
    assert classify_http_status(502) == FailureKind.HTTP_502
    assert classify_http_status(503) == FailureKind.HTTP_503
    assert classify_http_status(504) == FailureKind.HTTP_504
    assert classify_http_status(404) == FailureKind.HTTP_4XX
    assert classify_http_status(500) == FailureKind.HTTP_OTHER_5XX
    assert classify_http_status(200) == FailureKind.SUCCESS


def test_backoff_grows_exponentially_and_stays_within_ceiling():
    for attempt in range(1, 6):
        ceiling = 100 * (2 ** (attempt - 1))
        samples = [compute_backoff_ms(attempt, 100) for _ in range(200)]
        assert all(0 <= s <= ceiling for s in samples)
        # full jitter means not every sample is the same value
        assert len(set(round(s) for s in samples)) > 1


def test_deadline_tracker_remaining_time_decreases():
    ticks = iter([0.0, 0.5, 1.9, 2.5])
    tracker = DeadlineTracker(2000, clock=lambda: next(ticks))
    # first call to __init__ consumed one tick (0.0) as start
    assert round(tracker.remaining_seconds(), 6) == 1.5  # at 0.5s elapsed
    assert round(tracker.remaining_seconds(), 6) == 0.1  # at 1.9s elapsed
    assert round(tracker.remaining_seconds(), 6) == 0.0  # at 2.5s elapsed -> clamped to 0


def test_deadline_tracker_has_budget_for():
    ticks = iter([0.0, 0.05])
    tracker = DeadlineTracker(200, clock=lambda: next(ticks))
    assert tracker.has_budget_for(planned_backoff_ms=100)  # 150ms left > 100ms needed


def test_deadline_tracker_rejects_when_insufficient_budget():
    ticks = iter([0.0, 0.19])
    tracker = DeadlineTracker(200, clock=lambda: next(ticks))
    assert not tracker.has_budget_for(planned_backoff_ms=50)  # only ~10ms left
