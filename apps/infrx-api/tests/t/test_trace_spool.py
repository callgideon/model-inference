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
from infrx.contracts.records import TraceEnvelope, TraceMode, TraceOfferResult
from infrx.traces.spool import (FRAME, HEADER, SpoolIO, SpoolTraceSink, recover,
                                scan_segment, segment_header)

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
                 entered: threading.Event | None = None) -> None:
        self.free = free
        self.fail_write_on = fail_write_on
        self.fail_fsync_on = fail_fsync_on
        self.block = block
        self.entered = entered
        self.calls: Counter = Counter()

    def free_bytes(self, path: Path) -> int:
        self.calls["free_bytes"] += 1
        return self.free

    def write(self, fd: int, data: bytes) -> int:
        self.calls["write"] += 1
        if self.block is not None:
            # the slow disk: the writer thread parks here, inside the filesystem
            if self.entered is not None:
                self.entered.set()
            self.block.wait(30)
        if self.fail_write_on is not None and self.calls["write"] >= self.fail_write_on:
            raise OSError(28, "No space left on device")
        return super().write(fd, data)

    def fsync(self, fd: int) -> None:
        self.calls["fsync"] += 1
        if self.fail_fsync_on is not None and self.calls["fsync"] >= self.fail_fsync_on:
            raise OSError(5, "Input/output error")
        super().fsync(fd)

    def truncate(self, path: Path, size: int) -> None:
        self.calls["truncate"] += 1
        super().truncate(path, size)

    def unlink(self, path: Path) -> None:
        self.calls["unlink"] += 1
        super().unlink(path)


def sink(limits=None, *, io: SpoolIO | None = None, clock: FakeClock | None = None,
         tag: str = "sink", **kw) -> SpoolTraceSink:
    return SpoolTraceSink(clock or FakeClock(), limits=limits or DEFAULTS,
                          failures=FailurePlan(), spool_dir=_dir(tag),
                          io=io if io is not None else DrillIO(), **kw)


# The suites build a fresh sink per case and the lattice one per sequence - tens of
# thousands of them - and each writes through its own single writer thread. Nothing in the
# harness contract closes an adapter, so the factory retires the oldest: bounded live
# threads without waiting for a cycle collection to reach the sink.
_LIVE: deque = deque()


def _retire(spool: SpoolTraceSink) -> SpoolTraceSink:
    _LIVE.append(spool)
    while len(_LIVE) > 32:
        old = _LIVE.popleft()
        writer, old._writer = old._writer, None
        if writer is not None:
            writer.shutdown(wait=False)
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
        scan = recover(spool.spool_dir)
        assert [row.request_id for row in scan.records] == [ID_A], scan.line()
        # a torn frame after the promised prefix is tolerated, not fatal
        segment = spool.spool_dir / spool.segments()[0].name
        with open(segment, "ab") as handle:
            handle.write(FRAME.pack(4_096, 0, 0, 7)[:9])        # half a frame header
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
        await spool.close()
    asyncio.run(scenario())


def test_a_poison_record_does_not_stop_the_scan():
    """A frame whose checksum is good but whose payload is not a TraceEnvelope is one bad
    row, not the end of the spool: T2 quarantines it and ships the rest."""
    name = "trace-000000.seg"
    junk = b'{"not":"an envelope"}'
    good = codec.compact_bytes(b.trace(ID_A, content_bytes=0, metadata_bytes=16))
    data = (segment_header()
            + FRAME.pack(len(junk), 0, binascii.crc32(junk), 0) + junk
            + FRAME.pack(len(good), 0, binascii.crc32(good), 1) + good)
    scan = scan_segment(name, data)
    assert scan.poison == 1 and scan.torn == 0
    assert [row.request_id for row in scan.records] == [ID_A]
    assert scan.ids == [(name, 1)]


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
        assert all(view.bytes <= 512 + 1_024 for view in views), [v.bytes for v in views]
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
        assert spool.ack(active.name) is False, "the active segment was acked"
        assert spool.ack("trace-999999.seg") is False
        name = await spool.rotate()
        assert name == active.name
        assert spool.segments()[0].sealed is True
        shipped = spool.read_segment(name)
        assert [row.request_id for row in shipped.records] == [ID_A]
        assert spool.ack(name) is True
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
                                io=DrillIO())
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
            assert spool.ack(view.name) is True
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
        io = DrillIO(free=DEFAULTS.trace_spool_min_free_bytes - 1)
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
        await spool.close()
    asyncio.run(scenario())


def test_a_write_error_drops_the_rest_of_the_batch_and_abandons_the_segment():
    """A disk that fails mid-batch: the handle is at an unknown offset, so the segment is
    abandoned rather than appended behind unreadable bytes, and every record the batch
    still held is counted `disk_error` instead of silently vanishing."""
    async def scenario():
        io = DrillIO(fail_write_on=5)         # header, r1 head, r1 content, r2 head, boom
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
        await capture_one(spool, ID_A, b"in memory when the process stops")
        assert (await spool.stats())["in_memory"] == 1
        assert await spool.close() == 1
        stats = await spool.stats()
        assert stats["loss_reasons"]["shutdown"] == 1
        assert stats["in_memory"] == 0 and stats["in_memory_content_bytes"] == 0
        assert stats["appended"] == 0
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
                spool.ack(view.name)
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
