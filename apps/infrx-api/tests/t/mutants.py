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
POISON = "test_a_poison_record_does_not_stop_the_scan"
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
# round 2
LOOP = "test_no_filesystem_call_ever_happens_on_the_event_loop"
OPEN_FAIL = "test_a_failed_segment_open_leaks_no_descriptor_and_no_file"
CANCELLED = "test_a_cancelled_flush_still_settles_its_batch"
WRITER_BUG = "test_a_writer_error_that_is_not_an_oserror_still_settles_the_batch"
ONE_FLUSH = "test_only_one_flush_is_ever_in_flight"
ID_REUSE = "test_a_record_id_is_never_reused_after_an_ack_and_a_restart"
BITFLIP = "test_a_flipped_bit_in_a_frame_header_is_the_tail_never_a_wrong_identity"
SWAPPED = "test_a_frame_whose_lengths_were_swapped_is_the_tail"
ONE_LOSS = "test_one_capture_counts_one_loss_even_when_the_writer_refuses_it"
PARTS = "test_retained_content_is_released_with_its_charge"
METADATA = "test_the_metadata_reserve_is_released_as_rows_are_written"
FSYNC_ROUNDS = "test_repeated_fsync_rounds_count_each_record_once"
IDLE_FSYNC = "test_an_idle_flush_fsyncs_the_appended_tail"
TWO_SINKS = "test_two_sinks_cannot_share_one_spool_directory"
CLOSED = "test_a_closed_sink_refuses_and_starts_no_second_writer"
TORN_AT = "test_a_torn_tail_reports_where_it_stopped"
# round 3
CANCELLING = "test_a_cancelling_flusher_cannot_take_a_second_batch"
PAYLOAD_CAP = "test_the_queue_is_bounded_in_bytes_as_well_as_in_rows"
CLOCK = "test_a_sink_without_a_clock_refuses_to_exist"
MULTI_PART = "test_a_multi_part_capture_spools_its_parts_in_order_and_byte_exact"
FSYNC_FLAGS = "test_a_good_fsync_clears_the_loss_flags_it_promised"
DIR_FSYNC = "test_a_segment_and_its_deletion_are_both_committed_to_the_directory"
PARTIAL = "test_bytes_a_failed_write_left_behind_still_count_against_the_cap"
ADOPTED = "test_an_adopted_segment_says_its_counts_are_unknown"
UNREAD = "test_an_unreadable_segment_reports_every_byte_as_unread"
CLOSE_RACE = "test_close_admits_nothing_once_it_has_started_and_joins_off_the_loop"
FSYNC_RAISES = "test_an_fsync_step_that_raises_leaves_the_books_agreeing"
OWNERSHIP = "test_a_failed_ack_keeps_the_segment_and_a_failed_boot_keeps_no_lock"
MOVED_FRAME = "test_a_frame_excised_or_duplicated_mid_segment_is_the_tail"
PARTS_RELEASE = PARTS


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
       "        if frame_checksum(payload, (content,), content_bytes) != crc:",
       "        if False:", CORRUPT),
    _m("the_checksum_ignores_the_lengths", "a frame's lengths are inside its checksum",
       "    crc = binascii.crc32(LENGTHS.pack(len(payload), content_bytes, position))\n"
       "    crc = binascii.crc32(payload, crc)",
       "    crc = binascii.crc32(payload)", SWAPPED),
    _m("the_checksum_ignores_the_position", "a moved frame is not another record's id",
       "    crc = binascii.crc32(LENGTHS.pack(len(payload), content_bytes, position))",
       "    crc = binascii.crc32(LENGTHS.pack(len(payload), content_bytes, 0))",
       MOVED_FRAME),
    _m("the_writer_checksums_the_wrong_position", "the writer and reader agree on position",
       "                crc = frame_checksum(row.payload, row.parts, row.content_bytes,\n"
       "                                     segment.records)",
       "                crc = frame_checksum(row.payload, row.parts, row.content_bytes, 0)",
       TWICE, MOVED_FRAME),
    _m("the_reader_numbers_records_by_hand", "a record's id is its verified position",
       "        position, index = index, index + 1", "        position, index = 0, index + 1",
       BITFLIP),
    _m("a_poison_record_shifts_the_ids_after_it", "a poison row does not renumber the rest",
       "            scan.poison += 1\n            continue",
       "            scan.poison += 1\n            index -= 1\n            continue",
       POISON),
    _m("torn_tail_crashes_the_reader", "a torn tail is tolerated, not raised",
       "        if len(data) - offset < FRAME.size:\n"
       "            _torn(scan, name, offset, len(data))\n            break",
       "        if False:\n"
       "            _torn(scan, name, offset, len(data))\n            break",
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
       "            fsync_due = ((now - self._last_fsync).total_seconds()\n"
       "                         >= self.limits.trace_fsync_interval_s)",
       "            fsync_due = True", CONFORMANCE),
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
       "            if active.written + need <= self.segment_max_bytes:",
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
       "        elif self.paused:\n            # Nowhere for this record to land",
       "        elif False:\n            # Nowhere for this record to land",
       FULL_SPOOL),
    # --- the request path never pays -------------------------------------------
    _m("the_writer_runs_on_the_event_loop", "the request path never waits for the disk",
       "            future = self._submit(self._write_batch, batch, fsync_due)",
       "            future = asyncio.get_running_loop().create_future()\n"
       "            future.set_result(self._write_batch(batch, fsync_due))", SLOW, LOOP),
    _m("an_unserializable_envelope_raises", "nothing in the trace path raises into the request",
       "            except Exception:                 # noqa: BLE001 - R37: never raise into the",
       "            except ZeroDivisionError:         # noqa: BLE001 - R37: never raise into the",
       LATTICE),
    _m("an_oversized_envelope_is_written", "the writer never spools a frame the reader refuses",
       "                if len(payload) > MAX_ENVELOPE_BYTES:", "                if False:",
       OVERSIZE),
    _m("understated_content_is_accepted", "the bytes held and the bytes declared agree",
       "        elif content_bytes != envelope.content_bytes:", "        elif False:",
       AGREE),
    _m("shutdown_is_a_silent_loss", "a process that stops counts what it dropped",
       "            self.loss_reasons[TraceLossReason.shutdown] += lost\n"
       "            self.content_bytes = self.metadata_bytes = 0",
       "            self.content_bytes = self.metadata_bytes = 0",
       SHUTDOWN),
    _m("the_capture_list_grows_for_ever", "the capture list is bounded",
       "        if len(self.captures) >= self._prune_at:", "        if False:", PRUNE),

    # --- round 1 of review: the request path never waits for a syscall --------------
    _m("the_pause_recheck_runs_on_the_loop", "no syscall on the event loop (B1)",
       "        if not batch and self.paused:\n"
       "            # Nothing to write, but the pause is this thread's to re-evaluate",
       "        if False:\n"
       "            # Nothing to write, but the pause is this thread's to re-evaluate",
       LOOP, FLOOR),
    _m("ack_unlinks_on_the_callers_thread", "an ack's syscalls belong to the writer (B1)",
       "        await self._run(self._unlink_acked, segment.path)",
       "        self._unlink_acked(segment.path)", LOOP),
    _m("the_shipper_reads_on_the_loop", "a segment read belongs to the writer (B1)",
       "        return await self._run(self._read_segment, name)",
       "        return self._read_segment(name)", LOOP),
    # --- the descriptor and the orphan ----------------------------------------------
    _m("a_failed_open_keeps_its_descriptor", "a failed segment open closes its fd (B2)",
       "            self._close_segment(segment)\n            try:\n"
       "                self.io.unlink(path)",
       "            try:\n"
       "                self.io.unlink(path)", OPEN_FAIL),
    _m("a_failed_open_leaves_the_file", "a header-less segment is not left behind (B2)",
       "                self.io.unlink(path)\n            except OSError:\n                pass\n"
       "            raise",
       "                pass\n            except OSError:\n                pass\n"
       "            raise", OPEN_FAIL),
    # --- the batch is always settled --------------------------------------------------
    _m("a_cancelled_flush_strands_its_batch", "the batch is settled by the future (B3)",
       "            future.add_done_callback(settled)", "            pass",
       CANCELLED, WRITER_BUG),
    _m("the_writer_only_catches_oserror", "a writer bug does not eat accepted records (B3)",
       "            except Exception:                    # noqa: BLE001", "            except OSError:",
       WRITER_BUG),
    _m("settlement_ignores_a_failed_writer", "a failed writer batch is counted (B3)",
       "            result = _WriteResult(dropped=[(TraceLossReason.disk_error, row.counted)\n"
       "                                           for row in self.batch])",
       "            result = _WriteResult()", WRITER_BUG),
    _m("flushes_run_concurrently", "one flush in flight bounds what memory holds (B3)",
       "        async with self._flush_lock:", "        if True:", ONE_FLUSH),
    # --- stable ids -------------------------------------------------------------------
    _m("segment_names_forget_the_boot", "a segment name is unique for all time (B4)",
       'name = (f"{SEGMENT_PREFIX}{self.boot_id}-"\n'
       '                        f"{self._next_index:06d}{SEGMENT_SUFFIX}")',
       'name = f"{SEGMENT_PREFIX}{self._next_index:06d}{SEGMENT_SUFFIX}"',
       ID_REUSE),
    # --- R42 across the writer --------------------------------------------------------
    _m("writer_drops_forget_the_capture", "one loss per capture, writer included (B5)",
       "            self._drop(reason, counted=counted)", "            self._drop(reason)",
       ONE_LOSS),
    _m("the_row_forgets_its_loss", "the loss flag travels with the row (B5)",
       "        counted = capture.counted if capture is not None else False",
       "        counted = False", ONE_LOSS),
    _m("fsync_losses_counted_per_record", "an fsync error counts first losses only (B5)",
       "            result.fsync_losses += sum(1 for counted in flags if not counted)",
       "            result.fsync_losses += len(flags)", ONE_LOSS),
    # --- the surviving invariants of round 1 ------------------------------------------
    _m("discard_keeps_the_content_parts", "a discarded capture releases its bytes (R20)",
       "        self.parts.clear()\n        super()._discard(reason)",
       "        super()._discard(reason)", PARTS),
    _m("take_content_keeps_the_parts", "a handed-over capture keeps no copy (R21)",
       "        parts = tuple(self.parts) if declared else ()\n"
       "        total = sum(len(part) for part in parts)\n        self.parts.clear()",
       "        parts = tuple(self.parts) if declared else ()\n"
       "        total = sum(len(part) for part in parts)", PARTS),
    _m("the_metadata_charge_is_never_released", "a written row frees its metadata (R24)",
       "        sink.metadata_bytes = max(0, sink.metadata_bytes\n"
       "                                  - sum(row.metadata_bytes for row in self.batch))",
       "        sink.metadata_bytes = sink.metadata_bytes", METADATA),
    _m("synced_records_never_advance", "fsynced counts each record once (R03)",
       "        segment.synced_records = segment.records\n        segment.unsynced_counted = []",
       "        segment.unsynced_counted = []", FSYNC_ROUNDS),
    _m("an_idle_flush_never_fsyncs", "the appended tail becomes durable on a quiet host (R35)",
       "            if not batch and not self.paused and not (fsync_due and self._unsynced()):",
       "            if not batch:", IDLE_FSYNC),
    _m("rotation_ignores_the_incoming_record", "a segment's size bound includes the record (R06)",
       "            if active.written + need <= self.segment_max_bytes:",
       "            if active.written <= self.segment_max_bytes:", ROTATE),
    # No mutant for "a record too big for a segment still lands": the branch that used to
    # say so (`or active.records == 0`) was unreachable and is gone. A fresh segment is
    # returned straight to the record that opened it, so the active segment is never empty
    # at the rotation check - which is exactly why nothing could kill a mutant of it.
    _m("the_rest_of_the_batch_is_written_anyway", "a broken handle writes nothing more (R19)",
       "            if broken:\n                result.dropped.append((TraceLossReason.disk_error, row.counted))\n"
       "                continue",
       "            if False:\n                result.dropped.append((TraceLossReason.disk_error, row.counted))\n"
       "                continue", WRITE_ERROR),
    _m("shutdown_is_not_a_dropped_record", "a shutdown loss is a dropped record (R25)",
       "            self.dropped += lost\n            self.loss_reasons[TraceLossReason.shutdown] += lost",
       "            self.loss_reasons[TraceLossReason.shutdown] += lost", SHUTDOWN),
    _m("close_does_not_seal", "an orderly close promises what it can (R42)",
       "            _name, result = await self._run(self._seal_active)\n            self._apply(result)",
       "            result = _WriteResult()\n            self._apply(result)", SHUTDOWN),
    _m("crash_keeps_the_record_count", "a crash's bookkeeping follows its truncation (R40)",
       "                segment.records = segment.synced_records\n"
       "                segment.unsynced_counted = []",
       "                segment.unsynced_counted = []", RECOVER),
    _m("a_checksum_failure_discards_the_segment", "a torn tail keeps what came before (R41)",
       "        if frame_checksum(payload, (content,), content_bytes) != crc:\n"
       "            _torn(scan, name, offset, len(data))\n            break",
       "        if frame_checksum(payload, (content,), content_bytes) != crc:\n"
       "            _torn(scan, name, offset, len(data))\n            return Scan()",
       CORRUPT),
    _m("an_unmeasurable_disk_is_an_empty_one", "a disk that cannot be measured fails closed (R14)",
       "        except OSError:\n            free = 0", "        except OSError:\n            free = 1 << 62",
       FLOOR),
    _m("the_floor_ignores_the_incoming_record", "the free-disk floor includes the record (R13)",
       "                       or free - need < self.limits.trace_spool_min_free_bytes)",
       "                       or free < self.limits.trace_spool_min_free_bytes)", FLOOR),
    # --- what round 1 asked for beyond the blocking list -------------------------------
    _m("two_sinks_share_a_directory", "one writer per spool directory", 
       "        self._dir_lock: int | None = self.io.lock_dir(self.spool_dir) if lock_dir else None",
       "        self._dir_lock: int | None = None", TWO_SINKS),
    _m("a_closed_sink_still_accepts", "a closed sink keeps nothing", 
       "        if self._closed:\n            # A closed sink keeps nothing",
       "        if False:\n            # A closed sink keeps nothing", CLOSED),
    _m("a_closed_sink_starts_a_writer", "a closed sink starts no second writer",
       '        if self._closed:\n            # A closed sink does not quietly start a second writer thread for a late call.\n'
       '            raise RuntimeError("this trace sink is closed")',
       '        if False:\n            raise RuntimeError("this trace sink is closed")', CLOSED),
    _m("a_torn_tail_says_nothing_about_itself", "a torn tail reports where it stopped",
       "    scan.unread_bytes += size - offset", "    scan.unread_bytes += 0", TORN_AT),

    # --- round 2 of review ------------------------------------------------------------
    _m("a_cancelled_flush_releases_the_guard", "the guard is the batch's lifetime (B7)",
       "        if self._in_flight is not None:\n"
       "            # A batch is still with the writer.",
       "        if False:\n"
       "            # A batch is still with the writer.", CANCELLING, ONE_FLUSH),
    _m("the_settlement_keeps_the_guard", "the settlement releases the guard (B7)",
       "        if sink._in_flight is self:", "        if False:", CANCELLING),
    _m("the_queue_counts_only_rows", "the queue is bounded in bytes too (ruling 5)",
       "                elif (self.queued_payload_bytes + len(payload)\n"
       "                      > QUEUED_PAYLOAD_MAX_BYTES):",
       "                elif False:", PAYLOAD_CAP),
    _m("queued_payload_bytes_never_released", "a written row frees its payload bound",
       "        sink.queued_payload_bytes = max(0, sink.queued_payload_bytes\n"
       "                                        - sum(len(row.payload) for row in self.batch))",
       "        sink.queued_payload_bytes = sink.queued_payload_bytes", PAYLOAD_CAP),
    _m("a_clockless_sink_is_built", "a durable sink needs a real clock (ruling 7)",
       '            raise ValueError("a spool sink needs the clock it reads; None is not one")',
       "            pass", CLOCK),
    _m("only_the_first_content_part_is_written", "every part is written (B8/N22)",
       "                for part in row.parts:\n                    self.io.write(segment.fd, part)",
       "                for part in row.parts[:1]:\n                    self.io.write(segment.fd, part)",
       MULTI_PART),
    _m("content_parts_are_written_in_reverse", "the parts are written in order (B8/N23)",
       "                for part in row.parts:\n                    self.io.write(segment.fd, part)",
       "                for part in row.parts[::-1]:\n                    self.io.write(segment.fd, part)",
       MULTI_PART),
    _m("a_part_is_not_copied", "a part the caller reuses is already ours (B8/N31)",
       "            self.parts.append(part.encode() if isinstance(part, str) else bytes(part))",
       "            self.parts.append(part.encode() if isinstance(part, str) else part)",
       MULTI_PART),
    _m("a_good_fsync_keeps_the_flags", "a promised record is not counted again (B8/N26)",
       "        segment.synced_records = segment.records\n        segment.unsynced_counted = []",
       "        segment.synced_records = segment.records", FSYNC_FLAGS),
    _m("a_new_segment_is_not_committed", "a segment's name is fsynced (B8/N13)",
       "            self.io.fsync_dir(self.spool_dir)\n        except BaseException:",
       "            pass\n        except BaseException:", DIR_FSYNC),
    _m("a_deletion_is_not_committed", "an ack is fsynced (B8/N14)",
       "        self.io.fsync_dir(self.spool_dir)        # the deletion, not just the data",
       "        pass", DIR_FSYNC),
    _m("partial_write_bytes_escape_the_cap", "bytes on disk count against the cap (B8/N15)",
       "            self.spool_bytes += max(0, landed - active.written)",
       "            self.spool_bytes += 0", PARTIAL),
    _m("an_adopted_segment_is_not_flagged", "an adopted segment says so (B8/N16)",
       "                                           sealed=True, adopted=True))",
       "                                           sealed=True, adopted=False))", ADOPTED),
    _m("an_fsync_step_that_raises_escapes_the_writer", "every writer step is in the result",
       "        except BaseException:                    # noqa: BLE001\n"
       "            # An fsync step that raises is not allowed to discard",
       "        except ZeroDivisionError:                # noqa: BLE001\n"
       "            # An fsync step that raises is not allowed to discard",
       FSYNC_RAISES),
    _m("flush_raises_a_writer_failure_at_the_flusher", "one writer failure does not end tracing",
       "        except BaseException:\n"
       "            # A writer failure is already counted by the settlement. Raising it here",
       "        except asyncio.TimeoutError:\n"
       "            # A writer failure is already counted by the settlement. Raising it here",
       WRITER_BUG),
    _m("ack_forgets_the_segment_before_deleting_it", "a failed ack keeps its segment",
       "        await self._run(self._unlink_acked, segment.path)\n        with self._lock:\n"
       "            if segment in self._segments:",
       "        with self._lock:\n"
       "            if segment in self._segments:", OWNERSHIP),
    _m("a_failed_boot_keeps_the_directory_lock", "a sink that failed to start holds nothing",
       "            if self._dir_lock is not None:\n"
       "                self.io.unlock_dir(self._dir_lock)\n                self._dir_lock = None\n"
       "            raise",
       "            raise", OWNERSHIP),
    _m("a_reused_boot_id_is_accepted", "a boot id already on disk is refused",
       '                raise ValueError(f"boot id {boot_id!r} already has segments in {self.spool_dir}")',
       "                pass", OWNERSHIP),
    _m("the_boot_id_is_time_only", "two processes in one nanosecond differ (B4a)",
       '        self.boot_id = boot_id or (f"{time.time_ns():016x}"\n'
       '                                   f"{int.from_bytes(os.urandom(4), \'big\'):08x}")',
       '        self.boot_id = boot_id or f"{time.time_ns():016x}"',
       ID_REUSE),
    _m("settlement_forgets_the_counted_flag", "one loss per capture through a writer bug (N03)",
       "            result = _WriteResult(dropped=[(TraceLossReason.disk_error, row.counted)\n"
       "                                           for row in self.batch])",
       "            result = _WriteResult(dropped=[(TraceLossReason.disk_error, False)\n"
       "                                           for row in self.batch])",
       WRITER_BUG),
    _m("the_unpromised_count_is_not_reported", "the durability gap is visible (N27)",
       "        self.unpromised_records += result.fsync_failed",
       "        self.unpromised_records += 0", FSYNC_ERROR),
    _m("a_stripped_capture_spools_its_content", "the row and its bytes are one fact (N25)",
       "        parts = tuple(self.parts) if declared else ()",
       "        parts = tuple(self.parts)", PARTS_RELEASE),
    _m("a_failed_open_cleans_up_only_on_oserror", "any failed open cleans up (N29)",
       "        except BaseException:\n            # Round 1: the descriptor leaked",
       "        except OSError:\n            # Round 1: the descriptor leaked", OPEN_FAIL),
    _m("close_marks_the_sink_closed_last", "nothing enters once close has started",
       "        self._closed = True\n        writer, self._writer = self._writer, None",
       "        writer, self._writer = self._writer, None", CLOSE_RACE),
    # No mutant for "the writer join runs off the loop". It is the right shape - a thread
    # join is not the event loop's work, and round 2 measured 4,517 ms of stall through it -
    # but with the seal awaited *through the executor* first, the join has nothing left to
    # wait for: the writer is idle by then, so moving it back onto the loop changes no
    # observable behaviour. An oracle would have to be a stopwatch, which is the flake R40
    # would rather not have. The `close` race itself is killed by
    # `close_marks_the_sink_closed_last`.
    _m("an_unreadable_segment_reports_no_unread_bytes", "unreadable means all unread (B8/N19)",
       "        scan.unreadable += 1\n        scan.unread_bytes += len(data)\n"
       "        return scan\n    offset = HEADER.size",
       "        scan.unreadable += 1\n        return scan\n    offset = HEADER.size",
       UNREAD),
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
