#!/usr/bin/env python3
"""R32/R40 for R1: one single-edit defect per routing decision, each killed by a named case.

    cd apps/infrx-api && uv run --frozen pytest -q tests/r/routing/test_mutants.py     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/r/routing/test_mutants.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
SUITE = "tests/r/routing/test_routing.py"

if str(API_DIR) not in sys.path:        # `python tests/r/routing/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner  # noqa: E402,F401

R = "rollouts/routing/__init__.py"
PIN = "test_an_explicit_pin_is_served_as_asked_and_never_routed"
OFF = "test_no_active_policy_or_mode_off_is_todays_request"
REPEAT = "test_a_repeat_request_keeps_its_cohort_and_each_admission_records_it"
WEIGHT = "test_raising_a_weight_mid_session_keeps_every_candidate_subject"
BOUND = "test_candidate_traffic_is_bounded_by_the_policy_weight"
REVOKED = "test_a_revoked_subject_returns_to_the_baseline_at_once"
SESSION = "test_a_session_cohort_without_a_declared_session_serves_the_baseline"
OUTAGE = "test_a_release_store_failure_is_a_retryable_outage_never_a_silent_move"
PINNED = "test_a_release_names_a_pinned_revision_for_every_candidate"
SHADOW = "test_shadow_answers_the_baseline_and_runs_the_candidate_on_the_side"
CANARY = "test_a_canary_is_never_shadowed"
REPLAY = "test_a_replayed_or_refused_admission_is_not_shadowed_again"
LIMIT = "test_shadow_work_is_bounded_by_its_own_limit"
FAILING = "test_a_failing_shadow_never_reaches_the_caller"
NO_CHARGE = "test_shadow_through_the_relay_admits_holds_and_settles_one_job_only"
PRICED = "test_a_canary_subject_is_admitted_and_priced_on_the_candidate_revision"

_ASSIGN = "assign(policy, release.policy_ref, auth.org_id, request.request_id)"


def _m(name, invariant, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=R, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


MUTANTS: tuple[Mutant, ...] = (
    _m("explicit_pin_routed", "an explicit R62 pin is served as asked",
       'if "@" in request.model_revision:', "if False:", PIN),
    _m("mode_off_routed", "a policy in mode off routes nothing",
       'if release is None or release.policy.mode == "off":', "if release is None:", OFF),
    _m("cohort_by_request", "the subject is the account, not the request",
       _ASSIGN, "assign(policy, release.policy_ref, request.request_id, request.request_id)",
       REPEAT, WEIGHT),
    _m("cohort_salted_by_version", "a weight change (a new version) keeps the buckets",
       _ASSIGN, "assign(policy, release.policy_ref, f'{policy.version}:{auth.org_id}', "
                "request.request_id)", WEIGHT),
    _m("assignment_not_recorded", "every eligible admission records its assignment",
       "            await self.releases.record(assignment)\n", "            pass\n", REPEAT),
    _m("ineligible_routed", "eligibility is read now; a revoked subject is not routed",
       "if policy.cohort != \"account\" or not await self.releases.eligible(policy.policy_id,\n"
       "                                                                              auth):",
       "if policy.cohort != \"account\":", REVOKED),
    _m("ineligible_uncounted", "coverage counts the ineligible subjects",
       '                self.counts[(policy.policy_id, "ineligible")] += 1\n', "", REVOKED),
    _m("session_cohort_routed", "a session cohort without a declared session is not routed",
       'if policy.cohort != "account" or not', "if not", SESSION),
    _m("store_failure_silent", "a store outage is a retryable 503, never a silent baseline",
       '            raise errors.DependencyUnavailable("the release store did not answer") '
       "from None", "            return request, ()", OUTAGE),
    _m("candidate_not_rewritten", "a candidate subject is admitted on the candidate pin",
       '            return request.model_copy(update={"model_revision": pin}), ()',
       "            return request, ()", REPEAT, PRICED),
    _m("weight_cap_ignored", "candidate traffic is bounded by the policy weight",
       _ASSIGN, "assign(policy.model_copy(update={'candidates': [c.model_copy(update="
                "{'weight_bp': 10_000}) for c in policy.candidates]}), release.policy_ref, "
                "auth.org_id, request.request_id)", BOUND),
    _m("canary_shadowed", "only a shadow policy duplicates",
       'if policy.mode == "shadow":', "if True:", CANARY),
    _m("release_accepts_alias", "a candidate is admitted as a pin, never a moving alias",
       'if "@" not in pin:', "if not pin:", PINNED),
    _m("shadow_unbounded", "shadow duplicates have their own in-flight bound",
       "if self.inflight[policy_id] >= release.shadow_limit:", "if False:", LIMIT),
    _m("shadow_slot_leaked", "a finished duplicate frees its slot",
       "            self.inflight[policy_id] -= 1\n", "            pass\n", LIMIT, FAILING),
    _m("shadow_failure_escapes", "a failing duplicate is counted and swallowed",
       "        except Exception:\n            log.exception(\"shadow duplicate",
       "        except ZeroDivisionError:\n            log.exception(\"shadow duplicate",
       FAILING, dies_by=("RuntimeError",)),
    _m("shadow_on_the_baseline", "the duplicate runs the candidate revision",
       'update={"model_revision": release.revisions[serving_ref]})', "update={})", SHADOW),
    _m("replay_shadowed", "a replayed admission is not duplicated again",
       'answer.headers.get(wire.HEADER_IDEMPOTENCY_REPLAYED) != "true"', "True", REPLAY),
    _m("shadow_before_admission", "a refused admission is not duplicated",
       "        answer = await accept(auth, admitted, idem)\n        if pending and",
       "        router.shadow(pending, request)\n"
       "        answer = await accept(auth, admitted, idem)\n        if False and", REPLAY),
    _m("shadow_admitted_and_charged", "a duplicate never goes through admission",
       "            router.shadow(pending, request)",
       "            for _release, _ref in pending:\n"
       "                await accept(auth, request.model_copy(update={\"model_revision\": "
       "_release.revisions[_ref]}), idem)", NO_CHARGE, SHADOW, dies_by=("ValueError",)),
)

RUNNER = Runner(name="r1", targets=(SUITE,))


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    for mutant in MUTANTS:
        print(mutant.name, run_mutant(mutant))
