#!/usr/bin/env python3
"""R32/R83 for R2: one single-edit defect per decision `test_control.py` claims, through the
shared runner with `require_every_case`.

    uv run --frozen pytest -q tests/r/control/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/r/control/test_mutants.py   # all
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Result, Runner  # noqa: E402

SUITE = "tests/r/control/test_control.py"
P = "rollouts/control/__init__.py"

PLAN = "test_r2_the_plan_has_no_defaults_and_cannot_change_after_launch"
INCL = "test_r2_every_threshold_is_inclusive_and_a_healthy_candidate_at_the_horizon_expands"
OPS = "test_r2_an_operational_breach_rolls_back_without_any_judge"
UNITS = "test_r2_budget_units_never_mix"
GAP = "test_r2_insufficient_or_delayed_evidence_cannot_approve"
NOREP = "test_r2_no_report_or_an_inconclusive_one_holds"
PEEK = "test_r2_peeking_before_the_horizon_is_not_a_win"
SLICE = "test_r2_a_failed_quality_slice_rolls_back_with_its_evidence"
BOUND = "test_r2_a_report_must_be_bound_to_this_release"
APPROVE = "test_r2_operator_approval_records_one_expand_decision"
ONCE = "test_r2_a_breach_rolls_back_exactly_once_under_concurrent_controllers"
RESTART = "test_r2_a_restart_converges_without_a_second_decision_or_flapping"
RACE = "test_r2_a_lost_serving_race_rereads_and_converges"
EMERG = "test_r2_emergency_rollback_needs_no_evidence_and_happens_once"
LOST = "test_r2_a_decision_lost_to_another_transition_is_not_swallowed"
FORGED = "test_r2_a_policy_other_than_the_stored_revision_is_refused"
PROMO = "test_r2_a_promoted_candidate_is_recognised_by_its_serving_identity"
DEPLOY = "test_r2_a_deployment_only_candidate_rolls_back_once_and_never_relists_the_baseline"
LATER = "test_r2_a_later_listing_of_the_same_serving_version_is_left_alone_after_a_rollback"


def m(name, invariant, old, new, *cases, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=P, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- R2.a the frozen plan
    m("r2_default_horizon", "the horizon is a workload input",
      "horizon_s: int = Field(ge=1)", "horizon_s: int = Field(ge=1, default=86_400)", PLAN),
    m("r2_zero_horizon", "a horizon is at least a second",
      "horizon_s: int = Field(ge=1)", "horizon_s: int = Field(ge=0)", PLAN),
    m("r2_default_min_requests", "minimum evidence is a workload input",
      "min_requests: int = Field(ge=1)", "min_requests: int = Field(ge=0)", PLAN),
    m("r2_error_rate_unbounded", "an error rate is a fraction",
      "max_error_rate: float = Field(ge=0, le=1)", "max_error_rate: float = Field(ge=0)", PLAN),
    m("r2_skew_negative", "a skew bound is not negative",
      "max_skew_bp: int = Field(ge=0, le=10_000)", "max_skew_bp: int", PLAN),
    m("r2_coverage_zero", "some quality telemetry is always required",
      "min_quality_coverage: float = Field(gt=0, le=1)",
      "min_quality_coverage: float = Field(ge=0, le=1)", PLAN),
    m("r2_default_budget", "the budget is declared",
      "    budget: lab.Amount\n", "    budget: lab.Amount | None = None\n", PLAN),
    m("r2_default_protocol", "the B2 protocol is declared",
      "    protocol: dict[str, Any]  ", "    protocol: dict[str, Any] = {}  ", PLAN),
    m("r2_plan_digest_unchecked", "a plan edited after launch is refused",
      "        if release.plan_digest != plan_digest(plan):\n", "        if False:\n", PLAN),
    m("r2_plan_digest_partial", "the plan digest covers every threshold",
      'return _digest(plan.model_dump(mode="json"))', "return _digest(plan.protocol)", PLAN),
    # --- R2.b operational triggers: no judge needed
    m("r2_health_ignored", "an unhealthy candidate rolls back",
      "    if not live.candidate_healthy:\n", "    if False:\n", OPS),
    m("r2_error_rate_ignored", "an error rate over the bound rolls back",
      "    if cand.errors > plan.max_error_rate * cand.requests:\n", "    if False:\n", OPS),
    m("r2_error_rate_strict", "an error rate AT the bound is fine",
      "if cand.errors > plan.max_error_rate", "if cand.errors >= plan.max_error_rate", INCL),
    m("r2_latency_ignored", "a p99 over the bound rolls back",
      "cand.p99_ms is not None and cand.p99_ms > plan.max_p99_ms", "False", OPS),
    m("r2_latency_strict", "a p99 AT the bound is fine",
      "cand.p99_ms > plan.max_p99_ms", "cand.p99_ms >= plan.max_p99_ms", INCL),
    m("r2_budget_ignored", "spend past the budget rolls back",
      "    if live.spent.amount > plan.budget.amount:\n", "    if False:\n", OPS),
    m("r2_budget_strict", "spend AT the budget is fine",
      "live.spent.amount > plan.budget.amount", "live.spent.amount >= plan.budget.amount", INCL),
    m("r2_units_unchecked", "spend in another unit is refused, not compared",
      "    if live.spent.unit != plan.budget.unit:\n", "    if False:\n", UNITS,
      dies_by=("TypeError",)),
    m("r2_ops_need_a_report", "an operational rollback needs no report",
      "    if stop:\n", "    if stop and report is not None:\n", OPS),
    # --- R2.b holds: insufficient or delayed evidence
    m("r2_stale_ignored", "stale metrics hold",
      "    if now - live.observed_until > timedelta(seconds=plan.max_lag_s):\n",
      "    if False:\n", GAP),
    m("r2_stale_strict", "a lag AT the bound is fresh",
      "live.observed_until > timedelta", "live.observed_until >= timedelta", INCL),
    m("r2_min_requests_ignored", "too few candidate requests hold",
      "    if cand.requests < plan.min_requests:\n", "    if False:\n", GAP),
    m("r2_min_requests_strict", "exactly the minimum is enough",
      "if cand.requests < plan.min_requests", "if cand.requests <= plan.min_requests", INCL),
    m("r2_coverage_ignored", "missing quality telemetry halts expansion",
      "    if live.quality_covered < plan.min_quality_coverage * cand.requests:\n",
      "    if False:\n", GAP),
    m("r2_coverage_strict", "coverage AT the minimum is enough",
      "if live.quality_covered < plan", "if live.quality_covered <= plan", INCL),
    m("r2_skew_ignored", "a cohort share off the policy weight holds",
      "    if abs(cand.requests * 10_000", "    if False and abs(cand.requests * 10_000", GAP),
    m("r2_skew_one_sided", "an under-share is a skew too",
      "abs(cand.requests * 10_000 - weight * total)", "(cand.requests * 10_000 - weight * total)",
      GAP),
    m("r2_skew_strict", "a skew AT the bound is fine",
      "> plan.max_skew_bp * total", ">= plan.max_skew_bp * total", INCL),
    m("r2_horizon_ignored", "nothing expands before the horizon",
      "    if now < started_at + timedelta(seconds=plan.horizon_s):\n", "    if False:\n", PEEK),
    m("r2_horizon_strict", "the horizon instant itself may expand",
      "if now < started_at + timedelta", "if now <= started_at + timedelta", INCL),
    m("r2_no_report_expands", "no report holds",
      '        hold.append("no_report")\n', "        pass\n", NOREP),
    m("r2_inconclusive_expands", "only an accepting report expands",
      '    elif outcome["outcome"] != "accept":\n', "    elif False:\n", NOREP),
    # --- EVAL-COMPARE: the report is bound to this release
    m("r2_report_digest_unchecked", "a tampered report is refused",
      '    if report.get("report_digest") != _digest(body):\n', "    if False:\n", BOUND),
    m("r2_report_protocol_unchecked", "a report under another protocol is refused",
      '    if report.get("protocol_digest") != _digest(plan.protocol):\n', "    if False:\n", BOUND),
    m("r2_report_runs_unchecked", "the report's runs are the runs given",
      '    if runs is None or (report.get("baseline_run"), report.get("candidate_run")) != \\\n'
      "            (lab.ref_of(runs[0]), lab.ref_of(runs[1])):\n",
      "    if runs is None:\n", BOUND),
    m("r2_runs_optional", "a report without its runs is unbound",
      "    if runs is None or (", "    if (", BOUND, dies_by=("TypeError",)),
    m("r2_baseline_unchecked", "the baseline run served the policy's baseline",
      '    if runs[0]["serving_ref"] != policy.baseline_ref:\n', "    if False:\n", BOUND),
    m("r2_candidate_unchecked", "the candidate run served one of the policy's candidates",
      '    if runs[1]["serving_ref"] not in {c.serving_ref for c in policy.candidates}:\n',
      "    if False:\n", BOUND),
    m("r2_unbound_reject_acts", "an unbound report decides nothing",
      'outcome = report["decision"] if evidence else None',
      'outcome = report["decision"] if report is not None else None', BOUND),
    m("r2_reject_ignored", "a rejecting report (a failed required slice) rolls back",
      '    if outcome and outcome["outcome"] == "reject":\n', "    if False:\n", SLICE),
    m("r2_reject_reasons_dropped", "the report's reasons are kept",
      'stop += ["quality_reject", *outcome["reasons"]]', 'stop += ["quality_reject"]', SLICE),
    m("r2_rollback_no_evidence", "a quality rollback names its runs",
      'return Verdict("rollback", tuple(stop), evidence)', 'return Verdict("rollback", tuple(stop))',
      SLICE),
    m("r2_expand_no_evidence", "an expansion names its runs",
      'else Verdict("expand", (), evidence)', 'else Verdict("expand", ())', INCL),
    # --- R2.c decisions through the D9 CAS
    m("r2_hold_decides", "a hold is not a decision",
      '        if verdict.action == "rollback":\n            await self._decide(',
      '        if verdict.action != "expand":\n            await self._decide(', GAP),
    m("r2_controller_expands", "the controller never expands by itself",
      '        if verdict.action == "rollback":\n            await self._decide(',
      '        if verdict.action in ("rollback", "expand"):\n            await self._decide(',
      APPROVE),
    # Without the guard a hold reaches D9 as an expansion with no evidence, which the F3
    # record itself refuses (`LabRejected`): a documented second line, not the first.
    m("r2_approve_without_expand", "approval needs an expand verdict",
      '        if verdict.action != "expand":\n', '        if verdict.action == "rollback":\n', GAP,
      dies_by=("LabRejected",)),
    m("r2_approve_settled", "only a running release is approved",
      '        if release.state != "running":\n            raise',
      '        if False:\n            raise', APPROVE),
    m("r2_settled_reevaluated", "a settled release is not evaluated again",
      '        if release.state != "running":\n            return Verdict(release.state, ())\n',
      "", APPROVE),
    m("r2_decision_actor", "the decision names who decided",
      '"decided_by": actor,', '"decided_by": self._actor,', APPROVE, EMERG),
    m("r2_decision_kind", "an approval records an expansion",
      '"expand" if to == "approved" else "rollback"', '"rollback"', APPROVE),
    m("r2_decided_at", "the decision is dated now",
      'now.strftime("%Y-%m-%dT%H:%M:%SZ")', '"2026-09-27T10:00:00Z"', APPROVE),
    m("r2_conflict_swallowed", "a decision lost to another transition is raised",
      "            if (await self._store.release(policy_ref)).state != to:\n",
      "            if False:\n", LOST),
    m("r2_conflict_always_raised", "a lost race to the same rollback is accepted",
      ").state != to:", ').state != "never":', ONCE),
    m("r2_emergency_reason", "the operator's reason is recorded",
      '(f"operator:{reason}",)', "()", EMERG),
    # --- R2.c converging the L3 alias
    m("r2_no_converge_after_rollback", "a rollback moves the alias back",
      '            await self._decide(policy, policy_ref, release, "rolled_back", verdict, '
      "self._actor, now)\n            await self._converge(policy, policy_ref)\n",
      '            await self._decide(policy, policy_ref, release, "rolled_back", verdict, '
      "self._actor, now)\n", ONCE),
    m("r2_no_converge_on_restart", "a restart finishes an interrupted rollback",
      '        if release.state == "rolled_back":\n            await self._converge(policy, policy_ref, decided=False)\n',
      '        if release.state == "rolled_back":\n', RESTART),
    m("r2_emergency_no_converge", "an emergency rollback moves the alias back",
      "                           Verdict(\"rollback\", (f\"operator:{reason}\",)), operator_id, now)\n"
      "        await self._converge(policy, policy_ref)\n",
      "                           Verdict(\"rollback\", (f\"operator:{reason}\",)), operator_id, now)\n",
      EMERG),
    m("r2_converge_any_alias", "an alias moved on by someone else is left alone",
      "            if current == policy.baseline_ref or key(current) not in candidates:\n",
      "            if current == policy.baseline_ref:\n", RACE),
    # --- E8L-F2: the alias is a candidate by serving identity, not by the full ref
    m("r2_converge_full_ref_membership", "a promoted candidate (fresh deployment revision) "
      "is still this policy's candidate",
      "        key = serving_identity if decided else str\n",
      "        key = str\n", PROMO),
    m("r2_identity_is_the_full_ref", "the identity drops the deployment revision",
      "    return f\"{head.rpartition(':')[0]}@{digest}\"", "    return ref", PROMO),
    m("r2_identity_drops_provider", "the identity keeps the provider",
      "head.rpartition(':')[0]}", "head.rpartition(':')[0].rpartition(':')[0]}", PROMO),
    m("r2_identity_drops_digest", "the identity keeps the serving revision's digest",
      "    return f\"{head.rpartition(':')[0]}@{digest}\"",
      "    return head.rpartition(':')[0]", PROMO, RACE),
    m("r2_converge_relists_baseline", "an alias exactly on the baseline is never re-listed",
      "            if current == policy.baseline_ref or key(current) not in candidates:\n",
      "            if key(current) not in candidates:\n", DEPLOY),
    # --- 0-RI-1: identity membership is the decision's; a later pass matches named refs only
    m("r2_restart_converges_by_identity", "a pass over a rolled-back release leaves a later "
      "listing of the same serving version alone",
      "await self._converge(policy, policy_ref, decided=False)",
      "await self._converge(policy, policy_ref)", LATER),
    m("r2_converge_target", "the alias goes back to the baseline",
      "to_serving_ref=policy.baseline_ref,", "to_serving_ref=current,", ONCE),
    m("r2_converge_gives_up", "a lost serving CAS is reread",
      "            except errors.StateConflict:\n                continue\n",
      "            except errors.StateConflict:\n                return\n", RACE),
    m("r2_converge_one_try", "a lost serving CAS is retried",
      "for _ in range(CONVERGE_TRIES):", "for _ in range(1):", RACE),
    # --- R2.c the policy is the revision policy_ref names (fix round 0-RC-C1)
    m("r2_policy_unbound", "a caller's policy never steers the alias",
      "        if lab.ref_of(policy.model_dump(mode=\"json\", by_alias=True)) != policy_ref:\n",
      "        if False:\n", FORGED),
    m("r2_emergency_policy_unbound", "the operator's stop uses the stored policy too",
      "release = await self._release(policy, policy_ref, None)",
      "release = await self._store.release(policy_ref)", FORGED),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


RUNNER = Runner(name="r2", targets=(SUITE,), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the R2 control mutation list"))
