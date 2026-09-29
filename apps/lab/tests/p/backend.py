#!/usr/bin/env python3
"""P4 swap's real backend for `stack.test.ts`: lab-api's `/lab/v1/pipelines`
(`lab_pipelines.register`, WR-P4-1) served as merged over the REAL D7 (`PgLabDataStore`: the dataset,
the annotation and external-run records, the checkpoint receipts) and the REAL L2
(`LabAccess(PgAccessStore)`: the actor, P1's reviewer roles, P3's grant check, the database clock) on
the task-local PostgreSQL (`INFRX_D_TASK=p1`, port 57527), D7's seeded world plus an imported
8-sample dataset. D8's label log and run ledger (SR-P1-1, SR-P3-1) and B3's evaluation port are the
route suite's fakes: their tables are not on this base; the ledger's checkpoint listing (WR-LAB2-4)
reads D7's real `lab_checkpoint_receipts`. Objects are in memory. Test-only: the
bearer-token-per-user stand-in for `GoTrueSessions`, and `/_test/*` doors standing in for the
provider (an uploaded artifact), B3 (an evaluation result), a connector that lost its answer
(ambiguous), the grantor (a real revocation RPC) and the database clock (an export expiring).
P4.b (J06): the teacher batches run P2 over the REAL D8 `PgTeacherLedger` (0042, on the same
database: `lab_submission` on, a 100 PROVIDER_USD budget for the world's payer, C1's grant naming
external_judging) and J2's `HttpJudgeProvider` to the local teacher fake on p2's port (57529);
`/_test/teacher-mode` (the fake drops an answer: ambiguous) and `/_test/teacher-failures` (P2's
collect logging per-item failures) stand in for the teacher and the collector.

    INFRX_D_TASK=p1 uv run --frozen --project apps/infrx-api python apps/lab/tests/p/backend.py
    # prints one line: READY <port> <world json>
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[3] / "infrx-api"
sys.path.insert(0, str(API))

from fastapi import FastAPI, Request  # noqa: E402


def main() -> None:
    import uvicorn
    from psycopg.types.json import Jsonb

    from infrx.datasets import imports
    from infrx.gateway import lab_auth
    from infrx.contracts.tasklocal import local_services
    from infrx.gateway.routes import lab_pipelines as lp
    from infrx.judge.submit import HttpJudgeProvider
    from infrx.lab.access import LabAccess
    from infrx.media.store import InMemoryObjectStore
    from infrx.state import migrations
    from infrx.state.jobstore import connector
    from infrx.state.lab_access import PgAccessStore
    from infrx.pipelines import annotations as p1
    from infrx.pipelines.teachers import TeacherWiring
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_pipeline import PgTeacherLedger
    from tests.d import pgharness
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_d8_ledgers as d8
    from tests.d import test_l2sql_access as l2
    from tests.g import support
    from tests.g.lab_pipelines.test_lab_pipelines import LIVE, Ledger, Sessions, token
    from tests.j import fakes as j1
    from tests.j.submit.judge_fake import JudgeFake
    from tests.p.teachers import fakes as p2f
    from tests.n.imports.world import NEMO, chunks, fixture, run
    from tests.n.versions.test_versions import uid
    from tests.p.annotations.world import RUBRIC, FakeLabelLog, rows
    from tests.p.training import world as p3w

    if os.environ.get("INFRX_D_TASK") not in ("p1", "lab-on"):
        raise SystemExit("the p1 task-local key only (INFRX_D_TASK=p1)")
    db = f"{pgharness.DATABASE}_labui"
    pgharness.ensure()
    pgharness.recreate(db)
    pgharness.apply(db, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    conn = pgharness.connect(db)
    d8.seed(conn)                   # D7's world + lab_submission on + C1's grant naming external_judging
    d7.ok(conn, "lab_put_budget", {"provider_org_id": NEMO, "payer_ref": p3w.PAYER,
                                   "limit": "100.00000000", "actor": "ops", "reason": "p4b"})
    connect = connector(pgharness.dsn(db))
    store, objects = PgLabDataStore(connect), InMemoryObjectStore()
    spec, _ = fixture("benchmark")
    spec = {**spec, "import_id": uid(2, 0x1c), "dataset_id": uid(2, 0xdc),
            "grant_ref": d7.W["grant"], "fields": {"content": "q", "group": "g", "split": "split"}}
    data = b"".join(json.dumps(i).encode() + b"\n"
                    for i in rows(8, splits=("train", "train", "holdout", "validation")))
    ref = run(imports.Importer(store, objects).run(
        spec, chunks(data, 64), provider_org_id=NEMO, actor="dev@nemo")).dataset_ref
    splits = run(store.resolve(ref, provider_org_id=NEMO)).splits

    class Receipts(Ledger):
        """WR-LAB2-4's checkpoint listing over D7's real receipts, with P3's outcome notes."""

        async def checkpoint_rows(self, provider_org_id):
            found = conn.execute(
                "select checkpoint_id::text, artifact_digest, external_run_ref, state from "
                "infrx.lab_checkpoint_receipts where provider_org_id = %s order by received_at, "
                "checkpoint_id", (provider_org_id,)).fetchall()
            out = []
            for cid, digest, external, state in found:
                note = self.notes.get((provider_org_id, f"checkpoint:{cid}")) or {}
                out.append({"checkpoint_id": cid, "artifact_digest": digest,
                            "external_run_ref": external, "state": note.get("state", state),
                            "reason": note.get("reason")})
            return out

    ledger, evals = Receipts(store), p3w.FakeEvaluations()
    users = {"dev": l2.DEV, "admin": l2.ADMIN, "viewer": l2.VIEWER, "other_dev": l2.BOTH,
             "consumer": l2.C1}
    log, teacher_ledger = FakeLabelLog(), PgTeacherLedger(connect)
    fake = JudgeFake(port=local_services("p2")["teacher-fake"].host_port)
    teachers = TeacherWiring(members=PgAccessStore(connect), ledger=teacher_ledger,
                             provider=HttpJudgeProvider(fake.url), store=store, objects=objects,
                             labels=p1.import_labels, log=log, rates=j1.TEST_RATES,
                             settings=LIVE, redact=p2f.redact)
    x = lp.LabPipelines(Sessions(users.values()), LabAccess(PgAccessStore(connect)), store=store,
                        objects=objects, log=log, ledger=ledger, evals=evals, teachers=teachers)
    app = FastAPI()
    lp.register(app, support.runtime(), x)
    refusal = lab_auth.refusal
    lab_auth.refusal = lambda exc: (print(f"refusal: {exc!r}", file=sys.stderr), refusal(exc))[1]

    @app.post("/_test/artifact")
    async def artifact(request: Request):
        """The provider uploads a trained checkpoint descriptor under the run's prefix."""
        body = await request.json()
        blob = p3w.descriptor()
        key = f"lab/{NEMO}/training/{body['external_run_id']}/{body['name']}"
        await objects.put_if_absent(key, blob, "application/json")
        return {"key": key, "digest": p3w.digest(blob)}

    @app.post("/_test/evaluated")
    async def evaluated(request: Request):
        """B3's held-out run for a checkpoint ends."""
        body = await request.json()
        evals.runs[body["checkpoint_id"]]["state"] = body["state"]
        return {"state": body["state"]}

    @app.post("/_test/ambiguous")
    async def ambiguous(request: Request):
        """A submission whose answer was lost: the ledger row is `ambiguous`."""
        body = await request.json()
        key = (NEMO, body["external_run_id"])
        ledger.runs[key] = {**ledger.runs[key], "state": "ambiguous"}
        return {"state": "ambiguous"}

    @app.post("/_test/advance")
    async def advance(request: Request):
        """Time passes on the database clock (the task-local `infrx_test.clock`)."""
        seconds = int((await request.json())["seconds"])
        conn.execute("update infrx_test.clock set offset_s = offset_s + make_interval(secs => %s)",
                     (seconds,))
        return {"advanced": seconds}

    @app.post("/_test/revoke")
    def revoke():
        """C1's owner revokes its grant to NEMO through lab-sql's real RPC."""
        conn.execute("select infrx.lab_revoke_access_grant(%s)", (Jsonb({
            "actor_user_id": l2.C1, "grantor_org_id": l2.org(conn, l2.C1),
            "recipient_provider_org_id": NEMO}),))
        return {"revoked": True}

    @app.post("/_test/teacher-mode")
    async def teacher_mode(request: Request):
        """The local teacher fake answers `ok`, or `drop`s (a 504: the outcome is unknown)."""
        fake.mode = (await request.json())["mode"]
        return {"mode": fake.mode, "posts": len(fake.posts)}

    @app.post("/_test/teacher-failures")
    async def teacher_failures(request: Request):
        """P2's collect logs per-item failures for a chunk (D8's append-only log)."""
        body = await request.json()
        return {"inserted": await teacher_ledger.record_failures(
            body["run_id"], [(f["sample_id"], f["reason"]) for f in body["failures"]])}

    world = {"A": NEMO, "B": l2.OTHER, "dataset": ref, "rubric": RUBRIC, "payer": p3w.PAYER,
             "train": sorted(splits.train), "holdout": sorted(splits.holdout),
             "validation": sorted(splits.validation),
             "users": users, "tokens": {name: token(user) for name, user in users.items()},
             "teacher": j1.JUDGE_MODEL}
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
        fake.close()
        conn.close()


if __name__ == "__main__":
    os.chdir(API)
    main()
