"""Unit tests for apps/gateway/circuitbreaker/breaker.py.

These run with zero external dependencies -- a fake monotonic clock is
injected so time-based transitions (OPEN -> HALF_OPEN after
open_duration_seconds) are deterministic instead of relying on real
sleeps, per Section 64's requirement to test "closed / opening / open
rejection / half-open / recovery / repeated failure".
"""

from __future__ import annotations

import pytest

from apps.gateway.circuitbreaker.breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_breaker(**overrides) -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock()
    defaults = dict(
        failure_threshold=0.5,
        minimum_requests=10,
        open_duration_seconds=10.0,
        half_open_max_requests=3,
    )
    defaults.update(overrides)
    config = CircuitBreakerConfig(**defaults)
    breaker = CircuitBreaker("test-backend", config=config, clock=clock)
    return breaker, clock


def test_starts_closed():
    breaker, _ = make_breaker()
    assert breaker.state == CircuitState.CLOSED
    breaker.before_request()  # should not raise


def test_stays_closed_below_minimum_requests():
    breaker, _ = make_breaker(minimum_requests=10)
    for _ in range(9):
        breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED


def test_opens_when_failure_rate_reaches_threshold():
    breaker, _ = make_breaker(minimum_requests=10, failure_threshold=0.5)
    for _ in range(5):
        breaker.record_success()
    for _ in range(5):
        breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_does_not_open_below_threshold():
    breaker, _ = make_breaker(minimum_requests=10, failure_threshold=0.5)
    for _ in range(7):
        breaker.record_success()
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED


def test_open_circuit_rejects_requests():
    breaker, _ = make_breaker(minimum_requests=2, failure_threshold=0.5)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_request()


def test_transitions_to_half_open_after_open_duration():
    breaker, clock = make_breaker(
        minimum_requests=2, failure_threshold=0.5, open_duration_seconds=10.0
    )
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    clock.advance(5.0)
    assert breaker.state == CircuitState.OPEN  # not yet

    clock.advance(6.0)
    assert breaker.state == CircuitState.HALF_OPEN


def test_half_open_closes_after_enough_successful_probes():
    breaker, clock = make_breaker(
        minimum_requests=2,
        failure_threshold=0.5,
        open_duration_seconds=1.0,
        half_open_max_requests=3,
    )
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(2.0)
    assert breaker.state == CircuitState.HALF_OPEN

    for _ in range(3):
        breaker.before_request()
        breaker.record_success()

    assert breaker.state == CircuitState.CLOSED


def test_half_open_reopens_on_any_probe_failure():
    breaker, clock = make_breaker(
        minimum_requests=2,
        failure_threshold=0.5,
        open_duration_seconds=1.0,
        half_open_max_requests=3,
    )
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(2.0)
    assert breaker.state == CircuitState.HALF_OPEN

    breaker.before_request()
    breaker.record_success()
    breaker.before_request()
    breaker.record_failure()  # one probe fails -> reopen immediately

    assert breaker.state == CircuitState.OPEN


def test_half_open_limits_concurrent_probes():
    breaker, clock = make_breaker(
        minimum_requests=2,
        failure_threshold=0.5,
        open_duration_seconds=1.0,
        half_open_max_requests=2,
    )
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(2.0)
    assert breaker.state == CircuitState.HALF_OPEN

    breaker.before_request()
    breaker.before_request()
    with pytest.raises(CircuitOpenError):
        breaker.before_request()  # third concurrent probe rejected


def test_reopening_resets_outcome_window():
    breaker, clock = make_breaker(
        minimum_requests=2, failure_threshold=0.5, open_duration_seconds=1.0
    )
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(2.0)
    breaker.before_request()
    breaker.record_success()
    breaker.before_request()
    breaker.record_success()
    breaker.before_request()
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED

    # A single failure right after closing should not immediately reopen
    # since the outcome window was cleared on close.
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
