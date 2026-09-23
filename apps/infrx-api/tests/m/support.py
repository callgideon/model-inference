#!/usr/bin/env python3
"""Fixtures for the M suites. Nothing here touches the network or the wall clock.

The three seams `MediaFetcher` takes - `resolve`, `transport` and `monotonic` - are
everything the adversarial cases need: a rebinding resolver is a resolver whose second
answer differs from its first, a slow-loris body is a clock that jumps between chunks,
and a lying `Content-Length` is a header that disagrees with the stream beside it.
"""
from __future__ import annotations

import httpx

# Addresses, not names, so a case says what it means. 93.184.216.34 is example.com's
# documentation address; the rest are the ranges MEDIA-SEC names.
PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"
# A v4-mapped *public* address is allowed, because the v4 it names is. It is here so the
# mapped unwrap in `address_allowed` is killable: without the unwrap this form is refused
# as reserved space, along with every other mapped address.
PUBLIC_MAPPED = "::ffff:93.184.216.34"
METADATA = "169.254.169.254"
INTERNAL = (METADATA, "127.0.0.1", "10.0.0.7", "192.168.1.1", "100.64.0.1", "0.0.0.0",
            "::1", "fe80::1", "fc00::1", "::",
            "::ffff:169.254.169.254",          # v4-mapped metadata
            "::ffff:10.0.0.7",                 # v4-mapped private
            "::127.0.0.1",                     # IPv4-compatible loopback: global, not private
            "::a9fe:a9fe",                     # IPv4-compatible metadata
            "::10.0.0.1",                      # IPv4-compatible private
            "4000::1",                         # reserved space outside ::/96
            "224.0.0.1", "239.255.255.250",    # multicast, v4
            "ff02::1", "ff05::1:3",            # multicast, v6
            "64:ff9b::a9fe:a9fe",              # NAT64, well-known prefix
            "64:ff9b:1::a9fe:a9fe",            # NAT64, local use
            "2002:a9fe:a9fe::",                # 6to4 to the metadata address
            "2002:5db8:d822::",                # 6to4 with a public embedded v4
            "192.88.99.1",                     # 6to4 relay anycast
            "2001:0:4136:e378:8000:63bf:3fff:fdd2",   # Teredo
            "fec0::1",                         # deprecated site-local: is_global says True
            "2001:20::1",                      # ORCHIDv2
            "3fff::1",                         # documentation (RFC 9637)
            "198.18.0.1",                      # benchmarking (RFC 2544)
            "192.0.0.1",                       # IETF protocol assignments
            "not an address", "", "169.254.169.254%eth0")


def resolver(*answers):
    """A resolver whose n-th call returns the n-th answer; the last one repeats.

    `resolver([PUBLIC], [METADATA])` is the DNS-rebinding fixture: the answer that was
    validated and the answer that would be used if the name were resolved again.
    """
    calls: list[str] = []

    async def resolve(host):
        calls.append(host)
        answer = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return list(answer)

    resolve.calls = calls
    return resolve


class Chunks(httpx.AsyncByteStream):
    """A body that yields its chunks one at a time, counting what was really read.

    `on_chunk` is how the slow-loris case advances the injected clock: the body arrives,
    just far too slowly to be worth waiting for.
    """

    def __init__(self, chunks, on_chunk=None) -> None:
        self.chunks = list(chunks)
        self.on_chunk = on_chunk
        self.read = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += len(chunk)
            if self.on_chunk is not None:
                self.on_chunk()
            yield chunk


def response(status=200, *, mime="video/mp4", body=b"\x00\x00\x00 ftypmp42", headers=None,
            stream=None, location=None):
    """One scripted reply. `stream` gives a case control over what really arrives."""
    head = {}
    if mime is not None:
        head["content-type"] = mime
    if location is not None:
        head["location"] = location
    head.update(headers or {})
    # Always a stream: a `content=` response is already consumed, and the fetcher reads
    # the raw stream precisely so it can stop in the middle of one.
    return httpx.Response(status, headers=head, stream=stream or Chunks([body] if body else []))


class Transport:
    """Records the pinned requests and replies from a script, without a socket.

    Each script entry is a `httpx.Response` or a zero-argument callable returning one,
    so a case can hand out a fresh streaming body per hop. The last entry repeats.
    """

    def __init__(self, *script) -> None:
        self.script = list(script) or [response()]
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        entry = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        return entry() if callable(entry) else entry

    @property
    def pinned(self) -> list[str]:
        return [request.url.host for request in self.requests]

    @property
    def hosts(self) -> list[str]:
        return [request.headers.get("host", "") for request in self.requests]


