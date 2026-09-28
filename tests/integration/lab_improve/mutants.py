#!/usr/bin/env python3
"""R32/R83 for E7L: one single-edit defect per decision the gate claims, through the shared
runner (`apps/infrx-api/tests/contracts/mutants.py`, a private copy whose Python-only compile
check is relaxed for the SQL targets, as E3L's, E6L's and track I's).

* `MUTANTS` (layer 1, no stack): the runner's classification, cells, gate and exit codes, the
  NOT RUN vocabulary, its ports, and the unbound i07-i09 cases (never a pass once they stop
  skipping).
* `STACK_MUTANTS` ("the suite catches holdout leakage, relabeled synthetic truth, duplicate
  paid submission, missing transitive revocation and promotion from training loss alone"):
  the product decisions the two iterations guard - P1's forged-truth refusal, method
  mapping and train-only export, P2's holdout exclusion, one intent per chunk and budget
  stop, P3's holdout pin, eligibility gate, rights re-read at submit, reconciliation by
  lookup, unknown outcome, single settlement, digest and late-checkpoint refusals and
  redelivery, N3's permanent tombstones and B2's rejection - killed on a kept e7l stack by
  the scenario cases, in a copy that imports the mutated package. Without a kept stack they
  skip visibly:

    apps/infrx-api/.venv/bin/python tests/integration/lab_improve/runner.py --keep --out <dir>
    INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e7l apps/infrx-api/.venv/bin/python -m pytest -q \\
        -p no:cacheprovider tests/integration/lab_improve/test_mutants.py

`test_i04_a_regrant_resurrects_no_tombstoned_sample_into_training` FAILS on this base
(0-E7L-1: P1/P3 read D7's gate, not N3's): it is outside the stack list until the fix, whose
own edit is the defect a mutant would re-introduce.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
API_DIR = REPO / "apps" / "infrx-api"


def _shared():
    path = API_DIR / "tests" / "contracts" / "mutants.py"
    spec = importlib.util.spec_from_file_location("e7l_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = _shared()
Mutant, Outcome, Result, Runner, _m = (shared.Mutant, shared.Outcome, shared.Result,
                                       shared.Runner, shared._m)
shared.compile = lambda source, filename, mode, *a, **k: (
    compile(source, filename, mode, *a, **k) if str(filename).endswith(".py") else None)

R = "tests/integration/lab_improve/runner.py"
W = "tests/integration/lab_improve/lab_world.py"
P = "tests/integration/lab_improve/scenarios_pending.py"
LAYER1_FILES = ("tests/integration/lab_improve/test_e7l_runner.py", P)

MATRIX = "test_e7l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases"
REQUIRED = "test_e7l_the_required_cases_are_exactly_what_the_scenario_modules_define"
SKIPS = "test_e7l_a_skip_is_never_a_pass_and_names_its_kind"
MISSING = "test_e7l_a_scenario_missing_a_required_case_is_not_run"
HARNESS = "test_e7l_a_harness_error_is_invalid_not_a_product_fail"
GATE = "test_e7l_the_gate_and_the_cells_are_the_worst_status_and_exit_as_e2c_does"
NO_STACK = "test_e7l_no_stack_blocks_every_scenario"
NAMESPACE = "test_e7l_the_namespace_is_the_reserved_block"
RERUN = "test_e7l_a_not_run_case_names_its_lanes_and_the_exact_rerun"
TRIPWIRE = "test_e7l_the_i07_tripwire_fires_on_a_worker_pass_not_the_module"
UNBOUND = tuple(re.findall(r"^def (test_i\d\d_\w+)\(", (REPO / P).read_text(), re.M))

MUTANTS: tuple[Mutant, ...] = (
    _m("xfail_is_a_pass", "an xfail is never a pass", R,
       '        return NOT_RUN, "xfail is not a pass here: " + message[:300]',
       '        return PASS, ""', SKIPS),
    _m("not_run_is_a_pass", "a skip without a mark is NOT RUN", R,
       "[mark.group(1)] if mark else NOT_RUN,", "[mark.group(1)] if mark else PASS,", SKIPS),
    _m("required_cases_ignored", "a scenario missing a required case is NOT RUN", R,
       '        if absent and entry["cases"]:', "        if False:", MISSING),
    _m("harness_error_is_a_product_fail", "a broken harness is INVALID, never FAIL", R,
       "if HARNESS.search(message)", "if False and HARNESS.search(message)", HARNESS),
    _m("gate_is_the_best_status", "the gate is the WORST status", R,
       "    return max(statuses, key=RANK.__getitem__, default=NOT_RUN)",
       "    return min(statuses, key=RANK.__getitem__, default=NOT_RUN)", GATE),
    _m("a_cell_over_every_scenario", "a cell is judged on the scenarios carrying its test id",
       R, 'if tid in spec["test_ids"]) for tid in TEST_IDS}', ") for tid in TEST_IDS}", GATE),
    _m("not_run_exits_zero", "NOT RUN exits 3 (E2C), never 0", R,
       "EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}",
       "EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 0, INVALID: 4}", GATE),
    _m("no_stack_passes", "no stack blocks every scenario", R,
       'entry["status"], entry["reasons"] = BLOCKED, [f"BLOCKED[stack] {why}"]',
       'entry["status"], entry["reasons"] = PASS, [f"BLOCKED[stack] {why}"]', NO_STACK),
    _m("a_manifest_test_id_dropped", "the cells are the manifest's test ids", R,
       'TEST_IDS = ("DATA-LINEAGE", "PIPELINE-LINEAGE", "PIPELINE-BUDGET", "TRAIN-RECOVER")',
       'TEST_IDS = ("DATA-LINEAGE", "PIPELINE-LINEAGE", "PIPELINE-BUDGET")', MATRIX),
    _m("a_merged_cell_waits", "the cells the merged code supports run now", R,
       '            "test_ids": ["PIPELINE-BUDGET"], "lanes": []},',
       '            "test_ids": ["PIPELINE-BUDGET"], "lanes": ["composition-2"]},', MATRIX),
    _m("a_required_case_renamed", "the required cases are the modules' cases", R,
       '    "i06": ("test_i06_a_timeout_after_accept_and_a_lost_poll_are_one_paid_job",),',
       '    "i06": ("test_i06_a_lost_poll",),', REQUIRED),
    _m("another_namespace", "e7l runs in its own reserved block", R,
       'NAMESPACE = "e7l"', 'NAMESPACE = "e6l"', NAMESPACE),
    _m("an_endpoint_on_a_service_port", "the endpoints avoid E2's service ports", R,
       '"bad": 57362,', '"bad": 57332,', NAMESPACE),
    _m("the_teacher_on_the_protocol_port", "the scenarios' host processes never share a port",
       R, 'SERVICE_PORTS = {"teacher": 57364, "protocol": 57365}',
       'SERVICE_PORTS = {"teacher": 57365, "protocol": 57365}', NAMESPACE),
    _m("a_spare_on_a_service_port", "the spare ports avoid E2's service ports", R,
       "SPARE_PORTS = tuple(range(57366, 57379))", "SPARE_PORTS = tuple(range(57366, 57380))",
       NAMESPACE),
    _m("not_run_without_the_rerun", "a NOT RUN names the exact rerun", W,
       "rerun after the merge: {RERUN} --only {sid}", "rerun after the merge: {RERUN}", RERUN),
    _m("i07_fires_on_the_module", "composition-2's module alone binds nothing (its roles "
       "refuse by name); a real pass does", P,
       '    return main.exists() and f"{role} has no worker pass" not in main.read_text()',
       "    return main.exists()", TRIPWIRE),
    _m("unbound_case_runs", "a case waiting on composition-2/LAB_PIPELINES/staging is never a "
       "pass", P, "    lw.not_run(sid, *lanes, why=why)", "    return", *UNBOUND),
)

# ------------------------------------------------------------------ the stack list

P1 = "infrx/pipelines/annotations/__init__.py"
P2 = "infrx/pipelines/teachers/__init__.py"
P3 = "infrx/pipelines/training/__init__.py"
N3 = "infrx/datasets/lineage/__init__.py"
B2 = "infrx/evaluation/reports/__init__.py"

I01_LABELS = "test_i01_labels_keep_their_method_evidence_and_review"
I01_TEACHER = "test_i01_the_teacher_never_sees_the_holdout_and_its_labels_stay_synthetic"
I02_BUNDLE = "test_i02_the_bundle_trains_on_the_train_export_and_pins_the_holdout"
I02_ELIGIBLE = "test_i02_a_candidate_is_eligible_only_after_its_holdout_evaluation"
I02_BAD = "test_i02_the_bad_checkpoint_with_the_better_training_loss_is_rejected"
I02_REJECTED = "test_i02_missing_and_late_checkpoints_are_rejected_and_never_evaluated"
I03_TRACES = "test_i03_permitted_traces_become_a_derived_version_over_the_frozen_holdout"
I03_SECOND = "test_i03_the_second_iteration_improves_on_the_same_holdout_with_its_lineage"
I03_BUDGET = "test_i03_budgets_reconcile_per_unit_across_both_iterations"
I04_REVOKED = "test_i04_a_revocation_stops_the_queue_the_submit_and_the_export_reads"
I04_GATE = "test_i04_a_regrant_leaves_the_n3_gate_closed"
I05_DUP = "test_i05_a_duplicate_teacher_submit_is_one_paid_job"
I05_AMBIGUOUS = "test_i05_an_ambiguous_teacher_submit_is_held_and_never_resubmitted"
I05_BUDGET = "test_i05_the_budget_stops_the_batch_before_the_chunk_it_cannot_cover"
I06 = "test_i06_a_timeout_after_accept_and_a_lost_poll_are_one_paid_job"
#: FAILS on this base (0-E7L-1); outside the stack list until P1/P3 read N3's gate
FAILING = ("test_i04_a_regrant_resurrects_no_tombstoned_sample_into_training",)

STACK_MUTANTS: tuple[Mutant, ...] = (
    _m("st_forged_truth_imported", "a row claiming ground truth is refused (relabeled "
       "synthetic truth)", P1, '    if row.get("ground_truth", False) is not False:',
       "    if False:", I01_LABELS),
    _m("st_teacher_labels_imported", "a teacher's label is published synthetic, never "
       "imported/human", P1, 'METHODS = {"human": "imported", "model": "synthetic"}',
       'METHODS = {"human": "imported", "model": "imported"}', I01_LABELS, I01_TEACHER),
    _m("st_export_ships_holdout", "an export is train-only: the holdout is never training "
       "input", P1, '        if where[sample.sample_id] != "train":', "        if False:",
       I01_LABELS, I03_SECOND),
    _m("st_teacher_sees_holdout", "the holdout is never sent to a teacher", P2,
       "        if sample_id in holdout:", "        if False:", I01_TEACHER),
    _m("st_second_teacher_intent", "one submit intent per chunk: a duplicate submit is no "
       "second paid job", P2,
       "        if not mine:\n            runs.append(run)      # another call holds (or held) "
       "this chunk's intent\n            continue",
       "        if False:\n            runs.append(run)      # another call holds (or held) "
       "this chunk's intent\n            continue", I05_DUP, I05_AMBIGUOUS),
    _m("st_budget_skips_not_stops", "the budget stops the batch at the chunk it cannot cover",
       P2, '            return BatchReport(tuple(runs), "budget", (run_id, *unsent))',
       "            continue", I05_BUDGET),
    _m("st_bundle_carries_holdout", "a bundle pins the holdout, never carries it", P3,
       '        "holdout": {"size": len(holdout),',
       '        "holdout": {"size": len(holdout), "ids": holdout,', I02_BUNDLE),
    _m("st_eligible_without_holdout_run", "eligibility needs a succeeded holdout run "
       "(never training loss)", P3,
       'result.get("holdout_sha256")) != ("succeeded", bundle["dataset_ref"], "holdout",',
       'result.get("holdout_sha256")) != (result.get("state"), bundle["dataset_ref"], '
       '"holdout",', I02_ELIGIBLE),
    _m("st_submit_ignores_revocation", "submit re-reads every bundled sample's training "
       "grant", P3, '    if set(bundle["train"] + bundle["dev"]) - allowed:', "    if False:",
       I04_REVOKED),
    _m("st_ambiguous_not_reconciled", "an ambiguous run is reconciled by lookup on resume",
       P3, '    if run["state"] in ("submitting", "ambiguous"):',
       '    if run["state"] in ("submitting",):', I06),
    _m("st_timeout_is_a_refusal", "a timeout after accept is unknown (ambiguous), never "
       "failed", P3,
       "    except Exception:                                   # noqa: BLE001 - the outcome "
       "is unknown\n        return await ledger.move(external_run_id, provider_org_id="
       "provider_org_id,\n                                 expected=\"submitting\", target="
       "\"ambiguous\")",
       "    except Exception:                                   # noqa: BLE001 - the outcome "
       "is unknown\n        return await ledger.move(external_run_id, provider_org_id="
       "provider_org_id,\n                                 expected=\"submitting\", target="
       "\"failed\")", I06),
    _m("st_poll_settles_again", "a finished run is not polled (settled) again", P3,
       '    run = await _run(ledger, provider_org_id, external_run_id)\n'
       '    if run["state"] != "submitted":\n        return run\n'
       '    status = await connector.status(run["job_id"])',
       '    run = await _run(ledger, provider_org_id, external_run_id)\n'
       '    if False:\n        return run\n'
       '    status = await connector.status(run["job_id"])', I06),
    _m("st_manual_run_reserves", "the manual bundle is no platform-paid job: it reserves "
       "nothing", P3, "    paid = connector.name != MANUAL", "    paid = True",
       I02_BUNDLE, I03_BUDGET),
    _m("st_digest_unchecked", "a checkpoint is the declared bytes", P3,
       '    if "sha256:" + hashlib.sha256(data).hexdigest() != digest:', "    if False:",
       I02_REJECTED),
    _m("st_late_checkpoint_accepted", "a checkpoint after cancel is rejected", P3,
       '    if run["state"] not in ACTIVE:', "    if False:", I02_REJECTED),
    _m("st_redelivery_reevaluates", "a redelivered checkpoint is the first outcome", P3,
       "    if prior is not None:                       # a redelivery: the first outcome "
       "stands", "    if False:", I02_ELIGIBLE),
    _m("st_corrections_dropped", "the grantor's D6F feedback rides beside the original as "
       "corrections", N3, "for f in await feedback(grantor_org_id, request)] if joined else []",
       "for f in await feedback(grantor_org_id, request)] if False else []", I03_TRACES),
    _m("st_tombstones_ignored", "a tombstone is permanent: a re-grant resurrects nothing",
       N3, '    stones = {_id(k) for k in await objects.keys(f"{base}/tombstones/")} & wanted',
       "    stones = set()", I04_GATE),
    _m("st_inferior_accepted", "an inferior candidate is rejected (training loss never "
       "decides)", B2,
       '    inferior = [f"{name} inferior" for name, v in verdicts if v == "inferior"]',
       "    inferior = []", I02_BAD),
)
STACK_CASES = tuple(sorted({case for m in STACK_MUTANTS for case in m.cases}))
SCENARIO_FILES = ("scenarios_iterate.py", "scenarios_faults.py")


def case_names() -> set[str]:
    """The layer-1 cases: the runner's own, and the unbound i07-i09 cases."""
    return set(re.findall(r"^def (test_\w+)\(", (REPO / LAYER1_FILES[0]).read_text(), re.M)) \
        | set(UNBOUND)


