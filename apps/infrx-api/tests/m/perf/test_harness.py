"""The M4 harness runs end to end on one clip without a socket (MockTransport inside the
same `Loopback` wrapper the real run uses). It proves the tool works, not a product
invariant: those are in `tests/m/test_parity.py` and the M suites."""
from __future__ import annotations

import asyncio

import httpx

from .. import support
from . import harness


def test_the_harness_measures_one_clip_end_to_end(tmp_path):
    body = harness.padded_mp4(256 * 1024)
    clip = tmp_path / "c.mp4"
    clip.write_bytes(body)
    transport = harness.Loopback(port=1)
    transport.inner = httpx.MockTransport(lambda request: support.response(body=body))
    row = asyncio.run(harness.measure_clip(
        {"id": "c", "set": "test", "file": "c.mp4", "expect": None}, clip, transport, 2, tmp_path))
    assert row["outcome"] == "ok" and row["bytes_read"] == len(body)
    assert row["layout"] == "ftyp,moov,mdat"
    assert row["parity"]["identical"] == ["after-expiry", "fs", "inline", "memory-1", "memory-2"]
    assert row["parity"]["v1_identity"] is True
    assert row["prepare_warm_ms"]["n"] == 2 and set(row["peak_mib"]) == {
        "materialize_url", "materialize_inline", "prepare_cold"}
