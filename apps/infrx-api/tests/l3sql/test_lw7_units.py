#!/usr/bin/env python3
"""lab-sql LW7: the Python half of 0055's ports with NO database - what `PgLabVariants` and
`PgLabImportJobs.requeue` send and return. `tests/l3sql/mutants.py` runs here; the SQL is
`test_lw7.py`.

    uv run --frozen pytest -q tests/l3sql/test_lw7_units.py
"""
from __future__ import annotations

from infrx.state.lab_data import PgLabImportJobs
from infrx.state.lab_variants import PgLabVariants

from ..d.test_adapter_units import _Conn
from ..d.test_d7_units import NEMO, _ok, _sent


def _on(cls, *answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return cls(connect), conn


def test_variants__are_read_for_the_callers_provider_as_answered() -> None:
    """WR-C6-VARIANTS: the listing is asked for the caller's provider and returned whole."""
    rows = [{"variant_ref": "lab:variant:x", "comparison": None}]
    store, conn = _on(PgLabVariants, rows)
    assert _ok(store.variants(NEMO)) == rows
    assert _sent(conn) == ("lab_optimization_variants", {"provider_org_id": NEMO})


def test_requeue__names_the_failed_job_the_new_id_the_provider_and_the_requeuer() -> None:
    """WR-C6-REQUEUE: every field the SQL decides on is sent; the successor is returned."""
    again = {"job_id": "j2", "requeued_from": "j1", "state": "queued"}
    store, conn = _on(PgLabImportJobs, again)
    assert _ok(store.requeue("j1", new_job_id="j2", provider_org_id=NEMO, actor="dev")) == again
    assert _sent(conn) == ("lab_import_requeue", {"job_id": "j1", "new_job_id": "j2",
                                                  "provider_org_id": NEMO, "actor": "dev"})
