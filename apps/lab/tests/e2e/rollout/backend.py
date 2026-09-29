#!/usr/bin/env python3
"""LAB-E2E rollout (E8L k10's UI half): the Lab's releases page over lab-api's
`/lab/v1/releases` as the gateway composes it with LAB_RELEASES on (`pilot._lab`), beside R186's
control factory, on the task-local PostgreSQL (l4), D9's seeded world and the real L2.

The gateway's composition carries the records, proposal and store ports since WR-R4-2
(merge #50), over the Lab objects (in memory: l4 has no S3): `/_test/composition {"as":
"gateway"}` serves exactly it, and it fails closed here because the seeded releases were started
without a stored plan (R241: a 503 naming WR-C5-PLAN). The composed ports themselves are proven
on E8L's stack by k10's port half (`rollout launch|decide`). `{"as": "journey"}` replaces
them for the journey, because the page's verdicts and progress are R2's and R1's, which no
composed read holds yet (WR-C6-LIVE): D9's REAL `PgReleaseStore` (0039 CAS, 0048
`lab_releases_in`) and 0043's REAL proposal store (`PgReleaseProposals`: one pending per
revision, filed at the fence the page showed, decided only by 0039's CAS at that fence) behind
two small test-local adapters (the route's `ReleaseRecords` / `Proposals` shapes), with R2's own
`Controller`
deciding; R1's aggregates and R2's latest verdict are this backend's last-seen values (no table
holds them yet). `world.composed` says which ports the gateway's own composition carries, so the
gate reports NOT RUN until it carries them. Test-only doors: `/_test/probe` (the gateway records port's own refusal), `/_test/launch`, `/_test/step`
(R2's pass), `/_test/decide` (the operator, through R2, whose transition is the proposal's own
decide RPC).

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
    import dataclasses

    from infrx.contracts import errors
    from infrx.contracts.lab import records as lab
    from infrx.gateway.routes import lab_releases as lr
    from infrx.media.store import InMemoryObjectStore
    from infrx.rollouts import control as r2
    from infrx.state.jobstore import connector
    from infrx.state.lab_data import PgLabDataStore
    from infrx.state.lab_rollout import PgReleaseProposals, PgReleaseStore
    from tests.b.runner.world import EVALUATOR_ID, SPEC, harness, manifest, uid
    from tests.d import test_d7_lab_data as d7
    from tests.d import test_d9_rollout as d9
    from tests.d import test_l2sql_access as l2
    from tests.g import support
    from tests.r.control import test_control as r2w

    conn, dsn = stack.database("rollout")
    d9.seed(conn)
    connect = connector(dsn)
    store, pg, NEMO = PgReleaseStore(connect), PgReleaseProposals(connect), d9.NEMO
    data = PgLabDataStore(connect)
    sock, url = stack.listen()
    app = stack.control_app(dsn, url)
    users = {"admin": l2.ADMIN, "dev": l2.DEV, "viewer": l2.VIEWER, "other_dev": l2.BOTH,
             "consumer": l2.C1}
    stack.door(app, dsn, users)
    gateway = stack.composed("lab_releases", dsn, url, InMemoryObjectStore())["lab_releases"]
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
    launched: dict[str, dict] = {}              # policy_ref -> policy, R1's last aggregates, R2's verdict

    def runs_of(policy) -> tuple[dict, dict]:
        return ({**base_run, "serving_ref": policy.baseline_ref},
                {**base_run, "run_id": r2w.CAND_RUN["run_id"],
                 "serving_ref": policy.candidates[0].serving_ref,
                 "idempotency_key": r2w.CAND_RUN["idempotency_key"]})

    def arm(a) -> dict:
        return {"requests": a.requests, "errors": a.errors, "p99_ms": a.p99_ms}

    def plan_row() -> dict:
        p = plan.model_dump(mode="json")
        return {**{k: p[k] for k in ("horizon_s", "min_requests", "max_error_rate", "max_p99_ms",
                                     "max_skew_bp", "min_quality_coverage", "max_lag_s")},
                "budget": {"amount": p["budget"]["value"], "unit": p["budget"]["unit"]}}

    class Records:
        """The route's `ReleaseRecords` over 0048's `lab_releases_in` (D9's rows) and D9's
        decision events, with the launched policy's shape and R1/R2's last-seen values."""

        async def releases(self, provider_org_id):
            out = []
            for row in await store.releases_in(provider_org_id=provider_org_id):
                x = launched[row.policy_ref]
                pol, live, verdict = x["policy"], x["live"], x["verdict"]
                out.append({
                    "policy_ref": row.policy_ref, "endpoint_id": row.endpoint_id,
                    "version": pol.version, "baseline_ref": pol.baseline_ref, "mode": pol.mode,
                    "cohort": pol.cohort, "candidates": [
                        {"serving_ref": c.serving_ref, "weight_bp": c.weight_bp}
                        for c in pol.candidates],
                    "state": row.release.state, "fence": row.release.fence,
                    "plan_digest": row.release.plan_digest, "plan": plan_row(),
                    "started_at": row.release.started_at.strftime(Z),
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
            return []

    def proposal(row: dict) -> dict:
        at = lambda v: None if v is None else v[:19] + "Z"   # noqa: E731 - the port's instant
        return {"proposal_id": row["proposal_id"], "kind": row["kind"],
                "policy_ref": row["policy_ref"], "fence": row["fence"], "state": row["state"],
                "proposed_at": at(row["proposed_at"]), "decided_at": at(row["decided_at"])}

    class Proposals:
        """The route's `Proposals` over 0043's store. The route hands `add` no actor, so the
        proposer recorded is the world's administrator (the only role that may propose)."""

        async def proposals(self, provider_org_id):
            return [proposal(r) for r in await pg.proposals(provider_org_id=provider_org_id)]

        async def add(self, provider_org_id, p):
            return proposal(await pg.propose(p["policy_ref"], provider_org_id=provider_org_id,
                                             proposal_id=p["proposal_id"], kind=p["kind"],
                                             fence=p["fence"], proposed_by=l2.ADMIN))

    journey = dataclasses.replace(gateway, records=Records(), proposals=Proposals(), store=store)

    class Switch:
        """The mounted `LabReleases`: the gateway's own composition, or the journey's."""
        current = gateway

        def __getattr__(self, name):
            return getattr(self.current, name)

    switch = Switch()
    lr.register(app, support.runtime(), switch)

    class Decided:
        """R2's `ReleaseStore` for one operator decision: its transition is the proposal's own
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
        switch.current = {"gateway": gateway, "journey": journey}[body["as"]]
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
        """The launcher publishes a canary revision through D7 and starts it in D9."""
        tag = int(body["tag"])
        payload = d9.policy(d9.uid(tag, 0xb0), weights=(1_000,), endpoint=d9.uid(tag, 0xe0))
        policy, ref = lab.parse(payload), d7.publish(conn, payload)
        await store.start(ref, provider_org_id=NEMO, plan_digest=r2.plan_digest(plan),
                          decided_by=d9.USER, reason="canary 10%")
        for run in runs_of(policy):
            assert d7.publish(conn, run) == lab.ref_of(run)
        launched[ref] = {"policy": policy, "live": None, "verdict": None,
                         "serving": r2w.FakeServing(current=policy.candidates[0].serving_ref)}
        return {"policy_ref": ref}

    @app.post("/_test/step")
    async def step(body: dict):
        """One R2 controller pass on R1's aggregates (and B2's report, if any)."""
        rel = launched[body["policy_ref"]]
        live, runs = r2w.live(errors_=body.get("errors", 20)), runs_of(rel["policy"])
        rep = None if body.get("report") is None else r2w.report(body["report"], base_run=runs[0],
                                                                 cand_run=runs[1])
        ctl = r2.Controller(store, rel["serving"], actor_id=r2w.CONTROLLER)
        verdict = await ctl.step(rel["policy"], body["policy_ref"], plan, live, now=r2w.HORIZON,
                                 report=rep, runs=runs)
        if verdict.action in ("rollback", "hold", "expand"):
            rel["verdict"], rel["live"] = verdict, live
        return {"action": verdict.action}

    @app.post("/_test/decide")
    async def decide(body: dict):
        """The operator decides a release's pending proposal through R2 (approve an expansion with its evidence,
        or roll back); a stale one is refused by D9's CAS, then rejected."""
        rows = await pg.proposals(provider_org_id=NEMO)
        p = next(r for r in rows if r["policy_ref"] == body["policy_ref"]
                 and r["state"] == "proposed")
        rel = launched[p["policy_ref"]]
        ctl = r2.Controller(Decided(p["proposal_id"]), rel["serving"], actor_id=r2w.CONTROLLER)
        try:
            if not body["approve"]:
                raise errors.StateConflict("rejected by the operator")
            if p["kind"] == "expand":
                runs = runs_of(rel["policy"])
                await ctl.approve(r2w.OPERATOR, rel["policy"], p["policy_ref"], plan, r2w.live(),
                                  now=r2w.HORIZON, report=r2w.report(base_run=runs[0],
                                                                     cand_run=runs[1]), runs=runs)
            else:
                await ctl.emergency_rollback(r2w.OPERATOR, rel["policy"], p["policy_ref"],
                                             now=r2w.HORIZON, reason="operator:proposal")
            return {"result": "ok"}
        except errors.StateConflict:
            await pg.decide(p["proposal_id"], approve=False, decided_by=r2w.OPERATOR)
            return {"result": "rejected" if not body["approve"] else "conflict"}

    world = {"A": NEMO, "B": l2.OTHER, "operator": r2w.OPERATOR, "controller": r2w.CONTROLLER,
             "users": users, "composed": composed,
             "stand_ins": ["session verifier and PostgREST RPC door (stack.door)",
                           "the journey's records/proposals adapters over D9 + 0043: R2's "
                           "verdicts and R1's progress have no composed read (WR-C6-LIVE)",
                           "the Lab objects in memory (l4 has no S3)",
                           "R1 aggregates and R2 verdict as last seen (no table yet)",
                           "L3's serving alias (R2's FakeServing)"]}
    stack.serve(app, sock, world, conn.close)


if __name__ == "__main__":
    os.chdir(stack.API)
    main()
