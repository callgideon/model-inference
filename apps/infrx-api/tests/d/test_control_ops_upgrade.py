#!/usr/bin/env python3
"""AP-00 slice 00d / R151 rehearsal: `0060_control_operations.sql` applied by the hosted tool
(`deploy/migrate.py` plan -> apply --expect, the Supabase CLI history table) to a database at
the hosted level 0059 that holds consumer history changes nothing that existed (row counts,
money sums, the jobs' identity and money, every ACL of an existing relation, column or
function), is re-runnable, rolls back to exactly the 0059 state with the file's own ROLLBACK
lines, and rolls forward again into a working store.

    INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_control_ops_upgrade.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys

import pytest
from infrx.state import migrations
from infrx.state.control_ops import PgControlOps, input_hash
from infrx.state.jobstore import connector

from . import pgharness
from . import test_upgrade_d10 as d10
from .test_control_ops_units import actor, uid

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_control_ops_upgrade"
FILE = "0060_control_operations.sql"
NEW_TABLES = {"infrx.control_operations", "infrx.control_idempotency"}
NEW_FUNCTIONS = {*(f"infrx.control_op_{m}(jsonb)" for m in
                   ("start", "lease", "advance", "finish", "cancel", "get", "pending", "row")),
                 "infrx.control_owner(jsonb)", "infrx.control_op_doc(infrx.control_operations)"}

_spec = importlib.util.spec_from_file_location(
    "infrx_migrate_0060", migrations.DIR.parents[3] / "apps" / "infrx-api" / "deploy" / "migrate.py")
assert _spec and _spec.loader
migrate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = migrate
_spec.loader.exec_module(migrate)


def rollback_sql() -> str:
    """The `-- rollback: ` lines of the file's header, the one source of its rollback."""
    lines = [line.removeprefix("-- rollback: ") for line in
             (migrations.DIR / FILE).read_text().splitlines() if line.startswith("-- rollback: ")]
    assert len(lines) == 2, lines
    return "\n".join(lines)


def test_0060_rehearses_over_hosted_history_reruns_rolls_back_and_forward(monkeypatch,
                                                                          capsys) -> None:
    everything = migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    number = {label: label[:4] for label, _ in everything if label[:4].isdigit()}
    early = tuple(f for f in everything if number.get(f[0], "0000") < "0027")
    hosted = tuple(f for f in everything if "0027" <= number.get(f[0], "0000") <= "0059")
    assert [label for label, _ in everything if number.get(label, "") > "0059"] == [FILE], \
        "0060 is the only file past the hosted level (R271 allocates 0061-0064 to their lanes)"
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, early)
    conn = pgharness.connect(DB)
    made = d10.seed_history(conn)
    pgharness.apply(DB, hosted)
    conn.execute("create schema if not exists supabase_migrations; create table if not exists "
                 "supabase_migrations.schema_migrations (version text primary key, name text, "
                 "statements text[])")
    conn.execute("insert into supabase_migrations.schema_migrations (version, name) "
                 "select v, n from unnest(%s::text[], %s::text[]) as h(v, n)",
                 ([number[label] for label, _ in early + hosted if label in number],
                  [label[5:-4] for label, _ in early + hosted if label in number]))
    before = d10.snapshot(conn)

    monkeypatch.setenv(migrate.DSN_ENV, pgharness.dsn(DB))
    assert migrate.plan_command(migrations.DIR) == 0
    plan = capsys.readouterr().out
    assert f"pending: {FILE} " in plan and plan.count("pending:") == 1, plan
    expect = plan.rsplit("plan digest: ", 1)[1].strip()
    assert migrate.apply_command(migrations.DIR, expect) == 0
    assert conn.execute("select max(version) from supabase_migrations.schema_migrations"
                        ).fetchone()[0] == "0060"
    after = d10.snapshot(conn)

    assert set(after["counts"]) - set(before["counts"]) == NEW_TABLES
    assert {t: after["counts"][t] for t in NEW_TABLES} == dict.fromkeys(NEW_TABLES, 0)
    assert {t: n for t, n in after["counts"].items() if t not in NEW_TABLES} == \
        before["counts"], "0060 changed a row count"
    assert (after["sums"], after["jobs"]) == (before["sums"], before["jobs"]), \
        "0060 changed money or a job"
    assert {k: v for k, v in after["acl"].items() if k in before["acl"]} == before["acl"], \
        "0060 changed an existing relation's grants"
    assert after["cols"] == before["cols"]
    assert {k: v for k, v in after["fns"].items() if k in before["fns"]} == before["fns"], \
        "0060 changed an existing function's grants"
    assert set(after["fns"]) - set(before["fns"]) == NEW_FUNCTIONS

    pgharness.apply(DB, ((FILE, (migrations.DIR / FILE).read_text()),))
    assert d10.snapshot(conn) == after, "0060 is not re-runnable"
    conn.execute(rollback_sql())
    assert d10.snapshot(conn) == before, "the ROLLBACK lines do not restore the 0059 state"
    pgharness.apply(DB, ((FILE, (migrations.DIR / FILE).read_text()),))
    assert d10.snapshot(conn) == after, "rolling forward again is not the same 0060"
    who = actor(provider=uid(), user=uid())
    started = asyncio.run(PgControlOps(connector(pgharness.dsn(DB))).start(
        "deployment.create", who, "k", input_hash({"b": 1})))
    assert started.operation.state == "queued" and not started.replayed
    conn.close()
    print(f"0060 over {len(made)} seeded job states at 0059: {len(before['counts'])} tables "
          f"unchanged, +{sorted(NEW_TABLES)}; migrate.py plan/apply; re-run, rollback, forward")
