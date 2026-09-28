#!/usr/bin/env python3
"""N3 against the real L2, D7 and D6F on the task-local PostgreSQL (0001-0030; the D7 world
of `tests/d/test_d7_lab_data.py`): `LabAccess` over `PgAccessStore`, `PgLabDataStore`, the
grantor's feedback through `PgFeedbackService.list_owned` (0028's `request_feedback`), a job
admitted for the grantor's own request; T3 is the real `Retention` over in-memory
projections (the n3 key has no ClickHouse) and C2 is `FakeContent` (the content lane builds
it). The object port is in memory, or the task-local MinIO when `INFRX_M_S3_ENDPOINT` is set.

Outside the mutant runner (T2I/G8's pattern); the failure oracles are the fake-world cases'
mutants plus the recorded fail-first run in the N3 evidence.

    INFRX_D_TASK=n3 uv run --frozen pytest -q tests/n/lineage/test_lineage_pg.py
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.datasets import lineage
from infrx.lab.access import LabAccess
from infrx.state import migrations
from infrx.state.feedback import PgFeedbackService
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_content import PgSampleRestrictions
from infrx.state.lab_data import PgLabDataStore
from infrx.traces import ship
from infrx.traces.retention import Retention
from psycopg.types.json import Jsonb

from ...d import checks_admission as ca
from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_l2sql_access as l2
from ..imports.test_import_pg import object_store
from ..imports.world import NEMO, run
from .world import FakeContent, Stones, Traces

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") else \
    "PostgreSQL only on an explicit task-local key (INFRX_D_TASK=n3)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_n3"
CATEGORIES = ["request_content", "response_content", "feedback"]


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    objects = object_store()
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        grantor = l2.org(conn, l2.C1)
        l2.call(conn, "lab_put_access_grant", l2.scope(
            conn, categories=CATEGORIES, purposes=["provider_sharing", "training"]))
        conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                     "values ('feedback', true, 'rig', 'N3') on conflict (name) do update "
                     "set enabled = true")
        request = ca.credit_request(ca.World(conn), ca.C1_KEY, grantor)
        ca.admit(conn, request, b.idem(request, "n3-trace"), regime="credit")
        conn.execute("select infrx.accept_feedback(%s)", (Jsonb({
            "org_id": grantor, "principal": ca.C1_KEY, "by_operator": False,
            "channel": "api", "request_id": request.request_id,
            "feedback_id": f"fb_{uuid.uuid4().hex[:26]}",
            "body": {"name": "correction", "value": "the answer is 4"},
            "idem": {"org_id": grantor, "operation": "feedback", "key": "n3-fb",
                     "payload_hash": "sha256:" + "cd" * 32}}),))
        yield SimpleNamespace(conn=conn, dsn=pgharness.dsn(DB), grantor=grantor,
                              request=request.request_id, objects=objects)
    if os.environ.get("INFRX_M_S3_ENDPOINT"):
        for key in run(objects.keys("")):
            run(objects.delete(key))


def test_n3_pg_selection_revocation_and_tombstones(world) -> None:
    """Oracle (DATA-RIGHTS, DATA-LINEAGE on real rows): the grantor's trace becomes a D7
    sample bound to the current grant, with its durable D6F row as a correction; a
    consumer-only user and a viewer are refused; after the real `lab_revoke_access_grant`
    the gate returns nothing, reconcile tombstones the sample, and a re-grant does not bring
    it back. The reconcile is the Lab worker's call, without `restrictions` (1-DS5-R1):
    PgAccessStore's connection reaches 0041."""
    w, dsn = world, world.dsn
    directory = PgAccessStore(connector(dsn))
    access, store = LabAccess(directory), PgLabDataStore(connector(dsn))
    feedback = PgFeedbackService(connector(dsn))
    now = w.conn.execute("select infrx.now()").fetchone()[0]
    traces, stones = Traces(), Stones()
    from infrx.media.store import InMemoryObjectStore
    trace_objects = InMemoryObjectStore()
    key = ship.content_key(w.grantor, "77000000-0000-4000-8000-000000000001")
    traces.rows.append(SimpleNamespace(
        org_id=w.grantor, request_id=w.request, started_at=now - timedelta(hours=1),
        completed_at=now - timedelta(minutes=59), content_stored=True, content_key=key,
        model_revision=f"{l2.MODEL}@rev1"))
    trace_objects.seed(key, b'{"request": {"q": "2+2?"}, "output": {"a": "5"}}',
                       "application/json")
    retention = Retention(stones, traces, None, trace_objects, clock=lambda: now)

    async def rows(org, request):
        return await feedback.list_owned(SimpleNamespace(org_id=org, is_operator=False),
                                         request)

    async def model_of(row):
        return row.model_revision.split("@")[0]

    def select(user, selection):
        return run(lineage.select(
            access=access, retention=retention, content=FakeContent(directory, retention),
            feedback=rows, model_of=model_of, store=store, objects=w.objects, user_id=user,
            provider_org_id=NEMO, grantor_org_id=w.grantor, model_id=l2.MODEL,
            selection_id=selection, dataset_id="da000000-0000-4000-8000-0000000000e3",
            version=1, created_at="2026-09-27T12:30:00Z", request_ids=[w.request],
            actor="dev@nemo"))

    for user, refusal in ((l2.NOBODY, errors.NotFound), (l2.VIEWER, errors.Forbidden)):
        with pytest.raises(refusal):
            select(user, "5e000000-0000-4000-8000-0000000000f0")
    got = select(l2.DEV, "5e000000-0000-4000-8000-0000000000f1")
    assert (got.samples, got.omitted) == (1, [])
    bound = w.conn.execute(
        "select s.grant_id::text, s.split from infrx.lab_dataset_samples s "
        "where s.dataset_ref = %s", (got.dataset_ref,)).fetchall()
    grant_id = w.conn.execute(
        "select grant_id::text from infrx.lab_access_grants where grantor_org_id = %s "
        "and recipient_provider_org_id = %s limit 1", (w.grantor, NEMO)).fetchone()[0]
    assert bound == [(grant_id, "train")]
    assert w.conn.execute(
        "select sample_id::text, content_until from infrx.lab_sample_bounds "
        "where provider_org_id = %s", (NEMO,)).fetchall() == \
        [(sample_id, now - timedelta(hours=1) + timedelta(days=90))
         for sample_id in [run(store.resolve(got.dataset_ref,
                                             provider_org_id=NEMO)).samples[0].sample_id]]
    view = run(lineage.status(store, w.objects, got.dataset_ref, provider_org_id=NEMO, now=now))
    [sample] = view["samples"]
    assert (sample["restricted"], sample["trace"]["request_id"]) == (None, w.request)

    from infrx.datasets import imports
    manifest = run(store.resolve(got.dataset_ref, provider_org_id=NEMO))
    body = json.loads(run(w.objects.get(imports.sample_key(NEMO,
                                                           manifest.samples[0].content_digest))))
    assert [(c["name"], c["value"], c["author_role"]) for c in body["corrections"]] == \
        [("correction", "the answer is 4", "customer")]

    def gate():
        return run(lineage.permitted(store, w.objects, got.dataset_ref, provider_org_id=NEMO,
                                     purpose="training", now=now))
    assert gate() == {sample["sample_id"]}
    l2.call(w.conn, "lab_revoke_access_grant", {
        "actor_user_id": l2.C1, "grantor_org_id": w.grantor,
        "recipient_provider_org_id": NEMO})
    assert gate() == set()
    restrictions = PgSampleRestrictions(connector(dsn))      # WR-N3-5: 0041 is the authority
    report = run(lineage.reconcile(directory, retention, w.objects,   # the worker's shape
                                   provider_org_id=NEMO))             # (1-DS5-R1)
    assert report["tombstoned"] == [{"sample_id": sample["sample_id"],
                                     "reason": "grant_not_current"}]
    l2.call(w.conn, "lab_put_access_grant", l2.scope(
        w.conn, categories=CATEGORIES, purposes=["provider_sharing", "training"]))
    assert run(store.accessible_samples(got.dataset_ref, provider_org_id=NEMO,
                                        purpose="training")) == [sample["sample_id"]]
    assert gate() == set(), "a re-grant resurrected a tombstoned sample"
    assert run(restrictions.blocked(got.dataset_ref, provider_org_id=NEMO)) == \
        {sample["sample_id"]: "grant_not_current"}, "the tombstone never reached 0041"
    assert run(restrictions.permitted(got.dataset_ref, provider_org_id=NEMO,
                                      purpose="training")) == []


