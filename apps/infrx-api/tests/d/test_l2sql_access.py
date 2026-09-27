#!/usr/bin/env python3
"""L2-SQL (wave-5 LW1, lab-sql; 09-amendment-workstreams §L2, R150/R151): provider capabilities
and purpose-specific data-access grants, on real PostgreSQL (LAB-ACCESS, the SQL half).

Role matrix (the brief's): two consumers (CONSUMER_1's and CONSUMER_2's personal
organizations), two providers (NEMO with a developer and an administrator; OTHER), and one
user in both products (CONSUMER_2 owns a consumer organization and is an OTHER developer).
Each `check_*` takes a seeded connection, rolls back what it did, and is the check a mutant in
`code_mutants_l2sql.py` must break.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_l2sql_access.py
"""
from __future__ import annotations

import json

import psycopg
import pytest
from infrx.contracts.conformance import builders as b
from infrx.state import migrations
from infrx.state.jobstore import domain_error
from psycopg.types.json import Jsonb

from . import checks_admission as ca
from . import checks_credit as cc
from . import pgharness

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_l2sql"

NEMO, OTHER, MODEL = cc.NEMO, cc.OTHER_PROVIDER, cc.MODEL
OTHER_MODEL = "d0000009-0000-4000-8000-000000000009"
DEV, ADMIN, BOTH, C1 = cc.PROVIDER_DEV_USER, cc.PROVIDER_ADMIN_USER, cc.CONSUMER_2, cc.CONSUMER_1
VIEWER = "c1000000-0000-4000-8000-00000000000a"
RPCS = ("lab_grant_data_access", "lab_revoke_data_access", "lab_data_access_allowed",
        "lab_data_grants", "lab_deployment_aggregates")
TABLES = ("infrx.provider_role_capabilities", "infrx.lab_data_grants")


def seed(conn) -> None:
    """seed_admission's world plus OTHER's model, CONSUMER_2 as an OTHER developer and a
    NEMO viewer."""
    ca.seed_admission(conn)
    conn.execute("insert into auth.users (id, email) values (%s, 'viewer@example.com')",
                 (VIEWER,))
    conn.execute(
        "insert into public.models (id, name, provider, description, status, base_url, "
        "served_model, input_usd_per_m, output_usd_per_m, context_tokens, input_modalities, "
        "output_modalities, model_uuid, provider_org_id) values ('other/model', 'o', 'o', "
        "'o', 'live', 'https://o.example', 'o', 1, 1, 1024, '{text}', '{text}', %s, %s)",
        (OTHER_MODEL, OTHER))
    conn.execute("insert into infrx.provider_memberships (provider_org_id, user_id, role, "
                 "granted_by) values (%s, %s, 'developer', 'ops'), (%s, %s, 'viewer', 'ops')",
                 (OTHER, BOTH, NEMO, VIEWER))


def org(conn, user: str) -> str:
    return cc.personal_org(conn, user)


def call(conn, name: str, args: dict):
    return conn.execute(f"select infrx.{name}(%s)", (Jsonb(args),)).fetchone()[0]


def refusal(conn, name: str, args: dict) -> str | None:
    """The error code the call refuses with (in a savepoint), or None."""
    try:
        with conn.transaction():
            call(conn, name, args)
    except psycopg.Error as failed:
        mapped = domain_error(failed)
        return getattr(mapped, "code", f"sqlstate {failed.sqlstate}")
    return None


def grant(conn, owner: str = C1, provider: str = NEMO, model: str = MODEL,
          category: str = "traces", purpose: str = "evaluation", ttl_s: int = 3600) -> dict:
    return call(conn, "lab_grant_data_access", {
        "actor_user_id": owner, "source_org_id": org(conn, owner), "provider_org_id": provider,
        "model_id": model, "data_category": category, "purpose": purpose,
        "expires_at": conn.execute("select infrx.now() + make_interval(secs => %s)",
                                   (ttl_s,)).fetchone()[0].isoformat()})


def allowed(conn, user: str, source_owner: str = C1, provider: str = NEMO, model: str = MODEL,
            category: str = "traces", purpose: str = "evaluation") -> bool:
    return call(conn, "lab_data_access_allowed", {
        "user_id": user, "provider_org_id": provider, "source_org_id": org(conn, source_owner),
        "model_id": model, "data_category": category, "purpose": purpose})


