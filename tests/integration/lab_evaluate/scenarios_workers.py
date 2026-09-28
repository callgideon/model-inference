"""E6L j09 (bound, composition batch 2): the I5 eval worker PROCESS on this stack.

The real `python -m infrx.lab.workers eval` (the unit's command; E6L-O5: the same `lab_eval`
composition as `infrx.worker`'s) against this stack's Lab database and MinIO, its dev target
resolved from L3's own rows (the Marlin seed's private dev revision, its internal card) and
reached through the metered endpoint URL - here the `baseline` synthetic endpoint. A run is
frozen as the Lab route freezes it (D7 writes its `eval_run` event); the worker leases it, is
SIGKILLed mid-attempt, and a restarted worker (after the DB clock passed the lease and the
relay window) recovers the leases and finishes the run: every case scored once, the event
acknowledged once, the killed attempts `expired`; SIGTERM then exits 0.

j09's checkpoint half stays NOT RUN (`scenarios_pending.py`): the checkpoints role refuses
until L3's dev deployer and a registry adapter exist (WR-B3-3).
"""
from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import httpx
import lab_world as lw
from lab_world import run

#: The worker's loopback health port: a free one of the e6l block's tail, clear of E2's
#: services (valkey 57279, fake vLLM 57280, ClickHouse native 57290).
HEALTH_PORTS = tuple(range(57291, 57300))


def free_health_port() -> int:
    for port in HEALTH_PORTS:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise OSError("no free health port in 57291-57299")


def dev_serving_ref(lab) -> str:
    """L3's serving ref of the seed's private dev revision, from L3's own rows."""
    from infrx.lab.control.operations import serving_ref
    from infrx.state.catalog import PgCatalogDirectory
    from infrx.state.jobstore import connector
    from infrx.state.lab_control import PgControlStore
    from tests.d import checks_credit as cc
    deployment = run(PgControlStore(connector(lab.dsn)).deployment(cc.DEV_DEPLOYMENT))
    serving = run(PgCatalogDirectory(connector(lab.dsn)).serving_revision(
        deployment.serving_version_id))
    return serving_ref(deployment, serving)


def worker_env(lab, endpoint_url: str, health: int) -> dict[str, str]:
    """The role's env file, as I5's unit hands it over: nothing of the gateway's."""
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("AWS_", "LAB_", "DATABASE_", "S3_", "INFRX_"))}
    return {**base, **lw.stack.s3_env(), "LAB_DATABASE_URL": lab.dsn,
            "LAB_S3_BUCKET": lw.harness.S3_BUCKET, "LAB_S3_ENDPOINT": lw.harness.s3_endpoint(),
            "LAB_S3_PREFIX": lab.prefix, "LAB_EVAL_ENDPOINT_URL": endpoint_url,
            "LAB_EVAL_ENDPOINT_KEY": lw.KEY, "LAB_WORKER_HEALTH_PORT": str(health)}


@contextlib.contextmanager
def worker(env: dict[str, str], log):
    process = subprocess.Popen([sys.executable, "-m", "infrx.lab.workers", "eval"],
                               cwd=lw.API, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(30)


def until(predicate, timeout_s: float, what: str, *, every=None):
    """Poll `predicate` until true; `every()` runs each ~5 s of waiting (a clock step)."""
    deadline, last = time.monotonic() + timeout_s, time.monotonic()
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        if every and time.monotonic() - last > 5:
            every()
            last = time.monotonic()
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


def ready(health: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{health}/readyz", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def test_j09_the_eval_worker_process_killed_mid_run_loses_nothing(lab, workdir):
    try:
        run(lab.store.put_evaluator(lw.SPEC, provider_org_id=lab.NEMO,
                                    evaluator_id=lw.EVALUATOR_ID, actor="dev@nemo"))
    except Exception as held:                    # noqa: BLE001 - write-once: already there
        assert "conflict" in type(held).__name__.lower() or "exists" in str(held), held
    serving = dev_serving_ref(lab)
    frozen = lab.freeze(lab.run_payload(91, lab.dataset("text", 1).dataset_ref,
                                        lab.harness_ref(1), serving, max_cases=6))
    run_id, provider = frozen.run.run_id, lab.NEMO

    def event_pending() -> int:
        return lab.sql("select count(*) from infrx.lab_outbox where kind = 'eval_run' and "
                       "payload->>'run_id' = %s and acknowledged_at is null", run_id)[0][0]

    def cases(state: str) -> int:
        return lab.sql("select count(*) from infrx.lab_eval_cases where run_id = %s and "
                       "state = %s", run_id, state)[0][0]

    assert event_pending() == 1, "freeze wrote the run's eval_run event"
    gate, answered = threading.Event(), threading.Event()
    with lw.endpoint("baseline") as (wallet, http), \
            open(workdir / "worker.log", "wb") as log:
        model = wallet.answer

        def held(prompt):
            """The first call answers; the next ones are held mid-attempt (the kill)."""
            if answered.is_set():
                gate.wait(60)
            answered.set()
            return model(prompt)
        wallet.answer = held
        url = http._url.removesuffix("/v1/chat/completions")
        health = free_health_port()
        with worker(worker_env(lab, url, health), log) as first:
            until(lambda: ready(health), 60, "the worker's /readyz")
            until(lambda: len(wallet.calls) >= 2, 90, "a second model call in flight")
            first.kill()                                      # SIGKILL mid-attempt
            assert first.wait(30) == -signal.SIGKILL
        gate.set()
        leased = cases("leased")
        assert leased >= 1 and event_pending() == 1, (leased, event_pending())
        lab.advance(31)                     # past the 30 s leases and the relay's window
        health = free_health_port()
        with worker(worker_env(lab, url, health), log) as second:
            until(lambda: ready(health), 60, "the restarted worker's /readyz")
            until(lambda: event_pending() == 0, 180, "the run finished and acknowledged",
                  every=lambda: lab.advance(31))
            second.send_signal(signal.SIGTERM)
            assert second.wait(60) == 0, "SIGTERM is a clean exit"
    status = run(lab.store.run_status(run_id, provider_org_id=provider))
    results = lab.results(run_id)
    attempts = lab.sql("select case_id::text, attempt, state from infrx.lab_eval_attempts "
                       "where run_id = %s order by case_id, attempt", run_id)
    expired = [a for a in attempts if a[2] == "expired"]
    assert status["state"] == "succeeded", status
    assert sorted(results) == sorted(frozen.cases), "every case scored, once each"
    assert cases("done") == len(frozen.cases) and cases("leased") == 0
    assert len(expired) >= 1 and len(expired) == leased, (attempts, leased)
    assert lab.sql("select count(*) from infrx.lab_outbox where kind = 'eval_run' and "
                   "payload->>'run_id' = %s", run_id)[0][0] == 1
    lw.save(workdir, "worker.json", {"run_id": run_id, "serving_ref": serving,
                                     "status": status, "attempts": attempts,
                                     "wallet_calls": len(wallet.calls), "leased_at_kill": leased})
