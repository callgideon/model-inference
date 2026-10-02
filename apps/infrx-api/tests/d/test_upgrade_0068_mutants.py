#!/usr/bin/env python3
"""AP-00 (lane api-schema-3, wave 7 batch 3, R271): `0068_wave7_followups.sql` on real
PostgreSQL over D7's world (NemoStation, OTHER, L2's grantors, the dev endpoint) - SR-AP06-1
(the dev-key listing, the one-way revocation and the dev-wallet read on the control login),
SR-AP10C-1 (the datasets worker's own grant set, `infrx_lab_datasets`), the E4C database
findings (register row 94: the content-scrub sweep's partial index and the
`journal_bytes_charged()` body with no OR-join) - each check, the SQL mutation list that must
break it (R32/R40; needs Docker, skips visibly without it; plain PostgreSQL and
`INFRX_D1_IMAGE=supabase`), and the guards. The whole-set R151 rehearsal (0060-0068 over
hosted history) is `test_control_ops_upgrade.py`.

    INFRX_MUTANTS=all INFRX_D_TASK=ap0 uv run --frozen pytest -q tests/d/test_upgrade_0068_mutants.py
"""
from __future__ import annotations

import psycopg
import pytest
from psycopg.types.json import Jsonb

from infrx.state import migrations
from infrx.state.jobstore import domain_error

from . import checks_credit as cc
from . import code_mutants_d7 as d7
from . import migration_mutants as _d
from . import pgharness
from . import test_d7_lab_data as lab
from . import test_l2sql_access as l2

FILE = "0068_wave7_followups.sql"
DB, DB_MUT = f"{pgharness.DATABASE}_0068", f"{pgharness.DATABASE}_0068mut"
_reason = pgharness.unavailable()
CONTROL, DATASETS = "infrx_lab_control", "infrx_lab_datasets"
OTHERS = ("anon", "authenticated", "infrx_runtime", "infrx_monitor")
NEMO, OTHER, DEV = l2.NEMO, l2.OTHER, l2.DEV
DOORS = tuple(f"infrx.{n}(jsonb)" for n in ("lab_control_dev_keys",
                                             "lab_control_revoke_dev_key",
                                             "lab_control_dev_wallet"))


def _uid(n: int) -> str:
    return f"e0680000-0000-4000-8000-{n:012d}"


def as_role(conn, role: str, sql: str, params=(), *, keep: bool = False):
    """`sql` as `role` in one savepoint, rolled back unless `keep`: its rows, or the failure
    as (code, message) - the domain code of a refusal, the SQLSTATE otherwise. The harness
    login takes SET on the role inside the caller's transaction (0065's `as_login`: on the
    Supabase image `postgres` is no superuser and holds the role only WITH ADMIN)."""
    try:
        with conn.transaction(force_rollback=not keep):
            conn.execute(f"grant {role} to current_user with inherit false, set true")
            conn.execute(f"set local role {role}")
            rows = conn.execute(sql, params).fetchall()
            conn.execute("reset role")
            return rows
    except psycopg.Error as error:
        return getattr(domain_error(error), "code", None) or error.sqlstate, \
            error.diag.message_primary


def door(conn, fn: str, args: dict, *, role: str = CONTROL, keep: bool = False):
    """The door's one value as `role`, or its refusal (code, message)."""
    got = as_role(conn, role, f"select infrx.{fn}(%s)", (Jsonb(args),), keep=keep)
    return got[0][0] if isinstance(got, list) else got


def _can(conn, role: str, fn: str) -> bool:
    return conn.execute("select has_function_privilege(%s, %s, 'execute')",
                        (role, fn)).fetchone()[0]


def dev_key(conn, n: int, endpoint: str = cc.DEV_ENDPOINT, provider: str = NEMO) -> str:
    """A provider_dev key issued through 0032's door, as the platform does it."""
    return l2.ok(conn, "lab_control_dev_key", {
        "provider_org_id": provider, "endpoint_id": endpoint, "user_id": DEV,
        "key_hash": f"{n:064x}", "prefix": f"sk-dev-{n:02d}", "name": f"key {n}"})["key_id"]


