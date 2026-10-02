#!/usr/bin/env python3
"""R271 whole-set re-proof / R151 rehearsal (api-schema 00d, api-schema-2's remainder): every
LOCAL-ONLY wave-7 file (0060-0066 as merged: 0060, 0061, 0064, 0065, 0066; 0062 when it
lands) applied by the hosted tool (`deploy/migrate.py` plan -> apply --expect, the Supabase
CLI history table) to a database at the hosted level 0059 that holds consumer history:
nothing that existed changes (row counts, money sums, the jobs' identity and money, every
ACL of an existing relation or column) except the control login's EXECUTE on 0066's six
route reads and 0064's three judge/review doors; the set re-runs as a no-op; 0066's then 0065's own ROLLBACK lines restore the
state before them exactly (their functions gone, those six grants revoked, the two bodies
0066 re-creates back to 0043's and 0060's); rolling forward again is the same set, and an
operation starts. 0061/0064 carry prose rollbacks; their lanes' upgrade proofs stand.

    INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_control_ops_upgrade.py
    INFRX_D_TASK=ap0 INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_control_ops_upgrade.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
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
MINE = ("0066_wave7_grants_and_reads.sql", "0065_identity_functions.sql")   # rollback order
#: The only existing functions whose grants the set changes, each + infrx_lab_control:
#: 0066's SR-AP10-3 (its rollback revokes them) and 0064's judge/review doors (api-judge).
REGRANTED = {f"infrx.{n}(jsonb)" for n in (
    "lab_put_experiment", "lab_checkpoint_listing", "lab_list_datasets", "lab_evaluator",
    "lab_checkpoint_subscribe", "lab_checkpoint_decisions")}
REGRANTED_0064 = {"lab_judge_calibration(uuid,uuid,integer)",
                  "lab_judge_request_run(uuid,uuid,uuid,text)", "lab_review_feedback(jsonb)"}
#: (function, the earlier file whose body 0066's rollback restores)
REDEFINED = (("infrx.lab_experiments(jsonb)", "0043_lab_reads_and_proposals.sql"),
             ("infrx.control_op_cancel(jsonb)", "0060_control_operations.sql"))
DROPPED = {*(f"infrx.identity_{n}" for n in ("account", "user_by_email", "members",
                                             "grant_member", "revoke_member", "create_provider")),
           *(f"infrx.{n}" for n in ("lab_eval_catalog", "lab_external_runs_of",
                                    "lab_checkpoint_receipts_of", "lab_withdraw_access_grant"))}

_spec = importlib.util.spec_from_file_location(
    "infrx_migrate_0060", migrations.DIR.parents[3] / "apps" / "infrx-api" / "deploy" / "migrate.py")
assert _spec and _spec.loader
migrate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = migrate
_spec.loader.exec_module(migrate)


def rollback_sql(name: str) -> str:
    """The `-- rollback: ` lines of a file's header, the one source of its rollback."""
    lines = [line.removeprefix("-- rollback: ") for line in
             (migrations.DIR / name).read_text().splitlines() if line.startswith("-- rollback: ")]
    assert lines, name
    return "\n".join(lines)


def bodies(conn) -> dict[str, str]:
    """Each function's body, whitespace-normalized (a rollback line holds it on one line)."""
    return {fn: " ".join(src.split()) for fn, src in conn.execute(
        "select p.oid::regprocedure::text, p.prosrc from pg_proc p join pg_namespace n "
        "on n.oid = p.pronamespace where n.nspname in ('public', 'infrx')").fetchall()}


def source_body(name: str, fn: str) -> str:
    """`fn`'s body as `name` writes it, whitespace-normalized."""
    text = (migrations.DIR / name).read_text()
    start = text.index("$$", text.index(f"create or replace function {fn.split('(')[0]}("))
    return " ".join(text[start + 2:text.index("$$", start + 2)].split())


def new_tables(files) -> set[str]:
    return {f"infrx.{t}" for _, sql in files
            for t in re.findall(r"^create table if not exists infrx\.(\w+)", sql, re.M)}


