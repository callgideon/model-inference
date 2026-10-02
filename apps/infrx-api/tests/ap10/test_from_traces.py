#!/usr/bin/env python3
"""AP-10 10c (API-EVAL, DATA-RIGHTS): `infrx.lab.datasets.from_traces` - permitted traces
become an immutable dataset version with a recorded holdout through a ControlOps operation;
the AP-07 grant is checked at selection AND at materialisation, and a revocation, an expiry
or a new grant version between the two refuses with nothing published. N3's fake world
(`tests/n/lineage/world.py`: the real L2 over FakeAccessStore, T3, C2 stand-in, D7 in memory)
and 0060's `FakeControlOps`; the real half is `test_from_traces_pg.py` (ap10's PostgreSQL).

    uv run --frozen pytest -q tests/ap10/test_from_traces.py
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from infrx.contracts import api, errors
from infrx.contracts.v2 import records as v2
from infrx.datasets import versions
from infrx.lab.datasets import from_traces as ft
from infrx.state.control_ops import FakeControlOps
from infrx.state.lab_data import grant_ref

from ..n.imports.world import NEMO
from ..n.lineage.world import CONSUMER, DEV, GRANTOR, MODEL, VIEWER, World

DATASET = "da000000-0000-4000-8000-0000000000a1"


def run(coro):
    return asyncio.run(coro)


def actor(user: str = DEV) -> api.Actor:
    return api.Actor(audience="session", user_id=user, provider_org_id=NEMO, role="developer")


def body(requests, **kw) -> ft.TraceDataset:
    return ft.TraceDataset.model_validate({
        "grantor_org_id": GRANTOR, "model_id": MODEL, "request_ids": list(requests),
        "dataset_id": DATASET, "version": 1, "purpose": "training", "seed": 3,
        "train_bp": 4000, "validation_bp": 2000, **kw})


class Rig:
    def __init__(self) -> None:
        self.w = w = World()
        self.ops = FakeControlOps(now=lambda: w.now)
        self.ports = ft.Ports(access=w.access, retention=w.retention,
                              content=lambda user, grant: w.content, feedback=w.feedback,
                              model_of=w.model_of, store=w.lab, objects=w.objects)

    def start(self, requests, key="k-1", user=DEV, **kw):
        return run(ft.start(self.ops, self.w.access, self.w.objects, actor(user), key,
                            body(requests, **kw)))

    def work(self, **kw):
        return run(ft.work(self.ops, self.ports, worker_id="w1", **kw))

    def op(self, started):
        return run(self.ops.get(started.operation.operation_id, actor()))

    def outcome(self, started) -> dict[str, Any] | None:
        return run(ft.outcome(self.w.objects, provider_org_id=NEMO,
                              selection_id=started.operation.resource_id))

    def done(self, started) -> dict[str, Any]:
        got = self.outcome(started)
        assert got is not None, "no outcome"
        return got

    def resolve(self, ref: str) -> Any:
        return run(self.w.lab.resolve(ref, provider_org_id=NEMO))


def test_ap10_selection_then_materialisation_is_an_immutable_version_with_its_holdout():
    """Oracle: start records the selection under the CURRENT grant version and queues one
    operation (a replay is the same operation); the worker's pass publishes the selection
    (`@1`, every sample in train) and its split (`@2`, a child of `@1`) whose manifest holds
    the holdout the outcome records; another pass does nothing. A missing holdout, a split
    not derived from the selection, or a second operation for one key fails."""
    r = Rig()
    requests = [r.w.trace(n) for n in range(1, 9)]
    started = r.start(requests)
    assert (started.replayed, started.operation.state, started.operation.kind) == \
        (False, "queued", "dataset.from_traces")
    again = r.start(requests)
    assert again.replayed and again.operation.operation_id == started.operation.operation_id
    sel = json.loads(run(r.w.objects.get(
        f"lab/{NEMO}/lineage/selections/{started.operation.resource_id}.json")) or b"null")
    grant = r.w.directory.grants[(GRANTOR, NEMO)]
    assert (sel["grant_id"], sel["grant_version"], sel["user_id"]) == \
        (grant.grant_id, grant.version, DEV)
    assert r.w.lab.published == []                     # selection publishes nothing
    assert r.work() == {"succeeded": 1, "failed": 0, "cancelled": 0, "retry": 0}
    assert r.op(started).state == "succeeded"
    got = r.done(started)
    selected, split = r.resolve(got["selected_ref"]), r.resolve(got["dataset_ref"])
    assert (selected.version, split.version, split.parent_refs) == (1, 2, [got["selected_ref"]])
    assert len(selected.samples) == len(split.samples) == 8
    assert got["holdout"] == list(split.splits.holdout) and got["holdout"], got
    assert got["split_digest"] == versions.split_digest(
        {n: list(getattr(split.splits, n)) for n in ("train", "validation", "holdout")})
    assert (got["grant_id"], got["grant_version"], got["purpose"]) == \
        (grant.grant_id, grant.version, "training")
    assert r.work() == {"succeeded": 0, "failed": 0, "cancelled": 0, "retry": 0}


def test_ap10_a_revocation_between_selection_and_materialisation_refuses():
    """Oracle: the grantor revokes after the selection; the materialisation re-checks and
    finishes `failed` (forbidden, field `grant`) with nothing published and no outcome. A
    materialisation trusting the selection's grant would publish the revoked content."""
    r = Rig()
    requests = [r.w.trace(n) for n in range(1, 4)]
    started = r.start(requests)
    r.w.advance(minutes=1)
    r.w.revoke(GRANTOR)
    assert r.work() == {"succeeded": 0, "failed": 1, "cancelled": 0, "retry": 0}
    op = r.op(started)
    assert op.state == "failed" and op.error is not None
    assert (op.error.code, [f.code for f in op.error.field_errors]) == \
        ("forbidden", ["grant_not_current"])
    assert op.error.operation_id == started.operation.operation_id
    assert op.phase is None, "refused by the materialisation's gate before any phase"
    assert r.w.lab.published == [] and r.outcome(started) is None


