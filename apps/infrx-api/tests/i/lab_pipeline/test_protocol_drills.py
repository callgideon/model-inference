#!/usr/bin/env python3
"""I6.c (LAB-WORKERS, PIPELINE-BUDGET) drills: P3's automatic-connector protocol server over
TCP (loopback, ephemeral ports), P3's real `submit`/`reconcile`/`poll` and `HttpConnector`
under the training unit's container environment (the egress deny of `test_egress.py`), P3's
fake world for the store and D8's ledger (lab-sql; rerun on real D8 at its merge). A worker
"restart" is a fresh client and connector over the same durable ledger.

Skips until P3 (codex/w5-pipelines) is on the base; the evidence records a run on a scratch
merge. Outside the mutant runner (the oracles are P3's own mutants and this lane's
test_egress/test_units mutants), as I5's PostgreSQL drills.

    uv run --frozen pytest -q tests/i/lab_pipeline/test_protocol_drills.py
"""
from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager

import httpx
import pytest

p3 = pytest.importorskip("infrx.pipelines.training", reason="P3 is not on this base yet")

import uvicorn  # noqa: E402

from ...n.imports.world import NEMO, run  # noqa: E402
from ...p.annotations.world import DEV  # noqa: E402
from ...p.training.test_training import EXT, ON, World  # noqa: E402
from ...p.training.world import protocol_app  # noqa: E402
from .test_egress import apply, container_env  # noqa: E402

BLOCKED = (httpx.ConnectError, httpx.ProxyError)


def free_port(host: str) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


@contextmanager
def serving(app, host: str, port: int):
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, f"the protocol server did not start on {host}:{port}"
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def worker(w: World, url: str, step: str = "submit"):
    """One worker process's pass: a fresh client (the container's env proxies) and connector
    over the same ledger."""
    async def go():
        async with httpx.AsyncClient(base_url=url, timeout=0.5) as client:
            connector = p3.HttpConnector(client, "protocol-test")
            if step == "poll":
                return await p3.poll(w.ledger, connector, provider_org_id=NEMO,
                                     external_run_id=EXT)
            return await p3.submit(w.store, w.objects, w.ledger, connector, w.access,
                                   provider_org_id=NEMO, user_id=DEV, external_run_id=EXT,
                                   advertised=ON)
    return run(go())


@pytest.fixture
def world():
    w = World()
    w.prepare(connector="protocol-test")
    return w


def test_i6_blocked_egress_is_an_ambiguous_submit_that_no_restart_resends(world,
                                                                          monkeypatch) -> None:
    """Failure oracle: a submit to an unapproved host that is sent, marked `failed` (its
    reservation released while the outcome is unknown) or `submitted`; a restart that
    resubmits instead of looking the key up; or an approval added later that turns the
    unknown run into a second job instead of leaving it visible for reconciliation."""
    port = free_port("127.0.0.2")
    app, url = protocol_app("ok"), f"http://127.0.0.2:{port}"
    with serving(app, "127.0.0.2", port):
        apply(container_env({"LAB_EGRESS_ALLOW": "127.0.0.1"}), monkeypatch)
        assert worker(world, url)["state"] == "ambiguous"
        assert world.reservation(EXT)["state"] == "held" and app.state.posts == 0
        with pytest.raises(BLOCKED):                  # restarted, still blocked: lookup only
            worker(world, url)
        apply(container_env({"LAB_EGRESS_ALLOW": "127.0.0.1,127.0.0.2"}), monkeypatch)
        again = worker(world, url)                    # reachable now: the key is unknown there
        assert again["state"] == "ambiguous" and "job_id" not in again
        assert app.state.posts == 0 and world.reservation(EXT)["state"] == "held"


def test_i6_a_server_that_accepted_then_went_down_converges_on_restart_to_one_job(
        world, monkeypatch) -> None:
    """Failure oracle: a 5xx after the server accepted treated as a refusal (released, then
    a second job), a restart while the server is down that resubmits or changes the run, or a
    poll during an outage that settles an estimate; the job completes and settles once at the
    provider-reported cost."""
    port = free_port("127.0.0.1")
    app, url = protocol_app("accept_503"), f"http://127.0.0.1:{port}"
    apply(container_env({"LAB_EGRESS_ALLOW": "127.0.0.1"}), monkeypatch)
    with serving(app, "127.0.0.1", port):
        assert worker(world, url)["state"] == "ambiguous"
    with pytest.raises(BLOCKED):                      # the protocol server is down
        worker(world, url)
    assert world.run_state(EXT)["state"] == "ambiguous"
    app.state.mode = "ok"
    with serving(app, "127.0.0.1", port):             # back up, the same jobs
        resumed = worker(world, url)
        assert (resumed["state"], resumed["job_id"]) == ("submitted", "job-1")
    with pytest.raises(BLOCKED):
        worker(world, url, "poll")
    assert world.reservation(EXT)["state"] == "held"
    app.state.jobs["job-1"].update(state="completed", cost="12.50000000")
    with serving(app, "127.0.0.1", port):
        assert worker(world, url, "poll")["state"] == "completed"
    assert (app.state.posts, len(app.state.jobs)) == (1, 1)
    assert world.reservation(EXT)["state"] == "settled"
    assert world.reservation(EXT)["cost"] == "12.50000000"
