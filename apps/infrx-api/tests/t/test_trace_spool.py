#!/usr/bin/env python3
"""T1 / TRACE-BOUNDS + F-CONTRACT: the real spool sink.

The exported suites prove the accounting the fakes specify; everything after them proves
what a fake cannot have - a segment format, an fsync boundary, a torn tail, a full disk and
a writer thread that can be stuck without the request path noticing.

    uv run --frozen pytest -q tests/t/test_trace_spool.py
    uv run --frozen pytest -q tests/t/test_trace_spool.py -s      # with the measured numbers
    INFRX_TRACE_SEQUENCES=full uv run --frozen pytest -q tests/t/test_trace_spool.py \
        -k sequence                                               # the length-4 lattice

No wall-clock assertion anywhere: time is the injected `FakeClock`, and the one test that
measures (the synthetic load) prints its numbers rather than gating on them.
"""
from __future__ import annotations

import asyncio
import atexit
import binascii
import inspect
import os
import resource
import shutil
import tempfile
import threading
import time
from collections import Counter, deque
from pathlib import Path

from infrx import traces
from infrx.contracts import codec, ports
from infrx.contracts.conformance import (Harness, MissingHook, OPTIONAL_HOOKS, run_cases,
                                         run_tracesink_sequence_properties)
from infrx.contracts.conformance import builders as b
from infrx.contracts.conformance.services import tracesink_cases
from infrx.contracts.fakes.support import FailurePlan, FakeClock, SequentialIds
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import (TraceEnvelope, TraceLossReason, TraceMode,
                                     TraceOfferResult)
from infrx.traces.spool import (FRAME, HEADER, MAX_ENVELOPE_BYTES, SpoolIO,
                                SpoolTraceSink, frame_checksum, recover, scan_segment,
                                segment_header)

ID_A = "aaaaaaaa-0000-4000-8000-00000000000a"
ID_B = "bbbbbbbb-0000-4000-8000-00000000000b"
ID_C = "cccccccc-0000-4000-8000-00000000000c"

# One base directory for the whole module, removed at exit: the lattice builds tens of
# thousands of sinks, and a `TemporaryDirectory` per sink is the slowest part of the run.
BASE = Path(tempfile.mkdtemp(prefix="infrx-t1-spool-"))
atexit.register(shutil.rmtree, BASE, ignore_errors=True)
_COUNTER: Counter = Counter()
# A generous free-disk figure for the drills, so the 2 GiB floor is not what refuses a
# record unless a case says so.
PLENTY = 64 * 1024 * 1024 * 1024


def _dir(tag: str = "sink") -> Path:
    _COUNTER[tag] += 1
    return BASE / f"{tag}-{_COUNTER[tag]:06d}"


class DrillIO(SpoolIO):
    """The filesystem seam the drills need: a slow disk, ENOSPC, a bad fsync, a fixed
    free-space figure, and a record of every call so "never truncated" is checkable."""

    def __init__(self, *, free: int = PLENTY, fail_write_on: int | None = None,
                 fail_fsync_on: int | None = None, block: threading.Event | None = None,
                 entered: threading.Event | None = None, write_error: BaseException | None = None,
                 one_shot: bool = False, free_error: BaseException | None = None) -> None:
        self.free = free
        self.fail_write_on = fail_write_on
        self.fail_fsync_on = fail_fsync_on
        self.block = block
        self.entered = entered
        self.write_error = write_error
        self.one_shot = one_shot
        self.free_error = free_error
        self.calls: Counter = Counter()
        # (operation, thread name) for every call: the deterministic oracle for "no
        # filesystem call happens on the event loop". A timing assertion would be a flake;
        # a thread name is a fact.
        self.threads: list[tuple[str, str]] = []

    def _seen(self, operation: str) -> None:
        self.calls[operation] += 1
        self.threads.append((operation, threading.current_thread().name))

    def free_bytes(self, path: Path) -> int:
        self._seen("free_bytes")
        if self.free_error is not None:
            raise self.free_error
        return self.free

    def write(self, fd: int, data: bytes) -> int:
        self._seen("write")
        if self.block is not None:
            # the slow disk: the writer thread parks here, inside the filesystem
            if self.entered is not None:
                self.entered.set()
            self.block.wait(5)
        if self.fail_write_on is not None and self.calls["write"] >= self.fail_write_on:
            if self.one_shot:
                self.fail_write_on = None
            raise self.write_error or OSError(28, "No space left on device")
        return super().write(fd, data)

    def fsync(self, fd: int) -> None:
        self._seen("fsync")
        if self.fail_fsync_on is not None and self.calls["fsync"] >= self.fail_fsync_on:
            raise OSError(5, "Input/output error")
        super().fsync(fd)

    def fsync_dir(self, path: Path) -> None:
        self._seen("fsync_dir")
        super().fsync_dir(path)

    def truncate(self, path: Path, size: int) -> None:
        self._seen("truncate")
        super().truncate(path, size)

    def unlink(self, path: Path) -> None:
        self._seen("unlink")
        super().unlink(path)

    def read(self, path: Path) -> bytes:
        self._seen("read")
        return super().read(path)

    def open_append(self, path: Path) -> int:
        self._seen("open_append")
        return super().open_append(path)


def sink(limits=None, *, io: SpoolIO | None = None, clock: FakeClock | None = None,
         tag: str = "sink", **kw) -> SpoolTraceSink:
    return SpoolTraceSink(clock or FakeClock(), limits=limits or DEFAULTS,
                          failures=FailurePlan(), spool_dir=_dir(tag),
                          io=io if io is not None else DrillIO(), **kw)


# The suites build a fresh sink per case and the lattice one per sequence - a quarter of a
# million of them at length 4 - and each has its own writer thread and an open segment.
# Nothing in the harness contract closes an adapter, and a dropped sink's descriptor is an
# integer no garbage collection closes, so the factory retires the oldest. Measured before
# this closed the descriptors: the length-2 lattice alone leaked 132, which on a host with
# the usual 1024 limit is `EMFILE` somewhere inside the length-3 run.
_LIVE: deque = deque()


def _retire(spool: SpoolTraceSink) -> SpoolTraceSink:
    _LIVE.append(spool)
    while len(_LIVE) > 32:
        old = _LIVE.popleft()
        writer, old._writer = old._writer, None
        if writer is not None:
            writer.shutdown(wait=False)
        for segment in old._segments:
            old._close_segment(segment)
        if old._dir_lock is not None:
            old.io.unlock_dir(old._dir_lock)
            old._dir_lock = None
    return spool


def factory(limits=None, **_: object) -> Harness:
    """The harness contract (contracts README): a fresh, empty adapter every call, the
    clock the adapter reads, and the documented hooks. The same shape as the fake's, so
    the exported suites and the lattice run against the real sink unmodified."""
    clock, ids = FakeClock(), SequentialIds()
    spool = _retire(sink(limits, clock=clock))
    return Harness(port=spool, clock=clock, ids=ids, failures=spool.failures,
                   extra={"queued": lambda: list(spool.queued), "crash": spool.crash,
                          "content_budget": lambda: spool.content_budget,
                          "reap": spool.reap})


