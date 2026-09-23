#!/usr/bin/env python3
"""R32 for E2's own adapters: every invariant this task claims must be killable.

One single-edit mutant per claimed invariant, applied to a **copy** of the owned trees, with
the named cases that must then fail. A surviving mutant is a failed task, and a mutant whose
case cannot be driven (the layer-2 ones need the live stack) is reported **pending**, never
killed - the same rule the conformance suites use for a missing hook.

    apps/infrx-api/.venv/bin/python tests/integration/mutants.py --list
    apps/infrx-api/.venv/bin/python tests/integration/mutants.py            # layer 1
    apps/infrx-api/.venv/bin/python tests/integration/mutants.py --layer all

Nothing is mutated in place: each run copies `tests/integration/` and `models/marlin2b/`
into a temporary directory, edits one line there, and points the copy at the real repository
through INFRX_E2_REPO_ROOT.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness                                          # noqa: E402

# The trees a mutant may edit, copied wholesale so an edit cannot escape the copy.
# E2R item 1: `apps/infrx-api/tests/d` joins them for the D harness's ownership invariants.
# Its test loads `pgharness.py` by path relative to its own `__file__`, so in the copy it
# loads the MUTATED one; `infrx` itself comes from the real checkout through PYTHONPATH.
# E3B phase 2 (I3B req 8): `infra/` joins them, because I3B's alert rules and runbooks are
# claims too and its mutants (`all_mutants()`) now run here.
OWNED_TREES = ("tests/integration", "models/marlin2b", "apps/infrx-api/tests/d", "infra")
# E3B.c only: module code a defect mutant may edit, copied per mutant and never in place.
API_TREE = "apps/infrx-api/infrx"


@dataclass(frozen=True)
class Mutant:
    id: str
    invariant: str                # the claim this mutant is supposed to break
    path: str                     # repo-relative file inside OWNED_TREES
    before: str
    after: str
    suite: str                    # the suite to run, repo-relative inside the copy
    select: str                   # pytest -k expression naming the case(s) that must fail
    layer: int = 1
    occurrences: int = 1          # `before` must appear exactly this many times
    cases: tuple[str, ...] = field(default_factory=tuple)
    # r1 review B3: a CONTROL that must SURVIVE. If a no-op edit comes back "killed", the
    # runner is measuring its own setup and every other kill it reports is worthless.
    must_survive: bool = False
    # r2 review B1: proving "every check is rolled back" REQUIRES letting the un-rolled-back
    # writes commit, so this mutant dirties the shared database on purpose. The runner
    # re-provisions afterwards; without that, every later layer-2 mutant and the suite itself
    # fail on the residue and the failures look like broken policies.
    dirties_database: bool = False


MUTANTS: tuple[Mutant, ...] = (
    # ---------------- controls (must survive)
    Mutant("e2c01", "CONTROL: a comment-only edit changes no behaviour and must SURVIVE",
           "tests/integration/fake_vllm.py",
           "STALL_COMMENT = \": infrx-stall \"",
           "STALL_COMMENT = \": infrx-stall \"  # control: no behaviour change",
           "tests/integration/test_fake_vllm.py", "stall or conformance or terminator",
           must_survive=True),
    Mutant("e2c02", "CONTROL: a comment-only edit in the role matrix must SURVIVE",
           "tests/integration/pgstate.py",
           "PERMISSION_DENIED = \"42501\"",
           "PERMISSION_DENIED = \"42501\"  # control: no behaviour change",
           "tests/integration/test_services.py", "role_matrix_holds or should_fail",
           layer=2, must_survive=True),

    # ---------------- HttpEngine: the adapter the exported conformance suite runs against
    Mutant("e2m01", "malformed usage is never coerced into authoritative tokens",
           "tests/integration/fake_vllm.py",
           "        if not all(isinstance(value, int) and not isinstance(value, bool)\n"
           "                   for value in (prompt, completion)):\n"
           "            return None",
           "        if prompt is None or completion is None:\n"
           "            return None\n"
           "        prompt, completion = int(float(prompt)), int(float(completion))",
           "tests/integration/test_fake_vllm.py",
           "malformed or conformance",
           cases=("api_stream__malformed_or_missing_usage_is_never_authoritative",
                  "test_malformed_and_missing_usage_never_become_authoritative_tokens")),
    Mutant("e2m02", "a 200 stream without its terminator is a failure, not a success",
           "tests/integration/fake_vllm.py",
           '        if not saw_terminator:',
           '        if False:',
           "tests/integration/test_fake_vllm.py", "terminator",
           cases=("test_a_stream_without_its_terminator_is_a_failure_not_a_success",)),
    Mutant("e2m03", "a torn connection raises instead of ending the stream quietly",
           "tests/integration/fake_vllm.py",
           "            raise EngineProcessExited(f\"engine stream failed: "
           "{type(exc).__name__}\") from exc",
           "            return",
           "tests/integration/test_fake_vllm.py", "abrupt or conformance",
           cases=("api_stream__an_abrupt_exit_raises_rather_than_completing",
                  "test_an_abrupt_exit_tears_the_connection_and_is_not_a_domain_error")),
    Mutant("e2m04", "a declared stall advances the injected clock",
           "tests/integration/fake_vllm.py",
           "                            self.clock.advance(seconds)",
           "                            pass",
           "tests/integration/test_fake_vllm.py", "stall or conformance",
           cases=("api_stream__a_prefill_stall_produces_no_delta_within_the_budget",
                  "api_stream__a_midstream_stall_leaves_usage_unknown",
                  "test_a_stall_is_declared_not_slept_and_moves_only_the_injected_clock")),
    Mutant("e2m05", "the opening role chunk is the contract's progress event",
           "tests/integration/fake_vllm.py",
           "        if delta.get(\"role\") and not delta.get(\"content\"):",
           "        if False:",
           "tests/integration/test_fake_vllm.py", "conformance or stall",
           cases=("api_stream__canonical_events_end_with_authoritative_usage",)),

    # ---------------- the fake engine's own fault scripting
    Mutant("e2m06", "a cancelled job reports only the work it really did",
           "tests/integration/fake_vllm.py",
           "        if job_id and job_id in self.cancelled:",
           "        if False:",
           "tests/integration/test_fake_vllm.py", "cancellation or conformance",
           cases=("api_stream__a_cancellation_race_reports_what_was_produced",
                  "test_cancellation_is_keyed_by_job_and_leaves_other_jobs_alone")),
    Mutant("e2m07", "reasoning delimiters really are split across chunk boundaries",
           "tests/integration/fake_vllm.py",
           "        if fault is EngineFault.split_reasoning_delimiters:\n"
           "            return SPLIT_REASONING",
           "        if fault is EngineFault.split_reasoning_delimiters:\n"
           "            return (\"<think>x</think>Two people unload boxes.\",)",
           "tests/integration/test_fake_vllm.py", "split or conformance",
           cases=("api_stream__reasoning_delimiters_split_across_chunks",
                  "test_split_reasoning_delimiters_never_appear_whole_in_one_chunk")),

    # ---------------- run.py: the seven reviewer mutants of r1 B2, plus the claims
    Mutant("e2m30", "r1 B2: PENDING is never reported as a pass",
           "tests/integration/run.py",
           "        if any(entry[\"status\"] == PENDING for entry in self.stages):\n"
           "            return 3",
           "        if False:\n            return 3",
           "tests/integration/test_run.py", "exit_code_maps or pending",
           cases=("test_the_exit_code_maps_pass_pending_and_fail_and_never_confuses_them",)),
    Mutant("e2m31", "r1 B2: a failing stage is never reported as a pass",
           "tests/integration/run.py",
           "        if any(entry[\"status\"] == FAIL for entry in self.stages):\n"
           "            return 1",
           "        if False:\n            return 1",
           "tests/integration/test_run.py", "exit_code_maps or failing_suite or canary",
           cases=("test_the_exit_code_maps_pass_pending_and_fail_and_never_confuses_them",)),
    Mutant("e2m32", "r1 B2: a provisioning failure is PENDING, not PASS",
           "tests/integration/run.py",
           '        report.add("services", PENDING, f"could not provision: {exc}")',
           '        report.add("services", PASS, f"could not provision: {exc}")',
           "tests/integration/test_run.py", "provisioning_failure",
           cases=("test_a_provisioning_failure_is_pending_and_never_a_pass",)),
    Mutant("e2m33", "r1 B2: docker being unusable is PENDING, not PASS",
           "tests/integration/run.py",
           '            report.add("preflight", PENDING, f"docker unusable: {why}")',
           '            report.add("preflight", PASS, f"docker unusable: {why}")',
           "tests/integration/test_run.py", "docker_is_unusable",
           cases=("test_preflight_reports_pending_when_docker_is_unusable_and_never_pass",)),
    Mutant("e2m34", "r1 B2: an undetected canary fails the run",
           "tests/integration/run.py",
           '            problems.append(f"{half}: the canary failure was NOT detected (exit 0)")',
           "            pass",
           "tests/integration/test_run.py", "undetected_canary",
           cases=("test_an_undetected_canary_fails_the_run",)),
    Mutant("e2m69", "E2R item 4: a suite that reported no tests at all fails the run",
           "tests/integration/run.py",
           '    report.add("suites", FAIL if (failed or silent or unexpected) else PASS,',
           '    report.add("suites", FAIL if (failed or unexpected) else PASS,',
           "tests/integration/test_run.py", "reports_no_tests",
           cases=("test_a_suite_that_reports_no_tests_at_all_fails_the_run",)),
    Mutant("e2m35", "r1 B2: a failing suite fails the run",
           "tests/integration/run.py",
           '    failed = [run["argv"] for run in runs if run["exit"] != 0]',
           "    failed = []",
           "tests/integration/test_run.py", "failing_suite",
           cases=("test_a_failing_suite_fails_the_run",)),
    Mutant("e2m36", "r1 B2: teardown runs even when a stage raises",
           "tests/integration/run.py",
           "        if have_services and not args.keep:\n            teardown(report)",
           "        if False:\n            teardown(report)",
           "tests/integration/test_run.py", "teardown_runs_even",
           cases=("test_teardown_runs_even_when_a_stage_raises",)),
    Mutant("e2m37", "r1 B2 (H8): teardown fails if anything of ours survives",
           "tests/integration/harness.py",
           "    if still:\n        raise HarnessError(f\"teardown left {still} behind"
           " - disposable means gone\")",
           "    if False:\n        raise HarnessError(f\"teardown left {still} behind"
           " - disposable means gone\")",
           "tests/integration/test_run.py", "down_records_ids",
           cases=("test_down_records_ids_and_fails_if_anything_of_ours_survives",)),
    Mutant("e2m38", "r1 B2 (H9): the busy-port preflight really refuses",
           "tests/integration/run.py",
           '            report.add("preflight", FAIL,\n'
           '                       f"these task-local ports are already in use: {busy}',
           '            report.add("preflight", PASS,\n'
           '                       f"these task-local ports are already in use: {busy}',
           "tests/integration/test_run.py", "busy_task_local_port",
           cases=("test_preflight_fails_on_a_busy_task_local_port",)),
    Mutant("e2m39", "r1 B2 (H10): docker-unreachable is detected at all",
           "tests/integration/harness.py",
           '        return False, f"docker daemon unreachable: {(probe.stderr or \'\').strip()[:200]}"',
           '        return True, "assumed fine"',
           "tests/integration/test_run.py", "docker_available_reports",
           cases=("test_docker_available_reports_why_not_rather_than_raising",)),
    Mutant("e2m40", "r1 B2 (P1): a migration is never applied to a dirty database",
           "tests/integration/pgstate.py",
           "    if require_fresh and not is_fresh(conn):",
           "    if False:",
           "tests/integration/test_run.py", "not_fresh",
           cases=("test_apply_migrations_refuses_a_database_that_is_not_fresh",)),
    Mutant("e2m41", "r1 B2 (V7): the fake server binds loopback unless told otherwise",
           "tests/integration/fake_vllm.py",
           "    if args.host not in LOOPBACK and not args.allow_non_loopback:",
           "    if False:",
           "tests/integration/test_run.py", "binds_loopback",
           cases=("test_the_fake_server_binds_loopback_unless_explicitly_allowed",)),
    Mutant("e2m42", "r1 B2 (H5): the pause helper is namespace-checked like the others",
           "tests/integration/harness.py",
           '    name = assert_ours(container_of(service))\n    run(["docker", "pause", name]',
           '    name = container_of(service)\n    run(["docker", "pause", name]',
           "tests/integration/test_harness.py", "namespace_checked",
           cases=("test_every_container_helper_is_namespace_checked",)),
    Mutant("e2m43", "r1 B2: the canary needle is searched in the whole output",
           "tests/integration/run.py",
           '            "named": None if needle is None else (needle.lower() in output.lower()),',
           '            "named": None if needle is None else (needle.lower() in\n'
           '                     "\\n".join(output.strip().splitlines()[-12:]).lower()),',
           "tests/integration/test_run.py", "whole_output_not_the_tail",
           cases=("test_the_needle_is_searched_in_the_whole_output_not_the_tail",)),
    Mutant("e2m44", "r1 R-c: a bind failure is retried exactly once",
           "tests/integration/harness.py",
           "        if not retry_bind or not _looks_like_a_bind_failure(str(first)):\n"
           "            raise",
           "        raise",
           "tests/integration/test_run.py", "bind_failure",
           cases=("test_a_bind_failure_is_retried_once_and_then_refused",)),
    Mutant("e2m46", "r1 B3: a control that must survive is not counted as a problem",
           "tests/integration/mutants.py",
           # Multi-line on purpose: a single line of this expression also appears above as
           # this mutant's own data, and `occurrences` would then refuse it as stale.
           "    bad = [r for r in results\n"
           "           if r not in pending\n"
           "           and ((r[\"status\"] != \"killed\" and not r.get(\"must_survive\"))",
           "    bad = [r for r in results\n"
           "           if r not in pending\n"
           "           and ((r[\"status\"] != \"killed\")",
           "tests/integration/test_run.py", "count_the_verdict_the_same_way",
           cases=("test_the_mutation_stage_and_the_cli_count_the_verdict_the_same_way",)),
    Mutant("e2m45", "same pass: SIGTERM is handled like SIGINT so teardown still runs",
           "tests/integration/run.py",
           "    for signum in (signal.SIGINT, signal.SIGTERM):",
           "    for signum in (signal.SIGINT,):",
           "tests/integration/test_run.py", "sigterm",
           cases=("test_sigterm_tears_down_and_orphans_no_fake_server",)),

    # ---------------- the r2 review's eight unproven claims
    Mutant("e2m47", "r2 B2: a kill needs a reported failure, not just a non-zero exit",
           "tests/integration/mutants.py",
           # Multi-line: the single line also appears above as this mutant's own data, and
           # `occurrences` would refuse it as stale.
           '    killed = code != 0 and detail["failed"] > 0\n'
           "    if mutant.must_survive:",
           '    killed = code != 0\n'
           "    if mutant.must_survive:",
           "tests/integration/test_run.py", "verdict_reads_pytests_own_summary",
           cases=("test_the_verdict_reads_pytests_own_summary_and_not_the_exit_code",)),
    Mutant("e2m48", "r2 B2: a collection error is setup-error, never a kill",
           "tests/integration/mutants.py",
           '    if detail["errors"] or (code != 0 and not summary):\n'
           '        return {**detail, "status": "setup-error",',
           '    if code != 0 and not summary:\n'
           '        return {**detail, "status": "setup-error",',
           "tests/integration/test_run.py", "verdict_reads_pytests_own_summary",
           cases=("test_the_verdict_reads_pytests_own_summary_and_not_the_exit_code",)),
    Mutant("e2m49", "r2 B2: a selector that matched nothing is no-cases",
           "tests/integration/mutants.py",
           '    if nothing_ran:\n        return {**detail, "status": "no-cases",',
           '    if False:\n        return {**detail, "status": "no-cases",',
           "tests/integration/test_run.py", "verdict_reads_pytests_own_summary",
           cases=("test_the_verdict_reads_pytests_own_summary_and_not_the_exit_code",)),
    Mutant("e2m50", "r2 B2: the counts come from the summary line, not the whole output",
           "tests/integration/mutants.py",
           '    summary = _summary(output)\n'
           '    failed = re.search(r"(\\d+) failed", summary)',
           '    summary = output\n'
           '    failed = re.search(r"(\\d+) failed", summary)',
           "tests/integration/test_run.py", "verdict_reads_pytests_own_summary",
           cases=("test_the_verdict_reads_pytests_own_summary_and_not_the_exit_code",)),
    Mutant("e2m51", "r2 B5: a same-named unlabelled network is a candidate",
           "tests/integration/harness.py",
           '                  "network": lambda n: n == NETWORK or n.startswith(f"{PROJECT}_")}[kind]',
           '                  "network": lambda n: False}[kind]',
           "tests/integration/test_services.py", "unlabelled_network",
           layer=2,
           cases=("test_an_unlabelled_network_with_our_name_is_refused_not_removed",)),
    Mutant("e2m52", "r2 B5: the orphan scan really reads the process table",
           "tests/integration/run.py",
           '    marker = str((harness.HERE / "fake_vllm.py").resolve())',
           '    marker = "__missing_orphan_marker__"',
           "tests/integration/test_run.py", "real_orphaned_fake_server",
           cases=("test_a_real_orphaned_fake_server_is_found_by_its_command_line",)),
    Mutant("e2m70", "E2R: the ps orphan branch is not truncated by a narrow terminal",
           "tests/integration/run.py",
           '        result = subprocess.run(["ps", "-axww", "-o", "pid=,command="],',
           '        result = subprocess.run(["ps", "-ax", "-o", "pid=,command="],',
           "tests/integration/test_run.py", "real_orphaned_fake_server",
           cases=("test_a_real_orphaned_fake_server_is_found_by_its_command_line",)),
    Mutant("e2m71", "E2R: the ps parser needs the pid AND the command line on the line",
           "tests/integration/run.py",
           "            if pid != os.getpid() and marker in columns[1]:",
           "            if pid != os.getpid():",
           "tests/integration/test_run.py", "ps_orphan_parser",
           cases=("test_the_ps_orphan_parser_reads_a_pid_and_a_whole_command_line",)),
    Mutant("e2m72", "E2R review B1: one failed role-matrix row fails the rls STAGE and the run",
           "tests/integration/run.py",
           '    report.add("rls", FAIL if failed else PASS,\n'
           '               {"cases": len(rows), "failed": failed or None,',
           '    report.add("rls", PASS,\n'
           '               {"cases": len(rows), "failed": None,',
           "tests/integration/test_run.py", "rls_stage",
           cases=("test_a_failing_role_matrix_row_fails_the_rls_stage_and_the_run",)),
    Mutant("e2m73", "E2R review N1: anon rows are not exempt from the SQLSTATE comparison",
           "tests/integration/pgstate.py",
           '        passed = (outcome == "error" and observed == expected',
           '        passed = (outcome == "error" and (observed == expected or check.role == "anon")',
           "tests/integration/test_services.py", "should_fail",
           layer=2, cases=("test_a_check_that_should_fail_does_fail",)),
    Mutant("e2m53", "r2 minor M12: provision_database only talks to our own container",
           "tests/integration/harness.py",
           '    container = assert_ours(container_of("postgres"))',
           '    container = container_of("postgres")',
           "tests/integration/test_run.py", "provision_database_refuses",
           cases=("test_provision_database_refuses_a_container_outside_the_namespace",)),
    Mutant("e2m54", "r2 minor M43: the server log cannot be left in the temp directory",
           "tests/integration/fake_vllm.py",
           '        self._log = tempfile.TemporaryFile(prefix="infrx-e2-fake-vllm-", suffix=".log")',
           '        self._log = tempfile.NamedTemporaryFile(prefix="infrx-e2-fake-vllm-", delete=False)',
           "tests/integration/test_fake_vllm.py", "log_is_never_left_behind",
           cases=("test_the_server_log_is_never_left_behind_in_the_temp_directory",)),
    Mutant("e2m55", "r2 minor V7: the CLI default host is loopback",
           "tests/integration/fake_vllm.py",
           '    parser.add_argument("--host", default="127.0.0.1",',
           '    parser.add_argument("--host", default="0.0.0.0",',
           "tests/integration/test_run.py", "binds_loopback",
           cases=("test_the_fake_server_binds_loopback_unless_explicitly_allowed",)),
    Mutant("e2m56", "r2 B1 (P4): every matrix check is rolled back, leaving no residue",
           "tests/integration/pgstate.py",
           # Multi-line since E2R: `probe_clock` ends a transaction the same way, so the bare
           # line is no longer unique and `occurrences` would report this mutant as stale.
           '                outcome = "unexpected-success"\n            raise _Rollback\n',
           '                outcome = "unexpected-success"\n            pass\n',
           "tests/integration/test_services.py", "role_matrix_holds",
           layer=2, dirties_database=True,
           cases=("test_the_role_matrix_holds_for_every_role",)),
    Mutant("e2m57", "r2 B3: a surviving non-control fails the mutation stage",
           "tests/integration/run.py",
           '    status = FAIL if summary["problems"] else (PENDING if summary["pending"] else PASS)',
           "    status = PASS",
           "tests/integration/test_run.py", "count_the_verdict_the_same_way",
           cases=("test_the_mutation_stage_and_the_cli_count_the_verdict_the_same_way",)),
    Mutant("e2m58", "r2 B4: a 42501 case is qualified by the message it really emits",
           "tests/integration/pgstate.py",
           "                  and (check.message_contains is None\n"
           "                       or check.message_contains.lower() in message.lower()))",
           "                  and True)",
           "tests/integration/test_services.py", "should_fail",
           layer=2, cases=("test_a_check_that_should_fail_does_fail",)),

    # ---------------- namespace and pinning guards
    Mutant("e2m08", "a destructive helper refuses a name outside the namespace",
           "tests/integration/harness.py",
           "    if not container.startswith(PREFIX):",
           "    if False:",
           "tests/integration/test_harness.py", "destructive",
           cases=("test_a_destructive_helper_refuses_anything_outside_the_namespace",)),
    Mutant("e2m09", "every service image is pinned by digest",
           "tests/integration/compose.yaml",
           "    image: valkey/valkey@sha256:"
           "d2e18f3410b6f616de1417f570fa55261af2898b9c5b2cfb6781ce2373ea43d1",
           "    image: valkey/valkey:8.1-alpine",
           "tests/integration/test_harness.py", "digest",
           cases=("test_every_image_is_pinned_by_digest",)),
    Mutant("e2m10", "every published port is inside E2's range and on loopback",
           "tests/integration/harness.py",
           '    "clickhouse_http": 55523,',
           '    "clickhouse_http": 58123,',
           "tests/integration/test_harness.py", "port or compose_file",
           cases=("test_every_port_is_inside_the_range_tasklocal_grants_this_task",
                  "test_the_compose_file_publishes_exactly_those_ports_on_loopback")),
    Mutant("e2m11", "the movable database clock cannot exist in a deployed database",
           "tests/integration/pgstate.py",
           'CLOCK_SCHEMA = "infrx_test"',
           'CLOCK_SCHEMA = "infrx"',
           "tests/integration/test_harness.py", "movable_clock",
           cases=("test_the_movable_clock_cannot_exist_in_a_deployed_database",)),
    Mutant("e2m59", "E2R: the only installer of the movable clock is not a migration",
           "tests/integration/pgstate.py",
           'CLOCK_FIXTURE = harness.API_ROOT / "infrx" / "state" / "test_clock.sql"',
           'CLOCK_FIXTURE = harness.MIGRATIONS_DIR / "0003_pilot_durable_schema.sql"',
           "tests/integration/test_harness.py", "movable_clock",
           cases=("test_the_movable_clock_cannot_exist_in_a_deployed_database",)),
    Mutant("e2m61", "E2R: an anon denial names the relation whose grant is missing",
           "tests/integration/pgstate.py",
           'message_contains="permission denied for table organizations"),',
           'message_contains="permission denied for table"),',
           "tests/integration/test_harness.py", "role_matrix_covers",
           cases=("test_the_role_matrix_covers_every_role_and_every_expectation_kind",)),

    # ---------------- the test-id table
    Mutant("e2m12", "namespacing really separates the colliding legacy ids",
           "tests/integration/testids.py",
           '        return f"{self.prefix}-{legacy}"',
           "        return legacy",
           "tests/integration/test_harness.py", "namespac or issued_twice",
           cases=("test_no_namespaced_id_is_issued_twice_and_all_four_namespaces_are_used",)),
    Mutant("e2m13", "a legacy id is only claimed for a document that contains it",
           "tests/integration/testids.py",
           '    Series("TRACE", "H", _r(1, 7), "drills", "§9 cross-cutting drills",',
           '    Series("TRACE", "H", _r(1, 9), "drills", "§9 cross-cutting drills",',
           "tests/integration/test_harness.py", "occurs_in_the_document",
           cases=("test_every_legacy_test_id_occurs_in_the_document_it_is_attributed_to",)),
    Mutant("e2m14", "every matrix case states the invariant it pins",
           "tests/integration/pgstate.py",
           '              ("rowcount", 1), "an owner mints a key for their own tenant"),',
           '              ("rowcount", 1), ""),',
           "tests/integration/test_harness.py", "role_matrix_covers",
           cases=("test_the_role_matrix_covers_every_role_and_every_expectation_kind",)),

    # ---------------- the bench-client key gate (E1's finding, fixed here)
    Mutant("e2m15", "a public key prefix only counts with its separator",
           "models/marlin2b/bench.py",
           '        for form in (prefix, fold(prefix) + "-"):',
           "        for form in (prefix, fold(prefix)):",
           "models/marlin2b/tests/test_leaks.py", "separator",
           cases=("test_a_public_prefix_only_counts_with_its_separator",)),

    # ---------------- layer 2: need the live stack, reported pending without it
    Mutant("e2m16", "the role-matrix runner can report a failure at all",
           "tests/integration/pgstate.py",
           '            "outcome": outcome, "passed": passed, "why": check.why}',
           '            "outcome": outcome, "passed": True, "why": check.why}',
           "tests/integration/test_services.py", "should_fail or role_matrix_holds",
           layer=2, cases=("test_a_check_that_should_fail_does_fail",)),
    Mutant("e2m17", "an RLS refusal is checked against its SQLSTATE, not just 'it errored'",
           "tests/integration/pgstate.py",
           '        passed = (outcome == "error" and observed == expected',
           '        passed = (outcome == "error" and True',
           "tests/integration/test_services.py", "should_fail",
           layer=2, cases=("test_a_check_that_should_fail_does_fail",)),
    Mutant("e2m18", "E2R: a rolled-back move of the shared clock leaves it where it was",
           "tests/integration/pgstate.py",
           # Multi-line: `raise _Rollback` also ends every role-matrix check.
           "            advance_clock(conn, 1800.0)\n"
           "            inside = clock_delta_s(conn)\n"
           "            raise _Rollback",
           "            advance_clock(conn, 1800.0)\n"
           "            inside = clock_delta_s(conn)\n"
           "            pass",
           "tests/integration/test_services.py", "shared_clock",
           layer=2,
           cases=("test_the_shared_clock_moves_the_function_every_durable_decision_reads",)),
    Mutant("e2m60", "E2R item 2: anon is REFUSED by the missing grant, not filtered to 0 rows",
           "tests/integration/pgstate.py",
           'Check("E2-RLS-04", "anon", None, "select count(*) from public.models",\n'
           '              ("error", PERMISSION_DENIED),',
           'Check("E2-RLS-04", "anon", None, "select count(*) from public.models",\n'
           '              ("value", 0),',
           "tests/integration/test_services.py", "role_matrix_holds",
           layer=2, cases=("test_the_role_matrix_holds_for_every_role",)),
    Mutant("e2m62", "E2R item 2: E2-RLS-44's refusal is the function grant, not the body's check",
           "tests/integration/pgstate.py",
           '              message_contains="permission denied for function org_balance"),',
           '              message_contains="not a member of organization"),',
           "tests/integration/test_services.py", "role_matrix_holds",
           layer=2, cases=("test_the_role_matrix_holds_for_every_role",)),
    Mutant("e2m63", "E2R item 2: the legacy claim GUC is set, which is the one this image reads",
           "tests/integration/pgstate.py",
           "    conn.execute(\"select set_config('request.jwt.claim.sub', %s, true)\", (str(user_id),))",
           "    pass",
           "tests/integration/test_services.py", "both_jwt_claim_forms",
           layer=2, cases=("test_both_jwt_claim_forms_are_set_for_an_impersonated_principal",)),
    Mutant("e2m19", "the namespace guard holds against the live daemon, not just the prefix",
           "tests/integration/harness.py",
           '    if labels.get("com.docker.compose.project") != PROJECT:',
           "    if False:",
           "tests/integration/test_services.py", "cleanup_is_scoped or prefix_alone",
           layer=2, cases=("test_the_prefix_alone_is_not_enough_to_be_touchable",)),
    Mutant("e2m20", "the matrix's allowed writes really are allowed by the database",
           "tests/integration/pgstate.py",
           '              ("rowcount", 1), "an owner mints a key for their own tenant"),',
           '              ("rowcount", 0), "an owner mints a key for their own tenant"),',
           "tests/integration/test_services.py", "role_matrix_holds",
           layer=2, cases=("test_the_role_matrix_holds_for_every_role",)),
    Mutant("e2m21", "a cross-tenant denial is measured, not assumed",
           "tests/integration/pgstate.py",
           '              "select count(*) from public.api_keys where org_id = {beta}",\n'
           '              ("value", 0), "cross-tenant key metadata is invisible"),',
           '              "select count(*) from public.api_keys where org_id = {beta}",\n'
           '              ("value", 1), "cross-tenant key metadata is invisible"),',
           "tests/integration/test_services.py", "role_matrix_holds",
           layer=2, cases=("test_the_role_matrix_holds_for_every_role",)),
    Mutant("e2m22", "r1 B1: ownership needs the checkout label, not just the project name",
           "tests/integration/harness.py",
           "    return (labels.get(\"com.docker.compose.project\") == PROJECT\n"
           "            and labels.get(CHECKOUT_LABEL) == working_dir())",
           "    return labels.get(\"com.docker.compose.project\") == PROJECT",
           "tests/integration/test_services.py", "another_checkout or prefix_alone",
           layer=2,
           cases=("test_a_stack_labelled_for_another_checkout_is_refused_not_destroyed",)),
    Mutant("e2m23", "r1 B1: a same-named volume nobody labelled is refused, not deleted",
           "tests/integration/harness.py",
           '    return [item for kind in ("container", "volume", "network") for item in foreign(kind)]',
           '    return foreign("container")',
           "tests/integration/test_services.py", "unlabelled_volume",
           layer=2,
           cases=("test_an_unlabelled_volume_with_our_name_is_refused_not_deleted",)),
    Mutant("e2m24", "r1 B4: the fixture ids really are a function of the seed",
           "tests/integration/pgstate.py",
           "    rng = Random(seed)\n"
           "    ids = {\"users\": {handle: _uuid(rng) for handle, _, _ in PEOPLE},",
           "    rng = Random()\n"
           "    ids = {\"users\": {handle: _uuid(rng) for handle, _, _ in PEOPLE},",
           "tests/integration/test_services.py", "recomputed_from_the_seed",
           layer=2,
           cases=("test_exactly_the_seeded_ids_are_in_the_database_recomputed_from_the_seed",)),
    Mutant("e2m25", "r1 R-b: the JSON claims form is set as well as the legacy GUCs",
           "tests/integration/pgstate.py",
           "    conn.execute(\"select set_config('request.jwt.claims', %s, true)\",\n"
           "                 (json.dumps({\"sub\": str(user_id), \"role\": role}),))",
           "    pass",
           "tests/integration/test_services.py", "both_jwt_claim_forms",
           layer=2, cases=("test_both_jwt_claim_forms_are_set_for_an_impersonated_principal",)),
    Mutant("e2m26", "r1 R-b: a vacuous matrix (auth.uid() NULL) cannot pass",
           "tests/integration/pgstate.py",
           "                if str(identity) != str(principal.user_id):",
           "                if False:",
           "tests/integration/test_services.py", "authenticates_nobody or role_matrix_holds",
           layer=2, cases=("test_a_matrix_that_authenticates_nobody_fails",)),
    # ---------------- E2R item 1: the D harness owns its container before it uses it
    # Layer 1 by the harness's own definition (they need docker but not the E2 stack), and
    # they skip visibly without it, exactly as D's suite does.
    Mutant("e2m64", "E2R: a container this run did not create is never used or replaced",
           "apps/infrx-api/tests/d/pgharness.py",
           "    stranger = foreign()\n    if stranger is not None:",
           "    stranger = None\n    if stranger is not None:",
           "apps/infrx-api/tests/d/test_pgharness.py", "did_not_create",
           cases=("test_a_container_this_run_did_not_create_is_refused_and_survives",)),
    Mutant("e2m65", "E2R: the port lock refuses a concurrent run before anything is inspected",
           "apps/infrx-api/tests/d/pgharness.py",
           "    _acquire_lock()\n    stranger = foreign()",
           "    stranger = foreign()",
           "apps/infrx-api/tests/d/test_pgharness.py", "second_concurrent_run",
           cases=("test_a_second_concurrent_run_is_refused_and_alters_nothing",)),
    Mutant("e2m66", "E2R: a crashed run's leftover container is replaced, never adopted",
           "apps/infrx-api/tests/d/pgharness.py",
           '        _docker("rm", "-f", "-v", CONTAINER, check=False)\n'
           "    # The Supabase image initialises its own roles",
           "        pass\n"
           "    # The Supabase image initialises its own roles",
           "apps/infrx-api/tests/d/test_pgharness.py", "killed_mid_provision",
           cases=("test_a_run_killed_mid_provision_is_cleaned_up_by_the_next_one",)),
    Mutant("e2m67", "E2R: only what this run created is removed",
           "apps/infrx-api/tests/d/pgharness.py",
           "    if not _created:\n        return\n    assert_ours(\"remove\")",
           "    assert_ours(\"remove\")",
           "apps/infrx-api/tests/d/test_pgharness.py", "only_ever_removes",
           cases=("test_remove_only_ever_removes_what_this_run_created",)),
    Mutant("e2m68", "E2R: one lock covers both image variants, because they share the port",
           "apps/infrx-api/tests/d/pgharness.py",
           'return Path(tempfile.gettempdir()) / f"{SERVICE.container}-{PORT}.lock"',
           'return Path(tempfile.gettempdir()) / f"{CONTAINER}.lock"',
           "apps/infrx-api/tests/d/test_pgharness.py", "both_image_variants",
           cases=("test_the_port_lock_is_shared_by_both_image_variants",)),

    Mutant("e2m27", "r1 R-a: the template copy is owned by postgres, or migrations cannot run",
           "tests/integration/harness.py",
           "                             f\"template {PG_TEMPLATE_SOURCE} owner {PG_USER}\")],",
           "                             f\"template {PG_TEMPLATE_SOURCE}\")],",
           "tests/integration/test_run.py", "provision_database_statements",
           cases=("test_provision_database_statements_are_the_ones_r_a_requires",)),

    # ---------------- E3B.c: intentional defects the backend drills must DETECT. Each edits
    # a temporary copy of `apps/infrx-api/infrx` (see `run_one`), never the checkout: the
    # defect lives in the merged fake store or the composition root, and the named drill in
    # tests/integration/backend must fail. The PostgreSQL-side defects are drills that inject
    # themselves inside a rolled-back transaction (`test_schema.py` e3b_db03-05).
    Mutant("e3bc01", "CONTROL: a comment in the fake store changes nothing and must SURVIVE",
           "apps/infrx-api/infrx/contracts/fakes/state.py",
           "        wallet.reserved_total = wallet.reserved_total + hold\n",
           "        wallet.reserved_total = wallet.reserved_total + hold  # control\n",
           "tests/integration/backend/test_drills.py", "fake", must_survive=True),
    Mutant("e3bm01", "E3B defect: a missing durable acceptance (no hold reserved) is detected",
           "apps/infrx-api/infrx/contracts/fakes/state.py",
           "        wallet.reserved_total = wallet.reserved_total + hold\n",
           "        wallet.reserved_total = wallet.reserved_total\n",
           "tests/integration/backend/test_drills.py", "dr01 and fake",
           cases=("test_e3b_dr01_acceptance_crash_after_commit_retries_to_one_identity",)),
    Mutant("e3bm02", "E3B defect: a missing durable acceptance (no dispatch outbox) is detected",
           "apps/infrx-api/infrx/contracts/fakes/state.py",
           "        self.outbox.append(event)\n",
           "        self.outbox.append(event) if kind is not OutboxKind.prepare_dispatch "
           "else None\n",
           "tests/integration/backend/test_drills.py", "dr01 and fake",
           cases=("test_e3b_dr01_acceptance_crash_after_commit_retries_to_one_identity",)),
    Mutant("e3bm03", "E3B defect: a stale generation's append is detected",
           "apps/infrx-api/infrx/contracts/fakes/state.py",
           "        if job.generation != lease.generation:\n",
           "        if False:\n",
           "tests/integration/backend/test_drills.py", "dr05 and fake",
           cases=("test_e3b_dr05_a_stale_generation_cannot_append",)),
    Mutant("e3bm04", "E3B defect: a duplicate settlement (cancel re-settles) is detected",
           "apps/infrx-api/infrx/contracts/fakes/state.py",
           "            if job.terminal:\n"
           "                # Completion won the race; a completed job stays completed.\n",
           "            if False:\n"
           "                # Completion won the race; a completed job stays completed.\n",
           "tests/integration/backend/test_drills.py", "dr07 and fake",
           cases=("test_e3b_dr07_a_duplicate_settlement_settles_once",)),
    Mutant("e3bm05", "E3B defect: foreign result access (owner check dropped) is detected",
           "apps/infrx-api/infrx/contracts/fakes/state.py",
           "        if job is None or job.request.org_id != org_id:\n",
           "        if job is None:\n",
           "tests/integration/backend/test_drills.py", "dr08 and fake",
           cases=("test_e3b_dr08_a_foreign_tenant_cannot_read_cancel_or_see_a_result",)),
    Mutant("e3bm06", "E3B defect: a pilot falling back to the legacy shared key is detected",
           "apps/infrx-api/infrx/config.py",
           "        if missing or forbidden:\n",
           "        if missing:\n",
           "tests/integration/backend/test_drills.py", "dr16",
           cases=("test_e3b_dr16_pilot_refuses_the_legacy_shared_key",)),
    Mutant("e3bm07", "E3B stage: a pending backend case is never counted as a pass",
           "tests/integration/run.py",
           '    return PENDING if cases["pending"] else PASS\n',
           "    return PASS\n",
           "tests/integration/backend/test_stage.py", "pending_cases",
           cases=("test_pending_cases_are_counted_by_their_unblocking_id_and_never_as_passes",)),
    Mutant("e3bm08", "E3B stage: a plain skip at layer 3 is a case that did not run",
           "tests/integration/run.py",
           'if cases["failed"] or cases["skipped"] or',
           'if cases["failed"] or',
           "tests/integration/backend/test_stage.py", "plain_skip",
           cases=("test_a_failure_a_plain_skip_or_an_empty_run_fails_the_stage",)),
    Mutant("e3bm09", "E3B stage: only a PENDING[...] message makes a skip pending",
           "tests/integration/run.py",
           '                cases["skipped"].append(name)\n',
           '                cases["pending"].setdefault("?", []).append(name)\n',
           "tests/integration/backend/test_stage.py", "plain_skip",
           cases=("test_a_failure_a_plain_skip_or_an_empty_run_fails_the_stage",)),
    Mutant("e3bm10", "E3B stage (rv07): the stage status is the verdict of its own counts",
           "tests/integration/run.py",
           "    return backend_verdict(cases, exit_code), {\n",
           "    return PASS, {\n",
           "tests/integration/backend/test_stage.py", "pending_cases",
           cases=("test_pending_cases_are_counted_by_their_unblocking_id_and_never_as_passes",)),
    Mutant("e3bm11", "E3B stage (rv08): an xfail is never counted as pending",
           "tests/integration/run.py",
           '            if mark and skip.get("type") != "pytest.xfail":\n',
           "            if mark:\n",
           "tests/integration/backend/test_stage.py", "expected_failure",
           cases=("test_an_expected_failure_is_not_pending_even_if_it_says_so",)),
    Mutant("e3bm12", "E3B stage (rv07): backend() reports the status its summary computed",
           "tests/integration/run.py",
           '    report.add("backend", status, {"postgrest": postgrest, **summary},\n',
           '    report.add("backend", PASS, {"postgrest": postgrest, **summary},\n',
           "tests/integration/backend/test_stage.py", "reports_the_summary",
           cases=("test_the_backend_stage_reports_the_summary_of_what_its_suite_produced",)),

    # ---------------- E3B phase 2 (one per item; the item number is in the invariant)
    Mutant("e3bm13", "E3B2 item 0: no port of E2's block is left literal in a derived place",
           "tests/integration/compose.yaml",
           '      - "127.0.0.1:${INFRX_E2_PORT_POSTGRES:?}:5432"',
           '      - "127.0.0.1:55532:5432"',
           "tests/integration/test_harness.py", "compose_file_publishes and e3b2",
           cases=("test_the_compose_file_publishes_exactly_those_ports_on_loopback[e3b2]",)),
    Mutant("e3bm14", "E3B2 item 1a: a drill pends on ITS stubs, not on the global stub count",
           "tests/integration/backend/stack.py",
           "    return {rpc: owners[rpc] for rpc in rpcs if rpc in owners}\n",
           "    return dict(owners)\n",
           "tests/integration/backend/test_stage.py", "pends_only_on_the_stubs",
           cases=("test_a_drill_pends_only_on_the_stubs_it_drives",)),
    Mutant("e3bm15", "E3B2 item 1a: a drill whose functions are stubs pends, it never runs",
           "tests/integration/backend/test_drills.py",
           "        if stubs:\n            stack.pending(",
           "        if False:\n            stack.pending(",
           "tests/integration/backend/test_drills.py", "dr05 and postgres", layer=2,
           cases=("test_e3b_dr05_a_stale_generation_cannot_append[postgres]",)),
    Mutant("e3bm16", "E3B2 item 1c: an E3B case cannot name a merged task as its blocker",
           "tests/integration/backend/stack.py",
           "    unknown = [task for task in ids if task not in PENDING or task in RESIDUAL]\n",
           "    unknown = [task for task in ids if task not in PENDING]\n",
           "tests/integration/backend/test_stage.py", "merged_task",
           cases=("test_no_pending_id_names_a_merged_task_unless_it_is_a_named_residual",)),
    Mutant("e3bm17", "E3B2 item 2: the real store's replay is reported as a replay (dr01)",
           "apps/infrx-api/infrx/state/jobstore.py",
           'if name != "schema_version")\n',
           'if name not in ("schema_version", "replayed"))\n',
           "tests/integration/backend/test_drills.py", "dr01 and postgres", layer=2,
           cases=("test_e3b_dr01_acceptance_crash_after_commit_retries_to_one_identity"
                  "[postgres]",)),
    Mutant("e3bm18", "E3B2 item 4: without the rebuild the acknowledged dispatches are lost",
           "tests/integration/backend/test_drills.py",
           "        assert await relay.rebuild() == 3\n", "\n",
           "tests/integration/backend/test_drills.py", "dr13", layer=2,
           cases=("test_e3b_dr13_losing_the_queue_index_loses_no_accepted_job",)),
    Mutant("e3bm19", "E3B2 item 4: a rebuild from the pre-ack snapshot re-dispatches the "
                     "terminal job",
           "tests/integration/backend/test_drills.py",
           "        assert await relay.rebuild() == 3\n", "        await port.rebuild(early)\n",
           "tests/integration/backend/test_drills.py", "dr13", layer=2,
           cases=("test_e3b_dr13_losing_the_queue_index_loses_no_accepted_job",)),
    Mutant("e3bm20", "E3B2 item 5: the worker's reaper enqueues what recover requeued",
           "apps/infrx-api/infrx/worker/service.py",
           "                await self.loop.scheduler.enqueue(event)\n",
           "                pass\n",
           "tests/integration/backend/test_drills.py", "dr04 and postgres", layer=2,
           cases=("test_e3b_dr04_a_claim_whose_answer_was_lost_is_requeued_once[postgres]",)),
    Mutant("e3bm21", "E3B2 item 7: a relation with no matrix row fails the completeness case",
           "tests/integration/pgstate.py", '    "infrx.jobs": SERVICE,\n', "",
           "tests/integration/test_services.py", "row_for_every_relation", layer=2,
           cases=("test_the_role_matrix_has_a_row_for_every_relation_and_security_definer_"
                  "function",)),
    Mutant("e3bm22", "E3B2 item 8: the mutation stage runs I3B's list too",
           "tests/integration/run.py",
           "                   for mutant in mutants.all_mutants() if layer",
           "                   for mutant in mutants.MUTANTS if layer",
           "tests/integration/test_run.py", "every_list_through_one_runner",
           cases=("test_the_mutation_stage_runs_every_list_through_one_runner",)),
    Mutant("e3bm23", "E3B2 item 8: infra/ is copied, so an I3B rule mutant edits the copy",
           "tests/integration/mutants.py",
           # split so this definition is not a second occurrence of its own anchor
           '"apps/infrx-api/tests/d", ' '"infra")',
           '"apps/infrx-api/tests/d"' ')',
           "tests/integration/test_run.py", "every_list_through_one_runner",
           cases=("test_the_mutation_stage_runs_every_list_through_one_runner",)),
    Mutant("e3bm24", "E3B2 item 9: a suite past its budget is a failed run, not a crash",
           "tests/integration/run.py",
           "    except subprocess.TimeoutExpired:\n",
           "    except ZeroDivisionError:\n",
           "tests/integration/test_run.py", "outlives_its_budget",
           cases=("test_a_suite_that_outlives_its_budget_is_a_failed_run_not_a_traceback",)),
    Mutant("e3bm26", "E3B2 review H1: a mutant whose cases are red unmutated is never a kill",
           "tests/integration/mutants.py",
           "        if red is not None:\n", "        if False:\n",
           "tests/integration/test_run.py", "baseline_red",
           cases=("test_a_mutant_whose_cases_are_red_unmutated_is_baseline_red",)),
    Mutant("e3bm27", "E3B2 review H2: a pending id naming a merged task fails the stage",
           "tests/integration/run.py",
           '                  if tasks.get(task) in ("implemented", "integrated") and task not '
           'in residual)\n',
           "                  if False)\n",
           "tests/integration/backend/test_stage.py", "naming_a_merged_task_fails",
           cases=("test_a_pending_id_naming_a_merged_task_fails_the_stage",)),
    Mutant("e3bm28", "E3B2 review F3: the fake CREDIT admission holds on the CREDIT wallet",
           "apps/infrx-api/infrx/contracts/fakes/state.py",
           "                                               wallet_id=wallet_id)\n",
           "                                               wallet_id=None)\n",
           "tests/integration/backend/test_drills.py", "dr01c and fake",
           cases=("test_e3b_dr01c_a_credit_admission_replays_to_one_identity_and_one_credit_"
                  "hold[fake]",)),
    Mutant("e3bm29", "E3B2 review F5: dr01c's 'no USD hold' half is load-bearing (db08b)",
           "tests/integration/backend/test_drills.py",
           'from infrx.credit_holds where request_id = %s",',
           'from infrx.credit_holds where request_id <> %s",',
           "tests/integration/backend/test_drills.py", "db08b", layer=2,
           cases=("test_e3b_db08b_detects_a_usd_hold_beside_the_credit_one",)),
    Mutant("e3bm30", "E3B2 review F6: a live defect never lands outside this process's clones",
           "tests/integration/backend/stack.py",
           '    if not name.startswith(f"{harness.PG_DATABASE}_{os.getpid()}_"):\n',
           "    if False:\n",
           "tests/integration/backend/test_stage.py", "refused_outside",
           cases=("test_a_live_defect_is_refused_outside_this_processs_clones",)),
    Mutant("e3bm31", "E3B2 review H4: a leaked fake-vLLM log is swept in every namespace",
           "tests/integration/mutants.py",
           '                              *root.glob("infrx-e2-fake-vllm-*"))\n',
           "                              )\n",
           "tests/integration/test_run.py", "leaked_server_log",
           cases=("test_a_leaked_server_log_is_litter_in_every_namespace",)),
    Mutant("e3bm32", "E3B2 review H5: a timed-out suite takes its whole process group down",
           "tests/integration/run.py",
           "        os.killpg(process.pid, signal.SIGKILL)\n        stdout, stderr = "
           "process.communicate()\n",
           "        process.kill()\n        stdout, stderr = process.communicate()\n",
           "tests/integration/test_run.py", "outlives_its_budget",
           cases=("test_a_suite_that_outlives_its_budget_is_a_failed_run_not_a_traceback",)),
    Mutant("e3bm25", "E3B2: advance() is measured as returning the moved clock (D2's)",
           "tests/integration/pgstate.py",
           "    lag = (read_back - returned).total_seconds()\n",
           "    lag = (read_back - wall).total_seconds()\n",
           "tests/integration/test_services.py", "shared_clock", layer=2,
           cases=("test_the_shared_clock_moves_the_function_every_durable_decision_reads",)),
    Mutant("e3bm33", "E3B2 review H6: a timed-out suite fails the suites stage and the run",
           "tests/integration/run.py",
           '    failed = [run["argv"] for run in runs if run["exit"] != 0]\n',
           '    failed = [run["argv"] for run in runs if run["exit"] not in (0, 124)]\n',
           "tests/integration/test_run.py", "timed_out_fails_the_suites",
           cases=("test_a_suite_that_timed_out_fails_the_suites_stage_and_the_run",)),
    Mutant("e3bm34", "E3B2 review H7: a report names the commit it is evidence for",
           "tests/integration/run.py",
           '    return {"sha": git("rev-parse", "HEAD") or None,\n',
           '    return {"sha": None,\n',
           "tests/integration/test_run.py", "names_its_tree",
           cases=("test_the_report_names_its_tree_its_namespace_and_each_stages_duration",)),
    Mutant("e3bm35", "E3B2 review F6-findings: an unattributed api-test skip fails the stage",
           "tests/integration/run.py",
           "    report.add(\"suites\", FAIL if (failed or silent or unexpected) else PASS,\n",
           "    report.add(\"suites\", FAIL if (failed or silent) else PASS,\n",
           "tests/integration/test_run.py", "unexpected_skip",
           cases=("test_an_unexpected_skip_in_api_test_fails_the_suites_stage",)),
    Mutant("e3bm36", "E3B2 review F2: a relation's table-level write grants are pinned",
           "tests/integration/pgstate.py",
           '                     "infrx.provider_memberships", "infrx.provider_orgs",\n',
           '                     "infrx.provider_orgs",\n',
           "tests/integration/test_services.py", "role_matrix_holds", layer=2,
           cases=("test_the_role_matrix_holds_for_every_role",)),
    Mutant("e3bm37", "E3B2 review H1: a suite under apps/infrx-api runs against a copied infrx",
           "tests/integration/mutants.py",
           ' or mutant.suite.startswith("apps/infrx-api/"):\n',
           ":\n",
           "tests/integration/test_run.py", "copied_infrx",
           cases=("test_a_suite_under_the_api_tree_runs_against_a_copied_infrx",)),
)


def all_mutants() -> tuple[Mutant, ...]:
    """E's list plus I3B's (`backend/recovery/mutants_i3b.py`, I3B request 8): one runner,
    one mutation stage. Imported here, not at module level: that list imports this module."""
    recovery = str(harness.HERE / "backend" / "recovery")
    if recovery not in sys.path:
        sys.path.insert(0, recovery)
    import mutants_i3b
    return MUTANTS + tuple(mutants_i3b.MUTANTS)


def _copy_trees(destination: Path) -> None:
    for tree in OWNED_TREES:
        shutil.copytree(harness.REPO_ROOT / tree, destination / tree,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _temp_litter() -> set:
    """Files in the temp directory carrying our prefix, minus the state file, which is ours."""
    root = Path(tempfile.gettempdir())
    # Review H4: fake_vllm's log keeps E2's literal prefix in every namespace (it cannot import
    # the harness: W3's runner copies it alone), so it is swept by that name as well.
    return {path for path in (*root.glob(f"{harness.PROJECT}-*"),
                              *root.glob("infrx-e2-fake-vllm-*"))
            if path != harness.STATE_FILE}


def run_one(mutant: Mutant, *, stack_available: bool) -> dict:
    if mutant.layer == 2 and not stack_available:
        return {"id": mutant.id, "status": "pending", "why": "layer 2: no live stack",
                "invariant": mutant.invariant}
    # A mutant is broken code by construction, so it may leak what the real code cannot: e2m54
    # reintroduces the leaked server log, and every server the copy starts then leaves a file
    # behind, not only the one the guarded case watches. Anything new under our own prefix is
    # removed afterwards - never anything that was there before, and never the state file.
    litter_before = _temp_litter()
    with tempfile.TemporaryDirectory(prefix=f"{harness.PROJECT}-{mutant.id}-") as tmp:
        root = Path(tmp)
        _copy_trees(root)
        # E3B: a defect in module code is injected into a copy of `infrx`, which the suite
        # then imports through PYTHONPATH instead of the checkout's.
        api_root = harness.API_ROOT
        # E3B phase 2 (found by the pristine baseline, review H1): a suite under
        # apps/infrx-api/tests resolves `infrx` beside ITSELF (tests/d spawns children with
        # PYTHONPATH=<its api root>), so the copy needs the package too, or the unmutated
        # case fails in the copy and every "kill" of it was vacuous (e2m64-66).
        if mutant.path.startswith(API_TREE + "/") or mutant.suite.startswith("apps/infrx-api/"):
            shutil.copytree(harness.REPO_ROOT / API_TREE, root / API_TREE,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            api_root = root / "apps" / "infrx-api"
        target = root / mutant.path
        source = target.read_text()
        found = source.count(mutant.before)
        if found != mutant.occurrences:
            return {"id": mutant.id, "status": "stale", "invariant": mutant.invariant,
                    "why": f"the mutated text occurs {found} times, expected "
                           f"{mutant.occurrences}: the mutant no longer describes the code"}
        # R83 pristine baseline (E3B phase 2, review H1): the named cases must PASS on this
        # copy BEFORE the edit, or a "kill" is only the case's own red. Once per distinct
        # (suite, selector, copy kind) in this process.
        key = (mutant.suite, mutant.select, api_root != harness.API_ROOT)
        if key not in BASELINES:
            BASELINES[key] = _baseline(mutant, *_pytest(root, mutant, api_root))
        red = BASELINES[key]
        if red is not None:
            return {"id": mutant.id, "status": "baseline-red", "invariant": mutant.invariant,
                    "must_survive": mutant.must_survive, "why": red}
        target.write_text(source.replace(mutant.before, mutant.after, mutant.occurrences))
        verdict = _verdict(mutant, *_pytest(root, mutant, api_root))
    litter = sorted(str(path) for path in _temp_litter() - litter_before)
    for path in litter:
        pathlib_path = Path(path)
        if pathlib_path.is_dir():
            shutil.rmtree(pathlib_path, ignore_errors=True)
        else:
            pathlib_path.unlink(missing_ok=True)
    if litter:
        verdict["temp_litter_removed"] = litter
    if mutant.dirties_database:
        verdict["reprovisioned"] = _reprovision()
    return verdict


BASELINES: dict[tuple, str | None] = {}


def _pytest(root: Path, mutant: Mutant, api_root: Path) -> tuple[int, str]:
    """The mutant's named cases, run in the copy; (exit code, output)."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(root / mutant.suite),
         "-k", mutant.select, "-p", "no:cacheprovider", "--no-header", "-x"],
        cwd=str(root), capture_output=True, text=True, timeout=240,
        env={**os.environ, "INFRX_E2_REPO_ROOT": str(harness.REPO_ROOT),
             # `infrx` (the pinned contracts package) always comes from the real
             # checkout; only the owned trees above are the copy's.
             "PYTHONPATH": str(api_root),
             # The copy must claim the provisioning checkout's identity or B1's ownership
             # label correctly makes the live stack foreign, and every layer-2 mutant is
             # skipped instead of killed.
             "INFRX_E2_CHECKOUT": harness.working_dir(),
             "INFRX_E2_CANARY": "off", "PYTHONDONTWRITEBYTECODE": "1"})
    return result.returncode, result.stdout + result.stderr


