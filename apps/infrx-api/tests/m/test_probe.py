#!/usr/bin/env python3
"""M2: the container probe, against containers built byte by byte.

    uv run --frozen pytest -q tests/m/test_probe.py

MEDIA-SEC here is not about the network: it is about a parser that reads attacker-supplied
lengths. Every case below is a length, a depth or a declaration a real encoder would never
emit, and the invariant is always the same one - the probe refuses with a typed error, in
bounded time, having allocated nothing from a number the file chose.
"""
from __future__ import annotations

import struct
import time

import pytest
from infrx.contracts import errors
from infrx.media import probe

from . import support


# --- what a well-formed container says ---------------------------------------
@pytest.mark.parametrize("data, mime, seconds, size, codec", [
    (support.mp4(), "video/mp4", 10.0, (640, 480), "h264"),
    (support.mp4(version=1, seconds=115.0, width=1920, height=1080, codec=b"hev1"),
     "video/mp4", 115.0, (1920, 1080), "h265"),
    (support.mp4(brand=b"qt  "), "video/quicktime", 10.0, (640, 480), "h264"),
    (support.mp4(codec=b"av01", timescale=90_000, seconds=2.5), "video/mp4", 2.5,
     (640, 480), "av1"),
    (support.webm(), "video/webm", 10.0, (640, 480), "vp9"),
    (support.webm(seconds=30.0, codec=b"V_VP8", width=1280, height=720),
     "video/webm", 30.0, (1280, 720), "vp8"),
    (support.webm(codec=b"V_MPEG4/ISO/AVC"), "video/webm", 10.0, (640, 480), "h264"),
    # A duration is in timecode-scale units, so the scale is half the number.
    (support.webm(seconds=45.0, scale=1_000), "video/webm", 45.0, (640, 480), "vp9"),
    (support.webm(seconds=8.0, scale=100_000_000), "video/webm", 8.0, (640, 480), "vp9"),
    # Version 1 of the track header moves the geometry by twelve bytes.
    (support.mp4(tracks=support.trak(width=3840, height=2160, tkhd_version=1)),
     "video/mp4", 10.0, (3840, 2160), "h264"),
])
def test_the_probe_reads_the_container_the_bytes_describe(data, mime, seconds, size, codec):
    """Duration, geometry and codec come from the header, and the container is identified
    by its own magic - not by an extension, a `Content-Type` or anything a caller sent."""
    probed = probe.probe(data)
    assert probed.mime == mime
    assert probed.duration_s == pytest.approx(seconds)
    assert (probed.width, probed.height) == size
    assert probed.codec == codec


def test_the_declared_type_is_not_an_argument():
    """There is nowhere to put a claim: `probe` takes bytes. A WebM served as `video/mp4`
    is probed as WebM, which is what the ref, the cache extension and the engine get."""
    assert probe.probe(support.webm()).mime == "video/webm"
    assert probe.probe(support.mp4()).mime == "video/mp4"


def test_a_64_bit_box_length_is_read():
    """The `size == 1` extended form is how any file over 4 GiB stores a box; a probe that
    read the literal 1 would walk into the middle of the header."""
    inner = support.mvhd(5_000, 1_000) + support.trak()
    extended = struct.pack(">I", 1) + b"moov" + struct.pack(">Q", 16 + len(inner)) + inner
    data = support.box(b"ftyp", b"isom" + b"\x00" * 8) + extended
    assert probe.probe(data).duration_s == 5.0


def test_a_box_that_runs_to_the_end_of_the_file_is_read():
    """`size == 0` means "to the end", which real files use for the last `mdat`. Treating it
    as an empty box parses the payload that follows as if it were boxes."""
    data = support.mp4()[:-40] + struct.pack(">I", 0) + b"mdat" + b"\x00\x00\x00\x01" * 8
    assert probe.probe(data).duration_s == 10.0


def test_a_box_longer_than_the_file_is_refused_although_its_content_is_there():
    """The case that separates "checked the length" from "happened to refuse": the container
    is complete and parseable, and only `moov`'s declared length is too big. A probe that
    does not compare the length with what is really there reports a duration for a file it
    has not got."""
    data = support.mp4()
    inflated = data[:20] + struct.pack(">I", int.from_bytes(data[20:24], "big") + 100) + data[24:]
    assert probe.probe(data).duration_s == 10.0
    with pytest.raises(errors.UnsupportedMedia) as raised:
        probe.probe(inflated)
    # The *reason* matters here: a walk that only notices when it falls off the end of the
    # buffer has read another box's bytes as this one's on the way, and would have believed a
    # file whose trailing garbage happened to parse.
    assert raised.value.reason == "bad-box-length"