async def capture_one(spool: SpoolTraceSink, request_id: str, content: bytes,
                      *, metadata_bytes: int = 64) -> TraceOfferResult:
    """One whole captured request - open, add, finish - which is what G does per request."""
    with spool.open(request_id, b.ORG_A, TraceMode.full, spool.clock.at(600)) as capture:
        assert capture.add(content) is True
        return await capture.finish(b.trace(request_id, content_bytes=len(content),
                                            metadata_bytes=metadata_bytes))


def request_id(index: int) -> str:
    return f"{index:08x}-0000-4000-8000-{index:012x}"


# ======================================================================================
# F-CONTRACT: the exported suites, against the real adapter
# ======================================================================================
def test_the_spool_sink_passes_the_exported_tracesink_conformance_suite():
    """F-CONTRACT: the same cases the fake passes, with no case skipped for a missing
    hook - a skip is never a pass (R32), so the run reports both numbers."""
    cases = tracesink_cases()
    skipped: list[MissingHook] = []
    ran = run_cases(cases, factory, skipped=skipped)
    print(f"\ntracesink conformance against SpoolTraceSink: {ran}/{len(cases)} cases ran, "
          f"{len(skipped)} skipped")
    assert skipped == [], [(s.case, s.hook) for s in skipped]
    assert ran == len(cases)


def test_the_factory_supplies_every_documented_hook():
    """The other half of R32: the hooks the suite may skip on are all present, so the
    count above is the whole suite and not a subset that looked complete."""
    harness = factory()
    provided = set(harness.extra) | ({"failures"} if harness.failures is not None else set())
    assert OPTIONAL_HOOKS["tracesink"] - provided == set()


def test_the_spool_sink_satisfies_the_declared_protocols():
    """The port shape, not just the behaviour: `open`, `add` and `reap` are synchronous
    because they run on the request path, and everything else is a coroutine."""
    spool = sink()
    capture = spool.open(ID_A, b.ORG_A, TraceMode.full, spool.clock.at(600))
    assert isinstance(spool, ports.TraceSink)
    assert isinstance(capture, ports.TraceCapture)
    assert not inspect.iscoroutinefunction(capture.add)
    assert not inspect.iscoroutinefunction(spool.open)
    assert not inspect.iscoroutinefunction(spool.reap)
    for name in ("offer", "stats", "flush"):
        assert inspect.iscoroutinefunction(getattr(spool, name)), name
    for name in ("finish", "abandon"):
        assert inspect.iscoroutinefunction(getattr(capture, name)), name


def test_a_sink_without_a_spool_directory_refuses_to_exist():
    """08 §5: `TRACE_SPOOL_DIR` unset *disables capture*. A sink with nowhere to spool
    would have to pretend, so it refuses at construction and G builds none."""
    try:
        SpoolTraceSink(FakeClock(), limits=DEFAULTS.replace(trace_spool_dir="  "))
    except ValueError as error:
        assert "TRACE_SPOOL_DIR" in str(error)
    else:
        raise AssertionError("a sink was built with nowhere to spool")


def test_every_bounded_capture_sequence_holds_the_invariants_for_the_spool_sink():
    """TRACE-BOUNDS / R42: the lattice, against the real sink.

    The T1/G1 note is explicit that this is what caught two double-counting routes a
    one-off case per path had missed, so the durable sink runs it too rather than trusting
    that inheriting the accounting inherits its proof. Length 3 exhaustively by default
    (the length-4 product is ~250k sinks, i.e. ~250k directories and fsyncs, which is a
    different kind of test); `INFRX_TRACE_SEQUENCES=full` runs the whole product.
    """
    full = os.environ.get("INFRX_TRACE_SEQUENCES", "").lower() in ("full", "4")
    started = time.monotonic()
    report = run_tracesink_sequence_properties(factory, max_length=4 if full else 3)
    elapsed = time.monotonic() - started
    print(f"\nspool sink sequence properties: {report.line()} in {elapsed:.1f}s")
    assert report.unfired() == (), f"guarded invariants never reached: {report.unfired()}"
    assert report.sampled_lengths == ()
    assert report.sequences >= 10_000, report.line()


# ======================================================================================
# TRACE-RECOVER: the segment format and replay
# ======================================================================================
def test_a_captured_request_round_trips_through_the_segment_reader():
    """The content T2 ships comes out of the spool byte for byte, under the envelope it
    was filed with: a spool that kept only metadata would pass every accounting case and
    ship nothing."""
    async def scenario():
        spool = sink()
        content = b"the canonical request and response object" * 32
        assert await capture_one(spool, ID_A, content) is TraceOfferResult.accepted_in_memory
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        stats = await spool.flush(spool.clock.now())
        assert stats["appended"] == 1 and stats["fsynced"] == 1
        assert stats["in_memory"] == 0 and stats["in_memory_content_bytes"] == 0
        scan = recover(spool.spool_dir)
        assert [row.request_id for row in scan.records] == [ID_A]
        assert scan.contents == [content]
        assert scan.records[0].content_bytes == len(content)
        assert scan.torn == 0 and scan.poison == 0 and scan.unreadable == 0
        assert scan.ids == [(spool.segments()[0].name, 0)]
        await spool.close()
    asyncio.run(scenario())


def test_recovery_replays_only_fsynced_records_and_tolerates_a_torn_tail():
    """TRACE-RECOVER: durability begins at fsync.

    The crash is taken between the append and the fsync - the batch interval has not
    elapsed, which is the deterministic spelling of "kill the writer between append and
    fsync" - so the records that recover are exactly the ones an fsync had covered, not
    the ones the OS happened to have written.
    """
    async def scenario():
        spool = sink()
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        await capture_one(spool, ID_A, b"promised")
        after_fsync = await spool.flush(spool.clock.now())
        assert (after_fsync["appended"], after_fsync["fsynced"]) == (1, 1)
        await capture_one(spool, ID_B, b"unpromised")      # appended, interval not elapsed
        appended = await spool.flush(spool.clock.now())
        assert (appended["appended"], appended["fsynced"]) == (2, 1)
        assert len(recover(spool.spool_dir).records) == 2, "the OS had both; only one promised"
        lost = spool.crash()
        assert lost == 1
        stats = await spool.stats()
        assert stats["fsynced"] == 1 and stats["appended"] == 1
        assert stats["loss_reasons"]["shutdown"] == 1
        # the segment's own bookkeeping follows the truncation, or T2 ships a record count
        # that includes what the crash took away
        assert stats["spool_unacked_records"] == 1
        assert spool.segments()[0].records == 1
        scan = recover(spool.spool_dir)
        assert [row.request_id for row in scan.records] == [ID_A], scan.line()
        # a torn frame after the promised prefix is tolerated, not fatal
        segment = spool.spool_dir / spool.segments()[0].name
        with open(segment, "ab") as handle:
            handle.write(FRAME.pack(4_096, 0, 0)[:7])           # half a frame header
        torn = recover(spool.spool_dir)
        assert [row.request_id for row in torn.records] == [ID_A]
        assert torn.torn == 1, torn.line()
    asyncio.run(scenario())


