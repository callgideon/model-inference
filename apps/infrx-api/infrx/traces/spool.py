"""T1: byte-budgeted trace capture with a durable, checksummed on-disk spool.

Two layers, deliberately:

* **Accounting** (R27/R37/R42) is inherited from `contracts.fakes.traces`, which is the
  coordinator's *executable specification* of it - 250 lines of loss accounting pinned by
  an exhaustive sequence lattice and ~20 mutants. Copying it here would fork the spec on
  the next ruling, so `SpoolTraceSink` subclasses it and overrides only what durability
  changes (`open`, `_enqueue`, `flush`, `stats`, `crash`). If the coordinator would rather
  the shared accounting lived under a name without "Fake" in it, that is a contract
  revision, not something this module should decide.
* **Durability** is this module: a dedicated writer thread, versioned length-prefixed
  checksummed segments, batched fsync, host disk caps, an ack-based deletion interface for
  the shipper (T2) and a recovery scan that tolerates a torn tail.

What runs where, because it is the whole design:

| Thread | Operations | Touches |
|---|---|---|
| the event loop (request path) | `open`, `add`, `finish`, `abandon`, `offer`, `reap` | counters and the in-memory queue only - no syscall, no `await` |
| the spool writer (one per sink) | `write`, `fsync`, rotate, seal, `unlink`, `statvfs`, segment reads | segment files and `_segments` |
| the constructing thread, once | `mkdir`, `listdir`, `stat`, `flock` | adoption at boot |

`add`/`offer` are O(1) counter updates, so a writer stuck in a 30-second write cannot
block a request: that is the slow-disk drill. **Every** filesystem call after construction
belongs to the writer thread, including the pause re-check's `statvfs`, an `ack`'s `unlink`
and the shipper's segment reads - round 1 of review measured a 2-second event-loop stall
through each of the first two. No lock is held across a filesystem call either; the lock
exists only so the loop thread can read the segment list while the writer appends to it.
This sink therefore requires **one event loop and one writer**: `add` from two OS threads
would race the shared budget counter, because the accounting's check-and-charge is not one
atomic step (measured drift: 133,598 bytes charged with every capture closed).

Durability states are reported separately (02): `in_memory` -> `appended` (handed to the
OS) -> `fsynced` (the only records durability is claimed for). A record's charge against
the memory budget is released once the writer has appended it, not when `flush` takes it,
so the process bound stays one budget instead of one budget plus an in-flight batch.

Segment format, version 1 (08 §5 "spool segment 1"):

    header : magic b"INFRXTRC" + uint16 version
    frame  : uint32 envelope_bytes | uint32 content_bytes | uint32 crc32
             followed by the canonical envelope JSON and then the raw content bytes
    crc32  : over (envelope_bytes, content_bytes) + envelope JSON + content

A crash tears at most the last frame, and the reader stops at the first frame that is
short or fails its checksum. Nothing is ever rewritten or truncated in place: a shipped
segment is *unlinked whole* (`ack`), which is what "no copytruncate" means here.

The stable id a projection deduplicates by is `(segment name, position in segment)`.
Neither half can be corrupted into another record's id: the position is derived from the
frames the reader has already verified rather than stored (round 1 flipped one bit of a
stored index and got two records under the same id), and a segment name carries the
writing process's boot id, so it is unique for all time rather than only while the file
exists - the shipper acking everything and the process restarting used to reissue
`('trace-000000.seg', 0)` for a different record. The lengths are inside the checksum for
the same reason. This sink deliberately does not dedupe by request (R42, the T1/G1 note).
"""
from __future__ import annotations

import asyncio
import binascii
import fcntl
import os
import shutil
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..contracts import codec
from ..contracts.fakes.traces import FakeTraceCapture, FakeTraceSink
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import TraceEnvelope, TraceLossReason, TraceMode, TraceOfferResult

SEGMENT_VERSION = 1
SEGMENT_MAGIC = b"INFRXTRC"
SEGMENT_PREFIX = "trace-"
SEGMENT_SUFFIX = ".seg"
HEADER = struct.Struct("!8sH")          # magic, version
FRAME = struct.Struct("!III")           # envelope bytes, content bytes, crc32
LENGTHS = struct.Struct("!II")          # the part of the frame header the crc32 covers
# A metadata row is a few KiB. A torn tail can claim any length at all, so an envelope
# length past this is corruption rather than a record we are missing bytes for.
MAX_ENVELOPE_BYTES = 1 << 20
# Rotation size. There is no 08 §5 name for it, so it is a local default and a constructor
# argument; `TRACE_SPOOL_SEGMENT_BYTES` is an integration request. Small enough that a
# recovery scan reads one segment at a time without a memory spike.
SEGMENT_MAX_BYTES = 16 * 1024 * 1024
# Amortised pruning of the capture list `reap` walks: a process that never pruned it grew
# by one dead capture per request for ever, which is the leak a bounded sink cannot have.
CAPTURE_PRUNE_AT = 1_024
# The serialized envelope bytes this process will hold for records that are queued or with
# the writer. It exists because the *contract* counter charges the metadata reserve the
# caller-declared `envelope.metadata_bytes`, which a caller may under-declare (the frozen
# case `trace_bounds__metadata_exhaustion_drops_with_counters` requires the declared number,
# and F2.2 will let a real sink charge `max(declared, len(payload))`). Until then this is
# the honest bound on what the queue can actually cost: 32 MiB is ~80,000 ordinary metadata
# rows, far past `TRACE_QUEUE_MAX`, while 10,000 under-declared 20 KB envelopes - the
# reviewer's measurement, 435 MiB resident - stop at 32 MiB. Over the cap is `queue_full`,
# the same reason the row ceiling uses, because it is the same refusal.
QUEUED_PAYLOAD_MAX_BYTES = 32 * 1024 * 1024


