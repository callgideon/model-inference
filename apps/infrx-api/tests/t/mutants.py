#!/usr/bin/env python3
"""r1 R32 for T1: the invariants `test_trace_spool.py` claims must be killable.

One single-edit defect per invariant, applied to a **copy** of the package in a temporary
directory, with the cases that must fail because of it. A mutant that survives means the
case asserting that invariant proves nothing.

The list covers the durability half - the segment format, the fsync boundary, the caps, the
ack interface, recovery. The accounting half is inherited from `contracts/fakes/traces.py`
and is covered by the coordinator's own list (`tests/contracts/mutants.py`, the `T` file);
mutating another track's file from here would be measuring their suite, not this one. What
proves the inheritance is that the same exported suite and lattice run green against
`SpoolTraceSink`, which the conformance and sequence cases below do.

    uv run --frozen pytest -q tests/t/test_trace_mutants.py            # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/t/test_trace_mutants.py
    uv run --frozen python -m tests.t.mutants --list
"""
from __future__ import annotations

import argparse
import enum
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

API_DIR = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "infrx"
SUITE = "tests/t/test_trace_spool.py"
SPOOL = "traces/spool.py"

# the cases, spelled once
CONFORMANCE = "test_the_spool_sink_passes_the_exported_tracesink_conformance_suite"
LATTICE = "test_every_bounded_capture_sequence_holds_the_invariants_for_the_spool_sink"
ROUND_TRIP = "test_a_captured_request_round_trips_through_the_segment_reader"
RECOVER = "test_recovery_replays_only_fsynced_records_and_tolerates_a_torn_tail"
TWICE = "test_replaying_twice_yields_each_record_exactly_once"
CORRUPT = "test_a_corrupt_record_is_not_replayed_and_stops_the_tail"
VERSION = "test_an_unknown_segment_version_is_never_half_parsed"
CEILING = "test_a_frame_claiming_more_than_a_frame_may_hold_is_the_tail"
ROTATE = "test_rotation_seals_by_size_and_keeps_every_record"
ACK = "test_the_shipper_acks_whole_segments_and_nothing_is_ever_truncated"
RESTART = "test_a_restart_adopts_existing_segments_and_never_reuses_a_name"
SLOW = "test_a_slow_disk_never_blocks_the_request_path"
FULL_SPOOL = "test_a_full_spool_drops_with_disk_budget_and_comes_back_on_an_ack"
FLOOR = "test_the_free_disk_floor_refuses_before_the_host_runs_out"
WRITE_ERROR = "test_a_write_error_drops_the_rest_of_the_batch_and_abandons_the_segment"
FSYNC_ERROR = "test_an_fsync_error_never_claims_durability"
SHUTDOWN = "test_shutdown_is_a_counted_loss_not_a_silent_one"
AGREE = "test_the_declared_content_and_the_charged_bytes_must_agree"
OVERSIZE = "test_an_envelope_too_large_for_a_frame_is_refused_not_written"
PRUNE = "test_the_capture_list_is_pruned_so_reap_stays_bounded"


@dataclass(frozen=True)
class Mutant:
    """One single-edit defect and the cases that must fail because of it."""

    name: str
    invariant: str
    old: str
    new: str
    cases: tuple[str, ...] = field(default_factory=tuple)
    file: str = SPOOL

    @property
    def path(self) -> pathlib.Path:
        return pathlib.Path(PACKAGE) / self.file


