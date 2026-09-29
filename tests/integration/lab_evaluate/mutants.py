#!/usr/bin/env python3
"""R32/R83 for E6L: one single-edit defect per decision the gate claims, through the shared
runner (`apps/infrx-api/tests/contracts/mutants.py`, a private copy whose Python-only compile
check is relaxed for the SQL targets, as E3L's and track I's).

* `MUTANTS` (layer 1, no stack): the runner's classification, cells, gate and exit codes, the
  NOT RUN vocabulary.
* `STACK_MUTANTS` (E6L.c, "intentional defects are detected"): the product decisions the
  journey guards - N1's quarantine, replay and grant binding, N2's families, leak refusal and
  frozen holdout, D7's provider scoping, rights read and receipt dedup (0029), H1's pairing
  factors, B2's counting and decision, B3's derived run id - killed on a kept e6l stack by
  the scenario cases, in a copy that imports the mutated package and migrates its own Lab
  database from the mutated migrations. Without a kept stack they skip visibly:

    apps/infrx-api/.venv/bin/python tests/integration/lab_evaluate/runner.py --keep --out <dir>
    INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e6l apps/infrx-api/.venv/bin/python -m pytest -q \\
        -p no:cacheprovider tests/integration/lab_evaluate/test_mutants.py
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


def _sibling(name: str):
    """Loaded under a name unique to this package's own directory: `lab_world` is also every
    sibling lab_*/'s module name (WR-E7L-5) - with two such packages in one process, a bare
    `import lab_world` resolves to whichever package's directory sorts first in sys.path, not
    to the importing file's own package."""
    key = f"{HERE.name}.{name}"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _shared():
    path = API_DIR / "tests" / "contracts" / "mutants.py"
    spec = importlib.util.spec_from_file_location("e6l_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = _shared()
Mutant, Outcome, Result, Runner, _m = (shared.Mutant, shared.Outcome, shared.Result,
                                       shared.Runner, shared._m)
shared.compile = lambda source, filename, mode, *a, **k: (
    compile(source, filename, mode, *a, **k) if str(filename).endswith(".py") else None)

R = "tests/integration/lab_evaluate/runner.py"
W = "tests/integration/lab_evaluate/lab_world.py"
LAYER1_FILES = ("tests/integration/lab_evaluate/test_e6l_runner.py",)

MATRIX = "test_e6l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases"
REQUIRED = "test_e6l_the_required_cases_are_exactly_what_the_scenario_modules_define"
SKIPS = "test_e6l_a_skip_is_never_a_pass_and_names_its_kind"
MISSING = "test_e6l_a_scenario_missing_a_required_case_is_not_run"
HARNESS = "test_e6l_a_harness_error_is_invalid_not_a_product_fail"
GATE = "test_e6l_the_gate_and_the_cells_are_the_worst_status_and_exit_as_e2c_does"
NO_STACK = "test_e6l_no_stack_blocks_every_scenario"
NAMESPACE = "test_e6l_the_namespace_is_the_reserved_block"
RERUN = "test_e6l_a_not_run_case_names_its_lanes_and_the_exact_rerun"
R222 = "test_e6l_r222_accepts_only_a_not_run_out_of_local_scope"
#: the lane's recorded final verdict, read by the R222 case (R234: accepted over its statuses)
RECORDED = "research/plan/evidence/e/E6L-raw-24a7a065/verdict.json"

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
       '            "EVAL-COMPARE", "CHECKPOINT-IDEM")', '            "EVAL-COMPARE",)', MATRIX),
    _m("a_required_case_renamed", "the required cases are the modules' cases", R,
       '    "j10": ("test_j10_the_provider_ui_launches_compares_and_cancels",),',
       '    "j10": ("test_j10_the_provider_ui",),', REQUIRED),
    _m("another_namespace", "e6l runs in its own reserved block", R,
       'NAMESPACE = "e6l"', 'NAMESPACE = "e3l"', NAMESPACE),
    _m("an_endpoint_on_a_service_port", "the synthetic endpoints avoid E2's service ports", R,
       '"baseline_v2": 57263,', '"baseline_v2": 57232,', NAMESPACE),
    _m("a_spare_on_a_service_port", "the spare endpoint ports avoid E2's service ports", R,
       "SPARE_PORTS = tuple(range(57268, 57279))", "SPARE_PORTS = tuple(range(57268, 57280))",
       NAMESPACE),
    _m("not_run_without_the_rerun", "a NOT RUN names the exact rerun", W,
       "rerun after the merge: {RERUN} --only {sid}", "rerun after the merge: {RERUN}", RERUN),
    # R222 (lab-evaluate-2): what the local acceptance excuses
    _m("r222_in_scope_lane_excused", "a NOT RUN on in-scope work (L3's media path) stays open",
       R, 'return set(lanes) <= set(OUT_OF_SCOPE) and bool(entry["cases"]) and',
       'return bool(entry["cases"]) and', R222),
    _m("r222_out_of_scope_widened", "only R234's ruled classes are out of scope for E6L", R,
       '"L3": "product WR: WR-E6L-J11"}', '"L3": "product WR: WR-E6L-J11", "B1": "GPU"}', R222),
    _m("r222_never_run_excused", "a scenario with no case run is open", R,
       'set(lanes) <= set(OUT_OF_SCOPE) and bool(entry["cases"]) and',
       "set(lanes) <= set(OUT_OF_SCOPE) and", R222),
    _m("r222_any_reason_excuses", "every reason must be the scenario's own wait", R,
       "    all(f\"NOT RUN[{','.join(lanes)}]\" in reason for reason in entry[\"reasons\"])",
       "    any(f\"NOT RUN[{','.join(lanes)}]\" in reason for reason in entry[\"reasons\"])",
       R222),
    _m("r222_pass_is_open", "a PASS never keeps the gate from acceptance", R,
       'if entry["status"] != PASS and not excused(sid, entry)}',
       "if not excused(sid, entry)}", R222),
    # coordinator wirings at the lab-evaluate-2 merge (R234/R235)
    _m("r222_fail_excused_by_its_message", "an in-scope FAIL is never excused, whatever its "
       "message says (WR-E6L-RV-1)", R, '        if entry["status"] != NOT_RUN:',
       "        if False:", R222),
    _m("r222_product_wr_dropped", "R234 (ii): j11 waiting on WR-E6L-J11 is out of local scope "
       "(WR-E6L-SCOPE)", R, '"L3": "product WR: WR-E6L-J11"}', "}", R222),
)

