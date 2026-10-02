#!/usr/bin/env python3
"""WR-LC-HOSTED (lane lab-capture-2): `0057_trace_consent_read.sql` on real PostgreSQL, and its
SQL mutation list (R32/R40: needs Docker, skips visibly without it; a survivor fails the suite
and every check is named by a mutant).

`infrx.trace_consent(org, key)` is the gateway's consent read (`gateway.capture.ConsentSource`):
the key's own opt-in (`api_keys.trace_mode`) and its organization's consent head (the highest
`consent_history` version, revoked or not), for a key of that organization only. EXECUTE for
0021's dedicated runtime login and the platform role; nothing browser-facing, never PUBLIC.

World: `checks_admission.seed_admission` (orgs A/B and their keys). Every check rolls back.

    INFRX_D_TASK=t2f uv run --frozen pytest -q tests/d/test_code_mutants_lc2.py
"""
from __future__ import annotations

import sys

import psycopg
import pytest
from infrx.contracts.conformance import builders as b
from infrx.state import migrations

from . import checks_admission
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness

_reason = pgharness.unavailable()
DB = f"{pgharness.DATABASE}_lc2"
DB_MUT = f"{pgharness.DATABASE}_lc2mut"
FILE = "0057_trace_consent_read.sql"
RPC = "infrx.trace_consent(uuid, uuid)"
READ = "select * from infrx.trace_consent(%s, %s)"
seed = checks_admission.seed_admission


def rolled_back(check):
    def run(conn) -> str:
        with conn.transaction():
            out = check(conn)
            raise psycopg.Rollback()
        return out
    run.__name__, run.__doc__ = check.__name__, check.__doc__
    return run


def consent(conn, org: str, mode: str, *, revoked: bool = False) -> int:
    """A new consent head for `org` (0003's history: a new version, or its revocation)."""
    version = conn.execute("select coalesce(max(consent_version), 0) + 1 from "
                           "infrx.consent_history where org_id = %s", (org,)).fetchone()[0]
    conn.execute("insert into infrx.consent_history (org_id, consent_version, trace_mode, "
                 "content_retention_days, evaluation_consent, actor_principal, effective_at) "
                 "values (%s, %s, %s, 30, %s, 'owner@lc2', infrx.now() - interval '1 hour')",
                 (org, version, mode, mode == "full"))
    if revoked:
        conn.execute("update infrx.consent_history set revoked_at = infrx.now() "
                     "where org_id = %s and consent_version = %s", (org, version))
    return version


def opt_in(conn, key: str, mode: str | None) -> None:
    conn.execute("update public.api_keys set trace_mode = %s where id = %s", (mode, key))


def read_as(conn, role: str, org: str, key: str, sql: str = READ):
    """`trace_consent` as `role` (a stand-in for its login: `set local role`)."""
    with conn.transaction():
        pgharness.become(conn, role)
        rows = conn.execute(sql, (org, key)).fetchall()
        raise psycopg.Rollback()
    return rows


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_the_runtime_login_reads_consent_through_the_rpc(conn) -> str:
    """WR-LC-HOSTED: 0021's `infrx_runtime` (the dedicated gateway login) reads the key's opt-in
    and the org's head through the RPC alone (it still reads neither table), as the platform
    role does. Oracle: the grant dropped, or a SECURITY INVOKER body the login cannot run."""
    version = consent(conn, b.ORG_A, "full")
    opt_in(conn, b.KEY_A, "minimal")
    for role in ("infrx_runtime", "service_role"):
        try:
            rows = read_as(conn, role, b.ORG_A, b.KEY_A)
        except psycopg.errors.InsufficientPrivilege as refused:
            raise AssertionError(f"{role} cannot read consent: {refused}") from None
        assert [r[:3] for r in rows] == [("minimal", version, "full")], (role, rows)
    for table in ("infrx.consent_history", "public.api_keys"):
        try:
            read_as(conn, "infrx_runtime", b.ORG_A, b.KEY_A, f"select 1 from {table} "
                    "where %s::text is not null and %s::text is not null")
            raise AssertionError(f"the runtime login reads {table} directly")
        except psycopg.errors.InsufficientPrivilege:
            pass
    return "infrx_runtime and service_role read the head; the table stays closed"


def check_nothing_else_executes_it(conn) -> str:
    """DUR-RLS: no browser session, other platform role or PUBLIC executes the read (a
    consent head and a key's opt-in are the org's). Oracle: a grant to PUBLIC or a browser."""
    acl = conn.execute(f"select exists (select 1 from pg_proc p, aclexplode(p.proacl) a "
                       f"where p.oid = '{RPC}'::regprocedure and a.grantee = 0)").fetchone()[0]
    assert not acl, "PUBLIC executes trace_consent"
    reach = [role for role in ("anon", "authenticated", "infrx_monitor", "infrx_lab_control")
             if conn.execute("select has_function_privilege(%s, %s, 'execute')",
                             (role, RPC)).fetchone()[0]]
    assert not reach, f"these roles execute trace_consent: {reach}"
    return "PUBLIC, anon, authenticated, infrx_monitor and infrx_lab_control refused"


