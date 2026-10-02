"""LAB-HOSTING's scenarios (AP-05; run by `runner.py`, never collected by `pytest
tests/integration`): the private-deployment lifecycle through the routes, with the hosting
controller as a REAL long-running process (`tests/ap05/controller_proc.py`), the candidate
engine a real process (`candidate_engine.py` = `fake_vllm.py`'s app on ap5's 57557) and
ap5's PostgreSQL (every migration through 0062). The world is tests/ap05's `pg_world`.

Label: isolated - no GPU, the fake engine's text, the engine's image declared (no
container), no gateway private admission path (the gateway has one UPSTREAM; AP-06 /
coordinator wiring). A real candidate on the pilot L40S is a coordinator window
(infra/lab/hosting/README.md).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent
API = HERE.parents[2] / "apps" / "infrx-api"
sys.path[:0] = [p for p in (str(API),) if p not in sys.path]

from tests.ap05.conftest import (ENGINE_PORT, ON_AP5, pg_template, pg_world,  # noqa: E402,F401
                                 run)

PROC = API / "tests" / "ap05" / "controller_proc.py"
DEPLOYMENTS = "/lab/v1/control/deployments"
pytestmark = pytest.mark.skipif(not ON_AP5, reason="BLOCKED[stack] INFRX_D_TASK=ap5 is not set")


def controller(w, owner: str, boundary: str = "-", image: str | None = None):
    """A controller process over the world's database and directories."""
    return subprocess.Popen((sys.executable, str(PROC), w.service_dsn, w.dsn, str(w.tmp), owner,
                             boundary, *((image,) if image else ())),
                            env={**os.environ, "INFRX_AP5_PASSES": "1500"},
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True)


def stop(process) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(30)


def settled(w, operation: str, timeout_s: float = 120.0):
    """The operation once it is finished (the controller process works it), or AssertionError."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        doc = w.op(operation)
        if doc.state in ("succeeded", "failed", "cancelled"):
            return doc
        time.sleep(0.25)
    raise AssertionError(f"operation {operation} did not finish in {timeout_s}s: {doc}")


def engine_answers() -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{ENGINE_PORT}/v1/models", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def test_g01_the_private_lifecycle_through_the_api_and_a_controller_process(pg_world):
    w = pg_world
    worker = controller(w, "gate-controller")
    try:
        operation, deployment = w.deployed()
        assert settled(w, operation).state == "succeeded"
        identity = w.readiness(deployment)["identity"]
        assert identity["passed"] and identity["observed"]["served_models"] == ["marlin2b"]
        assert engine_answers()
        smoke = w.call("POST", f"{DEPLOYMENTS}/{deployment}/smoke", key=w.key()).json()
        assert settled(w, smoke["operation_id"]).state == "succeeded"
        end = time.monotonic() + 30
        while not w.readiness(deployment)["ready"] and time.monotonic() < end:
            time.sleep(0.25)                                 # its first health observation
        status = w.readiness(deployment)
        assert status["ready"], status["reasons"]
        assert status["smoke"]["observed"]["prompt_tokens"] >= 256
        retire = w.call("POST", f"{DEPLOYMENTS}/{deployment}/retire", key=w.key()).json()
        assert settled(w, retire["operation_id"]).state == "succeeded"
        assert w.state(deployment) == "retired" and not engine_answers()
        assert w.detail(deployment)["allocation"]["state"] == "released"
    finally:
        stop(worker)


def test_g02_one_slot_one_candidate_never_an_eviction(pg_world):
    w = pg_world
    worker = controller(w, "gate-controller")
    try:
        first_op, first = w.deployed()
        assert settled(w, first_op).state == "succeeded"
        second_op, second = w.deployed()
        refused = settled(w, second_op)
        assert refused.state == "failed" and refused.error.code == "capacity_unavailable"
        assert w.state(first) == "validating" and engine_answers()
        assert w.state(second) == "retired"
    finally:
        stop(worker)


def test_g03_a_killed_controller_process_is_replaced_and_its_work_finishes_once(pg_world):
    w = pg_world
    operation, deployment = w.deployed()
    doomed = controller(w, "doomed", boundary="launched")
    doomed.wait(240)
    assert doomed.returncode == -signal.SIGKILL
    state = w.launcher.inner.state_dir / f"infrx-hosting-{deployment}.json"
    engine = json.loads(state.read_text())["pid"]            # started, then its starter died
    w.advance(120)
    worker = controller(w, "replacement")
    try:
        assert settled(w, operation).state == "succeeded"
        assert list(w.launcher.running) == [f"infrx-hosting-{deployment}"] and engine_answers()
        assert json.loads(state.read_text())["pid"] == engine   # adopted, never a second one
    finally:
        stop(worker)


def test_g04_a_wrong_runtime_never_becomes_ready(pg_world):
    w = pg_world
    worker = controller(w, "gate-controller", image="sha256:" + "0" * 64)
    try:
        operation, deployment = w.deployed()
        failed = settled(w, operation)
        assert failed.state == "failed" and failed.error.code == "identity_mismatch"
        assert [r["field"] for r in w.readiness(deployment)["identity"]["reasons"]] == \
            ["runtime_image"]
        assert w.state(deployment) == "retired" and not engine_answers()
    finally:
        stop(worker)
