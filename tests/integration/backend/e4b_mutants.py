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
RUN = "tests/integration/run.py"
GENERATED = "test_e4b_the_endpoint_doc_is_what_the_code_generates"
KEEPS_LOG = "test_e4b_a_regeneration_keeps_the_verification_log"
ROUTES = "test_e4b_every_mounted_route_has_one_description_and_every_description_a_route"
CATALOGUE = "test_e4b_the_error_catalogue_is_complete_and_only_public"
EXAMPLES = "test_e4b_the_examples_call_only_mounted_routes_with_the_headers_the_contract_needs"
LINKS = "test_e4b_the_release_decision_links_resolve_to_files_and_sections"
D = "tests/integration/backend/endpoint_doc.py"


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
    _m("engine_target_skips_to_ledger", "the engine target stops at the client half, pending on the box",
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
    # --- E4B.b: the config pin ---------------------------------------------------------
    _m("moved_setting_accepted", "a setting that moved past its evidence fails the pin",
       "for name, (value, source) in declared.items() if current.get(name) != value]",
       "for name, (value, source) in declared.items() if False]", PIN),
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
    _m("any_next_server_counts", "only this repository's App/Lab servers count",
       "if is_next and any(cwd == root or cwd.startswith(root + os.sep) for root in roots):",
       "if is_next:", APPS),
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
       "    return [row for row in rows if _duration(row, clips) <= ceiling]",
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
       '<= CRITERIA["short_clip_max_edge_px"]]', '<= 10 ** 6]', RUNG),
    _m("climb_skips_a_failed_rung", "the envelope is contiguous from the lowest rung",
       "        if any(v != decide.PASS for v in core):\n            break",
       "        if any(v != decide.PASS for v in core):\n            continue", CLIMB),
    _m("other_rungs_cap_ignored", "a cap failure on any rung fails the envelope",
       'summarise([row for row in chosen if row[0] != "duration_cap"] + caps)',
       "summarise(chosen)", CLIMB),
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
       "    if args.box and not args.release_sha:\n", "    if False:\n", NOGIT),
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
       "    if args.box and not args.metrics_url:\n", "    if False:\n", SERVED),
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
       "   # 409 idempotency_conflict\",", "   # 200\",", EXAMPLES, file=D),
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