def _m(name, invariant, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- the segment format and its reader -------------------------------------
    _m("checksum_not_verified", "a corrupt record is never replayed",
       "        if binascii.crc32(content, binascii.crc32(payload)) != crc:",
       "        if False:", CORRUPT),
    _m("torn_tail_crashes_the_reader", "a torn tail is tolerated, not raised",
       "        if len(data) - offset < FRAME.size:\n            scan.torn += 1\n            break",
       "        if False:\n            scan.torn += 1\n            break",
       RECOVER),
    _m("a_frame_past_the_reader_ceiling_is_read", "the reader's frame ceiling is the writer's",
       "        if envelope_bytes > MAX_ENVELOPE_BYTES or len(data) - body",
       "        if False or len(data) - body", CEILING),
    # No mutant for the *other* half of that condition (`len(data) - body < envelope_bytes +
    # content_bytes`). A frame that claims more bytes than the file holds is sliced short and
    # then fails its checksum, so removing the length test changes nothing an oracle can see:
    # it is a fast path, not an invariant, and an equivalent mutant would only be killable by
    # weakening a case, which R40 forbids.
    _m("any_format_version_is_parsed", "an unknown segment version is not guessed at",
       "    if magic != SEGMENT_MAGIC or version != SEGMENT_VERSION:",
       "    if False:", VERSION),
    _m("the_reader_loses_the_content", "the reader returns the content it read",
       "        scan.contents.append(content)", "        scan.contents.append(b\"\")",
       ROUND_TRIP),
    _m("replay_reads_every_segment_twice", "a replay yields each record exactly once",
       "    for name in segment_names(directory, reader):",
       "    for name in segment_names(directory, reader) * 2:", TWICE),
    # --- the fsync boundary ----------------------------------------------------
    _m("fsync_claimed_at_every_flush", "durability begins at fsync, not at append",
       "        fsync_due = (now - self._last_fsync).total_seconds() >= self.limits.trace_fsync_interval_s",
       "        fsync_due = True", CONFORMANCE),
    _m("stats_reports_appended_as_fsynced", "appended and fsynced are separate states",
       '            "fsynced": self.fsynced_records,', '            "fsynced": self.appended_records,',
       CONFORMANCE, RECOVER, FSYNC_ERROR),
    _m("a_failed_fsync_is_reported_durable", "an fsync error never claims durability",
       "            segment.fsync_failed = True\n            segment.sealed = True\n"
       "            result.fsync_failed += unsynced",
       "            segment.fsync_failed = True\n            segment.sealed = True\n"
       "            result.fsynced += unsynced",
       FSYNC_ERROR),
    _m("rotation_seals_without_an_fsync", "a rotation never unpromises an appended record",
       "        if segment.fd is not None and segment.written > segment.synced and not segment.fsync_failed:\n"
       "            self._fsync(segment, result)",
       "        if False:\n            self._fsync(segment, result)",
       ROTATE),
    _m("unsynced_bytes_survive_a_crash", "only fsynced records recover",
       "            if segment.written > segment.synced:", "            if False:", RECOVER),
    _m("a_crash_keeps_its_promise_count", "a crash reports what it lost",
       "        self.appended_records = self.fsynced_records",
       "        self.appended_records = self.appended_records", RECOVER),
    # --- rotation, sealing and the ack interface --------------------------------
    _m("segments_never_rotate", "segments rotate by size",
       "            if active.written + need <= self.segment_max_bytes or active.records == 0:",
       "            if True:", ROTATE),
    _m("the_active_segment_can_be_acked", "only a sealed segment is deleted (no copytruncate)",
       "            if segment is None or not segment.sealed:",
       "            if segment is None:", ACK),
    _m("a_broken_segment_stays_open", "a segment whose write failed is abandoned",
       "                self._abandon_active(result)", "                pass",
       WRITE_ERROR),
    _m("a_restart_forgets_unshipped_segments", "old segments still count and are never reused",
       "        for name in segment_names(self.spool_dir, self.io):",
       "        for name in ():", RESTART),
    # --- the host caps ----------------------------------------------------------
    _m("the_spool_cap_is_ignored", "the 10 GiB host spool cap drops rather than grows",
       "        self.paused = (spool_bytes + need > self.limits.trace_spool_max_bytes\n"
       "                       or free - need < self.limits.trace_spool_min_free_bytes)",
       "        self.paused = (False\n"
       "                       or free - need < self.limits.trace_spool_min_free_bytes)",
       FULL_SPOOL),
    _m("the_free_disk_floor_is_ignored", "the 2 GiB free-disk floor is enforced",
       "        self.paused = (spool_bytes + need > self.limits.trace_spool_max_bytes\n"
       "                       or free - need < self.limits.trace_spool_min_free_bytes)",
       "        self.paused = (spool_bytes + need > self.limits.trace_spool_max_bytes\n"
       "                       or False)",
       FLOOR),
    _m("a_paused_sink_still_takes_records", "at the cap a record is refused, not queued",
       "        if self.paused:\n            # Nowhere for this record to land",
       "        if False:\n            # Nowhere for this record to land",
       FULL_SPOOL),
    _m("the_pause_never_lifts", "a freed disk resumes capture",
       "            self._refuse_bytes(self._refused_need)\n"
       "        assert len(self.queued) == len(self._pending)",
       "            pass\n"
       "        assert len(self.queued) == len(self._pending)",
       FLOOR),
    # --- the request path never pays -------------------------------------------
    _m("the_writer_runs_on_the_event_loop", "the request path never waits for the disk",
       "        result = await self._run(self._write_batch, batch, fsync_due)",
       "        result = self._write_batch(batch, fsync_due)", SLOW),
    _m("an_unserializable_envelope_raises", "nothing in the trace path raises into the request",
       "            except Exception:                 # noqa: BLE001 - R37: never raise into the",
       "            except ZeroDivisionError:         # noqa: BLE001 - R37: never raise into the",
       LATTICE),
    _m("an_oversized_envelope_is_written", "the writer never spools a frame the reader refuses",
       "                if len(payload) > MAX_ENVELOPE_BYTES:", "                if False:",
       OVERSIZE),
    _m("understated_content_is_accepted", "the bytes held and the bytes declared agree",
       "        elif len(content) != envelope.content_bytes:", "        elif False:",
       AGREE),
    _m("shutdown_is_a_silent_loss", "a process that stops counts what it dropped",
       "            self.loss_reasons[TraceLossReason.shutdown] += lost\n"
       "            self.content_bytes = self.metadata_bytes = 0",
       "            self.content_bytes = self.metadata_bytes = 0",
       SHUTDOWN),
    _m("the_capture_list_grows_for_ever", "the capture list is bounded",
       "        if len(self.captures) >= self._prune_at:", "        if False:", PRUNE),
)


