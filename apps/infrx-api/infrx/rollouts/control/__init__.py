"""R2: guardrails, operator approval and CAS rollback of a controlled release.

**R2.a the plan is frozen.** `Plan` is the release's thresholds, observation horizon,
minimum evidence, budget and B2 comparison protocol, every one a workload input (no
defaults). D9 stores its digest with the policy revision at launch; a plan whose digest
differs from the stored one is refused, so thresholds cannot be loosened after launch.
The protocol is a **fixed horizon**: nothing expands before `horizon_s` has passed since
launch, however good an earlier look is, so peeking never becomes a win. ponytail: no
sequential (alpha-spending) looks; add them when a workload needs early stopping for wins.

**R2.b guardrails** (`evaluate`, pure). Operational triggers roll back and need no judge
or report: candidate unhealthy, error rate or p99 over the bound, budget spent past the
limit (units never mix). A B2 report that is bound to this release (its own digest, the
frozen protocol's digest, the two runs' refs, the baseline and a candidate serving) and
rejects - any failed required slice - also rolls back. Everything else that is missing
holds: stale metrics, too few candidate requests, too little quality telemetry, a cohort
share off the policy weight (sample-ratio mismatch), before the horizon, no report, an
unbound or non-accepting report. Only then is the verdict `expand`.

**R2.c decisions** (`Controller`). Every decision goes through D9's CAS on the release row
(`transition` with the fence read), recorded as an F3 `lab.rollout_decision.1`; the
serving alias only through L3's CAS. The controller never expands by itself: `approve`
records an operator's expansion, and only on an `expand` verdict. A rollback is one D9
transition (a lost race rereads and accepts a rollback that another controller made, so a
breach rolls back exactly once) followed by `_converge`: while the endpoint's alias is a
candidate of this policy, CAS it back to the baseline. A controller killed between the two
steps converges on restart; a rolled-back release stays rolled back whatever the metrics
do later, and an alias someone else moved on is left alone (no flapping). Queued and
running jobs keep the serving and rate pins they were admitted with (R1/L3); only future
admissions follow the policy and alias. `emergency_rollback` is the operator's, from any
live state, independent of evidence.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import Field

from ...contracts import errors
from ...contracts.lab import records as lab


class Plan(lab.LabModel):
    horizon_s: int = Field(ge=1)
    min_requests: int = Field(ge=1)
    max_error_rate: float = Field(ge=0, le=1)
    max_p99_ms: int = Field(ge=1)
    max_skew_bp: int = Field(ge=0, le=10_000)
    min_quality_coverage: float = Field(gt=0, le=1)
    max_lag_s: int = Field(ge=0)
    budget: lab.Amount
    protocol: dict[str, Any]          # B2's `Protocol`, bound by digest to every report


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(lab.canonical(value)).hexdigest()


def plan_digest(plan: Plan) -> str:
    return _digest(plan.model_dump(mode="json"))


@dataclass(frozen=True)
class Arm:
    requests: int
    errors: int
    p99_ms: int | None


@dataclass(frozen=True)
class Live:
    """R1's aggregates for one policy revision: per arm, quality coverage, spend, health."""

    observed_until: datetime
    baseline: Arm
    candidate: Arm
    quality_covered: int              # candidate requests with quality telemetry
    spent: lab.Amount
    candidate_healthy: bool


@dataclass(frozen=True)
class Verdict:
    action: str                       # rollback | hold | expand (| a settled release's state)
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()


def _unbound(report: dict[str, Any], plan: Plan, policy: lab.RolloutPolicy,
             runs: tuple[dict[str, Any], dict[str, Any]] | None) -> str | None:
    """Why this report cannot decide this release, or None."""
    body = {k: v for k, v in report.items() if k != "report_digest"}
    if report.get("report_digest") != _digest(body):
        return "report_tampered"
    if report.get("protocol_digest") != _digest(plan.protocol):
        return "report_protocol_not_frozen"
    if runs is None or (report.get("baseline_run"), report.get("candidate_run")) != \
            (lab.ref_of(runs[0]), lab.ref_of(runs[1])):
        return "report_runs_unbound"
    if runs[0]["serving_ref"] != policy.baseline_ref:
        return "report_not_this_baseline"
    if runs[1]["serving_ref"] not in {c.serving_ref for c in policy.candidates}:
        return "report_not_this_release"
    return None


def evaluate(plan: Plan, policy: lab.RolloutPolicy, live: Live, *, started_at: datetime,
             now: datetime, report: dict[str, Any] | None = None,
             runs: tuple[dict[str, Any], dict[str, Any]] | None = None) -> Verdict:
    cand, base = live.candidate, live.baseline
    if live.spent.unit != plan.budget.unit:
        raise errors.InvalidRequest(f"spend in {live.spent.unit} against a {plan.budget.unit} "
                                    "budget: units never mix")
    stop = []
    if not live.candidate_healthy:
        stop.append("candidate_unhealthy")
    if cand.errors > plan.max_error_rate * cand.requests:
        stop.append("error_rate")
    if cand.p99_ms is not None and cand.p99_ms > plan.max_p99_ms:
        stop.append("latency")
    if live.spent.amount > plan.budget.amount:
        stop.append("budget")
    unbound = _unbound(report, plan, policy, runs) if report is not None else None
    evidence = () if report is None or unbound else (report["baseline_run"], report["candidate_run"])
    outcome = report["decision"] if evidence else None
    if outcome and outcome["outcome"] == "reject":
        stop += ["quality_reject", *outcome["reasons"]]
    if stop:
        return Verdict("rollback", tuple(stop), evidence)
    hold = []
    if now - live.observed_until > timedelta(seconds=plan.max_lag_s):
        hold.append("metrics_stale")
    if cand.requests < plan.min_requests:
        hold.append("min_requests")
    if live.quality_covered < plan.min_quality_coverage * cand.requests:
        hold.append("quality_coverage")
    total, weight = cand.requests + base.requests, sum(c.weight_bp for c in policy.candidates)
    if abs(cand.requests * 10_000 - weight * total) > plan.max_skew_bp * total:
        hold.append("cohort_skew")
    if now < started_at + timedelta(seconds=plan.horizon_s):
        hold.append("before_horizon")
    if report is None:
        hold.append("no_report")
    elif unbound:
        hold.append(unbound)
    elif outcome["outcome"] != "accept":
        hold.append(f"report_{outcome['outcome']}")
    return Verdict("hold", tuple(hold)) if hold else Verdict("expand", (), evidence)


