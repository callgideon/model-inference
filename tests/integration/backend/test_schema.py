"""E3B.b/c on the real PostgreSQL: the schema's own backstops, on E2's stack.

The D2-D5 RPCs are stubs, so the durable *operations* cannot crash here yet (their drills
are PENDING in `test_drills.py`). What migrations 0001-0005 already enforce can be attacked
now, on the migrated `infrx_e2` database - the last line of defence when an operation is
wrong:

* duplicate settlement: a settled job's facts are immutable, a second terminal event, a
  second usage debit and a second hold for one request are all refused, and the wallet
  total moves by exactly the one debit it accepted (conservation through the trigger);
* foreign result access: a job cannot be given another tenant's media or outbox event, and
  the browser roles cannot read a single pilot relation.

Each backstop is a function that returns what the schema ACCEPTED (empty = all refused),
so the defect drills can prove the case detects a missing backstop: disable the trigger or
drop the index inside the same rolled-back transaction and the function must report it.
Rows come from D's own fixture (`tests/d/checks.seed_fixtures`), seeded inside a
transaction that is always rolled back - nothing here is committed to E2's database.
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stack                                            # noqa: E402,F401  (infrx on path)

import harness                                          # noqa: E402


def _d_checks():
    spec = importlib.util.spec_from_file_location(
        "e3b_d_checks", harness.API_ROOT / "tests" / "d" / "checks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


D = _d_checks()


class _Accepted(Exception):
    """Raised inside a savepoint to roll back a write the schema should have refused."""


def _attempt(conn, sql: str) -> bool:
    """True if the schema ACCEPTED the write (it is rolled back either way)."""
    import psycopg
    try:
        with conn.transaction():
            conn.execute(sql)
            raise _Accepted
    except _Accepted:
        return True
    except psycopg.Error:
        return False


@pytest.fixture
def seeded():
    """D's fixture rows in one transaction on E2's database, rolled back afterwards."""
    state = harness.load_state()
    if not state or not harness.owned_containers():
        pytest.skip(f"no {harness.PROJECT} stack: run `tests/integration/run.py --layer 3`")
    import psycopg
    conn = psycopg.connect(harness.pg_dsn())
    try:
        D.seed_fixtures(conn)
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _wallet(conn, org: str) -> Decimal:
    row = conn.execute("select ledger_total from infrx.wallets where org_id = %s",
                       (org,)).fetchone()
    return row[0] if row else Decimal(0)


def duplicate_settlements_accepted(conn) -> list[str]:
    """Every duplicate-settlement shape the schema accepted. The job is D's settled
    `JOB_TERMINAL` (debit 0.0005); its one usage debit is written first, as D5 will."""
    job, org = D.JOB_TERMINAL, D.ORG_A
    usage = (f"insert into public.credit_ledger (org_id, delta_usd, kind, reason, request_id)"
             f" values ('{org}', -0.00050000, 'usage', 'e3b settlement', '{job}')")
    before = _wallet(conn, org)
    conn.execute(usage)
    accepted = []
    if _wallet(conn, org) - before != Decimal("-0.00050000"):
        accepted.append("the wallet total did not move by exactly the one debit")
    shapes = {
        "rewrite a settled debit":
            f"update infrx.jobs set debit = 0.00010000 where request_id = '{job}'",
        "re-settle as another outcome":
            f"update infrx.jobs set settlement_state = 'released_free', debit = 0, "
            f"usage_certainty = null where request_id = '{job}'",
        "a second terminal event":
            f"insert into infrx.stream_chunks (job_id, generation, sequence, event_type, "
            f"payload, bytes, expires_at) values ('{job}', 1, 9, 'terminal', "
            f"'{{\"state\":\"succeeded\"}}'::jsonb, 30, '2026-09-21T01:00:00Z')",
        "a second hold for one request":
            f"insert into infrx.credit_holds (request_id, org_id, key_id, amount, state) "
            f"values ('{D.JOB_QUEUED}', '{org}', '{D.KEY_A}', 1.25000000, 'held')",
    }
    accepted += [name for name, sql in shapes.items() if _attempt(conn, sql)]
    # The second debit is NOT rolled back when accepted (the others are): conservation must
    # then see the wallet move twice, so a missing one-debit rule shows up in the money too.
    import psycopg
    try:
        with conn.transaction():
            conn.execute(usage)
        accepted.append("a second usage debit")
    except psycopg.Error:
        pass
    if _wallet(conn, org) - before != Decimal("-0.00050000"):
        accepted.append("the wallet total moved by more than the one accepted debit")
    return accepted


def foreign_access_accepted(conn) -> list[str]:
    """Every cross-tenant or browser-role access to pilot rows the schema allowed."""
    accepted = []
    if _attempt(conn, f"insert into infrx.job_media (job_id, org_id, handle, role) "
                      f"values ('{D.JOB_B}', '{D.ORG_B}', 'upl_a', 'source')"):
        accepted.append("org B's job was given org A's media")
    if _attempt(conn, f"insert into infrx.outbox (event_id, aggregate_id, org_id, kind, "
                      f"available_at) values (gen_random_uuid(), '{D.JOB_QUEUED}', "
                      f"'{D.ORG_B}', 'usage_projection', '2026-09-21T00:00:00Z')"):
        accepted.append("an outbox event put org A's job under org B")
    for role in ("anon", "authenticated"):
        for relation in ("jobs", "stream_chunks", "credit_holds", "outbox", "job_media",
                         "idempotency"):
            if _attempt(conn, f"set local role {role}; "
                              f"select 1 from infrx.{relation} limit 1"):
                accepted.append(f"{role} read infrx.{relation}")
    return accepted


# ------------------------------------------------------------------ the backstops hold

def test_e3b_db01_the_schema_refuses_every_duplicate_settlement(seeded):
    """DUR-SETTLE / CREDIT-SPEND on the real schema: one debit, one terminal event, one
    usage row and one hold per request; a settled job's facts cannot be rewritten; the
    wallet total moves by exactly the accepted debit and by nothing a refusal attempted."""
    assert duplicate_settlements_accepted(seeded) == []


def test_e3b_db02_the_schema_refuses_foreign_result_access(seeded):
    """Tenant isolation on the real schema (R55, R59-4): no cross-tenant media attachment or
    outbox event, and neither browser role can read any pilot relation."""
    assert foreign_access_accepted(seeded) == []


# ------------------------------------------------------------------ defect drills (E3B.c)

def test_e3b_db03_detects_a_missing_settlement_guard(seeded):
    """Intentional defect: `jobs_guard` disabled (a settled job becomes rewritable). The
    duplicate-settlement case must name exactly the rewrites the guard was stopping."""
    seeded.execute("alter table infrx.jobs disable trigger jobs_guard")
    accepted = duplicate_settlements_accepted(seeded)
    assert "rewrite a settled debit" in accepted, accepted


def test_e3b_db04_detects_a_missing_one_usage_debit_rule(seeded):
    """Intentional defect: the one-usage-debit-per-request index dropped (a second debit
    for one request). The case must report the second debit AND the wallet drift."""
    seeded.execute("drop index public.credit_ledger_one_usage_per_request")
    accepted = duplicate_settlements_accepted(seeded)
    assert "a second usage debit" in accepted, accepted
    assert "the wallet total moved by more than the one accepted debit" in accepted, accepted


def test_e3b_db05_detects_a_browser_grant_on_a_pilot_relation(seeded):
    """Intentional defect: `authenticated` granted SELECT on `infrx.jobs` (and usage of the
    schema). The foreign-access case must name that relation for that role."""
    seeded.execute("grant usage on schema infrx to authenticated; "
                   "grant select on infrx.jobs to authenticated")
    accepted = foreign_access_accepted(seeded)
    assert "authenticated read infrx.jobs" in accepted, accepted
