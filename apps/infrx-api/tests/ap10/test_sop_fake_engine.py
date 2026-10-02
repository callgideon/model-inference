#!/usr/bin/env python3
"""AP-10 10d against a REAL engine process: `tests/integration/fake_vllm.py` on a free loopback
port, driven through the CLI (`python -m infrx.lab.improve.sop`). Outside the mutant runner
(a process per run), as the `_pg` files are.

    uv run --frozen pytest -q tests/ap10/test_sop_fake_engine.py
"""
from __future__ import annotations

import json
import pathlib
import socket
import sys

from infrx.lab.improve import sop

INTEGRATION = pathlib.Path(__file__).resolve().parents[4] / "tests/integration"
sys.path.insert(0, str(INTEGRATION))
import fake_vllm  # noqa: E402

FAKE_MODEL = "infrx-e2/fake-vllm"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_ap10_sop_the_fake_engine_run_is_labelled_fake_and_quality_blocked(tmp_path):
    """Oracle: against the fake engine, even declared `candidate`, performance is `fake`
    (never `meas.`) and quality BLOCKED[P-07]; the fake's answer has no timed event, so every
    sent item is an `insufficient_evidence` abstention, listed - not a pass."""
    (tmp_path / "clip.mp4").write_bytes(b"\x00fixture")
    (tmp_path / "items.jsonl").write_text("".join(json.dumps(
        {"id": f"i{k}", "video": "clip.mp4", "prompt": "caption", "max_tokens": 32,
         "start_s": 0, "end_s": 8}) + "\n" for k in range(3)))
    out = tmp_path / "report.json"
    with fake_vllm.FakeVllmServer(free_port()) as engine:
        assert sop.main(["--manifest", str(tmp_path / "items.jsonl"), "--dataset-version",
                         "fixture-1", "--base-url", engine.base_url + "/v1", "--model",
                         FAKE_MODEL, "--serving-revision", "fake-vllm", "--seed", "7",
                         "--target", "candidate", "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["performance"]["label"] == "fake"
    assert report["quality"]["verdict"] == "BLOCKED" and "P-07" in report["quality"]["blocked"]
    assert report["model"]["listed"] == [FAKE_MODEL]
    assert report["failures"] == []
    assert [a["reason"] for a in report["abstentions"]] == ["insufficient_evidence"] * 3
