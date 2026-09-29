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
so they write the objects the unit reads - an expansion too (WR-LIVE-DECIDE: approved only on
R2's `expand` verdict over D9's Live and the B2 report). The page's verdict of a running
release is the composed records' own (WR-LR6-VERDICT: R2's `evaluate` at read time over D9's
Live and the release's B2 report): `/_test/traffic` writes R1's assignments and the admitted
jobs they name as rows (tests/d's job fixture; no R1 traffic on l4) and B4's experiment with
B2's report of the release's two runs. Nothing is laid over the records. Test-only doors:
`/_test/probe` (the records port's own refusal), `/_test/launch`, `/_test/stop`,
`/_test/traffic`, `/_test/step` (R2's rollback pass on R1's aggregates as given),
`/_test/decide`. `composed` names the ports of `pilot._lab`'s `lab_releases` factory, the one
the control unit mounts (R186) - read here from a second call of it (LR6-RV-4).

    INFRX_D_TASK=l4 uv run --frozen --project apps/infrx-api python apps/lab/tests/e2e/rollout/backend.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stack  # noqa: E402

PORTS = ("records", "proposals", "store")


def main() -> None:
    import tempfile
    import uuid

    from infrx.contracts import errors
    from infrx.contracts.lab import records as lab
    from infrx.gateway import pilot
    from infrx.lab.workers import __main__ as lab_workers
    from infrx.media.store import InMemoryObjectStore
    from infrx.rollouts import control as r2
    from infrx.state.jobstore import connector
    from infrx.state.lab_data import PgLabDataStore, PgLabReads
    from infrx.state.lab_rollout import PgLabRolloutStore, PgReleaseProposals, PgReleaseStore
    from tests.b.runner.world import EVALUATOR_ID, SPEC, harness, manifest, uid
    from tests.d import checks_credit as cc
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_d9_rollout as d9
    from tests.d import test_l2sql_access as l2
    from tests.d.test_code_mutants_live import job, ref_of
    from tests.r.control import test_control as r2w

    conn, dsn = stack.database("rollout")
    d9.seed(conn)
    # the seed freezes the database clock (admission's world); the journey runs on the wall
    # clock `rollout decide` evaluates an expansion at (R2's max_lag_s against D9's Live)
    conn.execute("select infrx_test.unfreeze()")
    connect = connector(dsn)
    store, pg, NEMO = PgReleaseStore(connect), PgReleaseProposals(connect), d9.NEMO
    data, reads = PgLabDataStore(connect), PgLabReads(connect)
    candidate = ref_of(conn, cc.DEV_DEPLOYMENT)            # ready_private: R247's healthy arm
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

    # R2's plan, small enough for a few rows of traffic to reach its horizon and evidence
    base_run = asyncio.run(suite())
    plan = r2w.plan(horizon_s=1, min_requests=2, max_skew_bp=10_000)
    launched: dict[str, dict] = {}              # policy_ref -> policy, R2's last verdict, alias
    plan_path = Path(tempfile.mkdtemp(prefix="lab-e2e-rollout-")) / "plan.json"
    plan_path.write_text(plan.model_dump_json())
    # `rollout launch|decide` in process: the gateway's objects are theirs (l4 has no S3), and
    # R2's stop converges the release's own alias (FakeServing: the d9 world lists no endpoint)
    env = {"LAB_DATABASE_URL": dsn, "LAB_S3_BUCKET": "l4-in-memory"}

    def runs_of(policy, tag: int) -> tuple[dict, dict]:
        """The release's own baseline and candidate evaluation runs (B2's report binds them)."""
        return tuple({**base_run, "run_id": d9.uid(tag, arm), "serving_ref": serving,
                      "idempotency_key": f"run:{d9.uid(tag, arm)}"}
                     for arm, serving in ((0x1e, policy.baseline_ref),
                                          (0x1f, policy.candidates[0].serving_ref)))

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
        payload = {**d9.policy(d9.uid(tag, 0xb0), weights=(1_000,), endpoint=d9.uid(tag, 0xe0)),
                   "baseline_ref": d9.serving(10 + tag),     # its own: B4's experiment is its
                   "candidates": [{"serving_ref": candidate, "weight_bp": 1_000}]}
        policy, ref = lab.parse(payload), d7.publish(conn, payload)
        if body.get("planless"):
            await store.start(ref, provider_org_id=NEMO, plan_digest=r2.plan_digest(plan),
                              decided_by=d9.USER, reason="canary 10%")
        else:
            code = await lab_workers.launch_release({**env, "LAB_OPERATOR_ID": d9.USER}, ref,
                                                    str(plan_path), "canary 10%")
            assert code == 0, f"rollout launch exited {code}"
        for run in runs_of(policy, tag):
            assert d7.publish(conn, run) == lab.ref_of(run)
            d7.ok(conn, "lab_create_run", {"provider_org_id": NEMO, "run_ref": lab.ref_of(run)})
        launched[ref] = {"policy": policy, "payload": payload, "tag": tag,
                         "serving": r2w.FakeServing(current=policy.candidates[0].serving_ref)}
        return {"policy_ref": ref}

    @app.post("/_test/traffic")
    async def traffic(body: dict):
        """Healthy traffic past the plan's horizon and B2's `report` of the release's runs:
        two candidate jobs (each with an operator's feedback) and one baseline job assigned to
        the release (0054 reads them as its Live), B4's experiment of the two runs under the
        plan's protocol and B2's report stored in D7. Answers the composed records' verdict."""
        ref = body["policy_ref"]
        rel = launched[ref]
        base, cand = runs_of(rel["policy"], rel["tag"])
        n = rel["tag"] * 10
        for i, serving in ((1, candidate), (2, candidate), (3, rel["policy"].baseline_ref)):
            job(conn, ref, rel["payload"], d9.uid(n + i, 0x9c), serving, "succeeded", ms=20,
                feedback=("operator",) if serving == candidate else ())
        await reads.put_experiment(str(uuid.uuid4()), provider_org_id=NEMO,
                                   protocol=plan.protocol,
                                   protocol_digest=r2w.digest(plan.protocol),
                                   baseline_run_ref=lab.ref_of(base),
                                   candidate_run_ref=lab.ref_of(cand), actor=d9.USER)
        await data.put_eval_report(r2w.report(body["report"], base_run=base, cand_run=cand),
                                   provider_org_id=NEMO, actor=d9.USER)
        started = (await store.release(ref)).started_at        # the horizon, on the DB clock
        now = conn.execute("select infrx.now()").fetchone()[0]
        await asyncio.sleep(max(0.0, plan.horizon_s - (now - started).total_seconds()) + 0.1)
        [row] = [r for r in await gateway.records.releases(NEMO) if r["policy_ref"] == ref]
        return {"action": (row["verdict"] or {}).get("action"), "verdict": row["verdict"],
                "progress": row["progress"]}

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
        """One R2 controller pass on R1's aggregates as given: a guardrail breach."""
        rel = launched[body["policy_ref"]]
        live, runs = r2w.live(errors_=body.get("errors", 20)), runs_of(rel["policy"], rel["tag"])
        ctl = r2.Controller(store, rel["serving"], actor_id=r2w.CONTROLLER)
        verdict = await ctl.step(rel["policy"], body["policy_ref"], plan, live, now=r2w.HORIZON,
                                 report=None, runs=runs)
        return {"action": verdict.action}

    @app.post("/_test/decide")
    async def decide(body: dict):
        """The operator decides a release's pending proposal through `rollout decide`: a
        rollback, a rejection, or an expansion (on R2's expand verdict, WR-LIVE-DECIDE)."""
        rows = await pg.proposals(provider_org_id=NEMO)
        p = next(r for r in rows if r["policy_ref"] == body["policy_ref"]
                 and r["state"] == "proposed")
        serving[0] = launched[p["policy_ref"]]["serving"]
        code = await lab_workers.decide_proposal({**env, "LAB_OPERATOR_ID": r2w.OPERATOR},
                                                 p["policy_ref"], str(p["proposal_id"]),
                                                 bool(body["approve"]), "proposal")
        return {"result": ("ok" if body["approve"] else "rejected") if code == 0 else "conflict"}

    world = {"A": NEMO, "B": l2.OTHER, "operator": r2w.OPERATOR, "controller": r2w.CONTROLLER,
             "users": users, "composed": composed,
             "stand_ins": ["session verifier and PostgREST RPC door (stack.door)",
                           "the Lab objects in memory (l4 has no S3), handed to the control "
                           "unit and to `rollout launch|decide` (run in process to share them)",
                           "R1's assignments and the admitted jobs they name written as rows "
                           "(tests/d's job fixture; no R1 traffic on l4)",
                           "R1 aggregates as the rollback step's input (/_test/step)",
                           "L3's serving alias (R2's FakeServing)"]}
    stack.serve(app, sock, world, conn.close)


if __name__ == "__main__":
    os.chdir(stack.API)
    main()