class Ticker:
    """An injected monotonic clock that advances by `step` on every read. With `step=0`
    time does not pass, which is what every case except the slow-body one wants."""

    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


class Durable:
    """The attach record another process reads (MPILOT gap 2): `PgAttachments`' contract
    over a dict. `tests/m/test_pilot_media.py`'s `_pg` cases run the real one."""

    def __init__(self) -> None:
        self.rows: dict = {}

    async def put(self, job_id, refs):
        self.rows[job_id] = tuple(refs)

    async def get(self, job_id):
        return self.rows.get(job_id)


class Records:
    """A logger whose lines a case can read back, to prove what is *not* in them."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def warning(self, message, *args) -> None:
        self.lines.append(message % args if args else message)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


# --- synthetic containers (M2) ------------------------------------------------
# There is no ffmpeg, no ffprobe and no PyAV on this host (CLAUDE.md) and none in the
# pinned environment, so the fixtures are *built*, byte by byte, from the two container
# formats `infrx.media.probe` parses. That is the honest way round: a probe proved against
# containers a library produced would be proved against that library's habits, while these
# say exactly which bytes are under test - and the adversarial cases (a box shorter than
# its header, an EBML length nobody can bound, a duration of `inf`) are ones no encoder
# would ever emit.
import struct


def box(kind: bytes, *payload: bytes) -> bytes:
    """One ISO base-media box: a 32-bit length over its own header and payload."""
    body = b"".join(payload)
    return struct.pack(">I", 8 + len(body)) + kind + body


def mvhd(duration: int = 10_000, timescale: int = 1_000, version: int = 0) -> bytes:
    if version == 1:
        head = bytes([1, 0, 0, 0]) + b"\x00" * 16 + struct.pack(">IQ", timescale, duration)
    else:
        head = b"\x00" * 4 + b"\x00" * 8 + struct.pack(">II", timescale, duration)
    return box(b"mvhd", head + b"\x00" * 80)


def tkhd(width: int = 640, height: int = 480, version: int = 0) -> bytes:
    """A track header. Version 1 widens the three time fields, which moves the geometry."""
    head = bytes([version, 0, 0, 0]) + (b"\x00" * 32 if version == 1 else b"\x00" * 20)
    return box(b"tkhd", head + b"\x00" * 8 + b"\x00" * 8 + b"\x00" * 36
               + struct.pack(">II", width << 16, height << 16))


def stsd(codec: bytes = b"avc1") -> bytes:
    entry = box(codec, b"\x00" * 70)
    return box(b"stsd", b"\x00" * 4 + struct.pack(">I", 1) + entry)


def trak(*, width: int = 640, height: int = 480, codec: bytes = b"avc1",
         tkhd_version: int = 0) -> bytes:
    return box(b"trak", tkhd(width, height, tkhd_version),
               box(b"mdia", box(b"minf", box(b"stbl", stsd(codec)))))


def mp4(*, seconds: float = 10.0, width: int = 640, height: int = 480, codec: bytes = b"avc1",
        timescale: int = 1_000, brand: bytes = b"isom", version: int = 0,
        tracks: bytes | None = None) -> bytes:
    """A container with exactly the boxes a duration/geometry probe reads."""
    moov = box(b"moov", mvhd(round(seconds * timescale), timescale, version),
               trak(width=width, height=height, codec=codec) if tracks is None else tracks)
    return box(b"ftyp", brand + b"\x00\x00\x02\x00" + brand) + moov + box(b"mdat", b"\x00" * 32)


def vint(value: int, *, width: int | None = None) -> bytes:
    """An EBML length, in the narrowest form that holds it unless a width is forced."""
    width = width or next(w for w in range(1, 9) if value < (1 << (7 * w)) - 1)
    return (value | (1 << (7 * width))).to_bytes(width, "big")


def element(element_id: int, payload: bytes, *, size: bytes | None = None) -> bytes:
    raw = element_id.to_bytes((element_id.bit_length() + 7) // 8, "big")
    return raw + (size or vint(len(payload))) + payload


def webm(*, seconds: float = 10.0, width: int = 640, height: int = 480,
         codec: bytes = b"V_VP9", scale: int = 1_000_000, track_type: int = 1,
         duration: bytes | None = None) -> bytes:
    """A Matroska segment with the Info and Tracks elements a probe reads."""
    info = element(0x1549A966,
                   element(0x2AD7B1, scale.to_bytes(4, "big"))
                   + element(0x4489, duration if duration is not None
                             else struct.pack(">d", seconds * 1e9 / scale)))
    track = element(0xAE,
                    element(0x83, bytes([track_type])) + element(0x86, codec)
                    + element(0xE0, element(0xB0, width.to_bytes(2, "big"))
                              + element(0xBA, height.to_bytes(2, "big"))))
    return (element(0x1A45DFA3, b"\x00")
            + element(0x18538067, info + element(0x1654AE6B, track)))


def webm_info(seconds: float = 10.0, scale: int = 1_000_000) -> bytes:
    """The Info payload on its own, for a case that has to place it by hand."""
    return (element(0x2AD7B1, scale.to_bytes(4, "big"))
            + element(0x4489, struct.pack(">d", seconds * 1e9 / scale)))


def webm_tracks(codec: bytes = b"V_VP9", width: int = 640, height: int = 480,
                track_type: int = 1) -> bytes:
    """The Tracks element on its own, likewise."""
    return element(0x1654AE6B,
                   element(0xAE, element(0x83, bytes([track_type])) + element(0x86, codec)
                           + element(0xE0, element(0xB0, width.to_bytes(2, "big"))
                                     + element(0xBA, height.to_bytes(2, "big")))))


# --- M4: a second object store and the parity facts ----------------------------
import os
import pathlib
from types import SimpleNamespace

from infrx.config import Settings
from infrx.media.fetch import digest_of
from infrx.media.video import Media


class FileObjectStore:
    """The `store.ObjectStore` port on a directory, so MEDIA-PARITY can compare two stores
    that share no code. Keys are server-built (`store.py`), so a key is a relative path; the
    digest and type live in a sidecar written with the object. A test double: I/O is
    synchronous, and there is nothing concurrent inside one `put_if_absent`."""

    def __init__(self, root) -> None:
        self.root = pathlib.Path(root)

    def _path(self, key: str) -> pathlib.Path:
        path = (self.root / key).resolve()
        assert path.is_relative_to(self.root.resolve()), key
        return path

    def _meta(self, key: str) -> tuple[str, str] | None:
        side = self._path(key + ".meta")
        return tuple(side.read_text().split("\n", 1)) if side.exists() else None

    async def head(self, key):
        meta = self._meta(key)
        return meta[0] if meta else None

    async def get(self, key):
        return self._path(key).read_bytes() if self._meta(key) else None

    async def put_if_absent(self, key, data, content_type):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb") as handle:
                handle.write(data)
        except FileExistsError:
            return False
        self._path(key + ".meta").write_text(f"{digest_of(bytes(data))}\n{content_type}")
        return True

    async def describe(self, key):
        meta = self._meta(key)
        return (self._path(key).stat().st_size, meta[1]) if meta else None

    async def keys(self, prefix):
        found = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                 if p.is_file() and not p.name.endswith(".meta"))
        return sorted(k for k in found if k.startswith(prefix))

    async def delete(self, key):
        for path in (self._path(key), self._path(key + ".meta")):
            path.unlink(missing_ok=True)


BUDGET = Media(SimpleNamespace(settings=Settings()))


async def prepared_facts(adapter, org_id: str, source: str, job_id: str) -> dict:
    """Everything a prepared clip is, as the engine and the store will see it.

    Materialize one source, attach it to a job, prepare it at profile v1 and read back
    what was produced: the prepared ref, the digest of the durable prepared object and of
    the local file the engine opens, that file's place under the cache root, and the
    frame/pixel budget the engine derives from the measured duration, plus the frame size and
    codec the probe read. MEDIA-PARITY is
    "these facts are equal" across runs, stores and cache expiry."""
    ref = await adapter.materialize(org_id, source)
    await adapter.attach(job_id, (ref,))
    (prepared,) = await adapter.prepare(job_id, "v1")
    local = adapter.local_uri(prepared).removeprefix("file://")
    with open(local, "rb") as handle:
        local_digest = digest_of(handle.read())
    probed = adapter.cache.get(prepared.org_id, prepared.digest, prepared.profile_version).probed
    return {"source_digest": ref.digest,
            "probed": {"width": probed.width, "height": probed.height, "codec": probed.codec},
            "ref": prepared.model_dump(mode="json", exclude={"kind"}),
            "prepared_digest": digest_of(await adapter.objects.get(prepared.storage_ref)),
            "local_digest": local_digest,
            "local_path": os.path.relpath(local, adapter.cache.root),
            "budget": BUDGET.budget_kwargs(prepared.duration_s)}
