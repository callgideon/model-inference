#!/usr/bin/env python3
"""R2: guardrails, operator approval and CAS rollback (ROLLOUT-RECOVER, EVAL-COMPARE).

D9 (release policies, CAS transitions), L3 (the serving publish/rollback CAS) and B2 (the
comparison report) are not on this lane's base: D9 and L3 are the fakes below, shaped by
their briefs (the exact signatures are this lane's schema/wiring requests), and a B2 report
is built here in B2's `infrx.eval_report.1` shape (codex/w5-eval-runner d87408fc), digests
computed the way B2 computes them. Real integration is claimed only after they merge.

    uv run --frozen pytest -q tests/r/control/test_control.py
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records as lab
from infrx.rollouts import control as r2

P = "11111111-1111-4111-8111-111111111111"
CONTROLLER, OPERATOR = "00000090-0000-4000-8000-000000000090", "00000091-0000-4000-8000-000000000091"
BASE = f"lab:serving:{P}:00000028-0000-4000-8000-000000000028@sha256:{'f' * 64}"
CAND = f"lab:serving:{P}:00000029-0000-4000-8000-000000000029@sha256:{'b' * 64}"
POLICY = lab.parse({
    "schema": "lab.rollout_policy.1", "provider_org_id": P,
    "policy_id": "0000006e-0000-4000-8000-00000000006e", "version": 3,
    "created_at": "2026-09-27T10:00:00Z", "endpoint_id": "0000006f-0000-4000-8000-00000000006f",
    "baseline_ref": BASE, "mode": "canary", "cohort": "account",
    "candidates": [{"serving_ref": CAND, "weight_bp": 1_000}]})
POLICY_REF = lab.ref_of(POLICY.model_dump(mode="json", by_alias=True))   # D9 stores this ref
RUN = {
    "schema": "lab.eval_run.1", "provider_org_id": P,
    "run_id": "0000001e-0000-4000-8000-00000000001e", "created_at": "2026-09-27T10:00:00Z",
    "dataset_ref": f"lab:dataset:{P}:0000000a-0000-4000-8000-00000000000a@sha256:{'d' * 64}",
    "harness_ref": f"lab:harness:{P}:00000014-0000-4000-8000-000000000014@sha256:{'c' * 64}",
    "serving_ref": BASE,
    "evaluator_ref": f"lab:evaluator:{P}:00000032-0000-4000-8000-000000000032@sha256:{'a' * 64}",
    "seed": 7, "environment": "dev", "max_cases": 100, "state": "succeeded",
    "idempotency_key": "run:0000001e-0000-4000-8000-00000000001e",
    "budgets": [{"limit": {"unit": "CREDIT", "value": "500.00000000"},
                 "reserved": {"unit": "CREDIT", "value": "0.00000000"}}]}
BASE_RUN = RUN
CAND_RUN = {**RUN, "run_id": "0000001f-0000-4000-8000-00000000001f", "serving_ref": CAND,
            "idempotency_key": "run:0000001f-0000-4000-8000-00000000001f"}
PROTOCOL = {"confidence": 0.95, "margin": 0.02, "min_cases": 30,
            "metric_source": "deterministic_metric", "required_slices": {}}
START = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
HORIZON = START + timedelta(hours=24)
PLAN = {"horizon_s": 86_400, "min_requests": 1_000, "max_error_rate": 0.02, "max_p99_ms": 9_000,
        "max_skew_bp": 100, "min_quality_coverage": 0.5, "max_lag_s": 300,
        "budget": {"unit": "CREDIT", "value": "100.00000000"}, "protocol": PROTOCOL}


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(lab.canonical(value)).hexdigest()


def report(outcome="accept", *, base_run=BASE_RUN, cand_run=CAND_RUN, protocol=PROTOCOL,
           reasons=()):
    """A B2 report (`infrx.eval_report.1`), digest-bound as B2 binds it."""
    body = {"schema": "infrx.eval_report.1", "baseline_run": lab.ref_of(base_run),
            "candidate_run": lab.ref_of(cand_run), "universe_digest": digest([]),
            "protocol": protocol, "protocol_digest": digest(protocol),
            "pairing": {"kind": "single_factor", "factors": ["serving_ref"], "tag": None},
            "observed": {}, "estimates": {}, "decision": {"outcome": outcome, "reasons": list(reasons)}}
    return {**body, "report_digest": digest(body)}


def live(*, requests=1_000, errors_=20, p99=9_000, covered=500, spent="100.00000000",
         unit="CREDIT", healthy=True, lag_s=300, baseline=9_000):
    """At every threshold exactly: a candidate at 10% of traffic, on the plan's bounds."""
    return r2.Live(observed_until=HORIZON - timedelta(seconds=lag_s),
                   baseline=r2.Arm(requests=baseline, errors=0, p99_ms=8_000),
                   candidate=r2.Arm(requests=requests, errors=errors_, p99_ms=p99),
                   quality_covered=covered, spent=lab.Amount(unit=unit, value=spent),
                   candidate_healthy=healthy)


