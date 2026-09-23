#!/usr/bin/env python3
"""r1 R32 / R83 for W4: every invariant the W4 cases claim must be killable.

One kill rule (`tests/contracts/mutants.py`), three kinds of target:

* **the measurement code** (`models/marlin2b/measure/{decide,parity}.py`) and **the worker**
  (`infrx/worker/*.py`) go through the shared pytest runner, one process per mutant, on a
  copy of the repository's shape (`w3_mutants._layout` plus the measure scripts, the corpus
  manifests and the committed W3 sweep the golden cases read);
* **the box scripts** (`candidate.sh`, `concurrency.sh`) cannot - the runner compiles every
  mutated file as Python - so the edit lands on a throwaway copy of `models/` and the named
  case's own check runs in process under `assertion_kill`, after the same check passed on
  the unmutated copy (R83 (b); cached per case, the candidate checks run whole stub boxes).

    uv run --frozen pytest -q tests/w/test_w4_mutants.py                 # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_w4_mutants.py
    uv run --frozen python -m tests.w.w4_mutants --list
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Outcome, Result, Runner, _m
from . import test_w4 as w4
from . import w3_mutants

REPO = shared.API_DIR.parents[1]
# what the cases read outside the package, relative to the repository root
MODELS_FILES = ("models/common/env.sh", "models/marlin2b/model.env", "models/marlin2b/serve.sh",
                "models/marlin2b/serving-version.json", "models/marlin2b/corpus/manifest.json",
                "models/marlin2b/corpus-synth/manifest.json")


def _repo_copy(root: pathlib.Path) -> None:
    for name in MODELS_FILES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, root / name)
    shutil.copytree(REPO / w4.MEASURE, root / w4.MEASURE,
                    ignore=shutil.ignore_patterns("__pycache__"))


def _layout(root: pathlib.Path) -> pathlib.Path:
    api = w3_mutants._layout(root)
    _repo_copy(root)
    (api / "deploy").mkdir()                  # test_serving imports I's preflight by path
    shutil.copy2(shared.API_DIR / "deploy" / "preflight.py", api / "deploy" / "preflight.py")
    shutil.copytree(REPO / w4.SWEEP, root / w4.SWEEP)
    return api


TARGETS = ("tests/w/test_w4.py",)
WORKER = Runner(name="w4", targets=TARGETS, layout=_layout)
# a mutant's `file` is relative to models/ for this one: api/../../models is the copy's
MEASURE = Runner(name="w4-measure", targets=TARGETS, layout=_layout, package="../../models")

D = "marlin2b/measure/decide.py"
P = "marlin2b/measure/parity.py"
C = "marlin2b/measure/candidate.sh"
S = "marlin2b/measure/concurrency.sh"
E = "worker/engine.py"

PROTOCOL = "test_engine_opt__the_protocol_states_the_criteria_decide_applies"
NO_LEVEL = "test_engine_opt__the_050411Z_sweep_has_no_qualifying_level"
SET_ASIDE = "test_engine_opt__the_set_aside_reproduces_the_recorded_c16_only_when_named"
UNKNOWN = "test_perf_envelope__an_unsupported_tail_or_blank_sample_is_unknown"
STATES = "test_perf_envelope__cells_at_different_cache_states_are_not_compared"
PASSING = "test_engine_opt__a_passing_candidate_is_adopted_and_each_disqualifier_blocks_it"
PARITY = "test_media_parity__token_or_content_drift_disqualifies"
CLIENT = ("test_media_parity__the_parity_client_records_usage_content_and_the_engines_"
          "refusal")
CEILING = "test_engine_opt__the_interim_ceiling_fits_every_geometry_and_e1_covers_120_s"
LABEL = "test_perf_envelope__the_sweep_labels_the_engine_state_it_is_given"
RESTORES = "test_ops_recover__the_candidate_run_restores_the_engine_it_found"
REFUSES = "test_ops_recover__the_candidate_run_refuses_in_flight_work_and_unlisted_flags"
RECORDS = "test_ops_recover__the_candidate_run_records_start_to_ready_and_engine_errors"
REFUSAL = "test_engine_opt__a_deterministic_engine_refusal_settles_once_and_free"
BOUNDARY = "test_engine_opt__the_rule_holds_at_its_boundaries"

SCRIPT_CHECKS = {
    PROTOCOL: lambda repo, tmp: w4.check_protocol_matches(repo),
    LABEL: w4.check_the_sweep_labels_its_state,
    RESTORES: w4.check_candidate_restores,
    REFUSES: w4.check_candidate_refuses,
    RECORDS: w4.check_candidate_records,
}


def _dropped(name: str, call: str) -> Mutant:
    return _m(f"{name}_not_applied", f"the {name} disqualifier blocks adoption",
              D, f'    v["{name}"] = {call}', f'    v["{name}"] = (PASS, "not checked")', PASSING)


PY_MUTANTS: tuple[Mutant, ...] = (
    # --- item 1 --------------------------------------------------------------------------
    _m("threshold_edited_in_decide_only", "the protocol states the numbers decide.py applies",
       D, "MAX_TTFT_P95_S = 30.0", "MAX_TTFT_P95_S = 45.0", PROTOCOL),
    # --- item 4: the rule ----------------------------------------------------------------
    _m("failures_counted_over_accepted_only", "F counts every failed attempt",
       D, 'r.get("error_class") or "unclassified" for r in self.rows if r["outcome"] == "failed")',
       'r.get("error_class") or "unclassified" for r in self.accepted if r["outcome"] == "failed")',
       NO_LEVEL),
    _m("blank_sample_read_as_zero", "a blank sample is unknown, never 0",
       D, "        return float(value)\n", "        return float(value or 0)\n", UNKNOWN),
    _m("t_fraction_lowered", "W3's rule is T >= 0.9 x max T",
       D, "T_FRACTION = 0.9", "T_FRACTION = 0.8", SET_ASIDE, PROTOCOL),
    _m("floor_x_ignored", "the setting is min(c*, floor(X))",
       D, "return min(c, math.floor(x)) if", "return c if", SET_ASIDE),
    _m("set_aside_by_default", "a set-aside is named, never the default",
       D, "def load(root, set_aside=()) -> Run:",
       'def load(root, set_aside=("c012", "c025", "c038", "c051")) -> Run:', SET_ASIDE),
    _m("set_aside_by_default_on_the_command_line", "the CLI sets nothing aside unless named",
       D, 'ap.add_argument("--set-aside", default="",',
       'ap.add_argument("--set-aside", default="c012,c025,c038,c051",', SET_ASIDE),
    _m("pre_fix_waiting_trusted", "a nonzero waiting peak from the pre-82a7dbf sampler is unknown",
       D, "self.W = max(waiting) if exact_waiting or max(waiting) == 0 else None",
       "self.W = max(waiting)", NO_LEVEL),
    _m("p95_without_sufficiency", "a p95 needs 60 accepted samples",
       D, "    if len(values) < P95_MIN_ACCEPTED:\n", "    if not values:\n", UNKNOWN),
    _m("warm_compared_with_restarted", "cells at different states are never compared",
       D, "comparable = [c for c in common if run.levels[c].profile == base.levels[c].profile]",
       "comparable = list(common)", STATES),
    _m("unknown_is_adopted", "an unknown criterion adopts nothing",
       D, "if not failed and not unknown and chosen", "if not failed and chosen", SET_ASIDE),
    _dropped("error_rate", "error_verdict(run)"),
    _dropped("overload_masking", "masking_verdict(run)"),
    _dropped("severe_tail",
             '(UNKNOWN, f"not restarted at c={stale}") if stale else tail_verdict(run, cstar)'),
    _dropped("oom", "oom_verdict(run)"),
    _dropped("memory_growth", "memory_verdict(run)"),
    _dropped("cancellation", "cancellation_verdict(run)"),
    _dropped("short_job_starvation", "starvation_verdict(run, base, comparable)"),
    _dropped("usage_drift", "usage_verdict(run, base, comparable)"),
    _dropped("output_drift", "parity_verdict(run.parity, base.parity)"),
    # --- review round 2: D1 (restarted cells only), D2 (reconciled denominators), D3, D4-D7
    _m("warm_levels_in_the_rule", "the rule and the tail are taken over restarted cells only",
       D, '    stale = [c for c, level in sorted(run.levels.items())\n'
          '             if level.profile.get("engine_state") != "restarted"]',
       "    stale = []", STATES, NO_LEVEL),
    _m("raw_rows_not_reconciled", "raw rows must be every attempt the bench row counts",
       D, "        self.reconciled = (", "        self.reconciled = True or (", PASSING),
    _m("error_rate_ignores_reconciliation", "an unreconciled denominator is unknown, not smaller",
       D, "    if loose:\n", "    if False:\n", PASSING),
    _m("missing_retries_read_as_zero", "retries not recorded are unknown, not zero",
       D, '        self.retries_known = all("retries" in r for r in rows) and \\',
       "        self.retries_known = True or \\", PASSING),
    _m("largest_qualifying_level", "c* is the smallest qualifying level",
       D, "    for c in sorted(levels):\n        level = levels[c]",
       "    for c in sorted(levels, reverse=True):\n        level = levels[c]", BOUNDARY),
    _m("oom_line_ignored", "an 'out of memory' engine line disqualifies",
       D, "    if exited or oom:", "    if exited:", PASSING),
    _m("gpu_growth_unchecked", "GPU memory growth at c=32 disqualifies",
       D, "    ok = gpu <= MAX_GPU_GROWTH_MIB and host", "    ok = True and host", PASSING),
    _m("blank_usage_compared", "usage missing on both sides is unknown, not equal",
       D, "    if blank:\n", "    if False:\n", PASSING),
    _m("parity_blank_usage_compared", "a parity answer without usage is unknown, not equal",
       D, '            if row.get("prompt_tokens") is None or other.get("prompt_tokens") is None:',
       "            if False:", PARITY),
    _m("setting_below_one_adopted", "floor(X) < 1 adopts nothing",
       D, "and chosen is not None and chosen >= 1:", "and chosen is not None:", PASSING),
    _m("floor_x_rounds", "the setting floors X",
       D, "return min(c, math.floor(x)) if", "return min(c, round(x)) if", SET_ASIDE),
    _m("baseline_may_be_the_candidate", "the baseline is never the candidate's own run",
       D, "    if base.root.resolve() == run.root.resolve():", "    if False:", PASSING),
    _m("any_pair_accepted", "only (E1, E0) and (E3, E1) are compared",
       D, "    elif pair not in PAIRS:", "    elif False:", PASSING),
    _m("missing_levels_ignored", "c* is taken over all six predeclared levels",
       D, "    missing = [c for c in LEVELS if c not in run.levels]", "    missing = []", PASSING),
    # --- D10: the boundaries ---------------------------------------------------------------
    _m("threshold_exclusive", "T exactly 0.9 x max T qualifies",
       D, "level.T >= threshold", "level.T > threshold", BOUNDARY),
    _m("error_rate_boundary", "1 % failures is not below 1 %",
       D, "rate < MAX_FAILURE_RATE", "rate <= MAX_FAILURE_RATE", BOUNDARY),
    _m("tail_skips_c_star", "the tail is checked at c* itself",
       D, "if level <= cstar)", "if level < cstar)", BOUNDARY),
    _m("x_is_the_largest", "X is the smallest the engine reported",
       D, "self.X = min(xs) if xs else None", "self.X = max(xs) if xs else None", BOUNDARY),
    _m("set_aside_prefix_without_dash", "a set-aside names whole clip ids",
       D, 'startswith(name + "-")', "startswith(name)", BOUNDARY),
    _m("starvation_slack_dropped", "the starvation bound is ratio x baseline + slack",
       D, "STARVATION_RATIO * theirs + STARVATION_SLACK_S", "STARVATION_RATIO * theirs", BOUNDARY),
    _m("parity_pairs_by_clip_only", "parity pairs rows on the same clip bytes",
       D, 'clip, other = row["clip_id"], base.get((row["clip_id"], row.get("sha256")))',
       'clip, other = row["clip_id"], next((r for r in baseline if r["clip_id"] == '
       'row["clip_id"]), None)', BOUNDARY),
    # --- item 5: parity ------------------------------------------------------------------
    _m("hash_comparison_dropped", "clips both accept answer identically (or by events)",
       D, 'elif row.get("content_sha256") != other.get("content_sha256") and not (',
       "elif False and not (", PARITY),
    _m("prompt_token_equality_skipped", "clips both accept have equal prompt_tokens",
       D, '            elif row.get("prompt_tokens") != other.get("prompt_tokens"):',
       "            elif False:", PARITY),
    _m("refusal_count_unchecked", "a newly accepted clip's count is the pinned processor's",
       D, "elif int(refused.group(1)) != expected:", "elif False:", PARITY),
    _m("overhead_band_unchecked", "a newly accepted clip's prompt is its video plus its text",
       D, "            elif not (OVERHEAD_PER_GROUP_MIN\n",
       "            elif False and not (OVERHEAD_PER_GROUP_MIN\n", PARITY),
    _m("missing_candidate_row_ignored", "a clip the candidate was never asked is unknown",
       D, 'for clip in sorted({r["clip_id"] for r in baseline} - {r["clip_id"] for r in candidate}):',
       "for clip in ():", PARITY),
    _m("smart_resize_rounds", "the processor arithmetic floors when it downscales",
       D, "h_bar = max(factor, math.floor(height / beta / factor) * factor)",
       "h_bar = max(factor, round(height / beta / factor) * factor)", PARITY),
    _m("frames_not_rounded_to_even", "parity sends the worker's frame budget",
       D, "    return count + count % 2\n", "    return count\n", CLIENT),
    _m("refusal_text_not_recorded", "the engine's own refusal text is evidence",
       P, 'str(error.get("message") if isinstance(error, dict)',
       'str("refused" if isinstance(error, dict)', CLIENT),
    _m("accepted_without_done", "a stream without [DONE] is not an answer",
       P, '    if done and out["error_message"] is None and out["finish_reason"]',
       '    if out["error_message"] is None and out["finish_reason"]', CLIENT),
    _m("parity_sends_no_eos", "parity requests carry both EOS ids, as the worker's do",
       P, '"stop_token_ids": EOS,', '"stop_token_ids": [],', CLIENT),
    _m("accepted_without_usage", "an answer without usage is not accepted",
       P, ' and out["finish_reason"] and out["prompt_tokens"]:', ' and out["finish_reason"]:',
       CLIENT),
    _m("parity_check_always_passes", "--check fails when a parity clip is missing",
       P, "        return 1 if missing else 0", "        return 0", CLIENT),
    # --- P-20 ----------------------------------------------------------------------------
    _m("ceiling_ignores_the_sampling_edge", "the interim ceiling holds when fewer frames are "
       "sampled", D, "for sampled in range(max(MIN_FRAMES, budget - 2), budget + 1):",
       "for sampled in range(budget, budget + 1):", CEILING),
)

WORKER_MUTANTS: tuple[Mutant, ...] = (
    _m("engine_refusal_untyped", "a refusal after the headers is a typed engine_error",
       E, '        if obj.get("error") is not None:', "        if False:", REFUSAL),
)

SCRIPT_MUTANTS: tuple[Mutant, ...] = (
    _m("e1_flag_off_protocol", "candidate.sh runs the flags the protocol predeclared",
       C, "  e1) flags=(--max-num-batched-tokens 32768) ;;",
       "  e1) flags=(--max-num-batched-tokens 24576) ;;", PROTOCOL),
    _m("e3_flag_dropped", "E3 is E1 plus --api-server-count 2, as predeclared",
       C, "  e3) flags=(--max-num-batched-tokens 32768 --api-server-count 2) ;;",
       "  e3) flags=(--max-num-batched-tokens 32768) ;;", PROTOCOL),
    # --- review round 2: BS-1..BS-7 --------------------------------------------------------
    _m("unit_not_active_accepted", "a unit that is not active is not the engine to restore",
       C, '[ "$(systemctl is-active "$UNIT" 2>/dev/null || true)" = active ] \\\n  || refuse',
       "true \\\n  || refuse", REFUSES),
    _m("second_run_not_locked", "one candidate run at a time",
       C, "flock -n 9 || refuse", "true || refuse", REFUSES),
    _m("out_not_canonical", "OUT is canonical: no `..` walks out of $NVME/w4-*",
       C, '[ "$(realpath -m -s -- "$OUT")" = "$OUT" ] || refuse', "true || refuse", REFUSES),
    _m("empty_execstart_accepted", "a unit whose ExecStart cannot be read is refused",
       C, '[ -n "$unit_script" ] || refuse', "true || refuse", REFUSES),
    _m("waiting_not_counted", "waiting requests are in flight too",
       C, "/^vllm:num_requests_(running|waiting)[{ ]/", "/^vllm:num_requests_running[{ ]/",
       REFUSES),
    _m("ready_s_unvalidated", "READY_S is a whole number of seconds, checked before the stop",
       C, "[[ $READY_S =~ ^[1-9][0-9]*$ ]] || refuse", "true || refuse", REFUSES),
    _m("parity_set_unchecked", "every parity clip is in the cache before the stop",
       C, 'parity_ready=$("$PY" "$REPO/models/marlin2b/measure/parity.py" --check --cache '
          '"$CORPUS_CACHE" 2>&1) || {', "parity_ready=$(true) || {", REFUSES),
    _m("restore_ignores_the_image", "restored means the same image",
       C, ' && [ "$post_image" = "$pre_image" ]; then', "; then", RESTORES),
    _m("restore_ignores_health", "restored means healthy",
       C, '  if [ "$healthy" = yes ] && [ "$post_args"', '  if [ "$post_args"', RESTORES),
    _m("restore_under_set_e", "a failed restore step still reaches the report",
       C, "  set +e                          # load-bearing: a failed step must not skip the "
          "report\n", "", RESTORES),
    _m("candidate_started_over_the_last_one", "each start stops what holds the name first",
       C, "  stop_candidate\n  # every serve.sh input pinned here",
       "  # every serve.sh input pinned here", RESTORES),
    _m("port_from_the_environment", "the candidate serves on 127.0.0.1:8000, whatever PORT says",
       C, "  PORT=8000 GPU=0 WEIGHTS=$NVME/marlin2b", "  GPU=0 WEIGHTS=$NVME/marlin2b", RESTORES),
    _m("weights_from_the_environment", "the candidate serves the unit's weights",
       C, "GPU=0 WEIGHTS=$NVME/marlin2b VLLM_LOGGING_LEVEL", "GPU=0 VLLM_LOGGING_LEVEL", RESTORES),
    _m("media_root_from_the_environment", "no media root is mounted for inline media",
       C, "VLLM_LOGGING_LEVEL=INFO PROCESSING_CACHE_DIR= \\\n", "VLLM_LOGGING_LEVEL=INFO \\\n",
       RESTORES),
    _m("bytecode_written_to_the_checkout", "nothing is written into the checkout",
       C, "export CORPUS_CACHE PYTHONDONTWRITEBYTECODE=1", "export CORPUS_CACHE", RECORDS),
    _m("start_args_unrecorded", "every start records the candidate container's own args",
       C, """  { echo "args=$(docker inspect --format '{{json .Args}}' "$CONTAINER" 2>/dev/null)"\n""",
       "  {\n", RECORDS),
    _m("engine_state_ignored", "the sweep labels the state it is given",
       S, '--engine-state "${ENGINE_STATE:-warm}"', "--engine-state warm", LABEL),
    _m("restore_trap_removed", "the engine found is restored on exit",
       C, "trap restore EXIT\n", "", RESTORES),
    _m("restore_skipped_on_failure", "a failed run restores too",
       C, "  local status=$?\n", '  local status=$?\n  [ "$status" = 0 ] || exit "$status"\n',
       RESTORES),
    _m("restore_ignores_the_args", "restored means the same args and image, healthy",
       C, '  if [ "$healthy" = yes ] && [ "$post_args" = "$pre_args" ] && '
          '[ "$post_image" = "$pre_image" ]; then',
       '  if [ "$healthy" = yes ]; then', RESTORES),
    _m("partof_units_left_stopped", "the units stopped with the engine start again",
       C, '  systemctl start "$UNIT" "${active[@]}"', '  systemctl start "$UNIT"', RESTORES),
    _m("levels_share_one_engine", "every level starts on a fresh engine",
       C, '  start_candidate "c$c"\n', '  [ "$c" = 1 ] && start_candidate "c$c"\n', RESTORES),
    _m("levels_labelled_warm", "a level on a fresh engine is labelled restarted",
       C, "LEVELS=$c ENGINE_STATE=restarted", "LEVELS=$c ENGINE_STATE=warm", RESTORES),
    _m("in_flight_check_dropped", "requests in flight refuse the run",
       C, '[ "$inflight" = 0 ] || refuse', "true || refuse", REFUSES),
    _m("unreadable_metrics_taken_as_idle", "metrics it cannot read refuse the run",
       C, '[ -n "$inflight" ] || refuse', "inflight=${inflight:-0}; true || refuse", REFUSES),
    _m("free_form_flag_accepted", "no argument is passed to the engine",
       C, "[ $# -eq 0 ] || refuse", "true || refuse", REFUSES),
    _m("unlisted_candidate_runs", "only the closed table's candidates run",
       C, '  *) refuse "CANDIDATE must be one of e0 e1 e3',
       '  *) flags=() ;; never) refuse "CANDIDATE must be one of e0 e1 e3', REFUSES),
    _m("restart_consent_dropped", "a restart needs W4_ENGINE_RESTART_OK=1",
       C, '[ "${W4_ENGINE_RESTART_OK:-}" = 1 ] || refuse', "true || refuse", REFUSES),
    _m("unit_tree_accepted", "the unit's installed tree is never the measurement checkout",
       C, '[ "$(realpath -m -- "$serve")" != "$(realpath -m -- "$unit_script")" ] \\\n  || refuse',
       "true \\\n  || refuse", REFUSES),
    _m("writes_outside_w4", "it writes only under $NVME/w4-*",
       C, 'case "$OUT" in "$NVME"/w4-*) ;;', 'case "$OUT" in /*) ;;', REFUSES),
    _m("candidate_corpus_not_verified", "the corpus must verify before anything is stopped",
       C, 'verified=$("$PY" "$REPO/models/marlin2b/corpus/build.py" verify 2>&1) || {',
       "verified=$(true) || {", REFUSES),
    _m("no_container_not_refused", "no engine container is a refusal, not a run",
       C, '  || refuse "no container $CONTAINER:', '  || true "no container $CONTAINER:', REFUSES),
    _m("checkout_not_checked", "a checkout without the measurement scripts is refused",
       C, '  test -f "$REPO/models/marlin2b/$f" || refuse', "  true || refuse", REFUSES),
    _m("engine_log_capture_dropped", "each cell keeps the engine's own error lines",
       C, '    | grep -E -i "$ERRORS" > "$OUT/engine-errors-$1.log" || true',
       '    | grep -E -i "^$" > "$OUT/engine-errors-$1.log" || true', RECORDS),
    _m("start_to_ready_unrecorded", "every engine start records its start-to-ready seconds",
       C, '  echo "start=$1 start_to_ready_s=$ready_s"', '  echo "start=$1"', RECORDS),
)

MUTANTS: tuple[Mutant, ...] = PY_MUTANTS + WORKER_MUTANTS + SCRIPT_MUTANTS
_PRISTINE: dict[str, Result] = {}


def _check_copy(check, mutant: Mutant | None) -> Result:
    with tempfile.TemporaryDirectory(prefix="w4-script-") as tmp:
        root = pathlib.Path(tmp) / "repo"
        _repo_copy(root)
        if mutant is not None:
            target = root / "models" / mutant.file
            source = target.read_text()
            found = source.count(mutant.old)
            if found != mutant.occurrences:
                return Result(Outcome.misdeclared, f"anchor appears {found} times in "
                                                   f"{mutant.file}: {mutant.old[:60]!r}")
            target.write_text(source.replace(mutant.old, mutant.new, 1))
        (pathlib.Path(tmp) / "work").mkdir()
        return shared.assertion_kill(check, root, pathlib.Path(tmp) / "work")


def run_script_mutant(mutant: Mutant) -> Result:
    """The named case's check on a copy: it must pass unmutated, then fail mutated."""
    case = mutant.cases[0]
    if case not in _PRISTINE:
        _PRISTINE[case] = _check_copy(SCRIPT_CHECKS[case], None)
    if _PRISTINE[case].outcome is not Outcome.survived:
        return Result(Outcome.broken_runner,
                      f"pristine copy fails its own check: {_PRISTINE[case].detail}")
    return _check_copy(SCRIPT_CHECKS[case], mutant)


def run(mutant: Mutant) -> Result:
    if mutant.file.startswith("worker/"):
        return shared.run_mutant(mutant, WORKER)
    if mutant.file.endswith(".py"):
        return shared.run_mutant(mutant, MEASURE)
    return run_script_mutant(mutant)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="run W4's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:44s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over {len({c for m in MUTANTS for c in m.cases})} "
              f"named cases")
        return 0
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    bad = []
    for mutant in chosen:
        result = run(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}", flush=True)
        if not result.killed:
            bad.append(mutant.name)
    print(f"\n{len(chosen) - len(bad)}/{len(chosen)} killed" + (f"; not killed: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
