"""Unit tests for apps/gateway/routing/router.py (Section 64: route
matching, weighted selection, unhealthy exclusion, draining exclusion).
Pure stdlib -- no external services required.
"""

from __future__ import annotations

import pytest

from apps.gateway.routing.router import (
    BackendInstance,
    InstanceStatus,
    NoHealthyBackendError,
    RouteDefinition,
    RouteMatcher,
    RouteNotFoundError,
    WeightedRoundRobinSelector,
)


def make_route(**overrides) -> RouteDefinition:
    defaults = dict(
        id="r1",
        tenant_id="acme",
        name="users",
        path_pattern="/users/*",
        http_methods=["GET"],
        backend_service_id="users-service",
    )
    defaults.update(overrides)
    return RouteDefinition(**defaults)


def test_matches_exact_method_and_glob_path():
    matcher = RouteMatcher([make_route()])
    route = matcher.match("acme", "GET", "/users/42")
    assert route.id == "r1"


def test_no_match_for_wrong_tenant():
    matcher = RouteMatcher([make_route()])
    with pytest.raises(RouteNotFoundError):
        matcher.match("other-tenant", "GET", "/users/42")


def test_no_match_for_wrong_method():
    matcher = RouteMatcher([make_route(http_methods=["GET"])])
    with pytest.raises(RouteNotFoundError):
        matcher.match("acme", "POST", "/users/42")


def test_disabled_route_not_matched():
    matcher = RouteMatcher([make_route(enabled=False)])
    with pytest.raises(RouteNotFoundError):
        matcher.match("acme", "GET", "/users/42")


def test_selector_excludes_unhealthy_instances():
    selector = WeightedRoundRobinSelector()
    instances = [
        BackendInstance(id="a", host="a", port=1, status=InstanceStatus.HEALTHY),
        BackendInstance(id="b", host="b", port=2, status=InstanceStatus.UNHEALTHY),
    ]
    for _ in range(20):
        chosen = selector.select("svc", instances)
        assert chosen.id == "a"


def test_selector_excludes_draining_instances():
    selector = WeightedRoundRobinSelector()
    instances = [
        BackendInstance(id="a", host="a", port=1, status=InstanceStatus.HEALTHY),
        BackendInstance(id="b", host="b", port=2, status=InstanceStatus.DRAINING),
    ]
    for _ in range(20):
        chosen = selector.select("svc", instances)
        assert chosen.id == "a"


def test_selector_raises_when_no_healthy_instances():
    selector = WeightedRoundRobinSelector()
    instances = [BackendInstance(id="a", host="a", port=1, status=InstanceStatus.UNHEALTHY)]
    with pytest.raises(NoHealthyBackendError):
        selector.select("svc", instances)


def test_selector_respects_weight_distribution():
    selector = WeightedRoundRobinSelector()
    instances = [
        BackendInstance(id="heavy", host="a", port=1, weight=3, status=InstanceStatus.HEALTHY),
        BackendInstance(id="light", host="b", port=2, weight=1, status=InstanceStatus.HEALTHY),
    ]
    counts = {"heavy": 0, "light": 0}
    for _ in range(400):
        chosen = selector.select("svc", instances)
        counts[chosen.id] += 1
    # With weight 3:1 over many draws, heavy should clearly outnumber light.
    assert counts["heavy"] > counts["light"] * 2