class Outcome(enum.StrEnum):
    """What one mutant run proved. Only `killed` counts."""

    killed = "killed"
    survived = "survived"
    broken_runner = "broken_runner"
    misdeclared = "misdeclared"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    detail: str

    @property
    def killed(self) -> bool:
        return self.outcome is Outcome.killed


PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED = 0, 1
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) ([^\s:]+(?:::[^\s]+)?)")


def run_mutant(mutant: Mutant) -> Result:
    """Apply one mutant to a throwaway copy and run the cases it names.

    A kill needs all of: pytest exited 1 (not 2-5, which mean the *runner* broke, and not
    0, which means the defect went unnoticed); at least one test failed; and every failing
    test names one of this mutant's own cases, so a syntax error or an import-time crash
    cannot be counted as a kill. The worktree is never written to.
    """
    if not mutant.cases:
        return Result(Outcome.misdeclared, "declares no case")
    with tempfile.TemporaryDirectory(prefix=f"t1-mutant-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(API_DIR / PACKAGE, root / PACKAGE, ignore=ignore)
        shutil.copytree(API_DIR / "tests", root / "tests", ignore=ignore)
        target = root / mutant.path
        source = target.read_text()
        if mutant.old not in source:
            return Result(Outcome.misdeclared,
                          f"anchor not found in {mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
             "-rf", "--tb=no", SUITE, "-k", " or ".join(mutant.cases)],
            cwd=root, capture_output=True, text=True,
            env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"})
        stdout = done.stdout or ""
        lines = (stdout or done.stderr).strip().splitlines()
        summary = lines[-1] if lines else "no output"
        if done.returncode not in (PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED):
            return Result(Outcome.broken_runner, f"pytest exit {done.returncode}: {summary}")
        if not re.search(r"(\d+) (?:passed|failed|skipped)", summary) or "no tests ran" in summary:
            return Result(Outcome.misdeclared, f"no case matched: {summary}")
        failed = [match.group(1) for match in
                  (_FAILED_LINE.match(line.strip()) for line in stdout.splitlines()) if match]
        if done.returncode == PYTEST_ALL_PASSED or not failed:
            return Result(Outcome.survived, summary)
        stray = [test for test in failed
                 if not any(test.endswith(case) for case in mutant.cases)]
        if stray:
            return Result(Outcome.broken_runner, f"failures outside the named cases: {stray[:3]}")
        return Result(Outcome.killed, summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="run T1's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:42s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over "
              f"{len({case for m in MUTANTS for case in m.cases})} named cases")
        return 0
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    bad: dict[str, list[str]] = {}
    for mutant in chosen:
        result = run_mutant(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}", flush=True)
        if not result.killed:
            bad.setdefault(result.outcome.value, []).append(mutant.name)
    failures = sum(len(names) for names in bad.values())
    print(f"\n{len(chosen) - failures}/{len(chosen)} killed"
          + "".join(f"; {outcome}: {names}" for outcome, names in sorted(bad.items())))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