def plan(**changes):
    return r2.Plan.model_validate({**copy.deepcopy(PLAN), **changes})


def evaluate(live_=None, *, now=HORIZON, rep=None, runs=(BASE_RUN, CAND_RUN), plan_=None):
    return r2.evaluate(plan_ or plan(), POLICY, live_ or live(), started_at=START, now=now,
                       report=rep, runs=runs)


class FakeReleases:
    """D9's port: one row per policy revision, CAS on a fence, declared transitions only."""

    MOVES = {"running": {"approved", "rolled_back"}, "approved": {"rolled_back"}}

    def __init__(self, plan_=None):
        self.row = r2.Release(state="running", fence=1, plan_digest=r2.plan_digest(plan_ or plan()),
                              started_at=START)
        self.decisions: list[tuple[dict, tuple[str, ...]]] = []

    async def release(self, policy_ref):
        assert policy_ref == POLICY_REF
        await asyncio.sleep(0)            # let a concurrent controller read the same row
        return self.row

    async def transition(self, policy_ref, *, fence, to, decision, reasons):
        await asyncio.sleep(0)
        if fence != self.row.fence or to not in self.MOVES.get(self.row.state, ()):
            raise errors.StateConflict(f"{self.row.state}@{self.row.fence} -> {to}@{fence}")
        lab.parse(decision)
        self.decisions.append((decision, reasons))
        self.row = r2.Release(state=to, fence=fence + 1, plan_digest=self.row.plan_digest,
                              started_at=START)
        return self.row.fence


class FakeServing:
    """L3's port: the endpoint's serving alias under a fence; `fail` crashes one rollback."""

    def __init__(self, current=BASE, fail=0):
        self.current, self.fence, self.fail, self.rollbacks = current, 1, fail, []

    async def serving(self, endpoint_id):
        await asyncio.sleep(0)
        return self.current, self.fence

    async def rollback(self, endpoint_id, *, fence, to_serving_ref, reason):
        await asyncio.sleep(0)
        if self.fail:
            self.fail -= 1
            raise ConnectionError("controller died mid-rollback")
        if fence != self.fence:
            raise errors.StateConflict("stale serving fence")
        self.rollbacks.append((endpoint_id, to_serving_ref, reason))
        self.current, self.fence = to_serving_ref, fence + 1
        return self.fence


def controller(store, serving):
    return r2.Controller(store, serving, actor_id=CONTROLLER)


def step(ctl, live_=None, *, now=HORIZON, rep=None, plan_=None):
    return ctl.step(POLICY, POLICY_REF, plan_ or plan(), live_ or live(), now=now, report=rep,
                    runs=(BASE_RUN, CAND_RUN))


