# T1 — Byte-budgeted capture and persistent spool

| Field | Value |
|---|---|
| Task | T1 (track T), oracles TRACE-BOUNDS, F-CONTRACT (feeds TRACE-RECOVER) |
| Status | **implemented** — every result below is against the real `SpoolTraceSink` in a local process. Not integrated: no track router is mounted, no ClickHouse/S3 exists yet (T2), and no gateway builds the sink (G1). |
| Owner/session | Claude Opus 5 (1M context), session `01XbryjFN2xhdKwhuUQvbyFn` |
| Base SHA | `8744418` |
| Implementation SHA | round 1 `d03cbbf` (`b668ed2` sink + tests, `d03cbbf` mutation list); **round 2 `f55c401`** (review round 1 addressed — see the round-2 section; this report keeps its round-1 filename, with the two false sentences corrected in place) |
| Integrated SHA | pending (coordinator) |
| Branch / worktree | `codex/t1-trace-capture` in `.claude/worktrees/codex-t1` |

## What was built

`apps/infrx-api/infrx/traces/spool.py` — one module, two layers.

**Accounting** (R27/R37/R42) is *inherited*, not reimplemented: `SpoolTraceSink` and
`SpoolCapture` subclass `infrx.contracts.fakes.traces`, which is the coordinator's
executable specification of the loss accounting, and override only what durability
changes (`open`, `_enqueue`, `flush`, `stats`, `crash`). Copying 250 lines of loss
accounting would have forked the spec at the next ruling; what proves the inheritance is
that the exported suite **and** the R42 sequence lattice run green against the real sink
(below), not that the code looks similar. See the integration request about the name.

**Durability** is new:

- a **dedicated single writer thread** (`ThreadPoolExecutor(max_workers=1)`, created
  lazily, named `infrx-trace-spool`). `add`/`offer`/`finish`/`abandon`/`reap` run on the
  event loop, touch counters only, never await and never touch disk. No lock is held
  across a filesystem call, which is what makes the slow-disk drill pass rather than
  deadlock — **correction (round 2): "never touch disk" was true of `add`/`offer` but not
  of the sink as a whole.** `flush` re-checked the disk limits with a `statvfs` on the loop
  thread while paused, and `ack` was synchronous, so its `unlink` ran on the caller's
  thread: 2,006 ms and 2,010 ms of event-loop stall respectively, measured by the reviewer.
  Round 2 moves every syscall after construction onto the writer thread and pins it with a
  thread-name oracle;
- **segments, format version 1** (08 §5 "spool segment 1"): header `INFRXTRC` + uint16
  version; per record `uint32 envelope_bytes | uint32 content_bytes | uint32 crc32` then
  the canonical envelope JSON then the raw content bytes, with the checksum covering the
  two length fields as well (the stored `uint64 index` of round 1 is gone; see the round-2
  section). Rotation by size (`segment_max_bytes`, default 16 MiB), and a rotation fsyncs
  before it seals;
- **batched fsync at most every `TRACE_FSYNC_INTERVAL_S`** (2 s on the injected clock).
  `stats()` reports `in_memory` / `appended` / `fsynced` separately; durability is claimed
  only after fsync;
- **host caps**: `TRACE_SPOOL_MAX_BYTES` (10 GiB) and `TRACE_SPOOL_MIN_FREE_BYTES`
  (2 GiB free-disk floor). At either limit the sink *pauses*: records are dropped
  `disk_budget` with the charge released while inference continues, and the pause lifts
  when an `ack` frees a segment or the flusher's own re-check sees the disk come back.
  Nothing is ever deleted to make room;
- **ack-based deletion for T2**: `segments()`, `read_segment(name)`, `rotate()` and
  `ack(name)`. Only a *sealed* segment is deleted, and it is unlinked **whole** — there is
  no truncate anywhere except the crash model, which a drill asserts by counting calls on
  the injected filesystem;
- **recovery**: `recover(dir)` / `scan_segment(name, bytes)` replay only checksum-valid
  frames, tolerate a torn tail, count `torn` / `poison` / `unreadable` separately, and are
  pure reads — so a double replay yields the same records with the same stable ids
  (`(segment, index)`; segment names are never reused, including across a restart)
  — **correction (round 2): the clause after the semicolon was false.** `_next_index` came
  from the files present, so once the shipper had acked everything and the process
  restarted, the next segment was named `trace-000000.seg` again and `(segment, 0)` named a
  different record; the frame also *stored* its index outside the checksum, where one
  flipped bit produced two records under one id. Both are fixed in the round-2 section
  below (boot id in the name, position derived from the verified frames, lengths inside the
  checksum);
- **drop reasons** distinguish `memory_budget`, `metadata_budget`, `queue_full`,
  `disk_budget`, `disk_error`, `shutdown`, `malformed` and `abandoned`. `close()` is the
  orderly counterpart of `crash()` and counts what it dropped as `shutdown`;
- two bounds the in-memory specification does not have to care about and a real process
  does: the capture list is pruned (it grew by one dead capture per request for ever, and
  `reap` walked all of them), and `appended`/`fsynced` are counters rather than lists of
  every record ever written.

## Requirement coverage

`apps/infrx-api/tests/t/test_trace_spool.py` (42 tests after round 2; 49 in `tests/t`
with the mutation wrapper). Each row is the exact invariant the test id claims; the cases
round 2 added are listed in its own table rather than repeated here.

