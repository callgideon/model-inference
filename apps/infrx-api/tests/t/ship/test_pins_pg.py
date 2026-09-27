"""T2I-R1: the versions the projection carries, from D5's real pins lookup on PostgreSQL.

`test_ship.py`'s versions scenario rerun with `PgJobStore.admission_pins` (wiring request
WR-4, owner D5 / coordinator) over the task-local PostgreSQL of `tests/d`'s harness: a CREDIT
request admitted through the store's own `admit_credit` ships with the pins PostgreSQL
persisted; a legacy USD request, and another organization's envelope claiming the CREDIT
request's id, ship with none; a PostgreSQL failure holds the segment instead of shipping rows
without their versions. It skips visibly, naming WR-4, on a tree without `admission_pins`.

G8's pattern (`tests/g/ops/test_*_pg.py`): outside the mutant runner, since a PG case in a
mutant copy would contend for the harness port's lock; the failure oracles are the recorded
fail-first runs (T2I evidence, fix round).

    INFRX_D_TASK=t2i uv run --frozen pytest -q tests/t/ship/test_pins_pg.py
"""
from __future__ import annotations

import pytest

from infrx.contracts.conformance import builders as b
from infrx.state.jobstore import PgJobStore, connector
from infrx.traces import ship

from ...d import pgharness
from ...g.ops import pgworld
from .test_ship import (ID_A, MemoryProjection, Objects, Projection, backend,  # noqa: F401
                        capture, durable, recorded, run, spool, versions_scenario)

pytestmark = [
    pytest.mark.skipif(not hasattr(PgJobStore, "admission_pins"),
                       reason="T2I WR-4 (owner: D5 / coordinator wiring): "
                              "PgJobStore.admission_pins is not on this tree"),
    pgworld.needs_pg,
]


@pytest.fixture
def world():
    w = pgworld.world("ship")
    try:
        yield w
    finally:
        w.owner.close()


def test_the_versions_are_the_ones_postgresql_admitted(backend, world):  # noqa: F811
    """Oracle: a lookup that ignores the organization gives the other org's envelope the
    CREDIT request's pins; one that answers nothing leaves the admitted row without them."""
    projection, objects = backend
    credit, admission = run(pgworld.admit_credit(world, "t2i-credit"))
    legacy, _ = run(pgworld.admit_legacy(world, "t2i-usd"))
    run(versions_scenario(projection, objects, recorded(pgworld.jobs(world).admission_pins),
                          (credit.org_id, credit.request_id), admission.pins,
                          (legacy.org_id, legacy.request_id)))


def test_a_postgresql_failure_holds_the_segment(world):
    """Oracle: a lookup that turns a PostgreSQL failure into None ships the row without its
    versions for ever (a replay inserts the same version); raising holds the segment."""
    async def scenario():
        sink = spool()
        await capture(sink, ID_A, b"content")
        await durable(sink)
        projection, objects = Projection(MemoryProjection()), Objects()
        absent = PgJobStore(connector(pgharness.dsn(f"{world.database}_absent")))
        report = await ship.Shipper(sink, projection, objects, pins=absent.admission_pins).ship()
        assert report.shipped == 0 and len(report.held) == 1, report
        assert await projection.find(b.ORG_A, ID_A) == []
        assert len(sink.segments()) == 1
        await sink.close()
    run(scenario())
