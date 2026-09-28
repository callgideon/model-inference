#!/usr/bin/env python3
"""R32 for P2: one single-edit defect per decision `tests/p/teachers` claims, through the shared
runner (`tests/contracts/mutants.py`).

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/p/teachers/test_mutants.py
"""
from __future__ import annotations

import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(API_DIR) not in sys.path:        # `python tests/p/teachers/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401

SUITE = "tests/p/teachers"
T = "pipelines/teachers/__init__.py"

DRY = "test_p2__a_dry_run_plans_and_prices_without_a_rate_or_egress"
GATE = "test_p2__only_a_live_member_with_its_own_payer_and_a_rate_runs_a_batch"
ONCE = "test_p2__each_chunk_leaves_once_through_the_j2_path"
BUDGET = "test_p2__the_budget_stops_the_batch_before_the_chunk_it_cannot_cover"
AMBIG = "test_p2__an_ambiguous_submit_is_held_never_resubmitted_and_reconciled_from_evidence"
REVOKED = "test_p2__a_sample_revoked_mid_batch_never_leaves_and_its_label_is_not_imported"
RACE = "test_p2__a_revocation_between_the_check_and_egress_releases_the_chunk_and_stops"
MISSING = "test_p2__a_sample_without_content_is_skipped_before_egress"
COLLECT = "test_p2__collected_labels_import_once_as_model_labels_never_ground_truth"
PARTIAL = "test_p2__an_unfinished_batch_imports_what_arrived_and_settles_later"
LABEL = "test_p2__a_label_is_one_short_text_with_an_optional_confidence"
HTTP = "test_p2_http__a_batch_round_trips_through_the_local_teacher_fake"

