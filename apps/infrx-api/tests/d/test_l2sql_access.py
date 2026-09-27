#!/usr/bin/env python3
"""L2-SQL (wave-5 LW1, lab-sql; 09-amendment-workstreams §L2, R150/R156): the membership read
and the versioned source-purpose access grants, on real PostgreSQL (LAB-ACCESS, the SQL half,
and its composition with the frozen v2 predicate through `PgAccessStore`).

Role matrix (the brief's): two consumers (CONSUMER_1's and CONSUMER_2's personal
organizations), two providers (NEMO with a developer, an administrator and a viewer; OTHER),
and one user in both products (CONSUMER_2 owns a consumer organization and is an OTHER
developer). Each `check_*` takes a seeded connection and is the check a mutant in
`code_mutants_l2sql.py` must break; all but the last two roll back what they did.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_l2sql_access.py
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime

import psycopg
import pytest
from infrx.contracts.conformance import builders as b
from infrx.contracts.v2 import records as v2
from infrx.state import migrations
from infrx.state.jobstore import connector, domain_error
from infrx.state.lab_access import PgAccessStore
from psycopg.types.json import Jsonb

from . import checks
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
NOBODY = "9e000000-0000-4000-8000-00000000009e"
RPCS = ("lab_provider_memberships", "lab_put_access_grant", "lab_revoke_access_grant",
        "lab_access_grants", "lab_deployment_aggregates")


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


def ok(conn, name: str, args: dict):
    """The answer of a call that must succeed; its failure is the assertion (R40)."""
    try:
        with conn.transaction():
            return call(conn, name, args)
    except psycopg.Error as failed:
        raise AssertionError(f"{name} refused: {failed.sqlstate} "
                             f"{str(failed).splitlines()[0][:120]}") from None


def refusal(conn, name: str, args: dict) -> str | None:
    """The error code the call refuses with (in a savepoint), or None."""
    try:
        with conn.transaction():
            call(conn, name, args)
    except psycopg.Error as failed:
        return getattr(domain_error(failed), "code", f"sqlstate {failed.sqlstate}")
    return None


def scope(conn, owner: str = C1, provider: str = NEMO, **over) -> dict:
    return {"actor_user_id": owner, "grantor_org_id": org(conn, owner),
            "recipient_provider_org_id": provider, "model_ids": [MODEL],
            "categories": ["request_content"], "purposes": ["training"], "retention_days": 30,
            **over}


def rolled_back(check):
    """Run `check(conn)` and roll back everything it wrote, clock moves included."""
    def run(conn) -> str:
        out = None
        with conn.transaction():
            out = check(conn)
            raise psycopg.Rollback()
        return out
    run.__name__, run.__doc__ = check.__name__, check.__doc__
    return run


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session (anon, consumer, provider member/admin, operator) reads or
    writes the grants or executes the RPCs; the platform role reads the grants and writes
    them only through the RPCs (whose owner check a direct write would bypass)."""
    probes = ["select count(*) from infrx.lab_access_grants",
              "insert into infrx.lab_access_grants (grant_id) values (gen_random_uuid())"]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    for session in cc.BROWSER:
        for sql in probes:
            got = cc.refused_as(conn, session, sql)
            assert got is not None and got.startswith("42501"), \
                f"{session} ran `{sql[:60]}`: {got or 'allowed'}"
    assert cc.refused_as(conn, "service", probes[0]) is None, "service cannot read the grants"
    got = cc.refused_as(conn, "service", probes[1])
    assert got is not None and got.startswith("42501"), f"service wrote a grant directly: {got}"
    for name in RPCS:
        acl = conn.execute("select coalesce(proacl::text, '') from pg_proc where oid = "
                           "%s::regprocedure", (f"infrx.{name}(jsonb)",)).fetchone()[0]
        grantees = {item.split("=", 1)[0] for item in acl.strip("{}").split(",") if item}
        assert grantees <= {"postgres", "service_role"} and "service_role" in grantees, \
            f"infrx.{name}: {acl}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused; service reads"


@rolled_back
def check_memberships_are_the_users_own_rows(conn) -> str:
    """R156: the one membership read answers a user's own rows (current and revoked) with
    the provider's name; owning a consumer organization, or the operator bit, is none."""
    def read(user):
        return [(r["provider_org_id"], r["provider_name"], r["role"], r["revoked_at"] is None)
                for r in ok(conn, "lab_provider_memberships", {"user_id": user})]
    assert read(BOTH) == [(OTHER, "NemoStation", "developer", True)], read(BOTH)
    assert read(DEV) == [(NEMO, "NemoStation", "developer", True)], read(DEV)
    assert read(C1) == [] and read(checks.USER_OPERATOR) == [] and read(NOBODY) == []
    conn.execute("update infrx.provider_memberships set revoked_at = infrx.now() "
                 "where user_id = %s", (DEV,))
    assert read(DEV) == [(NEMO, "NemoStation", "developer", False)], "revocation not visible"
    return "dual user: OTHER only; consumer-only/operator/unknown: none; revocation shown"