def stack_case_names() -> set[str]:
    """Every running scenario case, except the one that fails on this base (`FAILING`)."""
    return {name for file in SCENARIO_FILES
            for name in re.findall(r"^def (test_i\d\d_\w+)\(", (HERE / file).read_text(), re.M)} \
        - set(FAILING)


def _layer1(root: pathlib.Path) -> pathlib.Path:
    junk = shutil.ignore_patterns("__pycache__", ".venv", "node_modules", ".next")
    shutil.copytree(REPO / "tests" / "integration", root / "tests" / "integration", ignore=junk)
    shutil.copytree(API_DIR / "infrx", root / "apps" / "infrx-api" / "infrx", ignore=junk)
    (root / "research" / "plan").mkdir(parents=True)
    shutil.copy2(REPO / "research" / "plan" / "tasks.json", root / "research" / "plan" / "tasks.json")
    return root


def _stack(root: pathlib.Path) -> pathlib.Path:
    """The copy one level down as `apps/infrx-api` (PYTHONPATH: the scenarios import it),
    beside the scenarios, B/D's test worlds and the migrations the Lab database is built from."""
    api = _layer1(root) / "apps" / "infrx-api"
    migrations = pathlib.Path("apps", "app", "supabase", "migrations")
    shutil.copytree(REPO / migrations, root / migrations)
    shutil.copytree(API_DIR / "tests", api / "tests",
                    ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
    (root / "apps" / "lab").symlink_to(REPO / "apps" / "lab")
    return api


RUNNER = Runner(name="e7l", targets=LAYER1_FILES, package="", layout=_layer1)
#: the kept stack's identity, handed to the copy (harness.working_dir / STATE_FILE seams)
STACK_ENV = ("INFRX_E2_NAMESPACE", "INFRX_E2_CHECKOUT", "INFRX_E2_STATE_FILE")
STACK_RUNNER = Runner(name="e7l-stack", package="", layout=_stack, env=STACK_ENV,
                      timeout_s=1800,
                      targets=tuple(f"../../tests/integration/lab_improve/{f}"
                                    for f in SCENARIO_FILES))


def claim_the_kept_stack() -> str | None:
    """Point the copies at the kept e7l stack; None if there is one, else why not."""
    os.environ["INFRX_E2_NAMESPACE"] = "e7l"
    sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "backend")]
    import lab_world
    if lab_world.harness.NAMESPACE != "e7l":
        return (f"this process loaded E2's harness as {lab_world.harness.NAMESPACE!r}: run the "
                "stack list in its own process with INFRX_E2_NAMESPACE=e7l")
    if not lab_world.stack.has_stack():
        return f"no kept e7l stack: run {lab_world.RUNNER} --keep first"
    os.environ["INFRX_E2_CHECKOUT"] = lab_world.harness.working_dir()
    os.environ["INFRX_E2_STATE_FILE"] = str(lab_world.harness.STATE_FILE)
    return None


def run_mutant(mutant) -> Result:
    if mutant not in STACK_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    return shared.pristine(STACK_CASES, STACK_RUNNER) or shared.run_mutant(mutant, STACK_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run E7L's layer-1 mutation list"))