def test_n3_pg_backfill_moves_object_restrictions_once(world) -> None:
    """Oracle (WR-N3-5 on 0041): `backfill` moves object-era tombstones (their reason) and
    trace-entry bounds into `lab_sample_tombstones`/`lab_sample_bounds`; a rerun stones
    nothing new and replays the same bounds."""
    from datetime import UTC, datetime

    from infrx.media.store import InMemoryObjectStore
    objects, a, b = InMemoryObjectStore(), str(uuid.uuid4()), str(uuid.uuid4())
    until, base = datetime(2026, 12, 1, tzinfo=UTC), f"lab/{NEMO}/lineage"
    for sid in (a, b):
        objects.seed(f"{base}/samples/{sid}.json", json.dumps(
            {"sample_id": sid, "content_until": until.isoformat()}).encode(), "application/json")
    objects.seed(f"{base}/tombstones/{a}.json", json.dumps(
        {"sample_id": a, "reason": "deleted"}).encode(), "application/json")
    restrictions = PgSampleRestrictions(connector(world.dsn))

    def move():
        return run(lineage.backfill(objects, provider_org_id=NEMO, restrictions=restrictions))
    assert move() == {"stones": 1, "tombstoned": 1, "bounded": 2}
    assert move() == {"stones": 1, "tombstoned": 0, "bounded": 2}
    assert world.conn.execute(
        "select sample_id::text, reason from infrx.lab_sample_tombstones "
        "where sample_id = any(%s::uuid[])", ([a, b],)).fetchall() == [(a, "deleted")]
    assert sorted(world.conn.execute(
        "select sample_id::text, content_until from infrx.lab_sample_bounds "
        "where sample_id = any(%s::uuid[])", ([a, b],)).fetchall()) == \
        sorted([(a, until), (b, until)])
