"""I3B.a: the gateway's `GET /metrics` - operator-only, never public.

Mounted by the composition root like every track router (`register(app, rt)`, r1 R44);
that wiring is an integration request, nothing mounts it today. Two gates, both kept, so
losing one layer is not an exposure:

* the edge: Caddy (I2B) answers `/metrics` itself and never proxies it;
* this route: it serves only a **direct loopback** peer - address 127.0.0.1 or ::1 and no
  proxy header. A request relayed by Caddy carries `X-Forwarded-For` (Caddy always sets
  it), so without the edge rule it still gets the same 404 an unknown path gets.

The operator reads it on the host (`curl -s 127.0.0.1:8001/metrics` over SSM) or through
the alert evaluator (`python -m infrx.observe.alerts`). Host gauges are read at scrape
time, in a thread: `nvidia-smi` may take seconds and must not stall the event loop.
"""
from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .host import collect_host
from .metrics import CONTENT_TYPE, Registry, record_pool

PATH = "/metrics"
LOOPBACK = frozenset({"127.0.0.1", "::1"})
PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
                 "x-real-ip", "via")
# Mount names are labels, paths are configuration; the root is the one every host has.
DEFAULT_DISKS = {"root": "/"}


def is_direct_loopback(request: Request) -> bool:
    peer = request.client.host if request.client else ""
    return peer in LOOPBACK and not any(name in request.headers for name in PROXY_HEADERS)


def register(app, rt):
    disks = getattr(rt, "metrics_disks", None) or DEFAULT_DISKS
    if getattr(rt, "metrics", None) is None:
        rt.metrics = Registry("gateway", mounts=disks)

    @app.get(PATH, include_in_schema=False)
    async def metrics(request: Request):
        if not is_direct_loopback(request):
            # Byte-identical to FastAPI's own answer for a path that does not exist.
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        rt.metrics.set("infrx_inflight_requests", getattr(rt, "inflight", 0))
        limit = getattr(getattr(rt, "settings", None), "max_inflight", None)
        if limit is not None:
            rt.metrics.set("infrx_inflight_limit", limit)
        pool = getattr(getattr(rt, "lifetime", None), "pool", None)
        if pool is not None:                  # WR-I8-2: the stores' pool, read at scrape
            record_pool(rt.metrics, pool.pop_stats())
        await asyncio.to_thread(collect_host, rt.metrics, disks)
        return PlainTextResponse(rt.metrics.render(), media_type=CONTENT_TYPE)

    return metrics