@rolled_back
def check_only_the_grantor_owner_writes_a_scope_of_the_recipients_models(conn) -> str:
    """LAB-ACCESS forged IDs: a non-owner (a provider member, another org's owner, a member of
    the grantor) cannot grant; a model of another provider, an unknown provider, category or
    purpose, a retention outside 1..90, an expiry before now and a suspended grantor are
    refused."""
    base = scope(conn)
    cases = {
        "forbidden": [{"actor_user_id": BOTH}, {"actor_user_id": DEV},
                      {"grantor_org_id": org(conn, BOTH)},
                      {"actor_user_id": BOTH, "grantor_org_id": org(conn, cc.SHARED)}],
        "invalid_request": [{"model_ids": [OTHER_MODEL]}, {"model_ids": [MODEL, OTHER_MODEL]},
                            {"recipient_provider_org_id": NOBODY, "model_ids": []},
                            {"categories": ["everything"]}, {"purposes": ["resale"]},
                            {"retention_days": 0}, {"retention_days": 91},
                            {"expires_at": "2000-01-01T00:00:00Z"}],
    }
    seen = 0
    for code, overrides in cases.items():
        for over in overrides:
            got = refusal(conn, "lab_put_access_grant", {**base, **over})
            assert got == code, f"{over}: {got}, expected {code}"
            seen += 1
    assert ok(conn, "lab_put_access_grant", base)["version"] == 1
    conn.execute("update public.organizations set suspended = true, suspended_at = "
                 "infrx.now(), suspension_reason = 'other' where id = %s", (org(conn, C1),))
    assert refusal(conn, "lab_put_access_grant", base) == "org_suspended"
    return f"{seen + 1} forged or out-of-bounds writes refused"


@rolled_back
def check_every_change_is_a_new_immutable_version(conn) -> str:
    """AccessGrant history: a new scope, a revocation and a re-grant are versions 2, 3, 4 of
    one grant id; a revocation keeps the scope; a second revocation is `state_conflict`;
    the history is per (grantor, provider), oldest first; no row is edited or removed."""
    first = ok(conn, "lab_put_access_grant", scope(conn))
    wider = ok(conn, "lab_put_access_grant", scope(conn, purposes=["training", "capture"]))
    revoke = {"actor_user_id": C1, "grantor_org_id": org(conn, C1),
              "recipient_provider_org_id": NEMO}
    assert refusal(conn, "lab_revoke_access_grant", {**revoke, "actor_user_id": BOTH}) \
        == "forbidden"
    gone = ok(conn, "lab_revoke_access_grant", revoke)
    assert refusal(conn, "lab_revoke_access_grant", revoke) == "state_conflict"
    again = ok(conn, "lab_put_access_grant", scope(conn))
    assert [g["version"] for g in (first, wider, gone, again)] == [1, 2, 3, 4]
    assert len({g["grant_id"] for g in (first, wider, gone, again)}) == 1
    assert gone["revoked_at"] is not None and gone["purposes"] == ["training", "capture"]
    assert again["revoked_at"] is None
    ok(conn, "lab_put_access_grant", scope(conn, owner=BOTH, provider=OTHER,
                                           model_ids=[OTHER_MODEL]))
    history = ok(conn, "lab_access_grants", {"grantor_org_id": org(conn, C1),
                                             "recipient_provider_org_id": NEMO})
    assert [g["version"] for g in history] == [1, 2, 3, 4], history
    assert ok(conn, "lab_access_grants", {"grantor_org_id": org(conn, C1),
                                          "recipient_provider_org_id": OTHER}) == []
    for sql in ("update infrx.lab_access_grants set revoked_at = null",
                "delete from infrx.lab_access_grants", "truncate infrx.lab_access_grants"):
        got = cc.attempt(conn, sql)
        assert got is not None and got.startswith("23514"), f"`{sql}` was allowed: {got}"
    return "versions 1-4 of one grant; history per pair, oldest first; rows immutable"


