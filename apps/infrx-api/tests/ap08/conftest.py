"""AP-08's PostgreSQL world on the lane's key (`INFRX_D_TASK=ap8`, PG 57563), the D6J judge
world (`tests/d/test_d6j_judge.seed`: NEMO's DEV/ADMIN/VIEWER, C1's external_judging grant, the
`lab_submission` flag, NEMO's 100 PROVIDER_USD payer) plus D6F's request (one CREDIT job of C1
on NEMO's model, C1's `feedback`/`provider_sharing` grant, the `feedback` flag), with 0064 and
0067 (SR-AP08-1's rubric store).

The doors run on the Lab control unit's own login (0043's `infrx_lab_control`, `set_role=False`
as `lab/control/app.py` composes it). Skips visibly unless the key is ap8 and Docker answers:
never the d1 default."""
from __future__ import annotations

import os

import pytest

from tests.d import pgharness

KEY = "ap8"
TEMPLATE, CASE = f"{pgharness.DATABASE}_ap8tpl", f"{pgharness.DATABASE}_ap8case"
LAB_PASSWORD = "infrx-ap8-lab-control"
W: dict[str, str] = {}


def pg_reason() -> str | None:
    if os.environ.get("INFRX_D_TASK") != KEY:
        return f"INFRX_D_TASK is not {KEY} (the lane's key)"
    return pgharness.unavailable()


def seed(conn) -> None:
    from infrx.contracts.conformance import builders as b
    from tests.d import checks_admission as ca
    from tests.d import checks_credit as cc
    from tests.d import test_d6j_judge as j
    j.seed(conn)
    conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                 "values ('feedback', true, 'ap8', 'reviews') on conflict (name) do update "
                 "set enabled = true")
    world = ca.World(conn)
    request = ca.credit_request(world, ca.C1_KEY, cc.personal_org(conn, j.C1),
                                model="nemostation/marlin-2b")
    ca.admit(conn, request, b.idem(request, request.request_id), regime="credit")
    # ONE current grant per (grantor, provider): judging AND sharing of the feedback
    g = j.judge_grant(conn, categories=[*j.BOTH_CATEGORIES, "feedback"],
                      purposes=["external_judging", "provider_sharing"])
    j.W["grant_id"], j.W["version"] = g["grant_id"], g["version"]
    conn.execute(f"alter role infrx_lab_control password '{LAB_PASSWORD}'")
    W["job"] = request.request_id


@pytest.fixture(scope="session")
def pg_template():
    reason = pg_reason()
    if reason:
        pytest.skip(f"task-local PostgreSQL unavailable: {reason}")
    from infrx.state import migrations
    pgharness.ensure()
    pgharness.recreate(TEMPLATE)
    pgharness.apply(TEMPLATE, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(TEMPLATE) as conn:
        seed(conn)
    return TEMPLATE


@pytest.fixture
def pg(pg_template):
    """A fresh copy of the world: the owner connection (`W["job"]` is its request)."""
    pgharness.assert_ours("copy a database in")
    with pgharness.connect("postgres") as admin:
        admin.execute(f'drop database if exists "{CASE}" with (force)')
        admin.execute(f'create database "{CASE}" template "{pg_template}"')
    with pgharness.connect(CASE) as conn:
        yield conn
