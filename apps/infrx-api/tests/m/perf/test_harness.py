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


def test_a_parity_or_manifest_disagreement_fails_the_run():
    """Review P4: `measure` exits 1 on any row `disagrees` flags, so exit 0 is a verdict."""
    good = {"parity": {"differs": [], "manifest": {"sha256": True, "budget_equal": True,
                                                   "duration_delta_s": 0.0004}}}
    assert not harness.disagrees(good) and not harness.disagrees({"outcome": "unsupported"})
    for broken in ({"differs": ["fs"]}, {"manifest": {"sha256": False}},
                   {"manifest": {"budget_equal": False}}, {"manifest": {"duration_delta_s": -0.002}}):
        row = {"parity": {**good["parity"], **broken}}
        if "manifest" in broken:
            row["parity"]["manifest"] = {**good["parity"]["manifest"], **broken["manifest"]}
        assert harness.disagrees(row), broken