def test_replaying_twice_yields_each_record_exactly_once():
    """TRACE-RECOVER: "replay segments twice" must not duplicate a logical trace. Replay
    is a read, so this is about the scan being idempotent and its stable ids unique - the
    sink deliberately does not dedupe by request (R42), so `(segment, index)` is what the
    projection keys on."""
    async def scenario():
        spool = sink()
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        for rid in (ID_A, ID_B, ID_C):
            await capture_one(spool, rid, b"x" * 100)
        await spool.flush(spool.clock.now())
        first, second = recover(spool.spool_dir), recover(spool.spool_dir)
        assert len(first.records) == 3
        assert [r.request_id for r in first.records] == [r.request_id for r in second.records]
        assert len(set(first.ids)) == len(first.ids) == 3
        assert first.ids == second.ids
        await spool.close()
    asyncio.run(scenario())


def test_a_corrupt_record_is_not_replayed_and_stops_the_tail():
    """A checksum is only worth the record it refuses: a flipped byte must not come back
    as a trace, and the records before it must still be there."""
    async def scenario():
        spool = sink()
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        await capture_one(spool, ID_A, b"first")
        await capture_one(spool, ID_B, b"second")
        await spool.flush(spool.clock.now())
        path = spool.spool_dir / spool.segments()[0].name
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 0xFF                                  # corrupt the last record's content
        path.write_bytes(bytes(raw))
        scan = recover(spool.spool_dir)
        assert [row.request_id for row in scan.records] == [ID_A], scan.line()
        assert scan.torn == 1
        # the same through the segment reader itself, which is what T2 calls: a checksum
        # failure must stop the scan and keep what came before it, not discard the segment
        direct = scan_segment(spool.segments()[0].name, path.read_bytes())
        assert [row.request_id for row in direct.records] == [ID_A], direct.line()
        assert direct.torn == 1 and direct.torn_at != {} and direct.unread_bytes > 0
        await spool.close()
    asyncio.run(scenario())


def test_a_poison_record_does_not_stop_the_scan():
    """A frame whose checksum is good but whose payload is not a TraceEnvelope is one bad
    row, not the end of the spool: T2 quarantines it and ships the rest."""
    name = "trace-000000.seg"
    junk = b'{"not":"an envelope"}'
    good = codec.compact_bytes(b.trace(ID_A, content_bytes=0, metadata_bytes=16))
    data = (segment_header()
            + FRAME.pack(len(junk), 0, frame_checksum(junk, (), 0)) + junk
            + FRAME.pack(len(good), 0, frame_checksum(good, (), 0)) + good)
    scan = scan_segment(name, data)
    assert scan.poison == 1 and scan.torn == 0
    assert [row.request_id for row in scan.records] == [ID_A]
    assert scan.ids == [(name, 1)]


def test_a_frame_claiming_more_than_a_frame_may_hold_is_the_tail():
    """The reader's frame ceiling is the other half of the writer's: a frame whose length
    field is past `MAX_ENVELOPE_BYTES` is the tail, even when its checksum agrees, because
    the writer is not allowed to have produced one. Without the ceiling this comes back as
    a poison record instead - a corrupt length field silently promoted to a bad row."""
    payload = b"j" * (MAX_ENVELOPE_BYTES + 1)
    data = (segment_header()
            + FRAME.pack(len(payload), 0, frame_checksum(payload, (), 0)) + payload)
    scan = scan_segment("trace-000000.seg", data)
    assert scan.torn == 1 and scan.poison == 0 and scan.records == []


def test_an_unknown_segment_version_is_never_half_parsed():
    """A segment a future writer produced is left alone rather than guessed at."""
    scan = scan_segment("trace-000000.seg", HEADER.pack(b"INFRXTRC", 99) + b"whatever")
    assert scan.unreadable == 1 and scan.records == []
    assert scan_segment("trace-000000.seg", b"tiny").unreadable == 1
    assert scan_segment("trace-000000.seg", HEADER.pack(b"NOTOURS!", 1)).unreadable == 1


def test_rotation_seals_by_size_and_keeps_every_record():
    """Segments rotate by size, and a rotation fsyncs before it closes: sealing an
    unsynced tail would quietly unpromise records this process had already appended."""
    async def scenario():
        spool = sink(segment_max_bytes=512)
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        for index in range(6):
            await capture_one(spool, request_id(index), b"y" * 200)
        await spool.flush(spool.clock.now())
        views = spool.segments()
        assert len(views) > 1, "no rotation happened at all"
        assert sum(view.records for view in views) == 6
        # The bound is the configured one, not a generous multiple of it: a segment may
        # only exceed it when it holds a single record too big to fit anywhere (and then it
        # holds exactly that one). A loose bound here let rotation ignore the incoming
        # record's size entirely.
        assert all(view.bytes <= 512 or view.records == 1 for view in views), \
            [(v.bytes, v.records) for v in views]
        assert all(view.records >= 1 for view in views), "an empty segment was created"
        stats = await spool.stats()
        assert stats["appended"] == 6 and stats["fsynced"] == 6, stats
        assert len(recover(spool.spool_dir).records) == 6
        await spool.close()
    asyncio.run(scenario())


def test_the_shipper_acks_whole_segments_and_nothing_is_ever_truncated():
    """TRACE-RECOVER: no copytruncate. A shipped segment is unlinked whole, the active one
    cannot be acked at all, and the only `truncate` in the module is the crash model."""
    async def scenario():
        io = DrillIO()
        spool = sink(io=io, segment_max_bytes=512)
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        await capture_one(spool, ID_A, b"z" * 100)
        await spool.flush(spool.clock.now())
        active = spool.segments()[0]
        assert active.sealed is False
        assert await spool.ack(active.name) is False, "the active segment was acked"
        assert await spool.ack("trace-999999.seg") is False
        name = await spool.rotate()
        assert name == active.name
        assert spool.segments()[0].sealed is True
        shipped = await spool.read_segment(name)
        assert [row.request_id for row in shipped.records] == [ID_A]
        assert await spool.ack(name) is True
        assert spool.segments() == ()
        assert (await spool.stats())["spool_bytes"] == 0
        assert not (spool.spool_dir / name).exists()
        assert io.calls["truncate"] == 0, "a segment was rewritten in place"
        assert io.calls["unlink"] == 1
        assert await spool.rotate() is None              # nothing open to seal
        await spool.close()
    asyncio.run(scenario())


def test_a_restart_adopts_existing_segments_and_never_reuses_a_name():
    """Unshipped segments from the previous process still count against the host cap, and
    the new process starts a new file: appending behind a torn tail would lose every
    record after it, because that is where the reader stops."""
    async def scenario():
        first = sink(tag="restart")
        first.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        await capture_one(first, ID_A, b"before the restart")
        await first.flush(first.clock.now())
        old = first.segments()[0]
        await first.close()

        second = SpoolTraceSink(FakeClock(), limits=DEFAULTS, spool_dir=first.spool_dir,
                                io=DrillIO(), boot_id="bbbbbbbbbbbbbbbbbbbb")
        assert [view.name for view in second.segments()] == [old.name]
        assert second.segments()[0].sealed is True
        assert (await second.stats())["spool_bytes"] == old.bytes
        second.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        await capture_one(second, ID_B, b"after the restart")
        await second.flush(second.clock.now())
        names = [view.name for view in second.segments()]
        assert names[0] == old.name and len(names) == 2 and names[1] != old.name
        assert len(recover(second.spool_dir).records) == 2
        await second.close()
    asyncio.run(scenario())