def test_ap10_a_new_grant_version_or_an_expiry_after_selection_refuses():
    """Oracle: the selection binds the grant VERSION: a narrowed (still permitting) new version
    refuses, and so does the selected version's expiry passing before the worker runs -
    refused by the materialisation's own gate before any phase starts. Either check missing
    reaches content under a grant the selection was not made under."""
    r = Rig()
    started = r.start([r.w.trace(1)])
    r.w.narrow(GRANTOR, retention_days=7)                 # a new version, still permitting
    assert r.work()["failed"] == 1 and r.w.lab.published == []
    error = r.op(started).error
    assert error is not None and [f.code for f in error.field_errors] == ["grant_not_current"]
    r = Rig()
    r.w.narrow(GRANTOR, expires_at=r.w.now + timedelta(minutes=5))
    started = r.start([r.w.trace(1)], key="k-2")
    r.w.advance(minutes=6)
    assert r.work()["failed"] == 1 and r.w.lab.published == []
    assert r.op(started).phase is None, "refused before any phase: selection never started"


def test_ap10_the_selection_needs_both_purposes_and_a_developer_of_the_provider():
    """Oracle: without `training` (annotation is training) or without `provider_sharing` the
    selection is refused before any operation exists; a viewer is forbidden, a consumer-only
    user and a key audience refused; a body without a holdout is invalid."""
    for purposes in ((v2.DataPurpose.provider_sharing,), (v2.DataPurpose.training,)):
        r = Rig()
        r.w.narrow(GRANTOR, purposes=purposes)
        for purpose in ("training", "annotation"):
            with pytest.raises(errors.Forbidden):
                r.start([r.w.trace(1)], purpose=purpose)
        assert r.ops._rows == {}
    r = Rig()
    with pytest.raises(errors.Forbidden):
        r.start([r.w.trace(1)], user=VIEWER)
    with pytest.raises(errors.NotFound):
        r.start([r.w.trace(1)], user=CONSUMER)
    with pytest.raises(errors.Forbidden):
        run(ft.start(r.ops, r.w.access, r.w.objects,
                     api.Actor(audience="consumer", user_id=DEV, provider_org_id=NEMO),
                     "k", body([r.w.trace(1)])))
    assert r.ops._rows == {}
    with pytest.raises(ValidationError):
        body(["x"], train_bp=8000, validation_bp=2000)


def test_ap10_a_key_reused_with_another_body_conflicts_even_after_a_crash_before_start():
    """Oracle: the selection is keyed by provider + Idempotency-Key and written before the
    operation; a retry with another body is a 409 whether or not the first attempt reached
    `ControlOps.start` - never an operation materialising the first body under the second's
    hash."""
    r = Rig()
    a, b = r.w.trace(1), r.w.trace(2)

    async def dies(*args, **kw):
        raise ConnectionError("died between the two writes")
    real = r.ops.start
    r.ops.start = dies
    with pytest.raises(ConnectionError):
        r.start([a])
    r.ops.start = real
    with pytest.raises(errors.IdempotencyConflict):
        r.start([b])
    assert r.ops._rows == {}
    assert r.start([a]).replayed is False                 # the same body completes the start


