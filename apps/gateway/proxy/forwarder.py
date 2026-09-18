"""Reverse-proxy forwarding (Section 30-33, 51-52).

Uses one shared `httpx.AsyncClient` per process with a bounded connection
pool -- never a fresh client per request (Section 51). Hop-by-hop headers
are stripped, and inbound `X-Forwarded-*` / `X-Request-ID` are only
trusted if the caller has been marked as a trusted upstream (e.g. our own
Nginx), otherwise Sentinel generates its own (Section 30, 40).
"""

from __future__ import annotations

import uuid

import httpx

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}


class ProxyForwarder:
    def __init__(
        self,
        *,
        connect_timeout_s: float,
        read_timeout_s: float,
        write_timeout_s: float,
        pool_timeout_s: float,
        max_connections: int,
        max_keepalive_connections: int,
    ) -> None:
        timeout = httpx.Timeout(
            connect=connect_timeout_s,
            read=read_timeout_s,
            write=write_timeout_s,
            pool=pool_timeout_s,
        )
        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )
        self._client = httpx.AsyncClient(timeout=timeout, limits=limits)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _clean_headers(self, headers: httpx.Headers) -> dict[str, str]:
        return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS}

    async def forward(
        self,
        *,
        method: str,
        target_url: str,
        headers: dict[str, str],
        params: dict[str, str] | None,
        body: bytes | None,
        request_id: str,
        instance_id: str,
        client_ip: str,
        per_attempt_timeout_s: float,
    ) -> httpx.Response:
        """Forward one attempt. Timeout here is the *remaining deadline*
        for this attempt, computed by the caller from the global deadline
        (see retry/policy.py DeadlineTracker) -- not a fresh full timeout
        per retry (Section 26)."""
        outbound_headers = self._clean_headers(httpx.Headers(headers))
        outbound_headers["X-Request-ID"] = request_id
        outbound_headers["X-Sentinel-Instance"] = instance_id
        outbound_headers["X-Forwarded-For"] = client_ip
        outbound_headers["X-Forwarded-Proto"] = "https"

        return await self._client.request(
            method=method,
            url=target_url,
            headers=outbound_headers,
            params=params,
            content=body,
            timeout=per_attempt_timeout_s,
        )


def new_request_id() -> str:
    return str(uuid.uuid4())
