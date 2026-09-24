#!/usr/bin/env python3
"""R32/R40 for E1B: every invariant this task claims must be killable by ONE edit.

    apps/infrx-api/.venv/bin/python models/marlin2b/tests/mutants.py --list
    apps/infrx-api/.venv/bin/python models/marlin2b/tests/mutants.py

Same shape as `tests/integration/mutants.py` (E2's runner), minus the parts that need a
live stack: nothing is mutated in place — each run copies `models/marlin2b/` into a
temporary directory, edits one line there, and runs the named cases inside the copy.

A kill is an **assertion failure**, never "the process exited non-zero": a collection
error, an import error or a selector that matched nothing says nothing about the
invariant and is reported as such. Two CONTROL mutants make no behavioural change and
must SURVIVE; if a no-op edit comes back killed, every other kill here is worthless.
"""
from __future__ import annotations

import argparse, json, os, re, shutil, subprocess, sys, tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREE = HERE.parent                                   # models/marlin2b
SUITE = "tests"                                      # relative to the copied tree


@dataclass(frozen=True)
class Mutant:
    id: str
    invariant: str
    path: str                     # relative to models/marlin2b
    before: str
    after: str
    select: str                   # pytest -k expression
    cases: tuple[str, ...] = field(default_factory=tuple)
    occurrences: int = 1
    must_survive: bool = False


