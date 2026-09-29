#!/usr/bin/env python3
"""SR-LCR-1 (lane lab-sql-lw8): `0056_lab_control_grants.sql` on real PostgreSQL - the Lab
control login's EXECUTE on the families' route halves - its checks and its SQL mutation list
(R32/R40; needs Docker, skips visibly without it). A survivor fails the suite, and every
check is named by a mutant.

    INFRX_D_TASK=l4 uv run --frozen pytest -q tests/d/test_code_mutants_lw8.py
"""
from __future__ import annotations

import psycopg
import pytest

from infrx.state import migrations

from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness

FILE = "0056_lab_control_grants.sql"
DB, DB_MUT = f"{pgharness.DATABASE}_lw8", f"{pgharness.DATABASE}_lw8mut"
_reason = pgharness.unavailable()
LOGIN = "infrx_lab_control"
#: 0043/0052's grants to the login, unchanged (the "13 of ~150" LCR-F1 found).
BEFORE = frozenset({
    "lab_control_endpoint", "lab_control_transition", "lab_control_propose", "lab_control_dev_key",
    "lab_control_publish", "lab_control_rollback", "lab_control_fund", "lab_control_events",
    "lab_control_reject", "lab_provider_memberships", "lab_access_grants",
    "lab_deployment_aggregates", "lab_control_operator"})
#: The route halves the control unit's families call (0056's header lists them per family).
#: 0041's lab_bound_samples / lab_tombstone_samples stay out: only lineage.select, tombstone
#: and backfill (the datasets worker, T3's hook) write them - never a route.
GRANTED = frozenset({
    "lab_resolve", "lab_publish", "lab_accessible_samples", "lab_import_job_enqueue",
    "lab_import_job", "lab_run_status", "lab_cancel_run", "lab_put_evaluator", "lab_create_run",
    "lab_receive_checkpoint", "lab_checkpoint_transition", "lab_checkpoint_receipt",
    "lab_label_append", "lab_label_events", "lab_external_run_get", "lab_external_run_move",
    "lab_run_reserve", "lab_run_release", "lab_run_settle", "lab_pipeline_note",
    "lab_pipeline_noted", "lab_checkpoint_record_event", "lab_checkpoint_subscriptions",
    "lab_judge_run", "lab_teacher_failures", "lab_releases_in", "lab_release",
    "lab_release_decisions", "lab_release_proposals", "lab_propose_release",
    "lab_blocked_samples", "lab_permitted_samples",
    # WR-LW8-R3: the teacher-batch approval's in-request submission (LAB_TEACHERS on)
    "lab_teacher_reserve", "lab_judge_begin_submit", "lab_teacher_record_sent",
    "lab_judge_release", "lab_judge_quarantine", "lab_judge_record_submission"})
#: One worker-only claim: an import job is claimed by the I5 datasets pool, never a route.
WORKER_CLAIM = "lab_import_job_claim"


def _holders(conn, names) -> dict[str, set[str]]:
    rows = conn.execute("""
        select p.proname, r.rolname from pg_proc p
          join pg_namespace n on n.oid = p.pronamespace and n.nspname = 'infrx'
          cross join pg_roles r
         where p.proname = any(%s)
           and r.rolname in ('anon', 'authenticated', 'service_role', 'infrx_runtime', %s)
           and has_function_privilege(r.oid, p.oid, 'execute')""",
                        (sorted(names), LOGIN)).fetchall()
    held: dict[str, set[str]] = {name: set() for name in names}
    for name, role in rows:
        held[name].add(role)
    return held


# ----------------------------------------------------------------------------- checks
def check_the_control_login_holds_exactly_the_route_halves(conn) -> str:
    """LCR-F1: the login executes 0043/0052's functions and 0056's route halves - no fewer
    (a family answers 503 on the unit), no more (a worker's claim or another RPC)."""
    held = {name for (name,) in conn.execute("""
        select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
         where n.nspname = 'infrx' and p.proname like 'lab\\_%%'
           and has_function_privilege(%s, p.oid, 'execute')""", (LOGIN,)).fetchall()}
    assert held == BEFORE | GRANTED, (f"missing {sorted((BEFORE | GRANTED) - held)}, "
                                      f"extra {sorted(held - BEFORE - GRANTED)}")
    return f"{LOGIN} executes {len(held)} infrx.lab_* functions"


def check_browser_roles_and_the_runtime_never_gain_execute(conn) -> str:
    """R237 / DUR-RLS: 0056's functions are executed by the platform role (0004's default,
    unchanged) and the Lab login only - never PUBLIC, anon, authenticated or the App
    runtime's login."""
    held = _holders(conn, GRANTED)
    wrong = {name: sorted(roles) for name, roles in held.items()
             if roles != {"service_role", LOGIN}}
    assert not wrong, f"EXECUTE held by other roles: {wrong}"
    return f"{len(held)} functions: service_role + {LOGIN} only"


