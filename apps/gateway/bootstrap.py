"""Temporary bootstrap loader for routes and backend instances.

Phase 9 (config cache) replaces this with a Postgres-backed cache refreshed
via Redis pub/sub, per Section 36-37. Until that phase is implemented,
routes are declared in `infra/docker/routes.yaml` so the gateway is fully
runnable end-to-end against the demo services today -- this is explicitly
a placeholder, not a substitute for the real control-plane cache, and it
is called out as such here and in docs/architecture.md so it's never
mistaken for the finished Phase 9 mechanism.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from apps.gateway.routing.router import BackendInstance, InstanceStatus, RouteDefinition

_DEFAULT_CONFIG_PATH = os.environ.get(
    "SENTINEL_ROUTES_FILE", "/app/infra/docker/routes.yaml"
)


def load_routes_and_backends(
    config_path: str | None = None,
) -> tuple[list[RouteDefinition], dict[str, list[BackendInstance]]]:
    path = Path(config_path or _DEFAULT_CONFIG_PATH)
    if not path.exists():
        return [], {}

    data = yaml.safe_load(path.read_text()) or {}

    routes = [
        RouteDefinition(
            id=r["id"],
            tenant_id=r["tenant_id"],
            name=r["name"],
            path_pattern=r["path_pattern"],
            http_methods=r["http_methods"],
            backend_service_id=r["backend_service_id"],
            timeout_ms=r.get("timeout_ms", 2000),
            priority=r.get("priority", "NORMAL"),
        )
        for r in data.get("routes", [])
    ]

    backends_by_service: dict[str, list[BackendInstance]] = {}
    for svc in data.get("backend_services", []):
        instances = [
            BackendInstance(
                id=inst["id"],
                host=inst["host"],
                port=inst["port"],
                weight=inst.get("weight", 1),
                status=InstanceStatus.UNKNOWN,
            )
            for inst in svc.get("instances", [])
        ]
        backends_by_service[svc["id"]] = instances

    return routes, backends_by_service
