#!/usr/bin/env python3
"""The real half of WR-LDP-2 / LDP-F1 (b) / LDP-F3 / LDP-F7: the control unit on the task-local
PostgreSQL of key l4 (57503), migrations 0001-0052 applied, L3's seed (`tests/l/control`),
logged in as 0043's `infrx_lab_control` (a local password, as the operator sets one) on the
direct port - where the old `connector(dsn)` ran `set role service_role` and was refused - and,
for comparison, as the owner. The session verifier is the case's own (one test token); every
family's read runs as provider A's administrator.

    INFRX_D_TASK=l4 uv run --frozen pytest -q -s -m pg tests/i/lab_control

Failure oracles: the unit not ready on its own login (LDP-F7); a member refused (401/404) or a
family missing on it; any 500 (LDP-F3). SR-LCR-1 (0056, lab-sql-lw8): the Lab column equals the
owner column for every family (LCR-F1 closed), each pinned in `EXPECTED`; a worker-only claim
stays refused to the Lab login (42501); 0041's sample reads the families' lineage calls run on
it (0-LW8-R1).
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from infrx.contracts import errors
from infrx.gateway import lab_auth
from infrx.lab.control import app as control_app

from ...d import pgharness
from ...l.control import worlds
from .test_control_routes import FAMILIES, PROVIDER, SWITCHES, TOKEN

pytestmark = pytest.mark.pg
_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "l4" else \
    "PostgreSQL only on the l4 task-local key (INFRX_D_TASK=l4)"
DB, LAB_PASSWORD = f"{pgharness.DATABASE}_lcr", "infrx-l4-lab-control"
A, ADMIN_A = worlds.PgWorld.A, worlds.PgWorld.ADMIN_A
#: family -> (status, body prefix) on BOTH logins after 0056. A 503 here is a port the unit does
#: not compose yet (evaluations' experiments: WR-B4-2; teachers off: P-10; R3's variants:
#: WR-C6-VARIANTS), the same on the owner login - never a grant the Lab login lacks.
EXPECTED = {
    "control": (200, '{"data":[{"model_id":"nemostation/marlin-2b"'),
    "datasets": (404, '{"detail":"not_found: no such Lab record for this provider"}'),
    "evaluations": (503, '{"refusal":"unavailable"}'),
    "pipelines": (404, '{"refusal":"not_found"}'),
    "teacher-batches": (503, '{"refusal":"unavailable"}'),
    "releases": (200, '{"data":{"releases":[],"decisions":[],"proposals":[]}}'),
    "optimizations": (503, '{"refusal":"unavailable"}'),
}


@pytest.fixture(scope="module")
def database():
    if _reason is not None:
        pytest.skip(f"task-local PostgreSQL unavailable: {_reason}")
    from infrx.state import migrations
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        worlds.seed_pg(conn, pgharness.dsn(DB))
        conn.execute(f"alter role infrx_lab_control password '{LAB_PASSWORD}'")
    owner = pgharness.dsn(DB)
    lab = make_conninfo(owner, user="infrx_lab_control", password=LAB_PASSWORD)
    assert conninfo_to_dict(lab)["port"] != "6543"        # the direct port: no pooler
    return {"lab": lab, "owner": owner}


def unit(monkeypatch, dsn: str) -> TestClient:
    for name, value in {control_app.DATABASE_URL: dsn,
                        control_app.SUPABASE_URL: "http://127.0.0.1:1",
                        control_app.SUPABASE_KEY: "anon"}.items():
        monkeypatch.setenv(name, value)
    for name in SWITCHES:
        monkeypatch.setenv(name, "0")
    for name in ("DATABASE_URL", "CLICKHOUSE_URL", "S3_TRACE_BUCKET", "LAB_S3_BUCKET",
                 "LAB_CHECKPOINT_KEYS"):
        monkeypatch.delenv(name, raising=False)

    async def user_id(self, token):
        if token != TOKEN:
            raise errors.InvalidApiKey("not a live session")
        return ADMIN_A

    monkeypatch.setattr(lab_auth.GoTrueSessions, "user_id", user_id)
    return TestClient(control_app.create_app(), raise_server_exceptions=False)


def test_control_routes_pg__every_family_is_served_on_the_lab_login_typed_never_a_500(
        database, monkeypatch):
    """On `infrx_lab_control`: `/readyz` 200 (LDP-F7), the control listing 200, and every
    family reached as A's administrator (never 401, never the membership's 404), answering
    its record (200, or 404 for the probe's absent `ds@1`) or its typed 503 - never a 500
    (LDP-F3). The owner login is the comparison column."""
    matrix = {}
    for login, dsn in database.items():
        with monkeypatch.context() as m:
            c = unit(m, dsn)
            assert c.get("/readyz").json() == {"status": "ready"}, login
            for family, path in FAMILIES.items():
                answer = c.get(path.replace(PROVIDER, A),
                               headers={"authorization": f"Bearer {TOKEN}"})
                matrix[login, family] = (answer.status_code, answer.text[:80])
    for login in database:
        print(f"\n[{login}]", {f: matrix[login, f] for f in FAMILIES})
    for (login, family), (status, text) in matrix.items():
        assert status not in (401, 500) and "workspace" not in text, (login, family, text)
        if status == 503:
            assert text in ('{"refusal":"unavailable"}',
                            '{"detail":"the datasets service failed"}'), (login, family, text)
    assert matrix["lab", "control"][0] == 200 and matrix["owner", "control"][0] == 200
    got = {(login, f): (status, text[:len(EXPECTED[f][1])])
           for (login, f), (status, text) in matrix.items()}
    assert got == {(login, f): EXPECTED[f] for login in database for f in FAMILIES}, got
    import psycopg
    with psycopg.connect(database["lab"], autocommit=True) as lab:     # SR-LCR-1: routes only
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            lab.execute("select infrx.lab_import_job_claim('{}'::jsonb)")
        # 0-LW8-R1: lineage.status / lineage.permitted (a version's page, derive, export,
        # read_part; label imports, select, export; training prepare) run 0041's reads on the
        # unit's login once a record exists - the absent `ds@1` above never reaches them.
        for function in ("lab_blocked_samples", "lab_permitted_samples"):
            args = {"provider_org_id": A, "dataset_ref": "lab:dataset:none"}
            try:
                lab.execute(f"select infrx.{function}(%s)", (Jsonb(args),))
            except psycopg.errors.InsufficientPrivilege as refused:
                pytest.fail(f"{function}: {refused.sqlstate} on the Lab login")
            except psycopg.Error:
                pass            # the function ran and refused its arguments: granted