def check_a_worker_claim_stays_refused_to_the_control_login(conn) -> str:
    """A route-only login never claims an import job (the I5 pool's work): 42501."""
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(f"set local role {LOGIN}")
            conn.execute(f"select infrx.{WORKER_CLAIM}('{{}}'::jsonb)")
            refused = None
    except psycopg.errors.InsufficientPrivilege as error:
        refused = error.sqlstate
    assert refused == "42501", f"{LOGIN} reached infrx.{WORKER_CLAIM}"
    return f"{WORKER_CLAIM}: 42501"


CHECKS = {c.__name__: c for c in (
    check_the_control_login_holds_exactly_the_route_halves,
    check_browser_roles_and_the_runtime_never_gain_execute,
    check_a_worker_claim_stays_refused_to_the_control_login)}
HOLDS, BROWSER, WORKER = CHECKS


def seed(conn) -> None:
    """No rows: every check reads the catalog or is refused before any row."""


# ----------------------------------------------------------------------------- mutants
def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    _s("lw8_drop_resolve", "  infrx.lab_resolve(jsonb), infrx.lab_publish(jsonb),",
       "  infrx.lab_publish(jsonb),", HOLDS,
       "datasets, pipelines and releases answer 503 on the unit (LCR-F1 back)"),
    _s("lw8_drop_noted", "  infrx.lab_pipeline_note(jsonb), infrx.lab_pipeline_noted(jsonb),",
       "  infrx.lab_pipeline_note(jsonb),", HOLDS,
       "a checkpoint's page answers 503 on the unit (its eligibility note)"),
    _s("lw8_drop_propose", "infrx.lab_release_proposals(jsonb), infrx.lab_propose_release(jsonb)",
       "infrx.lab_release_proposals(jsonb)", HOLDS,
       "a provider's release proposal answers 503 on the unit"),
    _s("lw8_drop_permitted",
       "  infrx.lab_blocked_samples(jsonb), infrx.lab_permitted_samples(jsonb),",
       "  infrx.lab_blocked_samples(jsonb),", HOLDS,
       "a version's derive/export and every label import answer 503 on the unit (0-LW8-R1)"),
    _s("lw8_drop_teacher_reserve",
       "  infrx.lab_teacher_reserve(jsonb), infrx.lab_judge_begin_submit(jsonb),",
       "  infrx.lab_judge_begin_submit(jsonb),", HOLDS,
       "an administrator's teacher-batch approval answers 503 on the unit (WR-LW8-R3)"),
    _s("lw8_grant_worker_claim", "infrx.lab_import_job(jsonb),",
       f"infrx.lab_import_job(jsonb), infrx.{WORKER_CLAIM}(jsonb),", WORKER,
       "the route login claims the datasets pool's import jobs"),
    _s("lw8_grant_to_public", "  to infrx_lab_control;", "  to public;", BROWSER,
       "every browser session executes the Lab's D7/D8/D9 functions"),
    _s("lw8_grant_to_authenticated", "  to infrx_lab_control;",
       "  to infrx_lab_control, authenticated;", BROWSER,
       "a signed-in browser writes Lab records past the routes' membership checks"),
    _s("lw8_grant_to_runtime", "  to infrx_lab_control;", "  to infrx_lab_control, infrx_runtime;",
       BROWSER, "the App runtime's login reaches the Lab families (R237)"),
)


def kill(mutant) -> tuple[str, str]:
    import sys
    return d7.kill(mutant, DB_MUT, sys.modules[__name__])


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    if _reason is not None:
        pytest.skip(f"task-local PostgreSQL unavailable: {_reason}")
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_lw8_grants(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")


def test_the_lists_are_well_formed() -> None:
    names = [m.name for m in SQL_MUTANTS]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL_MUTANTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    assert all(m.check in CHECKS for m in SQL_MUTANTS)
    assert not BEFORE & GRANTED and WORKER_CLAIM not in BEFORE | GRANTED


def test_no_mutant_anchors_in_a_superseded_function_body() -> None:
    found = _d.superseded(SQL_MUTANTS)
    assert not found, found


def test_every_case_is_covered_by_a_mutant() -> None:
    uncovered = sorted(set(CHECKS) - {m.check for m in SQL_MUTANTS})
    assert not uncovered, f"cases no mutant can break: {uncovered}"


@pytest.mark.skipif(_reason is not None, reason=f"task-local PostgreSQL unavailable: {_reason}")
@pytest.mark.parametrize("mutant", SQL_MUTANTS, ids=lambda m: m.name)
def test_sql_mutant_is_killed(mutant) -> None:
    outcome, detail = kill(mutant)
    assert outcome == _d.KILLED, (f"{mutant.name} was {outcome} by {mutant.check}: {detail}. "
                                  f"In production: {mutant.why}")
    print(f"{mutant.name}: {outcome} -> {detail}")