# --- R2.a: the plan is frozen before launch -----------------------------------------------
def test_r2_the_plan_has_no_defaults_and_cannot_change_after_launch():
    for field in PLAN:
        with pytest.raises(Exception):
            r2.Plan.model_validate({k: v for k, v in PLAN.items() if k != field})
    for bad in ({"max_error_rate": 1.5}, {"min_quality_coverage": 0}, {"max_skew_bp": -1},
                {"horizon_s": 0}, {"min_requests": 0}, {"extra": 1}):
        with pytest.raises(Exception):
            plan(**bad)
    store, serving = FakeReleases(), FakeServing(current=CAND)
    edited = plan(max_error_rate=0.5)             # loosened after launch
    with pytest.raises(errors.StateConflict):
        asyncio.run(step(controller(store, serving), live(errors_=400), plan_=edited))
    assert store.decisions == [] and serving.rollbacks == []
    assert r2.plan_digest(edited) != r2.plan_digest(plan())


# --- R2.b: guardrails --------------------------------------------------------------------
def test_r2_every_threshold_is_inclusive_and_a_healthy_candidate_at_the_horizon_expands():
    verdict = evaluate(rep=report())
    assert (verdict.action, verdict.reasons) == ("expand", ())
    assert verdict.evidence_refs == (lab.ref_of(BASE_RUN), lab.ref_of(CAND_RUN))
    # 11% of traffic against a 10% weight is exactly the 100 bp skew bound
    assert evaluate(live(requests=1_100, covered=550, baseline=8_900), rep=report()).action == "expand"


@pytest.mark.parametrize("breach, reason", [
    ({"errors_": 21}, "error_rate"), ({"p99": 9_001}, "latency"),
    ({"healthy": False}, "candidate_unhealthy"), ({"spent": "100.00000001"}, "budget")])
def test_r2_an_operational_breach_rolls_back_without_any_judge(breach, reason):
    verdict = evaluate(live(**breach), rep=None, runs=None)
    assert verdict.action == "rollback" and verdict.reasons == (reason,)
    # a breach before the horizon, with stale metrics and no evidence, still rolls back
    verdict = evaluate(live(**breach, lag_s=10_000, requests=1_000), now=START, rep=None)
    assert verdict.action == "rollback" and reason in verdict.reasons


def test_r2_budget_units_never_mix():
    with pytest.raises(errors.InvalidRequest):
        evaluate(live(unit="PROVIDER_USD"), rep=report())


@pytest.mark.parametrize("gap, reason", [
    ({"lag_s": 301}, "metrics_stale"), ({"requests": 999, "errors_": 0, "baseline": 8_991},
                                        "min_requests"),
    ({"covered": 499}, "quality_coverage"), ({"baseline": 8_090}, "cohort_skew"),
    ({"baseline": 10_112}, "cohort_skew")])
def test_r2_insufficient_or_delayed_evidence_cannot_approve(gap, reason):
    verdict = evaluate(live(**gap), rep=report())
    assert verdict.action == "hold" and reason in verdict.reasons
    store = FakeReleases()
    ctl = controller(store, FakeServing())
    with pytest.raises(errors.StateConflict):
        asyncio.run(ctl.approve(OPERATOR, POLICY, POLICY_REF, plan(), live(**gap), now=HORIZON,
                                report=report(), runs=(BASE_RUN, CAND_RUN)))
    assert store.decisions == []
    assert asyncio.run(step(ctl, live(**gap), rep=report())).action == "hold"
    assert store.decisions == []                   # a hold is not a decision


def test_r2_no_report_or_an_inconclusive_one_holds():
    assert evaluate(rep=None).reasons == ("no_report",)
    assert evaluate(rep=report("inconclusive")).reasons == ("report_inconclusive",)


def test_r2_peeking_before_the_horizon_is_not_a_win():
    early = HORIZON - timedelta(seconds=1)
    verdict = evaluate(live(lag_s=299), now=early, rep=report("accept"))
    assert (verdict.action, verdict.reasons) == ("hold", ("before_horizon",))
    assert evaluate(now=HORIZON, rep=report("accept")).action == "expand"


