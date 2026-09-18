"""Bounded concurrency and load shedding (Sections 17-19, 60).

Every layer of concurrency control (global, tenant, route, backend) is a
bounded async semaphore with a wait timeout — never an unbounded queue.
A request that cannot get a slot within `queue_timeout_ms` is rejected
with 503 rather than left to wait indefinitely, because an unbounded queue
just turns overload into a latency amplifier instead of preventing it
(Section 19).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import IntEnum


class Priority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2


class BackpressureRejected(Exception):
    """Raised when a bounded-concurrency slot could not be acquired in time."""

    def __init__(self, scope: str, waited_ms: float) -> None:
        self.scope = scope
        self.waited_ms = waited_ms
        super().__init__(f"backpressure rejected on scope={scope} after {waited_ms:.1f}ms")


@dataclass(frozen=True)
class ConcurrencyLimits:
    max_global: int = 1000
    max_per_tenant: int = 100
    max_per_route: int = 200
    max_per_backend: int = 100
    queue_timeout_ms: int = 500


class BoundedConcurrencyGate:
    """A single named bounded gate (e.g. "global", "tenant:acme",
    "backend:payments"). Acquire with a hard wait timeout."""

    def __init__(self, name: str, limit: int, queue_timeout_ms: int) -> None:
        self.name = name
        self.limit = limit
        self.queue_timeout_ms = queue_timeout_ms
        self._semaphore = asyncio.Semaphore(limit)
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @asynccontextmanager
    async def acquire(self):
        loop_time_start = asyncio.get_event_loop().time()
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self.queue_timeout_ms / 1000.0
            )
        except TimeoutError as exc:
            waited_ms = (asyncio.get_event_loop().time() - loop_time_start) * 1000
            raise BackpressureRejected(self.name, waited_ms) from exc
        self._in_flight += 1
        try:
            yield
        finally:
            self._in_flight -= 1
            self._semaphore.release()


class ConcurrencyManager:
    """Owns the global/tenant/route/backend gates and composes them for a
    single request. All four must grant a slot for the request to proceed;
    whichever is exhausted first determines the rejection reason reported
    in metrics/logs (Section 60: tenant fairness, Section 17: layered
    backpressure)."""

    def __init__(self, limits: ConcurrencyLimits) -> None:
        self.limits = limits
        self._global_gate = BoundedConcurrencyGate(
            "global", limits.max_global, limits.queue_timeout_ms
        )
        self._tenant_gates: dict[str, BoundedConcurrencyGate] = {}
        self._route_gates: dict[str, BoundedConcurrencyGate] = {}
        self._backend_gates: dict[str, BoundedConcurrencyGate] = {}

    def _get_or_create(
        self, registry: dict[str, BoundedConcurrencyGate], key: str, limit: int
    ) -> BoundedConcurrencyGate:
        gate = registry.get(key)
        if gate is None:
            gate = BoundedConcurrencyGate(key, limit, self.limits.queue_timeout_ms)
            registry[key] = gate
        return gate

    @asynccontextmanager
    async def acquire_all(self, tenant_id: str, route_id: str, backend_id: str):
        tenant_gate = self._get_or_create(
            self._tenant_gates, f"tenant:{tenant_id}", self.limits.max_per_tenant
        )
        route_gate = self._get_or_create(
            self._route_gates, f"route:{route_id}", self.limits.max_per_route
        )
        backend_gate = self._get_or_create(
            self._backend_gates, f"backend:{backend_id}", self.limits.max_per_backend
        )
        async with self._global_gate.acquire():
            async with tenant_gate.acquire():
                async with route_gate.acquire():
                    async with backend_gate.acquire():
                        yield


class LoadShedder:
    """Sheds LOW-priority traffic first when the gateway is itself near
    saturation. Driven by directly-controllable signals (in-flight count,
    queue depth) rather than CPU percentage alone (Section 18), because
    CPU sampling is noisy and doesn't reliably predict queueing collapse
    the way concurrency/queue-depth thresholds do.
    """

    def __init__(
        self,
        high_watermark_normal: int,
        high_watermark_low: int,
    ) -> None:
        # Above high_watermark_low in-flight requests, shed LOW priority.
        # Above high_watermark_normal, shed LOW and NORMAL, preserving HIGH.
        self.high_watermark_low = high_watermark_low
        self.high_watermark_normal = high_watermark_normal

    def should_shed(self, priority: Priority, current_in_flight: int) -> bool:
        if priority == Priority.HIGH:
            return False
        if priority == Priority.LOW:
            return current_in_flight >= self.high_watermark_low
        # NORMAL
        return current_in_flight >= self.high_watermark_normal
