#!/usr/bin/env python3
"""LAB-E2E evaluate (E6L j10): the Lab's evaluation pages over lab-api's `/lab/v1/evaluations`
as the Lab's control unit composes it (R186's factory, WR-LDP-2: `pilot._lab`, D7's REAL
`PgLabDataStore`, B1's freeze, the REAL L2; WR-LR6-E2E-SHADOW: the unit's own route, served
through `stack.unit_app`), on the task-local PostgreSQL (l4), D7's seeded world.

That composition carries the experiments and B3 ledger ports since WR-AP10-1 and 0066's
catalog since WR-UXVF-1 (`carried` asks it: a listing answering 503 is not carried). The
world adds one READY PRIVATE DEV serving beside l2's, so the catalog offers a baseline and a
candidate. `/_test/composition {"as": "gateway"}` serves exactly the unit's composition;
`{"as": "journey"}` swaps the route suite's own fakes (`tests/g/lab_evaluations`) in for the
ports the unit does NOT carry, and only those (WR-UXVF-2: none, once all four are carried),
over the same real D7/B1/L2. `world.composed` and `stand_ins` say which, so the gate reports
NOT RUN rather than a pass over a fake. Test-only doors: `/_test/state` (B1's worker moving a
run on, under D7's own state trigger) and `/_test/settle` (B2's worker storing its report
through `PgLabDataStore.put_eval_report` on the experiment's two runs).

    INFRX_D_TASK=l4 uv run --frozen --project apps/infrx-api python apps/lab/tests/e2e/evaluate/backend.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stack  # noqa: E402

PORTS = ("store", "experiments", "catalog", "ledger")


async def carried(composition, provider_org_id: str) -> dict[str, bool]:
    """Which ports the unit's own composition answers through: present, and for the catalog
    a listing that answers (a 503, `DependencyUnavailable`, is not carried - j10 stays NOT RUN
    rather than binding over the route suite's fake catalog)."""
    from infrx.contracts import errors
    got = {name: getattr(composition, name) is not None for name in PORTS}
    if got["catalog"]:
        try:
            await composition.catalog.catalog(provider_org_id)
        except errors.DependencyUnavailable:
            got["catalog"] = False
    return got


def main() -> None:
    from infrx.gateway.routes import lab_evaluations as le
    from infrx.state.jobstore import connector
    from infrx.state.lab_data import PgLabDataStore, PgLabReads
    from tests.b.runner.world import EVALUATOR_ID, SPEC, harness, manifest, uid
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_l2sql_access as l2
    from tests.d import test_l3sql_control as l3
    from tests.g.lab_evaluations.test_lab_evaluations import EVALUATOR, Experiments, Ledger

    conn, dsn = stack.database("evaluate")
    d7.seed(conn)
    # l3's second serving version (S2) with its dev revision D2 READY PRIVATE: beside l2's
    # ready dev revision, the pair 0066's catalog offers as baseline and candidate.
    conn.execute(f"""
    insert into infrx.serving_versions (serving_version_id, model_version_id, model_id,
      provider_org_id, revision_label, prompt_harness_ref, preprocessor_profile_version,
      runtime_image_ref, engine_options_digest, precision, capability, created_by)
    select '{l3.S2}', model_version_id, model_id, provider_org_id, '2026-10-01',
           prompt_harness_ref, preprocessor_profile_version, runtime_image_ref,
           engine_options_digest, precision, capability, 'ops'
      from infrx.serving_versions where serving_version_id = '{l3.S1}';
    insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id,
      provider_org_id, environment, serving_version_id, visibility, state, max_input_tokens,
      max_output_tokens, created_by)
    values ('{l3.D2}', '{l3.cc.DEV_ENDPOINT}', '{l3.NEMO}', 'dev', '{l3.S2}', 'private',
            'ready_private', 30720, 2048, 'dev@nemo');
    """)
    connect = connector(dsn)
    store, reads = PgLabDataStore(connect), PgLabReads(connect)
    NEMO, DEV = l2.NEMO, l2.DEV

    async def publish():
        dataset = await store.publish(manifest(3, d7.W["grant"], d7.W["source"], dataset=7),
                                      provider_org_id=NEMO, actor=DEV)
        harness_ref = await store.publish(harness(harness_id=uid(7, 0xa7)),
                                          provider_org_id=NEMO, actor=DEV)
        assert await store.put_evaluator(SPEC, provider_org_id=NEMO, evaluator_id=EVALUATOR_ID,
                                         actor=DEV) == EVALUATOR
        return dataset, harness_ref

    dataset, harness_ref = asyncio.run(publish())
    servings = [o["ref"] for o in asyncio.run(store.eval_catalog(provider_org_id=NEMO))["servings"]]
    assert len(servings) == 2, servings

    class Catalog:
        """WR-LAB2-2's listing (the route suite's fake): NEMO's own records only."""

        async def catalog(self, provider_org_id):
            if provider_org_id != NEMO:
                return {"datasets": [], "harnesses": [], "servings": [], "evaluators": []}
            return {"datasets": [{"ref": dataset, "label": "support-v1"}],
                    "harnesses": [{"ref": harness_ref, "harness_id": uid(7, 0xa7), "version": 1,
                                   "adapter": "text"}],
                    "servings": [{"ref": ref, "label": ref} for ref in servings],
                    "evaluators": [{"ref": EVALUATOR, "label": "exact match"}]}

        async def evaluator(self, provider_org_id, evaluator_ref):
            if (provider_org_id, evaluator_ref) != (NEMO, EVALUATOR):
                raise le.errors.NotFound("no such evaluator for this provider")
            return SPEC

    sock, url = stack.listen()
    app, switch = stack.unit_app(dsn, url, "lab_evaluations")
    users = {"dev": DEV, "viewer": l2.VIEWER, "other_dev": l2.BOTH, "consumer": l2.C1}
    stack.door(app, dsn, users)
    gateway = switch.own
    composed = asyncio.run(carried(gateway, NEMO))
    fakes = {"experiments": Experiments(), "ledger": Ledger(), "catalog": Catalog()}
    swapped = {port: fake for port, fake in fakes.items() if not composed[port]}
    journey = dataclasses.replace(gateway, **swapped)

    @app.post("/_test/composition")
    async def composition(body: dict):
        switch.current = {"gateway": gateway, "journey": journey}[body["as"]]
        return {"as": body["as"]}

    @app.post("/_test/state")
    async def state(body: dict):
        """B1's worker moving a run on: D7's own state trigger decides what is allowed."""
        conn.execute("update infrx.lab_eval_runs set state = %s where run_id = %s",
                     (body["state"], body["run_id"]))
        return {"state": body["state"]}

    @app.post("/_test/settle")
    async def settle(body: dict):
        """B2's worker storing its report on the experiment's two runs and protocol (WR-B-5):
        through D7's write-once store, read back by 0066's experiment listing."""
        e = next(e for e in await reads.experiments(provider_org_id=NEMO)
                 if e["experiment_id"] == body["experiment_id"])
        report = {**body["report"], "baseline_run": e["baseline_run_ref"],
                  "candidate_run": e["candidate_run_ref"],
                  "protocol_digest": e["protocol_digest"]}
        report.pop("report_digest", None)
        return {"report_digest": await store.put_eval_report(report, provider_org_id=NEMO,
                                                             actor=DEV)}

    world = {"A": NEMO, "B": l2.OTHER, "dataset": dataset, "servings": servings,
             "users": users, "composed": composed,
             "stand_ins": ["session verifier and PostgREST RPC door (stack.door)",
                           *([f"{'/'.join(swapped)}: the route suite's fakes (not carried)"]
                             if swapped else []),
                           "B1's worker and B2's report (test doors)"]}
    stack.serve(app, sock, world, conn.close)


if __name__ == "__main__":
    os.chdir(stack.API)
    main()