def rolled_back(check):
    """Run `check(conn)` and roll back everything it wrote, clock moves included."""
    def run(conn) -> str:
        out = None
        with conn.transaction():
            out = check(conn)
            raise psycopg.Rollback()
        return out
    run.__name__ = check.__name__
    run.__doc__ = check.__doc__
    return run


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session (anon, consumer, provider member/admin, operator) reads or
    writes the Lab access relations or executes their RPCs; the platform role reads the
    relations but writes a grant only through the RPC (whose owner checks it bypasses
    otherwise)."""
    probes = [f"select count(*) from {t}" for t in TABLES]
    probes += ["insert into infrx.lab_data_grants (source_org_id) values (gen_random_uuid())",
               "update infrx.lab_data_grants set revoked_at = now()",
               "insert into infrx.provider_role_capabilities values ('viewer', 'use_granted_data')"]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    for session in cc.BROWSER:
        for sql in probes:
            got = cc.refused_as(conn, session, sql)
            assert got is not None and got.startswith("42501"), \
                f"{session} ran `{sql[:60]}`: {got or 'allowed'}"
    for sql in probes[:2]:
        assert cc.refused_as(conn, "service", sql) is None, f"service cannot `{sql}`"
    for sql in probes[2:5]:
        got = cc.refused_as(conn, "service", sql)
        assert got is not None and got.startswith("42501"), f"service wrote directly: {sql}"
    # and not merely by the closed schema: no browser role holds EXECUTE on the RPCs
    for name in RPCS:
        acl = conn.execute("select coalesce(proacl::text, '') from pg_proc where oid = "
                           "%s::regprocedure", (f"infrx.{name}(jsonb)",)).fetchone()[0]
        grantees = {item.split("=", 1)[0] for item in acl.strip("{}").split(",") if item}
        assert grantees <= {"postgres", "service_role"} and "service_role" in grantees, \
            f"infrx.{name}: {acl}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused; service reads"


@rolled_back
def check_a_grant_is_purpose_model_and_recipient_specific(conn) -> str:
    """LAB-ACCESS: one grant (C1's traces, NEMO's model, evaluation) opens exactly that; a
    role alone opens nothing, and the operator bit is not a provider role."""
    assert not allowed(conn, DEV), "a provider role alone authorized data reuse"
    row = grant(conn)
    assert row["provider_org_id"] == NEMO and row["revoked_at"] is None, row
    assert allowed(conn, DEV) and allowed(conn, ADMIN)
    denied = {
        "another purpose": allowed(conn, DEV, purpose="training"),
        "another category": allowed(conn, DEV, category="content"),
        "another source": allowed(conn, DEV, source_owner=BOTH),
        "another provider's member": allowed(conn, BOTH, provider=OTHER),
        "another provider's member naming the grantee": allowed(conn, BOTH),
        "a viewer (no use_granted_data)": allowed(conn, VIEWER),
        "a consumer-only user": allowed(conn, C1),
        "the operator": allowed(conn, checks_operator_user()),
    }
    assert not any(denied.values()), {k: v for k, v in denied.items() if v}
    return f"grant opens 2 members; {len(denied)} other combinations denied"


def checks_operator_user() -> str:
    from . import checks
    return checks.USER_OPERATOR


@rolled_back
def check_only_the_data_owner_grants_a_model_of_the_recipient(conn) -> str:
    """LAB-ACCESS forged IDs: a non-owner (a member, another org's owner, a provider) cannot
    grant an organization's data; a model of another provider, a past or unbounded expiry, an
    unknown purpose and a suspended organization are refused."""
    base = {"actor_user_id": C1, "source_org_id": org(conn, C1), "provider_org_id": NEMO,
            "model_id": MODEL, "data_category": "traces", "purpose": "evaluation",
            "expires_at": "2099-01-01T00:00:00Z"}
    base["expires_at"] = conn.execute(
        "select (infrx.now() + interval '1 day')::text").fetchone()[0]
    cases = {
        "forbidden": [{"actor_user_id": BOTH}, {"actor_user_id": DEV},
                      {"source_org_id": org(conn, BOTH)},
                      # a MEMBER (not the owner) of the source organization
                      {"actor_user_id": BOTH, "source_org_id": org(conn, cc.SHARED)}],
        "invalid_request": [{"model_id": OTHER_MODEL}, {"purpose": "resale"},
                            {"data_category": "everything"},
                            {"expires_at": "2000-01-01T00:00:00Z"},
                            {"expires_at": "2999-01-01T00:00:00Z"}, {"expires_at": None}],
    }
    seen = 0
    for code, overrides in cases.items():
        for over in overrides:
            got = refusal(conn, "lab_grant_data_access", {**base, **over})
            assert got == code, f"{over}: {got}, expected {code}"
            seen += 1
    assert refusal(conn, "lab_grant_data_access", base) is None
    conn.execute("update public.organizations set suspended = true, suspended_at = "
                 "infrx.now(), suspension_reason = 'other' where id = %s", (org(conn, C1),))
    assert refusal(conn, "lab_grant_data_access", base) == "org_suspended"
    return f"{seen + 1} forged or out-of-bounds grants refused"


@rolled_back
def check_revocation_and_expiry_deny_immediately(conn) -> str:
    """LAB-ACCESS: a revocation denies in the same instant, is one-way and owner-only; an
    expired grant denies; a revoked membership denies; a grant row is never edited or
    deleted."""
    row = grant(conn)
    assert allowed(conn, DEV)
    assert refusal(conn, "lab_revoke_data_access",
                   {"actor_user_id": BOTH, "grant_id": row["grant_id"]}) == "forbidden"
    done = call(conn, "lab_revoke_data_access", {"actor_user_id": C1, "grant_id": row["grant_id"]})
    assert done["revoked_at"] is not None and not allowed(conn, DEV), "revocation not immediate"
    assert refusal(conn, "lab_revoke_data_access",
                   {"actor_user_id": C1, "grant_id": row["grant_id"]}) == "state_conflict"
    for sql in ("update infrx.lab_data_grants set revoked_at = null, revoked_by = null",
                "update infrx.lab_data_grants set purpose = 'training'",
                "delete from infrx.lab_data_grants"):
        got = cc.attempt(conn, sql)
        assert got is not None and got.startswith("23514"), f"`{sql}` was allowed: {got}"
    grant(conn, ttl_s=60)
    assert allowed(conn, DEV)
    conn.execute("select infrx_test.advance(61)")
    assert not allowed(conn, DEV), "an expired grant still authorizes"
    grant(conn)
    assert allowed(conn, DEV)
    conn.execute("update infrx.provider_memberships set revoked_at = infrx.now() "
                 "where provider_org_id = %s and user_id = %s", (NEMO, DEV))
    assert not allowed(conn, DEV), "a revoked member still reads granted data"
    return "revocation immediate, one-way, owner-only; expiry and membership revocation deny"


@rolled_back
def check_grant_history_is_scoped(conn) -> str:
    """LAB-ACCESS grant history (the port C/J/T read): a grantee member sees its provider's
    grants, revoked ones included; the data owner sees its organization's; another provider,
    a viewer and a stranger see none of them."""
    kept = grant(conn)
    gone = grant(conn, purpose="training")
    call(conn, "lab_revoke_data_access", {"actor_user_id": C1, "grant_id": gone["grant_id"]})
    grant(conn, owner=BOTH, provider=OTHER, model=OTHER_MODEL)

    def ids(args):
        return sorted(r["grant_id"] for r in call(conn, "lab_data_grants", args))
    mine = sorted([kept["grant_id"], gone["grant_id"]])
    assert ids({"user_id": DEV, "provider_org_id": NEMO}) == mine
    assert ids({"user_id": C1, "source_org_id": org(conn, C1)}) == mine
    assert len(ids({"user_id": BOTH, "provider_org_id": OTHER})) == 1
    for args in ({"user_id": BOTH, "provider_org_id": NEMO},
                 {"user_id": VIEWER, "provider_org_id": NEMO},
                 {"user_id": DEV, "source_org_id": org(conn, C1)}):
        assert refusal(conn, "lab_data_grants", args) == "forbidden", args
    return "history: grantee 2 (1 revoked), owner 2, other provider 1, 3 strangers refused"


@rolled_back
def check_default_aggregates_are_own_deployments_without_identity(conn) -> str:
    """LAB-05 / LAB-ACCESS: a provider member reads per-deployment counts of its OWN
    deployments only, carrying no customer organization, key, user or request id; another
    provider's member and a consumer-only user are refused."""
    world = ca.World(conn)
    made = []
    for user, key in ((C1, ca.C1_KEY), (BOTH, ca.C2_KEY)):
        request = ca.credit_request(world, key, org(conn, user))
        ca.admit(conn, request, b.idem(request, f"agg-{user[-2:]}"), regime="credit")
        made.append(request)
    rows = call(conn, "lab_deployment_aggregates", {"user_id": VIEWER, "provider_org_id": NEMO})
    assert sum(r["requests"] for r in rows) == 2, rows
    text = json.dumps(rows)
    for secret in [org(conn, C1), org(conn, BOTH), ca.C1_KEY, ca.C2_KEY, C1, BOTH,
                   *(r.request_id for r in made)]:
        assert secret not in text, f"an aggregate carries {secret}"
    assert set(rows[0]) == {"deployment_revision_id", "requests", "active", "succeeded",
                            "failed", "cancelled", "expired"}, rows[0]
    assert call(conn, "lab_deployment_aggregates",
                {"user_id": BOTH, "provider_org_id": OTHER}) == []
    for args in ({"user_id": BOTH, "provider_org_id": NEMO},
                 {"user_id": C1, "provider_org_id": NEMO}):
        assert refusal(conn, "lab_deployment_aggregates", args) == "forbidden", args
    return f"own aggregates {rows}; other provider empty; 2 strangers refused"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing, check_a_grant_is_purpose_model_and_recipient_specific,
    check_only_the_data_owner_grants_a_model_of_the_recipient,
    check_revocation_and_expiry_deny_immediately, check_grant_history_is_scoped,
    check_default_aggregates_are_own_deployments_without_identity)}


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
def test_l2sql(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
