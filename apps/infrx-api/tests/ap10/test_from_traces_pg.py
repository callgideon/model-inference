#!/usr/bin/env python3
"""AP-10 10c on real SQL: the trace -> dataset operation over 0060's `PgControlOps`, the real
L2 grants (0027's `lab_put_access_grant` / `lab_revoke_access_grant` through `PgAccessStore`),
D7's `PgLabDataStore` and D6F's feedback - `from_traces.pg_ports` on ap10's task-local
PostgreSQL, every migration, D7's seeded world (N3's PG world, `tests/n/lineage/
test_lineage_pg.py`). Stand-ins, as N3's PG half: T3 is the real `Retention` over in-memory
projections (ap10 has no ClickHouse), C2 is N3's `FakeContent` (C2's SQL needs a captured
trace body), the row's model is its revision's.

Oracles: a selection then a revocation through the real door -> the operation row `failed`
(forbidden, field `grant`), no dataset record; after a re-grant (a new version) a new
selection materialises two D7 versions (`@1` the selection, `@2` its split, holdout in the
manifest) and the operation row `succeeded` once; a second pass finds nothing pending.
Outside the mutant runner (B1's `_pg` pattern); `test_from_traces.py` carries the mutants.

    INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10/test_from_traces_pg.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest

from infrx.contracts import api
from infrx.contracts.conformance import builders as b
from infrx.lab.datasets import from_traces as ft
from infrx.media.store import InMemoryObjectStore
from infrx.state import migrations
from infrx.state.control_ops import PgControlOps
from infrx.state.jobstore import connector
from infrx.traces import ship
from infrx.traces.retention import Retention

from ..d import checks_admission as ca
from ..d import pgharness
from ..d import test_d7_lab_data as d7
from ..d import test_l2sql_access as l2
from ..n.lineage.world import FakeContent, Stones, Traces

TASK = os.environ.get("INFRX_D_TASK")
_reason = pgharness.unavailable() if TASK == "ap10" else \
    "PostgreSQL only on the ap10 task-local key (INFRX_D_TASK=ap10)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_ap10f"
NEMO = d7.NEMO
CATEGORIES = ["request_content", "response_content", "feedback"]
PURPOSES = ["provider_sharing", "training"]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        grantor = l2.org(conn, l2.C1)
        l2.call(conn, "lab_put_access_grant",
                l2.scope(conn, categories=CATEGORIES, purposes=PURPOSES))
        request = ca.credit_request(ca.World(conn), ca.C1_KEY, grantor)
        ca.admit(conn, request, b.idem(request, "ap10-trace"), regime="credit")
        yield SimpleNamespace(conn=conn, dsn=pgharness.dsn(DB), grantor=grantor,
                              request=request.request_id)


def ports(w, objects):
    now = w.conn.execute("select infrx.now()").fetchone()[0]
    traces, trace_objects = Traces(), InMemoryObjectStore()
    key = ship.content_key(w.grantor, "77000000-0000-4000-8000-0000000000a1")
    traces.rows.append(SimpleNamespace(
        org_id=w.grantor, request_id=w.request, started_at=now - timedelta(hours=1),
        completed_at=now - timedelta(minutes=59), content_stored=True, content_key=key,
        model_revision=f"{l2.MODEL}@rev1", serving_version_id=None))
    trace_objects.seed(key, b'{"request": {"q": "2+2?"}, "output": {"a": "4"}}',
                       "application/json")
    retention = Retention(Stones(), traces, None, trace_objects, clock=lambda: now)
    real = ft.pg_ports(connector(w.dsn), objects, retention)

    async def model_of(row):
        return row.model_revision.split("@")[0]
    content = FakeContent(real.access.store, retention)
    return dataclasses.replace(real, content=lambda user, grant: content, model_of=model_of)


def test_ap10_pg_a_revocation_refuses_and_a_regrant_materialises(world) -> None:
    w, objects = world, InMemoryObjectStore()
    ops, p = PgControlOps(connector(w.dsn)), ports(world, objects)
    actor = api.Actor(audience="session", user_id=l2.DEV, provider_org_id=NEMO,
                      role="developer")

    def body(dataset: str) -> ft.TraceDataset:
        return ft.TraceDataset.model_validate({
            "grantor_org_id": w.grantor, "model_id": l2.MODEL, "request_ids": [w.request],
            "dataset_id": dataset, "version": 1, "purpose": "annotation", "seed": 5,
            "train_bp": 5000, "validation_bp": 0})

    def row(op_id: str):
        return w.conn.execute("select state, phase, error->>'code', error->'field_errors' "
                              "from infrx.control_operations where operation_id = %s",
                              (op_id,)).fetchone()

    def records(dataset: str) -> int:
        return w.conn.execute("select count(*) from infrx.lab_records where ref like %s",
                              (f"lab:dataset:{NEMO}:{dataset}@%",)).fetchone()[0]

    revoked = "da000000-0000-4000-8000-0000000000b1"
    first = run(ft.start(ops, p.access, objects, actor, "ap10-pg-1", body(revoked)))
    assert row(first.operation.operation_id)[0] == "queued"
    l2.call(w.conn, "lab_revoke_access_grant", {
        "actor_user_id": l2.C1, "grantor_org_id": w.grantor, "recipient_provider_org_id": NEMO})
    assert run(ft.work(ops, p, worker_id="ap10")) == \
        {"succeeded": 0, "failed": 1, "cancelled": 0, "retry": 0}
    state, phase, code, fields = row(first.operation.operation_id)
    assert (state, phase, code) == ("failed", None, "forbidden")
    assert [f["code"] for f in fields] == ["grant_not_current"]
    assert records(revoked) == 0

    l2.call(w.conn, "lab_put_access_grant", l2.scope(w.conn, categories=CATEGORIES,
                                                   purposes=PURPOSES))     # a new version
    granted = "da000000-0000-4000-8000-0000000000b2"
    second = run(ft.start(ops, p.access, objects, actor, "ap10-pg-2", body(granted)))
    assert run(ft.work(ops, p, worker_id="ap10")) == \
        {"succeeded": 1, "failed": 0, "cancelled": 0, "retry": 0}
    assert row(second.operation.operation_id)[:2] == ("succeeded", "splitting")
    got = run(ft.outcome(objects, provider_org_id=NEMO,
                         selection_id=second.operation.resource_id or ""))
    assert got is not None and records(granted) == 2
    split = run(p.store.resolve(got["dataset_ref"], provider_org_id=NEMO))
    assert (split.version, split.parent_refs) == (2, [got["selected_ref"]])
    assert got["holdout"] == list(split.splits.holdout)
    assert run(ft.work(ops, p, worker_id="ap10")) == \
        {"succeeded": 0, "failed": 0, "cancelled": 0, "retry": 0}
