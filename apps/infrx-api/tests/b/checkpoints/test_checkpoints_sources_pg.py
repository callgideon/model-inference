#!/usr/bin/env python3
"""WR-B3-3 / WR-C4-B3-SUITES on real PostgreSQL: the production sources, nothing injected.

* The checkpoints role (`python -m infrx.lab.workers checkpoints`, composed from its env with
  the Lab objects only) decides a signed checkpoint whose artifact is `lab://<provider>/...`
  (the Lab registry) and whose digest a READY private dev revision of the provider pins (L3's
  rows, 0007 + 0044's reads): one queued D7 run, its serving ref L3's own.
* `LAB_PIPELINES`' P3 evaluation port (`pilot._lab`, the production suites on the gateway's
  pool): a checkpoint's D7 receipt -> D8's subscription of its run -> L3's dev revision of its
  digest -> ONE B1 run (a repeat is the same); a digest no revision serves is a typed 503.

World: `test_checkpoints_pg.py`'s (d7's seed, which carries the Marlin operator seed). Runs on
the b3 key, or on p3's PostgreSQL when a foreign `infrx-b3-postgres` holds b3.

    INFRX_D_TASK=p3 uv run --frozen pytest -q tests/b/checkpoints/test_checkpoints_sources_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

import asyncio
import dataclasses
import os

import pytest
from infrx.contracts import errors
from infrx.contracts.v2 import fixtures as v2fix
from infrx.contracts.v2.records import DeploymentState
from infrx.evaluation import checkpoints
from infrx.lab.access import LabAccess
from infrx.lab.control.operations import serving_ref
from infrx.lab.workers import __main__ as lab_workers
from infrx.media.store import InMemoryObjectStore
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.operations import PgRegistry

from ...d import pgharness
from ...d import test_l2sql_access as l2
from ..runner.world import uid
from .test_checkpoints_pg import DB, event, on_pg, receive, rows, run, world  # noqa: F401
from .world import NEMO, safetensors, sha

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") in ("b3", "p3") else \
    "PostgreSQL only on the b3 (or, b3 held, p3) task-local key"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")


def l3_revision(dsn: str, n: int, digest: str, state=DeploymentState.ready_private) -> str:
    """A serving revision of the seed's model pinning `digest`, and its private dev revision
    on the seed's dev endpoint in `state` (A3's registry, as L3's register/create_dev write
    them; validate's transition is the state here). Answers L3's serving ref."""
    registry = PgRegistry(connector(dsn))
    serving = v2fix.model("serving_revision.json").model_copy(update={
        "serving_version_id": uid(n, 0xd5), "model_version_id": uid(n, 0xd6),
        "weight_shard_digests": (digest,), "revision_label": f"ckpt-{n}"})
    dev = v2fix.model("deployment_revision_private_dev.json").model_copy(update={
        "deployment_revision_id": uid(n, 0xde), "serving_version_id": serving.serving_version_id,
        "state": state})
    run(registry.put(serving))
    run(registry.put(dev))
    return serving_ref(dev, serving)


def test_b3_pg_the_checkpoints_role_decides_with_the_lab_registry_and_l3s_dev_revision(
        world, monkeypatch):
    w = on_pg(world, 5)
    dsn = pgharness.dsn(DB)
    access = LabAccess(PgAccessStore(connector(dsn)))
    run(checkpoints.subscribe(w.ledger, w.store, w.subscription(1), access=access,
                              user_id=l2.DEV))
    data, objects = safetensors(501), InMemoryObjectStore()
    run(objects.put_if_absent(f"lab/{NEMO}/checkpoints/e5/1.safetensors", data, "x"))
    expected = l3_revision(dsn, 5, sha(data))
    e = event(w, 1, data=data, uri=f"lab://{NEMO}/checkpoints/e5/1.safetensors")
    receive(w, e)
    steps = {}

    async def never():
        await asyncio.Event().wait()
    monkeypatch.setattr(lab_workers, "every", lambda interval_s, step, what: (
        steps.__setitem__(what, step), never())[1])
    worker = lab_workers.compose("checkpoints", {"LAB_DATABASE_URL": dsn,
                                                 "LAB_WORKER_HEALTH_PORT": "9",
                                                 "LAB_S3_BUCKET": "unused"}, objects=objects)
    worker.tasks["lab_checkpoints"]().close()
    pump = steps["lab checkpoints"]
    run(pump())                          # R215: never claims the eval_run event (0-F3)
    claimed = rows(w, "select claimed_by from infrx.lab_outbox where kind = 'eval_run'")
    assert claimed and set(claimed) == {(None,)}, claimed
    (decided,) = rows(w, "select state, run_id::text from infrx.lab_checkpoint_decisions "
                         "where checkpoint_id = %s", e["checkpoint_id"])
    assert decided[0] == "queued", decided
    status = run(w.store.run_status(decided[1], provider_org_id=NEMO))
    assert run(w.store.resolve(status["run_ref"], provider_org_id=NEMO)).serving_ref == expected
    assert rows(w, "select state from infrx.lab_checkpoint_receipts where checkpoint_id = %s",
                e["checkpoint_id"]) == [("evaluated",)]


def test_b3_pg_p3s_evaluation_port_freezes_one_run_on_the_production_suite(world):
    from infrx.gateway import pilot
    from tests.g import support
    w = on_pg(world, 6)
    dsn = pgharness.dsn(DB)
    access = LabAccess(PgAccessStore(connector(dsn)))
    sub = run(checkpoints.subscribe(w.ledger, w.store, w.subscription(1), access=access,
                                    user_id=l2.DEV))
    digest, cid, bare = sha(safetensors(601)), uid(601, 0xc3), uid(602, 0xc3)
    expected = l3_revision(dsn, 6, digest)
    for checkpoint, pinned in ((cid, digest), (bare, sha(b"no revision serves this"))):
        run(w.store.receive_checkpoint(provider_org_id=NEMO, checkpoint_id=checkpoint,
                                       external_run_ref=w.external, artifact_digest=pinned))
    settings = support.settings(deployment=dataclasses.replace(support.BUILD,
                                                               lab_pipelines=True))
    objects = InMemoryObjectStore()
    evals = pilot._lab(settings, connector(dsn), objects)["lab_pipelines"].evals
    ask = {"provider_org_id": NEMO, "dataset_ref": w.dataset, "split": "holdout",
           "holdout_sha256": "0" * 64}
    ref = run(evals.evaluate(checkpoint_id=cid, **ask))
    record = run(w.store.resolve(ref, provider_org_id=NEMO))
    assert record.serving_ref == expected and record.harness_ref == sub.harness_ref
    assert record.run_id == checkpoints.run_id_of(sub.subscription_id, f"{cid}:{w.dataset}")
    assert run(evals.evaluate(checkpoint_id=cid, **ask)) == ref
    assert rows(w, "select count(*) from infrx.lab_eval_runs where run_id = %s",
                record.run_id) == [(1,)]
    with pytest.raises(errors.DependencyUnavailable):
        run(evals.evaluate(checkpoint_id=bare, **ask))
    with pytest.raises(errors.NotFound):
        run(evals.evaluate(checkpoint_id=cid, **{**ask, "provider_org_id": l2.OTHER}))