# --------------------------------------------------------------------------------------
# the filesystem, injectable
# --------------------------------------------------------------------------------------
class SpoolIO:
    """Every filesystem call the writer makes, in one place.

    The failure drills subclass this: a slow disk, ENOSPC, an fsync error and a
    power-loss tail are not reproducible against a real filesystem, and a spool whose
    failure paths are untested is a spool whose failure paths do not work.
    """

    def mkdir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def listdir(self, path: Path) -> list[str]:
        return sorted(os.listdir(path)) if path.exists() else []

    def size(self, path: Path) -> int:
        return path.stat().st_size

    def exists(self, path: Path) -> bool:
        return path.exists()

    def open_append(self, path: Path) -> int:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

    def write(self, fd: int, data: bytes) -> int:
        """All of `data`, or an `OSError`. A short write is looped, not lost."""
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        return len(data)

    def fsync(self, fd: int) -> None:
        os.fsync(fd)

    def close(self, fd: int) -> None:
        os.close(fd)

    def unlink(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    def truncate(self, path: Path, size: int) -> None:
        os.truncate(path, size)

    def read(self, path: Path) -> bytes:
        return path.read_bytes()

    def free_bytes(self, path: Path) -> int:
        return shutil.disk_usage(path).free

    def fsync_dir(self, path: Path) -> None:
        """Commit the directory entry, not just the file's data: an fsynced record in a
        file whose *name* was never committed is not a record anybody can find."""
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        except OSError:
            pass                     # some filesystems refuse; the data fsync still holds
        finally:
            os.close(fd)

    def lock_dir(self, path: Path) -> int:
        """An exclusive, non-blocking lock on the spool directory.

        Two sinks on one directory is not a configuration, it is data loss: each adopts the
        other's live segments as sealed and can ack them away while the other keeps writing
        to the unlinked file.
        """
        fd = os.open(path / ".writer.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise RuntimeError(f"another process already spools traces in {path}") from None
        return fd

    def unlock_dir(self, fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# --------------------------------------------------------------------------------------
# the segment format and its reader
# --------------------------------------------------------------------------------------
def segment_header() -> bytes:
    return HEADER.pack(SEGMENT_MAGIC, SEGMENT_VERSION)


def frame_checksum(payload: bytes, parts, content_bytes: int) -> int:
    """The checksum over a frame's **lengths**, its envelope and its content.

    The lengths are inside the checksum because a corrupt length field is how a reader
    silently mis-frames a record; round 1 of review found a single flipped bit turning one
    record into two with a wrong identity. `parts` is chained through `crc32` rather than
    joined, so a 96 MiB content part is never copied into a third buffer just to be hashed
    - nor, since round 2, joined at all.
    """
    crc = binascii.crc32(LENGTHS.pack(len(payload), content_bytes))
    crc = binascii.crc32(payload, crc)
    for part in parts:
        crc = binascii.crc32(part, crc)
    return crc


def pack_frame(payload: bytes, crc: int, content_bytes: int) -> bytes:
    return FRAME.pack(len(payload), content_bytes, crc) + payload


def frame_size(payload: bytes, content_bytes: int) -> int:
    return FRAME.size + len(payload) + content_bytes


@dataclass
class Scan:
    """What a recovery replay found. Counts, not prose, so evidence can quote them."""

    records: list[TraceEnvelope] = field(default_factory=list)
    contents: list[bytes] = field(default_factory=list)
    ids: list[tuple[str, int]] = field(default_factory=list)
    segments: int = 0
    torn: int = 0                  # segments whose tail was incomplete or corrupt
    poison: int = 0                # checksum-valid frames that are not a TraceEnvelope
    unreadable: int = 0            # bad magic or an unknown format version
    # Where each torn segment stopped, and how many bytes it left unread. A benign tail is
    # a few bytes; stopping 12 MiB early means a flipped bit cost a segment of *fsynced*
    # records, and T2 has to be able to tell those apart (round 1, nonblocking).
    torn_at: dict[str, int] = field(default_factory=dict)
    unread_bytes: int = 0

    def line(self) -> str:
        return (f"{len(self.records)} records over {self.segments} segments; "
                f"torn tails {self.torn}, poison {self.poison}, unreadable {self.unreadable}, "
                f"unread {self.unread_bytes} B")


def scan_segment(name: str, data: bytes, into: Scan | None = None) -> Scan:
    """Replay one segment's checksum-valid frames, tolerating a torn tail.

    A pure function over bytes: the reader is the half a crash drill has to trust, so it
    is testable without a filesystem at all.

    A record's identity is its **position** in the segment - the ordinal of the frame the
    reader has just verified - and never a number read off the disk. Round 1 flipped one
    bit in the stored index and got two records reported under the same id, checksum-clean,
    because the index was outside the crc. A position cannot be corrupted into a different
    position: the frame before it either verifies or the scan stops there.
    """
    scan = into if into is not None else Scan()
    scan.segments += 1
    if len(data) < HEADER.size:
        scan.unreadable += 1
        scan.unread_bytes += len(data)
        return scan
    magic, version = HEADER.unpack_from(data)
    if magic != SEGMENT_MAGIC or version != SEGMENT_VERSION:
        # Never guess at a format we do not know: a future writer's segment is left for a
        # reader that understands it rather than half-parsed by this one.
        scan.unreadable += 1
        scan.unread_bytes += len(data)
        return scan
    offset = HEADER.size
    index = 0
    while offset < len(data):
        if len(data) - offset < FRAME.size:
            _torn(scan, name, offset, len(data))
            break
        envelope_bytes, content_bytes, crc = FRAME.unpack_from(data, offset)
        body = offset + FRAME.size
        if envelope_bytes > MAX_ENVELOPE_BYTES or len(data) - body < envelope_bytes + content_bytes:
            # A partial write, or a length field that is itself corrupt. Either way this
            # is the tail: everything before it was whole.
            _torn(scan, name, offset, len(data))
            break
        payload = data[body:body + envelope_bytes]
        content = data[body + envelope_bytes:body + envelope_bytes + content_bytes]
        if frame_checksum(payload, (content,), content_bytes) != crc:
            _torn(scan, name, offset, len(data))
            break
        offset = body + envelope_bytes + content_bytes
        position, index = index, index + 1
        try:
            envelope = TraceEnvelope.model_validate_json(payload)
        except ValueError:
            # Framing was intact, so the rest of the segment is still readable; the
            # record itself is poison and T2 quarantines it rather than stopping here. The
            # position is still spent, so the ids of the records after it do not shift.
            scan.poison += 1
            continue
        scan.records.append(envelope)
        scan.contents.append(content)
        scan.ids.append((name, position))
    return scan


def _nothing() -> None:
    """Submitted to the writer thread to serialise behind whatever it is doing."""


def _torn(scan: Scan, name: str, offset: int, size: int) -> None:
    scan.torn += 1
    scan.torn_at[name] = offset
    scan.unread_bytes += size - offset


def segment_names(spool_dir: Path, io: SpoolIO | None = None) -> list[str]:
    reader = io or SpoolIO()
    return [name for name in reader.listdir(spool_dir)
            if name.startswith(SEGMENT_PREFIX) and name.endswith(SEGMENT_SUFFIX)]


def recover(spool_dir: Path | str, *, io: SpoolIO | None = None) -> Scan:
    """Replay every segment in the spool, oldest first.

    Read-only and idempotent: replaying twice yields the same records in the same order
    and consumes nothing, so a restart that scans before the shipper has acked cannot
    duplicate a row. Only checksum-valid frames come back; an unsynced tail the host lost
    is simply not there, which is exactly the promise `fsynced` makes.
    """
    directory, reader = Path(spool_dir), io or SpoolIO()
    scan = Scan()
    for name in segment_names(directory, reader):
        scan_segment(name, reader.read(directory / name), into=scan)
    return scan


# --------------------------------------------------------------------------------------
# segments in flight
# --------------------------------------------------------------------------------------
@dataclass
class SegmentView:
    """What the shipper (T2) needs to read, ship and acknowledge a segment."""

    name: str
    records: int
    bytes: int
    fsynced_records: int
    sealed: bool
    # A segment this process found on disk rather than wrote: `records` and
    # `fsynced_records` are **unknown** (counting them would mean reading up to 10 GiB at
    # boot), so T2 must scan it rather than trust a zero. Round 1 caught
    # `spool_unacked_records` silently under-reporting after a restart.
    adopted: bool = False


@dataclass
class _Segment:
    name: str
    path: Path
    fd: int | None = None
    records: int = 0
    written: int = 0               # bytes handed to the OS
    synced: int = 0                # bytes that survived an fsync: the promised prefix
    synced_records: int = 0
    sealed: bool = False
    fsync_failed: bool = False
    adopted: bool = False
    # One flag per appended-but-unsynced record: whether its capture had already counted a
    # loss. An fsync failure unpromises records from *earlier batches* too, so the flags
    # have to live with the segment - R42 bounds the losses one capture contributes however
    # far apart the two refusals are.
    unsynced_counted: list[bool] = field(default_factory=list)

    def view(self) -> SegmentView:
        return SegmentView(self.name, self.records, self.written, self.synced_records,
                           self.sealed, self.adopted)


@dataclass
class _Row:
    """One accepted record as the writer will see it: already serialized, with the loss
    count its capture has already contributed.

    `counted` travels with the row because R42 bounds the losses **one capture** may
    contribute, and a record the writer refuses is the same capture's second refusal.
    Round 1: a capture that breached the budget and was then refused by a failing disk
    reported `{'memory_budget': 1, 'disk_error': 1}`.
    """

    payload: bytes
    parts: tuple[bytes, ...]
    content_bytes: int
    metadata_bytes: int
    counted: bool


class _Settlement:
    """Applies one writer batch to the counters, exactly once, however the call ended.

    It is a done-callback rather than code after the `await`, because the awaiting flush can
    be cancelled: the batch has already left `queued`, the writer is still holding it, and
    only something attached to the *future* can be relied on to release the charges and
    count the records. A writer that raises something other than `OSError` (a bug, not a
    disk) is the same problem, so that is settled here too rather than escaping as a flush
    exception with five records silently gone.
    """

    def __init__(self, sink: "SpoolTraceSink", batch: "list[_Row]", now: datetime) -> None:
        self.sink, self.batch, self.now, self.done = sink, batch, now, False

    def __call__(self, future) -> None:
        try:
            result = future.result()
        except BaseException:                    # noqa: BLE001 - a writer bug, not a disk
            result = _WriteResult(dropped=[(TraceLossReason.disk_error, row.counted)
                                           for row in self.batch])
        self.settle(result)

    def settle(self, result: _WriteResult) -> None:
        if self.done:
            return
        self.done = True
        sink = self.sink
        if sink._in_flight is self:
            # Releasing the flush guard is this callback's job, not the awaiting caller's:
            # the caller may have been cancelled long ago (B7).
            sink._in_flight = None
        sink._apply(result)
        # The charge is released now, not when the batch was taken: until the writer has
        # appended these bytes they are still in this process, and the budget says so.
        sink.content_bytes = max(0, sink.content_bytes
                                 - sum(row.content_bytes for row in self.batch))
        sink.metadata_bytes = max(0, sink.metadata_bytes
                                  - sum(row.metadata_bytes for row in self.batch))
        sink.queued_payload_bytes = max(0, sink.queued_payload_bytes
                                        - sum(len(row.payload) for row in self.batch))
        if result.fsynced or result.fsync_failed:
            sink._last_fsync = self.now


@dataclass
class _WriteResult:
    """What one writer-thread call did. The loop thread applies it to the counters."""

    appended: int = 0
    fsynced: int = 0
    dropped: list[tuple[TraceLossReason, bool]] = field(default_factory=list)
    fsync_failed: int = 0          # appended records an fsync error unpromised
    fsync_losses: int = 0          # ... of which had not already counted a loss (R42)


# --------------------------------------------------------------------------------------
# the sink
# --------------------------------------------------------------------------------------
class SpoolCapture(FakeTraceCapture):
    """The accounting capture plus the bytes it is accounting for.

    The shared accounting charges content as it accumulates but keeps none of it: it is
    the specification of the *budget*, not of the spool. A real sink has to retain the
    parts it charged, because T2 ships the content object (R47) out of the spool. So
    `parts` mirrors `content_bytes` exactly - every route that releases the charge drops
    the parts in the same step, or the budget would report as free bytes still held.
    """

    def __init__(self, *args, **kw) -> None:
        super().__init__(*args, **kw)
        self.parts: list[bytes] = []

    def add(self, part: bytes | str) -> bool:
        accepted = super().add(part)
        if accepted:
            self.parts.append(part.encode() if isinstance(part, str) else bytes(part))
        return accepted

    def _discard(self, reason: TraceLossReason) -> None:
        # Whole-content discard (R27): the charge and the bytes go together. Keeping the
        # parts here would hold on to memory the released budget has promised away.
        self.parts.clear()
        super()._discard(reason)

    def take_content(self, declared: int) -> tuple[tuple[bytes, ...], int]:
        """The charged parts and their total, handed over and released from the capture.

        `declared` is what the envelope says; the accounting has already refused a claim
        larger than the charge, and a capture that finished as stripped metadata declares
        zero, so its parts are dropped rather than spooled.

        The parts are **not joined**: round 1 pointed out that `b"".join` of a 96 MiB
        capture is a memcpy on the event loop, and a second copy of the whole content at
        peak. The writer checksums and writes them in order instead.
        """
        parts = tuple(self.parts) if declared else ()
        total = sum(len(part) for part in parts)
        self.parts.clear()
        return parts, total


class SpoolTraceSink(FakeTraceSink):
    """`ports.TraceSink` over a durable spool.

    ponytail: one lock, guarding the segment list and the spool byte total - the only
    state the loop and writer threads share, and never held across a filesystem call.
    Per-segment locks would buy nothing while the writer is single-threaded; if the spool
    ever needs parallel writers, that is where to start.
    """

    def __init__(self, clock=None, *, limits: PilotSettings = DEFAULTS, failures=None,
                 spool_dir: Path | str | None = None, io: SpoolIO | None = None,
                 segment_max_bytes: int = SEGMENT_MAX_BYTES,
                 boot_id: str | None = None, lock_dir: bool = True) -> None:
        super().__init__(clock, limits=limits, failures=failures)
        configured = str(spool_dir if spool_dir is not None else limits.trace_spool_dir).strip()
        if not configured:
            # 08 §5: `TRACE_SPOOL_DIR` unset *disables capture*. A sink with nowhere to
            # spool would have to pretend, so G builds no sink at all in that case rather
            # than one whose every record is a loss.
            raise ValueError("TRACE_SPOOL_DIR must be set to build a spool sink")
        self.spool_dir = Path(configured)
        self.io = io or SpoolIO()
        self.segment_max_bytes = segment_max_bytes
        self.io.mkdir(self.spool_dir)
        # One writer per spool directory, enforced rather than assumed: two sinks sharing a
        # directory adopted each other's *live* segments as sealed and acked them out from
        # under the other process, which then appended to an unlinked file (round 1).
        self._dir_lock: int | None = self.io.lock_dir(self.spool_dir) if lock_dir else None
        # Segment names carry this process's boot id, so a name is unique for all time
        # rather than only while the previous file still exists. `_next_index` alone made
        # `(segment, position)` repeat after the shipper had acked everything and the
        # process restarted - which is the steady state: drain, then deploy.
        self.boot_id = boot_id or f"{time.time_ns():016x}{os.getpid() & 0xffff:04x}"
        self._lock = threading.Lock()
        self._writer: ThreadPoolExecutor | None = None
        self._segments: list[_Segment] = []
        self._next_index = 0
        self._closed = False
        # The batch currently with the writer, or None. At most one is out at a time: a
        # caller that fires flushes without awaiting them - or times each one out - would
        # otherwise pile batches up behind a blocked writer, outside the queue ceiling that
        # is supposed to bound memory (round 1: 299,901 records in flight; round 2: 600,000
        # through a cancelling flusher, because a lock is released by cancellation and this
        # is not).
        self._in_flight: _Settlement | None = None
        # One `_Row` per queued record, in lockstep with the inherited `queued`.
        self._pending: list[_Row] = []
        # Serialized envelope bytes held for queued *and* in-flight records.
        self.queued_payload_bytes = 0
        # Counters, not the inherited `appended`/`fsynced` lists: a spool exists so that a
        # written record can leave memory, and those two lists are what keeping every
        # record for ever looks like. They stay empty here; `stats` reads these.
        self.appended_records = 0
        self.fsynced_records = 0
        self.spool_bytes = 0
        self.paused = False
        # The size of the largest record the caps refused, so resuming asks "would that
        # record fit now?" instead of "is there a byte free?" - the second answer is yes
        # the moment one record is dropped, which made the pause oscillate and let every
        # other record into memory just to be refused by the writer.
        self._refused_need = 0
        self._prune_at = CAPTURE_PRUNE_AT
        self._adopt_existing_segments()

    def _adopt_existing_segments(self) -> None:
        """Segments a previous process left behind are still the shipper's to ack, so they
        count against the host cap. They are sealed: a restarted process must never append
        behind a tail it did not write, because the reader stops at a torn frame and would
        lose everything after it.

        Their record counts are left at zero and marked `adopted`, because counting them
        means reading every byte of a spool that may hold 10 GiB at boot. T2 scans an
        adopted segment instead of trusting the count.
        """
        for name in segment_names(self.spool_dir, self.io):
            path = self.spool_dir / name
            size = self.io.size(path)
            self._segments.append(_Segment(name=name, path=path, written=size, synced=size,
                                           sealed=True, adopted=True))
            self.spool_bytes += size

    # --- the request path -------------------------------------------------------------
    def open(self, request_id: str, org_id: str, mode: TraceMode,
             deadline_at: datetime | None = None) -> SpoolCapture:
        """As the accounting specifies (R27/R37), with a capture that keeps its bytes."""
        self.failures.before("open")
        if len(self.captures) >= self._prune_at:
            # Amortised: a closed capture is dead weight `reap` would walk for ever.
            self.captures = [capture for capture in self.captures if not capture.closed]
            self._prune_at = max(CAPTURE_PRUNE_AT, 2 * len(self.captures))
        no_op = mode is not TraceMode.full or deadline_at is None
        capture = SpoolCapture(self, request_id, org_id, mode, deadline_at=deadline_at,
                               no_op=no_op)
        self.captures.append(capture)
        return capture

    def _enqueue(self, envelope: TraceEnvelope, *, charged: int,
                 capture: SpoolCapture | None = None) -> TraceOfferResult:
        """Accept into memory as the bytes that will be written.

        The envelope is serialized **here**, on the loop thread, for two reasons. A record
        the writer cannot serialize would otherwise fail a batch that the sink has already
        reported accepted, and there is no honest counter for that; and an envelope
        assembled past the record validator (`model_construct`, which is how a caller bug
        reaches a sink) can be unserializable, so this is a trust boundary like any other.
        The *content* is not touched here - not even joined: checksumming or copying 96 MiB
        on the request path is exactly the work the writer thread exists to take away.
        """
        parts, content_bytes = (capture.take_content(envelope.content_bytes)
                                if capture is not None else ((), 0))
        reason = None
        if self._closed:
            # A closed sink keeps nothing: the writer is gone, so accepting would be a
            # promise no thread is left to keep (round 1: `finish` answered
            # `accepted_in_memory` after `close` and the next flush resurrected a thread).
            reason = TraceLossReason.shutdown
        elif self.paused:
            # Nowhere for this record to land: dropped now with the honest reason rather
            # than after the memory queue has filled with rows that cannot be written.
            reason = TraceLossReason.disk_budget
        elif content_bytes != envelope.content_bytes:
            # The bytes held and the bytes declared are one fact. The accounting refuses a
            # claim *larger* than the charge; a smaller one would spool content the row
            # does not account for, which is the same defect from the other side.
            reason = TraceLossReason.malformed
        payload = b""
        if reason is None:
            try:
                payload = codec.compact_bytes(envelope)
            except Exception:                 # noqa: BLE001 - R37: never raise into the
                # request path. Whatever pydantic thinks of this object, the answer is a
                # counted `malformed` drop.
                reason = TraceLossReason.malformed
            else:
                if len(payload) > MAX_ENVELOPE_BYTES:
                    # The reader refuses a frame this long, so writing one would spool
                    # bytes nothing can replay. Writer and reader agree or neither works.
                    reason = TraceLossReason.malformed
                elif (self.queued_payload_bytes + len(payload)
                      > QUEUED_PAYLOAD_MAX_BYTES):
                    # What the queue really costs, as opposed to what the caller declared it
                    # would. The row ceiling counts rows and the metadata reserve counts a
                    # caller's number; this counts bytes, so an under-declared 20 KB envelope
                    # cannot buy 10,000 places in a queue sized for metadata.
                    reason = TraceLossReason.queue_full
        if reason is not None:
            self.content_bytes = max(0, self.content_bytes - charged)
            if capture is not None:
                capture.content_bytes = 0
            return self._drop(reason,
                              counted=capture.counted if capture is not None else False)
        counted = capture.counted if capture is not None else False
        result = super()._enqueue(envelope, charged=charged, capture=capture)
        if result is TraceOfferResult.accepted_in_memory:
            self.queued_payload_bytes += len(payload)
            self._pending.append(_Row(payload=payload, parts=parts,
                                      content_bytes=content_bytes,
                                      metadata_bytes=envelope.metadata_bytes,
                                      counted=counted))
        return result

    # --- durability -------------------------------------------------------------------
    async def flush(self, deadline: datetime | None = None) -> dict[str, object]:
        """Hand what is in memory to the writer thread and wait for it to land.

        `deadline` is the caller's *database*-clock instant. It is deliberately not turned
        into a wall-clock timeout: the injected clock and the host clock are not
        comparable, and a flush that gave up early would leave its batch nowhere. What
        bounds this call is the batch, which `TRACE_QUEUE_MAX` bounds; a hung disk blocks
        the flusher and nothing else, which is why the writer is its own thread.
        """
        self.failures.before("flush")
        if self._closed:
            return await self.stats()
        if self._in_flight is not None:
            # A batch is still with the writer. **Not** a lock: round 2 found that an
            # `async with` releases the moment the awaiting flush is cancelled, while the
            # shielded batch is still out - so a flusher built exactly as this docstring
            # describes (`wait_for(flush, …)` on a tick) took a fresh 10,000-row batch every
            # tick and accepted 600,000 records with nothing to bound them. The guard is the
            # batch's own lifetime, cleared by its settlement, and it does not matter which
            # caller is waiting or whether anybody still is.
            return await self.stats()
        assert len(self.queued) == len(self._pending), "the queue and its bytes diverged"
        batch, self.queued, self._pending = self._pending, [], []
        now = self.clock.now()
        fsync_due = ((now - self._last_fsync).total_seconds()
                     >= self.limits.trace_fsync_interval_s)
        if not batch and not self.paused and not (fsync_due and self._unsynced()):
            return await self.stats()
        settled = _Settlement(self, batch, now)
        self._in_flight = settled
        try:
            future = self._submit(self._write_batch, batch, fsync_due)
        except BaseException:
            # Nothing was handed over, so nothing will settle it but this.
            settled.settle(_WriteResult(dropped=[(TraceLossReason.disk_error, row.counted)
                                                 for row in batch]))
            raise
        future.add_done_callback(settled)
        # `shield`: a caller that times this flush out (its only bound, since the deadline
        # argument is a database instant) must not leave the batch in limbo. The writer
        # keeps going and the callback settles the counters and the charges whichever way it
        # ends - round 1 lost five records and 5 MB of budget for ever to
        # `wait_for(flush, 0.2)`.
        await asyncio.shield(future)
        return await self.stats()

    def _apply(self, result: _WriteResult) -> None:
        """Writer-thread outcome -> the counters the request path reads."""
        self.appended_records += result.appended
        self.fsynced_records += result.fsynced
        for reason, counted in result.dropped:
            # `counted` is the capture's own loss count travelling with its row (R42): the
            # drop is recorded either way, the *loss* only once per capture.
            self._drop(reason, counted=counted)
        if result.fsync_losses:
            # Appended, then unpromised: not refused (the bytes may even be on disk), but
            # nothing here will claim durability for them. `fsync_failed` is how many
            # records that was; `fsync_losses` is how many of them are this capture's
            # first loss, which is what the loss table may count (R42).
            self.loss_reasons[TraceLossReason.disk_error] += result.fsync_losses

    async def stats(self) -> dict[str, object]:
        """The contract keys the suites read, plus what the spool adds.

        `appended` and `fsynced` are counters, not the lists the in-memory specification
        keeps: a spool exists so that a written record can leave memory.
        """
        with self._lock:
            segments = len(self._segments)
            spool_bytes = self.spool_bytes
            unacked = sum(segment.records for segment in self._segments)
        return {
            "accepted": self.accepted,
            "dropped": self.dropped,
            "in_memory": len(self.queued),
            "in_memory_content_bytes": self.content_bytes,
            "in_memory_metadata_bytes": self.metadata_bytes,
            "open_captures": len([c for c in self.captures if not c.closed]),
            "appended": self.appended_records,
            "fsynced": self.fsynced_records,
            "loss_reasons": {reason.value: count
                             for reason, count in self.loss_reasons.items()},
            # the spool's own numbers, for T2's shipper and I's dashboards
            "spool_bytes": spool_bytes,
            "spool_segments": segments,
            "spool_unacked_records": unacked,
            "spool_paused": self.paused,
            "queued_payload_bytes": self.queued_payload_bytes,
        }

    # --- the shipper's interface (T2) --------------------------------------------------
    def segments(self) -> tuple[SegmentView, ...]:
        """Oldest first: what the shipper may read, and what it may then `ack`."""
        with self._lock:
            return tuple(segment.view() for segment in self._segments)

    async def read_segment(self, name: str) -> Scan:
        """The shipper's read path: the checksum-valid records of one segment.

        `async` and routed through the writer, like everything else that touches a file:
        reading a 16 MiB segment on the event loop would be a request-path stall on the
        shipper's schedule.
        """
        return await self._run(self._read_segment, name)

    def _read_segment(self, name: str) -> Scan:
        return scan_segment(name, self.io.read(self.spool_dir / name))

    async def ack(self, name: str) -> bool:
        """Delete a shipped segment, whole.

        Only a sealed segment may go: the active one is still being appended to, and
        rewriting a file the writer holds open is precisely the copytruncate race this
        design exists to avoid (TRACE-RECOVER). Acking frees disk, so it is also where a
        paused spool comes back.

        `async` since round 1: the `unlink` and the pause re-check are filesystem calls, and
        on the loop thread they were a 2-second event-loop stall on a slow disk. The list
        bookkeeping stays on the loop (it is the loop's data); only the syscalls move.
        """
        self._executor()          # refuses a closed sink before any bookkeeping moves
        with self._lock:
            segment = next((s for s in self._segments if s.name == name), None)
            if segment is None or not segment.sealed:
                return False
            self._segments.remove(segment)
            self.spool_bytes = max(0, self.spool_bytes - segment.written)
        await self._run(self._unlink_acked, segment.path)
        return True

    def _unlink_acked(self, path: Path) -> None:
        """Writer thread: the syscalls of an ack."""
        self.io.unlink(path)
        self.io.fsync_dir(self.spool_dir)        # the deletion, not just the data
        if self.paused:
            # recomputed against the freed disk, for the record that was refused
            self._refuse_bytes(self._refused_need)

    async def drain(self) -> dict[str, object]:
        """Wait for the batch currently with the writer to settle, and report stats.

        The honest way for a caller to observe that a *cancelled* flush has landed: the
        no-op queues behind the batch on the single writer thread, and the batch's
        settlement callback is scheduled before this one's, so it has run by the time this
        returns. G's shutdown path wants the same guarantee before it stops flushing.
        """
        if not self._closed and self._in_flight is not None:
            await self._run(_nothing)
        return await self.stats()

    async def rotate(self) -> str | None:
        """Seal the active segment so the shipper can ack it; returns its name.

        Without this a quiet host keeps one segment open for ever and the ack interface
        can never reach the tail.
        """
        name, result = await self._run(self._seal_active)
        self._apply(result)
        return name

    async def close(self, *, drop_queued: bool = True) -> int:
        """Orderly shutdown: be honest about what is still in memory (`shutdown`, 08 §3),
        seal the segments and stop the writer. Returns what it dropped.

        The sink is closed afterwards and stays closed: a later `finish`/`offer` is dropped
        `shutdown` rather than accepted into a process that has no writer left, and `flush`
        no longer starts one.
        """
        lost = 0
        if drop_queued and self.queued:
            lost = len(self.queued)
            self.queued.clear()
            self._pending.clear()
            self.dropped += lost
            self.loss_reasons[TraceLossReason.shutdown] += lost
            self.content_bytes = self.metadata_bytes = 0
            self.queued_payload_bytes = 0
        if self._writer is not None:
            # Seal on the way out: an unsynced tail left behind is a record this process
            # appended and never promised, and sealing fsyncs before it closes.
            _name, result = await self._run(self._seal_active)
            self._apply(result)
            writer, self._writer = self._writer, None
            writer.shutdown(wait=True)
        self._closed = True
        if self._dir_lock is not None:
            self.io.unlock_dir(self._dir_lock)
            self._dir_lock = None
        return lost

    def crash(self) -> int:
        """Model the host dying: everything not fsynced is gone.

        Unsynced bytes are truncated away rather than left in place, because that is the
        state a power loss leaves and the state recovery has to cope with. Every segment
        is sealed afterwards: a restarted process must never append behind a torn tail,
        since the reader stops there and would lose whatever followed.

        No lock: a crashed process has no writer thread, which is what this models.
        """
        lost = self.appended_records - self.fsynced_records
        for segment in self._segments:
            self._close_segment(segment)
            if segment.written > segment.synced:
                try:
                    self.io.truncate(segment.path, segment.synced)
                except OSError:
                    pass
                segment.written = segment.synced
                segment.records = segment.synced_records
                segment.unsynced_counted = []
            segment.sealed = True
        self.spool_bytes = sum(segment.written for segment in self._segments)
        self.appended_records = self.fsynced_records
        self.queued.clear()
        self._pending.clear()
        for capture in self.captures:
            capture.closed = True
            capture.content_bytes = 0
            if isinstance(capture, SpoolCapture):
                capture.parts.clear()
        self.captures.clear()
        self.content_bytes = self.metadata_bytes = 0
        self.queued_payload_bytes = 0
        if lost:
            self.loss_reasons[TraceLossReason.shutdown] += lost
        return lost

    # --- the writer thread ------------------------------------------------------------
    def _executor(self) -> ThreadPoolExecutor:
        if self._closed:
            # A closed sink does not quietly start a second writer thread for a late call.
            raise RuntimeError("this trace sink is closed")
        if self._writer is None:
            # Lazily, and one worker: the spool writer is a single serial thread, so
            # segment state needs no ordering discipline beyond "the writer owns it". A
            # sink that never writes never starts a thread, which matters when a
            # conformance lattice builds tens of thousands of them.
            self._writer = ThreadPoolExecutor(max_workers=1,
                                              thread_name_prefix="infrx-trace-spool")
        return self._writer

    def _submit(self, function, *args):
        """Hand work to the writer thread and return its future, so a caller that is
        cancelled can still have the result settled by a done-callback."""
        return asyncio.get_running_loop().run_in_executor(self._executor(), function, *args)

    async def _run(self, function, *args):
        return await self._submit(function, *args)

    def _unsynced(self) -> int:
        """Appended records an fsync could still promise."""
        with self._lock:
            return sum(segment.records - segment.synced_records for segment in self._segments
                       if segment.fd is not None and not segment.fsync_failed)

    def _write_batch(self, batch: "list[_Row]", fsync_due: bool) -> _WriteResult:
        """The only code that touches a file. Runs in the writer thread."""
        result = _WriteResult()
        broken = False
        if not batch and self.paused:
            # Nothing to write, but the pause is this thread's to re-evaluate: the flusher
            # is the timer that lifts one when the disk comes back, and the `statvfs` that
            # decides belongs here rather than on the event loop.
            self._refuse_bytes(self._refused_need)
        for row in batch:
            if broken:
                result.dropped.append((TraceLossReason.disk_error, row.counted))
                continue
            crc = frame_checksum(row.payload, row.parts, row.content_bytes)
            need = frame_size(row.payload, row.content_bytes)
            refusal = self._refuse_bytes(need)
            if refusal is not None:
                result.dropped.append((refusal, row.counted))
                continue
            try:
                segment = self._segment_for(need, result)
                self.io.write(segment.fd, pack_frame(row.payload, crc, row.content_bytes))
                for part in row.parts:
                    self.io.write(segment.fd, part)
            except Exception:                    # noqa: BLE001
                # The disk refused mid-batch - or something worse than a disk did, which is
                # a bug rather than ENOSPC but must not escape the writer either: a raised
                # batch is records the sink has already reported accepted. Stop writing: the
                # handle is at an arbitrary offset, so the segment is abandoned (its tail is
                # torn, which the reader tolerates) and the rest of the batch is dropped
                # honestly rather than appended behind unreadable bytes.
                result.dropped.append((TraceLossReason.disk_error, row.counted))
                self._abandon_active(result)
                broken = True
                continue
            with self._lock:
                segment.records += 1
                segment.written += need
                segment.unsynced_counted.append(row.counted)
                self.spool_bytes += need
            result.appended += 1
        if fsync_due:
            self._fsync_pending(result)
        return result

    def _fsync_pending(self, result: _WriteResult) -> None:
        """Batched fsync: at most one per `TRACE_FSYNC_INTERVAL_S`, over every segment
        with unsynced bytes. Durability begins here and nowhere earlier."""
        with self._lock:
            pending = [s for s in self._segments
                       if s.fd is not None and s.written > s.synced and not s.fsync_failed]
        for segment in pending:
            self._fsync(segment, result)

    def _fsync(self, segment: _Segment, result: _WriteResult) -> None:
        flags = segment.unsynced_counted
        unsynced = segment.records - segment.synced_records
        try:
            self.io.fsync(segment.fd)
        except OSError:
            # POSIX gives no second chance after an fsync error, so these records are
            # counted lost rather than retried into a promise we cannot keep. The segment
            # is sealed: the next record starts a file whose fsync may work. A record whose
            # capture has already contributed its one loss is unpromised without counting a
            # second one (R42).
            segment.fsync_failed = True
            segment.sealed = True
            result.fsync_failed += unsynced
            result.fsync_losses += sum(1 for counted in flags if not counted)
            segment.unsynced_counted = []
            self._close_segment(segment)
            return
        segment.synced = segment.written
        segment.synced_records = segment.records
        segment.unsynced_counted = []
        result.fsynced += unsynced

    def _refuse_bytes(self, need: int) -> TraceLossReason | None:
        """The host caps (02, 08 §5): 10 GiB of spool and a 2 GiB free-disk floor.

        Sets `paused`, which the request path reads: at either limit a record is dropped
        as `disk_budget` while inference carries on untouched, and an `ack` that frees a
        segment lifts it.
        """
        with self._lock:
            spool_bytes = self.spool_bytes
        try:
            free = self.io.free_bytes(self.spool_dir)
        except OSError:
            free = 0
        self.paused = (spool_bytes + need > self.limits.trace_spool_max_bytes
                       or free - need < self.limits.trace_spool_min_free_bytes)
        self._refused_need = max(self._refused_need, need) if self.paused else 0
        return TraceLossReason.disk_budget if self.paused else None

    def _segment_for(self, need: int, result: _WriteResult) -> _Segment:
        """The segment this record belongs in, rotating by size.

        Called from the writer thread only, so reading a segment's fields outside the lock
        is safe: the writer is the only thread that changes them. The lock covers the
        list, which the loop thread reads.
        """
        with self._lock:
            active = self._segments[-1] if self._segments else None
        if active is not None and not active.sealed and active.fd is not None:
            if active.written + need <= self.segment_max_bytes:
                return active
            self._seal(active, result)
        # A record larger than the bound lands in a *fresh* segment on its own. There is no
        # "the active segment is empty, take it anyway" case: a freshly opened segment is
        # returned straight to the record that opened it, so it is never the active segment
        # at this check. The condition that used to say so was unreachable, which is why no
        # test could kill a mutant of it (R40).
        return self._open_segment()

    def _open_segment(self) -> _Segment:
        """A name carries this process's boot id, so it is never reused and
        `(segment, position)` is a stable id a projection can deduplicate by across a
        restart - including the restart that follows the shipper acking everything."""
        while True:
            with self._lock:
                name = (f"{SEGMENT_PREFIX}{self.boot_id}-"
                        f"{self._next_index:06d}{SEGMENT_SUFFIX}")
                self._next_index += 1
            path = self.spool_dir / name
            if not self.io.exists(path):
                break
        fd = self.io.open_append(path)
        segment = _Segment(name=name, path=path, fd=fd)
        try:
            self.io.write(fd, segment_header())
            # The *name* has to survive too: without this an fsynced record could be in a
            # file the directory entry for which was never committed.
            self.io.fsync_dir(self.spool_dir)
        except BaseException:
            # Round 1: the descriptor leaked and the header-less file stayed on disk on
            # every failed open - 300 flushes against a full disk left 300 orphans and 300
            # descriptors, which is `EMFILE` on the gateway. Neither is tracked anywhere, so
            # both are cleaned up here before the failure travels on.
            self._close_segment(segment)
            try:
                self.io.unlink(path)
            except OSError:
                pass
            raise
        segment.written = HEADER.size
        with self._lock:
            self.spool_bytes += HEADER.size
            self._segments.append(segment)
        return segment

    def _seal(self, segment: _Segment, result: _WriteResult) -> None:
        """Close a segment for good - fsyncing first, because sealing an unsynced tail
        would quietly unpromise records this process has already appended."""
        if segment.fd is not None and segment.written > segment.synced and not segment.fsync_failed:
            self._fsync(segment, result)
        segment.sealed = True
        self._close_segment(segment)

    def _close_segment(self, segment: _Segment) -> None:
        if segment.fd is not None:
            try:
                self.io.close(segment.fd)
            except OSError:
                pass
            segment.fd = None

    def _abandon_active(self, result: _WriteResult) -> None:
        """A segment whose write failed: sealed without an fsync, since the failing disk
        is in no state to promise anything and the torn tail is what the reader expects.

        Its size is re-read from disk, because a failed write may still have landed some of
        its bytes and those bytes count against the host cap whatever the writer thinks.
        """
        with self._lock:
            active = self._segments[-1] if self._segments else None
        if active is None or active.sealed:
            return
        active.sealed = True
        self._close_segment(active)
        try:
            landed = self.io.size(active.path)
        except OSError:
            return
        with self._lock:
            self.spool_bytes += max(0, landed - active.written)
            active.written = max(active.written, landed)

    def _seal_active(self) -> tuple[str | None, _WriteResult]:
        result = _WriteResult()
        with self._lock:
            active = self._segments[-1] if self._segments else None
        if active is None or active.sealed:
            return None, result
        self._seal(active, result)
        return active.name, result