MUTANTS: tuple[Mutant, ...] = (
    # --- P2.a: the manifest ------------------------------------------------------------------------
    _m("holdout_labelled", "the holdout is never teacher-labelled",
       T, "        if sample_id in holdout:", "        if False:", DRY),
    _m("egress_purpose_only", "a label for training needs the training purpose too",
       T, 'PURPOSES = ("external_judging", "training")', 'PURPOSES = ("external_judging",)',
       REVOKED),
    _m("training_purpose_only", "leaving for an external model needs external_judging",
       T, 'PURPOSES = ("external_judging", "training")', 'PURPOSES = ("training",)', DRY, ONCE),
    _m("either_purpose_suffices", "both purposes, not either",
       T, "allowed = ids if allowed is None else allowed & ids",
       "allowed = ids if allowed is None else allowed | ids", DRY, ONCE),
    _m("chunk_size_unbounded", "a chunk is 1..MAX_CANDIDATES samples",
       T, "    if not 1 <= batch.chunk_size <= MAX_CANDIDATES:", "    if False:", GATE),
    _m("run_id_ignores_the_samples", "each chunk is its own run, named by its samples",
       T, '",".join(ids)', "str(len(ids))", ONCE, HTTP),
    _m("unpriced_plan_prices", "without a rate the plan is unpriced, not a crash",
       T, "    if rate is not None:\n        total", "    if True:\n        total", DRY,
       dies_by=("AttributeError",)),
    _m("worst_case_of_one_sample", "a chunk's hold is the worst case over its samples",
       T, "worst_case(rate, batch.ceilings, len(ids))", "worst_case(rate, batch.ceilings, 1)",
       DRY, ONCE, occurrences=2),
    # --- P2.b: J2's gates and protocol ---------------------------------------------------------------
    _m("live_mode_not_required", "only exactly `live` submits (R57)",
       T, "    if wiring.settings.judge_mode != JUDGE_MODE_LIVE:", "    if False:", GATE),
    _m("any_payer_pays", "the provider's own named payer pays",
       T, "    require_own_payer(batch.provider_org_id, batch.payer_ref)\n", "", GATE),
    _m("membership_not_checked", "a current member of the provider runs a batch",
       T, "    await _member(batch, wiring.members)\n", "", GATE),
    _m("viewer_runs_a_batch", "a viewer does not run paid work",
       T, "    if not membership.permits(ProviderCapability.run_evaluation",
       "    if False and not membership.permits(ProviderCapability.run_evaluation", GATE),
    _m("unpriced_model_reserves", "no approved rate, no reservation (typed refusal)",
       T, "    if rate is None:\n        raise", "    if False:\n        raise", GATE,
       dies_by=("AttributeError",)),
    _m("budget_exhaustion_skips_a_chunk", "the budget stops the batch; nothing after is sent",
       T, '            return BatchReport(tuple(runs), "budget", (run_id, *unsent))',
       "            continue", BUDGET),
    _m("any_caller_egresses", "only the call that created the intent sends",
       T, "        if not mine:\n            runs.append(run)",
       "        if False:\n            runs.append(run)", ONCE),
    _m("revoked_sample_sent", "a sample not permitted now is skipped before egress",
       T, "            if sample.sample_id not in allowed:\n                continue",
       "            if False:\n                continue", ONCE, REVOKED),
    _m("content_sent_unredacted", "P2.a: content is redacted before egress",
       T, 'wiring.redact(json.loads(body)["content"])', 'json.loads(body)["content"]', ONCE),
    _m("missing_content_sent", "a sample with no content object is skipped before egress",
       T, "            if body is not None:", "            if True:", MISSING,
       dies_by=("TypeError",)),
    _m("no_recheck_before_egress", "the permission is read again immediately before egress",
       T, 'lambda: recheck(item["sample_id"] for item in items)',
       "lambda: _allowed(batch, wiring.store)", RACE),
    _m("withdrawn_permission_skips_a_chunk", "a withdrawn permission stops the batch",
       T, '            return BatchReport(tuple(runs), "permission", unsent)',
       "            continue", RACE),
    # --- P2.c: collection ---------------------------------------------------------------------------
    _m("completed_run_collected_again", "collecting a completed run is a no-op",
       T, '    if run is not None and run.state == "completed":\n        return Collected(run)',
       "    if False:\n        return Collected(run)", COLLECT, dies_by=("StateConflict",)),
    _m("unsent_sample_imported", "a result for a sample the run never sent is not imported",
       T, "        if sample_id not in run.sent_ids:", "        if False:", COLLECT),
    _m("duplicate_imported", "the first result for a sample is the one imported",
       T, "        if sample_id in seen:", "        if False:", COLLECT),
    _m("revoked_label_imported", "a label for a sample no longer permitted is not imported",
       T, "        if sample_id not in allowed:\n            failures.append",
       "        if False:\n            failures.append", REVOKED),
    _m("malformed_label_imported", "a malformed label is a per-item failure",
       T, "        if parsed is None:", "        if False:", COLLECT, dies_by=("TypeError",)),
    _m("teacher_label_is_human", "a teacher label is a `model` row (P1: synthetic)",
       T, 'TEACHER_METHOD = "model"', 'TEACHER_METHOD = "human"', COLLECT),
    _m("null_confidence_recorded", "an absent confidence is absent, not null",
       T, "        if parsed[1] is not None:", "        if True:", PARTIAL),
    _m("empty_import_called", "nothing arrived, nothing imported",
       T, "    if rows:\n        imported", "    if True:\n        imported", PARTIAL),
    _m("settled_before_done", "a run settles only when the provider is done",
       T, "    if polled.done:\n        run = await ledger.settle",
       "    if True:\n        run = await ledger.settle", PARTIAL, dies_by=("TypeError",)),
    # --- the label parser ---------------------------------------------------------------------------
    _m("any_keys_accepted", "a label has only label and confidence",
       T, ' or not set(payload) <= LABEL_KEYS:', ":", LABEL),
    _m("label_key_optional", "the label key is required",
       T, ' or "label" not in payload or', " or", LABEL, dies_by=("KeyError",)),
    _m("any_json_value_is_a_label_object", "a label document is an object",
       T, 'if type(payload) is not dict or "label"', 'if "label"', LABEL,
       dies_by=("TypeError",)),
    _m("blank_label_accepted", "a label is non-blank",
       T, " or not label.strip() or", " or", LABEL),
    _m("long_label_accepted", "a label is at most 4000 characters (R43's text bound)",
       T, "len(label) > MAX_FEEDBACK_TEXT_CHARS", "len(label) > 10 * MAX_FEEDBACK_TEXT_CHARS",
       LABEL),
    _m("confidence_out_of_range", "a confidence is in 0..1",
       T, "                                   or not 0 <= confidence <= 1):",
       "                                   or False):", LABEL),
    _m("boolean_confidence", "True is not a confidence",
       T, "type(confidence) not in (int, float)", "not isinstance(confidence, (int, float))",
       LABEL),
    _m("recursion_escapes", "a deeply nested document is malformed, never an exception",
       T, "    except (ValueError, TypeError, RecursionError):",
       "    except (ValueError, TypeError):", LABEL, dies_by=("RecursionError",)),
    _m("numeric_label_accepted", "a label is text",
       T, "if type(label) is not str or", "if not isinstance(label, (str, int)) or", LABEL,
       dies_by=("AttributeError",)),
    _m("failures_unrecorded", "each per-item failure is kept in D8's log (WR-P2-D8)",
       T, "        await ledger.record_failures(run_id, failures)\n", "        pass\n", COLLECT),
    _m("ambiguous_submit_released", "an unknown outcome keeps its hold (ambiguous)",
       "judge/submit.py", "        return await ledger.quarantine(run.run_id,",
       '        return await ledger.release(run.run_id, "failed",', AMBIG),
)

#: the real-store half (WR-P2-D8) is outside the runner (T2I/G8's pattern): the fake cases'
#: mutants are its oracles.
OUTSIDE = ("test_mutants.py", "test_teachers_pg.py")
RUNNER = Runner(name="p2", targets=(SUITE,),
                extra_args=tuple(f"--ignore={SUITE}/{name}" for name in OUTSIDE))


def case_names() -> set[str]:
    import re
    pattern = re.compile(r"^def (test_\w+)", re.MULTILINE)
    return {name for path in (API_DIR / SUITE).glob("test_*.py") if path.name not in OUTSIDE
            for name in pattern.findall(path.read_text())}


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run P2's mutation list"))
