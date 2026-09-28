"""WR-I2L-2: the Lab control service, `uvicorn --factory infrx.lab.control.app:create_app`
on 127.0.0.1:8003 (`infra/lab/app/lab.json` `control`).

A thin FastAPI factory, separate from the consumer gateway: `/readyz` (200 while its database
answers `infrx.now()`, else 503) and ONLY the Lab routers, so the gateway keeps LAB_CONTROL off
while this unit serves them. The Lab routers (`routes/lab_control.py`, `lab_traces.py`) are not
on this lane's base: none is mounted here yet (WR-I2L-2b composes them on the merged tree).
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def _store():
    """The control service's own database login (`INFRX_LAB_DATABASE_URL`, never the
    runtime's `DATABASE_URL`)."""
    from ...state.jobstore import connector
    from ...state.lab_control import PgControlStore
    return PgControlStore(connector(os.environ["INFRX_LAB_DATABASE_URL"]))


def create_app() -> FastAPI:
    store = _store()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/readyz")
    async def readyz():
        try:
            await store.db_now()
        except Exception:                     # noqa: BLE001 - the reason may carry the DSN
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    return app