def _baseline(mutant: Mutant, code: int, output: str) -> str | None:
    """None when the unmutated cases pass; else why this mutant cannot be judged."""
    summary = _summary(output)
    ran = re.search(r"\d+ (passed|failed|errors?)\b", summary)
    if not ran and summary:
        return None               # the selector matched nothing: `_verdict` says no-cases
    if code == 0 and not re.search(r"\d+ (failed|errors?)\b", summary):
        return None
    return (f"the unmutated copy is already red on {mutant.select!r} ({summary or 'no '
            f'summary, exit {code}'}): a failure under the mutant would prove nothing")


def _reprovision() -> str:
    """Put the shared database back after a mutant that was allowed to commit (r2 B1).

    Only `dirties_database` mutants reach this, and only they can: every other check runs in
    its own rolled-back transaction, which is the invariant e2m56 exists to prove.
    """
    import run as runner
    state = harness.load_state() or {}
    harness.provision_database()
    report = runner.Report()
    fixtures = runner.migrate(report, int(state.get("seed", 20260921)))
    if fixtures is None:
        raise RuntimeError(f"could not re-provision after a dirtying mutant: {report.stages}")
    return f"database {harness.PG_DATABASE} recreated and reseeded (seed {fixtures.seed})"


# pytest's own terminal summary, e.g. `1 failed, 30 passed in 1.23s` or `1 error in 0.4s`.
# The LAST such line is the run's verdict; anything else in the output is a traceback.
SUMMARY_LINE = re.compile(r"^(?:(?:\d+ [a-z]+(?:, )?)+|no tests ran) in [\d.]+s.*$", re.M)