| Test ID | Oracle | Invariant demonstrated |
|---|---|---|
| `test_the_spool_sink_passes_the_exported_tracesink_conformance_suite` | F-CONTRACT | the 17 exported `tracesink` cases pass against the real adapter, 0 skipped |
| `test_the_factory_supplies_every_documented_hook` | F-CONTRACT / R32 | every `OPTIONAL_HOOKS["tracesink"]` hook is supplied, so the count above is the whole suite |
| `test_the_spool_sink_satisfies_the_declared_protocols` | F-CONTRACT | `isinstance` of `ports.TraceSink`/`TraceCapture`; `open`/`add`/`reap` synchronous, the rest coroutines |
| `test_a_sink_without_a_spool_directory_refuses_to_exist` | TRACE-BOUNDS | `TRACE_SPOOL_DIR` unset disables capture: the sink refuses rather than pretending |
| `test_every_bounded_capture_sequence_holds_the_invariants_for_the_spool_sink` | TRACE-BOUNDS / R42 | every capture operation sequence × 3 modes × 2 deadline states holds the loss invariants against the durable sink |
| `test_a_captured_request_round_trips_through_the_segment_reader` | TRACE-RECOVER | the content T2 ships comes back byte for byte under the envelope it was filed with |
| `test_recovery_replays_only_fsynced_records_and_tolerates_a_torn_tail` | TRACE-RECOVER | a crash between append and fsync recovers exactly the fsynced records; a torn tail is tolerated |
| `test_replaying_twice_yields_each_record_exactly_once` | TRACE-RECOVER | replay is a read: two replays yield identical records and unique `(segment, index)` ids |
| `test_a_corrupt_record_is_not_replayed_and_stops_the_tail` | TRACE-RECOVER | a flipped byte is refused by its checksum; the records before it survive |
| `test_a_poison_record_does_not_stop_the_scan` | TRACE-RECOVER | a checksum-valid frame that is not a `TraceEnvelope` is one bad row, not the end of the spool |
| `test_a_frame_claiming_more_than_a_frame_may_hold_is_the_tail` | TRACE-RECOVER | the reader's frame ceiling is the writer's: a length past `MAX_ENVELOPE_BYTES` is the tail, not a poison row |
| `test_an_unknown_segment_version_is_never_half_parsed` | TRACE-RECOVER | a foreign magic or a future version is left alone, never guessed at |
| `test_rotation_seals_by_size_and_keeps_every_record` | TRACE-RECOVER | rotation by size loses no record, and sealing never unpromises an appended one |
| `test_the_shipper_acks_whole_segments_and_nothing_is_ever_truncated` | TRACE-RECOVER | whole-segment unlink only; the active segment cannot be acked; `truncate` call count is 0 |
| `test_a_restart_adopts_existing_segments_and_never_reuses_a_name` | TRACE-RECOVER | unshipped segments still count against the cap, and a restart appends to a new file |
| `test_a_slow_disk_never_blocks_the_request_path` | TRACE-BOUNDS | with the writer parked inside the filesystem, 50 further requests capture and finish; `appended` stays 0 |
| `test_a_full_spool_drops_with_disk_budget_and_comes_back_on_an_ack` | TRACE-BOUNDS | at the host cap: `disk_budget` drops, `spool_bytes` never above the cap, inference continues, an ack resumes |
| `test_the_free_disk_floor_refuses_before_the_host_runs_out` | TRACE-BOUNDS | the 2 GiB floor refuses, and the flusher's re-check lifts the pause when the disk returns |
| `test_a_write_error_drops_the_rest_of_the_batch_and_abandons_the_segment` | TRACE-BOUNDS | ENOSPC mid-batch: the rest is counted `disk_error`, the segment is sealed, earlier records stay readable |
| `test_an_fsync_error_never_claims_durability` | TRACE-RECOVER | a failed fsync counts `disk_error`, never `fsynced`, and seals the segment |
| `test_shutdown_is_a_counted_loss_not_a_silent_one` | TRACE-BOUNDS | an orderly stop counts what was still in memory as `shutdown`; idempotent |
| `test_the_declared_content_and_the_charged_bytes_must_agree` | TRACE-BOUNDS / R27 | an envelope declaring *less* content than was charged is `malformed`, charge released |
| `test_an_envelope_too_large_for_a_frame_is_refused_not_written` | TRACE-BOUNDS | an envelope past the frame ceiling is dropped `malformed` rather than spooled unreadable |
| `test_the_capture_list_is_pruned_so_reap_stays_bounded` | TRACE-BOUNDS | the structure `reap` walks is bounded over 5,000 requests |
| `test_synthetic_concurrent_load_stays_within_the_declared_bounds` | TRACE-BOUNDS | under 550 concurrent captures offering more than the budget, peak charged bytes stay ≤ the content budget and the overflow is a counted `memory_budget` loss |
| `tests/t/test_trace_mutants.py::test_mutant_is_killed[*]` | R32 | each declared invariant is killable; `test_the_runner_cannot_report_a_false_kill` proves the runner cannot count a syntax error, a no-op edit or a missing anchor as a kill |

## Environment

Local development host (not staging, not production): Linux `7.0.0-1010-aws` x86_64,
16 vCPU, 61 GiB RAM, root filesystem `/dev/root` ext4 with 59 GiB free.
CPython 3.12.3 from the pinned environment (`apps/infrx-api/.venv`, `uv 0.11.8`,
`uv sync --frozen --all-extras` via `make api-env`). No container, no cloud service, no
GPU, no network egress. `pydantic` 2.x from `uv.lock`; no new dependency. All times UTC,
2026-09-21. No seed applies except the lattice's own (`SEED = 20260921`, unused here
because every run below was exhaustive).

## Commands and results

All from `apps/infrx-api` unless the target is a root `make` target. Exit status and the
tail of the output are quoted, not summarised.

