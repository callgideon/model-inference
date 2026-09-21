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


MUTANTS: tuple[Mutant, ...] = (
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
           "        passed = outcome == \"error\" and observed == expected\n"
           "    else:\n"
           "        passed = outcome == \"ok\" and _same(observed, expected)",
           "        passed = True\n    else:\n        passed = True",
           "tests/integration/test_services.py", "should_fail or role_matrix",
           layer=2, cases=("test_a_check_that_should_fail_does_fail",)),
    Mutant("e2m17", "an RLS refusal is checked against its SQLSTATE, not just 'it errored'",
           "tests/integration/pgstate.py",
           '        passed = outcome == "error" and observed == expected',
           '        passed = outcome == "error"',
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
           "    if container not in owned_containers():",
           "    if False:",
           "tests/integration/test_services.py", "cleanup_is_scoped or prefix_alone",
           layer=2, cases=("test_cleanup_is_scoped_and_refuses_a_container_it_did_not_create",)),
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
            cwd=str(root), capture_output=True, text=True, timeout=900,
            env={**os.environ, "INFRX_E2_REPO_ROOT": str(harness.REPO_ROOT),
                 "INFRX_E2_CANARY": "off", "PYTHONDONTWRITEBYTECODE": "1"})
        output = result.stdout + result.stderr
        selected = "no tests ran" not in output
        killed = result.returncode != 0 and selected
        return {"id": mutant.id, "status": "killed" if killed else "SURVIVED",
                "invariant": mutant.invariant, "exit": result.returncode,
                "selected_cases": mutant.cases,
                "tail": "\n".join(output.strip().splitlines()[-6:])}


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
            print(f"{mutant.id}  layer {mutant.layer}  {mutant.path}\n"
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
        print(f"[{result['status']:>8}] {result['id']}  {result['invariant']}", flush=True)
    survived = [r for r in results if r["status"] in ("SURVIVED", "stale")]
    pending = [r for r in results if r["status"] == "pending"]
    summary = {"mutants": len(results), "killed": len(results) - len(survived) - len(pending),
               "survived": len(survived), "pending": len(pending), "results": results}
    print(json.dumps(summary, indent=2))
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2))
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