# ======================================================================================
# TRACE-BOUNDS: the failure drills
# ======================================================================================
def test_a_slow_disk_never_blocks_the_request_path():
    """TRACE-BOUNDS: "slow/full disk; inference continues". The writer parks inside the
    filesystem and the request path keeps capturing, because `add`/`offer` are counter
    updates on the event loop and the disk is somebody else's thread."""
    async def scenario():
        block, entered = threading.Event(), threading.Event()
        io = DrillIO(block=block, entered=entered)
        spool = sink(io=io)
        await capture_one(spool, ID_A, b"first")
        flushing = asyncio.create_task(spool.flush(spool.clock.now()))
        await asyncio.to_thread(entered.wait, 5)
        assert entered.is_set(), "the writer never reached the disk"
        for index in range(50):                    # the writer is stuck; the sink is not
            rid = request_id(index)
            capture = spool.open(rid, b.ORG_A, TraceMode.full, spool.clock.at(600))
            assert capture.add(b"more content while the disk hangs") is True
            assert await capture.finish(b.trace(rid, content_bytes=33, metadata_bytes=16)) \
                is TraceOfferResult.accepted_in_memory
        stats = await spool.stats()
        assert stats["accepted"] == 51 and stats["in_memory"] == 50
        assert stats["appended"] == 0, "the blocked writer appended something"
        assert not flushing.done()
        block.set()
        await flushing
        assert (await spool.stats())["appended"] == 1
        await spool.close()
    asyncio.run(scenario())


def test_a_full_spool_drops_with_disk_budget_and_comes_back_on_an_ack():
    """TRACE-BOUNDS: at the host cap the record is dropped and counted, inference is
    untouched, and the shipper acking a segment is what lifts the pause. Nothing is
    deleted to make room: only the shipper knows what has been shipped."""
    async def scenario():
        limits = DEFAULTS.replace(trace_spool_max_bytes=2_048, trace_spool_min_free_bytes=0)
        spool = sink(limits, segment_max_bytes=1_024)
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        for index in range(12):
            await capture_one(spool, request_id(index), b"c" * 200)
            await spool.flush(spool.clock.now())
        stats = await spool.stats()
        assert stats["spool_paused"] is True
        assert stats["spool_bytes"] <= limits.trace_spool_max_bytes
        assert stats["loss_reasons"]["disk_budget"] >= 1
        assert stats["appended"] + stats["loss_reasons"]["disk_budget"] == 12
        # a capture opened while paused still finishes - the request never fails - and the
        # loss carries the honest reason rather than a mislabelled `abandoned`
        before = stats["loss_reasons"]["disk_budget"]
        assert await capture_one(spool, ID_A, b"while paused") is TraceOfferResult.dropped
        assert (await spool.stats())["loss_reasons"]["disk_budget"] == before + 1
        await spool.rotate()                            # the shipper ships and acks
        for view in spool.segments():
            assert await spool.ack(view.name) is True
        assert spool.paused is False
        assert (await spool.stats())["spool_bytes"] == 0
        assert await capture_one(spool, ID_B, b"after the ack") \
            is TraceOfferResult.accepted_in_memory
        assert (await spool.flush(spool.clock.now()))["appended"] >= 1
        await spool.close()
    asyncio.run(scenario())


def test_the_free_disk_floor_refuses_before_the_host_runs_out():
    """The 2 GiB floor is about the host, not the spool: trace capture may not be the
    reason inference loses its disk. The pause lifts when the disk comes back - on the
    flusher's own timer, not only when the shipper acks something."""
    async def scenario():
        # free space *above* the floor, but not by as much as this record needs: the check
        # has to include the record, or the floor is crossed by exactly one write.
        io = DrillIO(free=DEFAULTS.trace_spool_min_free_bytes + 10)
        spool = sink(io=io)
        await capture_one(spool, ID_A, b"nowhere to go")
        stats = await spool.flush(spool.clock.now())
        assert stats["appended"] == 0
        assert stats["loss_reasons"]["disk_budget"] == 1
        assert stats["spool_paused"] is True
        # a record offered while paused is dropped at once, with the honest reason
        assert await capture_one(spool, ID_C, b"still nowhere") is TraceOfferResult.dropped
        assert (await spool.stats())["loss_reasons"]["disk_budget"] == 2
        io.free = PLENTY
        assert (await spool.flush(spool.clock.now()))["spool_paused"] is False
        await capture_one(spool, ID_B, b"room again")
        stats = await spool.flush(spool.clock.now())
        assert stats["appended"] == 1 and stats["spool_paused"] is False
        # a disk that cannot be measured is not a disk with room on it
        io.free_error = OSError(5, "statvfs failed")
        await capture_one(spool, request_id(7), b"unmeasurable disk")
        stats = await spool.flush(spool.clock.now())
        assert stats["appended"] == 1, "a record was written to an unmeasurable disk"
        assert stats["spool_paused"] is True
        assert stats["loss_reasons"]["disk_budget"] == 3
        io.free_error = None
        await spool.close()
    asyncio.run(scenario())


def test_a_write_error_drops_the_rest_of_the_batch_and_abandons_the_segment():
    """A disk that fails mid-batch: the handle is at an unknown offset, so the segment is
    abandoned rather than appended behind unreadable bytes, and every record the batch
    still held is counted `disk_error` instead of silently vanishing."""
    async def scenario():
        # One-shot on purpose: a disk that fails for ever would write nothing after the
        # error whatever the code did, so "the rest of the batch is dropped" would be
        # unobservable. This disk works again immediately - only the batch is abandoned.
        io = DrillIO(fail_write_on=5, one_shot=True)   # header, r1, r1, r2 head, boom
        spool = sink(io=io)
        for rid in (ID_A, ID_B, ID_C):
            await capture_one(spool, rid, b"d" * 50)
        stats = await spool.flush(spool.clock.now())
        assert stats["appended"] == 1, stats
        assert stats["loss_reasons"]["disk_error"] == 2, stats["loss_reasons"]
        assert stats["dropped"] == 2
        assert all(view.sealed for view in spool.segments()), "the broken segment stayed open"
        # what did land is still readable: a torn tail never costs the records before it
        io.fail_write_on = None
        assert len(recover(spool.spool_dir).records) == 1
        await spool.close()
    asyncio.run(scenario())


