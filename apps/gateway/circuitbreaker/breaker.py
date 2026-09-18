"""Per-backend circuit breaker state machine.

CLOSED -> OPEN happens when, within a rolling window of the last
`minimum_requests` outcomes, the failure rate reaches `failure_threshold`.
OPEN -> HALF_OPEN happens automatically once `open_duration` has elapsed.
HALF_OPEN allows a bounded number of probe requests through; if enough of
them succeed the breaker closes, otherwise it reopens (Section 20-23).

This class is deliberately Redis-free and synchronous-clock-injectable so
it can be fully exercised in unit tests (see tests/unit/test_circuit_breaker.py)
without any external dependency. A separate coordinator (not yet built —
see docs/adr/ADR-005-circuit-breaker-design.md) is responsible for
optionally broadcasting state transitions to other Sentinel instances via
Redis pub/sub so a circuit tripped by one instance's observations is
visible to the others; each instance's local breaker remains authoritative
for its own traffic in the meantime, which is a deliberate consistency
tradeoff documented in that ADR rather than a hidden gap.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    """Raised by `CircuitBreaker.before_request()` when the circuit is open."""


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: float = 0.5  # fraction, e.g. 0.5 == 50%
    minimum_requests: int = 20
    open_duration_seconds: float = 10.0
    half_open_max_requests: int = 3
    # window of recent outcomes considered for the failure-rate calculation
    rolling_window_size: int = 100


@dataclass
class _Outcome:
    success: bool
    at: float


class CircuitBreaker:
    """One instance per backend service (or per backend instance, per your
    routing granularity — the caller decides what `name` identifies)."""

    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
        clock: callable = time.monotonic,
    ) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._outcomes: deque[_Outcome] = deque(maxlen=self.config.rolling_window_size)
        self._opened_at: float | None = None
        self._half_open_in_flight = 0
        self._half_open_successes = 0
        self._half_open_failures = 0

    @property
    def state(self) -> CircuitState:
        # Lazily transition OPEN -> HALF_OPEN based on elapsed time; keeps
        # the breaker correct even if no request arrives to trigger it.
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            elapsed = self._clock() - self._opened_at
            if elapsed >= self.config.open_duration_seconds:
                self._transition_to_half_open()
        return self._state

    def before_request(self) -> None:
        """Call before dispatching a request to the backend.

        Raises CircuitOpenError if the request must fail fast instead of
        being sent. In HALF_OPEN, admits at most `half_open_max_requests`
        concurrent probes and rejects the rest.
        """
        current = self.state
        if current == CircuitState.OPEN:
            raise CircuitOpenError(self.name)
        if current == CircuitState.HALF_OPEN:
            if self._half_open_in_flight >= self.config.half_open_max_requests:
                raise CircuitOpenError(self.name)
            self._half_open_in_flight += 1

    def record_success(self) -> None:
        now = self._clock()
        self._outcomes.append(_Outcome(success=True, at=now))
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_in_flight = max(0, self._half_open_in_flight - 1)
            self._half_open_successes += 1
            if self._half_open_successes >= self.config.half_open_max_requests:
                self._transition_to_closed()

    def record_failure(self) -> None:
        now = self._clock()
        self._outcomes.append(_Outcome(success=False, at=now))
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_in_flight = max(0, self._half_open_in_flight - 1)
            self._half_open_failures += 1
            # Any failure during a probe immediately reopens the circuit --
            # we don't average across probes here, because letting a
            # backend that is still failing continue being probed at a
            # loose threshold defeats the point of fast failure.
            self._transition_to_open()
            return
        if self._state == CircuitState.CLOSED:
            self._maybe_open()

    def _maybe_open(self) -> None:
        if len(self._outcomes) < self.config.minimum_requests:
            return
        failures = sum(1 for o in self._outcomes if not o.success)
        failure_rate = failures / len(self._outcomes)
        if failure_rate >= self.config.failure_threshold:
            self._transition_to_open()

    def _transition_to_open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._half_open_in_flight = 0
        self._half_open_successes = 0
        self._half_open_failures = 0

    def _transition_to_half_open(self) -> None:
        self._state = CircuitState.HALF_OPEN
        self._half_open_in_flight = 0
        self._half_open_successes = 0
        self._half_open_failures = 0

    def _transition_to_closed(self) -> None:
        self._state = CircuitState.CLOSED
        self._opened_at = None
        self._outcomes.clear()
        self._half_open_in_flight = 0
        self._half_open_successes = 0
        self._half_open_failures = 0