# ------------------------------------------------------------------------ SR-AP06-1
@l2.rolled_back
def check_the_dev_key_listing_is_the_endpoints_own(conn) -> str:
    """SR-AP06-1: the control login lists the keys of the provider's own dev endpoint - id,
    endpoint, name, prefix, created/revoked instants, never the hash - oldest first; a key of
    the provider's second dev endpoint is not one; another provider naming the endpoint, or
    the provider naming a production endpoint, is `not_found`."""
    first, second = dev_key(conn, 1), dev_key(conn, 2)
    conn.execute("update public.api_keys set revoked_at = now() where id = %s", (second,))
    conn.execute("update public.api_keys set created_at = now() - interval '1 minute' "
                 "where id = %s", (first,))           # one transaction: one now()
    elsewhere = l2.ok(conn, "lab_control_endpoint", {"provider_org_id": NEMO, "name": "dev-2",
                                                     "environment": "dev", "actor": DEV})
    dev_key(conn, 5, endpoint=elsewhere["endpoint_id"])
    got = door(conn, "lab_control_dev_keys", {"provider_org_id": NEMO,
                                              "endpoint_id": cc.DEV_ENDPOINT})
    assert isinstance(got, list), got
    assert [(k["key_id"], k["endpoint_id"], k["name"], k["prefix"], k["revoked_at"] is None)
            for k in got] == [(first, cc.DEV_ENDPOINT, "key 1", "sk-dev-01", True),
                              (second, cc.DEV_ENDPOINT, "key 2", "sk-dev-02", False)], got
    assert all(set(k) == {"key_id", "endpoint_id", "name", "prefix", "created_at",
                          "revoked_at"} for k in got), got
    for provider, endpoint in ((OTHER, cc.DEV_ENDPOINT), (NEMO, cc.PROD_ENDPOINT)):
        refused = door(conn, "lab_control_dev_keys", {"provider_org_id": provider,
                                                      "endpoint_id": endpoint})
        assert isinstance(refused, tuple) and refused[0] == "not_found", (provider, endpoint,
                                                                         refused)
    return "two keys of NEMO's dev endpoint, oldest first; OTHER / the prod endpoint not_found"


@l2.rolled_back
def check_a_dev_key_revocation_is_one_way_and_audited_once(conn) -> str:
    """SR-AP06-1: the control login revokes a key of the provider's dev endpoint through 0009's
    `revoke_key` (its audit entry) and 0032's control event `lab_dev_key_revoke`, once; the
    same call again answers the first `revoked_at` and writes nothing; another provider's
    endpoint or a key of another endpoint is `not_found` and revokes nothing; one
    Idempotency-Key reused for a second key (another tenant's scope) still revokes it."""
    first, second = dev_key(conn, 3), dev_key(conn, 4)
    args = {"provider_org_id": NEMO, "endpoint_id": cc.DEV_ENDPOINT, "key_id": first,
            "actor": DEV, "idempotency_key": "rv-1"}
    for stranger in ({**args, "provider_org_id": OTHER},
                     {**args, "endpoint_id": cc.PROD_ENDPOINT}):
        refused = door(conn, "lab_control_revoke_dev_key", stranger, keep=True)
        assert isinstance(refused, tuple) and refused[0] == "not_found", (stranger, refused)
    assert conn.execute("select revoked_at from public.api_keys where id = %s",
                        (first,)).fetchone()[0] is None, "a refused revocation revoked"
    done = door(conn, "lab_control_revoke_dev_key", args, keep=True)
    assert isinstance(done, dict) and done["key_id"] == first and done["revoked_at"], done
    again = door(conn, "lab_control_revoke_dev_key", args, keep=True)
    assert again == done, (again, done)
    other = door(conn, "lab_control_revoke_dev_key", {**args, "key_id": second}, keep=True)
    assert isinstance(other, dict) and other["revoked_at"], other
    stored = conn.execute("select id::text, revoked_at is not null from public.api_keys "
                          "where id in (%s, %s) order by created_at", (first, second)).fetchall()
    assert stored == [(first, True), (second, True)], stored
    events = conn.execute("select subject, actor from infrx.lab_control_events where action = "
                          "'lab_dev_key_revoke' order by event_id").fetchall()
    assert events == [(first, DEV), (second, DEV)], events
    audit = conn.execute("select after->>'key_id' from infrx.audit_entries where action = "
                         "'admin_key_revoke'").fetchall()
    assert sorted(audit) == sorted([(first,), (second,)]), audit
    return "revoked once (event + audit), replay answers it, foreign scopes not_found"