# --- lengths, depths and declarations ----------------------------------------
@pytest.mark.parametrize("name, data", [
    # A box that claims to be shorter than its own header: advancing by it never moves,
    # and clamping it to the header size walks the same bytes for ever.
    ("box shorter than its header",
     support.box(b"ftyp", b"isom" + b"\x00" * 8) + struct.pack(">I", 4) + b"moov"),
    ("box length of two", support.box(b"ftyp", b"isom" + b"\x00" * 8)
     + struct.pack(">I", 2) + b"moov" + b"\x00" * 64),
    # A length past the end of the buffer: reading it would hand the next box's bytes -
    # or, in a streaming reader, wait for bytes that never come.
    ("box length past the end",
     support.box(b"ftyp", b"isom" + b"\x00" * 8) + struct.pack(">I", 1 << 30) + b"moov"),
    ("64-bit length past the end", support.box(b"ftyp", b"isom" + b"\x00" * 8)
     + struct.pack(">I", 1) + b"moov" + struct.pack(">Q", 1 << 40)),
    ("truncated header", support.box(b"ftyp", b"isom" + b"\x00" * 8) + b"\x00\x00"),
    # The body stops in the middle of the one box every fact is read from.
    ("truncated mvhd", support.mp4()[:len(support.box(b"ftyp", b"isom" + b"\x00" * 8)) + 24]),
    ("an EBML length nobody can bound", support.element(0x1A45DFA3, b"\x00")
     + support.element(0x18538067, b"\x00" * 4, size=support.vint(1 << 40))),
    # A vint whose first byte is zero declares a width of more than eight bytes.
    ("an EBML width of nine", b"\x1a\x45\xdf\xa3" + b"\x00" + b"\x00" * 16),
    # Unknown-size *and* otherwise complete: an element that says "I go on until something
    # ends me" is a length nobody can bound, and accepting it anywhere but on a streamed
    # Segment lets the rest of the file be read as that element's children.
    # Unknown-size *and* otherwise complete: with the restriction dropped this parses
    # perfectly, because everything the probe reads is inside the element that claims to run
    # to the end. "I go on until something ends me" is a length nobody can bound, and it is
    # legal on a streamed Segment and nowhere else.
    ("an unknown size on a leaf", support.element(0x1A45DFA3, b"\x00")
     + support.element(0x18538067, support.element(
         0x1549A966, support.webm_info() + support.webm_tracks(), size=b"\xff"))),
    ("a truncated EBML element", support.webm()[:12]),
    # An EBML unsigned integer is at most eight bytes wide. Wider ones are not "big
    # numbers": 100 bytes of TimecodeScale was accepted as a 6.67e+235-second clip, 200
    # bytes overflowed the multiplication, and a 3,000-byte PixelWidth broke the refusal's
    # own f-string on CPython's 4300-digit limit - a 500 where the contract says
    # `unsupported_media` (review B1).
    ("a 100-byte timecode scale", support.element(0x1A45DFA3, b"\x00")
     + support.element(0x18538067, support.element(
         0x1549A966, support.element(0x2AD7B1, b"\x01" + b"\x00" * 99)
         + support.element(0x4489, struct.pack(">d", 1e4))) + support.webm_tracks())),
    ("a 200-byte timecode scale", support.element(0x1A45DFA3, b"\x00")
     + support.element(0x18538067, support.element(
         0x1549A966, support.element(0x2AD7B1, b"\x01" + b"\x00" * 199)
         + support.element(0x4489, struct.pack(">d", 1e4))) + support.webm_tracks())),
    ("a 3000-byte pixel width", support.element(0x1A45DFA3, b"\x00")
     + support.element(0x18538067, support.webm_info() + support.element(
         0x1654AE6B, support.element(0xAE, support.element(0x83, b"\x01")
                                     + support.element(0x86, b"V_VP9")
                                     + support.element(0xE0, support.element(
                                         0xB0, b"\x01" + b"\x00" * 2_999)
                                         + support.element(0xBA, (480).to_bytes(2, "big"))))))),
    ("a 9-byte track type", support.element(0x1A45DFA3, b"\x00")
     + support.element(0x18538067, support.webm_info() + support.element(
         0x1654AE6B, support.element(0xAE, support.element(0x83, b"\x00" * 9)
                                     + support.element(0x86, b"V_VP9")
                                     + support.element(0xE0, support.element(
                                         0xB0, (640).to_bytes(2, "big"))
                                         + support.element(0xBA, (480).to_bytes(2, "big"))))))),
    # Overlapping siblings: Info claims one byte more than the elements after it, so with the
    # parent bound gone it swallows Tracks and the file parses perfectly. This is the case
    # that separates "an element may not claim past its parent" from "may not claim past the
    # file" - the buffer has bytes there, they just belong to something else.
    ("an element that overlaps its siblings", support.element(0x1A45DFA3, b"\x00")
     + support.element(0x18538067,
                       (0x1549A966).to_bytes(4, "big")
                       + support.vint(len(support.webm_info()) + len(support.webm_tracks()) + 1)
                       + support.webm_info() + support.webm_tracks())
     + support.element(0xEC, b"\x00")),
])
def test_a_hostile_length_is_refused_not_followed(name, data):
    """MEDIA-SEC: every declared length is checked against what is really there. A probe
    that trusts one either loops, reads another box's bytes as this one's, or allocates
    what the file asked for."""
    started = time.monotonic()
    with pytest.raises(errors.UnsupportedMedia):
        probe.probe(data)
    # The bound that matters is that it *returned*; a second is four orders of magnitude
    # more than the walk needs and still catches a loop.
    assert time.monotonic() - started < 1.0, name


