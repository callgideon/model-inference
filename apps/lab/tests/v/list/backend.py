#!/usr/bin/env python3
"""V1M's real backend for `stack.test.ts`: lab-api's provider trace read (`lab_traces.register`,
R176) served as merged, over the REAL L2 (`LabAccess` on `PgAccessStore`, grants through lab-sql's
RPCs, the LAB-ACCESS world of `tests/l/access/worlds.py`) on the task-local PostgreSQL
(`INFRX_D_TASK=lab-v1m`, port 57513), the registry (`PgServing`), T2I's projection and T3's
deletion ledger on the pinned ClickHouse (`infrx-t2i-clickhouse`, the t2i block 57540, a database
of its own per run). Objects are in memory. Test-only: the bearer-token-per-user stand-in for
`GoTrueSessions` (lab_auth's verifier) and `/_test/revoke` (the grantor's real revocation RPC).

    INFRX_D_TASK=lab-v1m uv run --frozen --project apps/infrx-api python apps/lab/tests/v/list/backend.py
    # prints one line: READY <port> <world json>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = Path(__file__).resolve().parents[4] / "infrx-api"
sys.path.insert(0, str(API))

from fastapi import FastAPI  # noqa: E402

from infrx.contracts import errors  # noqa: E402

CH_USER, CH_PASSWORD = "infrx_t2i", "infrx-t2i-local"          # task-local literals
FOREIGN_SERVING = "5e000000-0000-4000-8000-0000000000bb"       # not provider A's


def token(user: str) -> str:
    return f"eyJ0.{user.replace('-', '')}.c2ln"


class Sessions:
    """Stand-in for `GoTrueSessions`: each world user's token is theirs; anything else is 401."""

    def __init__(self, users) -> None:
        self.users = {token(u): u for u in users}

    async def user_id(self, value: str) -> str:
        if value not in self.users:
            raise errors.InvalidApiKey("not a live session")
        return self.users[value]


def main() -> None:
    import clickhouse_connect
    import uvicorn

    from infrx.contracts import tasklocal
    from infrx.contracts.v2 import records as v2
    from infrx.gateway.routes import lab_traces as lt
    from infrx.media.store import InMemoryObjectStore
    from infrx.state import migrations
    from infrx.state.jobstore import connector
    from infrx.traces import feedback, retention, ship
    from tests.d import checks_credit as cc
    from tests.d import pgharness
    from tests.l.access import worlds

    if os.environ.get("INFRX_D_TASK") not in ("lab-v1m", "lab-on"):
        raise SystemExit("the lab-v1m task-local key only (INFRX_D_TASK=lab-v1m)")
    db = f"{pgharness.DATABASE}_list"
    pgharness.ensure()
    pgharness.recreate(db)
    pgharness.apply(db, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    dsn = pgharness.dsn(db)
    conn = pgharness.connect(db)
    worlds.seed_pg(conn, dsn)
    w = worlds.PgWorld(conn, dsn)

    ch = dict(host="127.0.0.1", port=tasklocal.local_services("t2i")["clickhouse"].host_port,
              username=CH_USER, password=CH_PASSWORD)
    admin = clickhouse_connect.get_client(**ch, database="infrx_t2i")
    database = f"lab_v1m_{uuid.uuid4().hex}"
    admin.command(f"CREATE DATABASE {database}")
    client = clickhouse_connect.get_client(**ch, database=database)
    rows, fb = ship.ClickHouseProjection(client), feedback.ClickHouseFeedbackProjection(client)
    store, objects = retention.ClickHouseRetentionStore(client), InMemoryObjectStore()

    async def seed() -> tuple[retention.Retention, dict]:
        for projection in (rows, fb, store):
            await projection.apply_schema()
        policy = retention.Retention(store, rows, fb, objects)
        now, ids = policy.clock(), {}

        async def shipped(name, org, *, age, mode="full", content=b"", loss="none",
                          serving=cc.SERVING):
            request_id, trace_id = str(uuid.uuid4()), f"seg-{len(ids):04d}:0"
            key = ship.content_key(org, trace_id) if content else None
            if content:
                await objects.put_if_absent(key, content, "application/json")
            await rows.insert([ship.TraceRow(
                org_id=org, trace_id=trace_id, request_id=request_id,
                key_id="3c3c3c3c-0000-4000-8000-000000000003", mode=mode,
                started_at=now - age, completed_at=now - age + timedelta(seconds=2),
                loss_reason=loss, content_complete=loss == "none", content_bytes=len(content),
                content_key=key, content_stored=bool(content), request_schema_version=1,
                model_revision="nemostation/marlin-2b@r1", price_version="pv1",
                serving_version_id=serving, rate_card_version="rc1", policy_version="dap1")])
            ids[name] = request_id

        # C1 (BOTH's organization) grants A; C2 (CONSUMER_ONLY's) grants A nothing. A request made
        # with capture off is never shipped: it has no row at all.
        await shipped("granted", w.C1, age=timedelta(minutes=6), content=b'{"prompt":"c1"}')
        await shipped("ungranted", w.C2, age=timedelta(minutes=5), content=b'{"prompt":"c2"}')
        await shipped("lost", w.C1, age=timedelta(minutes=4), loss="queue_full")
        await shipped("minimal", w.C1, age=timedelta(minutes=3), mode="minimal")
        await shipped("foreign", w.C1, age=timedelta(minutes=2), serving=FOREIGN_SERVING,
                      content=b'{"prompt":"foreign"}')
        await shipped("deleted", w.C1, age=timedelta(minutes=1), content=b'{"prompt":"gone"}')
        await shipped("expired", w.C1, age=timedelta(days=policy.content_days, minutes=1),
                      content=b'{"prompt":"old"}')
        await policy.delete(w.C1, ids["deleted"], "customer")
        return policy, ids

    policy, ids = asyncio.run(seed())
    # The next version of C1's grant to A: both content categories for provider_sharing (lab-sql RPC).
    w._rpc("lab_put_access_grant", {
        "actor_user_id": w.owner[w.C1], "grantor_org_id": w.C1, "recipient_provider_org_id": w.A,
        "model_ids": [w.MODELS[w.A]], "purposes": ["provider_sharing"], "retention_days": 30,
        "categories": [v2.DataCategory.request_content.value, v2.DataCategory.response_content.value]})

    app = FastAPI()
    users = (w.DEV_A, w.DEV_B, w.VIEWER_A, w.BOTH, w.CONSUMER_ONLY)
    lt.register(app, None, lt.LabTraces(Sessions(users), w.access, lt.PgServing(connector(dsn)),
                                        lt.ClickHouseTraceRows(client), policy))

    @app.post("/_test/revoke")
    def revoke():
        """C1's owner revokes its grant to A through lab-sql's real RPC (test-only)."""
        w.revoke_grant(w.C1, w.A)
        return {"revoked": True}

    world = {"A": w.A, "B": w.B, "C1": w.C1, "C2": w.C2, "ids": ids,
             "tokens": {name: token(user) for name, user in (
                 ("dev_a", w.DEV_A), ("dev_b", w.DEV_B), ("viewer_a", w.VIEWER_A),
                 ("consumer_only", w.CONSUMER_ONLY))}}
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))

    async def serve():
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        print(f"READY {port} {json.dumps(world)}", flush=True)
        await task
    try:
        asyncio.run(serve())
    finally:
        admin.command(f"DROP DATABASE IF EXISTS {database}")
        conn.close()


if __name__ == "__main__":
    os.chdir(API)
    main()