@l2.rolled_back
def check_the_dev_wallet_reads_the_providers_balance(conn) -> str:
    """SR-AP06-1: the provider's dev wallet as the control login reads it - not opened and a
    zero balance before the first allocation (OTHER); after the operator's allocation (0032's
    `lab_control_fund`) opened, its AVAILABLE CREDIT (ledger - reserved: NEMO's 500 less a
    held dev job) as exact text; one provider's allocation is not another's."""
    zero = door(conn, "lab_control_dev_wallet", {"provider_org_id": OTHER})
    assert zero == {"opened": False, "balance": "0.00000000"}, zero
    rid, org = _uid(3), cc.personal_org(conn, cc.PROVIDER_DEV_USER)
    conn.execute(cc._provider_dev_key(org) + "; " + cc.credit_job(
        rid, "job_0068", org, cc.PROVIDER_WALLET, card=cc.DEV_CARD,
        deployment=cc.DEV_DEPLOYMENT, key=cc.PDEV_KEY) + "; " + cc.hold(
        rid, org, cc.PROVIDER_WALLET, amount="10.01440000", card=cc.DEV_CARD))
    nemo = {"opened": True, "balance": "489.98560000"}
    assert door(conn, "lab_control_dev_wallet", {"provider_org_id": NEMO}) == nemo
    l2.ok(conn, "lab_control_fund", {"provider_org_id": OTHER, "amount": "25.5", "actor": "ops",
                                     "operation_id": _uid(2), "reason": "budget"})
    got = door(conn, "lab_control_dev_wallet", {"provider_org_id": OTHER})
    assert got == {"opened": True, "balance": "25.50000000"}, got
    assert door(conn, "lab_control_dev_wallet", {"provider_org_id": NEMO}) == nemo
    return "OTHER unopened 0 -> opened 25.50000000; NEMO 500 - 10.0144 held"


def check_the_dev_doors_are_the_control_logins(conn) -> str:
    """SR-AP06-1 (R271): the control login and the platform role execute the three doors; no
    browser role, the gateway's or the monitor's login, nor PUBLIC, does."""
    missing = [f"{r} {f}" for r in ("service_role", CONTROL) for f in DOORS if not _can(conn, r, f)]
    reached = [f"{r} {f}" for r in OTHERS for f in DOORS if _can(conn, r, f)]
    public = [f for f in DOORS if conn.execute(
        "select coalesce(proacl::text, '') ~ '(^|[{,])=X' from pg_proc "
        "where oid = %s::regprocedure", (f,)).fetchone()[0]]
    assert not missing and not reached and not public, (missing, reached, public)
    return f"{len(DOORS)} doors: service_role + {CONTROL} only"


CHECKS = {c.__name__: c for c in (
    check_the_dev_key_listing_is_the_endpoints_own,
    check_a_dev_key_revocation_is_one_way_and_audited_once,
    check_the_dev_wallet_reads_the_providers_balance,
    check_the_dev_doors_are_the_control_logins)}
KEYS, REVOKE, WALLET, DOOR_GRANTS = CHECKS


def seed(conn) -> None:
    lab.seed(conn)


# ----------------------------------------------------------------------------- mutants
def _s(name, old, new, check, why, **kw):
    return _d.Mutant(name, FILE, old, new, "lab", check, why, **kw)


