#!/usr/bin/env python3
"""R4 swap's real backend for `stack.test.ts`: lab-api's `/lab/v1/releases` and
`/lab/v1/optimizations` (`lab_releases.register`, WR-R4-1) served as merged over the REAL D9
(`PgReleaseStore`, 0033/0039: each release row, its fence CAS and its decision events), R2's own
`Controller` deciding on that store, and the REAL L2 (`LabAccess(PgAccessStore)`: the actor, the
role and the proposal's clock) on the task-local PostgreSQL (`INFRX_D_TASK=r2`, port 57534), D9's
seeded world; each policy revision and its two B1 runs are published through D7. The read models (WR-R4-1's lab-sql
half: D9's rows and events as the port's records, R1's progress, R2's latest verdict, R3's
variants) are this backend's own small listing over those real rows; the proposal store (WR-R4-2)
is the route suite's fake; L3's serving alias is R2's `FakeServing`. Test-only: the
bearer-token-per-user stand-in for `GoTrueSessions`, and `/_test/*` doors standing in for the
launcher, R2's controller pass and the operator (approve, emergency rollback, reject).

    INFRX_D_TASK=r2 uv run --frozen --project apps/infrx-api python apps/lab/tests/r/backend.py
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

Z = "%Y-%m-%dT%H:%M:%SZ"


def main() -> None:
    import uvicorn

    from infrx.contracts import errors
    from infrx.contracts.lab import records as lab
    from infrx.gateway import lab_auth
    from infrx.gateway.routes import lab_releases as lr
    from infrx.lab.access import LabAccess
    from infrx.rollouts import control as r2
    from infrx.state import migrations
    from infrx.state.jobstore import connector
    from infrx.state.lab_access import PgAccessStore
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_rollout import PgReleaseStore
    from tests.b.runner.world import EVALUATOR_ID, SPEC, harness, manifest, uid
    from tests.d import pgharness
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_d9_rollout as d9
    from tests.d import test_l2sql_access as l2
    from tests.g import support
    from tests.g.lab_releases.test_lab_releases import Proposals, Sessions, token
    from tests.r.control import test_control as r2w

    if os.environ.get("INFRX_D_TASK") != "r2":
        raise SystemExit("the r2 task-local key only (INFRX_D_TASK=r2)")
    db = f"{pgharness.DATABASE}_labui"
    pgharness.ensure()
    pgharness.recreate(db)
    pgharness.apply(db, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    conn = pgharness.connect(db)
    d9.seed(conn)
    connect = connector(pgharness.dsn(db))
    store, NEMO = PgReleaseStore(connect), d9.NEMO
    data = PgLabDataStore(connect)

    async def suite() -> dict:
        """NEMO's dataset, harness and evaluator in D7: what its two B1 runs reference."""
        dataset = await data.publish(manifest(3, d7.W["grant"], d7.W["source"], dataset=7),
                                     provider_org_id=NEMO, actor=d9.USER)
        harness_ref = await data.publish(harness(harness_id=uid(7, 0xa7)), provider_org_id=NEMO,
                                         actor=d9.USER)
        evaluator = await data.put_evaluator(SPEC, provider_org_id=NEMO,
                                             evaluator_id=EVALUATOR_ID, actor=d9.USER)
        return {**r2w.BASE_RUN, "provider_org_id": NEMO, "dataset_ref": dataset,
                "harness_ref": harness_ref, "evaluator_ref": evaluator}

    base_run = asyncio.run(suite())
    plan = r2w.plan()
    launched: dict[str, dict] = {}                      # policy_ref -> policy, controller, R1/R2

    def plan_row() -> dict:
        p = plan.model_dump(mode="json")
        return {**{k: p[k] for k in ("horizon_s", "min_requests", "max_error_rate", "max_p99_ms",
                                     "max_skew_bp", "min_quality_coverage", "max_lag_s")},
                "budget": {"amount": p["budget"]["value"], "unit": p["budget"]["unit"]}}

    def runs_of(policy) -> tuple[dict, dict]:
        """R2's two B1 run fixtures, NEMO's, bound to this revision's two servings."""
        return ({**base_run, "serving_ref": policy.baseline_ref},
                {**base_run, "run_id": r2w.CAND_RUN["run_id"],
                 "serving_ref": policy.candidates[0].serving_ref,
                 "idempotency_key": r2w.CAND_RUN["idempotency_key"]})

    def arm(a) -> dict:
        return {"requests": a.requests, "errors": a.errors, "p99_ms": a.p99_ms}

    class Records:
        """WR-R4-1's read models over the real rows: D9's release and events, R1/R2 as last seen."""

        async def releases(self, provider_org_id):
            out = []
            for ref, x in launched.items():
                if provider_org_id != NEMO:
                    continue
                pol, row, live, verdict = x["policy"], await store.release(ref), x["live"], x["verdict"]
                out.append({
                    "policy_ref": ref, "endpoint_id": pol.endpoint_id, "version": pol.version,
                    "baseline_ref": pol.baseline_ref, "mode": pol.mode, "cohort": pol.cohort,
                    "candidates": [{"serving_ref": c.serving_ref, "weight_bp": c.weight_bp}
                                   for c in pol.candidates],
                    "state": row.state, "fence": row.fence, "plan_digest": row.plan_digest,
                    "plan": plan_row(), "started_at": row.started_at.strftime(Z),
                    "progress": None if live is None else {
                        "observed_until": live.observed_until.strftime(Z),
                        "baseline": arm(live.baseline), "candidate": arm(live.candidate),
                        "quality_covered": live.quality_covered,
                        "spent": {"amount": live.spent.value, "unit": live.spent.unit},
                        "candidate_healthy": live.candidate_healthy, "assignments": []},
                    "verdict": None if verdict is None else {
                        "action": verdict.action, "reasons": list(verdict.reasons),
                        "evidence_refs": list(verdict.evidence_refs),
                        "evaluated_at": r2w.HORIZON.strftime(Z)}})
            return out

        async def decisions(self, provider_org_id):
            found = conn.execute(
                "select e.policy_ref, coalesce(e.decision, e.decision_doc->>'decision'), e.reasons, "
                "e.evidence_refs, e.decided_by::text, e.at from infrx.lab_rollout_events e join "
                "infrx.lab_rollouts o using (policy_id) where o.provider_org_id = %s and "
                "e.action <> 'start' order by e.at, e.fence", (provider_org_id,)).fetchall()
            return [{"policy_ref": ref, "decision": decision, "reasons": list(reasons),
                     "evidence_refs": list(evidence), "decided_by": by, "decided_at": at.strftime(Z)}
                    for ref, decision, reasons, evidence, by, at in found]

        async def variants(self, provider_org_id):
            ident = {"engine": "vllm", "engine_version": "0.11.2", "hardware": "B300",
                     "quantization": "bf16", "capabilities": ["text"]}
            return [] if provider_org_id != NEMO else [{
                "variant_ref": f"lab:variant:{NEMO}:{d9.uid(1, 0x7a)}@sha256:{'7' * 64}",
                "base_serving_ref": d9.CAND, "variant_serving_ref": d9.CAND2,
                "changes": ["quantization:nvfp4"], "base": ident,
                "variant": {**ident, "quantization": "nvfp4"}, "comparison": None}]

    users = {"admin": l2.ADMIN, "dev": l2.DEV, "viewer": l2.VIEWER, "other_dev": l2.BOTH,
             "consumer": l2.C1}
    proposals = Proposals()
    x = lr.LabReleases(Sessions(users.values()), LabAccess(PgAccessStore(connect)),
                       records=Records(), proposals=proposals, store=store)
    app = FastAPI()
    lr.register(app, support.runtime(), x)
    refusal = lab_auth.refusal
    lab_auth.refusal = lambda exc: (print(f"refusal: {exc!r}", file=sys.stderr), refusal(exc))[1]

    @app.post("/_test/launch")
    async def launch(request: Request):
        """The launcher publishes a canary revision through D7 and starts it in D9."""
        tag = int((await request.json())["tag"])
        payload = d9.policy(d9.uid(tag, 0xb0), weights=(1_000,), endpoint=d9.uid(tag, 0xe0))
        policy, ref = lab.parse(payload), d7.publish(conn, payload)
        await store.start(ref, provider_org_id=NEMO, plan_digest=r2.plan_digest(plan),
                          decided_by=d9.USER, reason="canary 10%")
        for run in runs_of(policy):             # D9: an expansion's evidence is NEMO's own runs
            assert d7.publish(conn, run) == lab.ref_of(run)
        serving = r2w.FakeServing(current=policy.candidates[0].serving_ref)
        launched[ref] = {"policy": policy, "ctl": r2.Controller(store, serving,
                                                                actor_id=r2w.CONTROLLER),
                         "live": None, "verdict": None}
        return {"policy_ref": ref}

    @app.post("/_test/step")
    async def step(request: Request):
        """One R2 controller pass on R1's aggregates (and B2's report, if any)."""
        body = await request.json()
        rel = launched[body["policy_ref"]]
        live, runs = r2w.live(errors_=body.get("errors", 20)), runs_of(rel["policy"])
        rep = None if body.get("report") is None else r2w.report(body["report"], base_run=runs[0],
                                                                 cand_run=runs[1])
        verdict = await rel["ctl"].step(rel["policy"], body["policy_ref"], plan, live,
                                        now=r2w.HORIZON, report=rep, runs=runs)
        if verdict.action in ("rollback", "hold", "expand"):   # not a settled release's state
            rel["verdict"], rel["live"] = verdict, live
        return {"action": verdict.action, "reasons": list(verdict.reasons)}

    @app.post("/_test/decide")
    async def decide(request: Request):
        """The operator decides a proposal through R2 (approve / emergency rollback) or rejects
        it; D9's CAS refuses a stale one, which is then rejected."""
        body = await request.json()
        owner, p = next((o, p) for o, p in proposals.rows if p["proposal_id"] == body["proposal_id"])
        rel, now = launched[p["policy_ref"]], (await x.access.store.db_now()).strftime(Z)
        result = "ok"
        if body["approve"]:
            try:
                if p["kind"] == "expand":
                    runs = runs_of(rel["policy"])
                    await rel["ctl"].approve(r2w.OPERATOR, rel["policy"], p["policy_ref"], plan,
                                             r2w.live(), now=r2w.HORIZON,
                                             report=r2w.report(base_run=runs[0], cand_run=runs[1]),
                                             runs=runs)
                else:
                    await rel["ctl"].emergency_rollback(r2w.OPERATOR, rel["policy"],
                                                        p["policy_ref"], now=r2w.HORIZON,
                                                        reason="operator:proposal")
            except errors.StateConflict:
                result = "conflict"
        p.update(state="approved" if body["approve"] and result == "ok" else "rejected",
                 decided_at=now)
        return {"result": result}

    world = {"A": NEMO, "B": l2.OTHER, "operator": r2w.OPERATOR, "controller": r2w.CONTROLLER,
             "servings_run_ids": [r2w.BASE_RUN["run_id"], r2w.CAND_RUN["run_id"]],
             "tokens": {name: token(user) for name, user in users.items()}}
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