def test_r2_a_failed_quality_slice_rolls_back_with_its_evidence():
    verdict = evaluate(rep=report("reject", reasons=["slice sop inferior"]))
    assert verdict.action == "rollback"
    assert verdict.reasons == ("quality_reject", "slice sop inferior")
    assert verdict.evidence_refs == (lab.ref_of(BASE_RUN), lab.ref_of(CAND_RUN))


def test_r2_a_report_must_be_bound_to_this_release():
    other = {**PROTOCOL, "margin": 0.2}
    tampered = {**report("reject"), "decision": {"outcome": "accept", "reasons": []}}
    stranger = {**CAND_RUN, "serving_ref": BASE, "run_id": "00000020-0000-4000-8000-000000000020",
                "idempotency_key": "run:00000020-0000-4000-8000-000000000020"}
    reversed_ = {**BASE_RUN, "serving_ref": CAND}
    cases = {
        "report_tampered": dict(rep=tampered),
        "report_protocol_not_frozen": dict(rep=report(protocol=other)),
        "report_runs_unbound": dict(rep=report(), runs=(BASE_RUN, stranger)),
        "report_not_this_release": dict(rep=report(cand_run=stranger), runs=(BASE_RUN, stranger)),
        "report_not_this_baseline": dict(rep=report(base_run=reversed_), runs=(reversed_, CAND_RUN)),
    }
    for reason, kwargs in cases.items():
        verdict = evaluate(**kwargs)
        assert (verdict.action, verdict.reasons) == ("hold", (reason,)), reason
    # an unbound reject cannot roll back either: only operational triggers and bound reports act
    assert evaluate(rep=report("reject", protocol=other)).action == "hold"
    assert evaluate(rep=report(), runs=None).reasons == ("report_runs_unbound",)


# --- R2.c: approval, rollback exactly once, restart ------------------------------------
def test_r2_operator_approval_records_one_expand_decision():
    store = FakeReleases()
    ctl = controller(store, FakeServing())
    verdict = asyncio.run(ctl.approve(OPERATOR, POLICY, POLICY_REF, plan(), live(), now=HORIZON,
                                      report=report(), runs=(BASE_RUN, CAND_RUN)))
    assert verdict.action == "expand" and store.row.state == "approved"
    [(decision, reasons)] = store.decisions
    assert decision["decision"] == "expand" and decision["decided_by"] == OPERATOR
    assert decision["evidence_refs"] == [lab.ref_of(BASE_RUN), lab.ref_of(CAND_RUN)]
    assert decision["decided_at"] == "2026-09-28T10:00:00Z" and decision["policy_ref"] == POLICY_REF
    # the controller never expands on its own
    store2 = FakeReleases()
    assert asyncio.run(step(controller(store2, FakeServing()), rep=report())).action == "expand"
    assert store2.decisions == [] and store2.row.state == "running"
    with pytest.raises(errors.StateConflict):     # approving twice
        asyncio.run(ctl.approve(OPERATOR, POLICY, POLICY_REF, plan(), live(), now=HORIZON,
                                report=report(), runs=(BASE_RUN, CAND_RUN)))
    # an approved revision is settled for its controller (the next revision has its own
    # plan); an operator's emergency rollback stays available (below)
    assert asyncio.run(step(ctl, live(errors_=21))).action == "approved"
    assert len(store.decisions) == 1


def test_r2_a_breach_rolls_back_exactly_once_under_concurrent_controllers():
    store, serving = FakeReleases(), FakeServing(current=CAND)
    one, two = controller(store, serving), controller(store, serving)

    async def both():
        return await asyncio.gather(step(one, live(errors_=21)), step(two, live(errors_=21)))

    verdicts = asyncio.run(both())
    assert [v.action for v in verdicts] == ["rollback", "rollback"]
    [(decision, reasons)] = store.decisions
    assert decision["decision"] == "rollback" and decision["decided_by"] == CONTROLLER
    assert reasons == ("error_rate",) and store.row.state == "rolled_back"
    assert serving.current == BASE and len(serving.rollbacks) == 1
    assert serving.rollbacks[0] == (POLICY.endpoint_id, BASE, "rollback " + POLICY_REF)