SQL_MUTANTS = (
    # --- SR-AP06-1: the listing
    _s("ap0068_keys_any_endpoint", "\n     and k.endpoint_id = (p_args->>'endpoint_id')::uuid);",
       ");", KEYS, "a dev endpoint lists the provider's other endpoints' keys"),
    _s("ap0068_keys_foreign_endpoint", "     and provider_org_id = (p_args->>'provider_org_id')"
       "::uuid and environment = 'dev') then\n    perform infrx.refuse('not_found', 'no such dev "
       "endpoint for this provider');\n  end if;\n  return",
       "     and environment = 'dev') then\n    perform infrx.refuse('not_found', 'no such dev "
       "endpoint for this provider');\n  end if;\n  return", KEYS,
       "a provider lists another provider's dev keys by naming its endpoint"),
    _s("ap0068_keys_hash_listed", "'prefix', k.prefix, 'created_at'",
       "'prefix', k.prefix, 'key_hash', k.key_hash, 'created_at'", KEYS,
       "the credential's hash leaves the database", occurrences=2),
    # --- SR-AP06-1: the revocation
    _s("ap0068_revoke_any_provider", "\n     and a.provider_org_id = (p_args->>'provider_org_id')"
       "::uuid\n     and a.endpoint_id = (p_args->>'endpoint_id')::uuid\n   for update;",
       "\n   for update;", REVOKE, "a provider revokes another provider's credential"),
    _s("ap0068_revoke_audits_every_call", "  if k.revoked_at is null then\n    perform "
       "infrx.lab_control_audit(", "  if true then\n    perform infrx.lab_control_audit(", REVOKE,
       "a replayed revocation writes another control event (history noise)"),
    _s("ap0068_revoke_raw_idempotency_key", "'lab_dev_key_revoke:' || k.id || ':' || "
       "(p_args->>'idempotency_key')", "p_args->>'idempotency_key'", REVOKE,
       "two tenants' revocations under one Idempotency-Key: the second is a 500"),
    _s("ap0068_revoke_event_refused", "'lab_rollback', 'lab_fund', 'lab_dev_key_revoke'))",
       "'lab_rollback', 'lab_fund'))", REVOKE,
       "every dev-key revocation fails on 0032's action check (503)"),
    # --- SR-AP06-1: the wallet
    _s("ap0068_wallet_ledger_not_available", "coalesce(w.available, 0)",
       "coalesce(w.ledger_total, 0)", WALLET,
       "the dev wallet shows reserved CREDIT as spendable"),
    _s("ap0068_wallet_any_provider", "    on w.kind = 'provider_dev' and w.owner_provider_org_id "
       "= (p_args->>'provider_org_id')::uuid", "    on w.kind = 'provider_dev'", WALLET,
       "a provider reads another provider's balance"),
    _s("ap0068_wallet_number", "coalesce(w.available, 0)::numeric(20,8)::text",
       "coalesce(w.available, 0)::numeric(20,8)", WALLET,
       "the balance crosses JSON as a float (CREDIT is exact text)"),
    # --- SR-AP06-1: grants
    _s("ap0068_doors_to_authenticated", "  infrx.lab_control_dev_wallet(jsonb) to "
       "infrx_lab_control;", "  infrx.lab_control_dev_wallet(jsonb) to infrx_lab_control, "
       "authenticated;", DOOR_GRANTS, "a browser session lists and revokes any provider's keys"),
    _s("ap0068_doors_refused", "grant execute on function infrx.lab_control_dev_keys(jsonb), "
       "infrx.lab_control_revoke_dev_key(jsonb),\n  infrx.lab_control_dev_wallet(jsonb) to "
       "infrx_lab_control;", "", DOOR_GRANTS, "the dev-key and wallet routes answer 503"),
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
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_0068_checks(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")


def test_the_lists_are_well_formed() -> None:
    names = [m.name for m in SQL_MUTANTS]
    assert len(set(names)) == len(names), "duplicate mutant names"
    stale = [f"{m.name}: {_d.anchor_count(m)}" for m in SQL_MUTANTS
             if _d.anchor_count(m) != m.occurrences]
    assert not stale, f"misdeclared SQL anchors: {stale}"
    assert all(m.check in CHECKS for m in SQL_MUTANTS)


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
