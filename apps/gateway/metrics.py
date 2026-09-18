"""Prometheus metrics (Section 41-42).

Deliberately low-cardinality: labels are route-name/status-class/backend
identifiers, never request_id, user_id, or raw URLs (Section 41 explicitly
forbids this -- unbounded label cardinality is a classic way to take down
a Prometheus server).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REQUESTS_TOTAL = Counter(
    "sentinel_requests_total", "Total proxied requests", ["route", "status"]
)
REQUEST_DURATION = Histogram(
    "sentinel_request_duration_seconds", "End-to-end request duration", ["route"]
)
REQUESTS_IN_FLIGHT = Gauge(
    "sentinel_requests_in_flight", "Requests currently being processed"
)
RATE_LIMIT_REJECTIONS_TOTAL = Counter(
    "sentinel_rate_limit_rejections_total", "Requests rejected by rate limiting", ["scope"]
)
BACKEND_ERRORS_TOTAL = Counter(
    "sentinel_backend_errors_total", "Backend error responses", ["backend"]
)
BACKEND_REQUESTS_TOTAL = Counter(
    "sentinel_backend_requests_total", "Requests sent to a backend", ["backend"]
)
BACKEND_LATENCY = Histogram(
    "sentinel_backend_latency_seconds", "Backend response latency", ["backend"]
)
RETRIES_TOTAL = Counter("sentinel_retries_total", "Retry attempts made", ["route"])
CIRCUIT_BREAKER_STATE = Gauge(
    "sentinel_circuit_breaker_state",
    "Circuit breaker state (0=CLOSED, 1=HALF_OPEN, 2=OPEN)",
    ["backend"],
)
LOAD_SHED_TOTAL = Counter("sentinel_load_shed_total", "Requests shed under overload", ["priority"])
QUEUE_DEPTH = Gauge("sentinel_queue_depth", "Current backpressure queue depth", ["scope"])
HEALTH_CHECK_FAILURES_TOTAL = Counter(
    "sentinel_health_check_failures_total", "Backend health check failures", ["backend"]
)
ACTIVE_WEBSOCKET_CONNECTIONS = Gauge(
    "sentinel_active_websocket_connections", "Active WebSocket connections"
)
