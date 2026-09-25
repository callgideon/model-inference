#!/usr/bin/env python3
"""INTAKE-DRAIN: a refusal raised while the body is still arriving is READ by the caller.

Real sockets, on purpose: the defect only exists in the kernel. The E4B certification's
overload cell (release 4226315, 2026-09-24) sent 32 `video_b64` bodies of 0.3-3 MB;
the gateway journalled a 429 for each refused one, and the client read 14 of them as
`ReadError` - the gateway closed with body bytes unread, the kernel answered the
client's next write with an RST, and the typed 429 died in flight. So these cases run
the app under uvicorn on a private loopback port (the deploy unit's server, same
defaults) and talk to it with httpx (the certification's client) or a raw socket.

Wall-clock bounds are the point here, unlike `test_intake.py`: "the drain stopped" is
"the answer arrived before the client's own timeout", and every wait is bounded.
"""
from __future__ import annotations

import base64
import contextlib
import json
import socket
import threading
import time

import asyncio

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from infrx.contracts import errors
from infrx.gateway.routes import ingress, intake
from infrx.observe import metrics

from . import support

# The shape the certification sends: one user message, a `video/mp4` data URL, ~3 MB.
VIDEO = base64.b64encode(b"\0" * 2_250_000).decode()
BODY = json.dumps({"model": support.PUBLIC_MODEL, "messages": [{"role": "user", "content": [
    {"type": "text", "text": "count"},
    {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + VIDEO}}]}]}).encode()
CLIENT_TIMEOUT_S = 3.0      # well past a prompt answer, well short of a drain that waits


@contextlib.contextmanager
def served(**pilot):
    """The chat ingress under uvicorn on a loopback socket; the large-body gate refuses
    every body over its threshold (`limit=0`), which is the box's 429 with Retry-After 2."""
    calls, accept = support.recorder()
    app, _ = support.cutover_app(
        support.settings(**pilot),
        ingress_deps=support.deps(accept=accept, large_bodies=intake.LargeBodies(limit=0)))
    sock = socket.socket()
    # Inherited by every accepted connection: a 3 MB body is then still in flight when the
    # gate answers, as it is across a network or on a loaded box. Loopback's autotuned
    # default (several MB) would let the client finish writing first and hide the race.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 16)
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn did not start"
    try:
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def raw_exchange(port, head: bytes, sent: bytes) -> tuple[bytes, bool, float]:
    """Send `head` + `sent` and never the rest; read until the server closes.

    Returns (what was read, whether the server closed, seconds to the first byte). A
    server that waits for the rest of the body answers after the client's timeout, which
    reads as `b""`/not closed rather than as an exception, and so does a reset: the
    defect this file is about is a connection that ends without the answer."""
    with socket.create_connection(("127.0.0.1", port)) as conn:
        conn.settimeout(CLIENT_TIMEOUT_S)
        began = time.monotonic()
        got, closed, first = b"", False, None
        try:
            conn.sendall(head + sent)
            while True:
                chunk = conn.recv(65536)
                if first is None:
                    first = time.monotonic() - began
                if not chunk:
                    closed = True
                    break
                got += chunk
        except OSError:                     # a timeout, or the reset (ECONNRESET/EPIPE)
            pass
        return got, closed, first if first is not None else float("inf")


