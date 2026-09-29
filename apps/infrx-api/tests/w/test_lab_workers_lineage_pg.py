#!/usr/bin/env python3
"""WR-DS5-2's real half: `test_lineage_pg`'s selection-revocation-tombstone flow with its one
reconcile run by the datasets role's own pass (`python -m infrx.lab.workers datasets`, composed
from its environment on the n3 PostgreSQL) - `lineage.reconcile` with `PgSampleRestrictions`
passed explicitly on the role's login - instead of the bare call. T3's retention is the flow's
in-memory one (the n3 key has no ClickHouse). Outside the mutant runner; the oracles are
`lw_reconcile_restrictions_*`.

    INFRX_D_TASK=n3 uv run --frozen pytest -q tests/w/test_lab_workers_lineage_pg.py
"""
from __future__ import annotations

from infrx.datasets import lineage
from infrx.lab.workers import __main__ as lab_workers
from infrx.state.lab_content import PgSampleRestrictions

from ..n.lineage import test_lineage_pg as n3
from ..n.lineage.test_lineage_pg import world  # noqa: F401,F811 - the n3 fixture

pytestmark = n3.pytestmark


def test_lab_workers_lineage_pg__the_datasets_pass_reconciles_into_0041(world, monkeypatch):  # noqa: F811
    """The flow's revocation reconcile, through the role: the sample is tombstoned in 0041
    (a re-grant resurrects nothing) and the pass handed `lineage.reconcile` the explicit
    `PgSampleRestrictions`."""
    real, seen, reports = lineage.reconcile, [], []
    env = {"LAB_DATABASE_URL": world.dsn, "LAB_WORKER_HEALTH_PORT": "18012",
           "LAB_S3_BUCKET": "not-read", "CLICKHOUSE_URL": "http://ch.invalid:8123/infrx",
           "S3_TRACE_BUCKET": "not-read"}

    async def recorded(*args, **kw):
        seen.append(kw.get("restrictions"))
        reports.append(await real(*args, **kw))
        return reports[-1]

    async def through_the_role(directory, retention, objects, *, provider_org_id):
        steps = {}

        async def idle():
            pass
        monkeypatch.setattr(lab_workers, "trace_retention", lambda *a, **k: retention)
        monkeypatch.setattr(lab_workers, "every", lambda interval_s, step, what: (
            steps.__setitem__(what, step), idle())[1])
        monkeypatch.setattr(lineage, "reconcile", recorded)
        worker = lab_workers.compose("datasets", env, objects=objects)
        await worker.tasks["lineage_reconcile"]()
        assert await steps["lineage reconcile"]() == {"providers": 1, "failed": 0}
        monkeypatch.setattr(lineage, "reconcile", through_the_role)
        return {"tombstoned": [t for r in reports for t in r["tombstoned"]]}
    monkeypatch.setattr(lineage, "reconcile", through_the_role)
    n3.test_n3_pg_selection_revocation_and_tombstones(world)
    assert seen and all(type(r) is PgSampleRestrictions for r in seen), seen
