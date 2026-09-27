#!/usr/bin/env python3
"""R151 / lab-sql brief item 5: the Lab migrations (0027 on) applied to a 0001-0026 database
that holds consumer history - admitted CREDIT and USD jobs, money, identity and grants - change
nothing that existed (row counts except the declared seed rows, money sums, the jobs' identity
and money, every ACL of an existing relation or function) and are re-runnable.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_upgrade_lab.py
"""
from __future__ import annotations

import pytest
from infrx.state import migrations

from . import pgharness
from . import test_upgrade_d10 as d10

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_upgrade_lab"

#: What each Lab migration adds. A new Lab migration extends these three, nothing else.
NEW_TABLES = {"infrx.provider_role_capabilities", "infrx.lab_data_grants"}   # 0027
SEEDED = {"infrx.provider_role_capabilities": 5}                            # 0027


def split(everything=None):
    """(the shim, 0001-0026 and the clock; the Lab files 0027 on) as `(label, sql)` pairs."""
    everything = everything or migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    lab = tuple(f for f in everything if f[0][:4].isdigit() and f[0][:4] >= "0027")
    return tuple(f for f in everything if f not in lab), lab


def test_the_split_puts_every_file_from_0027_in_the_lab_set() -> None:
    files = (("shim.sql", ""), ("0026_x.sql", ""), ("0027_a.sql", ""), ("0031_b.sql", ""),
             ("clock.sql", ""))
    base, lab = split(files)
    assert [f for f, _ in base] == ["shim.sql", "0026_x.sql", "clock.sql"]
    assert [f for f, _ in lab] == ["0027_a.sql", "0031_b.sql"]


def test_lab_upgrade_preserves_history_money_identity_and_grants() -> None:
    base, lab = split()
    assert lab, "no Lab migration to upgrade to"
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, base)
    conn = pgharness.connect(DB)
    made = d10.seed_history(conn)
    # a pre-Lab feedback row: D6F's entry order must number it, and it stays as it was
    conn.execute("insert into infrx.feedback (feedback_id, org_id, request_id, author_principal, "
                 "author_role, channel, name, value_bool) select 'fb_pre_lab', org_id, "
                 "request_id, 'k', 'customer', 'api', 'thumb', true from infrx.jobs "
                 "where request_id = %s", (made["usd_settled"],))
    before = d10.snapshot(conn)
    pgharness.apply(DB, lab)
    after = d10.snapshot(conn)
    assert set(after["counts"]) - set(before["counts"]) == NEW_TABLES, \
        set(after["counts"]) ^ set(before["counts"])
    assert {t: n for t, n in after["counts"].items() if t in NEW_TABLES} == \
        {t: SEEDED.get(t, 0) for t in NEW_TABLES}, "a Lab table was seeded from history"
    assert {t: n for t, n in after["counts"].items() if t not in NEW_TABLES} == \
        before["counts"], "the Lab upgrade changed a row count"
    assert after["sums"] == before["sums"], (before["sums"], after["sums"])
    assert after["jobs"] == before["jobs"], "the Lab upgrade changed a job's identity or money"
    assert {k: v for k, v in after["acl"].items() if k in before["acl"]} == before["acl"], \
        "the Lab upgrade changed an existing relation's grants"
    assert {k: v for k, v in after["cols"].items() if k in before["cols"]} == before["cols"]
    changed = {k for k in before["fns"] if after["fns"].get(k) != before["fns"][k]}
    assert changed == set(), f"an existing function's grants changed: {changed}"
    assert conn.execute("select entry_seq from infrx.feedback where feedback_id = 'fb_pre_lab'"
                        ).fetchone()[0] is not None                              # 0028
    pgharness.apply(DB, lab)
    assert d10.snapshot(conn) == after, "the Lab set is not re-runnable"
    print(f"Lab upgrade over {len(made)} seeded job states: {len(before['counts'])} tables "
          f"unchanged, +{sorted(NEW_TABLES)}; sums {after['sums']}; re-run no-op")