def _summary(output: str) -> str:
    matches = SUMMARY_LINE.findall(output)
    return matches[-1] if matches else ""


def _verdict(mutant: Mutant, code: int, output: str) -> dict:
    """R40 / r1 review B3: a kill is an ASSERTION FAILURE, not "the process exited non-zero".

    The old criterion was `returncode != 0`, and in a temporary copy the fake-vLLM child could
    not import `infrx`, so every engine mutant "died" of a `ModuleNotFoundError` in
    `setup_module` - a collection **error**, and a comment-only edit died the same way. A run
    therefore only counts as a kill when pytest reports `failed` and no `error`, and a run
    that selected nothing is neither a kill nor a survival but a broken selector.
    """
    # Counts come from pytest's SUMMARY LINE, never from anywhere in the output: a traceback
    # quotes the failing test's own source, and a test that necessarily carries canned pytest
    # output in it (`test_the_verdict_reads_pytests_own_summary_and_not_the_exit_code` does)
    # would otherwise be read as "1 error" and misclassified `setup-error` (r2 review B2).
    summary = _summary(output)
    failed = re.search(r"(\d+) failed", summary)
    errors = re.search(r"(\d+) errors?\b", summary)
    # ALSO read off the summary line only. A traceback quotes the failing test's source, and
    # the case that guards this function necessarily contains the literal "no tests ran" - so
    # scanning the whole output turned a genuine kill into `no-cases` (measured: e2m47 and
    # e2m50 reported no-cases while their summary said "1 failed").
    nothing_ran = ("no tests ran" in summary
                   # `-k` that matches nothing prints only "N deselected": no test ran, so the
                   # mutant was never exercised and calling that a survival would be a lie.
                   or (re.search(r"\d+ deselected", summary)
                       and not re.search(r"\d+ (passed|failed)", summary)))
    detail = {"id": mutant.id, "invariant": mutant.invariant, "exit": code,
              "selected_cases": mutant.cases, "must_survive": mutant.must_survive,
              "summary": summary,
              "failed": int(failed.group(1)) if failed else 0,
              "errors": int(errors.group(1)) if errors else 0,
              "tail": "\n".join(output.strip().splitlines()[-6:])}
    if nothing_ran:
        return {**detail, "status": "no-cases",
                "why": f"the selector {mutant.select!r} matched nothing: not a kill"}
    if detail["errors"] or (code != 0 and not summary):
        return {**detail, "status": "setup-error",
                "why": "pytest reported an ERROR, or no summary at all, rather than a failure: "
                       "the suite could not run, so this says nothing about the invariant"}
    killed = code != 0 and detail["failed"] > 0
    if mutant.must_survive:
        return {**detail, "status": "SURVIVED" if not killed else "CONTROL-KILLED",
                "why": None if not killed else
                       "a no-op edit was reported killed: the runner is measuring its own "
                       "setup, so every other kill it reports is worthless"}
    return {**detail, "status": "killed" if killed else "SURVIVED"}


