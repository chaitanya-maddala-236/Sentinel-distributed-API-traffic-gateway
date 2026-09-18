"""Background health-check loop (Section 28-29).

Runs on an interval per backend instance, flips HEALTHY/UNHEALTHY based on
consecutive failure/success thresholds (not a single flaky check), and
supports a DRAINING state so an instance being removed finishes in-flight
work before being excluded entirely.
"""

from __future__ import annotations

import asyncio

import httpx

from apps.gateway.routing.router import BackendInstance, InstanceStatus
from packages.common.logging import get_logger

logger = get_logger("sentinel.health")


class HealthChecker:
    def __init__(
        self,
        *,
        backends_by_service: dict[str, list[BackendInstance]],
        interval_s: float,
        timeout_s: float,
        failure_threshold: int,
        success_threshold: int,
        health_path: str = "/health",
    ) -> None:
        self._backends_by_service = backends_by_service
        self._interval_s = interval_s
        self._timeout_s = timeout_s
        self._failure_threshold = failure_threshold
        self._success_threshold = success_threshold
        self._health_path = health_path
        self._consecutive_successes: dict[str, int] = {}
        self._consecutive_failures: dict[str, int] = {}

    async def check_once(self, client: httpx.AsyncClient) -> None:
        for instances in self._backends_by_service.values():
            for instance in instances:
                if instance.status == InstanceStatus.DRAINING:
                    continue  # draining instances are not health-polled back to HEALTHY
                await self._check_instance(client, instance)

    async def _check_instance(self, client: httpx.AsyncClient, instance: BackendInstance) -> None:
        url = f"{instance.url}{self._health_path}"
        try:
            resp = await client.get(url, timeout=self._timeout_s)
            healthy = resp.status_code < 500
        except httpx.HTTPError:
            healthy = False

        if healthy:
            self._consecutive_successes[instance.id] = (
                self._consecutive_successes.get(instance.id, 0) + 1
            )
            self._consecutive_failures[instance.id] = 0
            if self._consecutive_successes[instance.id] >= self._success_threshold:
                if instance.status != InstanceStatus.HEALTHY:
                    logger.info("backend.became_healthy", extra={"instance": instance.id})
                instance.status = InstanceStatus.HEALTHY
        else:
            self._consecutive_failures[instance.id] = (
                self._consecutive_failures.get(instance.id, 0) + 1
            )
            self._consecutive_successes[instance.id] = 0
            instance.failure_count += 1
            if self._consecutive_failures[instance.id] >= self._failure_threshold:
                if instance.status != InstanceStatus.UNHEALTHY:
                    logger.warning("backend.became_unhealthy", extra={"instance": instance.id})
                instance.status = InstanceStatus.UNHEALTHY

    async def run_forever(self) -> None:
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    await self.check_once(client)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 -- health loop must never die
                    logger.exception("health_checker.iteration_failed")
                await asyncio.sleep(self._interval_s)