def test_r2_a_restart_converges_without_a_second_decision_or_flapping():
    store, serving = FakeReleases(), FakeServing(current=CAND, fail=1)
    with pytest.raises(ConnectionError):          # killed between the D9 and L3 steps
        asyncio.run(step(controller(store, serving), live(p99=9_001)))
    assert store.row.state == "rolled_back" and serving.current == CAND
    restarted = controller(store, serving)
    # recovered metrics and a winning report do not bring the candidate back
    verdict = asyncio.run(step(restarted, live(), rep=report()))
    assert (verdict.action, verdict.reasons) == ("rolled_back", ())
    assert serving.current == BASE and len(serving.rollbacks) == 1 and len(store.decisions) == 1
    asyncio.run(step(restarted, live(), rep=report()))
    assert len(serving.rollbacks) == 1 and len(store.decisions) == 1
    with pytest.raises(errors.StateConflict):
        asyncio.run(restarted.approve(OPERATOR, POLICY, POLICY_REF, plan(), live(), now=HORIZON,
                                      report=report(), runs=(BASE_RUN, CAND_RUN)))


def test_r2_a_lost_serving_race_rereads_and_converges():
    store, serving = FakeReleases(), FakeServing(current=CAND)

    class Racing(FakeServing):
        """Another controller moves the alias between our read and our CAS."""

        async def rollback(self, endpoint_id, *, fence, to_serving_ref, reason):
            if not serving.rollbacks:
                await serving.rollback(endpoint_id, fence=fence, to_serving_ref=to_serving_ref,
                                       reason=reason)
            return await serving.rollback(endpoint_id, fence=fence, to_serving_ref=to_serving_ref,
                                          reason=reason)

        async def serving(self, endpoint_id):
            return await serving.serving(endpoint_id)

    asyncio.run(step(controller(store, Racing()), live(errors_=21)))
    assert serving.current == BASE and len(serving.rollbacks) == 1
    # an alias moved by someone else (a later publish) is left alone: no flapping
    other = FakeServing(current=CAND)
    THIRD = CAND.replace("b" * 64, "e" * 64)

    class Moved(FakeServing):
        async def serving(self, endpoint_id):
            return await other.serving(endpoint_id)

        async def rollback(self, endpoint_id, *, fence, to_serving_ref, reason):
            other.current, other.fence = THIRD, other.fence + 1
            raise errors.StateConflict("moved")

    asyncio.run(step(controller(FakeReleases(), Moved()), live(errors_=21)))
    assert other.current == THIRD
    later = controller(FakeReleases(), FakeServing(current=THIRD))
    later_store = later._store
    later_store.row = r2.Release(state="rolled_back", fence=2, plan_digest=later_store.row.plan_digest,
                                 started_at=START)
    asyncio.run(step(later, live()))
    assert later._serving.current == THIRD and later._serving.rollbacks == []

    class Stuck(FakeServing):
        async def rollback(self, endpoint_id, *, fence, to_serving_ref, reason):
            raise errors.StateConflict("always stale")

    with pytest.raises(errors.StateConflict):     # a CAS that never lands is not swallowed
        asyncio.run(step(controller(FakeReleases(), Stuck(current=CAND)), live(errors_=21)))


def test_r2_emergency_rollback_needs_no_evidence_and_happens_once():
    for state in ("running", "approved"):
        store, serving = FakeReleases(), FakeServing(current=CAND)
        store.row = r2.Release(state=state, fence=1, plan_digest=store.row.plan_digest,
                               started_at=START)
        ctl = controller(store, serving)
        asyncio.run(ctl.emergency_rollback(OPERATOR, POLICY, POLICY_REF, now=START,
                                           reason="pager"))
        [(decision, reasons)] = store.decisions
        assert decision["decided_by"] == OPERATOR and reasons == ("operator:pager",)
        assert store.row.state == "rolled_back" and serving.current == BASE
        asyncio.run(ctl.emergency_rollback(OPERATOR, POLICY, POLICY_REF, now=START,
                                           reason="again"))
        assert len(store.decisions) == 1 and len(serving.rollbacks) == 1


