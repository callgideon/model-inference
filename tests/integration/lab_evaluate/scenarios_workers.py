"""E6L j09 (bound, composition batch 2): the I5 eval worker PROCESS on this stack.

The real `python -m infrx.lab.workers eval` (the unit's command; E6L-O5: the same `lab_eval`
composition as `infrx.worker`'s) against this stack's Lab database and MinIO, its dev target
resolved from L3's own rows (the Marlin seed's private dev revision, its internal card) and
reached through the metered endpoint URL - here the `baseline` synthetic endpoint. A run is
frozen as the Lab route freezes it (D7 writes its `eval_run` event); the worker leases it, is
SIGKILLed mid-attempt, and a restarted worker (after the DB clock passed the lease and the
relay window) recovers the leases and finishes the run: every case scored once, the event
acknowledged once, the killed attempts `expired`; SIGTERM then exits 0.

j09's checkpoint half (bound, composition batch 5, WR-B3-3): the real `python -m
infrx.lab.workers checkpoints` with nothing injected - D8's checkpoint ledger (0042), the Lab
registry over this stack's MinIO (`lab://<provider>/...`) and L3's dev deployer over 0044's
reads, the checkpoint's digest pinned by a READY private dev revision registered in L3's rows.
A signed checkpoint is received once; the worker decides it (one queued D7 run, served by that
revision, one `eval_run` event, the receipt `evaluated`); the event is then released and
delivered again (a relay redelivery) and the second delivery changes nothing; the queued
run's `eval_run` event is never claimed by this role (R215, its own kinds only).
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
def worker(env: dict[str, str], log, role: str = "eval"):
    process = subprocess.Popen([sys.executable, "-m", "infrx.lab.workers", role],
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


def settled(lab) -> bool:
    """No other backend on the Lab database is running a statement or holding a transaction."""
    return lab.sql("select count(*) from pg_stat_activity where datname = current_database() "
                   "and backend_type = 'client backend' and pid <> pg_backend_pid() "
                   "and state <> 'idle'")[0][0] == 0


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
        # A statement the killed worker sent just before SIGKILL still commits (a case it had
        # answered finishes `succeeded`): count the leases once its backends are gone.
        until(lambda: settled(lab), 30, "the killed worker's in-flight statements")
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


def l3_checkpoint_revision(lab, n: int, digest: str) -> str:
    """L3's rows for a checkpoint: a serving revision of the seed's model pinning `digest` as
    its weights and its READY private dev revision on the seed's dev endpoint (A3's registry,
    as L3's register/create_dev/validate leave them). Answers L3's serving ref."""
    from infrx.contracts.v2 import fixtures as v2fix
    from infrx.lab.control.operations import serving_ref
    from infrx.state.jobstore import connector
    from infrx.state.operations import PgRegistry
    registry = PgRegistry(connector(lab.dsn))
    serving = v2fix.model("serving_revision.json").model_copy(update={
        "serving_version_id": lw.uid(n, 0xd5), "model_version_id": lw.uid(n, 0xd6),
        "weight_shard_digests": (digest,), "revision_label": f"ckpt-{n}"})
    dev = v2fix.model("deployment_revision_private_dev.json").model_copy(update={
        "deployment_revision_id": lw.uid(n, 0xde),
        "serving_version_id": serving.serving_version_id})
    run(registry.put(serving))
    run(registry.put(dev))
    return serving_ref(dev, serving)


def test_j09_the_checkpoint_worker_drains_the_outbox_once(lab, workdir):
    import hashlib
    import json
    import secrets

    from infrx.contracts.lab import records
    from infrx.evaluation import checkpoints
    from infrx.state.jobstore import connector
    from infrx.state.lab_pipeline import PgCheckpointLedger
    from tests.b.checkpoints.world import safetensors
    n, provider = 92, lab.NEMO
    ledger = PgCheckpointLedger(connector(lab.dsn))
    dataset = lab.dataset("text", 1).dataset_ref
    external_id = lw.uid(n, 0xe3e)
    external = lab.publish({
        "schema": "lab.external_run.1", "provider_org_id": provider,
        "external_run_id": external_id, "purpose": "training", "connector": "manual-bundle",
        "dataset_ref": dataset, "submit_key": records.submit_key(external_id),
        "state": "prepared",
        "budget": {"limit": {"unit": "PROVIDER_USD", "value": "40.00000000"},
                   "reserved": {"unit": "PROVIDER_USD", "value": "0.00000000"},
                   "payer_ref": f"lab:payer:{provider}:{lw.uid(n, 0xfa6)}@sha256:" + "0" * 64}})
    sub = run(checkpoints.subscribe(ledger, lab.store, {
        "subscription_id": lw.uid(n, 0x5b6), "provider_org_id": provider,
        "external_run_ref": external, "dataset_ref": dataset,
        "harness_ref": lab.harness_ref(1), "evaluator_ref": lw.EVALUATOR(provider),
        "evaluator": lw.SPEC, "seed": 7, "max_cases": 20,
        "run_limit": {"unit": "CREDIT", "value": "100.00000000"},
        "limit": {"unit": "CREDIT", "value": "1000.00000000"}, "max_active": 5,
        "policy": "every"}, access=lab.access, user_id=lab.DEV))
    data = safetensors(n)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    run(lab.objects.put_if_absent(f"lab/{provider}/checkpoints/j09/1.safetensors", data,
                                  "application/octet-stream"))
    expected = l3_checkpoint_revision(lab, n, digest)
    secret, checkpoint = secrets.token_bytes(32), lw.uid(n, 0xc3e)
    body = json.dumps({
        "schema": checkpoints.EVENT_SCHEMA, "provider_org_id": provider, "key_id": "j09",
        "checkpoint_id": checkpoint, "external_run_ref": external, "step": 1,
        "artifact": {"uri": f"lab://{provider}/checkpoints/j09/1.safetensors",
                     "digest": digest},
        "issued_at": lab.sql("select infrx.now()")[0][0].strftime("%Y-%m-%dT%H:%M:%SZ")}).encode()
    receipt = run(checkpoints.receive(body, checkpoints.sign(body, secret),
                                      keys={"j09": (provider, secret)}.get, ledger=ledger,
                                      store=lab.store))
    assert receipt["state"] == "received"
    run_id = checkpoints.run_id_of(sub.subscription_id, checkpoint)

    def acknowledged() -> bool:
        return lab.sql("select acknowledged_at is not null from infrx.lab_outbox where kind = "
                       "'checkpoint_received' and payload->>'checkpoint_id' = %s",
                       checkpoint) == [(True,)]

    def counts() -> tuple:
        return tuple(lab.sql(sql, key)[0][0] for sql, key in (
            ("select count(*) from infrx.lab_checkpoint_receipts where checkpoint_id = %s",
             checkpoint),
            ("select count(*) from infrx.lab_outbox where kind = 'checkpoint_received' "
             "and payload->>'checkpoint_id' = %s", checkpoint),
            ("select count(*) from infrx.lab_checkpoint_decisions where checkpoint_id = %s",
             checkpoint),
            ("select count(*) from infrx.lab_eval_runs where run_id = %s", run_id),
            ("select count(*) from infrx.lab_outbox where kind = 'eval_run' "
             "and payload->>'run_id' = %s", run_id)))

    env = {**worker_env(lab, "http://127.0.0.1:9", 0)}
    for name in ("LAB_EVAL_ENDPOINT_URL", "LAB_EVAL_ENDPOINT_KEY"):
        env.pop(name)
    with open(workdir / "checkpoints-worker.log", "wb") as log:
        health = free_health_port()
        with worker({**env, "LAB_WORKER_HEALTH_PORT": str(health)}, log,
                    "checkpoints") as process:
            until(lambda: ready(health), 60, "the checkpoints worker's /readyz")
            until(acknowledged, 60, "the checkpoint_received event decided and acknowledged")
            first = counts()
            lab.sql("update infrx.lab_outbox set acknowledged_at = null, claimed_at = null, "
                    "claimed_by = null where kind = 'checkpoint_received' and "
                    "payload->>'checkpoint_id' = %s", checkpoint)      # released: redelivered
            until(acknowledged, 60, "the redelivered event acknowledged again")
            process.send_signal(signal.SIGTERM)
            assert process.wait(60) == 0, "SIGTERM is a clean exit"
    assert first == counts() == (1, 1, 1, 1, 1), (first, counts())
    assert lab.sql("select claimed_by from infrx.lab_outbox where kind = 'eval_run' and "
                   "payload->>'run_id' = %s", run_id) == [(None,)], \
        "R215: the checkpoints role never claims the eval role's event"
    assert lab.sql("select state, run_id::text from infrx.lab_checkpoint_decisions where "
                   "checkpoint_id = %s", checkpoint) == [("queued", run_id)]
    assert lab.sql("select state from infrx.lab_checkpoint_receipts where checkpoint_id = %s",
                   checkpoint) == [("evaluated",)]
    status = run(lab.store.run_status(run_id, provider_org_id=provider))
    assert run(lab.store.resolve(status["run_ref"], provider_org_id=provider)).serving_ref \
        == expected, "the run is served by L3's dev revision of the checkpoint's digest"
    lw.save(workdir, "checkpoint-worker.json", {"checkpoint_id": checkpoint, "run_id": run_id,
                                                "serving_ref": expected, "counts": first,
                                                "status": status})