1. `make api-test` — exit 0, 2026-09-21T06:39Z

```
702 passed, 2 warnings in 50.22s
```

Baseline for comparison, same tree with the new directory excluded
(`uv run --frozen pytest -q --ignore=tests/t`) — exit 0:

```
670 passed, 2 warnings in 25.54s
```

`uv run --frozen pytest -q tests/t --collect-only` reports `32 tests collected`, and
670 + 32 = 702: the new tests are additive and nothing existing changed behaviour (R48: no track
test imports `gateway`, and the helper module is reached with `from . import mutants`).

2. Focused suite, `uv run --frozen pytest -q tests/t -p no:cacheprovider -s` — exit 0:

```
tracesink conformance against SpoolTraceSink: 17/17 cases ran, 0 skipped
spool sink sequence properties: 17724 sequences, 51828 operations (exhaustive); len 1: 14, len 2: 196, len 3: 2744; guarded invariants reached: closed_capture_holds_nothing 13344, closed_capture_stores_nothing 4044, crash_losses_are_bounded 3450, lost_content_is_counted 287, lost_content_is_marked 287, minimal_stores_no_content 944, quiet_modes_charge_nothing 11816, rows_belong_to_their_capture 1701, rows_carry_the_opened_mode 1701 in 4.0s
synthetic concurrent capture load (microbenchmark, no GPU, no network): 1100 requests, 550 concurrent, 8x64000B each, in 3.8s (289 req/s, 148 MB/s offered)
  peak charged content 259,584,000 B against a 260,046,848 B budget; peak spool 259,875,637 B against a 1,073,741,824 B cap; maxrss 59 -> 322 MiB
  1,100 appended, 1,100 fsynced, 0 dropped, losses {'memory_budget': 86}
32 passed in 17.64s
```

The conformance line is the F-CONTRACT number: **17 of 17 cases ran, 0 skipped**. A skip
would have been reported here and is never a pass (R32); the hook check is a separate
test rather than an inspection.

3. The whole R42 lattice, exhaustive to length 4 —
`INFRX_TRACE_SEQUENCES=full uv run --frozen pytest -q tests/t/test_trace_spool.py -k sequence -s` — exit 0:

```
spool sink sequence properties: 248220 sequences, 973812 operations (exhaustive); len 1: 14, len 2: 196, len 3: 2744, len 4: 38416; guarded invariants reached: closed_capture_holds_nothing 180960, closed_capture_stores_nothing 59724, crash_losses_are_bounded 62580, lost_content_is_counted 3686, lost_content_is_marked 3686, minimal_stores_no_content 11810, quiet_modes_charge_nothing 165480, rows_belong_to_their_capture 21361, rows_carry_the_opened_mode 21361 in 79.5s
1 passed, 24 deselected in 79.62s (0:01:19)
```

Every guarded invariant was reached, i.e. `report.unfired() == ()`, which the test asserts.
This is the run the T1/G1 note asks for: 248,220 sequences, 973,812 operations, each against a
freshly constructed durable sink, exhaustive with no sampling. The default suite runs
length 3 (17,724 sequences, 4 s) because length 4 builds ~250k spool directories and is
a 78-second test.

4. Mutation list, `uv run --frozen python -m tests.t.mutants` — exit 0, 26 mutants:

```
[killed       ] checksum_not_verified: 1 failed, 24 deselected in 0.34s
[killed       ] torn_tail_crashes_the_reader: 1 failed, 24 deselected in 0.35s
[killed       ] a_frame_past_the_reader_ceiling_is_read: 1 failed, 24 deselected in 0.33s
[killed       ] any_format_version_is_parsed: 1 failed, 24 deselected in 0.39s
[killed       ] the_reader_loses_the_content: 1 failed, 24 deselected in 0.43s
[killed       ] replay_reads_every_segment_twice: 1 failed, 24 deselected in 0.47s
[killed       ] fsync_claimed_at_every_flush: 1 failed, 24 deselected in 11.53s
[killed       ] stats_reports_appended_as_fsynced: 3 failed, 22 deselected in 4.39s
[killed       ] a_failed_fsync_is_reported_durable: 1 failed, 24 deselected in 0.33s
[killed       ] rotation_seals_without_an_fsync: 1 failed, 24 deselected in 0.37s
[killed       ] unsynced_bytes_survive_a_crash: 1 failed, 24 deselected in 0.34s
[killed       ] a_crash_keeps_its_promise_count: 1 failed, 24 deselected in 0.34s
[killed       ] segments_never_rotate: 1 failed, 24 deselected in 0.35s
[killed       ] the_active_segment_can_be_acked: 1 failed, 24 deselected in 0.34s
[killed       ] a_broken_segment_stays_open: 1 failed, 24 deselected in 0.34s
[killed       ] a_restart_forgets_unshipped_segments: 1 failed, 24 deselected in 0.33s
[killed       ] the_spool_cap_is_ignored: 1 failed, 24 deselected in 0.36s
[killed       ] the_free_disk_floor_is_ignored: 1 failed, 24 deselected in 0.36s
[killed       ] a_paused_sink_still_takes_records: 1 failed, 24 deselected in 0.35s
[killed       ] the_pause_never_lifts: 1 failed, 24 deselected in 0.34s
[killed       ] the_writer_runs_on_the_event_loop: 1 failed, 24 deselected in 15.33s
[killed       ] an_unserializable_envelope_raises: 1 failed, 24 deselected in 0.36s
[killed       ] an_oversized_envelope_is_written: 1 failed, 24 deselected in 0.36s
[killed       ] understated_content_is_accepted: 1 failed, 24 deselected in 0.37s
[killed       ] shutdown_is_a_silent_loss: 1 failed, 24 deselected in 0.33s
[killed       ] the_capture_list_grows_for_ever: 1 failed, 24 deselected in 0.39s

26/26 killed
```