def test_an_fsync_error_never_claims_durability():
    """An fsync error is the one failure that cannot be retried into a promise, so the
    records it covered are counted `disk_error`, never reported `fsynced`, and the segment
    is sealed so the next record starts a file whose fsync may work."""
    async def scenario():
        io = DrillIO(fail_fsync_on=1)
        spool = sink(io=io)
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        await capture_one(spool, ID_A, b"e" * 40)
        stats = await spool.flush(spool.clock.now())
        assert stats["appended"] == 1
        assert stats["fsynced"] == 0, "durability was claimed over a failed fsync"
        assert stats["loss_reasons"]["disk_error"] == 1
        assert spool.segments()[0].sealed is True
        io.fail_fsync_on = None
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        await capture_one(spool, ID_B, b"f" * 40)
        stats = await spool.flush(spool.clock.now())
        assert stats["appended"] == 2 and stats["fsynced"] == 1
        assert len(spool.segments()) == 2
        await spool.close()
    asyncio.run(scenario())


def test_shutdown_is_a_counted_loss_not_a_silent_one():
    """`close` is the orderly half of `crash`: whatever is still in memory is dropped and
    counted `shutdown` (08 §3), because a process that stops is a loss the coverage
    figures have to show."""
    async def scenario():
        spool = sink()
        # one record already appended but not yet fsynced (the interval has not elapsed),
        # and one still in memory: close must promise the first and count the second
        await capture_one(spool, ID_B, b"appended, not yet promised")
        appended = await spool.flush(spool.clock.now())
        assert (appended["appended"], appended["fsynced"]) == (1, 0)
        await capture_one(spool, ID_A, b"in memory when the process stops")
        assert (await spool.stats())["in_memory"] == 1
        assert await spool.close() == 1
        stats = await spool.stats()
        assert stats["loss_reasons"]["shutdown"] == 1
        assert stats["dropped"] == 1, "a shutdown loss was not a dropped record"
        assert stats["in_memory"] == 0 and stats["in_memory_content_bytes"] == 0
        assert stats["appended"] == 1
        # close seals the active segment, and sealing fsyncs: an appended tail left
        # unpromised by an orderly shutdown is a record we could have kept and did not
        assert stats["fsynced"] == 1
        assert spool.segments()[0].sealed is True
        assert len(recover(spool.spool_dir).records) == 1
        assert await spool.close() == 0                 # idempotent
    asyncio.run(scenario())


def test_the_declared_content_and_the_charged_bytes_must_agree():
    """The accounting refuses an envelope claiming *more* content than it charged. A claim
    for *less* is the same defect from the other side: it would spool bytes the row does
    not account for, so it is dropped `malformed` with the charge released."""
    async def scenario():
        spool = sink()
        capture = spool.open(ID_A, b.ORG_A, TraceMode.full, spool.clock.at(600))
        assert capture.add(b"x" * 1_000) is True
        understated = b.trace(ID_A, content_bytes=400, metadata_bytes=16)
        assert await capture.finish(understated) is TraceOfferResult.dropped
        stats = await spool.stats()
        assert stats["in_memory"] == 0 and stats["in_memory_content_bytes"] == 0
        assert stats["loss_reasons"]["malformed"] == 1
        await spool.flush(spool.clock.now())
        assert recover(spool.spool_dir).records == []
        await spool.close()
    asyncio.run(scenario())


def test_an_envelope_too_large_for_a_frame_is_refused_not_written():
    """The writer and the reader agree on what a frame may be, or neither works: the reader
    refuses an envelope past `MAX_ENVELOPE_BYTES`, so writing one would spool bytes nothing
    can replay. It is dropped `malformed` on the way in instead."""
    async def scenario():
        spool = sink()
        huge = b.trace(ID_A, mode=TraceMode.minimal, content_bytes=0,
                       metadata_bytes=16).model_copy(update={"model_revision": "m" * (2 << 20)})
        assert await spool.offer(huge) is TraceOfferResult.dropped
        stats = await spool.stats()
        assert stats["in_memory"] == 0
        assert stats["loss_reasons"]["malformed"] == 1
        await spool.flush(spool.clock.now())
        assert recover(spool.spool_dir).records == []
        await spool.close()
    asyncio.run(scenario())


def test_the_capture_list_is_pruned_so_reap_stays_bounded():
    """A sink that never pruned its capture list grew by one dead capture per request for
    ever and made `reap` walk them all: an unbounded structure in the one component whose
    whole purpose is bounds."""
    async def scenario():
        spool = sink()
        for index in range(5_000):
            with spool.open(request_id(index), b.ORG_A, TraceMode.minimal,
                            spool.clock.at(600)):
                pass
        assert len(spool.captures) <= 2 * traces.spool.CAPTURE_PRUNE_AT, len(spool.captures)
        assert (await spool.stats())["open_captures"] == 0
        assert spool.reap() == 0
    asyncio.run(scenario())


# ======================================================================================
# round 1 of review: the request path, the writer's failures and the ids
# ======================================================================================
def test_no_filesystem_call_ever_happens_on_the_event_loop():
    """B1. The module's threading table is a promise: after construction, every syscall
    belongs to the writer thread.

    The oracle is the *thread name* each call was made on, not a timing threshold - a
    stopwatch on a shared machine is a flake, a thread name is a fact. Round 1 measured a
    2,006 ms event-loop stall through the paused flush's `statvfs` and another through
    `ack`'s `unlink`; both are here.
    """
    async def scenario():
        io = DrillIO(free=DEFAULTS.trace_spool_min_free_bytes - 1)
        spool = sink(io=io)
        io.threads.clear()                       # construction is allowed its own syscalls
        await capture_one(spool, ID_A, b"a" * 100)       # request path: no syscall at all
        assert io.threads == [], f"the request path touched the disk: {io.threads}"
        await spool.flush(spool.clock.now())             # refused: the floor is below us
        assert spool.paused is True
        await spool.flush(spool.clock.now())             # the paused re-check: statvfs
        io.free = PLENTY
        await spool.flush(spool.clock.now())
        await capture_one(spool, ID_B, b"b" * 100)
        await spool.flush(spool.clock.now())
        name = await spool.rotate()
        assert await spool.read_segment(name) is not None
        assert await spool.ack(name) is True
        offenders = [(op, thread) for op, thread in io.threads
                     if not thread.startswith("infrx-trace-spool")]
        assert offenders == [], f"filesystem calls off the writer thread: {offenders}"
        assert {op for op, _t in io.threads} >= {"free_bytes", "write", "unlink", "read"}
        await spool.close()
    asyncio.run(scenario())


def test_a_failed_segment_open_leaks_no_descriptor_and_no_file():
    """B2. A disk that refuses the header write used to leak the descriptor *and* leave the
    header-less file behind, once per flush: 300 flushes against a full disk meant 300
    orphans and 300 descriptors, i.e. `EMFILE` on the gateway. Neither is tracked anywhere,
    so nothing would ever clean them up."""
    async def scenario():
        io = DrillIO(fail_write_on=1)                    # even the header fails
        spool = sink(io=io)
        before = len(os.listdir("/proc/self/fd"))
        for index in range(60):
            await capture_one(spool, request_id(index), b"x" * 100)
            await spool.flush(spool.clock.now())
        after = len(os.listdir("/proc/self/fd"))
        stats = await spool.stats()
        assert after == before, f"descriptors leaked: {before} -> {after}"
        assert [name for name in os.listdir(spool.spool_dir)
                if name.endswith(".seg")] == [], "orphan segments were left on disk"
        assert stats["spool_segments"] == 0 and stats["spool_bytes"] == 0
        assert stats["loss_reasons"]["disk_error"] == 60
        assert stats["dropped"] == 60
        assert stats["in_memory_content_bytes"] == 0, "a failed write kept its charge"
        await spool.close()
    asyncio.run(scenario())


