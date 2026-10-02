"""One API instance in its own process for `test_publication_pg`'s race: the operator
publication routes composed as the Lab unit composes L3 (`lab.compose.lab_operations` over
PostgreSQL) with 0060's `PgControlOps`, one approval, the answer as JSON on stdout.

    python -m tests.ap06.racer <dsn> <operator user> <proposal> <source> <serving> <key>
"""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from infrx.contracts import api
from infrx.gateway import control
from infrx.gateway.routes import operator_publication as op
from infrx.lab import compose
from infrx.lab.access import LabAccess
from infrx.state.control_ops import PgControlOps
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore


class Ready:
    """AP-05's receipt for the one source revision this racer approves."""

    def __init__(self, source: str, serving: str) -> None:
        self.receipt_ = op.ReadinessReceipt(deployment_revision_id=source,
                                            serving_version_id=serving, ready=True,
                                            checked_at="2026-10-02T00:00:00Z")

    async def receipt(self, deployment_revision_id):
        ready = self.receipt_
        return ready if deployment_revision_id == ready.deployment_revision_id else None


async def main(dsn: str, user: str, proposal: str, source: str, serving: str, key: str) -> dict:
    connect = connector(dsn)
    pub = op.Publication(compose.lab_operations(connect, LabAccess(PgAccessStore(connect))),
                         PgControlOps(connect), Ready(source, serving))
    app = FastAPI()
    op.register(app, SimpleNamespace(lab_publication=pub, actors=control.StaticActors(
        api.Actor(audience="session", user_id=user, operator=True))))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://api.test", timeout=120) as client:
        r = await client.post(
            f"/operator/v1/publication-proposals/{proposal}/approve",
            headers={"Idempotency-Key": key},
            json={"expected_version": 1, "rate_card_version": f"rc_{key}",
                  "input_rate": "300", "output_rate": "900", "reason": "race"})
    return {"status": r.status_code, "body": r.json()}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(*sys.argv[1:7]))))