# --- the ports: D9 (release rows, CAS) and L3 (the serving alias, CAS) ---------------------
@dataclass(frozen=True)
class Release:
    """D9's row for one policy revision."""

    state: str                        # running | approved | rolled_back (D9 owns the set)
    fence: int
    plan_digest: str
    started_at: datetime


class ReleaseStore(Protocol):
    async def release(self, policy_ref: str) -> Release: ...

    async def transition(self, policy_ref: str, *, fence: int, to: str, decision: dict[str, Any],
                         reasons: tuple[str, ...]) -> int:
        """CAS: `StateConflict` unless the row is still at `fence` and `to` is declared."""


class ServingControl(Protocol):
    async def serving(self, endpoint_id: str) -> tuple[str, int]: ...

    async def rollback(self, endpoint_id: str, *, fence: int, to_serving_ref: str,
                       reason: str) -> int:
        """CAS on the endpoint's alias: `StateConflict` unless it is still at `fence`."""


CONVERGE_TRIES = 3


class Controller:
    def __init__(self, store: ReleaseStore, serving: ServingControl, *, actor_id: str) -> None:
        self._store, self._serving, self._actor = store, serving, actor_id

    async def _release(self, policy_ref: str, plan: Plan) -> Release:
        release = await self._store.release(policy_ref)
        if release.plan_digest != plan_digest(plan):
            raise errors.StateConflict("the plan changed after launch")
        return release

    async def _decide(self, policy: lab.RolloutPolicy, policy_ref: str, release: Release, to: str,
                      verdict: Verdict, actor: str, now: datetime) -> None:
        decision = {"schema": "lab.rollout_decision.1", "provider_org_id": policy.provider_org_id,
                    "policy_ref": policy_ref,
                    "decision": "expand" if to == "approved" else "rollback",
                    "evidence_refs": list(verdict.evidence_refs), "decided_by": actor,
                    "decided_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        try:
            await self._store.transition(policy_ref, fence=release.fence, to=to,
                                         decision=decision, reasons=verdict.reasons)
        except errors.StateConflict:
            if (await self._store.release(policy_ref)).state != to:
                raise                  # lost to a different decision: never overwrite it

    async def _converge(self, policy: lab.RolloutPolicy, policy_ref: str) -> None:
        candidates = {c.serving_ref for c in policy.candidates}
        for _ in range(CONVERGE_TRIES):
            current, fence = await self._serving.serving(policy.endpoint_id)
            if current not in candidates:
                return
            try:
                await self._serving.rollback(policy.endpoint_id, fence=fence,
                                             to_serving_ref=policy.baseline_ref,
                                             reason=f"rollback {policy_ref}")
                return
            except errors.StateConflict:
                continue
        raise errors.StateConflict(f"the serving alias did not converge in {CONVERGE_TRIES} tries")

    async def step(self, policy: lab.RolloutPolicy, policy_ref: str, plan: Plan, live: Live, *,
                   now: datetime, report: dict[str, Any] | None = None,
                   runs: tuple[dict[str, Any], dict[str, Any]] | None = None) -> Verdict:
        """One controller pass: act on a rollback verdict, converge a rolled-back release."""
        release = await self._release(policy_ref, plan)
        if release.state == "rolled_back":
            await self._converge(policy, policy_ref)
            return Verdict("rolled_back", ())
        if release.state != "running":
            return Verdict(release.state, ())
        verdict = evaluate(plan, policy, live, started_at=release.started_at, now=now,
                           report=report, runs=runs)
        if verdict.action == "rollback":
            await self._decide(policy, policy_ref, release, "rolled_back", verdict, self._actor, now)
            await self._converge(policy, policy_ref)
        return verdict

    async def approve(self, operator_id: str, policy: lab.RolloutPolicy, policy_ref: str,
                      plan: Plan, live: Live, *, now: datetime, report: dict[str, Any],
                      runs: tuple[dict[str, Any], dict[str, Any]]) -> Verdict:
        """An operator's expansion: only a running release on an `expand` verdict."""
        release = await self._release(policy_ref, plan)
        if release.state != "running":
            raise errors.StateConflict(f"a {release.state} release cannot be approved")
        verdict = evaluate(plan, policy, live, started_at=release.started_at, now=now,
                           report=report, runs=runs)
        if verdict.action != "expand":
            raise errors.StateConflict(f"not approvable: {verdict.action} {verdict.reasons}")
        await self._decide(policy, policy_ref, release, "approved", verdict, operator_id, now)
        return verdict

    async def emergency_rollback(self, operator_id: str, policy: lab.RolloutPolicy,
                                 policy_ref: str, *, now: datetime, reason: str) -> None:
        """The operator's stop, from any live state: no plan, report or metrics needed. A
        release already rolled back is refused by the CAS and only converges."""
        release = await self._store.release(policy_ref)
        await self._decide(policy, policy_ref, release, "rolled_back",
                           Verdict("rollback", (f"operator:{reason}",)), operator_id, now)
        await self._converge(policy, policy_ref)