def test_a_deeply_nested_container_stops_at_the_depth_limit():
    """A few hundred bytes of nesting must not become a few hundred stack frames."""
    payload = support.stsd()
    for _ in range(40):
        payload = support.box(b"stbl", support.box(b"minf", support.box(b"mdia", payload)))
    # Otherwise complete - a track header and a movie header - so the depth is the only
    # reason to refuse it and the case cannot pass for the wrong one.
    data = (support.box(b"ftyp", b"isom" + b"\x00" * 8)
            + support.box(b"moov", support.mvhd(),
                          support.box(b"trak", support.tkhd(), payload)))
    assert probe.probe(support.mp4()).duration_s == 10.0
    with pytest.raises(errors.UnsupportedMedia) as raised:
        probe.probe(data)
    assert raised.value.reason == "too-deep"


def test_many_empty_boxes_stop_at_the_element_budget():
    """The cheapest CPU bomb in a length-prefixed format: thousands of empty headers in a
    few kilobytes. The walk stops counting at `MAX_ELEMENTS`, whatever the file claims."""
    filler = support.box(b"free") * (probe.MAX_ELEMENTS + 10)
    data = support.box(b"ftyp", b"isom" + b"\x00" * 8) + filler + support.mp4()
    with pytest.raises(errors.UnsupportedMedia):
        probe.probe(data)


# --- declarations that are not media -----------------------------------------
@pytest.mark.parametrize("name, data", [
    ("an empty body", b""),
    ("a JPEG", b"\xff\xd8\xff\xe0" + b"\x00" * 64),
    ("a zip (a media bomb's usual wrapper)", b"PK\x03\x04" + b"\x00" * 64),
    ("an ELF binary", b"\x7fELF" + b"\x00" * 64),
    ("HTML from a captive portal", b"<!DOCTYPE html><html><body>login</body></html>"),
    ("an MPEG-1 program stream (no parser, so no guess)", b"\x00\x00\x01\xba" + b"\x00" * 64),
])
def test_a_container_with_no_parser_is_refused_rather_than_guessed(name, data):
    """A probe that falls back to "probably fine" is how a media bomb is admitted. There is
    no default branch: two containers are parsed and everything else is refused."""
    with pytest.raises(errors.UnsupportedMedia):
        probe.probe(data)


@pytest.mark.parametrize("name, data", [
    ("no moov at all", support.box(b"ftyp", b"isom" + b"\x00" * 8) + support.box(b"mdat")),
    ("a zero duration", support.mp4(seconds=0.0)),
    # A zero timescale with a *non-zero* duration: the pair is what has to be checked, and
    # dividing by it is not a refusal.
    ("a zero timescale", support.box(b"ftyp", b"isom" + b"\x00" * 8)
     + support.box(b"moov", support.mvhd(10_000, 0), support.trak())),
    # A version nobody has defined, with a *plausible* version-0 body behind it: a probe
    # that ignores the version reads 5 s out of a header it does not understand.
    ("an unknown mvhd version",
     support.box(b"ftyp", b"isom" + b"\x00" * 8)
     + support.box(b"moov", support.box(b"mvhd", bytes([7, 0, 0, 0]) + b"\x00" * 8
                                       + struct.pack(">II", 1_000, 5_000) + b"\x00" * 80),
                   support.trak())),
    ("a WebM with no duration", support.element(0x1A45DFA3, b"\x00")
     + support.element(0x18538067, support.element(0x1654AE6B, b""))),
    ("a WebM duration of zero", support.webm(seconds=0.0)),
])
def test_a_container_that_states_no_usable_duration_is_refused(name, data):
    """The duration is what the engine's frame budget is computed from, so "unknown" can
    never become a default: `budget_kwargs(0)` is a four-frame answer about a whole clip."""
    with pytest.raises(errors.UnsupportedMedia):
        probe.probe(data)