# ------------------------------------------------------------------ the stack list

N1 = "infrx/datasets/imports/__init__.py"
N2 = "infrx/datasets/versions/__init__.py"
H1 = "infrx/harnesses/replay.py"
B2 = "infrx/evaluation/reports/__init__.py"
B3 = "infrx/evaluation/checkpoints/__init__.py"
B1 = "infrx/evaluation/runner/__init__.py"
D7 = "../app/supabase/migrations/0029_lab_data.sql"
D7F = "../app/supabase/migrations/0034_lab_eval_followup.sql"

J01_REFUSED = "test_j01_a_benchmark_with_bad_rows_is_refused_until_its_rejects_are_accepted"
J01_REPLAY = "test_j01_a_replayed_upload_is_the_same_dataset_and_a_changed_one_conflicts"
J02 = "test_j02_finite_video_imports_with_its_cap_and_bundle_checks"
J03_FOREIGN = "test_j03_a_foreign_dataset_is_refused_at_import_resolve_and_schedule"
J03_REVOKED = "test_j03_a_revoked_grant_stops_derivation_and_scheduling"
J04_FAMILIES = "test_j04_related_sources_are_placed_whole_and_deterministically"
J04_LEAK = "test_j04_a_leaking_base_is_refused_and_the_frozen_holdout_admits_no_relative"
J05_KILL = "test_j05_a_worker_killed_mid_attempt_is_recovered_and_each_case_scored_once"
J05_DUP = "test_j05_duplicate_delivery_scores_each_case_once"
J05_CANCEL = "test_j05_a_cancel_mid_run_stops_spending"
J06 = "test_j06_two_serving_versions_reproduce_the_frozen_benchmark"
J07_REJECT = "test_j07_a_required_slice_regression_rejects_despite_an_aggregate_gain"
J07_ACCEPT = "test_j07_a_clean_win_is_accepted_and_claimed_improved"
J07_MISSING = "test_j07_a_missing_candidate_output_is_counted_not_dropped"
J07_UNFINISHED = "test_j07_an_unfinished_candidate_counts_its_missing_cases"
J07_TOOLS = "test_j07_tool_cases_are_recorded_and_not_comparable"
J08_TWICE = "test_j08_a_checkpoint_delivered_twice_is_one_receipt_and_one_run"
J08_CRASH = "test_j08_a_crash_before_the_decision_is_one_run_on_redelivery"

