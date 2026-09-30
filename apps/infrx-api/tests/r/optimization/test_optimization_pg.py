#!/usr/bin/env python3
"""WR-LSQ-6: R3 stores its `infrx.variant_comparison.1` through D7 on real PostgreSQL -
`PgLabDataStore` (0034 `put_eval_report`, 0040 `put_variant_comparison`) on the task-local key
(INFRX_D_TASK=r2, 57534; r1 when r2 is held by another checkout). The two runs are NEMO's
published evaluation records serving the base and the variant; the report is B2-shaped and
digest-bound as in `test_optimization.py`. Outside the mutant runner: the oracles are the fake
case's `r3_store_*` mutants.

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/r/optimization/test_optimization_pg.py
"""
from __future__ import annotations

import asyncio
import os

import pytest
from infrx.contracts import errors
from infrx.rollouts import optimization as r3
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore
from infrx.state.lab_variants import PgLabVariants

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from .test_optimization import BASE, NVFP4, engine, report

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") in ("r2", "r1", "l3") else \
    "PostgreSQL only on the r2 (or r1, l3) task-local key (INFRX_D_TASK=r2|r1|l3)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_r3"
NEMO, uid = d7.NEMO, d7.uid


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        yield conn


def test_r3_pg_a_comparison_rests_on_the_stored_variant_and_report(world) -> None:
    """The comparison, its report and its variant are stored once and read back beside the
    report; another provider sees none of it; a comparison on an unstored report is refused
    by D7 when R3's order is not followed."""
    variant = asyncio.run(r3.register(NEMO, uid(1, 0x8c), BASE, NVFP4, engine()))
    dataset = d7.publish(world, d7.manifest(uid(1, 0x8d), n=1, tag=0x8d))
    harness = d7.publish(world, d7.harness(uid(3, 0x8d)))
    runs = tuple({**d7.eval_run(uid(n, 0x8e), dataset, harness), "serving_ref": ref}
                 for n, ref in ((1, variant["base_serving_ref"]),
                                (2, variant["variant_serving_ref"])))
    for payload in runs:
        d7.publish(world, payload)
    rep = report(runs=runs)
    result = r3.compare(variant, BASE, NVFP4, report=rep, runs=runs)
    data = PgLabDataStore(connector(pgharness.dsn(DB)))
    early = {**result, "report_digest": report("inconclusive", runs=runs)["report_digest"]}
    with pytest.raises(errors.NotFound):
        asyncio.run(data.put_variant_comparison(early, provider_org_id=NEMO, actor="dev@nemo"))
    ids = {"identities": (BASE, NVFP4), "variants": PgLabVariants(connector(pgharness.dsn(DB)))}
    first = asyncio.run(r3.store(data, variant, result, rep, provider_org_id=NEMO,
                                 actor="dev@nemo", **ids))
    again = asyncio.run(r3.store(data, variant, result, rep, provider_org_id=NEMO,
                                 actor="dev@nemo", **ids))
    assert first == again and first.startswith("sha256:")
    stored = asyncio.run(data.variant_comparisons(rep["report_digest"], provider_org_id=NEMO))
    assert stored == [result]
    assert asyncio.run(data.eval_report(rep["report_digest"], provider_org_id=NEMO)) == rep
    assert asyncio.run(data.variant_comparisons(rep["report_digest"],
                                                provider_org_id=d7.OTHER)) == []
