#!/usr/bin/env python3
"""LAB-E2E rollout (E8L k10's UI half): the Lab's releases page over lab-api's
`/lab/v1/releases` as the Lab's control unit composes it (R186's factory, WR-LDP-2: every Lab
family through the gateway's `pilot._lab`, the unit is the switch, R237) on the task-local
PostgreSQL (l4), D9's seeded world and the real L2.

WR-LR5-1: the page reads that composition's own ports (`pilot.lab_releases`: records = D9 0048
+ D7 + the stored plan, decisions = 0053, proposals = 0043, store = D9) over the Lab objects,
held in memory (l4 has no S3) and handed to the unit through `LAB_S3_BUCKET`'s seam
(`lab_objects`). A release is launched by `rollout launch`'s own code (`launch_release`: the
plan stored write-once, then D9 starts it, R241) and a proposal decided by `rollout decide`'s
(`decide_proposal`: 0043's decision at the proposal's fence, then R2's stop), in this process
so they write the objects the unit reads. What no composed read holds is laid over the
records, and nothing else: R2's `hold`/`expand` verdict of a running release D9 holds no
decision for (the composed verdict is D9's latest decision; WR-LR6-VERDICT), and an
expansion's approval, which `rollout decide` refuses (WR-LIVE-DECIDE), goes through R2's
`Controller.approve` with 0043's decide RPC as its transition. Test-only doors: `/_test/probe`
(the records port's own refusal), `/_test/composition` (the verdict laid over, or not),
`/_test/launch`, `/_test/stop`, `/_test/step` (R2's pass on R1's aggregates as given),
`/_test/decide`.

    INFRX_D_TASK=l4 uv run --frozen --project apps/infrx-api python apps/lab/tests/e2e/rollout/backend.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stack  # noqa: E402

Z = "%Y-%m-%dT%H:%M:%SZ"
PORTS = ("records", "proposals", "store")


def main() -> None:
    import tempfile

    from infrx.contracts import errors
    from infrx.contracts.lab import records as lab
    from infrx.gateway import pilot
    from infrx.lab.workers import __main__ as lab_workers
    from infrx.media.store import InMemoryObjectStore
    from infrx.rollouts import control as r2
    from infrx.state.jobstore import connector
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_rollout import PgLabRolloutStore, PgReleaseProposals, PgReleaseStore
    from tests.b.runner.world import EVALUATOR_ID, SPEC, harness, manifest, uid
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_d9_rollout as d9
    from tests.d import test_l2sql_access as l2
    from tests.r.control import test_control as r2w

    conn, dsn = stack.database("rollout")
    d9.seed(conn)
    connect = connector(dsn)
    store, pg, NEMO = PgReleaseStore(connect), PgReleaseProposals(connect), d9.NEMO
    data = PgLabDataStore(connect)
    sock, url = stack.listen()
    # the Lab objects of the control unit's composition and of `rollout launch|decide`, in
    # process; R2's stop converges the release's own alias (the d9 world lists no endpoint)
    objects, serving = InMemoryObjectStore(), [None]
    lab_workers.lab_objects = lambda mode, env: objects
    pilot.control_serving = lambda connect, principal: serving[0]
    os.environ["LAB_S3_BUCKET"] = "l4-in-memory"
    app = stack.control_app(dsn, url)
    users = {"admin": l2.ADMIN, "dev": l2.DEV, "viewer": l2.VIEWER, "other_dev": l2.BOTH,
             "consumer": l2.C1}
    stack.door(app, dsn, users)
    gateway = stack.composed("lab_releases", dsn, url, objects)["lab_releases"]
    composed = {name: getattr(gateway, name) is not None for name in PORTS}

    async def suite() -> dict:
        dataset = await data.publish(manifest(3, d7.W["grant"], d7.W["source"], dataset=7),
                                     provider_org_id=NEMO, actor=d9.USER)
        harness_ref = await data.publish(harness(harness_id=uid(7, 0xa7)), provider_org_id=NEMO,
                                         actor=d9.USER)
        evaluator = await data.put_evaluator(SPEC, provider_org_id=NEMO,
                                             evaluator_id=EVALUATOR_ID, actor=d9.USER)
        return {**r2w.BASE_RUN, "provider_org_id": NEMO, "dataset_ref": dataset,
                "harness_ref": harness_ref, "evaluator_ref": evaluator}

    base_run, plan = asyncio.run(suite()), r2w.plan()
    launched: dict[str, dict] = {}              # policy_ref -> policy, R2's last verdict, alias
    plan_path = Path(tempfile.mkdtemp(prefix="lab-e2e-rollout-")) / "plan.json"
    plan_path.write_text(plan.model_dump_json())
    # `rollout launch|decide` in process: the gateway's objects are theirs (l4 has no S3), and
    # R2's stop converges the release's own alias (FakeServing: the d9 world lists no endpoint)
    env = {"LAB_DATABASE_URL": dsn, "LAB_S3_BUCKET": "l4-in-memory"}

    def runs_of(policy) -> tuple[dict, dict]:
        return ({**base_run, "serving_ref": policy.baseline_ref},
                {**base_run, "run_id": r2w.CAND_RUN["run_id"],
                 "serving_ref": policy.candidates[0].serving_ref,
                 "idempotency_key": r2w.CAND_RUN["idempotency_key"]})

    overlaid, composed_releases = [False], pilot.ReleaseRecords.releases

    async def releases(self, provider_org_id):
        """The composed records, with R2's hold/expand verdict of a running release laid over
        one D9 holds no decision for (WR-LR6-VERDICT) while the journey runs."""
        rows = await composed_releases(self, provider_org_id)
        for row in rows if overlaid[0] else ():
            verdict = launched.get(row["policy_ref"], {}).get("verdict")
            if row["verdict"] is None and verdict is not None:
                row["verdict"] = {"action": verdict.action, "reasons": list(verdict.reasons),
                                  "evidence_refs": list(verdict.evidence_refs),
                                  "evaluated_at": r2w.HORIZON.strftime(Z)}
        return rows
    pilot.ReleaseRecords.releases = releases

    class Decided:
        """R2's `ReleaseStore` for an expansion's approval: its transition is the proposal's own
        decide RPC (0043: 0039's CAS at the fence the proposal was filed at)."""

        def __init__(self, proposal_id: str) -> None:
            self.proposal_id = proposal_id

        async def release(self, ref):
            return await store.release(ref)

        async def transition(self, ref, *, fence, to, decision, reasons):
            await pg.decide(self.proposal_id, approve=True, decided_by=r2w.OPERATOR,
                            decision=decision, reasons=tuple(reasons))
            return (await store.release(ref)).fence

    @app.post("/_test/composition")
    async def composition(body: dict):
        overlaid[0] = {"gateway": False, "journey": True}[body["as"]]
        return {"as": body["as"]}

    @app.post("/_test/probe")
    async def probe(body: dict):
        """The gateway's own records port, read directly: which refusal R01's page fails on."""
        try:
            return {"rows": len(await gateway.records.releases(NEMO))}
        except errors.DependencyUnavailable as refused:
            return {"refusal": str(refused)}

    @app.post("/_test/launch")
    async def launch(body: dict):
        """A canary revision published through D7, launched by `rollout launch` (its plan
        stored first, R241) - or, `planless`, started in D9 without its launcher."""
        tag = int(body["tag"])
        payload = d9.policy(d9.uid(tag, 0xb0), weights=(1_000,), endpoint=d9.uid(tag, 0xe0))
        policy, ref = lab.parse(payload), d7.publish(conn, payload)
        if body.get("planless"):
            await store.start(ref, provider_org_id=NEMO, plan_digest=r2.plan_digest(plan),
                              decided_by=d9.USER, reason="canary 10%")
        else:
            code = await lab_workers.launch_release({**env, "LAB_OPERATOR_ID": d9.USER}, ref,
                                                    str(plan_path), "canary 10%")
            assert code == 0, f"rollout launch exited {code}"
        for run in runs_of(policy):
            assert d7.publish(conn, run) == lab.ref_of(run)
        launched[ref] = {"policy": policy, "verdict": None,
                         "serving": r2w.FakeServing(current=policy.candidates[0].serving_ref)}
        return {"policy_ref": ref}

    @app.post("/_test/stop")
    async def stop(body: dict):
        """The operator stops a release (D9's own transition): it leaves the listing."""
        release = await store.release(body["policy_ref"])
        await PgLabRolloutStore(connect).transition(
            launched[body["policy_ref"]]["policy"].policy_id, "stop", fence=release.fence,
            provider_org_id=NEMO, decided_by=d9.USER, reason="e2e case boundary")
        return {"stopped": body["policy_ref"]}

    @app.post("/_test/step")
    async def step(body: dict):
        """One R2 controller pass on R1's aggregates as given (and B2's report, if any)."""
        rel = launched[body["policy_ref"]]
        live, runs = r2w.live(errors_=body.get("errors", 20)), runs_of(rel["policy"])
        rep = None if body.get("report") is None else r2w.report(body["report"], base_run=runs[0],
                                                                 cand_run=runs[1])
        ctl = r2.Controller(store, rel["serving"], actor_id=r2w.CONTROLLER)
        verdict = await ctl.step(rel["policy"], body["policy_ref"], plan, live, now=r2w.HORIZON,
                                 report=rep, runs=runs)
        if verdict.action in ("rollback", "hold", "expand"):
            rel["verdict"] = verdict
        return {"action": verdict.action}

    @app.post("/_test/decide")
    async def decide(body: dict):
        """The operator decides a release's pending proposal: `rollout decide` (a rollback or
        a rejection), or R2's approval of an expansion with its evidence (WR-LIVE-DECIDE)."""
        rows = await pg.proposals(provider_org_id=NEMO)
        p = next(r for r in rows if r["policy_ref"] == body["policy_ref"]
                 and r["state"] == "proposed")
        rel = launched[p["policy_ref"]]
        if p["kind"] == "expand" and body["approve"]:
            ctl = r2.Controller(Decided(p["proposal_id"]), rel["serving"],
                                actor_id=r2w.CONTROLLER)
            runs = runs_of(rel["policy"])
            try:
                await ctl.approve(r2w.OPERATOR, rel["policy"], p["policy_ref"], plan, r2w.live(),
                                  now=r2w.HORIZON, report=r2w.report(base_run=runs[0],
                                                                     cand_run=runs[1]), runs=runs)
            except errors.StateConflict:
                return {"result": "conflict"}
            return {"result": "ok"}
        serving[0] = rel["serving"]
        code = await lab_workers.decide_proposal({**env, "LAB_OPERATOR_ID": r2w.OPERATOR},
                                                 p["policy_ref"], str(p["proposal_id"]),
                                                 bool(body["approve"]), "proposal")
        return {"result": ("ok" if body["approve"] else "rejected") if code == 0 else "conflict"}

    world = {"A": NEMO, "B": l2.OTHER, "operator": r2w.OPERATOR, "controller": r2w.CONTROLLER,
             "users": users, "composed": composed,
             "stand_ins": ["session verifier and PostgREST RPC door (stack.door)",
                           "the Lab objects in memory (l4 has no S3), handed to the control "
                           "unit and to `rollout launch|decide` (run in process to share them)",
                           "R2's hold/expand verdict laid over the gateway's records while D9 "
                           "holds no decision for the release (WR-LR6-VERDICT)",
                           "an expansion approved through R2's Controller.approve: `rollout "
                           "decide` refuses it (WR-LIVE-DECIDE)",
                           "R1 aggregates as the step's input (no R1 traffic on l4)",
                           "L3's serving alias (R2's FakeServing)"]}
    stack.serve(app, sock, world, conn.close)


if __name__ == "__main__":
    os.chdir(stack.API)
    main()
