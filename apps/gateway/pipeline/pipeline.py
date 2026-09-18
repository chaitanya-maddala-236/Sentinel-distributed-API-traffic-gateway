"""The explicit request pipeline described in Section 5:

Request -> Request ID -> Auth -> Tenant ID -> Route match -> Size check ->
Rate limit -> Concurrency limit -> Circuit breaker -> Health check ->
Retry eligibility -> Backend selection -> Timeout -> Proxy -> Response
classification -> Metrics -> Response.

Every stage is a small, independently testable function/method; this
module's job is only to sequence them and turn each failure mode into the
right HTTP response. It intentionally does not hide stage boundaries
behind a monolithic try/except -- each `except` below corresponds to
exactly one named stage in Section 5 so the failure -> response mapping
stays traceable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from apps.gateway.backpressure.semaphores import BackpressureRejected, ConcurrencyManager
from apps.gateway.circuitbreaker.breaker import CircuitBreaker, CircuitOpenError
from apps.gateway.proxy.forwarder import ProxyForwarder
from apps.gateway.retry.policy import (
    DeadlineTracker,
    RetryPolicy,
    classify_http_status,
    compute_backoff_ms,
    is_retryable,
    sleep_backoff,
)
from apps.gateway.retry.policy import FailureKind
from apps.gateway.routing.router import (
    BackendInstance,
    NoHealthyBackendError,
    RouteDefinition,
    RouteMatcher,
    RouteNotFoundError,
    WeightedRoundRobinSelector,
)
from packages.redis.client import DistributedRateLimiter, RateLimitDecision, TokenBucketPolicy


@dataclass
class PipelineResult:
    status_code: int
    headers: dict[str, str]
    body: bytes
    stage_failed: str | None = None


class RequestTooLargeError(Exception):
    pass


class GatewayPipeline:
    """Wires together rate limiting, backpressure, circuit breaking, retry,
    routing and proxying for one inbound request. One instance is shared
    across requests; per-request state lives entirely in local variables.
    """

    def __init__(
        self,
        *,
        route_matcher: RouteMatcher,
        selector: WeightedRoundRobinSelector,
        rate_limiter: DistributedRateLimiter,
        concurrency: ConcurrencyManager,
        forwarder: ProxyForwarder,
        circuit_breakers: dict[str, CircuitBreaker],
        max_body_size_bytes: int,
        instance_id: str,
    ) -> None:
        self._routes = route_matcher
        self._selector = selector
        self._rate_limiter = rate_limiter
        self._concurrency = concurrency
        self._forwarder = forwarder
        self._breakers = circuit_breakers
        self._max_body_size_bytes = max_body_size_bytes
        self._instance_id = instance_id

    def _breaker_for(self, backend_service_id: str) -> CircuitBreaker:
        breaker = self._breakers.get(backend_service_id)
        if breaker is None:
            breaker = CircuitBreaker(name=backend_service_id)
            self._breakers[backend_service_id] = breaker
        return breaker

    async def handle(
        self,
        *,
        request_id: str,
        tenant_id: str,
        method: str,
        path: str,
        headers: dict[str, str],
        params: dict[str, str],
        body: bytes,
        client_ip: str,
        rate_limit_policy: TokenBucketPolicy,
        rate_limit_key: str,
        backend_instances_by_service: dict[str, list[BackendInstance]],
        retry_policy: RetryPolicy,
    ) -> PipelineResult:
        # --- Request size validation ---
        if len(body) > self._max_body_size_bytes:
            return PipelineResult(
                413,
                {"X-Request-ID": request_id},
                b'{"error":{"code":"PAYLOAD_TOO_LARGE"}}',
                stage_failed="request_size_validation",
            )

        # --- Route matching ---
        try:
            route: RouteDefinition = self._routes.match(tenant_id, method, path)
        except RouteNotFoundError:
            return PipelineResult(
                404,
                {"X-Request-ID": request_id},
                b'{"error":{"code":"ROUTE_NOT_FOUND"}}',
                stage_failed="route_matching",
            )

        # --- Rate limit ---
        decision: RateLimitDecision = await self._rate_limiter.check_token_bucket(
            rate_limit_key, rate_limit_policy
        )
        if not decision.allowed:
            retry_after_s = max(1, decision.retry_after_ms // 1000)
            return PipelineResult(
                429,
                {
                    "X-Request-ID": request_id,
                    "Retry-After": str(retry_after_s),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": str(decision.remaining),
                },
                b'{"error":{"code":"RATE_LIMITED","message":"Rate limit exceeded"}}',
                stage_failed="rate_limit",
            )

        # --- Concurrency / backpressure ---
        try:
            async with self._concurrency.acquire_all(
                tenant_id=tenant_id, route_id=route.id, backend_id=route.backend_service_id
            ):
                return await self._dispatch_with_resilience(
                    request_id=request_id,
                    route=route,
                    method=method,
                    headers=headers,
                    params=params,
                    body=body,
                    client_ip=client_ip,
                    backend_instances_by_service=backend_instances_by_service,
                    retry_policy=retry_policy,
                )
        except BackpressureRejected as exc:
            return PipelineResult(
                503,
                {"X-Request-ID": request_id, "Retry-After": "1"},
                b'{"error":{"code":"OVERLOADED","message":"Server is at capacity"}}',
                stage_failed=f"backpressure:{exc.scope}",
            )

    async def _dispatch_with_resilience(
        self,
        *,
        request_id: str,
        route: RouteDefinition,
        method: str,
        headers: dict[str, str],
        params: dict[str, str],
        body: bytes,
        client_ip: str,
        backend_instances_by_service: dict[str, list[BackendInstance]],
        retry_policy: RetryPolicy,
    ) -> PipelineResult:
        breaker = self._breaker_for(route.backend_service_id)
        deadline = DeadlineTracker(retry_policy.total_deadline_ms)
        attempt = 0
        last_error_body = b'{"error":{"code":"BAD_GATEWAY"}}'
        last_status = 502

        while attempt < retry_policy.max_attempts:
            attempt += 1
            try:
                breaker.before_request()
            except CircuitOpenError:
                return PipelineResult(
                    503,
                    {"X-Request-ID": request_id},
                    b'{"error":{"code":"CIRCUIT_OPEN","message":"Backend circuit is open"}}',
                    stage_failed="circuit_breaker",
                )

            try:
                instances = backend_instances_by_service.get(route.backend_service_id, [])
                instance = self._selector.select(route.backend_service_id, instances)
            except NoHealthyBackendError:
                breaker.record_failure()
                return PipelineResult(
                    503,
                    {"X-Request-ID": request_id},
                    b'{"error":{"code":"NO_HEALTHY_BACKEND"}}',
                    stage_failed="backend_selection",
                )

            remaining_s = deadline.remaining_seconds()
            if remaining_s <= 0:
                breaker.record_failure()
                break

            per_attempt_timeout = min(remaining_s, route.timeout_ms / 1000.0)
            target_url = f"{instance.url}{route.path_pattern.rstrip('*')}"

            try:
                response = await self._forwarder.forward(
                    method=method,
                    target_url=target_url,
                    headers=headers,
                    params=params,
                    body=body,
                    request_id=request_id,
                    instance_id=self._instance_id,
                    client_ip=client_ip,
                    per_attempt_timeout_s=per_attempt_timeout,
                )
                failure_kind = classify_http_status(response.status_code)
                if failure_kind == FailureKind.SUCCESS or response.status_code < 500:
                    if response.status_code < 500:
                        breaker.record_success()
                    return PipelineResult(
                        response.status_code,
                        {"X-Request-ID": request_id, **dict(response.headers)},
                        response.content,
                    )

                breaker.record_failure()
                last_status = response.status_code
                last_error_body = response.content

            except (httpx.ConnectError, httpx.ConnectTimeout):
                breaker.record_failure()
                failure_kind = FailureKind.CONNECTION_ERROR
            except httpx.TimeoutException:
                breaker.record_failure()
                failure_kind = FailureKind.TIMEOUT

            if not is_retryable(
                method, failure_kind, retry_policy, circuit_is_open=breaker.state.value == "OPEN"
            ):
                break
            if attempt >= retry_policy.max_attempts:
                break

            backoff_ms = compute_backoff_ms(attempt, retry_policy.base_backoff_ms)
            if not deadline.has_budget_for(backoff_ms):
                break
            await sleep_backoff(backoff_ms)

        return PipelineResult(
            last_status,
            {"X-Request-ID": request_id},
            last_error_body,
            stage_failed="proxy",
        )