def test_wave7_rehearses_over_hosted_history_reruns_rolls_back_and_forward(monkeypatch,
                                                                           capsys) -> None:
    everything = migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    number = {label: label[:4] for label, _ in everything if label[:4].isdigit()}
    early = tuple(f for f in everything if number.get(f[0], "0000") < "0027")
    hosted = tuple(f for f in everything if "0027" <= number.get(f[0], "0000") <= "0059")
    wave7 = tuple(f for f in everything if number.get(f[0], "") > "0059")
    labels = [label for label, _ in wave7]
    # 0062 (api-hosting) and 0067 (R271's late allocation) are admitted, never required
    assert all("0060" <= label[:4] <= "0067" for label in labels) and \
        {"0060_control_operations.sql", *MINE} <= set(labels), \
        f"R271: past 0059 only the wave-7 range 0060-0067: {labels}"
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
    before, at_0059 = d10.snapshot(conn), bodies(conn)

    monkeypatch.setenv(migrate.DSN_ENV, pgharness.dsn(DB))
    assert migrate.plan_command(migrations.DIR) == 0
    plan = capsys.readouterr().out
    assert [line.split()[1] for line in plan.splitlines() if line.startswith("pending:")] == \
        labels, plan
    expect = plan.rsplit("plan digest: ", 1)[1].strip()
    assert migrate.apply_command(migrations.DIR, expect) == 0
    assert conn.execute("select max(version) from supabase_migrations.schema_migrations"
                        ).fetchall() == [(labels[-1][:4],)]
    after = d10.snapshot(conn)

    added = new_tables(wave7)
    assert set(after["counts"]) - set(before["counts"]) == added
    assert {t: after["counts"][t] for t in added} == dict.fromkeys(added, 0)
    assert {t: n for t, n in after["counts"].items() if t not in added} == before["counts"], \
        "the wave-7 set changed a row count"
    assert (after["sums"], after["jobs"]) == (before["sums"], before["jobs"]), \
        "the wave-7 set changed money or a job"
    assert {k: v for k, v in after["acl"].items() if k in before["acl"]} == before["acl"], \
        "the wave-7 set changed an existing relation's grants"
    assert after["cols"] == before["cols"]
    moved = {k for k, v in after["fns"].items() if k in before["fns"] and v != before["fns"][k]}
    assert moved == REGRANTED | REGRANTED_0064, f"existing function grants moved: {sorted(moved)}"
    assert all("infrx_lab_control=X" in after["fns"][k][0] for k in moved)

    pgharness.apply(DB, wave7)
    assert d10.snapshot(conn) == after, "the wave-7 set is not re-runnable"
    for name in MINE:
        pgharness.apply(DB, ((f"rollback {name}", rollback_sql(name)),))
    back, back_bodies = d10.snapshot(conn), bodies(conn)
    assert back["counts"] == after["counts"] and back["acl"] == after["acl"]
    assert {k: v for k, v in back["fns"].items() if k in before["fns"]} == \
        {k: after["fns"][k] if k in REGRANTED_0064 else v for k, v in before["fns"].items()}, \
        "the ROLLBACK lines do not restore the existing functions' grants"
    gone = set(after["fns"]) - set(back["fns"])
    assert {f.split("(")[0] for f in gone} == DROPPED and set(back["fns"]) < set(after["fns"])
    for fn, name in REDEFINED:
        assert back_bodies[fn] == source_body(name, fn), f"{fn}: not {name}'s body"
    assert back_bodies["infrx.lab_experiments(jsonb)"] == at_0059["infrx.lab_experiments(jsonb)"]

    pgharness.apply(DB, tuple(f for f in wave7 if f[0] in MINE))
    assert d10.snapshot(conn) == after, "rolling forward again is not the same set"
    who = actor(provider=uid(), user=uid())
    started = asyncio.run(PgControlOps(connector(pgharness.dsn(DB))).start(
        "deployment.create", who, "k", input_hash({"b": 1})))
    assert started.operation.state == "queued" and not started.replayed
    conn.close()
    print(f"{labels} over {len(made)} seeded job states at 0059: {len(before['counts'])} "
          f"tables unchanged, +{len(added)} tables, {len(REGRANTED)} regrants; migrate.py "
          f"plan/apply; re-run; 0066+0065 rollback; forward")
