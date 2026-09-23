"""M2: what the bytes say they are, read from the bytes.

Every fact preparation needs about a clip - how long it is, how big its frames are and
what codec they are in - is measured here from the object's own header, never taken from
the caller's JSON, the `Content-Type` of the response or the file extension. The engine
computes its frame budget from `duration_s` (`worker/engine.py` `budget_kwargs`), so a
duration the caller could choose is a caller-chosen number of frames, and the 120 s cap
(`MAX_VIDEO_SECONDS`, the 2 fps pin of `research/workloads/marlin-sop.md` §1.5) would
mean nothing.

**No ffmpeg, no ffprobe, no PyAV.** This host has none of them (CLAUDE.md) and the pinned
environment has no media dependency, so the two containers the pilot accepts are parsed
here: ISO base media (MP4/MOV, `ftyp` + `moov`) and Matroska/WebM (EBML). Both are
length-prefixed trees, which is a few hundred lines of walking, and the walk is written
for hostile input rather than for well-formed files:

* it never recurses on caller-controlled depth (an explicit stack, `MAX_DEPTH`),
* it never trusts a length (`size < header`, `size > remaining` and the `size == 0`
  "to end of file" form all refuse instead of looping or reading past the buffer),
* it never allocates from a declared number (nothing here is `read(n)`; the bytes are
  already in memory and bounded by `MAX_MEDIA_BYTES`),
* it stops after `MAX_ELEMENTS` headers whatever the file claims, so a few hundred bytes
  of nested empty boxes cannot cost seconds of CPU.

A container this module cannot parse is `unsupported_media`, not a guess: a probe that
falls back to "probably fine" is a probe that admits a media bomb.

**What it deliberately does not read**, each one measured rather than assumed:

* **Edit lists.** `edts`/`elst` are ignored, so a clip whose edit list trims or repeats a
  span is reported at its untrimmed `mvhd` duration. The engine samples the same untrimmed
  clip, so the frame budget still matches what is decoded; a decoder that honours edit
  lists would see a shorter clip than the budget was sized for, which costs frames and
  never gains any.
* **A `mvhd` duration of `0xFFFFFFFF`** ("unknown", the version-0 sentinel) is read as
  4,294,967.295 s at a 1 kHz timescale and then refused by the duration cap, not by the
  probe. The outcome is a refusal either way; it is recorded because the refusal says "too
  long" rather than "no duration".
* **Fragmented MP4.** A file whose duration lives in `mvex`/`mehd` with `mvhd.duration = 0`
  is refused as `no-duration`, so fMP4 and DASH/CMAF segments are not served. Reading
  `mehd` would be a few lines, but a fragmented file's real duration is the sum of its
  fragments and `mehd` is only a hint, so the cap would be enforced against a number the
  file is free to understate.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from ..contracts import errors

# The two containers with a parser here. `video/mpeg` is in `ALLOWED_VIDEO_MIME` and is
# **not** in this table: an MPEG-1/2 program stream carries no duration in a header (it is
# derived by scanning presentation timestamps to the end of the file), so there is nothing
# to read cheaply and nothing here pretends otherwise. Refusing it is a narrowing of the
# accepted set and an integration request, not a silent pass.
MP4_MIME = "video/mp4"
QUICKTIME_MIME = "video/quicktime"
WEBM_MIME = "video/webm"

# Sample-entry formats (MP4) and `CodecID`s (Matroska) the pilot will serve, mapped to one
# spelling. Fail-closed: anything else - ProRes, Theora, MPEG-4 part 2, a JPEG image track -
# is refused by name, because "the engine will probably decode it" is not a serving pin.
MP4_CODECS = {"avc1": "h264", "avc3": "h264", "hev1": "h265", "hvc1": "h265",
              "av01": "av1", "vp08": "vp8", "vp09": "vp9"}
MATROSKA_CODECS = {"V_MPEG4/ISO/AVC": "h264", "V_MPEGH/ISO/HEVC": "h265",
                   "V_AV1": "av1", "V_VP8": "vp8", "V_VP9": "vp9"}

MAX_DEPTH = 8                  # moov/trak/mdia/minf/stbl/stsd is six; eight is slack
MAX_ELEMENTS = 4_096           # headers walked per probe, whatever the file declares
MAX_DIMENSION = 8_192          # 8K is 7680 wide; 0 and 65535 are declarations, not frames
BOX_HEADER = 8
# Matroska stores a duration in timecode-scale units; the default scale is 1 ms.
DEFAULT_TIMECODE_SCALE = 1_000_000
NANOSECONDS = 1_000_000_000

# EBML ids, as the integers their id bytes spell (marker bits included, which is how the
# specification writes them).
EBML_HEADER = 0x1A45DFA3
SEGMENT = 0x18538067
INFO = 0x1549A966
TIMECODE_SCALE = 0x2AD7B1
DURATION = 0x4489
TRACKS = 0x1654AE6B
TRACK_ENTRY = 0xAE
TRACK_TYPE = 0x83
CODEC_ID = 0x86
VIDEO = 0xE0
PIXEL_WIDTH = 0xB0
PIXEL_HEIGHT = 0xBA
VIDEO_TRACK_TYPE = 1
# Only these are descended into. A leaf whose payload happened to start with a known id
# would otherwise be walked as a tree, which is how a fuzzer finds a parser.
EBML_CONTAINERS = frozenset({SEGMENT, INFO, TRACKS, TRACK_ENTRY, VIDEO})


@dataclass(frozen=True)
class Probed:
    """What the bytes say. `mime` is the sniffed container, not the declared one."""

    mime: str
    duration_s: float
    width: int
    height: int
    codec: str


def _refuse(why: str) -> errors.UnsupportedMedia:
    """One refusal class for every unreadable container: the customer learns that the
    media is not supported, and the reason class stays in the operator's log."""
    error = errors.UnsupportedMedia("the media could not be read as a supported video",
                                    param="messages")
    error.reason = why
    return error


