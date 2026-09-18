"""Shared pytest fixtures.

Unit tests under tests/unit/ use no fixtures from here -- they are pure
logic tests with injected fake clocks (see each test module's own
docstring). Fixtures here are for tests/integration/ once Phase 14 stands
up real Redis/Postgres via testcontainers.
"""
