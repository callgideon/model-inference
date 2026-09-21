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
| the event loop (request path) | `open`, `add`, `finish`, `abandon`, `offer`, `reap` | counters and the in-memory queue only - never disk, never `await` |
| the spool writer (one per sink) | serialize, `write`, `fsync`, rotate, seal | segment files and `_segments` |

`add`/`offer` are O(1) counter updates, so a writer stuck in a 30-second write cannot
block a request: that is the slow-disk drill. No lock is ever held across a filesystem
call, for the same reason - the lock exists only so the loop thread can read the segment
list while the writer appends to it.

Durability states are reported separately (02): `in_memory` -> `appended` (handed to the
OS) -> `fsynced` (the only records durability is claimed for). A record's charge against
the memory budget is released once the writer has appended it, not when `flush` takes it,
so the process bound stays one budget instead of one budget plus an in-flight batch.

Segment format, version 1 (08 §5 "spool segment 1"):

    header : magic b"INFRXTRC" + uint16 version
    frame  : uint32 envelope_bytes | uint32 content_bytes | uint32 crc32 | uint64 index
             followed by the canonical envelope JSON and then the raw content bytes

A crash tears at most the last frame, and the reader stops at the first frame that is
short or fails its checksum. Nothing is ever rewritten or truncated in place: a shipped
segment is *unlinked whole* (`ack`), which is what "no copytruncate" means here. The
stable id a projection deduplicates by is `(segment name, index)`; segment names are never
reused, and this sink deliberately does not dedupe by request (R42, the T1/G1 note).
"""
from __future__ import annotations

import asyncio
import binascii
import os
import shutil
import struct
import threading
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
FRAME = struct.Struct("!IIIQ")          # envelope bytes, content bytes, crc32, index
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


# --------------------------------------------------------------------------------------
# the segment format and its reader
# --------------------------------------------------------------------------------------
def segment_header() -> bytes:
    return HEADER.pack(SEGMENT_MAGIC, SEGMENT_VERSION)


def frame_checksum(payload: bytes, content: bytes) -> int:
    """One checksum over envelope + content, computed without joining them: a 96 MiB
    content part copied into a third buffer just to be hashed is 96 MiB of avoidable peak
    memory."""
    return binascii.crc32(content, binascii.crc32(payload))


def pack_frame(payload: bytes, crc: int, content_bytes: int, index: int) -> bytes:
    return FRAME.pack(len(payload), content_bytes, crc, index) + payload


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

    def line(self) -> str:
        return (f"{len(self.records)} records over {self.segments} segments; "
                f"torn tails {self.torn}, poison {self.poison}, unreadable {self.unreadable}")


def scan_segment(name: str, data: bytes, into: Scan | None = None) -> Scan:
    """Replay one segment's checksum-valid frames, tolerating a torn tail.

    A pure function over bytes: the reader is the half a crash drill has to trust, so it
    is testable without a filesystem at all.
    """
    scan = into if into is not None else Scan()
    scan.segments += 1
    if len(data) < HEADER.size:
        scan.unreadable += 1
        return scan
    magic, version = HEADER.unpack_from(data)
    if magic != SEGMENT_MAGIC or version != SEGMENT_VERSION:
        # Never guess at a format we do not know: a future writer's segment is left for a
        # reader that understands it rather than half-parsed by this one.
        scan.unreadable += 1
        return scan
    offset = HEADER.size
    while offset < len(data):
        if len(data) - offset < FRAME.size:
            scan.torn += 1
            break
        envelope_bytes, content_bytes, crc, index = FRAME.unpack_from(data, offset)
        body = offset + FRAME.size
        if envelope_bytes > MAX_ENVELOPE_BYTES or len(data) - body < envelope_bytes + content_bytes:
            # A partial write, or a length field that is itself corrupt. Either way this
            # is the tail: everything before it was whole.
            scan.torn += 1
            break
        payload = data[body:body + envelope_bytes]
        content = data[body + envelope_bytes:body + envelope_bytes + content_bytes]
        if binascii.crc32(content, binascii.crc32(payload)) != crc:
            scan.torn += 1
            break
        offset = body + envelope_bytes + content_bytes
        try:
            envelope = TraceEnvelope.model_validate_json(payload)
        except ValueError:
            # Framing was intact, so the rest of the segment is still readable; the
            # record itself is poison and T2 quarantines it rather than stopping here.
            scan.poison += 1
            continue
        scan.records.append(envelope)
        scan.contents.append(content)
        scan.ids.append((name, index))
    return scan


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

    def view(self) -> SegmentView:
        return SegmentView(self.name, self.records, self.written, self.synced_records,
                           self.sealed)


@dataclass
class _WriteResult:
    """What one writer-thread call did. The loop thread applies it to the counters."""

    appended: int = 0
    fsynced: int = 0
    dropped: list[TraceLossReason] = field(default_factory=list)
    fsync_failed: int = 0          # appended records an fsync error unpromised


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

    def take_content(self, declared: int) -> bytes:
        """The charged bytes, handed to the queue and released from the capture.

        `declared` is what the envelope says; the accounting has already refused a claim
        larger than the charge, and a capture that finished as stripped metadata declares
        zero, so its parts are dropped rather than spooled.
        """
        content = b"".join(self.parts) if declared else b""
        self.parts.clear()
        return content


class SpoolTraceSink(FakeTraceSink):
    """`ports.TraceSink` over a durable spool.

    ponytail: one lock, guarding the segment list and the spool byte total - the only
    state the loop and writer threads share, and never held across a filesystem call.
    Per-segment locks would buy nothing while the writer is single-threaded; if the spool
    ever needs parallel writers, that is where to start.
    """

    def __init__(self, clock=None, *, limits: PilotSettings = DEFAULTS, failures=None,
                 spool_dir: Path | str | None = None, io: SpoolIO | None = None,
                 segment_max_bytes: int = SEGMENT_MAX_BYTES) -> None:
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
        self._lock = threading.Lock()
        self._writer: ThreadPoolExecutor | None = None
        self._segments: list[_Segment] = []
        self._next_index = 0
        # (envelope bytes, content bytes) per queued row, in lockstep with the inherited
        # `queued`: what the writer will append, already serialized.
        self._pending: list[tuple[bytes, bytes]] = []
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
        count against the host cap and their names are never reused. They are sealed: a
        restarted process must never append behind a tail it did not write, because the
        reader stops at a torn frame and would lose everything after it."""
        for name in segment_names(self.spool_dir, self.io):
            path = self.spool_dir / name
            size = self.io.size(path)
            self._segments.append(_Segment(name=name, path=path, written=size, synced=size,
                                           sealed=True))
            self.spool_bytes += size
            digits = name[len(SEGMENT_PREFIX):-len(SEGMENT_SUFFIX)]
            if digits.isdigit():
                self._next_index = max(self._next_index, int(digits) + 1)

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
        The *content* is not touched here: checksumming 96 MiB on the request path is
        exactly the work the writer thread exists to take away.
        """
        content = capture.take_content(envelope.content_bytes) if capture is not None else b""
        reason = None
        if self.paused:
            # Nowhere for this record to land: dropped now with the honest reason rather
            # than after the memory queue has filled with rows that cannot be written.
            reason = TraceLossReason.disk_budget
        elif len(content) != envelope.content_bytes:
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
        if reason is not None:
            self.content_bytes = max(0, self.content_bytes - charged)
            if capture is not None:
                capture.content_bytes = 0
            return self._drop(reason,
                              counted=capture.counted if capture is not None else False)
        result = super()._enqueue(envelope, charged=charged, capture=capture)
        if result is TraceOfferResult.accepted_in_memory:
            self._pending.append((payload, content))
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
        if self.paused:
            # The flusher is the timer: re-checking the host limits here is what lifts a
            # pause when the disk was freed by something other than an `ack`. It is a
            # `statvfs` on the flush path, never on the request path.
            self._refuse_bytes(self._refused_need)
        assert len(self.queued) == len(self._pending), "the queue and its bytes diverged"
        batch = list(zip(self.queued, self._pending))
        self.queued, self._pending = [], []
        now = self.clock.now()
        fsync_due = (now - self._last_fsync).total_seconds() >= self.limits.trace_fsync_interval_s
        if not batch and not (fsync_due and self._unsynced()):
            return await self.stats()
        result = await self._run(self._write_batch, batch, fsync_due)
        self._apply(result)
        # The charge is released now, not when the batch was taken: until the writer has
        # appended these bytes they are still in this process, and the budget says so.
        self.content_bytes = max(0, self.content_bytes
                                 - sum(len(content) for _e, (_p, content) in batch))
        self.metadata_bytes = max(0, self.metadata_bytes
                                  - sum(envelope.metadata_bytes for envelope, _b in batch))
        if result.fsynced or result.fsync_failed:
            self._last_fsync = now
        return await self.stats()

    def _apply(self, result: _WriteResult) -> None:
        """Writer-thread outcome -> the counters the request path reads."""
        self.appended_records += result.appended
        self.fsynced_records += result.fsynced
        for reason in result.dropped:
            self._drop(reason)
        if result.fsync_failed:
            # Appended, then unpromised: not refused (the bytes may even be on disk), but
            # nothing here will claim durability for them.
            self.loss_reasons[TraceLossReason.disk_error] += result.fsync_failed

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
        }

    # --- the shipper's interface (T2) --------------------------------------------------
    def segments(self) -> tuple[SegmentView, ...]:
        """Oldest first: what the shipper may read, and what it may then `ack`."""
        with self._lock:
            return tuple(segment.view() for segment in self._segments)

    def read_segment(self, name: str) -> Scan:
        """The shipper's read path: the checksum-valid records of one segment."""
        return scan_segment(name, self.io.read(self.spool_dir / name))

    def ack(self, name: str) -> bool:
        """Delete a shipped segment, whole.

        Only a sealed segment may go: the active one is still being appended to, and
        rewriting a file the writer holds open is precisely the copytruncate race this
        design exists to avoid (TRACE-RECOVER). Acking frees disk, so it is also where a
        paused spool comes back.
        """
        with self._lock:
            segment = next((s for s in self._segments if s.name == name), None)
            if segment is None or not segment.sealed:
                return False
            self._segments.remove(segment)
            self.spool_bytes = max(0, self.spool_bytes - segment.written)
        self.io.unlink(segment.path)
        if self.paused:
            # recomputed against the freed disk, for the record that was refused
            self._refuse_bytes(self._refused_need)
        return True

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
        seal the segments and stop the writer. Returns what it dropped."""
        lost = 0
        if drop_queued and self.queued:
            lost = len(self.queued)
            self.queued.clear()
            self._pending.clear()
            self.dropped += lost
            self.loss_reasons[TraceLossReason.shutdown] += lost
            self.content_bytes = self.metadata_bytes = 0
        if self._writer is not None:
            name, result = await self._run(self._seal_active)
            self._apply(result)
            writer, self._writer = self._writer, None
            writer.shutdown(wait=True)
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
        if lost:
            self.loss_reasons[TraceLossReason.shutdown] += lost
        return lost

    # --- the writer thread ------------------------------------------------------------
    def _executor(self) -> ThreadPoolExecutor:
        if self._writer is None:
            # Lazily, and one worker: the spool writer is a single serial thread, so
            # segment state needs no ordering discipline beyond "the writer owns it". A
            # sink that never writes never starts a thread, which matters when a
            # conformance lattice builds tens of thousands of them.
            self._writer = ThreadPoolExecutor(max_workers=1,
                                              thread_name_prefix="infrx-trace-spool")
        return self._writer

    async def _run(self, function, *args):
        return await asyncio.get_running_loop().run_in_executor(self._executor(), function,
                                                                *args)

    def _unsynced(self) -> int:
        """Appended records an fsync could still promise."""
        with self._lock:
            return sum(segment.records - segment.synced_records for segment in self._segments
                       if segment.fd is not None and not segment.fsync_failed)

    def _write_batch(self, batch, fsync_due: bool) -> _WriteResult:
        """The only code that touches a file. Runs in the writer thread."""
        result = _WriteResult()
        broken = False
        for _envelope, (payload, content) in batch:
            if broken:
                result.dropped.append(TraceLossReason.disk_error)
                continue
            crc = frame_checksum(payload, content)
            need = frame_size(payload, len(content))
            refusal = self._refuse_bytes(need)
            if refusal is not None:
                result.dropped.append(refusal)
                continue
            try:
                segment = self._segment_for(need, result)
                self.io.write(segment.fd, pack_frame(payload, crc, len(content),
                                                     segment.records))
                if content:
                    self.io.write(segment.fd, content)
            except OSError:
                # The disk refused mid-batch. Stop writing: the handle is at an arbitrary
                # offset, so the segment is abandoned (its tail is torn, which the reader
                # tolerates) and the rest of the batch is dropped honestly rather than
                # appended behind unreadable bytes.
                result.dropped.append(TraceLossReason.disk_error)
                self._abandon_active(result)
                broken = True
                continue
            with self._lock:
                segment.records += 1
                segment.written += need
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
        unsynced = segment.records - segment.synced_records
        try:
            self.io.fsync(segment.fd)
        except OSError:
            # POSIX gives no second chance after an fsync error, so these records are
            # counted lost rather than retried into a promise we cannot keep. The segment
            # is sealed: the next record starts a file whose fsync may work.
            segment.fsync_failed = True
            segment.sealed = True
            result.fsync_failed += unsynced
            self._close_segment(segment)
            return
        segment.synced = segment.written
        segment.synced_records = segment.records
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
            if active.written + need <= self.segment_max_bytes or active.records == 0:
                return active
            self._seal(active, result)
        return self._open_segment()

    def _open_segment(self) -> _Segment:
        """A name is never reused, so `(segment, index)` is a stable id a projection can
        deduplicate by even across a restart."""
        while True:
            with self._lock:
                name = f"{SEGMENT_PREFIX}{self._next_index:06d}{SEGMENT_SUFFIX}"
                self._next_index += 1
            path = self.spool_dir / name
            if not self.io.exists(path):
                break
        fd = self.io.open_append(path)
        segment = _Segment(name=name, path=path, fd=fd)
        self.io.write(fd, segment_header())
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
        is in no state to promise anything and the torn tail is what the reader expects."""
        with self._lock:
            active = self._segments[-1] if self._segments else None
        if active is not None and not active.sealed:
            active.sealed = True
            self._close_segment(active)

    def _seal_active(self) -> tuple[str | None, _WriteResult]:
        result = _WriteResult()
        with self._lock:
            active = self._segments[-1] if self._segments else None
        if active is None or active.sealed:
            return None, result
        self._seal(active, result)
        return active.name, result
