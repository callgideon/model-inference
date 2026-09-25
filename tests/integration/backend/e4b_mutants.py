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


def _compile_python_only(source, filename, mode, *args, **kwargs):
    """The runner's "a mutant that does not compile is broken" rule is a Python rule; E4B also
    mutates the Markdown its cases hold to the code (tests/i's same track-local override)."""
    if str(filename).endswith(".py"):
        return compile(source, filename, mode, *args, **kwargs)
    return None


shared.compile = _compile_python_only

# What the E4B cases read, relative to the repository root. Directories are copied whole.
COPIED = ("tests/integration", "models/marlin2b", "apps/infrx-api/infrx",
          "apps/infrx-api/deploy", "apps/infrx-api/uv.lock", "apps/app/supabase/migrations",
          "infra", "research/plan/tasks.json",
          "research/plan/evidence/w/box/inventory-20260923T0319Z.txt",
          "research/plan/evidence/e/E4B-endpoint.md", "research/plan/evidence/e/E4B-release-decision.md",
          "apps/infrx-api/client_example.py")
SUITES = ("tests/integration/backend/test_certify.py",
          "tests/integration/backend/test_endpoint_doc.py")


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
PIN = "test_e4b_the_config_pin_names_every_setting_that_moved_past_its_evidence"
INVENTORY = "test_e4b_the_deployed_engine_is_judged_from_the_box_inventory"
APPS = "test_e4b_app_and_lab_servers_of_this_repository_fail_the_preconditions"
RUNG = "test_e4b_an_envelope_rung_judges_the_duration_cap_apart_from_its_failures"
CLIMB = "test_e4b_the_supported_rate_is_the_highest_rung_climbing_from_the_lowest"
SOAK = "test_e4b_the_soak_judges_memory_the_reconciler_and_latency_from_its_samples"
OVERLOAD = "test_e4b_overload_refusals_are_429_with_retry_guidance_and_never_5xx"
SCRAPE = "test_e4b_scrape_reads_the_series_the_soak_judges"
CELLS = "test_e4b_the_load_cells_run_the_declared_shapes_and_pend_where_they_cannot_judge"
CRASH = "test_e4b_a_runner_error_is_a_recorded_failure_and_the_report_is_still_written"
IDENTITY = "test_e4b_a_report_counts_for_one_clean_known_tree_or_it_fails"
NOGIT = "test_e4b_a_host_without_git_writes_a_report_that_fails_its_identity"
SERVED = "test_e4b_the_box_report_is_tied_to_the_build_the_gateway_serves"
UNANSWERED = "test_e4b_an_unanswered_attempt_is_a_failure_whatever_its_cause"
LABELS = "test_e4b_only_a_box_run_with_its_preconditions_met_is_a_measurement"
RECORD = "test_e4b_the_declared_settings_are_the_serving_record_read_never_typed"
RULES = "test_e4b_each_stated_client_rule_holds_one_assertion_each"
EXIT = "test_e4b_the_suite_halves_carry_pytests_own_exit_code"
BOX_ARGS = "test_e4b_a_box_run_names_its_release_and_reads_its_metrics"
TREE = "test_e4b_a_checkout_without_git_is_the_named_release_and_the_report_says_so"
CAP_SOURCE = "test_e4b_the_deployed_cap_and_its_refusal_are_the_media_layers"
PARITY_CAP = "test_e4b_parity_pairs_the_clips_within_the_cap_and_asks_admission_for_the_rest"
ADMISSION = "test_e4b_admission_is_asked_as_bench_asks_and_only_its_code_and_param_are_kept"
DATASET_CAP = "test_e4b_the_dataset_drill_schedules_only_clips_within_the_deployed_cap"
CANCELLED = "test_e4b_an_item_the_interruption_cancelled_is_terminal_after_one_replay"
CAP_WIRING = "test_e4b_the_run_reads_the_deployed_cap_once_and_every_cell_judges_by_it"
RUN = "tests/integration/run.py"
RUNG_SIZE = "test_e4b_a_box_rung_is_sized_to_hold_enough_short_clips_for_its_ttft_p95"
TIMEOUTS = "test_e4b_each_client_run_is_bounded_by_its_own_schedule_never_a_flat_hour"
GENERATED = "test_e4b_the_endpoint_doc_is_what_the_code_generates"
KEEPS_LOG = "test_e4b_a_regeneration_keeps_the_verification_log"
ROUTES = "test_e4b_every_mounted_route_has_one_description_and_every_description_a_route"
CATALOGUE = "test_e4b_the_error_catalogue_is_complete_and_only_public"
EXAMPLES = "test_e4b_the_examples_call_only_mounted_routes_with_the_headers_the_contract_needs"
LINKS = "test_e4b_the_release_decision_links_resolve_to_files_and_sections"
STATUSES = "test_e4b_every_status_the_prose_cites_is_the_one_the_code_answers"
CAUSE_AUTH = "test_e4b_the_prose_names_the_cause_the_auth_and_the_headers_the_modules_implement"
SUCCESS = "test_e4b_every_success_status_the_prose_cites_is_the_one_its_route_answers"
MODEL = "test_e4b_the_model_table_says_whether_the_published_release_is_the_measured_pin"
D = "tests/integration/backend/endpoint_doc.py"
E1C_ARGV = "test_e1c_a_remote_cell_runs_under_its_own_profile_and_the_key_inventory"
E1C_BLOCKED = "test_e1c_a_remote_run_without_its_profile_or_inventory_is_blocked_never_pass"
E1C_VALIDITY = "test_e1c_a_rung_whose_bench_summary_is_not_valid_fails"
E1C_CELLS = "test_e1c_the_soak_and_overload_cells_fail_when_bench_calls_them_invalid"


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
       "status = run.backend_verdict(cases, exit_code)", "status = PASS", SPLIT),
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
       "if Decimal(str(after.reserved_total)) != Decimal(str(before.reserved_total)) + kept:",
       "if False:", LEDGER),
    # --- the resume drill, orchestration -----------------------------------------------
    _m("client_problems_ignored", "a client-side problem fails the drill on any target",
       "    if problems:\n        report.check(\"e4b.a.dataset-resume\", FAIL",
       "    if False:\n        report.check(\"e4b.a.dataset-resume\", FAIL", DATASET),
    _m("resume_exit_ignored", "a resumed run that failed fails the drill",
       '    if resumed["exit"] != 0:\n', "    if False:\n", DATASET),
    _m("engine_target_skips_to_ledger", "the engine target stops at the client half, pending on the box",
       '    if not target["metered"]:\n', "    if False:\n", DATASET),
    _m("missing_ledger_passes", "no ledger adapter is PENDING on the box, never PASS",
       'report.check("e4b.a.dataset-resume", PENDING,\n                     '
       '"client invariants hold; `infrx',
       'report.check("e4b.a.dataset-resume", PASS,\n                     '
       '"client invariants hold; `infrx', DATASET),
    # --- the protocol ------------------------------------------------------------------
    _m("criterion_drifts_from_protocol", "the runner applies the predeclared numbers",
       '"ttft_p95_short_s": 6.0,', '"ttft_p95_short_s": 8.0,', PROTOCOL),
    _m("criterion_dropped_from_the_runner", "a §5 row the runner does not apply is superseded",
       '    "soak_latency_drift": 1.5,\n}\n', "}\n", PROTOCOL),
    _m("superseded_criterion_revived", "a superseded §5 row is not applied",
       '    "soak_latency_drift": 1.5,\n}\n',
       '    "soak_latency_drift": 1.5,\n    "applied_cap_s": 72,\n}\n', PROTOCOL),
    _m("superseded_row_unmarked", "the superseded §5 row says so in place",
       "`MAX_VIDEO_SECONDS=72`. *Superseded by 5(c): the deployed cap* |",
       "`MAX_VIDEO_SECONDS=72` |", PROTOCOL, file="models/marlin2b/results/E4B-protocol.md"),
    _m("superseded_envelope_row_unmarked", "the superseded §4 envelope rule says so in place",
       "is refused. *Superseded by 5(c): the deployed cap (`MAX_VIDEO_SECONDS`) is the one "
       "bound, and a clip over it gets the typed refusal* |",
       "is refused |", PROTOCOL, file="models/marlin2b/results/E4B-protocol.md"),
    _m("matrix_drifts_from_protocol", "the local cell shapes are the predeclared ones",
       '"dataset": {"items": 12, "interrupt_after": 4, "rate": 4.0}},',
       '"dataset": {"items": 12, "interrupt_after": 6, "rate": 4.0}},', PROTOCOL),
    # --- E4B.b: the config pin ---------------------------------------------------------
    _m("moved_setting_accepted", "a setting that moved past its evidence fails the pin",
       "for name, (value, source) in pinned.items() if current.get(name) != value]",
       "for name, (value, source) in pinned.items() if False]", PIN),
    _m("published_digest_read_from_the_record", "the published release is read from G6B's record",
       '"published_engine_options_digest": published["engine_options_digest"],',
       '"published_engine_options_digest": record["engine_options_digest"],', PIN),
    _m("unread_box_passes", "a pin with the deployed engine unread is PENDING, never PASS",
       'report.check("e4b.b.config-pin", PENDING,', 'report.check("e4b.b.config-pin", PASS,',
       PIN),
    _m("image_pin_unchecked", "the deployed engine runs the pinned image",
       'if lines.get("image_equals_pin") != "yes":', "if False:", INVENTORY),
    _m("missing_flags_accepted", "every pinned flag runs on the deployed engine",
       "    if missing:\n", "    if False:\n", INVENTORY),
    _m("extra_flags_accepted", "nothing runs beyond the pinned flags",
       "    if extra:\n", "    if False:\n", INVENTORY),
    # --- E4B.b: preconditions ----------------------------------------------------------
    _m("any_next_server_counts", "only an App or Lab package's Next.js servers count",
       "if cwd is None or APP_OR_LAB.search(cwd) or any(APP_OR_LAB.search(part) for part in argv):",
       "if True:", APPS),
    _m("unreadable_cwd_skipped", "a Next.js server whose directory is unreadable is not stopped",
       "        except OSError:\n            cwd = None\n",
       "        except OSError:\n            continue\n", APPS),
    _m("package_path_in_cmdline_ignored", "an App/Lab is found by its command line too",
       "if cwd is None or APP_OR_LAB.search(cwd) or any(APP_OR_LAB.search(part) for part in argv):",
       "if cwd is None or APP_OR_LAB.search(cwd):", APPS),
    _m("only_this_checkout_counts", "an App/Lab of any checkout counts",
       'APP_OR_LAB = re.compile(r"(?:^|/)apps/(?:app|lab)(?:/|$)")',
       'APP_OR_LAB = re.compile(re.escape(str(harness.REPO_ROOT)) + r"/apps/(?:app|lab)(?:/|$)")',
       APPS),
    _m("a_shell_naming_next_counts", "a process is a Next.js server by what it runs",
       'is_next = head == "next-server" or (', 'is_next = "next-server" in " ".join(argv) or (',
       APPS),
    _m("window_consent_unchecked", "the box run needs the maintenance window's consent",
       'if os.environ.get("E4B_WINDOW_OK") != "1":', "if False:", APPS),
    _m("busy_engine_accepted", "the box run starts on an idle engine",
       "        if busy != 0:\n", "        if False:\n", APPS),
    # --- E4B.b: the envelope -----------------------------------------------------------
    _m("within_cap_refusal_accepted", "a clip within the applied cap is never refused",
       "verdict = decide.FAIL if admitted or refused else", "verdict = decide.FAIL if admitted else",
       RUNG),
    _m("admitted_long_clip_accepted", "a clip past the ceiling is refused at admission",
       "verdict = decide.FAIL if admitted or refused else", "verdict = decide.FAIL if refused else",
       RUNG),
    _m("over_ceiling_judged_as_failures", "the cap's attempts are no one's failures",
       "    return [row for row in rows if _duration(row, clips) <= cap_s]",
       "    return list(rows)", RUNG),
    _m("failure_rate_loosened", "platform failures stay under 1 %",
       'decide.PASS if rate < CRITERIA["max_failure_rate"] else decide.FAIL',
       "decide.PASS if rate < 1 else decide.FAIL", RUNG),
    _m("refusals_below_the_rate_accepted", "a refusal at an envelope rate fails the rung",
       'out.append(("rejections", decide.FAIL if refusals else decide.PASS,',
       'out.append(("rejections", decide.PASS,', RUNG),
    _m("tail_limit_ignored", "each tail meets its predeclared limit",
       "decide.PASS if tail <= limit else decide.FAIL", "decide.PASS", RUNG),
    _m("short_class_unfiltered", "the 6 s TTFT row is judged on clips <= 30 s only",
       '<= CRITERIA["short_clip_max_s"]', '<= 10 ** 6', RUNG),
    _m("short_class_any_resolution", "the 6 s TTFT row is judged on clips <= 720p only",
       '<= CRITERIA["short_clip_max_edge_px"])', '<= 10 ** 6)', RUNG),
    _m("climb_skips_a_failed_rung", "the envelope is contiguous from the lowest rung",
       "        if any(v != decide.PASS for v in core):\n            break",
       "        if any(v != decide.PASS for v in core):\n            continue", CLIMB),
    _m("other_rungs_cap_ignored", "a cap failure on any rung fails the envelope",
       'if row[0] not in ("duration_cap", "bench_validity")] + caps)',
       "] + [])", CLIMB),
    _m("unknown_is_pass", "an unjudged criterion pends; it never passes",
       "    if UNKNOWN in states:\n", "    if False:\n", CLIMB, SOAK),
    # --- E4B.b: the soak ---------------------------------------------------------------
    _m("memory_growth_limit_ignored", "memory growth stays within W4's limits",
       "decide.PASS if grew <= limit else decide.FAIL", "decide.PASS", SOAK),
    _m("unreconciled_end_accepted", "the store is reconciled at the end of the soak",
       "decide.PASS if ends == [0, 0] else decide.FAIL", "decide.PASS", SOAK),
    _m("latency_drift_ignored", "the last third's p50 stays within 1.5x the first's",
       'decide.PASS if late <= CRITERIA["soak_latency_drift"] * early',
       "decide.PASS if True", SOAK),
    _m("thin_thirds_judged", "a p50 per third needs 6 samples",
       "    if third < 6:\n", "    if third < 1:\n", SOAK),
    # --- E4B.b: overload ---------------------------------------------------------------
    _m("refusal_without_retry_after_accepted", "an overload refusal carries Retry-After",
       'if r.get("http_status") != 429 or not (r.get("retry_after") or 0) > 0',
       'if r.get("http_status") != 429', OVERLOAD),
    _m("refusal_code_unchecked", "an overload refusal names an overload code",
       '             or r.get("error_code") not in OVERLOAD_CODES]', "             ]", OVERLOAD),
    _m("overload_5xx_accepted", "overload is never a 5xx or a broken stream",
       "    if broken:\n", "    if False:\n", OVERLOAD),
    _m("unreached_limit_accepted", "a burst that admission never refuses is a defect",
       "    if not refused:\n", "    if False:\n", OVERLOAD),
    _m("gpu_total_read_as_used", "the soak judges the GPU's used memory",
       'total("infrx_gpu_memory_bytes", state="used")', 'total("infrx_gpu_memory_bytes")',
       SCRAPE),
    _m("engine_target_overload_run", "overload against an engine target is not run: it pends on the box",
       "    if not gateway:\n        report.check(\"e4b.b.overload\"",
       "    if False:\n        report.check(\"e4b.b.overload\"", CELLS),
    _m("soak_at_the_full_rate", "the box soak runs at the declared fraction of the envelope",
       'rate = soak.get("rate") or (supported * soak["rate_fraction"] if supported else None)',
       'rate = soak.get("rate") or supported', CELLS),
    _m("crashed_client_accepted", "a client run that did not finish cleanly fails its cell",
       '"client_exit", decide.PASS if code == 0 else decide.FAIL', '"client_exit", decide.PASS',
       CLIMB, CELLS),
    _m("settlement_not_awaited", "a debit that lands after the answer is waited for",
       "        if not problems or time.monotonic() >= end:", "        if True:", DATASET),
    _m("runner_error_escapes", "an unexpected error is recorded and the report still written",
       "        except Exception as crashed:", "        except KeyboardInterrupt as crashed:",
       CRASH),
    # --- review F1: one clean, known tree ----------------------------------------------
    _m("missing_sha_accepted", "a report with no SHA is evidence for no release",
       '        if not head.get("sha"):\n', "        if False:\n", IDENTITY),
    _m("dirty_tree_accepted", "a dirty tree at either end fails the release identity",
       'if head.get("dirty") is not False:', "if False:", IDENTITY),
    _m("unknown_state_accepted", "an unknown tree state is not a clean one",
       'if head.get("dirty") is not False:', 'if head.get("dirty"):', IDENTITY),
    _m("moved_tree_accepted", "the tree at the end is the tree at the start",
       'if start.get("sha") and end.get("sha") and start["sha"] != end["sha"]:', "if False:",
       IDENTITY),
    _m("identity_never_fails", "the identity check reaches the exit code",
       'self.check("release-identity", FAIL if problems else PASS,',
       'self.check("release-identity", PASS,', IDENTITY),
    _m("unknown_tree_recorded_clean", "git that cannot answer records an unknown tree, not a clean one",
       '            "dirty": None if status is None else bool(status)}\n',
       '            "dirty": bool(status)}\n', IDENTITY, file=RUN),
    # --- review F2: a host without git -------------------------------------------------
    _m("missing_git_crashes", "no git binary is an unknown tree, never a lost report",
       "        except (OSError, subprocess.SubprocessError):\n            return None\n",
       "        except subprocess.SubprocessError:\n            return None\n", NOGIT, file=RUN),
    _m("release_sha_unchecked", "a box report is for the release its operator names",
       "    if release_sha is not None and start.get(\"sha\") != release_sha:\n",
       "    if False:\n", NOGIT),
    _m("box_without_release_sha_accepted", "a box run names the release it certifies",
       "    if args.box and not args.release_sha:\n", "    if False:\n", BOX_ARGS),
    # --- review F3: the build the gateway serves ---------------------------------------
    _m("served_revision_unchecked", "the gateway serves the report's tree",
       "    elif not (head_sha and len(str(revision)) >= 7 and head_sha.startswith(str(revision))):\n",
       "    elif False:\n", SERVED),
    _m("missing_build_info_accepted", "an unknown served build is a failure",
       "    elif revision is None:\n", "    elif False:\n", SERVED),
    _m("gateway_image_unset_accepted", "the serving image is recorded",
       "    if not gateway_image:\n", "    if False:\n", SERVED),
    _m("release_image_mismatch_accepted", "the gateway runs the image built for the release",
       "    elif gateway_image and gateway_image != release_image:\n", "    elif False:\n",
       SERVED),
    _m("build_info_read_from_any_series", "the revision is infrx_build_info's",
       'if series == "infrx_build_info" and value == 1),', "if value == 1),", SCRAPE),
    _m("box_without_metrics_accepted", "a box run reads the gateway's build from /metrics",
       "    if args.box and not args.metrics_url:\n", "    if False:\n", BOX_ARGS),
    # --- review F4: every unanswered attempt is a failure ------------------------------
    _m("transport_failures_uncounted", "a timeout or a reset is a failure like a 5xx",
       '    failed = [r for r in rows if r.get("outcome") == "failed"]\n',
       "    failed = [r for r in rows if bench.is_platform_failure(r)]\n", UNANSWERED),
    _m("nothing_accepted_passes", "a cell that accepted nothing supports nothing",
       '    return ("answered", decide.PASS if accepted else decide.FAIL,',
       '    return ("answered", decide.PASS,', UNANSWERED),
    _m("climb_ignores_answers", "the envelope climb stops at a rung that answered nothing",
       'if name in ("failure_rate", "answered", "rejections", "client_exit")]',
       'if name in ("failure_rate", "rejections", "client_exit")]', UNANSWERED),
    _m("overload_resets_accepted", "a reset under overload is a failure, not a refusal",
       '              if (r.get("http_status") or 0) >= 500 or r.get("outcome") == "failed"]',
       '              if (r.get("http_status") or 0) >= 500 or bench.is_platform_failure(r)]',
       UNANSWERED),
    # --- review F5: labels --------------------------------------------------------------
    _m("local_run_labelled_measured", "the local target's numbers are the fake engine's",
       '"bench_target": "direct", "model": "marlin2b", "scale": scale, "label": FAKE,',
       '"bench_target": "direct", "model": "marlin2b", "scale": scale, "label": MEAS,', LABELS),
    _m("remote_run_measured_by_default", "a --target run is unverified until the box proves it",
       '            "label": UNVERIFIED, "namespace": None,', '            "label": MEAS, "namespace": None,',
       LABELS),
    _m("unready_box_measured", "a box run whose preconditions failed measures nothing",
       "    return MEAS if box and preconditions == PASS else UNVERIFIED",
       "    return MEAS if box else UNVERIFIED", LABELS),
    _m("label_never_decided", "the run labels its numbers after its preconditions",
       '            report.target["label"] = target["label"] = target_label(target, args.box, ready)\n',
       "", LABELS),
    _m("unserved_build_measured", "a box whose served build is not the release measures nothing",
       "                ready = ready if served == PASS else FAIL\n", "                ready = ready\n",
       LABELS),
    # --- review F6: the declared settings are the record's -----------------------------
    _m("published_digest_declared_as_the_placeholder", "the published digest is the record's",
       '        "published_engine_options_digest": (digest, "R76/R78: the serving revision every "',
       '        "published_engine_options_digest": ("sha256:" + "44" * 32, "R76/R78: the serving revision every "',
       RECORD),
    _m("published_image_declared_as_the_tag", "the published image is the record's",
       '        "published_runtime_image": (image, "R76/R78, as above"),',
       '        "published_runtime_image": ("vllm/vllm-openai:nightly", "R76/R78, as above"),', RECORD),
    _m("seqs_declared_as_a_literal", "the declared engine concurrency is the record's",
       '        "engine_max_num_seqs": (seqs, "W3 serving-version.json settings"),',
       '        "engine_max_num_seqs": ("32", "W3 serving-version.json settings"),', RECORD),
    _m("digest_declared_as_a_literal", "the declared digest follows the record",
       '        "engine_options_digest": (digest, "W3 serving-version.json (W4-ecacd50 phase A adopted "',
       '        "engine_options_digest": ("sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147", "W3 serving-version.json (W4-ecacd50 phase A adopted "',
       RECORD),
    _m("encoder_budget_declared_as_a_literal", "the declared encoder budget is the record's flags'",
       "        \"encoder_budget_tokens\": (encoder_budget(served_flags(record)),",
       "        \"encoder_budget_tokens\": (16384,", RECORD),
    _m("image_read_from_the_record_not_serve_sh", "serve.sh is held against the record",
       '            "runtime_image": pins["image"],', '            "runtime_image": record["runtime_image"]["ref"],',
       RECORD),
    _m("seqs_read_from_the_record_not_serve_sh", "serve.sh's concurrency is held against the record",
       '            "engine_max_num_seqs": pins["seqs"],',
       '            "engine_max_num_seqs": record["settings"]["ENGINE_MAX_NUM_SEQS"],', RECORD),
    # --- E1C wiring: per-cell profile + key inventory, the validity gate ---------------
    _m("profile_dropped_from_argv", "a remote cell runs under its own profile and the inventory",
       '    if target["kind"] != "local":\n        argv += ["--profile"',
       '    if False:\n        argv += ["--profile"', E1C_ARGV),
    _m("profile_not_stamped_per_cell", "the stamped profile describes this cell's arrival",
       '    profile["measurement"].update(arrival="open-loop", rate_per_s=rate)\n', "", E1C_ARGV),
    _m("profile_bounds_loosened", "the coordinator's bounds reach bench unchanged",
       '    path = workdir / f"{name}-profile.json"\n',
       '    profile["bounds"]["max_requests"] = 10 ** 9\n    path = workdir / f"{name}-profile.json"\n',
       E1C_ARGV),
    _m("unprofiled_remote_cells_run", "a remote cell without profile + inventory never starts",
       '        if why := profile_blocked(target):\n            for check_id',
       '        if False:\n            for check_id', E1C_BLOCKED),
    _m("bench_argv_starts_unprofiled", "bench_argv refuses a remote cell without its files",
       '    if why := profile_blocked(target):\n        raise Blocked(why)',
       '    if False:\n        raise Blocked(why)', E1C_BLOCKED),
    _m("profile_schema_unchecked", "only an infrx.run-profile/1 file unblocks a cell",
       "    if schema != RUN_PROFILE_SCHEMA:\n", "    if False:\n", E1C_BLOCKED),
    _m("validity_gate_removed", "a rung bench did not call VALID never passes",
       "    out += [bench_validity(summary, local), failures(counted), answered(counted)]",
       "    out += [failures(counted), answered(counted)]", E1C_VALIDITY, CELLS),
    _m("validity_invalid_is_pass", "validity != VALID fails",
       '    if validity.get("verdict") == "VALID":\n', "    if True:\n", E1C_VALIDITY, E1C_CELLS),
    _m("remote_unprofiled_excused", "only a local cell's `unprofiled` goes unjudged",
       "if not (local and r == bench.UNPROFILED)]", "if not r == bench.UNPROFILED]",
       E1C_VALIDITY),
    _m("missing_summary_excused", "a remote cell with no summary fails",
       "    if reasons or not local:\n", "    if reasons:\n", E1C_VALIDITY),
    _m("other_rungs_validity_ignored", "an INVALID rung fails the envelope, not only the chosen",
       'if row[0] in ("duration_cap", "bench_validity")]', 'if row[0] == "duration_cap"]',
       E1C_VALIDITY),
    _m("soak_validity_unwired", "the soak judges its own bench validity",
       '[client_exit(done["exit"]), bench_validity(\n                                     bench_summary(workdir / "soak.jsonl"), local)]',
       '[client_exit(done["exit"])]', E1C_CELLS),
    _m("overload_validity_unwired", "overload judges its own bench validity",
       "    if validity[1] == decide.FAIL:\n", "    if False:\n", E1C_CELLS),
    # --- review F8: one assertion per stated rule -------------------------------------
    _m("retries_hide_refusals", "the client never retries: a retry may not hide a refusal",
       '"--max-tokens", BENCH_MAX_TOKENS, "--retries", "0",',
       '"--max-tokens", BENCH_MAX_TOKENS, "--retries", "3",', RULES),
    _m("box_runs_the_fast_subset", "the box scale runs the full corpus",
       '"--subset", "full" if target["scale"] == "box" else "fast",', '"--subset", "fast",',
       RULES),
    _m("quarantined_item_resent_ok", "a quarantined 4xx item is terminal and never re-sent",
       '    terminal = {row["item_key"] for row in first if bench.is_terminal(row)}',
       '    terminal = {row["item_key"] for row in first if row.get("outcome") == "accepted"}',
       RULES),
    _m("accepted_without_id_ok", "an accepted item with no Inference-Id cannot be reconciled",
       "if len(seen) != 1 or None in seen)", "if len(seen) != 1)", RULES),
    _m("signalled_exit_unchecked", "a signal the client answered with exit 0 interrupted nothing",
       'first_interrupted=first["signalled"] and first["exit"] == 130)',
       'first_interrupted=first["signalled"])', RULES),
    _m("unreadable_engine_is_idle", "an engine whose metrics cannot be read is not idle",
       '        busy = None if engine is None or engine["running"] is None \\\n',
       '        busy = 0 if engine is None or engine["running"] is None \\\n', RULES),
    # --- review N1: pytest's own exit code ---------------------------------------------
    _m("exit_code_ignored", "a backend run that exited abnormally fails both halves",
       "    status = run.backend_verdict(cases, exit_code)", "    status = run.backend_verdict(cases, 0)",
       EXIT),
    _m("exit_one_never_attributed", "exit 1 with a failed case is that case's half's",
       '    return 0 if code == 1 and cases["failed"] else code', "    return code", EXIT),
    _m("unexplained_exit_one_accepted", "exit 1 with no failed case is not explained",
       '    return 0 if code == 1 and cases["failed"] else code',
       '    return 0 if code == 1 else code', EXIT),
    _m("unrun_backend_accepted", "a backend suite that never ran fails the halves",
       '        down.append("backend=not run")', "        pass", EXIT),
    # --- verifier V2: serve.sh's encoder budget ----------------------------------------
    _m("serve_sh_budget_ignored", "a raised encoder budget in serve.sh fails the pin",
       '            "encoder_budget": budget}', '            "encoder_budget": 16384}', RECORD),
    _m("non_literal_budget_accepted", "a budget serve.sh gives as a variable is unknown",
       "    budget = None if None in literal else max([16384, *(int(m.group(1)) for m in literal)])",
       "    budget = max([16384, *(int(m.group(1)) for m in literal if m)])", RECORD),
    _m("commented_budget_counted", "a comment naming the flag is not a budget",
       '               if not line.lstrip().startswith("#")\n', "", RECORD),
    # --- verifier V5: one assertion each -----------------------------------------------
    _m("release_sha_prefix_accepted", "the release is the full commit id, not a prefix",
       '    if release_sha is not None and start.get("sha") != release_sha:\n',
       '    if release_sha is not None and not str(start.get("sha")).startswith(release_sha):\n',
       NOGIT),
    _m("short_revision_accepted", "a served revision shorter than 7 characters identifies nothing",
       "len(str(revision)) >= 7", "len(str(revision)) >= 1", SERVED),
    _m("app_path_substring_matches", "only an apps/app or apps/lab path component is a package",
       'APP_OR_LAB = re.compile(r"(?:^|/)apps/(?:app|lab)(?:/|$)")',
       'APP_OR_LAB = re.compile(r"apps/(?:app|lab)")', APPS),
    # --- CERTIFY-TREE: a checkout without git is the named release ---------------------
    _m("no_git_tree_unnamed", "a checkout git cannot read is certified as --release-sha",
       "    if release_sha and missing:\n", "    if False:\n", TREE),
    _m("git_overridden_by_release_sha", "git that runs is never overridden by --release-sha",
       "    if release_sha and missing:\n", "    if release_sha:\n", TREE),
    _m("no_git_binary_ignored", "no git binary is what names the tree (no git)",
       '    missing = ("no git" if shutil.which("git") is None\n',
       '    missing = ("no git" if False\n', TREE),
    _m("no_dot_git_ignored", "a checkout with no .git is not git's to read",
       '               else "no .git" if not (harness.REPO_ROOT / ".git").exists() else None)\n',
       "               else None)\n", TREE),
    _m("named_tree_recorded_clean", "a tree named by --release-sha is of unknown state",
       '        return {"sha": release_sha, "dirty": None, "source": f"--release-sha ({missing})"}\n',
       '        return {"sha": release_sha, "dirty": False, "source": f"--release-sha ({missing})"}\n',
       TREE),
    _m("named_tree_source_dropped", "the report says the SHA is --release-sha's, not git's",
       '        return {"sha": release_sha, "dirty": None, "source": f"--release-sha ({missing})"}\n',
       '        return {"sha": release_sha, "dirty": None}\n', TREE),
    _m("start_sample_ignores_release", "the start sample is the named release too",
       "        self.head = release_head(release_sha) or self.head\n", "", TREE),
    _m("end_sample_ignores_release", "the end sample is the named release too",
       "            self.head_end = release_head(self.release_sha) or run.git_head()\n",
       "            self.head_end = run.git_head()\n", TREE),
    _m("served_detail_hides_the_source", "the served-build cell names where its tree came from",
       "f\"({report.head.get('source', 'git')}), the gateway from \"",
       "f\"(git), the gateway from \"", TREE),
    _m("worker_gauge_unread", "the worker's build is judged like the gateway's",
       '    for process, scraped in (("gateway", gateway), ("worker", worker)):\n',
       '    for process, scraped in (("gateway", gateway),):\n', SERVED),
    _m("worker_read_at_the_gateway_url", "the worker's build is read from the worker's /metrics",
       "                                     scrape(worker_metrics_url))\n",
       "                                     scrape(metrics_url))\n", SERVED),
    _m("page_owner_unchecked", "a page is judged only as the process whose label it carries",
       "        elif scraped.get(\"process\") != process:\n", "        elif False:\n", SERVED),
    _m("build_process_unread", "scrape reads whose build the page names",
       '"process": build.get("process")}', '"process": None}', SCRAPE),
    _m("box_without_worker_metrics_accepted", "a box run reads the worker's build from /metrics",
       "    if args.box and not args.worker_metrics_url:\n", "    if False:\n", BOX_ARGS),
    # --- CERTIFY-TREE item 3: the deployed duration cap ---------------------------------
    _m("cap_read_from_the_tree", "the cap is the deployed MAX_VIDEO_SECONDS, not the tree's",
       "    return pilot_from_env().max_video_seconds\n", "    return 120.0\n", CAP_SOURCE),
    _m("over_cap_code_typed_wrong", "the expected refusal is the M layer's code",
       '"code": "unsupported_media", "param": "messages"}',
       '"code": "invalid_request", "param": "messages"}', CAP_SOURCE),
    _m("over_cap_param_typed_wrong", "the expected refusal is the M layer's param",
       '"code": "unsupported_media", "param": "messages"}',
       '"code": "unsupported_media", "param": None}', CAP_SOURCE),
    _m("cap_unrecorded_in_the_target", "the report's target records the deployed cap",
       '            cap_s = report.target["max_video_seconds"] = deployed_cap_s()\n',
       "            cap_s = deployed_cap_s()\n", CAP_WIRING),
    _m("cap_unrecorded_in_the_pin", "the config pin records the deployed cap",
       'measured = {"current": current_config(), "deployed_max_video_seconds": cap_s}',
       'measured = {"current": current_config()}', CAP_WIRING),
    _m("parity_judged_at_the_tree_cap", "parity judges by the deployed cap",
       '                     local=target["kind"] == "local", cap_s=cap_s,\n',
       '                     local=target["kind"] == "local", cap_s=120.0,\n', CAP_WIRING),
    _m("dataset_judged_at_the_tree_cap", "the dataset drill judges by the deployed cap",
       "        dataset_check(report, target, workdir, cap_s)\n",
       "        dataset_check(report, target, workdir, 120.0)\n", CAP_WIRING),
    _m("load_cells_judged_at_the_tree_cap", "the load cells judge by the deployed cap",
       "        load_cells(report, target, workdir, args.metrics_url, cap_s)\n",
       "        load_cells(report, target, workdir, args.metrics_url, 120.0)\n", CAP_WIRING),
    _m("engine_target_asked_for_admission", "only a target with admission is asked",
       'gateway=target if target["bench_target"] == "gateway" else None)',
       "gateway=target)", CAP_WIRING),
    _m("gateway_never_asked", "a gateway target is asked to refuse the over-cap clips",
       'gateway=target if target["bench_target"] == "gateway" else None)',
       "gateway=None)", CAP_WIRING),
    _m("parity_pairs_over_cap_clips", "parity pairs only the clips within the cap",
       "    verdict, why = decide.parity_verdict(within(decide.jsonl(candidate)),\n"
       "                                         within(decide.jsonl(base)))\n",
       "    verdict, why = decide.parity_verdict(decide.jsonl(candidate), decide.jsonl(base))\n",
       PARITY_CAP),
    _m("over_cap_read_from_the_outcome", "a within-cap refusal is paired, and fails",
       '    within = (lambda rows: [row for row in rows if row["clip_id"] not in over])\n',
       '    within = (lambda rows: [row for row in rows if row.get("outcome") == "accepted"])\n',
       PARITY_CAP),
    _m("parity_cap_exclusive", "a parity clip at the cap is within it",
       'if clips[clip]["duration_s"] > cap_s)', 'if clips[clip]["duration_s"] >= cap_s)',
       PARITY_CAP),
    _m("over_cap_unasked", "each over-cap clip is asked of the gateway",
       "                   for clip in over} if gateway else {}\n",
       "                   for clip in ()} if gateway else {}\n", PARITY_CAP),
    _m("over_cap_acceptance_accepted", "an over-cap clip the gateway answers fails parity",
       "    if wrong or busy:\n        status = FAIL\n", "    if busy:\n        status = FAIL\n",
       PARITY_CAP),
    _m("capacity_twice_passes", "a clip refused for capacity twice was never judged: FAIL",
       "    if wrong or busy:\n        status = FAIL\n", "    if wrong:\n        status = FAIL\n",
       PARITY_CAP),
    _m("capacity_listed_as_not_refused", "capacity twice is listed apart, not as unrefused",
       '                   if answer != OVER_CAP and answer["http_status"] != 429)',
       "                   if answer != OVER_CAP)", PARITY_CAP),
    _m("over_cap_refusal_untyped", "an over-cap refusal must be the typed one",
       'if answer != OVER_CAP and answer["http_status"] != 429)',
       'if answer["http_status"] != 400 and answer["http_status"] != 429)', PARITY_CAP),
    _m("v_parity_param_ignored", "the over-cap refusal is admission's, param included",
       'if answer != OVER_CAP and answer["http_status"] != 429)',
       'if (answer["http_status"], answer["code"]) != (OVER_CAP["http_status"], '
       'OVER_CAP["code"]) and answer["http_status"] != 429)', PARITY_CAP),
    _m("engine_target_over_cap_passes", "an engine target cannot judge the over-cap clips",
       "    elif over and not gateway and status == PASS:\n", "    elif False:\n", PARITY_CAP),
    _m("acceptance_read_as_a_refusal", "an admitted over-cap clip is recorded as admitted",
       '            return {"http_status": answer.status, "code": None, "param": None}\n',
       '            return {"http_status": 400, "code": None, "param": None}\n', ADMISSION),
    _m("param_assumed", "the param is the gateway's, never assumed",
       '"param": bench.allow(error.get("param"), bench.CODE_OK, key)}',
       '"param": "messages"}', ADMISSION),
    _m("code_unallowlisted", "the code passes bench's allowlist, never the server's text",
       '"code": bench.allow(error.get("code"), bench.CODE_OK, key),',
       '"code": error.get("code"),', ADMISSION),
    _m("key_not_sent", "admission is asked with the client's key",
       '"authorization": f"Bearer {key}"}', '"authorization": "Bearer "}', ADMISSION),
    _m("cap_exclusive_at_the_boundary", "a clip at the cap is within it",
       "    return [row for row in rows if _duration(row, clips) <= cap_s]\n",
       "    return [row for row in rows if _duration(row, clips) < cap_s]\n", RUNG),
    _m("untyped_over_cap_refusal_accepted", "an over-cap clip needs the typed code, not a 400",
       '"rejected", OVER_CAP["http_status"], OVER_CAP["code"])',
       '"rejected", OVER_CAP["http_status"], row.get("error_code"))', RUNG),
    _m("v_soak_judges_over_cap", "the soak judges its failures within the cap",
       "    counted = judged(rows, clips, cap_s)\n    out = [failures(counted), answered(counted)]\n",
       "    counted = rows\n    out = [failures(counted), answered(counted)]\n", SOAK),
    _m("soak_cap_breach_unreported", "a gateway soak reports a breach of the cap",
       "    if gateway and cap[1] == decide.FAIL:\n", "    if False:\n", SOAK),
    _m("soak_reports_a_cap_pass", "the soak never reports the cap as passing",
       "    if gateway and cap[1] == decide.FAIL:\n", "    if gateway:\n", SOAK),
    _m("soak_gateway_unwired", "the load cells tell the soak it runs against a gateway",
       '                                 cap_s, gateway) + [client_exit(done["exit"]), bench_validity(',
       '                                 cap_s) + [client_exit(done["exit"]), bench_validity(', CELLS),
    _m("v_soak_cut_at_an_hour", "a bench run is bounded by its own schedule, never a flat hour",
       "    return run.shell(argv, cwd=harness.REPO_ROOT, env=env, timeout=client_timeout_s(argv))",
       "    return run.shell(argv, cwd=harness.REPO_ROOT, env=env, timeout=3600.0)", TIMEOUTS),
    _m("schedule_ignores_the_rate", "a run's schedule is its requests over its rate",
       "    return requests / rate + CLIENT_MARGIN_S\n", "    return requests + CLIENT_MARGIN_S\n",
       TIMEOUTS),
    _m("no_margin_after_the_schedule", "the last requests get bench's own timeout to finish",
       "    return requests / rate + CLIENT_MARGIN_S\n", "    return requests / rate\n", TIMEOUTS),
    _m("v_rung_unsized", "a box rung is sized for its TTFT p95",
       '    requests = rung_requests(shape["envelope"]["requests"], "full") if target["scale"] == "box" \\\n',
       '    requests = shape["envelope"]["requests"] if target["scale"] == "box" \\\n', CELLS),
    _m("tiny_rung_sized", "the tiny scale keeps its declared rung",
       'if target["scale"] == "box" \\\n        else shape["envelope"]["requests"]',
       'if True \\\n        else shape["envelope"]["requests"]', CELLS),
    _m("sizing_counts_every_clip", "the rung is sized on short clips, not on every clip",
       '    while sum(short_clip(item["clip"]) for item in bench.build_schedule(',
       '    while sum(True for item in bench.build_schedule(', RUNG_SIZE),
    _m("sizing_below_the_declared_rung", "a rung never sends fewer than its declared requests",
       "    n = declared\n    while sum(", "    n = 1\n    while sum(", RUNG_SIZE),
    _m("v_capacity_refusal_judged_as_the_cap", "a clip refused for capacity is not the cap's",
       "    judged_over = [r for r in over if not overloaded(r)]\n", "    judged_over = over\n",
       RUNG),
    _m("v_any_over_cap_refusal_excused", "only a capacity refusal leaves the cap unjudged",
       '    return row.get("http_status") == 429 and row.get("error_code") in OVERLOAD_CODES\n',
       '    return row.get("outcome") == "rejected"\n', RUNG),
    _m("capacity_refusals_unrecorded", "the cap's detail names the clips refused for capacity",
       '        "over_cap_refused_for_capacity": sorted({r["clip_id"] for r in over if overloaded(r)}),',
       '        "over_cap_refused_for_capacity": [],', RUNG),
    _m("admission_not_retried_after_capacity", "a 429 from admission is asked once more",
       "            if refused.code == 429 and attempt == 1:\n", "            if False:\n",
       ADMISSION),
    _m("tail_quoted_without_its_p50", "a latency row prints its p50 and accepted count",
       'f"p95 {tail}, p50 {round(statistics.median(values), 3) if values else None} "',
       'f"p95 {tail} "', RUNG),
    _m("overload_counts_capped_clips", "the cap's refusals are not overload's",
       "    rows = judged(rows, clips, cap_s)\n", "", OVERLOAD),
    _m("dataset_corpus_unfiltered", "the drill schedules only clips within the cap",
       '    data["clips"] = [clip for clip in data["clips"] '
       'if clip["derived"]["duration_s"] <= cap_s]\n',
       "", DATASET_CAP),
    _m("dataset_corpus_cap_exclusive", "a corpus clip at the cap is scheduled",
       'clip["derived"]["duration_s"] <= cap_s]', 'clip["derived"]["duration_s"] < cap_s]',
       DATASET_CAP),
    _m("dataset_corpus_cache_moved", "the filtered corpus reads the corpus's own cache",
       '    data["cache_root_default"] = str(corpus_cache())\n', "", DATASET_CAP),
    _m("first_run_on_the_full_corpus", "the first run reads the within-cap corpus",
       'requests=shape["items"], dataset_version=version, corpus=corpus)',
       'requests=shape["items"], dataset_version=version)', DATASET_CAP),
    _m("resume_on_the_full_corpus", "the resume reads the same corpus as the first run",
       "                                corpus=corpus), env)",
       "                                ), env)",
       DATASET_CAP),
    _m("v_capped_second_run_only", "a first-run refusal as over the cap fails the drill too",
       'for row in rows_first + rows_second if refused_over_cap(row)})',
       'for row in rows_second if refused_over_cap(row)})', DATASET_CAP),
    _m("capped_items_accepted", "an item refused as over the cap fails the drill",
       "    if capped:\n", "    if False:\n", DATASET_CAP),
    # --- CERTIFY-TREE item 5: R106, a cancelled job's replay is terminal ---------------
    _m("cancelled_items_uncounted", "the drill counts the items the interruption cancelled",
       'if row.get("outcome") == bench.CANCELLED_REPLAY})', 'if row.get("outcome") == "cancelled"})',
       CANCELLED),
    _m("v_platform_cancel_counted_as_the_interruption",
       "only the client's own tear is cancelled by the interruption",
       "    torn = [item for item in cancelled if item not in firsts or torn_by_the_client(firsts[item])]\n",
       "    torn = list(cancelled)\n", CANCELLED),
    _m("v_unrowed_item_called_the_platforms", "an item with no first-run row is the client's tear",
       "if item not in firsts or torn_by_the_client(firsts[item])]",
       "if torn_by_the_client(firsts.get(item, {}))]", CANCELLED),
    _m("sop_hides_the_platform_count", "the SOP line states the platform's count",
       '            f"each terminal after exactly one replay of its key; {len(platform)} cancelled by "',
       '            f"each terminal after exactly one replay of its key; 0 cancelled by "', CANCELLED),
    _m("retry_after_ignored", "the retry waits the gateway's Retry-After",
       '                wait = bench.as_float(refused.headers.get("retry-after"))\n',
       "                wait = None\n", ADMISSION),
    _m("retry_uncapped", "the retry never waits more than 60 s",
       "                time.sleep(min(max(1.0 if wait is None else wait, 0.0), 60.0))\n",
       "                time.sleep(max(1.0 if wait is None else wait, 0.0))\n", ADMISSION),
    _m("retry_any_refusal", "only a 429 is asked once more",
       "            if refused.code == 429 and attempt == 1:\n",
       "            if attempt == 1:\n", ADMISSION),
    _m("platform_cancel_passes", "a replay the platform cancelled fails the drill",
       "    if platform:\n", "    if False:\n", CANCELLED),
    _m("stream_error_counted_as_a_tear", "a platform stream failure is not the client's tear",
       ' and kind not in bench.PLATFORM_ERROR_CLASSES\n', "\n", CANCELLED),
    _m("http_answer_counted_as_a_tear", "a server's HTTP answer is not the client's tear",
       ' and not kind.startswith("http_") and', " and", CANCELLED),
    _m("platform_cancels_unrecorded", "the drill records the items the platform cancelled",
       '"cancelled_by_the_platform": cancelled_replays(rows_first, rows_second)[1],',
       '"cancelled_by_the_platform": [],', CANCELLED),
    _m("second_replay_accepted", "a cancelled item is terminal after exactly one replay",
       "    if unreplayed:\n", "    if False:\n", CANCELLED),
    _m("accepted_twice_unchecked", "no item is accepted twice across both runs",
       "    if twice:\n", "    if False:\n", RESUME),
    _m("cancel_holds_unaccounted", "a cancelled job's hold that is still held is accounted for",
       "!= Decimal(str(before.reserved_total)) + kept:", "!= Decimal(str(before.reserved_total)):",
       CANCELLED),
    _m("released_cancel_holds_counted", "only a cancelled job's hold still held is kept",
       '                if hold.request_id in cancelled and str(hold.state) == "held"), Decimal(0))',
       "                if hold.request_id in cancelled), Decimal(0))", CANCELLED),
    _m("v_cancelled_from_any_non_accepted", "only the cancelled replays' holds are allowed for",
       '                 if row.get("outcome") == bench.CANCELLED_REPLAY} - {None}',
       '                 if row.get("outcome") != "accepted"} - {None}', CANCELLED),
    _m("sop_property_unstated", "a passing drill states the MARLIN-SOP property it proved",
       "                 problems or f\"reconciled; {measured['sop']}\",",
       '                 problems or "reconciled",', CANCELLED),
    _m("over_cap_refusals_bias_the_rate", "the cap's refusals never lower the supported rate",
       '    refusals = [r for r in counted if r.get("outcome") == "rejected"]\n',
       '    refusals = [r for r in rows if r.get("outcome") == "rejected"]\n', RUNG),
    # --- E4B.c: the endpoint document --------------------------------------------------
    _m("delete_routes_unread", "every method the modules mount is in the route table",
       'METHODS = ("get", "post", "put", "delete", "patch")',
       'METHODS = ("get", "post", "put", "patch")', ROUTES, file=D),
    _m("route_constant_unresolved", "a path named by a constant is the constant's value",
       "else getattr(module, arg.id)", "else arg.id", ROUTES, file=D),
    _m("uploads_module_undocumented", "every router the cutover mounts is documented",
       '"infrx.gateway.routes.uploads", "infrx.observe.route")', '"infrx.observe.route")',
       ROUTES, file=D),
    _m("retry_after_column_blank", "the Retry-After column is RETRY_AFTER_CODES",
       '"yes" if code in retry else ""', '""', CATALOGUE, file=D),
    _m("an_error_code_dropped", "every public error code is documented",
       "for code, (status, kind) in sorted(errors.HTTP_ERRORS.items(),",
       "for code, (status, kind) in sorted(list(errors.HTTP_ERRORS.items())[1:],",
       CATALOGUE, file=D),
    _m("stream_codes_dropped", "the in-stream terminal codes are documented",
       "for code in sorted(errors.STREAM_CODES))}.", "for code in ())}.", CATALOGUE, file=D),
    _m("async_example_without_a_key", "a request that creates a job carries Idempotency-Key",
       "-H 'Idempotency-Key: sop1.k3' \\\\\",", "\\\\\",", EXAMPLES, file=D),
    _m("example_on_an_unmounted_path", "the examples call mounted routes only",
       'f"     \\"$BASE{jobs.JOBS_PATH}\\"', 'f"     \\"$BASE/v1/job\\"', EXAMPLES, file=D),
    _m("key_on_the_command_line", "the key never appears in a command line",
       'f"curl -sS -H @.auth \\"$BASE{job}\\"                     # JobStatus",',
       'f"curl -sS -H \\"Authorization: Bearer $INFRX_API_KEY\\" \\"$BASE{job}\\"  # JobStatus",',
       EXAMPLES, file=D),
    _m("r94_example_dropped", "the cross-mode conflict (R94) is shown",
       "        f\"# {errors.http_status('idempotency_conflict')} idempotency_conflict\",",
       "        f\"# 200\",", EXAMPLES, file=D),
    _m("description_status_hand_typed", "a status the prose cites is the catalogue's",
       "    (\"GET\", \"/v1/jobs/{handle}/result\"): f\"`JobResult`; {code('result_pending')} while it \"",
       "    (\"GET\", \"/v1/jobs/{handle}/result\"): f\"`JobResult`; `result_pending` (404) while it \"",
       STATUSES, file=D),
    _m("example_status_hand_typed", "a status an example cites is the catalogue's",
       "        f\"({errors.http_status('result_pending')} result_pending while it runs)\",",
       "        f\"(404 result_pending while it runs)\",", STATUSES, file=D),
    _m("cancel_cause_hand_typed", "the DELETE cause is the one the jobs module passes",
       "    (\"DELETE\", \"/v1/jobs/{handle}\"): f\"cancel (`{cancel_cause()}`), answering the committed \"",
       "    (\"DELETE\", \"/v1/jobs/{handle}\"): f\"cancel (`sync_deadline`), answering the committed \"",
       CAUSE_AUTH, file=D),
    _m("models_listed_as_authenticated", "the unauthenticated routes are read from the modules",
       "        if \"auth\" not in Path(module.__file__).read_text().lower():\n",
       "        if False:\n", CAUSE_AUTH, file=D),
    _m("location_header_dropped", "the 202's Location header is documented",
       "                   jobs.HEADER_LOCATION})", "                   })", CAUSE_AUTH, file=D),
    _m("stream_refusal_dropped", "POST /v1/jobs refuses a streaming body, and the doc says so",
       "        f\"a body with `\\\"stream\\\": true` is refused {code('invalid_request')} with `param` \"",
       "        f\"a body with `\\\"stream\\\": true` is accepted with `param` \"", CAUSE_AUTH, file=D),
    _m("upload_201_typed_200", "a 2xx the prose cites is the one its route answers",
       "                             f\"{ok('POST', '/v1/uploads')} `UploadCreated`\",",
       "                             f\"200 `UploadCreated`\",", SUCCESS, file=D),
    _m("example_202_typed_200", "a 2xx an example cites is the one its route answers",
       "          # {ACCEPTED} JobAccepted: job_handle\",", "          # 200 JobAccepted: job_handle\",",
       SUCCESS, file=D),
    _m("success_read_as_the_default", "the status is read from the route's own answer",
       "    (status,) = codes or {200}", "    status = 200", SUCCESS, GENERATED, file=D),
    _m("model_note_always_the_pin", "a published placeholder or tag is flagged in the Model table",
       "    if published == measured:\n", "    if True:\n", MODEL, file=D),
    _m("model_note_always_flagged", "the measured pin is named, not flagged",
       "    if published == measured:\n", "    if False:\n", MODEL, file=D),
    _m("regeneration_drops_the_log", "a regeneration keeps the verification log",
       "DOC.write_text(body + committed_log())", "DOC.write_text(body + LOG)", KEEPS_LOG, file=D),
    _m("committed_doc_edited_by_hand", "the committed document is the generator's output",
       "| `rate_limited` | 429 | rate_limit_error | yes |",
       "| `rate_limited` | 429 | rate_limit_error |  |", GENERATED,
       file="research/plan/evidence/e/E4B-endpoint.md"),
    _m("decision_links_a_missing_section", "the decision's runbook links resolve",
       "infra/runbooks/restart.md#engine)", "infra/runbooks/restart.md#engines)", LINKS,
       file="research/plan/evidence/e/E4B-release-decision.md"),
)


def run(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


def main() -> int:
    return shared.main(MUTANTS, RUNNER, "run E4B's mutation list")


if __name__ == "__main__":
    raise SystemExit(main())
