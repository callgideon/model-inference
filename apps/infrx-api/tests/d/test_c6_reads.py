#!/usr/bin/env python3
"""Composition batch 6: `0053_lab_composition_reads.sql` on real PostgreSQL - the reads the
composed Lab surfaces and worker roles need (WR-R4-2's decisions listing), composed with
their stores.

World: test_d9_rollout's (test_d7_lab_data's role matrix and records) and test_d9_release's
launches. Each `check_*` is the check a mutant in `code_mutants_c6.py` must break.

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/d/test_c6_reads.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_rollout import PgReleaseStore

from . import checks_credit as cc
from . import pgharness
from . import test_d9_release as d9r
from . import test_d9_rollout as d9

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_c6"
NEMO, OTHER, uid, ok, t = d9.NEMO, d9.OTHER, d9.uid, d9.ok, d9.t
RPCS = ("lab_release_decisions",)
seed = d9.seed


# ----------------------------------------------------------------------------- checks
@d9.rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session executes 0053's reads; the platform role does."""
    probes = [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached 0053: {reached}"
    got = cc.refused_as(conn, "service", "select infrx.lab_release_decisions("
                        f"'{{\"provider_org_id\": \"{NEMO}\"}}')")
    assert got is None, f"the platform role cannot read: {got}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused"


def check_decisions_are_the_providers_own_oldest_first(conn) -> str:
    """WR-R4-2: every decision of the provider's releases (a start is none), oldest first,
    with its reasons, evidence and decider; another provider's never appears. Commits (the
    store reads on its own connection): NEMO's rows are this check's alone (tags 60-62)."""
    first = d9r.launch(conn, 61, endpoint=uid(61, 0xe1))    # the later policy id, decided first
    second = d9r.launch(conn, 60, endpoint=uid(60, 0xe0))
    quiet = d9r.launch(conn, 63, endpoint=uid(63, 0xe3))
    ok(conn, "lab_release_transition", d9r.move(first, 1, "rolled_back",
                                                d9r.decision(first, "rollback", ["run:x"]),
                                                reasons=("error_rate", "latency")))
    t.advance(conn, 5)                                      # the seed's clock is frozen
    ok(conn, "lab_release_transition", d9r.move(second, 1, "rolled_back",
                                                d9r.decision(second)))
    with conn.transaction():
        other = t.publish(conn, d9.policy(uid(62, 0xb0), endpoint=uid(62, 0xe2),
                                          provider=OTHER), provider=OTHER)
    ok(conn, "lab_release_start", {"provider_org_id": OTHER, "policy_ref": other,
                                   "plan_digest": d9r.PLAN, "decided_by": d9r.USER,
                                   "reason": "x"})
    ok(conn, "lab_release_transition", d9r.move(other, 1, "rolled_back",
                                                d9r.decision(other, provider=OTHER)))
    rows = ok(conn, "lab_release_decisions", {"provider_org_id": NEMO})
    mine = [r for r in rows if r["policy_ref"] in (first, second, quiet, other)]
    assert [r["policy_ref"] for r in mine] == [first, second], rows   # no start, no OTHER
    row = mine[0]
    assert (row["decision"], row["reasons"], row["evidence_refs"], row["decided_by"]) == \
        ("rollback", ["error_rate", "latency"], ["run:x"], d9r.USER), row
    assert row["decided_at"] < mine[1]["decided_at"], mine
    assert ok(conn, "lab_release_decisions", {"provider_org_id": uid(99, 0x99)}) == []

    async def typed():
        return await PgReleaseStore(connector(pgharness.dsn(conn.info.dbname))).decisions(
            provider_org_id=OTHER)
    assert [r["policy_ref"] for r in asyncio.run(typed())] == [other]
    return "own provider only, decisions only, oldest first, [] for none; the store reads it"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing,
    check_decisions_are_the_providers_own_oldest_first)}


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_c6_reads(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