def _u(data: bytes, at: int, size: int) -> int:
    """A big-endian unsigned integer, or a refusal if it is not all there."""
    if at < 0 or at + size > len(data):
        raise _refuse("truncated")
    return int.from_bytes(data[at:at + size], "big")


# --- ISO base media (MP4, M4V, MOV) ------------------------------------------
def _iso_children(data: bytes, start: int, end: int):
    """The boxes between `start` and `end`, with every declared length checked.

    The three lengths a hostile file uses are all handled here rather than in the walk:
    `size == 1` (64-bit extended), `size == 0` ("to the end", which must still advance) and
    a size smaller than its own header or larger than what is left - both refusals, because
    clamping the first one is an infinite loop and clamping the second reads another box's
    bytes as this one's.
    """
    at = start
    while at + BOX_HEADER <= end:
        size = _u(data, at, 4)
        kind = data[at + 4:at + 8].decode("latin-1")
        body = at + BOX_HEADER
        if size == 1:
            size = _u(data, body, 8)
            body += 8
        elif size == 0:
            size = end - at
        if size < body - at or at + size > end:
            raise _refuse("bad-box-length")
        yield kind, body, at + size
        at += size


def _mvhd(data: bytes, start: int) -> tuple[int, int]:
    """(timescale, duration) from the movie header, version 0 or 1."""
    version = _u(data, start, 1)
    if version == 1:
        return _u(data, start + 20, 4), _u(data, start + 24, 8)
    if version == 0:
        return _u(data, start + 12, 4), _u(data, start + 16, 4)
    raise _refuse("mvhd-version")


def _tkhd(data: bytes, start: int) -> tuple[int, int]:
    """(width, height) from the track header, as the 16.16 fixed-point it stores."""
    version = _u(data, start, 1)
    at = start + (88 if version == 1 else 76)
    if version not in (0, 1):
        raise _refuse("tkhd-version")
    return _u(data, at, 4) >> 16, _u(data, at + 4, 4) >> 16


def _stsd_format(data: bytes, start: int, end: int) -> str:
    """The first sample entry's four-character format, or "" if there is none."""
    entry = start + 8                          # version/flags, entry_count
    if entry + BOX_HEADER > end:
        return ""
    return data[entry + 4:entry + 8].decode("latin-1")


