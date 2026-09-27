"""T2I WR-3: the versions the projection carries, from D5's pins lookup on real PostgreSQL.

`test_ship.py`'s versions scenario rerun with `ship.PgPins` over the task-local PostgreSQL of
`tests/d`'s harness (`tests/g/ops/pgworld.world`: every migration, the admission seed): a
CREDIT request admitted through the store's own `admit_credit` ships with the pins PostgreSQL
persisted; a legacy USD request, and another organization's envelope claiming the CREDIT
request's id, ship with none; a PostgreSQL failure holds the segment instead of shipping rows
without their versions.

Each `check_*(pins, world)` takes the lookup module (`infrx.traces.ship.pins`) as an argument,
so `tests/t/feedback/mutants.py` kills its SQL in-process on a fresh world per run.

    INFRX_D_TASK=t2f uv run --frozen pytest -q tests/t/ship/test_pins_pg.py
"""
from __future__ import annotations

import pytest

from infrx.contracts.conformance import builders as b
from infrx.state.jobstore import connector
from infrx.traces import ship
from infrx.traces.ship import pins as lookup

from ...d import pgharness
from ...g.ops import pgworld
from .test_ship import (ID_A, MemoryProjection, Objects, Projection, capture, durable,
                        recorded, run, spool, versions_scenario)

pytestmark = pgworld.needs_pg


def check_the_versions_are_the_ones_postgresql_admitted(pins, w) -> None:
    """Oracle: a lookup that ignores the organization gives the other org's envelope the
    CREDIT request's pins; one that ignores the regime, or answers nothing, ships a wrong
    row (or holds a legacy one for ever)."""
    credit, admission = run(pgworld.admit_credit(w, "t2i-credit"))
    legacy, _ = run(pgworld.admit_legacy(w, "t2i-usd"))
    run(versions_scenario(Projection(MemoryProjection()), Objects(),
                          recorded(pins.PgPins(w.connect())),
                          (credit.org_id, credit.request_id), admission.pins,
                          (legacy.org_id, legacy.request_id)))


def check_a_postgresql_failure_holds_the_segment(pins, w) -> None:
    """Oracle: a lookup that turns a PostgreSQL failure into None ships the row without its
    versions for ever (a replay inserts the same version); raising holds the segment."""
    async def scenario():
        sink = spool()
        await capture(sink, ID_A, b"content")
        await durable(sink)
        projection, objects = Projection(MemoryProjection()), Objects()
        absent = pins.PgPins(connector(pgharness.dsn(f"{w.database}_absent")))
        report = await ship.Shipper(sink, projection, objects, pins=absent).ship()
        assert report.shipped == 0 and len(report.held) == 1, report
        assert await projection.find(b.ORG_A, ID_A) == []
        assert len(sink.segments()) == 1
        await sink.close()
    run(scenario())


CHECKS = {name: check for name, check in dict(globals()).items() if name.startswith("check_")}


@pytest.fixture
def world():
    w = pgworld.world("pins")
    try:
        yield w
    finally:
        w.owner.close()


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_pins_lookup_on_postgresql(name, world):
    CHECKS[name](lookup, world)
