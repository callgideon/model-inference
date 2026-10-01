"""T1's segment format: the on-disk frames, their checksum, the recovery reader (A12, W6).

Split mechanically out of `spool.py` (which re-exports every name, so the shipper, the
drills and `recover` callers are unchanged). Pure functions over bytes plus `SpoolIO`, the
one injectable filesystem both the writer (`SpoolTraceSink`) and the reader use; the
format itself is documented in `spool.py`'s module docstring.
"""
from __future__ import annotations

import binascii
import fcntl
import os
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts.records import TraceEnvelope


SEGMENT_VERSION = 2
SEGMENT_MAGIC = b"INFRXTRC"
SEGMENT_PREFIX = "trace-"
SEGMENT_SUFFIX = ".seg"
HEADER = struct.Struct("!8sH")          # magic, version
FRAME = struct.Struct("!III")           # envelope bytes, content bytes, crc32
# What the crc32 covers besides the bytes: the two lengths and the record's position in its
# segment. The position is *derived* by the reader, not stored, but it is checksummed, so a
# frame excised from or duplicated inside a segment cannot silently renumber the records
# after it - it is a torn segment instead (round 2).
LENGTHS = struct.Struct("!IIQ")
# A metadata row is a few KiB. A torn tail can claim any length at all, so an envelope
# length past this is corruption rather than a record we are missing bytes for.
MAX_ENVELOPE_BYTES = 1 << 20


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


def frame_checksum(payload: bytes, parts, content_bytes: int, position: int) -> int:
    """The checksum over a frame's **lengths and position**, its envelope and its content.

    The lengths are inside the checksum because a corrupt length field is how a reader
    silently mis-frames a record; round 1 of review found a single flipped bit turning one
    record into two with a wrong identity. The position joined them in round 2: it is derived
    rather than stored, so nothing on disk can corrupt it, but a frame *excised* from or
    *duplicated* inside a segment shifts every record after it onto someone else's identity,
    and checksumming the position is what turns that into a torn segment. `parts` is chained
    through `crc32` rather than joined, so a 96 MiB content part is never copied into a third
    buffer just to be hashed - nor, since round 2, joined at all.
    """
    crc = binascii.crc32(LENGTHS.pack(len(payload), content_bytes, position))
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
        if frame_checksum(payload, (content,), content_bytes, index) != crc:
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