def probe_iso(data: bytes) -> Probed:
    """Duration, frame size and codec from `moov`, or a typed refusal.

    One walk with an explicit stack: `mvhd` gives the duration, and each `trak` is read for
    its `tkhd` size and its `stsd` format. The **video** track is the one whose sample entry
    is a video codec, which is also the fact the allow-list needs, so no `hdlr` detour.
    """
    timescale = duration = 0
    seen_movie_header = False
    tracks: list[dict] = []
    walked = 0
    # (start, end, depth, the trak this subtree belongs to or None)
    stack: list[tuple[int, int, int, dict | None]] = [(0, len(data), 0, None)]
    while stack:
        start, end, depth, track = stack.pop()
        for kind, body, box_end in _iso_children(data, start, end):
            walked += 1
            if walked > MAX_ELEMENTS:
                raise _refuse("too-many-boxes")
            if kind == "mvhd":
                if seen_movie_header:
                    # Two movie headers disagree about the one number the frame budget is
                    # computed from, and first-wins means the second one is a free rewrite
                    # of whatever a checker looked at (review R20). A flag rather than
                    # "have I got a duration yet", so a first header of all zeros cannot
                    # license a second one.
                    raise _refuse("duplicate-header")
                seen_movie_header = True
                timescale, duration = _mvhd(data, body)
            elif kind == "tkhd" and track is not None:
                track["width"], track["height"] = _tkhd(data, body)
            elif kind == "stsd" and track is not None:
                track.setdefault("format", _stsd_format(data, body, box_end))
            elif kind in ("moov", "trak", "mdia", "minf", "stbl"):
                if depth >= MAX_DEPTH:
                    raise _refuse("too-deep")
                if kind == "trak":
                    track = {"width": 0, "height": 0}
                    tracks.append(track)
                stack.append((body, box_end, depth + 1, track))
    if not timescale or not duration:
        raise _refuse("no-duration")
    video = next((t for t in tracks if t.get("format") in MP4_CODECS), None)
    if video is None:
        formats = sorted({str(t.get("format") or "?") for t in tracks})
        raise _refuse(f"no-supported-video-track:{','.join(formats)[:40]}")
    brand = data[8:12] if data[4:8] == b"ftyp" else b""
    return Probed(mime=QUICKTIME_MIME if brand.startswith(b"qt") else MP4_MIME,
                  duration_s=duration / timescale,
                  width=video["width"], height=video["height"],
                  codec=MP4_CODECS[video["format"]])


# --- Matroska / WebM ---------------------------------------------------------
def _vint(data: bytes, at: int, *, keep_marker: bool) -> tuple[int, int, bool]:
    """One EBML variable-length integer: (value, bytes consumed, is the "unknown" form).

    An id keeps its marker bits (that is how ids are written); a size strips them. A first
    byte of zero declares a width of more than eight bytes, which no valid element has and
    which is refused rather than read as a huge length.
    """
    first = _u(data, at, 1)
    if first == 0:
        raise _refuse("bad-vint")
    width = 8 - first.bit_length() + 1
    if at + width > len(data):
        raise _refuse("truncated")
    raw = int.from_bytes(data[at:at + width], "big")
    mask = (1 << (7 * width)) - 1
    return (raw if keep_marker else raw & mask), width, (raw & mask) == mask


def _ebml_uint(data: bytes, at: int, size: int) -> int:
    """An EBML unsigned integer, whose width the specification bounds at eight bytes.

    `_u` will read any width the file declares, and every value read here ends up in
    arithmetic or in a message: a 100-byte `TimecodeScale` was **accepted** and produced a
    duration of 6.67e+235, a 200-byte one raised `OverflowError` on the multiplication, and
    a 3000-byte `PixelWidth` raised `ValueError` (CPython's 4300-digit `int.__str__` limit)
    while formatting the refusal. All three were a 500 where the contract says
    `unsupported_media` (review B1).
    """
    if size > 8:
        raise _refuse("bad-uint")
    # A zero-length unsigned integer is legal and means zero, which is what `_u` returns and
    # what every reader of one here already treats as "absent": the timecode scale falls back
    # to the default, and a zero track type or dimension is refused further down.
    return _u(data, at, size)


def _ebml_float(data: bytes, start: int, end: int) -> float:
    size = end - start
    if size == 4:
        return struct.unpack(">f", data[start:end])[0]
    if size == 8:
        return struct.unpack(">d", data[start:end])[0]
    if 0 < size <= 8:                     # an integer duration is legal too
        return float(int.from_bytes(data[start:end], "big"))
    raise _refuse("bad-duration")


