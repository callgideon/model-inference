#!/usr/bin/env python3
"""N2 against the real D7 store: N1 imports, derived versions and exports through
`PgLabDataStore` on the task-local PostgreSQL (0001-0029, D7's world: C1's grant to NEMO for
provider_sharing + training, plus BOTH's grant to NEMO added here). The object port is
`InMemoryObjectStore`, or the task-local MinIO when `INFRX_M_S3_ENDPOINT` is set (as
`tests/n/imports/test_import_pg.py`).

T2I/G8's pattern: outside the mutant runner; the oracles are the fake-world cases' mutants
plus the recorded fail-first runs in the N2 evidence.

    INFRX_D_TASK=n2 uv run --frozen pytest -q tests/n/versions/test_versions_pg.py
"""
from __future__ import annotations

import json
import os

import pytest
from infrx.contracts import errors
from infrx.contracts.v2 import records as v2
from infrx.datasets import imports, versions
from infrx.media.store import InMemoryObjectStore
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore, grant_ref

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_l2sql_access as l2
from ..imports.test_import_pg import object_store
from ..imports.world import NEMO, chunks, fixture, run
from .test_versions import NOW, POLICY, split_of, text, uid

# Only on an explicit task-local key: the D harness default is another lane's port.
_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") else \
    "PostgreSQL only on an explicit task-local key (INFRX_D_TASK=n2)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_n2"
G: dict[str, str] = {}


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    objects = object_store()
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        second = l2.ok(conn, "lab_put_access_grant", l2.scope(
            conn, owner=l2.BOTH, purposes=["provider_sharing", "training"]))
        G["a"], G["b"] = d7.W["grant"], grant_ref(v2.AccessGrant.model_validate(second))
        yield conn, PgLabDataStore(connector(pgharness.dsn(DB))), objects
    if not isinstance(objects, InMemoryObjectStore):
        for key in run(objects.keys("")):
            run(objects.delete(key))


def imported(store, objects, items, n: int, grant: str) -> str:
    spec, _ = fixture("benchmark")
    spec = {**spec, "import_id": uid(n, 0x1b), "dataset_id": uid(n, 0xdb), "grant_ref": grant,
            "fields": {"content": "q", "group": "g"}}
    data = b"".join(json.dumps(i).encode() + b"\n" for i in items)
    return run(imports.Importer(store, objects).run(
        spec, chunks(data, 64), provider_org_id=NEMO, actor="dev@nemo")).dataset_ref


def derive(store, objects, version, **kw):
    return run(versions.derive(store, objects, provider_org_id=NEMO, actor="dev@nemo",
                               dataset_id=uid(1, 0xdd), version=version,
                               created_at="2026-09-27T13:00:00Z", policy=POLICY, **kw))


def test_n2_pg_versions_keep_a_frozen_holdout_and_d7_stores_the_splits(world) -> None:
    """v1 over an import is the same ref in a second derivation; v2 = v1 + a duplicate
    import + new rows keeps v1's holdout exactly, and D7's membership rows carry the
    manifest's splits; the same version with other bytes is D7's state_conflict."""
    conn, store, objects = world
    a = imported(store, objects, text(30), 1, G["a"])
    v1 = derive(store, objects, 1, add=[a])
    assert derive(store, objects, 1, add=[a]) == v1
    again = imported(store, objects, text(30) + text(10, "new"), 2, G["b"])
    v2 = derive(store, objects, 2, base=v1.dataset_ref, add=[again])
    m1, m2 = (run(store.resolve(v.dataset_ref, provider_org_id=NEMO)) for v in (v1, v2))
    assert m2.splits.holdout == m1.splits.holdout and len(m2.samples) == 40
    assert [o["reason"] for o in v2.omitted] == ["duplicate"] * 30
    rows = dict(conn.execute("select sample_id::text, split from infrx.lab_dataset_samples "
                             "where dataset_ref = %s", (v2.dataset_ref,)).fetchall())
    assert rows == split_of(m2)
    with pytest.raises(errors.StateConflict):
        run(versions.derive(store, objects, provider_org_id=NEMO, actor="dev@nemo",
                            dataset_id=uid(1, 0xdd), version=2, created_at="2026-09-28T00:00:00Z",
                            policy=POLICY, base=v1.dataset_ref, add=[]))


def test_n2_pg_an_export_reads_the_grants_now(world) -> None:
    """An export of v2 ships no holdout; after BOTH revokes its grant the next export omits
    that source's samples (grant_not_current) and the first export's parts stop serving
    them; a derivation after the revocation omits them too."""
    conn, store, objects = world
    v2_ref = conn.execute("select ref from infrx.lab_records where object_id = %s and "
                          "version = 2", (uid(1, 0xdd),)).fetchone()[0]
    first = run(versions.export(store, objects, provider_org_id=NEMO, dataset_ref=v2_ref,
                                export_id=uid(1, 0xee), now=NOW, ttl_s=600))
    assert {o["reason"] for o in first["omitted"]} == {"holdout"}, first["omitted"]
    l2.ok(conn, "lab_revoke_access_grant", {"actor_user_id": l2.BOTH,
                                            "grantor_org_id": l2.org(conn, l2.BOTH),
                                            "recipient_provider_org_id": NEMO})
    second = run(versions.export(store, objects, provider_org_id=NEMO, dataset_ref=v2_ref,
                                 export_id=uid(2, 0xee), now=NOW, ttl_s=600))
    assert {o["reason"] for o in second["omitted"]} == {"holdout", "grant_not_current"}
    served = [json.loads(line) for n in range(len(first["parts"])) for line in run(
        versions.read_part(store, objects, provider_org_id=NEMO, export_id=uid(1, 0xee),
                           part=n, now=NOW)).splitlines()]
    assert served and {i["grant_ref"] for i in served} == {G["a"]}, served[:1]
    v3 = derive(store, objects, 3, base=v2_ref, add=[])
    assert {o["reason"] for o in v3.omitted} == {"grant_not_current"}