MUTANTS: tuple[Mutant, ...] = (
    # ---------------- controls (must survive)
    Mutant("e1bc01", "CONTROL: a comment-only edit in bench.py changes nothing",
           "bench.py", 'IDEMPOTENCY_PREFIX = "sop1."',
           'IDEMPOTENCY_PREFIX = "sop1."  # control: no behaviour change',
           "item_key or resume or tenant", must_survive=True),
    Mutant("e1bc03", "CONTROL: the copy layout does not by itself fail the CLI default-path case",
           "bench.py", 'DEFAULT_OUT = os.path.join(HERE, "results", "bench.jsonl")',
           'DEFAULT_OUT = os.path.join(HERE, "results", "bench.jsonl")  # control',
           "historical_cli", must_survive=True),
    Mutant("e1bc02", "CONTROL: a comment-only edit in synth.py changes nothing",
           "corpus-synth/synth.py", 'MEDIA_SUBDIR = "sop-synth-v1"',
           'MEDIA_SUBDIR = "sop-synth-v1"  # control: no behaviour change',
           "manifest or validator or bit_exact", must_survive=True),

    # ---------------- E1B.a: the reference form and the item identity
    Mutant("e1bm01", "the upload reference is infrx-upload:upl_… and nothing else (R61)",
           "bench.py", 'UPLOAD_REF_SCHEME = "infrx-upload:"', 'UPLOAD_REF_SCHEME = "upload://"',
           "request_forms",
           cases=("test_request_forms_and_upload_flow",)),
    Mutant("e1bm02", "a handle that is not a contract handle is never sent back as a reference",
           "bench.py", 'HANDLE_OK = re.compile(r"upl_[A-Za-z0-9_-]{22,64}")',
           'HANDLE_OK = re.compile(r"[A-Za-z0-9_-]{1,72}")',
           "contract_handle",
           cases=("test_an_upload_handle_that_is_not_a_contract_handle_is_never_sent_back",)),
    Mutant("e1bm03", "every field of the SOP recipe changes the item key",
           "bench.py",
           "    parts = (dataset_version, source_id, episode_id, str(segment_index),\n"
           "             f\"{start_s}-{end_s}\", prompt_version, profile_version)",
           "    parts = (dataset_version, source_id, episode_id, str(segment_index),\n"
           "             f\"{start_s}-{end_s}\", prompt_version)",
           "item_key",
           cases=("test_item_key_is_the_sop_recipe_and_a_function_of_the_payload_only",)),
    Mutant("e1bm04", "the unit separator keeps neighbouring fields from colliding",
           "bench.py", 'return sha256("\\x1f".join(parts).encode("utf-8"))',
           'return sha256("".join(parts).encode("utf-8"))',
           "item_key",
           cases=("test_item_key_is_the_sop_recipe_and_a_function_of_the_payload_only",)),
    Mutant("e1bm05", "two scheduled copies of one clip are two items, not one replayed",
           "bench.py", "        segment = occurrences.get(source_id, 0)",
           "        segment = 0",
           "distinct_idempotency",
           cases=("test_every_scheduled_item_carries_a_distinct_idempotency_key",)),

    # ---------------- E1B.a: MARLIN-SOP resume
    Mutant("e1bm06", "every request carries its Idempotency-Key, so a resume cannot duplicate",
           "bench.py",
           '    headers = {**cfg["headers_for"](item["tenant"]),\n'
           '               "idempotency-key": item["idempotency_key"]}',
           '    headers = {**cfg["headers_for"](item["tenant"])}',
           "second_accepted_item or reuses_its_handle",
           cases=("test_resume_after_a_lost_ack_creates_no_second_accepted_item",
                  "test_a_resumed_upload_item_reuses_its_handle_instead_of_conflicting")),
    Mutant("e1bm07", "a failed item is not terminal: a resume must re-send it",
           "bench.py",
           '    if row.get("outcome") in ("accepted", CANCELLED_REPLAY):\n        return True',
           '    if row.get("outcome") in ("accepted", CANCELLED_REPLAY, "failed"):\n        return True',
           "second_accepted_item",
           cases=("test_resume_after_a_lost_ack_creates_no_second_accepted_item",)),
    Mutant("e1bm08", "a resumed upload item re-uses its staged handle instead of re-staging",
           "bench.py",
           '        if row is not None and row.get("upload_handle"):\n'
           '            item["upload_handle"] = row["upload_handle"]',
           '        if False:\n'
           '            item["upload_handle"] = row["upload_handle"]',
           "reuses_its_handle",
           cases=("test_a_resumed_upload_item_reuses_its_handle_instead_of_conflicting",)),
    Mutant("e1bm09", "each tenant sends its own key, so the idempotency scope is per org",
           "bench.py", "        value = keys[tenant % len(keys)] if keys else \"\"",
           "        value = keys[0] if keys else \"\"",
           "two_tenants",
           cases=("test_two_tenants_may_hold_identical_item_keys",)),

    # ---------------- E1B.b: the driver
    Mutant("e1bm10", "a burst is several arrivals at one instant, at the same mean rate",
           "bench.py", "            if i % burst == 0:", "            if True:",
           "bursty",
           cases=("test_bursty_arrivals_are_bursts_and_keep_the_mean_rate",)),
    Mutant("e1bm11", "a cancelled request is neither accepted nor failed",
           "bench.py",
           '                row["outcome"], row["error_class"] = "cancelled", "client_cancelled"',
           '                row["outcome"], row["error_class"] = "accepted", "client_cancelled"',
           "cancellation",
           cases=("test_cancellation_is_its_own_outcome_and_stays_in_the_denominators",)),
    Mutant("e1bm12", "a phase the target does not publish is named, not inferred",
           "bench.py", '"declared_missing": [p for p in PHASES if p not in seen]',
           '"declared_missing": []',
           "phase_timings",
           cases=("test_phase_timings_come_from_server_timing_and_absent_phases_are_named",)),
    Mutant("e1bm13", "a soak always has a last resource sample to compare with its first",
           "bench.py",
           '        row = {"kind": "resource_sample", "t_s": round(CLOCK() - t0, 6), '
           '"final": True, **sampler()}\n        samples.append(row)\n        write_row(cfg, row)\n'
           '        raise',
           '        raise',
           "resource_samples",
           cases=("test_resource_samples_are_written_to_the_raw_file_and_summarised",)),
    Mutant("e1bm14", "successful work is counted in video-seconds that really went out",
           "bench.py",
           '    video_seconds = sum(r["duration_s"] or 0 for r in accepted if r["media_sent"])',
           '    video_seconds = sum(r["duration_s"] or 0 for r in accepted)',
           "declared_profile",
           cases=("test_the_declared_profile_carries_every_axis_a_throughput_number_needs",)),
    Mutant("e1bm15", "the report refuses a tail the sample count cannot support",
           "bench.py",
           '        cell = lambda block, q: ("—" if (pct.get(block) or {}).get(f"p{q}") is None',
           '        cell = lambda block, q: ((pct.get(block) or {}).get(f"p{q}") if False',
           "report_refuses",
           cases=("test_the_report_refuses_unsupported_tails_and_names_every_cell_limit",)),
    Mutant("e1bm16", "the sample-sufficiency rule is p50>=6 / p95>=60 / p99>=300",
           "bench.py",
           "MIN_TAIL = 3            # a reported quantile needs this many samples strictly beyond it",
           "MIN_TAIL = 1            # a reported quantile needs this many samples strictly beyond it",
           "suppressed or predeclared_protocol",
           cases=("test_percentiles_are_suppressed_when_samples_cannot_support_them",
                  "test_the_predeclared_protocol_matches_the_client_that_implements_it")),

    Mutant("e1bm24", "a pre-E1B summary row is listed apart, not read as an empty cell",
           "bench.py", '    return "denominators" not in cell or "profile" not in cell',
           "    return False",
           "report_refuses",
           cases=("test_the_report_refuses_unsupported_tails_and_names_every_cell_limit",)),

    # ---------------- the review's non-blocking items, each with its own case
    Mutant("n05", "only an attributable failure feeds the platform-caused criterion",
           "bench.py",
           '    return row.get("outcome") == "failed" and (\n'
           '        (isinstance(status, int) and status >= 500)\n'
           '        or row.get("error_class") in PLATFORM_ERROR_CLASSES)',
           '    return row.get("outcome") == "failed"',
           "unattributable",
           cases=("test_an_unattributable_failure_is_not_counted_as_platform_caused",)),
    Mutant("n07", "a resumed run makes no cold/warm claim",
           "bench.py", '    for item in schedule:\n        item["cold"] = None',
           '    for item in []:\n        item["cold"] = None',
           "no_cold_warm_claim",
           cases=("test_a_resumed_run_makes_no_cold_warm_claim",)),
    Mutant("n08", "a resume across a changed identity knob is refused",
           "bench.py",
           "    differing = [name for name in FINGERPRINT_FIELDS "
           "if previous.get(name) != current[name]]",
           "    differing = []",
           "run_profile",
           cases=("test_the_raw_file_declares_its_run_profile_and_a_mismatched_resume_is_refused",)),

    # ---------------- the reviewer's surviving mutants (review of 2cf7a81), now killable
    Mutant("r05", "a target that publishes no Server-Timing gets no invented phase",
           "bench.py", "    return out or None", '    return out or {"queue": 0.0}',
           "no_phase_timings",
           cases=("test_a_target_that_publishes_no_phase_timings_says_so",)),
    Mutant("r06", "the handle minimum is 22 characters, one short is refused",
           "bench.py", 'HANDLE_OK = re.compile(r"upl_[A-Za-z0-9_-]{22,64}")',
           'HANDLE_OK = re.compile(r"upl_[A-Za-z0-9_-]{21,64}")',
           "handle_grammar",
           cases=("test_the_handle_grammar_is_enforced_at_both_edges",)),
    Mutant("r19", "a handle may not carry a dot or a slash, whatever its length",
           "bench.py", 'HANDLE_OK = re.compile(r"upl_[A-Za-z0-9_-]{22,64}")',
           r'HANDLE_OK = re.compile(r"upl_[\S]{22,64}")',
           "handle_grammar",
           cases=("test_the_handle_grammar_is_enforced_at_both_edges",)),
    Mutant("r08", "EVERY tenant's key is checked, not just the first",
           "bench.py",
           "    if isinstance(key, (list, tuple, set, frozenset)):\n"
           "        return any(carries_key(value, one) for one in key)",
           "    if False:\n"
           "        return any(carries_key(value, one) for one in key)",
           "two_tenants",
           cases=("test_two_tenants_may_hold_identical_item_keys",)),
    Mutant("r13", "a 429 is resumable, not terminal",
           "bench.py",
           "TERMINAL_REJECT_STATUS = {400, 401, 403, 404, 409, 410, 413, 415, 422}",
           "TERMINAL_REJECT_STATUS = {400, 401, 403, 404, 409, 410, 413, 415, 422, 429}",
           "rejected_429",
           cases=("test_a_rejected_429_item_is_resumed_with_the_same_key",)),
    Mutant("r11", "the concurrency sweep stays inside the engine's own --max-num-seqs",
           "results/E1B-protocol.md", "`--target direct -c 1,2,4,8,16,32`",
           "`--target direct -c 1,2,4,8,16,64`",
           "predeclared_protocol",
           cases=("test_the_predeclared_protocol_matches_the_client_that_implements_it",)),

    # ---------------- E1B.c: the pre-registration cannot drift
    Mutant("e1bm17", "the protocol's frozen seed is the seed the runs use",
           "results/E1B-protocol.md", "`--seed 20260922`", "`--seed 7`",
           "predeclared_protocol",
           cases=("test_the_predeclared_protocol_matches_the_client_that_implements_it",)),
    Mutant("e1bm18", "the absent latency criterion stays absent",
           "results/E1B-protocol.md", "| **explicitly absent** |", "| **provisional (P-18)** |",
           "predeclared_protocol",
           cases=("test_the_predeclared_protocol_matches_the_client_that_implements_it",)),

    # ---------------- E1B.a: sop-synth-v1
    Mutant("e1bm19", "a step declared absent is never drawn",
           "corpus-synth/synth.py",
           '        steps = [s for s in steps if s["canonical_index"] != ABSENT_CANONICAL_INDEX]',
           '        steps = list(steps)',
           "bit_exact or committed_manifest or each_case",
           cases=("test_the_render_command_is_bit_exact_and_derives_only_from_the_script",)),
    Mutant("e1bm20", "the render is bit-exact and single-threaded, or the sha256 is not a pin",
           "corpus-synth/synth.py",
           '            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-movflags", "+faststart",',
           '            "-movflags", "+faststart",',
           "bit_exact",
           cases=("test_the_render_command_is_bit_exact_and_derives_only_from_the_script",)),
    Mutant("e1bm21", "only the declared clip may put two steps inside one frame period",
           "corpus-synth/synth.py", "        inside = [g for g in gaps if g < period]",
           "        inside = []",
           "validator",
           cases=("test_the_validator_rejects_every_way_the_script_can_go_wrong",)),
    Mutant("e1bm22", "a boundary crossing is recorded whether or not it is the declared case",
           "corpus-synth/synth.py",
           '        if c.get("boundary_spanning_step_ids") != spanning:',
           '        if False:',
           "validator",
           cases=("test_the_validator_rejects_every_way_the_script_can_go_wrong",)),
    Mutant("e1bm23", "a missing prerequisite is named exactly, and nothing reaches the network",
           "corpus-synth/synth.py",
           "    if not (ffmpeg.exists() and ffprobe.exists()) and not allow_download:",
           "    if False:",
           "missing_prerequisite",
           cases=("test_a_missing_prerequisite_names_the_exact_tool_and_never_reaches_the_network",)),
    # ---------------- CERTIFY-TREE item 5: the cancelled-replay rule (R106)
    Mutant("e1bm25", "a replay answered state_conflict is terminal: its key is spent",
           "bench.py", '    if row.get("outcome") in ("accepted", CANCELLED_REPLAY):',
           '    if row.get("outcome") in ("accepted",):',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    Mutant("e1bm26", "a replay answered state_conflict is classified, not left a failure",
           "bench.py", "        row[\"outcome\"] = CANCELLED_REPLAY\n", "        pass\n",
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    Mutant("e1bm27", "only a replay's state_conflict is the cancelled replay, never a fresh one's",
           "bench.py", '            "failed", "stream_error_event", "state_conflict", True):',
           '            "failed", "stream_error_event", "state_conflict", row["idempotency_replayed"]):',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    Mutant("e1bm28", "only state_conflict is the cancelled replay, never a replay's other error",
           "bench.py", '            "failed", "stream_error_event", "state_conflict", True):',
           '            "failed", "stream_error_event", row["error_code"], True):',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    Mutant("e1bm29", "the summary counts the cancelled replays",
           "bench.py", '"cancelled": len(cancelled), CANCELLED_REPLAY: len(cancelled_replays),',
           '"cancelled": len(cancelled), CANCELLED_REPLAY: 0,',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
    # ---------------- CERTIFY-POLISH N7: only a stream event is the cancelled replay
    Mutant("e1bm30", "only a replay whose STREAM answered state_conflict is the cancelled replay",
           "bench.py", '            "failed", "stream_error_event", "state_conflict", True):',
           '            "failed", row["error_class"], "state_conflict", True):',
           "cancelled_by_the_interruption",
           cases=("test_a_replay_answered_state_conflict_is_terminal_as_cancelled_by_the_interruption",)),
)


SUMMARY_LINE = re.compile(r"^(?:(?:\d+ [a-z]+(?:, )?)+|no tests ran) in [\d.]+s.*$", re.M)


def run_one(mutant: Mutant) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"infrx-e1b-{mutant.id}-") as tmp:
        # models/marlin2b, not marlin2b: bench.DEFAULT_OUT is derived from the file's own
        # path, and test_historical_cli_still_parses… asserts it ends with
        # models/marlin2b/results/bench.jsonl. A copy one level shallower failed that case in
        # EVERY run, so any mutant whose selector reached it was killed by the copy layout
        # rather than by the edit. Control e1bc03 guards exactly that.
        root = Path(tmp) / "models" / "marlin2b"
        shutil.copytree(TREE, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        target = root / mutant.path
        source = target.read_text()
        found = source.count(mutant.before)
        if found != mutant.occurrences:
            return {"id": mutant.id, "status": "stale", "invariant": mutant.invariant,
                    "why": f"the mutated text occurs {found} times, expected "
                           f"{mutant.occurrences}: the mutant no longer describes the code"}
        target.write_text(source.replace(mutant.before, mutant.after, mutant.occurrences))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(root / SUITE), "-k", mutant.select,
             "-p", "no:cacheprovider", "--no-header", "-x"],
            cwd=str(root), capture_output=True, text=True, timeout=600,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return verdict(mutant, result.returncode, result.stdout + result.stderr)


def verdict(mutant: Mutant, code: int, output: str) -> dict:
    matches = SUMMARY_LINE.findall(output)
    summary = matches[-1] if matches else ""
    failed = re.search(r"(\d+) failed", summary)
    errors = re.search(r"(\d+) errors?\b", summary)
    nothing_ran = ("no tests ran" in summary
                   or (re.search(r"\d+ deselected", summary)
                       and not re.search(r"\d+ (passed|failed)", summary)))
    detail = {"id": mutant.id, "invariant": mutant.invariant, "exit": code,
              "selected_cases": mutant.cases, "must_survive": mutant.must_survive,
              "summary": summary, "failed": int(failed.group(1)) if failed else 0,
              "errors": int(errors.group(1)) if errors else 0,
              "tail": "\n".join(output.strip().splitlines()[-4:])}
    if nothing_ran:
        return {**detail, "status": "no-cases",
                "why": f"the selector {mutant.select!r} matched nothing: not a kill"}
    if detail["errors"] or (code != 0 and not summary):
        return {**detail, "status": "setup-error",
                "why": "pytest reported an ERROR rather than a failure: the suite could not "
                       "run, so this says nothing about the invariant"}
    killed = code != 0 and detail["failed"] > 0
    if mutant.must_survive:
        return {**detail, "status": "SURVIVED" if not killed else "CONTROL-KILLED",
                "why": None if not killed else
                       "a no-op edit was reported killed: the runner is measuring its own "
                       "setup, so every other kill it reports is worthless"}
    return {**detail, "status": "killed" if killed else "SURVIVED"}


def summarise(results: list[dict]) -> dict:
    bad = [r for r in results
           if (r["status"] != "killed" and not r.get("must_survive"))
           or r["status"] in ("CONTROL-KILLED", "stale", "setup-error", "no-cases")]
    return {"mutants": len(results),
            "killed": sum(1 for r in results if r["status"] == "killed"),
            "controls_survived": sum(1 for r in results
                                     if r.get("must_survive") and r["status"] == "SURVIVED"),
            "not_killed": len(bad), "problems": [r["id"] for r in bad] or None,
            "results": results}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", help="a single mutant id")
    ap.add_argument("--report", type=Path)
    a = ap.parse_args(argv)
    if a.list:
        for m in MUTANTS:
            print(f"{m.id}  {'CONTROL ' if m.must_survive else ''}{m.path}\n        {m.invariant}")
        print(f"\n{len(MUTANTS)} mutants")
        return 0
    wanted = [m for m in MUTANTS if a.only is None or m.id == a.only]
    results = [run_one(m) for m in wanted]
    for r in results:
        print(f"[{r['status']:>13}] {r['id']}  {r['invariant']}", flush=True)
    summary = summarise(results)
    print(json.dumps(summary, indent=2))
    if a.report:
        a.report.write_text(json.dumps(summary, indent=2))
    return 1 if summary["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