@pytest.mark.parametrize("name, data", [
    ("infinity", support.webm(duration=struct.pack(">d", float("inf")))),
    ("not a number", support.webm(duration=struct.pack(">d", float("nan")))),
    ("negative", support.webm(duration=struct.pack(">d", -5e9))),
])
def test_a_non_finite_duration_never_leaves_the_probe(name, data):
    """`inf` and `nan` are two bytes of difference in a float and reach the frame budget as
    an OverflowError - or, negative, as the *minimum* budget for a long clip."""
    with pytest.raises(errors.UnsupportedMedia):
        probe.probe(data)


@pytest.mark.parametrize("name, data", [
    ("a zero-by-zero video", support.mp4(width=0, height=0)),
    ("a one-dimension-zero video", support.mp4(width=1920, height=0)),
    ("65535 square", support.webm(width=65_535, height=65_535)),
    ("wider than 8K", support.mp4(width=probe.MAX_DIMENSION + 1, height=1080)),
])
def test_a_frame_size_that_is_a_declaration_is_refused(name, data):
    """A frame size is a fact about pixels that exist. 0x0 and 65535x65535 are the cheapest
    decode bombs there are, and the second one is a declaration no encoder emits."""
    with pytest.raises(errors.UnsupportedMedia):
        probe.probe(data)


@pytest.mark.parametrize("name, data", [
    ("ProRes", support.mp4(codec=b"apcn")),
    ("MPEG-4 part 2", support.mp4(codec=b"mp4v")),
    ("a JPEG image track", support.mp4(codec=b"jpeg")),
    ("Theora", support.webm(codec=b"V_THEORA")),
    ("an audio-only Matroska", support.webm(codec=b"A_OPUS", track_type=2)),
    ("a subtitle track", support.webm(codec=b"S_TEXT/UTF8", track_type=17)),
    # A video codec declared on an audio track: the track type is what says whether there
    # are frames, so a probe that reads only the codec reports a video that is not one.
    ("a video codec on an audio track", support.webm(codec=b"V_VP9", track_type=2)),
    ("a track with no sample description",
     support.mp4(tracks=support.box(b"trak", support.tkhd()))),
])
def test_a_container_with_no_servable_video_track_is_refused(name, data):
    """The codec allow-list is the serving pin, not the decoder's tolerance: a format the
    engine might happen to open is still a format nobody measured this model on."""
    with pytest.raises(errors.UnsupportedMedia):
        probe.probe(data)


@pytest.mark.parametrize("name, data", [
    ("two movie headers", support.box(b"ftyp", b"isom" + b"\x00" * 8)
     + support.box(b"moov", support.mvhd(10_000, 1_000), support.mvhd(600_000, 1_000),
                   support.trak())),
    # A first header of all zeros must not license a second one: "have I got a duration yet"
    # would have let this through and used the second header's numbers.
    ("a zero movie header then a real one", support.box(b"ftyp", b"isom" + b"\x00" * 8)
     + support.box(b"moov", support.mvhd(0, 0), support.mvhd(600_000, 1_000), support.trak())),
    ("two durations in one Info", support.element(0x1A45DFA3, b"\x00")
     + support.element(0x18538067,
                       support.element(0x1549A966,
                                       support.webm_info(10.0) + support.webm_info(600.0))
                       + support.webm_tracks())),
])
def test_a_duplicate_duration_header_is_refused(name, data):
    """Two headers stating the one number the engine's frame budget is computed from is not
    a file with a spare field: first-wins lets the second copy say anything, so a 10 s clip
    passes the 120 s cap and a decoder that reads the last one samples ten minutes at four
    frames (review R20)."""
    with pytest.raises(errors.UnsupportedMedia) as raised:
        probe.probe(data)
    assert raised.value.reason == "duplicate-header"


def test_the_video_track_is_chosen_by_its_codec_not_its_position():
    """A file whose first track is audio or subtitles still has its geometry read from the
    video track - reading track 1 blindly reports a 0x0 "video"."""
    data = support.mp4(tracks=support.box(b"trak", support.tkhd(0, 0),
                                          support.box(b"mdia", support.box(b"minf",
                                              support.box(b"stbl", support.stsd(b"mp4a")))))
                       + support.trak(width=1920, height=1080, codec=b"avc1"))
    probed = probe.probe(data)
    assert (probed.width, probed.height, probed.codec) == (1920, 1080, "h264")


def test_the_refusal_names_its_reason_for_the_operator_only():
    """The customer sees one fixed message; the reason class stays on the error for the
    log, exactly as M1's fetch refusals do."""
    with pytest.raises(errors.UnsupportedMedia) as raised:
        probe.probe(b"PK\x03\x04" + b"\x00" * 32)
    assert raised.value.reason == "unknown-container"
    assert "PK" not in str(raised.value) and "container" not in str(raised.value)