def head(length: int | None, *, key: bool = True) -> bytes:
    lines = [f"POST {support.CHAT_PATH} HTTP/1.1", "host: gateway",
             "content-type: application/json"]
    if key:
        lines.append(f"authorization: Bearer {support.TOKEN}")
    lines.append("transfer-encoding: chunked" if length is None else f"content-length: {length}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def status_of(raw: bytes) -> int:
    return int(raw.split(b" ", 2)[1]) if raw.startswith(b"HTTP/1.1 ") else 0


def test_media_sec__a_refused_video_body_is_drained_so_the_client_reads_the_429():
    """The box cell in one request: a 3 MB video_b64 body, refused by the large-body gate
    before a byte is read. httpx reads the typed 429 (Retry-After, the JSON envelope) -
    not a ReadError - and the connection is still closed after it."""
    with served() as port, httpx.Client(timeout=10) as client:
        for _ in range(4):                  # the burst's shape: one refusal after another
            try:
                response = client.post(f"http://127.0.0.1:{port}{support.CHAT_PATH}",
                                       headers=support.RAW, content=BODY)
            except httpx.TransportError as lost:    # the box's ReadError is one of these
                raise AssertionError(f"the 429 never reached the client: {lost!r}") from None
            assert response.status_code == 429, response.text[:200]
            assert response.headers["retry-after"] == "2"
            assert response.headers["connection"] == "close"
            assert support.error_of(response)["code"] == "capacity_exhausted"


def test_media_sec__a_drained_refusal_still_closes_the_connection():
    """The drain is for the caller's benefit, not for reuse: after the whole declared body
    is read, the 429 still says `Connection: close` and the server closes the socket."""
    with served() as port:
        got, closed, _ = raw_exchange(port, head(len(BODY)), BODY)
    assert status_of(got) == 429, got[:200]
    assert b"\r\nconnection: close\r\n" in got.lower()
    assert b'"capacity_exhausted"' in got
    assert closed, "the server kept the connection after a drained refusal"


def test_media_sec__a_chunked_refusal_is_answered_and_closed_without_a_drain():
    """No declared end, no drain: an endless chunked body is the attack `CLOSE_CODES`
    exists for. The caller sends one chunk just over the large-body threshold (refused
    when the running total crosses it) and never the rest; it still reads the 429,
    promptly, and the close - a server that drained would wait for the rest instead."""
    size = 1_048_577
    chunk = b"%x\r\n" % size + b"x" * size     # its CRLF is never sent: nothing unread
    with served(intake_timeout_s=30) as port:
        got, closed, took = raw_exchange(port, head(None), chunk)
    assert status_of(got) == 429, got[:200]
    assert b'"capacity_exhausted"' in got
    assert closed and took < CLIENT_TIMEOUT_S, f"answered after {took:.1f}s: drained"


def test_media_sec__a_declared_length_over_the_cap_is_not_drained():
    """A declared length over MAX_REQUEST_BYTES would be read only to be refused: answered
    at once, not after the caller sends 96 MiB (or after the window runs out)."""
    with served(intake_timeout_s=30) as port:
        got, closed, took = raw_exchange(port, head(100_663_297), b"x" * 4096)
    assert status_of(got) == 429, got[:200]
    assert closed and took < CLIENT_TIMEOUT_S, f"answered after {took:.1f}s: drained"


def test_media_sec__a_drain_ends_with_the_intake_window():
    """A caller that declares 3 MB and stops sending is drained only until the intake
    window closes; then the 429 goes out and the socket closes."""
    with served(intake_timeout_s=1) as port:
        got, closed, took = raw_exchange(port, head(len(BODY)), BODY[:4096])
    assert status_of(got) == 429, got[:200]
    assert closed and 0.5 < took < CLIENT_TIMEOUT_S, f"answered after {took:.1f}s"


def test_media_sec__an_unauthenticated_caller_is_drained_only_up_to_1_mib():
    """A 401 drains a small body (the caller reads it) but never a video: an anonymous
    caller cannot make the gateway read 96 MiB. Over the bound it is answered at once."""
    small = b'{"messages": []}' + b" " * 500_000
    with served(intake_timeout_s=30) as port:
        got, closed, _ = raw_exchange(port, head(len(small), key=False), small)
        assert status_of(got) == 401 and closed, got[:200]
        got, closed, took = raw_exchange(port, head(len(BODY), key=False), BODY[:4096])
    assert status_of(got) == 401, got[:200]
    assert closed and took < CLIENT_TIMEOUT_S, f"answered after {took:.1f}s: drained"


# --- WR-I8-3: the gate and the drain on the gateway's /metrics ---------------------------
# The declarations `observe.metrics.FAMILIES` carries since the G7 merge (WR-4, verbatim);
# without them the intake records nothing rather than raise on the request path.
WIRED = {
    intake.SLOTS_IN_USE: metrics.Spec(
        "gauge", "Large request and upload bodies holding a slot now (LARGE_BODY_LIMIT)."),
    intake.SLOTS_LIMIT: metrics.Spec(
        "gauge", "The large-body slots configured (LARGE_BODY_LIMIT)."),
    intake.SLOTS_REFUSED: metrics.Spec(
        "counter", "Large bodies refused 429 because every slot was held."),
    intake.DRAINED: metrics.Spec(
        "counter", "Refused bodies read to their declared end so the caller reads the refusal.",
        (("code", intake.CLOSE_CODES),)),
}


def test_ops_alert__the_gateway_declares_the_large_body_families_as_the_intake_records_them():
    """G7 WR-4: the declared families are the intake's, name, kind, help and labels. Oracle:
    a drifted or missing declaration leaves the gate invisible on `/metrics`."""
    assert {name: metrics.FAMILIES.get(name) for name in WIRED} == WIRED


def sample(registry, name, **labels) -> float | None:
    """One sample's value from the exposition, or None when the series is absent."""
    wanted = ",".join([f'process="{registry.process}"',
                       *(f'{key}="{value}"' for key, value in labels.items())])
    for line in registry.render().splitlines():
        if line.startswith(f"{name}{{{wanted}}} "):
            return float(line.rsplit(" ", 1)[1])
    return None


def test_ops_alert__the_large_body_gauges_move_under_a_held_slot(monkeypatch):
    """WR-I8-3: while a large body holds the one slot, `/metrics` says so (in use 1 of 1);
    a second large body is refused 429 and counted, drained to its declared end and counted
    by code; when the first body ends, the slot is free again. No request id, key or
    tenant in any label."""
    for name, spec in WIRED.items():
        monkeypatch.setitem(metrics.FAMILIES, name, spec)
    registry = metrics.Registry("gateway")
    rt = support.runtime(support.settings())
    rt.metrics = registry
    calls, accept = support.recorder()
    app = FastAPI()
    app.state.runtime, rt.app = rt, app
    slots = intake.LargeBodies(limit=1, threshold=64)
    ingress.register(app, rt, support.deps(accept=accept, large_bodies=slots))
    assert sample(registry, intake.SLOTS_LIMIT) == 1
    body = json.dumps({"messages": [{"role": "user", "content": "x" * 200}]}).encode()
    head = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "path": support.CHAT_PATH, "raw_path": support.CHAT_PATH.encode(),
            "query_string": b"", "root_path": "", "scheme": "http",
            "client": ("198.51.100.7", 40000), "server": ("gw", 8001),
            "headers": [(b"authorization", f"Bearer {support.TOKEN}".encode()),
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode())]}

    async def scenario():
        rest = asyncio.Event()
        parts = [body[:100], body[100:]]

        async def slow():                     # the first chunk, then held until `rest`
            if len(parts) == 1:
                await rest.wait()
            return {"type": "http.request", "body": parts.pop(0), "more_body": bool(parts)}

        sent = []

        async def keep(message):
            sent.append(message)

        held = asyncio.ensure_future(app(head, slow, keep))
        for _ in range(50):
            await asyncio.sleep(0)
            if sample(registry, intake.SLOTS_IN_USE) == 1:
                break
        in_use = sample(registry, intake.SLOTS_IN_USE)
        whole = [{"type": "http.request", "body": body, "more_body": False}]

        async def at_once():
            return whole.pop(0) if whole else {"type": "http.disconnect"}

        refused = []
        await app(head, at_once, lambda m: asyncio.sleep(0, refused.append(m)))
        rest.set()
        await held
        return in_use, refused, sent

    in_use, refused, sent = asyncio.run(scenario())
    assert in_use == 1, "the held slot is not visible on /metrics"
    assert refused[0]["status"] == 429
    assert sample(registry, intake.SLOTS_REFUSED) == 1
    assert sample(registry, intake.DRAINED, code="capacity_exhausted") == 1
    assert sent[0]["status"] == 202 and calls                    # the held body went on
    assert sample(registry, intake.SLOTS_IN_USE) == 0
    assert "Bearer" not in registry.render() and support.ORG not in registry.render()


def test_ops_alert__without_the_declarations_the_intake_records_nothing(monkeypatch):
    """Before the wiring lands the families are undeclared: the gate still gates, the
    refusal is still typed, and nothing on the request path raises for want of a family."""
    for name in WIRED:
        monkeypatch.delitem(metrics.FAMILIES, name, raising=False)
    registry = metrics.Registry("gateway")
    slots = intake.LargeBodies(limit=1, threshold=64, registry=registry)
    slots.publish()
    first = slots.slot()
    first.account(100)
    with pytest.raises(errors.CapacityExhausted):
        slots.slot().account(100)
    first.release()
    assert (slots.in_flight, slots.refused) == (0, 1)
    assert "large_body" not in registry.render()

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
