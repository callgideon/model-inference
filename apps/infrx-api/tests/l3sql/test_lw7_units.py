#!/usr/bin/env python3
"""lab-sql LW7: the Python half of 0055's ports with NO database - what
`PgLabImportJobs.requeue` sends and returns (`PgLabVariants` reads 0058's identified listing
since LW9: `test_lw9_units.py`). `tests/l3sql/mutants.py` runs here; the SQL is
`test_lw7.py`.

    uv run --frozen pytest -q tests/l3sql/test_lw7_units.py
"""
from __future__ import annotations

from infrx.state.lab_data import PgLabImportJobs

from ..d.test_adapter_units import _Conn
from ..d.test_d7_units import NEMO, _ok, _sent


def _on(cls, *answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return cls(connect), conn


def test_requeue__names_the_failed_job_the_new_id_the_provider_and_the_requeuer() -> None:
    """WR-C6-REQUEUE: every field the SQL decides on is sent; the successor is returned."""
    again = {"job_id": "j2", "requeued_from": "j1", "state": "queued"}
    store, conn = _on(PgLabImportJobs, again)
    assert _ok(store.requeue("j1", new_job_id="j2", provider_org_id=NEMO, actor="dev")) == again
    assert _sent(conn) == ("lab_import_requeue", {"job_id": "j1", "new_job_id": "j2",
                                                  "provider_org_id": NEMO, "actor": "dev"})
