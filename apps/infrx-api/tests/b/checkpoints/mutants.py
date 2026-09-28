#!/usr/bin/env python3
"""R32/R40/R83 for B3: one single-edit defect per decision `test_checkpoints.py` claims,
through the shared runner with `require_every_case`.

    uv run --frozen pytest -q tests/b/checkpoints/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/b/checkpoints/test_mutants.py   # all
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

SUITE = "tests/b/checkpoints/test_checkpoints.py"
P = "evaluation/checkpoints/__init__.py"

ONCE = "test_b3_a_signed_event_is_received_once_and_a_replay_is_the_same_receipt"
FORGED = "test_b3_forged_foreign_stale_and_unbound_events_are_refused"
SUB = "test_b3_subscribe_needs_a_scheduling_member_a_credit_budget_and_its_evaluator"
VALID = "test_b3_a_valid_checkpoint_is_one_dev_evaluation_per_subscription_never_promoted"
BAD = "test_b3_a_malformed_or_changed_artifact_is_rejected_and_never_deploys"
ORDER = "test_b3_out_of_order_events_never_redefine_latest"
BURST = "test_b3_a_burst_is_bounded_by_concurrency_and_budget_with_visible_skips"
CRASH = "test_b3_a_crash_between_receipt_and_dispatch_never_queues_paid_work_twice"
REVOKED = "test_b3_a_revoked_owner_or_grant_is_a_visible_skip"


def m(name, invariant, old, new, *cases, dies_by=(), occurrences=1):
    return Mutant(name=name, invariant=invariant, file=P, old=old, new=new, cases=cases,
                  dies_by=dies_by, occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- B3.a receive
    m("b3_signature_unchecked", "a body must verify under the named key",
      "    if key is None or not hmac.compare_digest(sign(body, key[1]), signature):",
      "    if key is None:", FORGED),
    m("b3_provider_unbound", "the key's provider is the event's and its external run's",
      "    if {event.provider_org_id, lab.REF_RE.fullmatch(event.external_run_ref).group(2)} != \\\n"
      "            {key[0]}:", "    if False:", FORGED, dies_by=("NotFound",)),
    m("b3_window_open", "an event outside the window is refused",
      ".total_seconds()) > window_s:", ".total_seconds()) > window_s * 100:", FORGED),
    m("b3_window_one_sided", "a future-dated event is outside the window too",
      "    if abs((await ledger.db_now() - _ts(event.issued_at)).total_seconds()) > window_s:",
      "    if (await ledger.db_now() - _ts(event.issued_at)).total_seconds() > window_s:", FORGED),
    m("b3_event_before_run_check", "nothing is recorded for another provider's run",
      "    await store.resolve(event.external_run_ref, provider_org_id=event.provider_org_id)\n"
      "    await ledger.record_event(event)\n",
      "    await ledger.record_event(event)\n", FORGED),
    m("b3_receipt_digest_unsigned", "the receipt pins the signed digest",
      "external_run_ref=event.external_run_ref, artifact_digest=event.artifact.digest)\n\n\n"
      "async def subscribe",
      "external_run_ref=event.external_run_ref, artifact_digest=\"sha256:\" + \"0\" * 64)\n\n\n"
      "async def subscribe", ONCE),
    m("b3_event_unrecorded", "a received event is kept for its handler",
      "    await ledger.record_event(event)\n", "", FORGED),
    # --- subscriptions
    m("b3_subscription_any_unit", "a subscription is CREDIT",
      '    if {sub.run_limit.unit, sub.limit.unit} != {"CREDIT"} or \\',
      "    if False or \\", SUB),
    m("b3_run_limit_over_total", "a run's limit is within the total",
      "            sub.run_limit.amount > sub.limit.amount:",
      "            False:", SUB),
    m("b3_subscription_evaluator_unbound", "the spec is the one its ref names",
      "evaluator_id=evaluator_id) != sub.evaluator_ref:",
      "evaluator_id=evaluator_id) != sub.evaluator_ref and False:", SUB),
    m("b3_subscriber_unchecked", "a viewer cannot subscribe",
      "    await runner.may_schedule(access, user_id=user_id, provider_org_id=sub.provider_org_id)\n",
      "", SUB),
    m("b3_external_run_unresolved", "a subscription names the provider's own external run",
      "    await store.resolve(sub.external_run_ref, provider_org_id=sub.provider_org_id)\n", "",
      SUB),
    # --- B3.b registration
    m("b3_any_registry", "a scheme no adapter serves is refused",
      'registries.get(event.artifact.uri.split(":", 1)[0])', 'registries.get("mem")', BAD),
    m("b3_digest_unchecked", "bytes changed under the URL are refused",
      "hexdigest() != event.artifact.digest:", "hexdigest() != event.artifact.digest and False:",
      BAD),
    m("b3_any_bytes_are_a_checkpoint", "a malformed file is refused",
      'return None if _valid_safetensors(data) else "malformed"', "return None", BAD),
    m("b3_tensor_past_the_file", "every tensor lies inside the file",
      "0 <= a <= b <= len(data) - 8 - size for a, b in spans)", "0 <= a <= b for a, b in spans)",
      BAD),
    m("b3_rejected_deploys", "a rejected checkpoint never deploys",
      '    if receipt["state"] == "rejected":\n', "    if False:\n", BAD),
    m("b3_rejection_unrecorded", "a rejection is visible with its reason",
      "        await ledger.reject(event.checkpoint_id, reason, provider_org_id=event.provider_org_id)\n",
      "        pass\n", BAD),
    # --- B3.b decisions
    m("b3_latest_by_arrival", "a lower step is superseded whatever the order",
      '            if sub.policy == "latest_only" and superseded:',
      '            if sub.policy == "latest_only" and False:', ORDER),
    m("b3_rejected_supersedes", "a newer step supersedes only once it registered valid",
      '))["state"] != "rejected":\n                        superseded = True',
      '))["state"] or True:\n                        superseded = True', ORDER),
    m("b3_every_is_latest", "an every-policy subscription evaluates each step",
      '            if sub.policy == "latest_only" and superseded:',
      "            if superseded:", ORDER),
    m("b3_budget_unbounded", "a burst never spends past the total",
      "            if spend > sub.limit.amount:\n", "            if False:\n", BURST),
    m("b3_budget_off_by_one", "a run that exactly reaches the total is allowed",
      "            if spend > sub.limit.amount:\n", "            if spend >= sub.limit.amount:\n",
      BURST),
    m("b3_concurrency_unbounded", "max_active unfinished runs defer the next",
      "            if len(active) >= sub.max_active:\n", "            if False:\n", BURST),
    m("b3_concurrency_off_by_one", "max_active runs, not one more",
      "            if len(active) >= sub.max_active:\n",
      "            if len(active) > sub.max_active:\n", BURST),
    m("b3_finished_runs_count_as_active", "an ended run frees its slot",
      '["state"] in ("queued", "running")]', '["state"] is not None]', BURST),
    m("b3_busy_swallowed", "a deferred checkpoint is handed back to the relay",
      "    if busy:\n", "    if False:\n", BURST),
    m("b3_deployed_per_subscription", "one deployment per checkpoint",
      "        serving = serving or await deployer.deploy(",
      "        serving = await deployer.deploy(", VALID),
    m("b3_run_per_checkpoint_only", "one run per subscription and checkpoint",
      'f"{subscription_id}:{checkpoint_id}"', 'f"{checkpoint_id}"', VALID),
    m("b3_run_budget_is_the_total", "a run is bounded by the per-run limit",
      '"budgets": [{"limit": sub.run_limit.model_dump(),',
      '"budgets": [{"limit": sub.limit.model_dump(),', VALID),
    m("b3_run_suite_unpinned", "the run pins the subscription's max_cases",
      '"max_cases": sub.max_cases, "state": "queued",', '"max_cases": 100, "state": "queued",',
      VALID),
    m("b3_never_evaluated", "a queued checkpoint is marked evaluated",
      "        await store.transition_checkpoint(checkpoint_id, \"evaluated\",",
      "        None and await store.transition_checkpoint(checkpoint_id, \"evaluated\",",
      VALID, CRASH),
    m("b3_evaluated_without_a_run", "a checkpoint nothing was queued for is not evaluated",
      'and any(d["state"] == "queued" for d in out.values()):', ":", REVOKED),
    m("b3_owner_checked_after_deploy", "a revoked owner gets no dev deployment",
      "            await runner.may_schedule(access, user_id=sub.owner_user_id,\n"
      "                                      provider_org_id=provider_org_id)\n",
      "            pass\n", REVOKED),
    m("b3_refusal_code_lost", "a skip names the refusal",
      '"skipped", refused.code)', '"skipped", "forbidden")', REVOKED, occurrences=2),
    m("b3_refusal_raises", "a revoked owner or grant is a skip, not a retry",
      "        except (errors.Forbidden, errors.NotFound) as refused:",
      "        except errors.StateConflict as refused:", REVOKED,
      dies_by=("NotFound", "Forbidden"), occurrences=2),
    m("b3_skip_redecided", "a recorded skip is final",
      '        if done is not None and done["state"] != "queued":\n',
      "        if False:\n", BURST),
)


def case_names() -> set[str]:
    tree = ast.parse((API_DIR / SUITE).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}


RUNNER = Runner(name="b3", targets=(SUITE,), require_every_case=True)


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the B3 checkpoint mutation list"))