@rolled_back
def check_default_aggregates_are_own_deployments_without_identity(conn) -> str:
    """LAB-05 / LAB-ACCESS: per-deployment counts of the provider's OWN deployments in the
    window, carrying no customer organization, key, user or request id; another provider
    and a window with no traffic read nothing."""
    world = ca.World(conn)
    made = []
    for user, key in ((C1, ca.C1_KEY), (BOTH, ca.C2_KEY)):
        request = ca.credit_request(world, key, org(conn, user))
        ca.admit(conn, request, b.idem(request, f"agg-{user[-2:]}"), regime="credit")
        made.append(request)
    rows = ok(conn, "lab_deployment_aggregates", {"provider_org_id": NEMO})
    assert sum(r["requests"] for r in rows) == 2 and all(r["errors"] == 0 for r in rows), rows
    text = json.dumps(rows)
    for secret in [org(conn, C1), org(conn, BOTH), ca.C1_KEY, ca.C2_KEY, C1, BOTH,
                   *(r.request_id for r in made)]:
        assert secret not in text, f"an aggregate carries {secret}"
    assert set(rows[0]) == {"deployment_revision_id", "window_start", "window_end",
                            "requests", "errors", "p95_latency_ms"}, rows[0]
    assert ok(conn, "lab_deployment_aggregates", {"provider_org_id": OTHER}) == []
    assert ok(conn, "lab_deployment_aggregates", {"provider_org_id": NEMO,
                                                  "since": "2099-01-01T00:00:00Z",
                                                  "until": "2099-01-02T00:00:00Z"}) == []
    return f"own aggregates {rows}; other provider and an empty window read nothing"


def check_two_writers_of_one_grant_get_consecutive_versions(conn) -> str:
    """Two grantor sessions write the same pair at once: the second waits for the first and
    writes the next version, never a failure (commits; runs on its own pair)."""
    pair = scope(conn, owner=BOTH, provider=NEMO)
    first = psycopg.connect(pgharness.dsn(conn.info.dbname))
    second = psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True)
    answers: dict = {}
    try:
        base = call(first, "lab_put_access_grant", pair)["version"]
        racer = threading.Thread(target=lambda: answers.__setitem__(
            "second", call(second, "lab_put_access_grant", pair)))
        racer.start()
        racer.join(1.0)
        assert racer.is_alive(), "the second writer did not wait for the first"
        first.commit()
        racer.join(10.0)
    finally:
        first.close()
    second.close()
    assert answers.get("second", {}).get("version") == base + 1, answers
    return f"versions {base} and {base + 1}"


def check_content_needs_a_current_developer_and_a_current_grant(conn) -> str:
    """LAB-ACCESS composed: `PgAccessStore`'s rows under the frozen v2 predicate. A current
    developer with the current grant reads exactly that model/category/purpose; a viewer, the
    other provider's member (the dual user), a consumer-only user, another purpose and the
    moment after revocation are denied (commits)."""
    store = PgAccessStore(connector(pgharness.dsn(conn.info.dbname)))   # as service_role
    grantor = org(conn, C1)
    now = lambda: conn.execute("select infrx.now()").fetchone()[0]    # noqa: E731

    def may(user, provider=NEMO, purpose=v2.DataPurpose.training):
        async def go():
            return v2.may_read_customer_content(
                membership=await store.membership(provider, user),
                grant=await store.current_grant(grantor, provider), now=now(),
                provider_org_id=provider, model_id=MODEL,
                category=v2.DataCategory.request_content, purpose=purpose)
        return asyncio.run(go())
    assert not may(DEV), "a developer read content with no grant"
    asyncio.run(store.put_grant(C1, {k: v for k, v in scope(conn).items()
                                     if k != "actor_user_id"}))
    allowed = {"developer": may(DEV), "administrator": may(ADMIN)}
    denied = {"viewer": may(VIEWER), "dual user of OTHER": may(BOTH),
              "dual user naming OTHER": may(BOTH, provider=OTHER),
              "consumer-only": may(C1), "another purpose": may(DEV, purpose=v2.DataPurpose.capture)}
    assert all(allowed.values()) and not any(denied.values()), (allowed, denied)
    revoked = asyncio.run(store.revoke_grant(C1, grantor, NEMO))
    assert isinstance(revoked.revoked_at, datetime) and not may(DEV), "revocation not immediate"
    history = asyncio.run(store.grant_history(grantor, NEMO))
    assert [g.version for g in history][-2:] == [history[-1].version - 1, history[-1].version]
    return f"allowed {sorted(allowed)}; denied {sorted(denied)} and after revocation"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing, check_memberships_are_the_users_own_rows,
    check_only_the_grantor_owner_writes_a_scope_of_the_recipients_models,
    check_every_change_is_a_new_immutable_version,
    check_default_aggregates_are_own_deployments_without_identity,
    check_two_writers_of_one_grant_get_consecutive_versions,
    check_content_needs_a_current_developer_and_a_current_grant)}


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