STACK_MUTANTS: tuple[Mutant, ...] = (
    _m("st_duplicate_row_accepted", "a repeated row is quarantined as a duplicate", N1,
       "                if digest in seen:\n", "                if False:\n", J01_REFUSED),
    _m("st_published_with_rejects", "an import with rejected rows is refused unless accepted",
       N1, "        if not accepted or (rejected and not accept_rejects):",
       "        if not accepted:", J01_REFUSED),
    # (a changed upload's own chunk check is backstopped by D7's source digest: both refuse
    # with a Conflict, so a mutant of either alone is equivalent at this seam - N1's list
    # kills them one by one)
    _m("st_replay_conflicts", "the same bytes again are a no-op: a replay is the same "
       "dataset", N1, "            await objects.head(key) != digest_of(data):",
       "            True:", J01_REPLAY),
    # F3's manifest contract backstops N1's row checks at publication: the typed refusal
    # (LabRejected) is the kill, declared.
    _m("st_video_over_the_cap", "a finite-video span is at most 82 s", N1,
       "not 0 <= start < end <= start + lab.MAX_VIDEO_MS:",
       "not 0 <= start < end <= start + lab.MAX_VIDEO_MS + 1000:", J02,
       dies_by=("LabRejected",)),
    _m("st_grant_of_another_provider", "an import names a grant to its own provider", N1,
       "    if lab.REF_RE.fullmatch(spec.grant_ref).group(2) != spec.provider_org_id:",
       "    if False:", J03_FOREIGN),
    _m("st_split_conflict_accepted", "one group is never declared in two splits", N1,
       "                if groups.get(group, split) != split:", "                if False:",
       J04_LEAK, dies_by=("LabRejected",)),
    _m("st_resolve_any_provider", "a Lab record resolves for its own provider only", D7,
       "   where ref = p_args->>'ref' and provider_org_id = (p_args->>'provider_org_id')::uuid;",
       "   where ref = p_args->>'ref';", J03_FOREIGN),
    _m("st_rights_ignore_the_grant", "a sample is readable only under a grant in force now",
       D7, "     and infrx.lab_grant_current(s.grant_id,\n"
           "                                 coalesce(p_args->>'purpose', 'provider_sharing'))\n"
           "$$;", "     and true\n$$;", J03_REVOKED),
    _m("st_derive_reads_revoked", "a derivation omits what it may no longer read", N2,
       "            if sample.sample_id not in readable:", "            if False:",
       J03_REVOKED),
    _m("st_near_duplicates_unrelated", "text equal up to case and spacing is one family", N2,
       '        return "text:" + " ".join(body["content"].casefold().split())',
       '        return "text:" + body["content"]', J04_FAMILIES, J04_LEAK),
    _m("st_clips_unrelated", "two spans of one clip are one family", N2,
       '    if body.get("media_digest"):', "    if False:", J04_FAMILIES),
    _m("st_leak_allowed", "a base whose declared splits leak is refused", N2,
       "    if leaks:\n        raise LeakRefused(leaks)", "    if False:\n        raise LeakRefused(leaks)",
       J04_LEAK),
    _m("st_holdout_not_frozen", "a new relative of a holdout sample is omitted", N2,
       '            if target == "holdout" and base is not None and i not in placed:',
       "            if False:", J04_LEAK),
    _m("st_serving_not_a_factor", "two serving versions are a single-factor comparison", H1,
       'FACTORS = ("serving_ref", "harness_ref", "seed")', 'FACTORS = ("harness_ref", "seed")',
       J06),
    _m("st_error_not_counted", "an errored case scores 0, it is never dropped", B2,
       '    return 0.0 if record.get("error") else record.get("score")',
       '    return record.get("score")', J07_MISSING),
    _m("st_slice_regression_hidden", "an inferior required slice rejects", B2,
       '    inferior = [f"{name} inferior" for name, v in verdicts if v == "inferior"]',
       '    inferior = [f"{name} inferior" for name, v in verdicts if v == "inferior" and '
       'name == "overall"]', J07_REJECT),
    _m("st_partial_coverage_decides", "incomplete coverage is inconclusive", B2,
       "    if len(pairs) < len(cases):", "    if False:", J07_TOOLS),
    _m("st_improved_on_a_tie", "`improved` needs the lower bound above 0", B2,
       '            "improved": low > 0}', '            "improved": mean >= 0}', J06),
    _m("st_run_id_not_derived", "a checkpoint's run id is derived from (subscription, "
       "checkpoint)", B3,
       '    digest = hashlib.sha256(f"{subscription_id}:{checkpoint_id}".encode()).digest()',
       "    digest = uuid.uuid4().bytes", J08_TWICE, J08_CRASH),
    # 0034 (lab-sql LW2) re-creates lab_receive_checkpoint: 0029's copy is dead code, so the
    # mutant targets the live definition (lab-evaluate-2: it survived on 0029)
    _m("st_receipt_twice", "a redelivered checkpoint is the same receipt, no second event",
       D7F, "    return infrx.lab_receipt_json(c);\n  end if;\n"
           "  insert into infrx.lab_outbox (provider_org_id, kind, payload)\n"
           "  values (v_provider, 'checkpoint_received'",
       "  end if;\n  insert into infrx.lab_outbox (provider_org_id, kind, payload)\n"
       "  values (v_provider, 'checkpoint_received'", J08_TWICE),
    _m("st_expired_case_stays_leased", "a recovered lease puts its case back", D7,
       "  update infrx.lab_eval_cases c set state = 'pending'\n    from expired e",
       "  update infrx.lab_eval_cases c set state = 'leased'\n    from expired e", J05_KILL),
    _m("st_lease_a_leased_case", "a case is leased by one worker at a time", D7,
       "                               where x.run_id = r.run_id and x.state = 'pending'",
       "                               where x.run_id = r.run_id and x.state in ('pending', "
       "'leased')", J05_DUP),
    _m("st_lease_on_a_cancelled_run", "a cancelled run leases nothing more", D7,
       "  if r.state not in ('queued', 'running') then\n"
       "    perform infrx.refuse('already_terminal', 'run ' || r.run_id || ' is ' || r.state);\n"
       "  end if;\n  update infrx.lab_eval_cases set state = 'leased'",
       "  if false then\n"
       "    perform infrx.refuse('already_terminal', 'run ' || r.run_id || ' is ' || r.state);\n"
       "  end if;\n  update infrx.lab_eval_cases set state = 'leased'", J05_CANCEL),
    _m("st_accept_without_evidence", "accept only with every verdict non-inferior", B2,
       '        {"outcome": "inconclusive", "reasons": unsure} if unsure else \\',
       '        {"outcome": "accept", "reasons": unsure} if unsure else \\', J07_MISSING),
    _m("st_missing_not_counted", "a case with no record is counted missing", B2,
       '            "missing": len(cases) - len(recs),', '            "missing": 0,',
       J07_UNFINISHED),
    _m("st_mean_over_the_records", "the mean is over the universe, a missing case scores 0",
       B2, '"mean": math.fsum(v or 0.0 for v in values) / len(cases),',
       '"mean": math.fsum(v or 0.0 for v in values) / max(1, len(recs)),', J07_UNFINISHED),
    _m("st_no_call_fence", "a cancel stops spending at the fence before each model call", B1,
       "        await self._store.heartbeat(lease, lease_s=self._limits.lease_s)   # the fence",
       "        pass", J05_CANCEL),
    _m("st_improving_claims_nothing", "a clean win is claimed improved", B2,
       '            "improved": low > 0}', '            "improved": False}', J07_ACCEPT),
)
J09_EVAL = "test_j09_the_eval_worker_process_killed_mid_run_loses_nothing"
WORKER = "infrx/worker/__main__.py"
LAB_WORKERS = "infrx/lab/workers/__main__.py"
# composition batch 2: the real eval worker process (j09's bound half)
STACK_MUTANTS += (
    _m("st_worker_never_recovers", "the eval worker process recovers expired leases", WORKER,
       ',\n            "lab_recover": lambda: every(LAB_RECOVER_S, store.recover, '
       '"lab recover")}\n', "}\n", J09_EVAL),
    _m("st_worker_other_objects", "the eval role reads the Lab objects where they are",
       LAB_WORKERS, 'env.get("LAB_S3_PREFIX") or LAB_PREFIX,', "LAB_PREFIX,", J09_EVAL),
)
# LAB-E2E: j10, the provider UI - cancelling a finished run must come back refused
J10_UI = "test_j10_the_provider_ui_launches_compares_and_cancels"
STACK_MUTANTS += (
    _m("st_evaluations_cancel_a_finished_run", "a finished run is never cancelled (the page's "
       "second cancel is a conflict)", "infrx/gateway/routes/lab_evaluations.py",
       '    if status["state"] not in LIVE:', "    if False:", J10_UI),
)
# composition batch 5: the real checkpoints worker process (j09's checkpoint half, WR-B3-3)
J09_CKPT = "test_j09_the_checkpoint_worker_drains_the_outbox_once"
STACK_MUTANTS += (
    _m("st_checkpoints_serve_unvalidated", "only a READY private dev revision serves a "
       "checkpoint", B3, "                    and d.state is DeploymentState.ready_private):",
       "                    and d.state is DeploymentState.draft):", J09_CKPT),
    _m("st_checkpoints_run_per_delivery", "a redelivered checkpoint is the same run",
       B3, "    run_id = run_id_of(sub.subscription_id, event.checkpoint_id)\n",
       "    run_id = str(uuid.uuid4())\n", J09_CKPT),
)
# WR-E6L-J11 (lab-eval-media): j11, a finite-video case sent as its presigned URL
STACK_MUTANTS += (
    _m("st_clip_unsigned", "a clip reaches the dev endpoint as its presigned URL", B1,
       "        media = [await clip_url(", "        media = media or [await clip_url(",
       "test_j11_a_finite_video_case_reaches_the_dev_endpoint"),
)
STACK_CASES = tuple(sorted({case for m in STACK_MUTANTS for case in m.cases}))
SCENARIO_FILES = ("scenarios_data.py", "scenarios_eval.py", "scenarios_checkpoint.py",
                  "scenarios_workers.py")