def check_it_is_a_definer_with_a_fixed_search_path(conn) -> str:
    """CMO-3: the read runs as its owner (SECURITY DEFINER) under a pinned search_path, so a
    caller's schema can never shadow `api_keys` or `consent_history` inside it. Oracle: an
    invoker body, or a definer whose search_path is the caller's."""
    definer, config = conn.execute("select prosecdef, coalesce(proconfig, '{}') from pg_proc "
                                   f"where oid = '{RPC}'::regprocedure").fetchone()
    assert definer, "trace_consent is not SECURITY DEFINER"
    paths = [c for c in config if c.startswith("search_path=")]
    assert paths == ["search_path=infrx, public, pg_temp"], config
    return f"definer; {paths[0]}"


@rolled_back
def check_a_key_answers_only_under_its_own_org(conn) -> str:
    """A key named under another organization reads nothing (its opt-in never answers for
    the asker's org). Oracle: the org predicate dropped - a read across orgs."""
    consent(conn, b.ORG_A, "full")
    consent(conn, b.ORG_B, "full")
    opt_in(conn, b.KEY_B, "full")
    assert read_as(conn, "infrx_runtime", b.ORG_A, b.KEY_B) == []
    assert len(read_as(conn, "infrx_runtime", b.ORG_B, b.KEY_B)) == 1
    return "ORG_B's key under ORG_A: no row"


@rolled_back
def check_the_head_is_the_newest_version_revoked_or_not(conn) -> str:
    """The head is the highest version: a newer, lower consent wins over an older one, and a
    revoked head is read revoked (off), never an older consent. Oracle: the oldest head, or
    the head chosen among unrevoked rows only."""
    opt_in(conn, b.KEY_A, "full")
    consent(conn, b.ORG_A, "full")
    newer = consent(conn, b.ORG_A, "minimal")
    [row] = read_as(conn, "infrx_runtime", b.ORG_A, b.KEY_A)
    assert row[:3] == ("full", newer, "minimal"), row
    revoked = consent(conn, b.ORG_A, "full", revoked=True)
    [row] = read_as(conn, "infrx_runtime", b.ORG_A, b.KEY_A)
    assert (row[1], row[6] is not None) == (revoked, True), row
    return "the newest head, revoked or not"


CHECKS = {c.__name__: c for c in (
    check_the_runtime_login_reads_consent_through_the_rpc, check_nothing_else_executes_it,
    check_it_is_a_definer_with_a_fixed_search_path, check_a_key_answers_only_under_its_own_org,
    check_the_head_is_the_newest_version_revoked_or_not)}
RUNTIME, ONLY, DEFINER, OWN_ORG, HEAD = CHECKS


def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    _s("lc2_runtime_grant_dropped",
       "grant execute on function infrx.trace_consent(uuid, uuid) to infrx_runtime;\n", "",
       RUNTIME, "the dedicated gateway login reads no consent: capture stays off hosted"),
    _s("lc2_security_invoker", "stable security definer", "stable security invoker", RUNTIME,
       "the runtime login runs the read without the tables' privileges: capture stays off"),
    _s("lc2_no_search_path",
       "stable security definer set search_path = infrx, public, pg_temp as",
       "stable security definer as", DEFINER,
       "a definer body resolves names through the caller's search_path: a shadowed table"),
    _s("lc2_key_opt_in_ignored", "  select k.trace_mode, c.consent_version",
       "  select 'full'::text, c.consent_version", RUNTIME,
       "a key that never opted in is captured at its org's mode"),
    _s("lc2_granted_to_public", "to infrx_runtime;\n", "to infrx_runtime, public;\n", ONLY,
       "any role executes the read (PUBLIC)"),
    _s("lc2_browser_reads", "to infrx_runtime;\n", "to infrx_runtime, authenticated;\n", ONLY,
       "a browser session reads every org's consent head by key id"),
    _s("lc2_key_of_any_org", "   where k.id = p_key and k.org_id = p_org", "   where k.id = p_key",
       OWN_ORG, "a key of another org answers for the asker's org: a read across orgs"),
    _s("lc2_oldest_head", "order by h.consent_version desc limit 1",
       "order by h.consent_version asc limit 1", HEAD,
       "a lowered or revoked consent never lands: the first one answers for ever"),
    _s("lc2_head_skips_revoked", "                         from infrx.consent_history h "
       "where h.org_id = k.org_id\n", "                         from infrx.consent_history h "
       "where h.org_id = k.org_id\n                          and h.revoked_at is null\n", HEAD,
       "a revocation falls back to the previous consent instead of off"),
)


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("name", list(CHECKS))
def test_trace_consent(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")


def test_the_list_is_well_formed() -> None:
    names = [m.name for m in SQL_MUTANTS]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL_MUTANTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    assert all(m.check in CHECKS for m in SQL_MUTANTS)
    assert not _d.superseded(SQL_MUTANTS)


def test_every_case_is_covered_by_a_mutant() -> None:
    uncovered = sorted(set(CHECKS) - {m.check for m in SQL_MUTANTS})
    assert not uncovered, f"cases no mutant can break: {uncovered}"


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", SQL_MUTANTS, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = d7.kill(mutant, DB_MUT, sys.modules[__name__])
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")
