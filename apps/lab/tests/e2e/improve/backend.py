#!/usr/bin/env python3
"""LAB-E2E improve (E7L i08's provider-UI half, WR-C4-UI): the Lab's annotations and training
pages over lab-api's `/lab/v1/pipelines` as the gateway composes it with LAB_PIPELINES on
(`pilot._lab`: D7, D8's REAL label log and run ledger, L2, the Lab objects, P3's evaluation
port over B3/B1), beside R186's control factory, on the task-local PostgreSQL (l4): D8's seeded
world plus an imported 8-sample benchmark (as `tests/p/backend.py`), a 100 PROVIDER_USD budget
for the world's payer. The Lab objects are in memory (the l4 key has no S3).

The gateway's composition answers 503 for the run and checkpoint listings the training page
reads (`pilot.RunLedger`, WR-LAB2-4): `/_test/composition {"as": "gateway"}` serves exactly it;
`{"as": "journey"}` adds those two listings over the same D8/D7 rows (`Listing`, E7L's i08
stand-in, plus the run listing over `lab_external_runs`). `world.composed` says whether the
gateway's own composition lists them. Test-only door: `/_test/artifact` (the provider uploads a
trained checkpoint descriptor under its run's prefix).

    INFRX_D_TASK=l4 uv run --frozen --project apps/infrx-api python apps/lab/tests/e2e/improve/backend.py
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stack  # noqa: E402


def main() -> None:
    from infrx.contracts import errors
    from infrx.datasets import imports
    from infrx.gateway.routes import lab_pipelines as lp
    from infrx.media.store import InMemoryObjectStore
    from infrx.state.jobstore import connector
    from infrx.state.lab_data import PgLabDataStore
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_d8_ledgers as d8
    from tests.d import test_l2sql_access as l2
    from tests.g import support
    from tests.n.imports.world import NEMO, chunks, fixture, run
    from tests.n.versions.test_versions import uid
    from tests.p.annotations.world import RUBRIC, rows
    from tests.p.training import world as p3w

    conn, dsn = stack.database("improve")
    d8.seed(conn)
    d7.ok(conn, "lab_put_budget", {"provider_org_id": NEMO, "payer_ref": p3w.PAYER,
                                   "limit": "100.00000000", "actor": "ops", "reason": "e2e"})
    store, objects = PgLabDataStore(connector(dsn)), InMemoryObjectStore()
    spec, _ = fixture("benchmark")
    spec = {**spec, "import_id": uid(2, 0x1c), "dataset_id": uid(2, 0xdc),
            "grant_ref": d7.W["grant"], "fields": {"content": "q", "group": "g", "split": "split"}}
    data = b"".join(json.dumps(i).encode() + b"\n"
                    for i in rows(8, splits=("train", "train", "holdout", "validation")))
    ref = run(imports.Importer(store, objects).run(
        spec, chunks(data, 64), provider_org_id=NEMO, actor="dev@nemo")).dataset_ref
    splits = run(store.resolve(ref, provider_org_id=NEMO)).splits

    sock, url = stack.listen()
    app = stack.control_app(dsn, url)
    users = {"dev": l2.DEV, "admin": l2.ADMIN, "viewer": l2.VIEWER, "other_dev": l2.BOTH,
             "consumer": l2.C1}
    stack.door(app, dsn, users)
    gateway = stack.composed("lab_pipelines", dsn, url, objects)["lab_pipelines"]

    async def listed(ledger) -> bool:
        try:
            await ledger.run_rows(NEMO)
        except errors.DependencyUnavailable:
            return False
        return True

    class Listing:
        """WR-LAB2-4's stand-in over the composed `RunLedger` (D8's `PgRunLedger`): the run
        listing over `lab_external_runs` and the checkpoint listing over D7's receipts with
        P3's outcome note (E7L's i08 `Listing`)."""

        def __init__(self, composed) -> None:
            self.composed = composed

        def __getattr__(self, name):
            return getattr(self.composed, name)

        async def run_rows(self, provider_org_id):
            return [(rid, doc) for rid, doc in conn.execute(
                "select external_run_id::text, doc from infrx.lab_external_runs where "
                "provider_org_id = %s order by created_at, external_run_id",
                (provider_org_id,)).fetchall()]

        async def checkpoint_rows(self, provider_org_id):
            out = []
            for cid, ext, digest, state in conn.execute(
                    "select checkpoint_id::text, external_run_ref, artifact_digest, state from "
                    "infrx.lab_checkpoint_receipts where provider_org_id = %s order by "
                    "received_at, checkpoint_id", (provider_org_id,)).fetchall():
                note = await self.composed.noted(f"checkpoint:{cid}",
                                                 provider_org_id=provider_org_id) or {}
                out.append({"checkpoint_id": cid, "external_run_ref": ext,
                            "artifact_digest": digest, "state": note.get("state", state),
                            "reason": note.get("reason")})
            return out

    journey = dataclasses.replace(gateway, ledger=Listing(gateway.ledger))
    composed = {"listings": run(listed(gateway.ledger)), "suites": gateway.evals.suites is not None}

    class Switch:
        """The mounted `LabPipelines`: the gateway's own composition, or the journey's."""
        current = gateway

        def __getattr__(self, name):
            return getattr(self.current, name)

    switch = Switch()
    lp.register(app, support.runtime(), switch)

    @app.post("/_test/composition")
    async def composition(body: dict):
        switch.current = {"gateway": gateway, "journey": journey}[body["as"]]
        return {"as": body["as"]}

    @app.post("/_test/artifact")
    async def artifact(body: dict):
        """The provider uploads a trained checkpoint descriptor under the run's prefix."""
        blob = p3w.descriptor()
        key = f"lab/{NEMO}/training/{body['external_run_id']}/{body['name']}"
        await objects.put_if_absent(key, blob, "application/json")
        return {"key": key, "digest": p3w.digest(blob)}

    world = {"A": NEMO, "B": l2.OTHER, "dataset": ref, "rubric": RUBRIC, "payer": p3w.PAYER,
             "train": sorted(splits.train), "holdout": sorted(splits.holdout),
             "validation": sorted(splits.validation), "users": users, "composed": composed,
             "stand_ins": ["session verifier and PostgREST RPC door (stack.door)",
                           "Lab objects in memory (no S3 on l4)",
                           "run and checkpoint listings over D8/D7 rows (WR-LAB2-4 to compose)",
                           "no B3 suite source in P3's evaluation port unless composed (WR-B3-3)"]}
    stack.serve(app, sock, world, conn.close)


if __name__ == "__main__":
    os.chdir(stack.API)
    main()