def test_r2_a_decision_lost_to_another_transition_is_not_swallowed():
    store = FakeReleases()
    ctl = controller(store, FakeServing())

    async def race():
        await ctl.approve(OPERATOR, POLICY, POLICY_REF, plan(), live(), now=HORIZON,
                          report=report(), runs=(BASE_RUN, CAND_RUN))

    stale = r2.Release(state="running", fence=1, plan_digest=store.row.plan_digest,
                       started_at=START)
    asyncio.run(race())                            # the row is now approved@2
    store.release = lambda ref: asyncio.sleep(0, stale)   # a controller holding a stale read
    with pytest.raises(errors.StateConflict):
        asyncio.run(step(ctl, live(errors_=21)))
    assert len(store.decisions) == 1


def test_r2_a_policy_other_than_the_stored_revision_is_refused():
    """The rollback target is the stored policy's baseline, never a caller's substitute."""
    wrong = CAND.replace("b" * 64, "c" * 64).replace("00000029", "00000030")
    forged = lab.parse({**POLICY.model_dump(mode="json", by_alias=True), "baseline_ref": wrong})
    store, serving = FakeReleases(), FakeServing(current=CAND)
    ctl = controller(store, serving)
    for call in (lambda: ctl.step(forged, POLICY_REF, plan(), live(errors_=21), now=HORIZON),
                 lambda: ctl.approve(OPERATOR, forged, POLICY_REF, plan(), live(), now=HORIZON,
                                     report=report(), runs=(BASE_RUN, CAND_RUN)),
                 lambda: ctl.emergency_rollback(OPERATOR, forged, POLICY_REF, now=START,
                                                reason="pager")):
        with pytest.raises(errors.InvalidRequest):
            asyncio.run(call())
    assert store.decisions == [] and serving.rollbacks == [] and serving.current == CAND
    asyncio.run(ctl.step(POLICY, POLICY_REF, plan(), live(errors_=21), now=HORIZON))
    assert serving.current == BASE and len(store.decisions) == 1


# E8L-F2: L3's promotion (`operations.propose`) lists the candidate's serving revision under a
# fresh deployment revision: the same provider and digest, another deployment_revision_id.
PROMOTED = CAND.replace("00000029-0000-4000-8000-000000000029",
                        "0000002a-0000-4000-8000-00000000002a")
BASE_ELSEWHERE = BASE.replace("00000028-0000-4000-8000-000000000028",
                              "0000002b-0000-4000-8000-00000000002b")


def test_r2_a_promoted_candidate_is_recognised_by_its_serving_identity():
    """The alias on the candidate's serving revision under another deployment revision is
    this policy's candidate: a breach and the operator's stop both move it back. The same
    digest under another provider, or another digest, is not, and is left alone."""
    store, serving = FakeReleases(), FakeServing(current=PROMOTED)
    asyncio.run(step(controller(store, serving), live(errors_=21)))
    assert serving.current == BASE and len(serving.rollbacks) == 1
    store, serving = FakeReleases(), FakeServing(current=PROMOTED)
    asyncio.run(controller(store, serving).emergency_rollback(OPERATOR, POLICY, POLICY_REF,
                                                              now=START, reason="pager"))
    assert serving.current == BASE and len(serving.rollbacks) == 1
    other_provider = PROMOTED.replace(P, "22222222-2222-4222-8222-222222222222")
    other_digest = PROMOTED.replace("b" * 64, "e" * 64)
    for foreign in (other_provider, other_digest):
        serving = FakeServing(current=foreign)
        asyncio.run(step(controller(FakeReleases(), serving), live(errors_=21)))
        assert serving.current == foreign and serving.rollbacks == [], foreign