def test_a_cancelled_flush_still_settles_its_batch():
    """B3. `flush` has no deadline of its own, so a caller's `wait_for` is its only bound -
    and cancelling it must not strand the batch, which has already left the queue. Round 1:
    five records vanished (appended 0, dropped 0, an empty loss table) and 5 MB stayed
    charged for ever, with `fsynced` claiming 5 at the same time."""
    async def scenario():
        block, entered = threading.Event(), threading.Event()
        spool = sink(io=DrillIO(block=block, entered=entered))
        for index in range(5):
            await capture_one(spool, request_id(index), b"z" * 1_000)
        charged = (await spool.stats())["in_memory_content_bytes"]
        assert charged == 5_000
        try:
            await asyncio.wait_for(spool.flush(spool.clock.now()), 0.05)
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("the flush was not cancelled")
        assert entered.is_set(), "the writer never started"
        block.set()
        # A second flush *carrying a record* queues behind the first on the single writer
        # thread, so awaiting it is a deterministic "the cancelled batch has landed" - no
        # polling, no sleep. (An empty flush would return without queueing anything.)
        await capture_one(spool, ID_C, b"the flush after the cancelled one")
        await spool.flush(spool.clock.now())
        stats = await spool.stats()
        assert stats["appended"] == 6, stats
        assert stats["in_memory_content_bytes"] == 0, "the cancelled batch kept its charge"
        assert stats["in_memory"] == 0
        assert len(recover(spool.spool_dir).records) == 6
        await spool.close()
    asyncio.run(scenario())


def test_a_writer_error_that_is_not_an_oserror_still_settles_the_batch():
    """B3, the other half: a bug in the writer is not a disk, but a raised batch is still
    records the sink has already reported accepted. Both layers are checked - a non-OSError
    from the filesystem, and the whole call failing."""
    async def scenario():
        spool = sink(io=DrillIO(fail_write_on=2, write_error=RuntimeError("boom")))
        for index in range(5):
            await capture_one(spool, request_id(index), b"z" * 1_000)
        stats = await spool.flush(spool.clock.now())
        assert stats["appended"] + stats["dropped"] == 5, stats
        assert stats["loss_reasons"]["disk_error"] == stats["dropped"]
        assert stats["in_memory_content_bytes"] == 0, "a RuntimeError kept the charge"
        await spool.close()

        # and when the writer call itself cannot even run
        spool = sink()
        for index in range(3):
            await capture_one(spool, request_id(index), b"y" * 1_000)

        def explode(*_args):
            raise RuntimeError("the writer itself failed")

        spool._write_batch = explode
        try:
            await spool.flush(spool.clock.now())
        except RuntimeError:
            pass                                 # the caller may see it; the books may not lie
        stats = await spool.stats()
        assert stats["loss_reasons"]["disk_error"] == 3, stats["loss_reasons"]
        assert stats["dropped"] == 3
        assert stats["in_memory_content_bytes"] == 0
        assert stats["appended"] == 0
        await spool.close()
    asyncio.run(scenario())


def test_only_one_flush_is_ever_in_flight():
    """B3/memory. A caller that fires flushes on a timer without awaiting them used to pile
    batches up behind a blocked writer - 299,901 records in flight, outside the queue
    ceiling that is supposed to bound memory. One flush at a time keeps in-flight rows
    bounded by `TRACE_QUEUE_MAX`, and the rows stay in the queue where the ceiling can see
    them."""
    async def scenario():
        block, entered = threading.Event(), threading.Event()
        spool = sink(DEFAULTS.replace(trace_queue_max=4),
                     io=DrillIO(block=block, entered=entered))
        await capture_one(spool, ID_A, b"first")
        first = asyncio.create_task(spool.flush(spool.clock.now()))
        await asyncio.to_thread(entered.wait, 5)
        second = asyncio.create_task(spool.flush(spool.clock.now()))
        await asyncio.sleep(0)
        for index in range(1, 8):                # more than the queue may hold
            await capture_one(spool, request_id(index), b"more")
        stats = await spool.stats()
        assert stats["in_memory"] == 4, "the second flush took a batch of its own"
        assert stats["loss_reasons"]["queue_full"] == 3, stats["loss_reasons"]
        block.set()
        await first
        await second
        stats = await spool.stats()
        assert stats["appended"] == 5 and stats["in_memory"] == 0
        await spool.close()
    asyncio.run(scenario())


def test_a_record_id_is_never_reused_after_an_ack_and_a_restart():
    """B4(a). The steady state is drain then deploy: the shipper acks everything and the
    process restarts. With names derived from the files present, the next process reissued
    `('trace-000000.seg', 0)` for a different record, and 02's "stable event IDs" became a
    collision the projection would dedupe *away*."""
    async def scenario():
        directory = _dir("reuse")
        first = SpoolTraceSink(FakeClock(), limits=DEFAULTS, spool_dir=directory,
                               io=DrillIO())
        first.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        await capture_one(first, ID_A, b"the first process's record")
        await first.flush(first.clock.now())
        name = await first.rotate()
        before = await first.read_segment(name)
        assert await first.ack(name) is True
        assert first.segments() == ()
        await first.close()

        second = SpoolTraceSink(FakeClock(), limits=DEFAULTS, spool_dir=directory,
                               io=DrillIO())
        second.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        await capture_one(second, ID_B, b"the second process's record")
        await second.flush(second.clock.now())
        after = await second.read_segment(second.segments()[0].name)
        assert before.ids and after.ids
        assert before.ids != after.ids, "two different records were filed under one id"
        assert second.boot_id != first.boot_id
        await second.close()
    asyncio.run(scenario())


def test_a_flipped_bit_in_a_frame_header_is_the_tail_never_a_wrong_identity():
    """B4(b). The frame carried its own index, outside the checksum: flipping one bit of it
    made the reader report two records under the same id, checksum-clean, and all 32 flips
    of that field were silent. The id is now the verified position and the lengths are
    inside the checksum, so no single-bit change can produce a record with someone else's
    identity - it can only stop the scan."""
    async def scenario():
        spool = sink()
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        for index, letter in enumerate((b"A", b"B", b"C")):
            await capture_one(spool, request_id(index), letter * 60)
        await spool.flush(spool.clock.now())
        name = spool.segments()[0].name
        await spool.close()
        data = (spool.spool_dir / name).read_bytes()
        whole = scan_segment(name, data)
        assert len(whole.records) == 3
        truth = dict(zip(whole.ids, zip(whole.records, whole.contents)))
        checked = 0
        for offset in range(HEADER.size, len(data)):
            for bit in (0x01, 0x80):
                mutated = bytearray(data)
                mutated[offset] ^= bit
                scan = scan_segment(name, bytes(mutated))
                checked += 1
                assert len(scan.ids) == len(set(scan.ids)), \
                    f"one id twice at offset {offset}: {scan.ids}"
                for row_id, record, content in zip(scan.ids, scan.records, scan.contents):
                    expected = truth.get(row_id)
                    assert expected is not None, f"invented id {row_id} at offset {offset}"
                    assert (record, content) == expected, \
                        f"id {row_id} came back as another record at offset {offset}"
        print(f"\nframe identity: {checked} single-bit flips, no wrong or duplicate id")
    asyncio.run(scenario())


