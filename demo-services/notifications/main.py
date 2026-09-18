"""Demo backend: notifications-service.

Real endpoints plus simulation endpoints (Section 48-49) so Sentinel's
rate limiting, circuit breaking, and retry behavior can be demonstrated
against genuinely misbehaving backends -- not mocked metrics.
"""

from __future__ import annotations

import asyncio
import os
import random

from fastapi import FastAPI, HTTPException, Response

app = FastAPI(title="demo notifications-service")

# Runtime-mutable failure injection, controlled via /admin/* endpoints so
# the dashboard/demo scripts can actually make this backend misbehave
# (Section 49: "Do not fake gateway metrics. The backend must actually
# misbehave.").
_STATE = {"forced_error_rate": 0.0, "forced_latency_ms": 0, "killed": False}


@app.get("/health")
async def health() -> dict:
    if _STATE["killed"]:
        raise HTTPException(status_code=503, detail="killed")
    return {"status": "healthy"}


@app.get("/api/notifications/{user_id}")
async def get_notifications_item(user_id: str) -> dict:
    await _maybe_misbehave()
    return {"id": user_id, "name": f"user-{user_id}", "service": "notifications-service"}


@app.get("/api/notifications")
async def list_notifications() -> dict:
    await _maybe_misbehave()
    return {"users": [{"id": str(i)} for i in range(1, 6)]}


@app.get("/simulate/500")
async def simulate_500() -> Response:
    return Response(status_code=500, content=b'{"error":"simulated failure"}')


@app.get("/simulate/slow")
async def simulate_slow(delay_ms: int = 2000) -> dict:
    await asyncio.sleep(delay_ms / 1000.0)
    return {"status": "ok", "delayed_ms": delay_ms}


@app.get("/simulate/random")
async def simulate_random(failure_rate: float = 0.3) -> Response:
    if random.random() < failure_rate:  # noqa: S311 -- demo-only chaos injection
        return Response(status_code=503, content=b'{"error":"simulated random failure"}')
    return Response(status_code=200, content=b'{"status":"ok"}')


@app.post("/admin/kill")
async def kill() -> dict:
    _STATE["killed"] = True
    return {"status": "killed"}


@app.post("/admin/restore")
async def restore() -> dict:
    _STATE["killed"] = False
    _STATE["forced_error_rate"] = 0.0
    _STATE["forced_latency_ms"] = 0
    return {"status": "restored"}


@app.post("/admin/error-rate/{rate}")
async def set_error_rate(rate: float) -> dict:
    _STATE["forced_error_rate"] = rate
    return {"forced_error_rate": rate}


@app.post("/admin/latency/{ms}")
async def set_latency(ms: int) -> dict:
    _STATE["forced_latency_ms"] = ms
    return {"forced_latency_ms": ms}


async def _maybe_misbehave() -> None:
    if _STATE["killed"]:
        raise HTTPException(status_code=503, detail="backend killed")
    if _STATE["forced_latency_ms"]:
        await asyncio.sleep(_STATE["forced_latency_ms"] / 1000.0)
    if _STATE["forced_error_rate"] and random.random() < _STATE["forced_error_rate"]:  # noqa: S311
        raise HTTPException(status_code=503, detail="injected failure")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 9001)))