def summarise(results: list[dict]) -> dict:
    """The one place the verdict is counted, so `run.py`'s stage and this CLI cannot disagree.

    A control that SURVIVED is a pass; anything else that survived, or that could not be driven
    at all, is a failure of this list rather than of the code (R40 / r1 B3). A `pending` layer-2
    mutant is neither.
    """
    pending = [r for r in results if r["status"] == "pending"]
    bad = [r for r in results
           if r not in pending
           and ((r["status"] != "killed" and not r.get("must_survive"))
                or r["status"] in ("CONTROL-KILLED", "stale", "setup-error", "no-cases",
                                   "baseline-red"))]
    controls = [r for r in results if r.get("must_survive") and r["status"] == "SURVIVED"]
    return {"mutants": len(results),
            "killed": sum(1 for r in results if r["status"] == "killed"),
            "controls_survived": len(controls), "not_killed": len(bad),
            "pending": len(pending), "problems": [r["id"] for r in bad] or None,
            "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--layer", choices=("1", "2", "all"), default="1")
    parser.add_argument("--only", help="a single mutant id")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    if args.list:
        for mutant in all_mutants():
            print(f"{mutant.id}  layer {mutant.layer}  "
                  f"{'CONTROL ' if mutant.must_survive else ''}{mutant.path}\n"
                  f"        {mutant.invariant}")
        print(f"\n{len(all_mutants())} mutants")
        return 0

    wanted = [m for m in all_mutants()
              if (args.layer == "all" or m.layer == int(args.layer))
              and (args.only is None or m.id == args.only)]
    stack = bool(harness.load_state()) and bool(harness.docker_available()[0]) \
        and bool(harness.owned_containers() if harness.docker_available()[0] else [])
    results = [run_one(mutant, stack_available=stack) for mutant in wanted]
    for result in results:
        print(f"[{result['status']:>13}] {result['id']}  {result['invariant']}", flush=True)
    summary = summarise(results)
    bad = [r for r in results if r["id"] in (summary["problems"] or ())]
    print(json.dumps(summary, indent=2))
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
