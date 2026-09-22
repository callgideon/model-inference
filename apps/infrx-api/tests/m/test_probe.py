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
    ("an unknown size on a leaf", support.element(0x1A45DFA3, b"\x00")
     + support.element(0x18538067,
                       support.element(0x1549A966, b"", size=b"\xff") + b"\x00" * 8)),
    ("a truncated EBML element", support.webm()[:12]),
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
    data = (support.box(b"ftyp", b"isom" + b"\x00" * 8)
            + support.box(b"moov", support.mvhd(), support.box(b"trak", payload)))
    with pytest.raises(errors.UnsupportedMedia):
        probe.probe(data)


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
    ("a zero timescale", support.mp4(timescale=0)),
    ("an unknown mvhd version",
     support.box(b"ftyp", b"isom" + b"\x00" * 8)
     + support.box(b"moov", support.box(b"mvhd", bytes([7]) + b"\x00" * 100), support.trak())),
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
    ("a track with no sample description",
     support.mp4(tracks=support.box(b"trak", support.tkhd()))),
])
def test_a_container_with_no_servable_video_track_is_refused(name, data):
    """The codec allow-list is the serving pin, not the decoder's tolerance: a format the
    engine might happen to open is still a format nobody measured this model on."""
    with pytest.raises(errors.UnsupportedMedia):
        probe.probe(data)


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
