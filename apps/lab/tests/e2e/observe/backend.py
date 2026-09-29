#!/usr/bin/env python3
"""LAB-E2E observe (E5L o10, CONSOLE-FLOWS): the Lab's requests list and request review page
over lab-api's provider trace read (`lab_traces.register`, R176) on the REAL L2 (`LabAccess`
over `PgAccessStore`, lab-sql's grant RPCs, the LAB-ACCESS world of `tests/l/access/worlds`)
and the registry (`PgServing`) on the task-local PostgreSQL (l4), beside R186's control
factory, with the review page's session doors (0030 memberships, 0038 feedback review, 0043
judge runs) run as the signed-in user by `stack.door`. The projection and deletion ledger are
T2I's and T3's in-memory stand-ins (`tests/g/lab_traces`'s PostgreSQL half on the same key:
the l4 key has no ClickHouse; `pilot._lab_traces` needs one, so LAB_TRACES' own composition
is not used here and the o03 gate cell holds the ClickHouse half). Test-only: `/_test/revoke`
(the grantor's real revocation RPC).

    INFRX_D_TASK=l4 uv run --frozen --project apps/infrx-api python apps/lab/tests/e2e/observe/backend.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stack  # noqa: E402


def main() -> None:
    from infrx.gateway import lab_auth
    from infrx.gateway.routes import lab_traces as lt
    from infrx.state.jobstore import connector
    from tests.g.lab_traces.test_lab_traces import grant_content, traces
    from tests.l.access import worlds

    conn, dsn = stack.database("observe")
    worlds.seed_pg(conn, dsn)
    w = worlds.PgWorld(conn, dsn)
    t = traces(w)
    grant_content(w, w.C1, w.A)
    sock, url = stack.listen()
    app = stack.control_app(dsn, url)
    users = {"dev_a": w.DEV_A, "dev_b": w.DEV_B, "viewer_a": w.VIEWER_A,
             "consumer": w.CONSUMER_ONLY}
    stack.door(app, dsn, users)
    sessions = lab_auth.GoTrueSessions(__import__("httpx").AsyncClient(base_url=url), "anon")
    lt.register(app, None, lt.LabTraces(sessions, w.access, lt.PgServing(connector(dsn)),
                                        t.rows, t.policy))

    @app.post("/_test/revoke")
    def revoke():
        """C1's owner revokes its grant to A through lab-sql's real RPC (test-only)."""
        w.revoke_grant(w.C1, w.A)
        return {"revoked": True}

    world = {"A": w.A, "B": w.B, "ids": t.ids, "users": users,
             "stand_ins": ["session verifier and PostgREST RPC door (stack.door)",
                           "T2I projection and T3 ledger in memory (no ClickHouse on l4)"]}
    stack.serve(app, sock, world, conn.close)


if __name__ == "__main__":
    os.chdir(stack.API)
    main()