`stats_reports_appended_as_fsynced` fails three cases because three of them assert the
two states separately; every other mutant is killed by exactly the case that claims its
invariant, and a failure outside the named cases is reported `broken_runner`, not a kill.

The first run of this list was **25/26**: `a_short_frame_is_read_as_a_record` survived,
because the guard it edited was never exercised — the case appended a half frame *header*,
which an earlier check catches. Two things came out of that rather than a weakened case
(R40): a new test for the reader's frame ceiling with a valid checksum over an oversized
payload (killable, now `a_frame_past_the_reader_ceiling_is_read`), and a recorded decision
that the second half of that condition has **no** mutant, because a frame claiming more
bytes than the file holds is sliced short and then fails its checksum — the length test is
a fast path, not an invariant, and a mutant for it would be equivalent.

`uv run --frozen pytest -q tests/t/test_trace_mutants.py` (the fast subset plus the
runner's self-tests) — exit 0: `7 passed in 4.88s`.

5. `make api-mutants` — **not run**. It mutates `infrx/contracts/**` and runs
`tests/contracts/test_conformance.py`; this task changed no file it touches, and its
default subset already ran inside `make api-test` above. Console targets
(`make console-test/-lint/-typecheck`) — **not applicable**, no console file changed.

## Failure drill

Five injection points, all through the injected filesystem (`SpoolIO`, subclassed as
`DrillIO` in the suite), because a slow disk, ENOSPC, an fsync error and a lost tail are
not reproducible against a real one:

| Injection | Durable state before | Durable state after | Retry / duplicate behaviour |
|---|---|---|---|
| `write` parks inside the filesystem (slow disk) | one segment, 0 records appended | unchanged while parked; the record appends once released | 50 further requests captured and finished meanwhile; `appended` 0 → 1, nothing duplicated |
| `write` raises ENOSPC on the 5th call (mid-batch) | one segment, 1 record | segment sealed with a torn tail; the 1 record still replays | the 2 unwritten records counted `disk_error`, never silently retried |
| `fsync` raises EIO | 1 record appended, 0 fsynced | segment sealed, `fsynced` stays 0, 1 `disk_error` | the next record starts a new segment and fsyncs normally (`appended` 2, `fsynced` 1) |
| free disk below the 2 GiB floor / spool at the 10 GiB cap | segments unchanged | unchanged — nothing is deleted to make room | records dropped `disk_budget`; capture resumes after an `ack` or when the flusher sees the disk return |
| `crash()` between append and fsync | see below | see below | see below |

The crash drill, run as a script against the real filesystem and quoted verbatim:

```
after flush #1 (fsync due): appended=1 fsynced=1 spool_bytes=558
  durable state before: files=['trace-000000.seg 558B']
after flush #2 (no fsync): appended=2 fsynced=1 spool_bytes=1108
  recover() while the process lives: 2 records over 1 segments; torn tails 0, poison 0, unreadable 0
  durable state before the crash: files=['trace-000000.seg 1108B']
crash(): lost=1 appended=1 fsynced=1 losses={'shutdown': 1}
  durable state after: files=['trace-000000.seg 558B']
  replay 1: 1 records over 1 segments; torn tails 0, poison 0, unreadable 0 ids=[('trace-000000.seg', 0)]
  replay 2: 1 records over 1 segments; torn tails 0, poison 0, unreadable 0 ids=[('trace-000000.seg', 0)]
  duplicate ids across two replays: 1 (a replay is a read; the projection keys on these ids)
ack(trace-000000.seg) -> True; files after=[]
cleanup: /tmp/t1-drill-udgtmi6i removed = True
```

Read that as: the OS had both records (the live `recover` sees 2), but only one was ever
*promised*, and after the crash exactly that one recovers — 558 of 1108 bytes survive,
and the unpromised record is counted `shutdown: 1` rather than disappearing. The two
replays return the identical stable id, which is the honest statement of T2's obligation:
a shipper that replays after a restart re-presents `('trace-000000.seg', 0)` and the
projection's dedupe by stable event id collapses it (02) — the sink itself does not dedupe
(R42). Cleanup: the shipper's `ack` unlinked the whole segment, the drill directory was
removed, and the suite's own temp tree is removed at exit. No container was created, so
there is none to remove.

## Measured bounds (synthetic microbenchmark)

Marked explicitly as a **synthetic microbenchmark of the sink**, per 04-verification: no
engine, no GPU, no network, so the throughput figure describes this loop and says nothing
about request latency.

| Quantity | Measured | Configured bound |
|---|---|---|
| peak content bytes charged | 259,584,000 B | 260,046,848 B (`TRACE_CAPTURE_BYTES` 256 MiB − `TRACE_METADATA_RESERVE_BYTES` 8 MiB) |
| process resident memory | 59 → 322 MiB (`ru_maxrss`) | ≈ the 248 MiB budget plus interpreter and test overhead |
| peak spool bytes | 259,875,637 B | 1,073,741,824 B in this run; the shipped cap is 10 GiB, exercised separately at a scaled cap |
| offered load | 1,100 requests, 550 concurrent, 8 × 64,000 B each, 3.8 s | — |
| offered rate | 289 req/s, 148 MB/s of content charged | — |
| outcome | 1,100 appended, 1,100 fsynced, 0 dropped, `{'memory_budget': 86}` | 86 captures lost their content instead of the process growing |

The parts are `bytearray`s, so every accepted part is a distinct copy: a shared `bytes`
object would have made the resident-memory figure a fiction. The bound was genuinely
pressed — 550 concurrent × 512 KiB is more content than the budget allows, and the test
fails if no `memory_budget` loss occurs.

## Artifacts

| Path | Notes |
|---|---|
| `apps/infrx-api/infrx/traces/__init__.py`, `spool.py` | the sink, the segment format, the reader, the filesystem seam |
| `apps/infrx-api/tests/t/test_trace_spool.py` | 25 tests: the exported suite, the lattice, the format, the drills, the measured bound |
| `apps/infrx-api/tests/t/mutants.py`, `test_trace_mutants.py` | 26 mutants, the runner and its self-tests |
| `research/plan/evidence/t/T1-d03cbbf.md` | this report |

No credentials, customer prompts or signed URLs appear anywhere; the only content in any
test is literal filler bytes. Raw run logs are the command outputs quoted above.

## Changes

Owned paths only: `apps/infrx-api/infrx/traces/`, `apps/infrx-api/tests/t/`,
`research/plan/evidence/t/`. No migration, no deployment, no schema. `infra/clickhouse/`
is deliberately untouched (T2). Nothing outside those paths is modified — no
`pyproject.toml`, no lock file, no `infrx/config.py`, no composition root, no `Makefile`.

Rollback is deleting the three directories: nothing else imports them yet, and no durable
state outside a configured `TRACE_SPOOL_DIR` exists. A spool left behind by a rolled-back
build is readable by `recover()` at format version 1 and is safe to delete once T2 has
shipped it.

## Limits

- **Not integrated.** The sink is constructed by tests only. Until G1 builds it on the
  request path and T2 ships and acks segments, this is "implemented": the suites and
  drills are real, the end-to-end path is not.
- **No real crash.** `crash()` models a host death by truncating each segment back to its
  fsynced length and is called in-process; no drill killed an OS process or cut power.
  That is the honest statement of what was tested: the *promise* (only fsynced records
  recover) is exercised, the hardware failure is not. A `kill -9` drill belongs with T2's
  restart test, where a separate process already exists.
- **`flush(deadline)` ignores its argument** beyond the batch it takes. The deadline is a
  database-clock instant and the writer's bound is wall-clock; turning one into the other
  would be a fiction. What bounds a flush is `TRACE_QUEUE_MAX`, and a hung disk blocks the
  flusher only. If a wall-clock bound on a flush is wanted, it needs a name in 08 §5.
- **No internal timer.** The port has no "run me every N seconds" operation, so `flush`
  and `reap` are called by the caller. G1 must schedule both (integration request 2);
  without them the sink accumulates in memory and never spools, which the bounds handle
  but the coverage figures would not like.
- **Segment size has no 08 §5 name** (integration request 3). It is a module default
  (16 MiB) and a constructor argument, so nothing depends on a name that does not exist.
- **fsync semantics after EIO.** A failed fsync counts its records `disk_error` and seals
  the segment; the bytes may still be on disk and may therefore be replayed later by
  `recover`. That is deliberate — T2 dedupes by stable id — but it means `disk_error` is
  "not promised", not "definitely gone".
- **`paused` is a plain bool** written by the writer thread and read by the request path.
  On CPython that is atomic enough for a pause flag, and a stale read costs one
  misrouted record, not a bound violation. It is not a lock.
- **One event loop and one writer, as a requirement rather than an assumption**
  (rewritten in round 2; the earlier wording called it a single-producer *assumption* and
  understated it). The accounting's check-and-charge is not one atomic step, so `add` from
  several OS threads does not merely overshoot by one part: the reviewer drove eight
  threads for eight seconds and finished with **133,598 bytes still charged while every
  capture was closed**, i.e. lost updates in both directions. The sink is documented in its
  module docstring as requiring one loop and one writer, and G1 uses it from the request
  path of a single-loop process. A threaded producer would need a lock inside the shared
  accounting, which is the coordinator's file.
- **Descriptor hygiene.** A sink dropped without `close()` keeps its active segment's
  descriptor open, because a file descriptor is an integer no garbage collection closes.
  With one sink per process that is a non-issue, and `close()`/`crash()`/`ack` all close
  properly; the test harness retires sinks explicitly because the lattice builds a quarter
  of a million of them. Measured before that was added: the length-2 lattice alone leaked
  132 descriptors, i.e. the length-3 default run would have hit `EMFILE` on a host with the
  usual 1024 limit (this host allows 1,048,576, which is why the first full run passed).
  After the fix the same measurement is `open fds 4 -> 4`.
- **The metadata reserve is accounted, not physically separate.** `metadata_bytes` is
  charged against `TRACE_METADATA_RESERVE_BYTES` exactly as the specification has it; no
  separate allocator exists.
- **No ClickHouse, no S3, no container.** Task-local services (58123/59000/59110) were
  not needed and were not started.

## Handback

**Next unblocked task:** **T2** (idempotent analytics and content shipping). It starts
after T1 and integrates after D5/D6. Everything it needs from this task exists:
`segments()` → `read_segment(name)` → ship → `ack(name)`, plus `rotate()` to seal the tail
of a quiet host, `recover(dir)` at startup, and `(segment, index)` as the stable event id
to deduplicate by. `Scan.poison` is the poison-record count T2's quarantine path keys on.
T2 owns `infra/clickhouse/` and the DDL; nothing here writes SQL.

**Integration requests (exact asks, all outside my owned paths):**

1. **`infrx/contracts/fakes/traces.py` — a name, not a behaviour.** `SpoolTraceSink`
   inherits the R27/R37/R42 accounting from `FakeTraceCapture`/`FakeTraceSink` so the
   specification has one implementation. If the coordinator would rather production code
   did not subclass a class called `Fake…`, the resolution is a rename or an extraction
   (for example `contracts/accounting.py` holding `TraceAccounting{Sink,Capture}` with the
   fakes and this sink both inheriting it) as a contract revision, with no behaviour
   change. T1 will follow whichever is decided; it did not want to fork 250 lines of loss
   accounting to avoid a word.
2. **G1 (gateway composition root):** construct the sink once per process as
   `SpoolTraceSink(clock, limits=settings.pilot, spool_dir=settings.pilot.trace_spool_dir)`
   **only when `TRACE_SPOOL_DIR` is set** — it is documented as disabling capture, and the
   constructor refuses an empty value rather than pretending. Schedule two calls on a
   timer in the app lifespan: `await sink.flush(clock.now())` at least every
   `TRACE_FSYNC_INTERVAL_S`, and `sink.reap(60.0)` on the same tick (R37: `reap` is what
   releases the bytes of a request that died without its `finally`). Call
   `await sink.close()` on shutdown so what is in memory is counted `shutdown` rather than
   vanishing. No router is requested: T1 exposes no HTTP surface.
3. **08 §5 / `contracts/limits.py`:** please add `TRACE_SPOOL_SEGMENT_BYTES`
   (suggested default `16777216`) as a `PilotSettings` field. Rotation needs a size, there
   is no name for one, and a local constant is where configuration goes to be forgotten.
   Until then `SpoolTraceSink(segment_max_bytes=…)` is the knob and the default is the
   module's.
4. **I (observability), for T3:** `stats()` carries four keys beyond the contract set —
   `spool_bytes`, `spool_segments`, `spool_unacked_records`, `spool_paused`. They are the
   spool-side metrics the dashboards will want (durability gap = `appended − fsynced`,
   shipping backlog = `spool_unacked_records`, capture halted = `spool_paused`). T3 owns
   the metric names; this is only a note that the numbers exist.

5. **F2 / `contracts/conformance`, from round 2:** may the sink charge the metadata reserve
   the **serialized** length of a row instead of the caller-declared `envelope.metadata_bytes`?
   The declared number is what the frozen case
   `trace_bounds__metadata_exhaustion_drops_with_counters` asserts against (64 declared
   against a 64-byte reserve, accepted, while the real payload is ~400 bytes), so charging
   the truth would fail the exported suite. Round 1 of review is right that the declared
   number is a client-visible figure a caller can under-report; the fix belongs in the
   contract, not in T1.
6. **F2 / harness hooks:** `TraceSink.crash()` exists only for the `crash` harness hook and
   truncates unsynced bytes. It is a public method on a production class that destroys data
   if anybody calls it by accident. A hook name that says so (`simulate_host_loss`, mapped to
   `extra["crash"]`) would be a one-line contract note; T1 kept `crash` so round 1's
   reproductions keep working.
7. **F2, when the accounting base is extracted:** make `clock` required rather than
   defaulting to `FakeClock()`. A real sink silently using a fake clock would report a
   durability interval that never elapses; the default is inherited, not chosen.
**Unresolved findings:** none open against another track. Two decisions made here that a
reviewer should see rather than discover: the capture keeps its content parts (the shared
accounting charges bytes but keeps none, and T2 needs the bytes), and the envelope is
serialized at `finish`/`offer` rather than in the writer, so a record the writer cannot
serialize is a counted `malformed` drop instead of a failed batch the sink had already
reported accepted. The second one is what the lattice found: it feeds envelopes assembled
past the record validator, whose timestamps are already serialized strings.

## Round 2 — review round 1 (`fix_required` at `794b95b`) addressed

Implementation SHA for this round: `f55c401`. Five commits on `codex/t1-trace-capture`.
The review's verdict on round 1 was that the accounting held and the durability half did
not; that is accurate, and every reproduction it shipped is quoted below re-run.

### Each blocking finding, its fix and the case that fails without it

| # | Finding | Commit | Case that kills it |
|---|---|---|---|
| B1 | `flush`'s paused re-check (`statvfs`) and `ack`'s `unlink` ran on the event loop: 2,006 ms and 2,010 ms of loop stall | `7b51558` | `test_no_filesystem_call_ever_happens_on_the_event_loop` — the oracle is the *thread name* of every syscall, so it cannot flake and cannot be satisfied by being fast |
| B2 | a failed header write leaked the descriptor and left an orphan file, once per flush | `7b51558` | `test_a_failed_segment_open_leaks_no_descriptor_and_no_file` |
| B3 | a cancelled flush or a non-`OSError` in the writer lost accepted records and leaked the budget for ever | `7b51558` | `test_a_cancelled_flush_still_settles_its_batch`, `test_a_writer_error_that_is_not_an_oserror_still_settles_the_batch`, `test_only_one_flush_is_ever_in_flight` |
| B4 | `(segment, index)` was not stable: names were reused after a full ack plus restart, and the index sat outside the checksum | `7b51558` | `test_a_record_id_is_never_reused_after_an_ack_and_a_restart`, `test_a_flipped_bit_in_a_frame_header_is_the_tail_never_a_wrong_identity`, `test_a_frame_whose_lengths_were_swapped_is_the_tail` |
| B5 | a capture already counted could be counted again by a writer-side drop | `7b51558`, `3e42046` | `test_one_capture_counts_one_loss_even_when_the_writer_refuses_it` |
| B6 | eight mutants survived on claimed invariants | `e055579`, `510483c`, `f55c401` | `58/58 killed` (below); the new cases are listed per mutant in `tests/t/mutants.py` |

Writing B5's case found **a second hole the review had not reached**: the fsync path
counted losses by record *count*, so an fsync error unpromising a record whose capture had
already counted a loss counted a second one. The flag now travels with the record into the
segment (`_Segment.unsynced_counted`), because an fsync spans batches. Fixed in `3e42046`
and covered by the same case (all three writer refusals).

### Commands (exit status, output tail, 2026-09-21T17:0x–17:4xZ)

`make api-test` — exit 0:

```
718 passed, 2 warnings in 101.14s (0:01:41)
```

(670 baseline + 48; `pytest -q tests/t --collect-only` → `49 tests collected`, one of which
is the mutant-list well-formedness test that the baseline count does not include.)

Focused suite, `uv run --frozen pytest -q tests/t/test_trace_spool.py -s` — exit 0:

```
tracesink conformance against SpoolTraceSink: 17/17 cases ran, 0 skipped
spool sink sequence properties: 17724 sequences, 51828 operations (exhaustive); len 1: 14, len 2: 196, len 3: 2744; guarded invariants reached: closed_capture_holds_nothing 13344, closed_capture_stores_nothing 4044, crash_losses_are_bounded 3450, lost_content_is_counted 287, lost_content_is_marked 287, minimal_stores_no_content 944, quiet_modes_charge_nothing 11816, rows_belong_to_their_capture 1701, rows_carry_the_opened_mode 1701 in 11.4s
truncation sweep: 3315 offsets, every prefix replayed exactly
frame identity: 3510 single-bit flips, no wrong or duplicate id
synthetic concurrent capture load (microbenchmark, no GPU, no network): 1100 requests, 550 concurrent, 8x64000B each, in 3.7s (293 req/s, 150 MB/s offered)
42 passed in 27.15s
```

Mutants, `uv run --frozen python -m tests.t.mutants` — exit 0, 58 mutants (26 in round 1):

```
58/58 killed
```

Getting there took three rounds and each failure was a case that proved less than it
claimed, so they are recorded rather than quietly fixed:

- `the_checksum_ignores_the_lengths` survived against the bit-flip sweep, because a
  single-bit change to a length field shifts the payload slice and fails the checksum
  anyway. The corruption the length-checksum actually protects against moves the
  envelope/content boundary *without* changing the total, so
  `test_a_frame_whose_lengths_were_swapped_is_the_tail` was added and the mutant retargeted.
- `rotation_ignores_the_incoming_record` survived because the rotation case used records
  larger than the segment bound, so every one took the "too big to fit anywhere" path and
  the threshold was never exercised. The case now mixes one oversized record with seven that
  fit and asserts the tight bound (`bytes <= max or records == 1`) plus "no empty segment".
- `an_oversize_record_rotates_for_ever` could not be killed at all, and the reason was that
  the branch it edited (`or active.records == 0`) was **unreachable**: a freshly opened
  segment is returned straight to the record that opened it, so the active segment is never
  empty at that check. The branch is deleted and the mutant with it (`510483c`) — R40
  forbids both keeping an unkillable claim and weakening a case to kill it.
- `a_closed_sink_starts_a_writer` survived because `flush` returns before reaching the
  executor; `ack` now refuses a closed sink before any bookkeeping moves, and the case
  drives `rotate`/`ack`/`read_segment`.

### The reviewer's own reproductions, re-run at `f55c401`

`a1_loop.py` (event-loop watchdog, 10 ms ticks, each syscall blocked 2 s in turn):

```
block=['write'] paused_case=False: worst add+finish 3.1 ms, max loop gap 21 ms
block=['fsync'] paused_case=False: worst add+finish 3.8 ms, max loop gap 17 ms
block=['open'] paused_case=False: worst add+finish 3.2 ms, max loop gap 23 ms
block=['free'] paused_case=False: worst add+finish 3.2 ms, max loop gap 25 ms
   flush-while-paused took 2.0
block=['free'] paused_case=True: worst add+finish 6.1 ms, max loop gap 24 ms
sync ack() called on the loop with slow unlink: max loop gap 15 ms
```

The paused flush still *takes* 2.0 s — its writer is inside a 2-second `statvfs`, which is
the point — while the loop gap is 24 ms instead of 2,006 ms. (`ack` is a coroutine now, so
that line of the script no longer awaits anything; the awaited path is covered by
`test_no_filesystem_call_ever_happens_on_the_event_loop`.)

`a2_err.py` (300 flushes against a disk that fails every write, then EMFILE on open, then a
`RuntimeError` from the writer, then a cancelled flush):

```
[ENOSPC every write (incl. header)] 300 flushes: fds 8->8, files on disk 1, tracked segments 0, spool_bytes 0, losses {'disk_error': 300}, dropped 300, accepted 300, in_mem_content 0, in_mem_md 0, flush raised=None
[EIO every write] 300 flushes: fds 8->8, files on disk 1, tracked segments 0, spool_bytes 0, losses {'disk_error': 300}, dropped 300, accepted 300, in_mem_content 0, in_mem_md 0, flush raised=None
[EMFILE on open] losses {'disk_error': 50} dropped 50 content 0 paused False fds 8 8
flush returned
   stats after: {'accepted': 5, 'dropped': 5, 'in_memory': 0, 'in_memory_content_bytes': 0, 'in_memory_metadata_bytes': 0, 'appended': 0, 'loss_reasons': {'disk_error': 5}}
[cancelled flush] wait_for timed out (flush cancelled)
   stats after the writer finished and two more flushes: {'accepted': 5, 'dropped': 0, 'in_memory': 0, 'in_memory_content_bytes': 0, 'in_memory_metadata_bytes': 0, 'appended': 5, 'fsynced': 5, 'loss_reasons': {}, 'spool_unacked_records': 5}
   on disk: 5 records over 1 segments; torn tails 0, poison 0, unreadable 0, unread 0 B
```

Round 1 read `fds 7->307`, 300 orphan files, a flush that raised with five records gone and
an empty loss table, and 5,000,000 bytes charged for ever after a cancellation. The one
remaining file is `.writer.lock` (verified by listing the directory), not a segment.

`a3_mem.py` (a stalled writer, then 300,000 metadata-only records with fire-and-forget
flushes):

```
md=256 content=1000000 n=2000 fire_and_forget=False: rss 36->290 MiB, peak charged 260,000,000 (budget 260,046,848), in_memory 1999, in-flight behind writer 1, md bytes 512,000, results {(True, 'accepted_in_memory'): 260, (False, 'accepted_in_memory'): 1740}, losses {'memory_budget': 1740}
md=0 content=0 n=300000 fire_and_forget=True: rss 56->60 MiB, peak charged 0 (budget 260,046,848), in_memory 10000, in-flight behind writer 1, md bytes 0, results {(False, 'accepted_in_memory'): 10001, (False, 'dropped'): 289999}, losses {'queue_full': 289999}
```

Round 1 had 299,901 records in flight and RSS 55 → 631 MiB on the last line. One flush in
flight puts the queue ceiling back in charge: 10,000 in memory, one batch behind the writer,
and the rest honestly `queue_full`.

`a6_r42.py` (R42 across writer failures, repeated fsync rounds, an idle flush, the index
flip):

```
ONE capture, losses: {'memory_budget': 1} sum = 1 dropped 1 accepted 1
fsync EIO: losses {'memory_budget': 1} dropped 0 appended 1 fsynced 0 | still shippable from disk: 1 records over 1 segments; torn tails 0, poison 0, unreadable 0, unread 0 B
3 fsync rounds on one segment: appended 3 fsynced 3
   tail appended 4 fsynced 3
   idle flush after interval: fsynced 4
one bit flipped in a frame's index field: 1 records over 1 segments; torn tails 1, poison 0, unreadable 0, unread 575 B ids [('trace-18d764dc14d87c86ebf0-000000.seg', 0)] requests ['00000000']
```

Round 1: `{'memory_budget': 1, 'disk_error': 1}` for one capture on both the write and fsync
paths, `appended 3 fsynced 6` over three rounds, no fsync on an idle tick, and two records
under one id after the bit flip.

`a5_threads.py` (eight OS threads calling `add`) still reports drift — `peak observed
1,160,508, overshoot 0 B; residual charge with all captures closed: 1,036,880` — which is
why round 2 states one loop and one writer as a **requirement** in the module docstring and
in Limits, rather than calling it an assumption.

### Nonblocking items taken in this pass

`Scan.torn_at` / `Scan.unread_bytes` (T2 can now tell a benign tail from mid-segment
corruption, case: `test_a_torn_tail_reports_where_it_stopped`); directory `fsync` after a
segment is created and after an ack; `flock` on the spool directory
(`test_two_sinks_cannot_share_one_spool_directory`); a closed state
(`test_a_closed_sink_refuses_and_starts_no_second_writer`); one flush in flight; partial
write bytes re-measured into `spool_bytes`; `SegmentView.adopted` so T2 scans a segment
whose counts this process cannot know; content parts handed to the writer **unjoined**, so
`finish` no longer memcpys up to 96 MiB on the loop (peak RSS in the microbenchmark
322 → 301 MiB); and the reviewer's truncation sweep kept as a case.

### Still open, deliberately

- **The metadata reserve charges the declared `envelope.metadata_bytes`, not the serialized
  length.** Charging the real length is what the review suggested, and it cannot be done
  here: the exported conformance case `trace_bounds__metadata_exhaustion_drops_with_counters`
  offers a 64-byte-declared envelope against a 64-byte reserve and requires it to be
  *accepted*, while its real payload is ~400 bytes. Changing the charge would fail the
  frozen suite, so it is integration request 5 (a contract question for F2) rather than a
  local edit. The unbounded growth it was raised for is fixed by the flush guard.
- `crash()` is still a public truncating method on the production class, used only by the
  harness hook. Moving it into the test would mean the test owning segment internals; a
  rename to something unmistakable is integration request 6.
- The fsync-error path counts `disk_error` without incrementing `dropped`, because the
  record was accepted and appended rather than refused; it may still be readable on disk.
- `recover()` returns a **superset** of the promise: it replays every checksum-valid frame,
  including appended-but-unsynced ones the host did not lose. The promise is that fsynced
  records are *always* there, not that nothing else is.
- Segment ordering across a restart follows the boot id, which is time-based: a host whose
  wall clock jumps backwards between boots would sort a newer segment before an older one.
  T2 dedupes by stable id and does not depend on global order.
- `clock or FakeClock()` is inherited from the shared accounting; a real sink should be
  handed the clock it reads (integration request 7).

## Verification log

- 2026-09-21: Authored from the runs quoted above on the pinned local environment. Every
  count, timing and byte figure is copied from command output; nothing here is a claim
  about ClickHouse, S3, a gateway process or a real host crash, none of which was
  exercised.
- 2026-09-21 (round 2): Review round 1's six blocking findings fixed at `f55c401`; two false
  sentences corrected in place above and the single-producer note rewritten as a
  requirement; mutants 26 → 58, all killed; one further R42 hole found while writing B5's
  case and fixed. Three cases were strengthened and one unreachable branch deleted because
  their mutants could not otherwise die. Nothing new is claimed about integration.
