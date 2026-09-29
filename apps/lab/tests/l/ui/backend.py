#!/usr/bin/env python3
"""WR-E3L-J's real backend for `stack.test.ts`: the Lab control service as R186's factory
(`infrx.lab.control.app:create_app`, composed by `pilot.lab_operations` = L3's `Operations` over
the REAL `PgControlStore` and its 0044 ControlReads, `LabControl`, A3's registry and catalog, and
L2's `LabAccess(PgAccessStore)`) on the task-local PostgreSQL (`INFRX_D_TASK=l4`, port 57503),
seeded with L3's LAB-PUBLISH world (`tests/l/control/worlds.seed_pg`: NemoStation's Marlin listed
at version 1 over its operator-imported weights; Other Lab; ADMIN/DEV/VIEWER/BOTH/CONSUMER_ONLY).
Stand-ins, none deciding an oracle: the session verifier (a bearer token per user, for GoTrue),
the engine smoke (R203: `NoEngine.smoke` answers, WR-L3-2 is not wired), and `/_test/*` doors for
the operator (approve at a card, roll the listing back; L3 has no rejection, E3L-F4) and App
discovery (what admission pins for a consumer calling the alias).

    INFRX_D_TASK=l4 uv run --frozen --project apps/infrx-api python apps/lab/tests/l/ui/backend.py
    # prints one line: READY <port> <world json>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[4] / "infrx-api"
sys.path.insert(0, str(API))


def main() -> None:
    import uvicorn

    from infrx.contracts.v2.fixtures import SHARD_DIGESTS
    from infrx.gateway import lab_auth
    from infrx.lab.control import app as control_app
    from infrx.state import migrations
    from tests.d import pgharness
    from tests.g.lab_releases.test_lab_releases import Sessions, token
    from tests.l.control import worlds

    if os.environ.get("INFRX_D_TASK") != "l4":
        raise SystemExit("the l4 task-local key only (INFRX_D_TASK=l4)")
    db = f"{pgharness.DATABASE}_labctl"
    pgharness.ensure()
    pgharness.recreate(db)
    pgharness.apply(db, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    conn = pgharness.connect(db)
    worlds.seed_pg(conn, pgharness.dsn(db))
    w = worlds.PgWorld(conn, pgharness.dsn(db))   # the operator's and the App's side, same rows

    users = {"admin": w.ADMIN_A, "dev": w.DEV_A, "viewer": w.VIEWER_A, "rival": w.ADMIN_B,
             "rival_dev": w.DEV_B, "consumer": w.CONSUMER_ONLY, "ops": w.OPS_USER}
    os.environ.update({control_app.DATABASE_URL: pgharness.dsn(db),
                       control_app.SUPABASE_URL: "http://127.0.0.1:9",
                       control_app.SUPABASE_KEY: "anon"})
    for name in ("CLICKHOUSE_URL", "S3_TRACE_BUCKET"):
        os.environ.pop(name, None)
    lab_auth.GoTrueSessions = lambda *_a, **_k: Sessions(users.values())

    async def smoke(self, serving, deployment) -> bool:       # R203: the engine answers
        return True
    control_app.NoEngine.smoke = smoke
    app = control_app.create_app()

    def listed() -> int:
        return asyncio.run(w.control_store.listing_versions(worlds.ALIAS))[-1].version

    @app.post("/_test/approve")
    def approve(body: dict):
        """The operator approves a proposal at a fresh card, at the current listing version (the
        rejection is the route's own operator door, E3L-F4)."""
        n = listed()
        asyncio.run(w.control.approve(worlds.OPERATOR, body["proposal_id"],
                                      rate_card_version=f"rc_lab_{n + 1}", input_rate="3",
                                      output_rate="9", expected_version=n, reason="lab journey"))
        return {"result": "ok"}

    @app.post("/_test/rollback")
    def rollback():
        """The operator's rollback: the alias re-lists the version before its current one."""
        versions = asyncio.run(w.control_store.listing_versions(worlds.ALIAS))
        current = versions[-1]
        back = next(v for v in reversed(versions)
                    if v.deployment_revision_id != current.deployment_revision_id)
        asyncio.run(w.control.rollback(worlds.OPERATOR, worlds.ALIAS, to_version=back.version,
                                       expected_version=current.version, reason="lab journey"))
        return {"result": "ok"}

    @app.get("/_test/discoverable")
    def discoverable():
        """App discovery: the deployment revision admission pins for a consumer's call."""
        return {"deployment_revision_id": worlds.pin(w, worlds.ALIAS).deployment_revision_id}

    world = {"A": w.A, "B": w.B, "alias": worlds.ALIAS, "name": worlds.ALIAS.rpartition("/")[2],
             "weights": list(SHARD_DIGESTS), "tokens": {k: token(u) for k, u in users.items()}}
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
        conn.close()


if __name__ == "__main__":
    os.chdir(API)
    main()
