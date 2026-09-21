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
OWNED_TREES = ("tests/integration", "models/marlin2b")


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
    Mutant("e2m35", "r1 B2: a failing suite fails the run",
           "tests/integration/run.py",
           '    failed = [run for run in runs if run["exit"] != 0]',
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
           'CLOCK_SCHEMA = "infrx_e2_test"',
           'CLOCK_SCHEMA = "public"',
           "tests/integration/test_harness.py", "movable_clock",
           cases=("test_the_movable_clock_cannot_exist_in_a_deployed_database",)),

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
    Mutant("e2m18", "database time moves only inside a transaction that asks for it",
           "tests/integration/pgstate.py",
           '    conn.execute("select set_config(%s, %s, %s)", (CLOCK_GUC, str(float(seconds)), local))',
           '    conn.execute("select set_config(%s, %s, false)", (CLOCK_GUC, str(float(seconds))))',
           "tests/integration/test_services.py", "database_time",
           layer=2,
           cases=("test_database_time_moves_only_inside_a_transaction_that_asks_for_it",)),
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
    Mutant("e2m27", "r1 R-a: the template copy is owned by postgres, or migrations cannot run",
           "tests/integration/harness.py",
           "                             f\"template {PG_TEMPLATE_SOURCE} owner {PG_USER}\")],",
           "                             f\"template {PG_TEMPLATE_SOURCE}\")],",
           "tests/integration/test_run.py", "provision_database_statements",
           cases=("test_provision_database_statements_are_the_ones_r_a_requires",)),
)


def _copy_trees(destination: Path) -> None:
    for tree in OWNED_TREES:
        shutil.copytree(harness.REPO_ROOT / tree, destination / tree,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def run_one(mutant: Mutant, *, stack_available: bool) -> dict:
    if mutant.layer == 2 and not stack_available:
        return {"id": mutant.id, "status": "pending", "why": "layer 2: no live stack",
                "invariant": mutant.invariant}
    with tempfile.TemporaryDirectory(prefix=f"infrx-e2-{mutant.id}-") as tmp:
        root = Path(tmp)
        _copy_trees(root)
        target = root / mutant.path
        source = target.read_text()
        found = source.count(mutant.before)
        if found != mutant.occurrences:
            return {"id": mutant.id, "status": "stale", "invariant": mutant.invariant,
                    "why": f"the mutated text occurs {found} times, expected "
                           f"{mutant.occurrences}: the mutant no longer describes the code"}
        target.write_text(source.replace(mutant.before, mutant.after, mutant.occurrences))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(root / mutant.suite),
             "-k", mutant.select, "-p", "no:cacheprovider", "--no-header", "-x"],
            cwd=str(root), capture_output=True, text=True, timeout=240,
            env={**os.environ, "INFRX_E2_REPO_ROOT": str(harness.REPO_ROOT),
                 # The copy must claim the provisioning checkout's identity or B1's ownership
                 # label correctly makes the live stack foreign, and every layer-2 mutant is
                 # skipped instead of killed.
                 "INFRX_E2_CHECKOUT": harness.working_dir(),
                 "INFRX_E2_CANARY": "off", "PYTHONDONTWRITEBYTECODE": "1"})
        output = result.stdout + result.stderr
        return _verdict(mutant, result.returncode, output)


def _verdict(mutant: Mutant, code: int, output: str) -> dict:
    """R40 / r1 review B3: a kill is an ASSERTION FAILURE, not "the process exited non-zero".

    The old criterion was `returncode != 0`, and in a temporary copy the fake-vLLM child could
    not import `infrx`, so every engine mutant "died" of a `ModuleNotFoundError` in
    `setup_module` - a collection **error**, and a comment-only edit died the same way. A run
    therefore only counts as a kill when pytest reports `failed` and no `error`, and a run
    that selected nothing is neither a kill nor a survival but a broken selector.
    """
    failed = re.search(r"(\d+) failed", output)
    errors = re.search(r"(\d+) error", output)
    nothing_ran = ("no tests ran" in output
                   or re.search(r"^0 selected", output, re.M)
                   # `-k` that matches nothing prints only "N deselected": no test ran, so the
                   # mutant was never exercised and calling that a survival would be a lie.
                   or (re.search(r"\d+ deselected", output)
                       and not re.search(r"\d+ (passed|failed)", output)))
    detail = {"id": mutant.id, "invariant": mutant.invariant, "exit": code,
              "selected_cases": mutant.cases, "must_survive": mutant.must_survive,
              "failed": int(failed.group(1)) if failed else 0,
              "errors": int(errors.group(1)) if errors else 0,
              "tail": "\n".join(output.strip().splitlines()[-6:])}
    if nothing_ran:
        return {**detail, "status": "no-cases",
                "why": f"the selector {mutant.select!r} matched nothing: not a kill"}
    if detail["errors"]:
        return {**detail, "status": "setup-error",
                "why": "pytest reported an ERROR, not a failure: the suite could not run, so "
                       "this says nothing about the invariant"}
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
                or r["status"] in ("CONTROL-KILLED", "stale", "setup-error", "no-cases"))]
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
        for mutant in MUTANTS:
            print(f"{mutant.id}  layer {mutant.layer}  "
                  f"{'CONTROL ' if mutant.must_survive else ''}{mutant.path}\n"
                  f"        {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants")
        return 0

    wanted = [m for m in MUTANTS
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