def probe_matroska(data: bytes) -> Probed:
    """Duration, frame size and codec from the EBML tree, or a typed refusal."""
    duration = 0.0
    scale = DEFAULT_TIMECODE_SCALE
    tracks: list[dict] = []
    current: dict | None = None
    walked = 0
    stack: list[tuple[int, int, int]] = [(0, len(data), 0)]
    while stack:
        start, end, depth = stack.pop()
        at = start
        while at < end:
            element, id_width, _ = _vint(data, at, keep_marker=True)
            size, size_width, unknown = _vint(data, at + id_width, keep_marker=False)
            body = at + id_width + size_width
            walked += 1
            if walked > MAX_ELEMENTS:
                raise _refuse("too-many-elements")
            # An unknown size is legal for a streamed Segment only, and means "the rest".
            # Anywhere else it is a length nobody can bound, so it is refused.
            if unknown:
                if element != SEGMENT:
                    raise _refuse("unknown-size")
                size = end - body
            if size < 0 or body + size > end:
                raise _refuse("bad-element-length")
            body_end = body + size
            if element in EBML_CONTAINERS:
                if depth >= MAX_DEPTH:
                    raise _refuse("too-deep")
                if element == TRACK_ENTRY:
                    current = {"type": 0, "codec": "", "width": 0, "height": 0}
                    tracks.append(current)
                stack.append((body, body_end, depth + 1))
            elif element == TIMECODE_SCALE:
                scale = _ebml_uint(data, body, size) or DEFAULT_TIMECODE_SCALE
            elif element == DURATION:
                if duration:
                    raise _refuse("duplicate-header")
                duration = _ebml_float(data, body, body_end)
            elif current is not None and element == TRACK_TYPE:
                current["type"] = _ebml_uint(data, body, size)
            elif current is not None and element == CODEC_ID:
                current["codec"] = data[body:body_end].decode("latin-1").rstrip("\0")
            elif current is not None and element == PIXEL_WIDTH:
                current["width"] = _ebml_uint(data, body, size)
            elif current is not None and element == PIXEL_HEIGHT:
                current["height"] = _ebml_uint(data, body, size)
            at = body_end
    if duration <= 0:
        raise _refuse("no-duration")
    video = next((t for t in tracks
                  if t["type"] == VIDEO_TRACK_TYPE and t["codec"] in MATROSKA_CODECS), None)
    if video is None:
        codecs = sorted({t["codec"] or "?" for t in tracks})
        raise _refuse(f"no-supported-video-track:{','.join(codecs)[:40]}")
    return Probed(mime=WEBM_MIME, duration_s=duration * scale / NANOSECONDS,
                  width=video["width"], height=video["height"],
                  codec=MATROSKA_CODECS[video["codec"]])


# --- M4: the header of a download still in progress -----------------------------
def probe_header(data) -> Probed | None:
    """What a header-first ISO file says before its media has arrived, or None.

    The probe of every top-level box up to the end of the first `moov`. None when there is
    nothing to read yet - not ISO base media, the `moov` still arriving, or the media first
    (the `moov` is then at the end, and only the whole object says anything). Never a
    refusal of its own: a prefix that does not probe is left to `probe` on the whole object.
    A `Probed` from here is read from bytes the whole-object probe reads the same way, so a
    profile refusal based on it is one the complete download would also get - it only comes
    sooner. `data` may be the fetcher's growing `bytearray`; nothing here keeps it.
    """
    if data[4:8] != b"ftyp":
        return None
    at = 0
    try:
        for _ in range(MAX_ELEMENTS):
            size, kind = _u(data, at, 4), data[at + 4:at + 8]
            if size == 1:
                size = _u(data, at + 8, 8)
            if kind == b"moov":
                return probe(data[:at + size])
            if size < BOX_HEADER:          # "to the end of the file": no header after it
                return None
            at += size
    except errors.UnsupportedMedia:        # truncated, or a moov that does not probe (yet)
        return None
    return None


# --- the entry point ---------------------------------------------------------
def probe(data: bytes) -> Probed:
    """What the bytes are, by their own magic. The declared type is not an argument.

    `_refuse` for everything that is not one of the two containers with a parser, for a
    container that does not hold a video track this pilot serves, and for any dimension
    that is a declaration rather than a frame size (a 0-by-0 or 65535-by-65535 "video" is
    the cheapest media bomb there is).
    """
    if data[:4] == b"\x1a\x45\xdf\xa3":
        probed = probe_matroska(data)
    elif len(data) >= 8 and data[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"skip"):
        probed = probe_iso(data)
    else:
        raise _refuse("unknown-container")
    if not 0 < probed.width <= MAX_DIMENSION or not 0 < probed.height <= MAX_DIMENSION:
        raise _refuse(f"dimensions:{probed.width}x{probed.height}")
    # `inf`/`nan` reach here from a Matroska float duration, and the engine's own guard is
    # the last line of defence, not the first: a non-finite duration must never become a
    # frame budget, a token estimate or a hold.
    if not (0 < probed.duration_s < float("inf")):
        raise _refuse(f"duration:{probed.duration_s}")
    return probed
