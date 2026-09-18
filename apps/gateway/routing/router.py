"""Route matching and weighted round-robin backend selection with health
and draining exclusion (Section 27-29, 64)."""

from __future__ import annotations

import fnmatch
import itertools
import random
from dataclasses import dataclass, field
from enum import Enum


class InstanceStatus(str, Enum):
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"
    DRAINING = "DRAINING"
    UNKNOWN = "UNKNOWN"


@dataclass
class BackendInstance:
    id: str
    host: str
    port: int
    weight: int = 1
    status: InstanceStatus = InstanceStatus.UNKNOWN
    failure_count: int = 0

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def is_routable(self) -> bool:
        return self.status == InstanceStatus.HEALTHY


@dataclass
class RouteDefinition:
    id: str
    tenant_id: str
    name: str
    path_pattern: str
    http_methods: list[str]
    backend_service_id: str
    timeout_ms: int = 2000
    priority: str = "NORMAL"
    enabled: bool = True


class NoHealthyBackendError(Exception):
    pass


class RouteNotFoundError(Exception):
    pass


class RouteMatcher:
    """Matches (tenant, method, path) against configured routes.

    path_pattern uses glob-style wildcards, e.g. `/api/users/*`, which is
    sufficient for the examples in Section 8 without pulling in a full
    templating/regex routing layer prematurely (Section 2: non-goals).
    """

    def __init__(self, routes: list[RouteDefinition]) -> None:
        # group by tenant for fast lookup; still linear-scan per tenant,
        # which is fine at the route-count scale this project targets.
        self._by_tenant: dict[str, list[RouteDefinition]] = {}
        for r in routes:
            self._by_tenant.setdefault(r.tenant_id, []).append(r)

    def match(self, tenant_id: str, method: str, path: str) -> RouteDefinition:
        candidates = self._by_tenant.get(tenant_id, [])
        for route in candidates:
            if not route.enabled:
                continue
            if method.upper() not in [m.upper() for m in route.http_methods]:
                continue
            if fnmatch.fnmatch(path, route.path_pattern):
                return route
        raise RouteNotFoundError(f"no route for {tenant_id} {method} {path}")


class WeightedRoundRobinSelector:
    """Weighted round-robin over the HEALTHY, non-DRAINING instances of a
    backend service. Rebuilds its weighted cycle whenever the instance set
    changes so newly-healthy/unhealthy instances take effect immediately.
    """

    def __init__(self) -> None:
        self._cycles: dict[str, itertools.cycle] = {}
        self._last_seen_signature: dict[str, tuple] = {}

    def select(self, service_id: str, instances: list[BackendInstance]) -> BackendInstance:
        routable = [i for i in instances if i.is_routable]
        if not routable:
            raise NoHealthyBackendError(service_id)

        signature = tuple((i.id, i.weight) for i in routable)
        if self._last_seen_signature.get(service_id) != signature:
            expanded = []
            for inst in routable:
                expanded.extend([inst] * max(1, inst.weight))
            random.shuffle(expanded)  # noqa: S311 -- load distribution, not security
            self._cycles[service_id] = itertools.cycle(expanded)
            self._last_seen_signature[service_id] = signature

        return next(self._cycles[service_id])
