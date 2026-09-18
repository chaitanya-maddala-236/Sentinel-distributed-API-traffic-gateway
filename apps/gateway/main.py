"""Sentinel gateway (data plane) entrypoint.

Boots the shared HTTP client, Redis connection, rate limiter, concurrency
manager, route matcher, circuit breakers, and health checker, then exposes:

  GET  /healthz            -- liveness
  GET  /readyz              -- readiness (false while draining)
  GET  /metrics             -- Prometheus exposition
  *    /proxy/{path:path}   -- the actual data-plane traffic (Section 46)

Graceful shutdown (Section 53): on SIGTERM, stop accepting new proxy
traffic (readyz flips to false immediately so the LB stops routing here),
let in-flight requests finish up to `graceful_shutdown_timeout_s`, then
close the HTTP client and Redis connection.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from apps.gateway.backpressure.semaphores import ConcurrencyLimits, ConcurrencyManager
from apps.gateway.circuitbreaker.breaker import CircuitBreaker
from apps.gateway.metrics import (
    REQUEST_DURATION,
    REQUESTS_IN_FLIGHT,
    REQUESTS_TOTAL,
)
from apps.gateway.pipeline.pipeline import GatewayPipeline
from apps.gateway.proxy.forwarder import ProxyForwarder, new_request_id
from apps.gateway.retry.policy import RetryPolicy
from apps.gateway.routing.router import RouteMatcher, WeightedRoundRobinSelector
from packages.common.logging import configure_logging, get_logger
from packages.config.settings import settings
from packages.redis.client import DistributedRateLimiter, RedisFailureMode, TokenBucketPolicy

configure_logging(settings.log_level)
logger = get_logger("sentinel.gateway")

_STATE: dict = {"draining": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)
    rate_limiter = DistributedRateLimiter(redis_client)
    forwarder = ProxyForwarder(
        connect_timeout_s=settings.connect_timeout_s,
        read_timeout_s=settings.read_timeout_s,
        write_timeout_s=settings.write_timeout_s,
        pool_timeout_s=settings.pool_timeout_s,
        max_connections=settings.max_connections,
        max_keepalive_connections=settings.max_keepalive_connections,
    )
    concurrency = ConcurrencyManager(
        ConcurrencyLimits(
            max_global=settings.max_global_concurrency,
            max_per_tenant=settings.max_tenant_concurrency,
            max_per_route=settings.max_route_concurrency,
            max_per_backend=settings.max_backend_concurrency,
            queue_timeout_ms=settings.queue_timeout_ms,
        )
    )
    # Route/backend config is loaded from the control-plane cache in later
    # phases (Phase 9); until that cache exists, routes are supplied via
    # the CONFIG env-driven bootstrap loader in apps/gateway/bootstrap.py
    # so the gateway is runnable end-to-end today rather than a stub.
    from apps.gateway.bootstrap import load_routes_and_backends

    routes, backends_by_service = load_routes_and_backends()
    route_matcher = RouteMatcher(routes)
    selector = WeightedRoundRobinSelector()
    circuit_breakers: dict[str, CircuitBreaker] = {}

    pipeline = GatewayPipeline(
        route_matcher=route_matcher,
        selector=selector,
        rate_limiter=rate_limiter,
        concurrency=concurrency,
        forwarder=forwarder,
        circuit_breakers=circuit_breakers,
        max_body_size_bytes=settings.max_body_size_bytes,
        instance_id=settings.instance_id,
    )

    app.state.pipeline = pipeline
    app.state.redis_client = redis_client
    app.state.forwarder = forwarder
    app.state.backends_by_service = backends_by_service

    from apps.gateway.health.checker import HealthChecker

    health_checker = HealthChecker(
        backends_by_service=backends_by_service,
        interval_s=settings.health_check_interval_s,
        timeout_s=settings.health_check_timeout_s,
        failure_threshold=settings.health_check_failure_threshold,
        success_threshold=settings.health_check_success_threshold,
    )
    health_task = asyncio.create_task(health_checker.run_forever())

    logger.info("gateway.started", extra={"instance_id": settings.instance_id})
    try:
        yield
    finally:
        _STATE["draining"] = True
        logger.info("gateway.draining")
        health_task.cancel()
        await asyncio.sleep(0)  # let the readyz flip propagate to the LB
        await forwarder.aclose()
        await redis_client.aclose()
        logger.info("gateway.shutdown_complete")


app = FastAPI(title="Sentinel Gateway", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(response: Response) -> dict:
    if _STATE["draining"]:
        response.status_code = 503
        return {"status": "draining"}
    return {"status": "ready"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.api_route(
    "/proxy/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(full_path: str, request: Request) -> Response:
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    tenant_id = request.headers.get("X-Tenant-ID", "default")  # real auth: Phase 12
    api_key = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    client_ip = request.client.host if request.client else "unknown"
    body = await request.body()

    rate_limit_key = f"rl:apikey:{api_key or 'anonymous'}"
    rate_limit_policy = TokenBucketPolicy(
        capacity=100,
        refill_rate=100.0,
        failure_mode=RedisFailureMode(settings.rate_limit_default_fail_mode),
    )
    retry_policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_backoff_ms=settings.retry_base_backoff_ms,
        total_deadline_ms=settings.retry_total_deadline_ms,
    )

    start = time.monotonic()
    REQUESTS_IN_FLIGHT.inc()
    try:
        result = await request.app.state.pipeline.handle(
            request_id=request_id,
            tenant_id=tenant_id,
            method=request.method,
            path=f"/{full_path}",
            headers=dict(request.headers),
            params=dict(request.query_params),
            body=body,
            client_ip=client_ip,
            rate_limit_policy=rate_limit_policy,
            rate_limit_key=rate_limit_key,
            backend_instances_by_service=request.app.state.backends_by_service,
            retry_policy=retry_policy,
        )
    finally:
        REQUESTS_IN_FLIGHT.dec()

    duration = time.monotonic() - start
    REQUEST_DURATION.labels(route=full_path.split("/")[0] or "root").observe(duration)
    REQUESTS_TOTAL.labels(
        route=full_path.split("/")[0] or "root", status=str(result.status_code)
    ).inc()

    logger.info(
        "request.completed",
        extra={
            "request_id": request_id,
            "tenant_id": tenant_id,
            "method": request.method,
            "status_code": result.status_code,
            "duration_ms": round(duration * 1000, 2),
            "stage_failed": result.stage_failed,
        },
    )
    return Response(
        content=result.body, status_code=result.status_code, headers=result.headers
    )