def test_r2_a_deployment_only_candidate_rolls_back_once_and_never_relists_the_baseline():
    """A candidate that is the baseline's serving revision on another deployment revision
    shares the baseline's serving identity: it is rolled back, and the alias on the baseline
    itself is never re-listed on a later pass (no flapping)."""
    body = {**POLICY.model_dump(mode="json", by_alias=True),
            "candidates": [{"serving_ref": BASE_ELSEWHERE, "weight_bp": 1_000}]}
    policy, ref = lab.parse(body), lab.ref_of(body)
    store, serving = FakeReleases(), FakeServing(current=BASE_ELSEWHERE)
    store.release = lambda _ref: asyncio.sleep(0, store.row)
    ctl = controller(store, serving)
    asyncio.run(ctl.emergency_rollback(OPERATOR, policy, ref, now=START, reason="pager"))
    assert serving.current == BASE and len(serving.rollbacks) == 1
    verdict = asyncio.run(ctl.step(policy, ref, plan(), live(), now=HORIZON))
    assert verdict.action == "rolled_back" and len(serving.rollbacks) == 1


# 0-RI-1: identity membership is the decision's, not the pass loop's. A later deliberate
# listing of the same serving version (another fresh deployment revision) after the release
# was rolled back and converged is left alone by every later pass.
REPROMOTED = CAND.replace("00000029-0000-4000-8000-000000000029",
                          "0000003c-0000-4000-8000-00000000003c")
BASE_REDEPLOYED = BASE.replace("00000028-0000-4000-8000-000000000028",
                               "0000003d-0000-4000-8000-00000000003d")


def test_r2_a_later_listing_of_the_same_serving_version_is_left_alone_after_a_rollback():
    """The rollback decision (a breach, the operator's stop) recognises a promoted candidate by
    serving identity; a pass over the rolled-back release converges only an alias on a
    candidate ref the policy names. So a re-promotion under a fresh deployment revision, and a
    redeploy of a deployment-only candidate's (the baseline's) serving version, stay; an
    interrupted rollback of a promoted listing is finished by the operator's stop."""
    store, serving = FakeReleases(), FakeServing(current=PROMOTED)
    ctl = controller(store, serving)
    asyncio.run(ctl.emergency_rollback(OPERATOR, POLICY, POLICY_REF, now=START, reason="pager"))
    assert serving.current == BASE and len(serving.rollbacks) == 1
    serving.current, serving.fence = REPROMOTED, serving.fence + 1    # a deliberate re-promotion
    for _ in range(2):
        assert asyncio.run(step(ctl, live())).action == "rolled_back"
    assert serving.current == REPROMOTED and len(serving.rollbacks) == 1
    # deployment-only: the candidate is the baseline's serving version; a later redeploy stays
    body = {**POLICY.model_dump(mode="json", by_alias=True),
            "candidates": [{"serving_ref": BASE_ELSEWHERE, "weight_bp": 1_000}]}
    policy, ref = lab.parse(body), lab.ref_of(body)
    store, serving = FakeReleases(), FakeServing(current=BASE_ELSEWHERE)
    store.release = lambda _ref: asyncio.sleep(0, store.row)
    ctl = controller(store, serving)
    asyncio.run(ctl.emergency_rollback(OPERATOR, policy, ref, now=START, reason="pager"))
    serving.current, serving.fence = BASE_REDEPLOYED, serving.fence + 1
    asyncio.run(ctl.step(policy, ref, plan(), live(), now=HORIZON))
    assert serving.current == BASE_REDEPLOYED and len(serving.rollbacks) == 1
    # killed between D9 and L3 on a promoted listing: the pass leaves it, the operator's stop
    # (a refused second decision) converges it
    store, serving = FakeReleases(), FakeServing(current=PROMOTED, fail=1)
    with pytest.raises(ConnectionError):
        asyncio.run(step(controller(store, serving), live(errors_=21)))
    restarted = controller(store, serving)
    asyncio.run(step(restarted, live()))
    assert serving.current == PROMOTED and serving.rollbacks == []
    asyncio.run(restarted.emergency_rollback(OPERATOR, POLICY, POLICY_REF, now=HORIZON,
                                             reason="finish"))
    assert serving.current == BASE and len(serving.rollbacks) == 1 and len(store.decisions) == 1