def test_ap10_a_crash_mid_materialisation_resumes_to_the_same_refs_once():
    """Oracle: a failure after the selection was published finishes nothing; once the lease
    lapses the next pass (a new fence) republishes the same bytes and finishes once. A
    transient refusal is a retry, never a failed operation."""
    r = Rig()
    started = r.start([r.w.trace(n) for n in range(1, 5)])
    real = versions.derive

    async def dies(*args, **kw):
        raise RuntimeError("the worker died")
    versions.derive = dies
    try:
        assert r.work() == {"succeeded": 0, "failed": 0, "cancelled": 0, "retry": 1}
    finally:
        versions.derive = real
    first = list(r.w.lab.published)
    assert len(first) == 1 and r.op(started).state == "running"
    assert r.work()["retry"] == 0 and r.op(started).state == "running"   # lease still live
    r.w.advance(seconds=ft.LEASE_S + 1)
    assert r.work() == {"succeeded": 1, "failed": 0, "cancelled": 0, "retry": 0}
    assert r.op(started).fence == 2
    assert r.w.lab.published[0] == first[0] and len(r.w.lab.published) == 2
    assert r.done(started)["selected_ref"] == first[0]
    r = Rig()
    r.start([r.w.trace(1)])
    r.w.content.down = True                               # C2: a 503 is retried
    assert r.work() == {"succeeded": 0, "failed": 0, "cancelled": 0, "retry": 1}


def test_ap10_a_cancelled_operation_publishes_nothing():
    """Oracle: cancel before the lease finishes it at once; a cancel requested while it runs
    is honoured at the next phase - `cancelled`, nothing past that phase published."""
    r = Rig()
    started = r.start([r.w.trace(1)])
    assert run(r.ops.cancel(started.operation.operation_id, actor())).state == "cancelled"
    assert r.work() == {"succeeded": 0, "failed": 0, "cancelled": 0, "retry": 0}
    r = Rig()                                    # its worker died; the cancel came meanwhile
    started = r.start([r.w.trace(1)], key="k-2")
    run(r.ops.lease(started.operation.operation_id, "w0", 60))
    assert run(r.ops.cancel(started.operation.operation_id, actor())).state == \
        "cancel_requested"
    r.w.advance(seconds=61)
    assert r.work()["cancelled"] == 1
    assert r.op(started).state == "cancelled" and r.w.lab.published == []
    r = Rig()
    started = r.start([r.w.trace(1)], key="k-3")
    advance = r.ops.advance

    async def cancelled_meanwhile(operation_id, fence, phase, retry_after_s=None):
        if phase == "splitting":
            await r.ops.cancel(operation_id, actor())
        return await advance(operation_id, fence, phase, retry_after_s)
    r.ops.advance = cancelled_meanwhile
    assert r.work()["cancelled"] == 1
    assert r.op(started).state == "cancelled" and len(r.w.lab.published) == 1


def test_ap10_c2_refs_are_bound_to_the_selected_grant_version_for_training():
    """Oracle: the production content port issues each ref to the operation's user under
    the SELECTED grant version's ref for `training`, and content C2 does not serve is `Gone`
    (an omission), never an empty sample."""
    w = World()
    grant = w.directory.grants[(GRANTOR, NEMO)]
    calls = []

    class C2:
        async def issue(self, **kw):
            calls.append(kw)
            return "handle-1", None

        async def read(self, *, handle, user_id, provider_org_id):
            calls.append((handle, user_id, provider_org_id))
            return SimpleNamespace(state="expired", content=None)
    port = ft.C2Content(C2(), DEV, grant)
    assert run(port.sign(provider_org_id=NEMO, grantor_org_id=GRANTOR, request_id="r1",
                         grant_id=grant.grant_id, model_id=MODEL)) == "handle-1"
    assert calls[0] == {"user_id": DEV, "provider_org_id": NEMO, "grant_ref": grant_ref(grant),
                        "request_id": "r1", "purpose": v2.DataPurpose.training}
    with pytest.raises(errors.Gone):
        run(port.read("handle-1", provider_org_id=NEMO))
    assert calls[1] == ("handle-1", DEV, NEMO)
