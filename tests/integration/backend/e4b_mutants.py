#!/usr/bin/env python3
"""R32 / R83 for E4B: every decision the certification runner makes must be killable.

The shared runner (`apps/infrx-api/tests/contracts/mutants.py`, R83: one runner, one kill
rule, a pristine baseline per list) with a layout of E4B's own: the mutated files live
under `tests/integration/backend/`, so the throwaway copy carries the repository's shape -
`tests/integration`, `models/marlin2b`, the `infrx` package and the few trees the runner
hashes or reads - and pytest runs at the copy's root.

    apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_e4b_mutants.py
    INFRX_MUTANTS=all apps/infrx-api/.venv/bin/python -m pytest -q \\
        tests/integration/backend/test_e4b_mutants.py              # the whole list (make api-mutants)
    apps/infrx-api/.venv/bin/python tests/integration/backend/e4b_mutants.py --list
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
SHARED_PATH = REPO / "apps" / "infrx-api" / "tests" / "contracts" / "mutants.py"


def _shared():
    """The shared runner, by path: this list lives outside `apps/infrx-api`."""
    spec = importlib.util.spec_from_file_location("e4b_shared_mutants", SHARED_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = _shared()
Mutant, Outcome, Result = shared.Mutant, shared.Outcome, shared.Result

# What the E4B cases read, relative to the repository root. Directories are copied whole.
COPIED = ("tests/integration", "models/marlin2b", "apps/infrx-api/infrx",
          "apps/infrx-api/deploy", "apps/infrx-api/uv.lock", "apps/app/supabase/migrations",
          "infra", "research/plan/tasks.json")
SUITES = ("tests/integration/backend/test_certify.py",)


def _layout(root: pathlib.Path) -> pathlib.Path:
    junk = shutil.ignore_patterns("__pycache__", "*.pyc", "raw")
    for name in COPIED:
        source, target = REPO / name, root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=junk)
        else:
            shutil.copy2(source, target)
    return root


def _definitions() -> dict[str, str]:
    where = {}
    for suite in SUITES:
        for name in re.findall(r"^def (test_\w+)", (REPO / suite).read_text(), re.M):
            where[name] = suite
    return where


def case_names() -> set[str]:
    return set(_definitions())


# `package=""`: a mutant's `file` is relative to the repository root (the copy's root).
RUNNER = shared.Runner(name="e4b", package="", layout=_layout,
                       targets_for=lambda cases: sorted({_definitions()[c] for c in cases
                                                         if c in _definitions()}))

C = "tests/integration/backend/certify.py"
REPORT = "test_e4b_the_report_carries_both_heads_the_target_the_hashes_and_the_exit_rule"
UNTYPED = "test_e4b_a_skip_or_pending_without_a_known_owner_is_a_failure"
HASHES = "test_e4b_the_release_hashes_recompute_the_pinned_engine_options"
DERIVED = "test_e4b_the_recomputed_digest_comes_from_the_flags_not_from_the_record"
SPLIT = "test_e4b_the_backend_suite_splits_into_protocol_and_recovery_by_the_gates_rule"
PARITY = "test_e4b_sop_parity_pairs_the_engine_with_its_baseline_and_fails_on_drift"
RESUME = "test_e4b_the_resume_drill_counts_a_run_that_was_not_interrupted_as_proving_nothing"
LEDGER = "test_e4b_the_ledger_reconciles_item_by_item_with_no_duplicate_accepted_item"
DATASET = "test_e4b_the_dataset_drill_pends_on_the_owner_it_needs_and_passes_only_reconciled"
PROTOCOL = "test_e4b_the_protocol_file_states_the_numbers_the_runner_applies"


def _m(name, invariant, old, new, *cases, file=C, occurrences=1) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  occurrences=occurrences)


MUTANTS: tuple[Mutant, ...] = (
    # --- the report --------------------------------------------------------------------
    _m("untyped_skip_accepted", "a SKIP names an owner or is a FAIL",
       "if status in (PENDING, SKIP) and (", "if status in (PENDING,) and (", UNTYPED),
    _m("unknown_owner_accepted", "an owner outside the closed vocabulary is a FAIL",
       "(not owners or set(owners) - set(OWNERS))", "(not owners)", UNTYPED),
    _m("skip_exits_zero", "a typed SKIP is never exit 0",
       "return 3 if statuses - {PASS} else 0", "return 3 if statuses - {PASS, SKIP} else 0",
       REPORT),
    _m("owners_not_recorded", "each entry records the owners it pends on",
       "owners=list(owners) or None,", "owners=None,", REPORT),
    # --- hashes ------------------------------------------------------------------------
    _m("digest_copied_not_recomputed", "the engine-options digest is recomputed from flags",
       '"engine_options_digest_recomputed": options_digest(served_flags(record)),',
       '"engine_options_digest_recomputed": record["engine_options_digest"],', DERIVED),
    _m("flags_not_substituted", "the served flags carry the settings, not `${NAME}`",
       'flag = flag.replace("${" + name + "}", str(value))', "flag = flag", HASHES),
    # --- the stack suite ---------------------------------------------------------------
    _m("recovery_counted_as_protocol", "I3B's drills are E4B.b's, not the protocol suite's",
       'halves["recovery" if run._is_recovery(name) else "protocol"][bucket].append(name)',
       'halves["protocol"][bucket].append(name)', SPLIT),
    _m("stack_down_ignored", "a gate stage that did not pass fails the suite check",
       "    if down:\n", "    if False:\n", SPLIT),
    _m("pending_suite_is_pass", "a suite with pending cases is PENDING, never PASS",
       "status = run.backend_verdict(cases, 0)", "status = PASS", SPLIT),
    # --- MARLIN-SOP parity -------------------------------------------------------------
    _m("parity_unknown_is_pass", "an unpaired parity row is PENDING, never PASS",
       "{decide.PASS: PASS, decide.FAIL: FAIL}.get(verdict, PENDING)",
       "{decide.PASS: PASS, decide.FAIL: FAIL}.get(verdict, PASS)", PARITY),
    _m("parity_without_baseline_runs", "a remote engine is only paired with a named baseline",
       "if baseline is None and not local:", "if False:", PARITY),
    # --- the resume drill, client half -------------------------------------------------
    _m("interruption_unchecked", "a run that was not interrupted proves nothing",
       "if not first_interrupted or not 0 < len(accepted) < items:", "if False:", RESUME,
       DATASET),
    _m("key_split_unchecked", "one idempotency key per item across both runs",
       "    if split:\n", "    if False:\n", RESUME),
    _m("resent_unchecked", "the resume never re-sends a terminal item",
       "    if resent:\n", "    if False:\n", RESUME),
    _m("item_count_unchecked", "every scheduled item appears across both runs",
       "    if len(last) != items:\n", "    if False:\n", RESUME),
    _m("open_items_unchecked", "every item is terminal after the resume",
       "    if open_items:\n", "    if False:\n", RESUME),
    # --- the resume drill, ledger half -------------------------------------------------
    _m("duplicate_job_unchecked", "an item accepted as two jobs is a duplicate",
       "    if duplicated:\n", "    if False:\n", LEDGER),
    _m("missing_usage_unchecked", "a job with no usage record is a missing debit",
       "if count != 1)", "if count > 1)", LEDGER),
    _m("usd_usage_accepted", "the CREDIT regime records no USD usage",
       "    if usd:\n", "    if False:\n", LEDGER),
    _m("held_hold_accepted", "no hold of the run is still held at the end",
       "    if held:\n", "    if False:\n", LEDGER),
    _m("hold_count_unchecked", "one hold per job",
       "if any(count != 1 for count in hold_count.values()):", "if False:", LEDGER),
    _m("sum_unchecked", "Σ charged equals the ledger's fall",
       "    if charged != fell:\n", "    if False:\n", LEDGER),
    _m("reserved_unchecked", "the reserved total comes back to its value before the run",
       "if Decimal(str(after.reserved_total)) != Decimal(str(before.reserved_total)):",
       "if False:", LEDGER),
    # --- the resume drill, orchestration -----------------------------------------------
    _m("client_problems_ignored", "a client-side problem fails the drill on any target",
       "    if problems:\n        report.check(\"e4b.a.dataset-resume\", FAIL",
       "    if False:\n        report.check(\"e4b.a.dataset-resume\", FAIL", DATASET),
    _m("resume_exit_ignored", "a resumed run that failed fails the drill",
       '    if resumed["exit"] != 0:\n', "    if False:\n", DATASET),
    _m("engine_target_skips_to_ledger", "the engine target pends on the held cutover",
       '    if not target["metered"]:\n', "    if False:\n", DATASET),
    _m("missing_ledger_passes", "no ledger adapter is PENDING on D5, never PASS",
       'report.check("e4b.a.dataset-resume", PENDING,\n                     '
       '"client invariants hold; `infrx',
       'report.check("e4b.a.dataset-resume", PASS,\n                     '
       '"client invariants hold; `infrx', DATASET),
    # --- the protocol ------------------------------------------------------------------
    _m("criterion_drifts_from_protocol", "the runner applies the predeclared numbers",
       '"ttft_p95_short_s": 6.0,', '"ttft_p95_short_s": 8.0,', PROTOCOL),
    _m("matrix_drifts_from_protocol", "the local cell shapes are the predeclared ones",
       '"dataset": {"items": 12, "interrupt_after": 4, "rate": 4.0}},',
       '"dataset": {"items": 12, "interrupt_after": 6, "rate": 4.0}},', PROTOCOL),
)


def run(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


def main() -> int:
    return shared.main(MUTANTS, RUNNER, "run E4B's mutation list")


if __name__ == "__main__":
    raise SystemExit(main())