def test_one_capture_counts_one_loss_even_when_the_writer_refuses_it():
    """B5 / R42. A capture that has already contributed its one loss - it breached the
    budget and finished as honest metadata - and is then refused by the writer must still
    count **one**. Round 1: `{'memory_budget': 1, 'disk_error': 1}` for a single capture,
    on all three writer refusals. The lattice cannot see this, because it never makes the
    writer fail."""
    async def scenario():
        tiny = DEFAULTS.replace(trace_capture_bytes=DEFAULTS.trace_metadata_reserve_bytes + 1_000)
        refusals = {
            "write error": DrillIO(fail_write_on=1),
            "fsync error": DrillIO(fail_fsync_on=1),
            "spool cap": DrillIO(free=0),
        }
        for label, io in refusals.items():
            spool = sink(tiny, io=io)
            spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
            capture = spool.open(ID_A, b.ORG_A, TraceMode.full, spool.clock.at(600))
            assert capture.add(b"x" * 2_000) is False        # breaches: one loss counted
            assert await capture.finish(
                b.trace(ID_A, content_bytes=0, metadata_bytes=64)) \
                is TraceOfferResult.accepted_in_memory
            stats = await spool.flush(spool.clock.now())
            losses = sum(stats["loss_reasons"].values())
            assert losses == 1, f"{label}: one capture counted {stats['loss_reasons']}"
            assert stats["loss_reasons"]["memory_budget"] == 1, label
            await spool.close()
    asyncio.run(scenario())


def test_retained_content_is_released_with_its_charge():
    """B6/R20+R21. The capture keeps the parts it charged, so every route that releases the
    charge has to drop them in the same step - otherwise the budget reports bytes as free
    while the process is still holding them. Nothing asserted this: the microbenchmark
    printed resident memory but never checked it."""
    async def scenario():
        spool = sink(DEFAULTS.replace(trace_capture_bytes=5_000,
                                      trace_metadata_reserve_bytes=1_000))
        # (a) a budget breach discards the whole content
        breached = spool.open(ID_A, b.ORG_A, TraceMode.full, spool.clock.at(600))
        assert breached.add(b"x" * 3_000) is True
        assert breached.parts, "nothing was retained to begin with"
        assert breached.add(b"x" * 2_000) is False
        assert breached.parts == [], "a discarded capture kept its content"
        # (b) an abandon releases them
        abandoned = spool.open(ID_B, b.ORG_A, TraceMode.full, spool.clock.at(600))
        assert abandoned.add(b"y" * 1_000) is True
        await abandoned.abandon(TraceLossReason.abandoned)
        assert abandoned.parts == []
        # (c) a finish hands them over and keeps nothing
        kept = spool.open(ID_C, b.ORG_A, TraceMode.full, spool.clock.at(600))
        assert kept.add(b"z" * 1_000) is True
        await kept.finish(b.trace(ID_C, content_bytes=1_000, metadata_bytes=16))
        assert kept.parts == [], "a finished capture kept a second copy of its content"
        # (d) a reap releases them
        reaped = spool.open(request_id(9), b.ORG_A, TraceMode.full, spool.clock.at(10))
        assert reaped.add(b"w" * 1_000) is True
        spool.clock.advance(200)
        assert spool.reap() == 1
        assert reaped.parts == []
        await spool.flush(spool.clock.now())
        assert (await spool.stats())["in_memory_content_bytes"] == 0
        await spool.close()
    asyncio.run(scenario())


def test_the_metadata_reserve_is_released_as_rows_are_written():
    """B6/R24. The reserve is what keeps metadata flowing after content has been cut off,
    so a written row has to give its metadata bytes back. Without that the sink refuses
    everything as `metadata_budget` for ever once 8 MiB of rows have passed through - a
    process that spools perfectly and then goes silent."""
    async def scenario():
        spool = sink(DEFAULTS.replace(trace_metadata_reserve_bytes=128))
        for index in range(10):                  # 10 x 64 B, against a 128 B reserve
            spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
            assert await spool.offer(b.trace(request_id(index), content_bytes=0,
                                             metadata_bytes=64)) \
                is TraceOfferResult.accepted_in_memory, index
            stats = await spool.flush(spool.clock.now())
            assert stats["in_memory_metadata_bytes"] == 0, index
        stats = await spool.stats()
        assert stats["appended"] == 10
        assert "metadata_budget" not in stats["loss_reasons"], stats["loss_reasons"]
        await spool.close()
    asyncio.run(scenario())


def test_repeated_fsync_rounds_count_each_record_once():
    """B6/R03. `fsynced` is a count of records, so the per-segment promised mark has to
    advance with it; otherwise every fsync round re-counts the whole segment and the
    durability gap a dashboard reads goes negative."""
    async def scenario():
        spool = sink()
        for index in range(3):
            spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
            await capture_one(spool, request_id(index), b"x" * 20)
            stats = await spool.flush(spool.clock.now())
            assert stats["fsynced"] == index + 1, stats
            assert stats["appended"] == index + 1
        assert len(spool.segments()) == 1, "the records went to different segments"
        await spool.close()
    asyncio.run(scenario())


def test_an_idle_flush_fsyncs_the_appended_tail():
    """B6/R35. "fsync at most every 2 s" is also a promise that it *does* happen: on a host
    that goes quiet after one request, the appended tail must still become durable on the
    next tick rather than waiting for traffic that never comes."""
    async def scenario():
        spool = sink()
        await capture_one(spool, ID_A, b"the last record before the host went quiet")
        stats = await spool.flush(spool.clock.now())
        assert (stats["appended"], stats["fsynced"]) == (1, 0)
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        stats = await spool.flush(spool.clock.now())          # nothing in the queue
        assert stats["fsynced"] == 1, "an idle tick never promised the appended tail"
        assert len(recover(spool.spool_dir).records) == 1
        await spool.close()
    asyncio.run(scenario())


def test_two_sinks_cannot_share_one_spool_directory():
    """Round 1: two sinks on one directory adopted each other's *live* segments as sealed
    and acked them away, after which the other process kept appending to an unlinked file.
    That is not a configuration to document, it is one to refuse."""
    async def scenario():
        directory = _dir("shared")
        first = SpoolTraceSink(FakeClock(), limits=DEFAULTS, spool_dir=directory,
                               io=DrillIO())
        try:
            SpoolTraceSink(FakeClock(), limits=DEFAULTS, spool_dir=directory, io=DrillIO())
        except RuntimeError as error:
            assert "spools traces" in str(error)
        else:
            raise AssertionError("two sinks shared one spool directory")
        await first.close()
        # and the lock is released, so the next process starts normally
        second = SpoolTraceSink(FakeClock(), limits=DEFAULTS, spool_dir=directory,
                                io=DrillIO())
        await second.close()
    asyncio.run(scenario())


