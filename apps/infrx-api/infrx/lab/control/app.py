"""WR-I2L-2: the Lab control service, `uvicorn --factory infrx.lab.control.app:create_app`
on 127.0.0.1:8003 (`infra/lab/app/lab.json` `control`).

A thin FastAPI factory, separate from the consumer gateway: `/readyz` (200 while its database
answers `infrx.now()`, else 503) and ONLY the Lab routers, so the gateway keeps LAB_CONTROL off
while this unit serves them (WR-I2L-2b). Composed from its own `INFRX_LAB_*` settings - never
the runtime's `DATABASE_URL` or service-role key - the way `pilot._lab` composes the gateway's:
`lab_control` over L3 (`Operations`), and `lab_traces` only when `CLICKHOUSE_URL` and
`S3_TRACE_BUCKET` are set (one without the other is refused by `pilot._lab_traces`).
A missing `INFRX_LAB_*` setting refuses startup by name (`RuntimeMisconfigured`).
"""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ...config import RuntimeMisconfigured
from ...contracts import errors

MODE = "lab-control"
DATABASE_URL, SUPABASE_URL = "INFRX_LAB_DATABASE_URL", "INFRX_LAB_SUPABASE_URL"
SUPABASE_KEY = "INFRX_LAB_SUPABASE_ANON_KEY"      # the publishable key GoTrue wants as `apikey`
REQUIRED = (DATABASE_URL, SUPABASE_URL, SUPABASE_KEY)


class NoEngine:
    """Until G/W's engine smoke adapter exists (WR-L3-2), a smoke answers 503."""

    async def smoke(self, serving, deployment) -> bool:
        raise errors.DependencyUnavailable("the engine smoke adapter is not wired (WR-L3-2)")


def _settings() -> dict[str, str]:
    values = {name: os.environ.get(name, "").strip() for name in REQUIRED}
    missing = tuple(name for name, value in values.items() if not value)
    if missing:
        raise RuntimeMisconfigured(MODE, missing)
    return values


def _store():
    """The control service's own database login (`INFRX_LAB_DATABASE_URL`, never the
    runtime's `DATABASE_URL`)."""
    from ...state.jobstore import connector
    from ...state.lab_control import PgControlStore
    return PgControlStore(connector(os.environ[DATABASE_URL]))


def _compose(lab: dict[str, str], store):
    """`pilot._lab`'s composition on the Lab's own login: L3's operations are the gateway's
    one `lab_operations` (WR-LAB-API-2c); `store` serves `/readyz` only."""
    import httpx

    from ...config import from_env
    from ...gateway.lab_auth import GoTrueSessions
    from ...gateway.pilot import _lab_traces, lab_operations
    from ...gateway.routes.lab_control import LabControl as Routes
    from ...state.jobstore import connector
    from ...state.lab_access import PgAccessStore
    from ..access import LabAccess
    settings, connect = from_env(), connector(lab[DATABASE_URL])
    # ponytail: process-lifetime client, as in `pilot._lab`.
    sessions = GoTrueSessions(httpx.AsyncClient(base_url=lab[SUPABASE_URL].rstrip("/"),
                                                timeout=httpx.Timeout(5, connect=2)),
                              lab[SUPABASE_KEY])
    access = LabAccess(PgAccessStore(connect))
    control = Routes(sessions, access, lab_operations(connect, access))
    pilot = settings.pilot
    traces = _lab_traces(settings, connect, sessions, access) \
        if pilot.clickhouse_url.strip() or pilot.s3_trace_bucket.strip() else None
    return SimpleNamespace(settings=settings, clock=time.time), control, traces


def create_app() -> FastAPI:
    lab = _settings()
    store = _store()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/readyz")
    async def readyz():
        try:
            await store.db_now()
        except Exception:                     # noqa: BLE001 - the reason may carry the DSN
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    from ...gateway.routes import lab_control, lab_traces
    rt, control, traces = _compose(lab, store)
    lab_control.register(app, rt, control)
    lab_traces.register(app, rt, traces)
    return app