def case_names() -> set[str]:
    """The layer-1 cases: the runner's own (no case waits unbound: j09 and j10 are bound)."""
    return set(re.findall(r"^def (test_\w+)\(", (REPO / LAYER1_FILES[0]).read_text(), re.M))


def stack_case_names() -> set[str]:
    """Every running scenario case (j11 runs since WR-E6L-J11, lab-eval-media)."""
    return {name for file in SCENARIO_FILES
            for name in re.findall(r"^def (test_j\d\d_\w+)\(", (HERE / file).read_text(), re.M)}


def _layer1(root: pathlib.Path) -> pathlib.Path:
    junk = shutil.ignore_patterns("__pycache__", ".venv", "node_modules", ".next")
    shutil.copytree(REPO / "tests" / "integration", root / "tests" / "integration", ignore=junk)
    shutil.copytree(API_DIR / "infrx", root / "apps" / "infrx-api" / "infrx", ignore=junk)
    (root / "research" / "plan").mkdir(parents=True)
    shutil.copy2(REPO / "research" / "plan" / "tasks.json", root / "research" / "plan" / "tasks.json")
    (root / RECORDED).parent.mkdir(parents=True)
    shutil.copy2(REPO / RECORDED, root / RECORDED)
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


RUNNER = Runner(name="e6l", targets=LAYER1_FILES, package="", layout=_layer1)
#: the kept stack's identity, handed to the copy (harness.working_dir / STATE_FILE seams)
STACK_ENV = ("INFRX_E2_NAMESPACE", "INFRX_E2_CHECKOUT", "INFRX_E2_STATE_FILE", "INFRX_LAB_DIR")
STACK_RUNNER = Runner(name="e6l-stack", package="", layout=_stack, env=STACK_ENV,
                      timeout_s=1800,
                      targets=tuple(f"../../tests/integration/lab_evaluate/{f}"
                                    for f in SCENARIO_FILES))


def claim_the_kept_stack() -> str | None:
    """Point the copies at the kept e6l stack; None if there is one, else why not."""
    os.environ["INFRX_E2_NAMESPACE"] = "e6l"
    sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "backend")]
    lab_world = _sibling("lab_world")
    if lab_world.harness.NAMESPACE != "e6l":
        return (f"this process loaded E2's harness as {lab_world.harness.NAMESPACE!r}: run the "
                "stack list in its own process with INFRX_E2_NAMESPACE=e6l")
    if not lab_world.stack.has_stack():
        return f"no kept e6l stack: run {lab_world.RUNNER} --keep first"
    os.environ["INFRX_E2_CHECKOUT"] = lab_world.harness.working_dir()
    os.environ["INFRX_E2_STATE_FILE"] = str(lab_world.harness.STATE_FILE)
    os.environ["INFRX_LAB_DIR"] = str(REPO / "apps" / "lab")      # j10's UI suite (LAB-E2E)
    return None


def run_mutant(mutant) -> Result:
    if mutant not in STACK_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    return shared.pristine(STACK_CASES, STACK_RUNNER) or shared.run_mutant(mutant, STACK_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run E6L's layer-1 mutation list"))