def test_a_closed_sink_refuses_and_starts_no_second_writer():
    """Round 1: after `close()` a `finish` still answered `accepted_in_memory` and the next
    flush started a fresh writer thread, so a shut-down process kept promising to spool
    records nothing would ever write."""
    async def scenario():
        spool = sink()
        await capture_one(spool, ID_A, b"before the close")
        await spool.flush(spool.clock.now())
        threads_before = threading.active_count()
        await spool.close()
        late = await capture_one(spool, ID_B, b"after the close")
        assert late is TraceOfferResult.dropped, "a closed sink accepted a record"
        stats = await spool.flush(spool.clock.now())
        assert stats["in_memory"] == 0
        assert stats["loss_reasons"]["shutdown"] == 1
        assert threading.active_count() <= threads_before, "a closed sink started a writer"
        assert stats["appended"] == 1
    asyncio.run(scenario())


def test_a_torn_tail_reports_where_it_stopped():
    """T2 has to tell a benign torn tail from mid-segment corruption: one flipped bit can
    cost a whole segment of *fsynced* records, and a scan that only says "torn" cannot be
    alarmed about the difference."""
    async def scenario():
        spool = sink()
        spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
        for index in range(4):
            await capture_one(spool, request_id(index), b"x" * 200)
        await spool.flush(spool.clock.now())
        name = spool.segments()[0].name
        await spool.close()
        path = spool.spool_dir / name
        data = path.read_bytes()
        clean = scan_segment(name, data)
        assert clean.torn == 0 and clean.unread_bytes == 0 and clean.torn_at == {}
        record = (len(data) - HEADER.size) // 4
        # a tail lost to a crash: at most the record it was in the middle of
        tail = scan_segment(name, data[:-10])
        assert tail.torn == 1 and tail.unread_bytes <= record, (tail.unread_bytes, record)
        assert len(tail.records) == 3
        assert tail.torn_at[name] < len(data)
        # a bit flipped in the first record: the whole segment is unread, which is the
        # alarm, not the routine tail
        mutated = bytearray(data)
        mutated[HEADER.size + FRAME.size + 4] ^= 0xFF
        corrupt = scan_segment(name, bytes(mutated))
        assert corrupt.records == [] and corrupt.torn == 1
        assert corrupt.unread_bytes >= 4 * record, (corrupt.unread_bytes, record)
        assert corrupt.torn_at[name] == HEADER.size
    asyncio.run(scenario())


# ======================================================================================
# the measured bound (synthetic microbenchmark, not GPU request latency)
# ======================================================================================
def test_synthetic_concurrent_load_stays_within_the_declared_bounds():
    """TRACE-BOUNDS, measured: many concurrent captures against the shipped 256 MiB
    process budget and a small spool cap.

    This is a **synthetic microbenchmark** of the sink alone (04-verification: distinguish
    synthetic microbenchmarks from GPU request latency): no engine, no GPU, no network, so
    the throughput number describes this loop and nothing about request latency. What it
    asserts is the bound - charged bytes never above the content budget, spool bytes never
    above the cap - and it prints the measured peaks so evidence quotes them.

    The parts are `bytearray`s, so every accepted part is a distinct copy in memory: a
    shared `bytes` object would have made the resident-memory figure a fiction.
    """
    async def scenario():
        # The shipped memory profile (256 MiB, 8 MiB metadata reserve) and a spool cap the
        # shipper keeps ahead of, so what this presses is the *memory* bound; the disk cap
        # has its own test. 550 concurrent requests x 512 KiB is more content than the
        # budget allows, which is the point: the overflow must be refused, not absorbed.
        limits = DEFAULTS.replace(trace_spool_max_bytes=1024 * 1024 * 1024,
                                  trace_spool_min_free_bytes=0)
        spool = sink(limits, io=SpoolIO(), segment_max_bytes=32 * 1024 * 1024)
        part = bytearray(64_000)                 # a realistic content part, copied per add
        concurrent, batches, parts_each = 550, 2, 8
        requests = concurrent * batches
        peak_charged = peak_spool = 0
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        started = time.monotonic()

        async def one(index: int) -> None:
            nonlocal peak_charged
            rid = request_id(index)
            with spool.open(rid, b.ORG_A, TraceMode.full, spool.clock.at(600)) as capture:
                charged = 0
                for _ in range(parts_each):
                    if capture.add(part):
                        charged += len(part)
                    await asyncio.sleep(0)       # interleave, as a streaming request does
                stats = await spool.stats()
                peak_charged = max(peak_charged, stats["in_memory_content_bytes"])
                await capture.finish(b.trace(rid, content_bytes=charged, metadata_bytes=256))

        for batch in range(batches):
            offset = batch * concurrent
            await asyncio.gather(*(one(offset + i) for i in range(concurrent)))
            spool.clock.advance(DEFAULTS.trace_fsync_interval_s + 1)
            stats = await spool.flush(spool.clock.now())
            peak_charged = max(peak_charged, stats["in_memory_content_bytes"])
            peak_spool = max(peak_spool, stats["spool_bytes"])
            await spool.rotate()
            for view in spool.segments():        # stand in for T2's shipper
                await spool.ack(view.name)
        elapsed = time.monotonic() - started
        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        stats = await spool.stats()
        budget = spool.content_budget
        charged_bytes = requests * parts_each * len(part)
        print(f"\nsynthetic concurrent capture load (microbenchmark, no GPU, no network): "
              f"{requests} requests, {concurrent} concurrent, {parts_each}x{len(part)}B "
              f"each, in {elapsed:.1f}s ({requests / elapsed:,.0f} req/s, "
              f"{charged_bytes / elapsed / 1e6:,.0f} MB/s offered)")
        print(f"  peak charged content {peak_charged:,} B against a {budget:,} B budget; "
              f"peak spool {peak_spool:,} B against a {limits.trace_spool_max_bytes:,} B "
              f"cap; maxrss {rss_before / 1024:,.0f} -> {rss_after / 1024:,.0f} MiB")
        print(f"  {stats['appended']:,} appended, {stats['fsynced']:,} fsynced, "
              f"{stats['dropped']:,} dropped, losses {stats['loss_reasons']}")
        assert peak_charged <= budget, f"{peak_charged} over the {budget} content budget"
        assert peak_spool <= limits.trace_spool_max_bytes
        # every request ends as exactly one row or one drop - a row may be accepted into
        # memory and still be refused by the writer, so these two are not a partition of
        # `accepted`
        assert stats["appended"] + stats["dropped"] == requests
        assert stats["accepted"] <= requests
        assert stats["in_memory_content_bytes"] == 0
        assert stats["fsynced"] == stats["appended"]
        # the bound was actually pressed: more content was offered than the budget allows,
        # so some capture had to lose its content rather than the process growing
        assert stats["loss_reasons"].get("memory_budget", 0) > 0, stats["loss_reasons"]
        await spool.close()
    asyncio.run(scenario())
