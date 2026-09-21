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
METADATA = "169.254.169.254"
INTERNAL = (METADATA, "127.0.0.1", "10.0.0.7", "192.168.1.1", "100.64.0.1", "0.0.0.0",
            "::1", "fe80::1", "fc00::1", "::",
            "::ffff:169.254.169.254",          # v4-mapped metadata
            "64:ff9b::a9fe:a9fe",              # NAT64 well-known prefix
            "2002:a9fe:a9fe::",                # 6to4
            "2001:0:4136:e378:8000:63bf:3fff:fdd2",   # Teredo
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


class Records:
    """A logger whose lines a case can read back, to prove what is *not* in them."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def warning(self, message, *args) -> None:
        self.lines.append(message % args if args else message)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)
