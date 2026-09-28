#!/usr/bin/env python3
"""P1 against the merged real stores: D7's `PgLabDataStore` (labels are `lab.annotation.1`
records in `infrx.lab_records`) and L2's `PgAccessStore` (reviewer roles on the database
clock) and D8's `PgLabelLog` (0042: one label/assign/review event per (provider, key) for
ever; WR-P1-D8), on the task-local PostgreSQL (D7's seeded world).

T2I/G8's pattern: outside the mutant runner; the oracles are the fake-world cases' mutants.

    INFRX_D_TASK=p1 uv run --frozen pytest -q tests/p/annotations/test_annotations_pg.py
"""
from __future__ import annotations

import json
import os

import pytest
from infrx.contracts import errors
from infrx.datasets import imports
from infrx.media.store import InMemoryObjectStore
from infrx.pipelines import annotations as p1
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_data import PgLabDataStore
from infrx.state.lab_pipeline import PgLabelLog

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_l2sql_access as l2
from ...n.imports.world import NEMO, chunks, fixture, run
from ...n.versions.test_versions import uid
from .test_annotations import label_rows
from .world import NOW, RUBRIC, rows

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") else \
    "PostgreSQL only on an explicit task-local key (INFRX_D_TASK=p1)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_p1"
DEV, ADMIN, VIEWER = l2.DEV, l2.ADMIN, l2.VIEWER


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        connect = connector(pgharness.dsn(DB))
        store, objects = PgLabDataStore(connect), InMemoryObjectStore()
        spec, _ = fixture("benchmark")
        spec = {**spec, "import_id": uid(1, 0x1c), "dataset_id": uid(1, 0xdc),
                "grant_ref": d7.W["grant"], "fields": {"content": "q", "group": "g",
                                                       "split": "split"}}
        data = b"".join(json.dumps(i).encode() + b"\n"
                        for i in rows(6, splits=("train", "holdout", "validation")))
        ref = run(imports.Importer(store, objects).run(
            spec, chunks(data, 64), provider_org_id=NEMO, actor="dev@nemo")).dataset_ref
        yield conn, store, objects, PgAccessStore(connect), PgLabelLog(connect), ref


def test_p1_pg_labels_are_d7_records_reviewed_on_the_l2_clock(world) -> None:
    """Imported labels are D7 annotation records (a re-import is D7's replay, the same
    refs); a viewer cannot be assigned; the developer's correction is a `human` record in
    D7 citing the original, which still resolves; the export is train only with lineage."""
    conn, store, objects, access, log, ref = world
    m = run(store.resolve(ref, provider_org_id=NEMO))
    items = label_rows(sorted(s.sample_id for s in m.samples))
    got = run(p1.import_labels(store, log, provider_org_id=NEMO, actor="dev@nemo",
                               dataset_ref=ref, rubric_ref=RUBRIC, rows=items))
    assert not got.rejected and len(got.accepted) == 6
    again = run(p1.import_labels(store, log, provider_org_id=NEMO, actor="dev@nemo",
                                 dataset_ref=ref, rubric_ref=RUBRIC, rows=items))
    assert again.accepted == got.accepted
    assert d7.count(conn, "select count(*) from infrx.lab_records where kind = 'annotation'") \
        == 6
    with pytest.raises(errors.Forbidden):
        run(p1.assign(log, access, provider_org_id=NEMO, user_id=ADMIN, dataset_ref=ref,
                      sample_id=m.samples[0].sample_id, reviewer_id=VIEWER, rubric_ref=RUBRIC))
    train = set(m.splits.train)
    fixed = None
    for label in got.accepted:
        record = run(store.resolve(label, provider_org_id=NEMO))
        run(p1.assign(log, access, provider_org_id=NEMO, user_id=ADMIN, dataset_ref=ref,
                      sample_id=record.sample_id, reviewer_id=DEV, rubric_ref=RUBRIC))
        correct = fixed is None and record.sample_id in train
        out = run(p1.review(store, log, access, provider_org_id=NEMO, user_id=DEV,
                            dataset_ref=ref, annotation_ref=label,
                            decision="rejected" if correct else "accepted", rubric_ref=RUBRIC,
                            correction={"answer": "fixed"} if correct else None))
        fixed = out or fixed
    human = run(store.resolve(fixed, provider_org_id=NEMO))
    assert (human.method, human.reviewer_id, human.ground_truth) == ("human", DEV, True)
    rec = run(p1.export(store, log, objects, provider_org_id=NEMO, dataset_ref=ref,
                        export_id=uid(1, 0xe8), adapter="sft.1",
                        now=NOW, ttl_s=3600))
    assert sorted(x["sample_id"] for x in rec["lineage"]) == sorted(train)
    assert {"human"} in [set(x["methods"]) for x in rec["lineage"]]
    assert {o["reason"] for o in rec["omitted"]} == {"holdout", "validation"}
    kinds = dict(conn.execute("select kind, count(*) from infrx.lab_label_events group by kind"
                              ).fetchall())
    # WR-P1-D8: the log is D8's - 6 imported labels (the re-import replays, adding none) + the
    # human correction, one assignment and one review per label
    assert kinds == {"label": 7, "assign": 6, "review": 6}, kinds


def test_p1_pg_a_revoked_grant_stops_labels_and_exports(world) -> None:
    """After C1 revokes its grant to NEMO, D7's access and training reads refuse the samples:
    a new label is `grant_not_current` and a new export ships nothing."""
    conn, store, objects, access, log, ref = world
    l2.ok(conn, "lab_revoke_access_grant", {"actor_user_id": l2.C1,
                                            "grantor_org_id": l2.org(conn, l2.C1),
                                            "recipient_provider_org_id": NEMO})
    m = run(store.resolve(ref, provider_org_id=NEMO))
    got = run(p1.import_labels(store, log, provider_org_id=NEMO, actor="dev@nemo",
                               dataset_ref=ref, rubric_ref=RUBRIC,
                               rows=label_rows([m.samples[0].sample_id], method="human")))
    assert got.rejected == [{"row": 1, "reason": "grant_not_current"}]
    rec = run(p1.export(store, log, objects, provider_org_id=NEMO, dataset_ref=ref,
                        export_id=uid(2, 0xe8), adapter="sft.1",
                        now=NOW, ttl_s=3600))
    assert rec["items"] == 0 and "grant_not_current" in {o["reason"] for o in rec["omitted"]}
