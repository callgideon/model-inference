"""E3C s10 (runtime / browser / operator role denials, RV-09 / D-31) and s13 (CATALOG-TRUTH,
RV-01; coordinator update 1).

s10: the database login the box processes actually use cannot reach an owner role or
rewrite money; the Supabase browser roles (`anon`, `authenticated`) can execute no function
and write no table outside the console's granted surface - measured over the catalog and
exercised through PostgREST; an operator credential spends nothing and a consumer credential
operates nothing.

s13: what `GET /v1/models` publishes is compared with what this deployment's admission
does: no zero-data-retention claim while serving stores content, no advertised parameter
admission refuses, no concurrency promise; and, where the tree has F2C-C's projection, no
`violations()` against the running serving profile."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import stack                                            # noqa: E402

# What 0001/0004/0005/0008 grant the browser roles on purpose (the console's read surface).
BROWSER_FUNCTIONS = {
    "public.is_operator", "public.is_org_member", "public.is_org_owner",
    "public.is_service_client", "public.principal_uuid", "public.visible_principal",
    "public.org_usage_summary", "public.org_usage_daily", "public.org_balance",
    "public.org_wallet_summary", "public.console_wallet_summary",
    "public.console_usage_summary", "public.console_usage_daily",
    "public.console_legacy_usd_statement"}
BROWSER_WRITES = {("public.profiles", "UPDATE"), ("public.organizations", "UPDATE"),
                  ("public.api_keys", "UPDATE")}


RUNTIME_PROBES = {
    "rewrite a ledger row": "update infrx.credit_ledger set reason = reason where false",
    "delete a job": "delete from infrx.jobs where false",
    "grant itself credit": "insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
                           "select id, 1, 'grant', 'e3c probe' from public.organizations limit 0",
    "DDL in infrx": "create table infrx.e3c_probe (x int)",
}


def test_s10_the_runtime_login_cannot_become_an_owner_or_rewrite_money(workdir):
    """As the box's own DATABASE_URL, configured as its pool configures every connection
    (`set role service_role`): the login is no owner (else `reset role` undoes the role), and
    the runtime role rewrites no money and runs no DDL. Every probe rolls back."""
    import psycopg
    with world.composed(workdir, start=()) as trip:
        allowed = []
        with psycopg.connect(trip.box.env["DATABASE_URL"], autocommit=True) as conn:
            owner, = conn.execute(
                "select rolsuper or pg_has_role(session_user, (select relowner from pg_class "
                "where oid = 'infrx.credit_ledger'::regclass), 'MEMBER') from pg_roles "
                "where rolname = session_user").fetchone()
            if owner:
                allowed.append("reach the money tables' owner by `reset role`")
            for what, sql in RUNTIME_PROBES.items():
                try:
                    with conn.transaction():
                        conn.execute("set local role service_role")
                        conn.execute(sql)
                        allowed.append(what)
                        raise _Rollback
                except (_Rollback, psycopg.errors.InsufficientPrivilege):
                    pass
        assert not allowed, f"the runtime login can: {allowed} (RV-09 / D-31)"


class _Rollback(Exception):
    pass


def browser_surface(trip) -> dict:
    """Functions `anon`/`authenticated` can EXECUTE in a schema they can USE, and tables
    they can write, beyond the granted console surface (trigger functions excluded)."""
    rows = trip.db("""
        select r.rolname, n.nspname || '.' || p.proname
          from pg_proc p join pg_namespace n on n.oid = p.pronamespace
          cross join (values ('anon'), ('authenticated')) r(rolname)
         where n.nspname in ('public', 'infrx') and p.prorettype <> 'trigger'::regtype
           and has_schema_privilege(r.rolname, n.oid, 'USAGE')
           and has_function_privilege(r.rolname, p.oid, 'EXECUTE')""")
    writes = trip.db("""
        select r.rolname, n.nspname || '.' || c.relname, w.priv
          from pg_class c join pg_namespace n on n.oid = c.relnamespace
          cross join (values ('anon'), ('authenticated')) r(rolname)
          cross join (values ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE')) w(priv)
         where n.nspname in ('public', 'infrx') and c.relkind in ('r', 'p', 'v')
           and has_schema_privilege(r.rolname, n.oid, 'USAGE')
           and has_table_privilege(r.rolname, c.oid, w.priv)""")
    return {"functions": sorted({(role, fn) for role, fn in rows
                                 if fn not in BROWSER_FUNCTIONS
                                 and not (role == "authenticated" and fn in BROWSER_FUNCTIONS)}),
            "writes": sorted({(role, table, priv) for role, table, priv in writes
                              if not (role == "authenticated"
                                      and (table, priv) in BROWSER_WRITES)})}


def test_s10_the_browser_roles_reach_nothing_outside_the_console_surface(workdir):
    import httpx
    with world.composed(workdir, start=()) as trip:
        extra = browser_surface(trip)
        rest, alpha = trip.box.env["SUPABASE_URL"], trip.world.alpha
        member = {"Authorization": f"Bearer {stack.jwt('authenticated', alpha.user_id)}"}
        with httpx.Client(base_url=rest, timeout=10.0) as http:
            attempts = {
                "insert credit_ledger": http.post("/credit_ledger", headers=member, json={
                    "org_id": alpha.org_id, "delta_usd": 5, "kind": "grant", "reason": "x"}),
                "api_keys audience -> operator": http.patch(
                    "/api_keys", params={"id": f"eq.{alpha.key_id}"}, headers=member,
                    json={"audience": "operator"}),
                "claim another user's grant": http.post(
                    "/rpc/claim_signup_grant", headers=member,
                    json={"p_user_id": trip.world.beta.user_id}),
            }
        let_through = {what: answer.status_code for what, answer in attempts.items()
                       if answer.status_code < 400}
        assert not extra["functions"] and not extra["writes"] and not let_through, \
            f"the browser roles reach: {extra}; PostgREST let through {let_through}"


def test_s10_operator_and_consumer_credentials_stay_in_their_lane(workdir):
    """A consumer key cannot operate (CLI refuses it); an operator key runs no inference; a
    revoked operator key operates nothing."""
    with world.composed(workdir, start=("worker", "gateway")) as trip:
        alpha = trip.world.alpha
        status, answer = world.cli(trip, "grant", "--user", alpha.user_id, "--idempotency-key",
                                   "g-consumer", "--reason", "e3c", secret=alpha.secret)
        assert status == 1 and answer.get("error") in ("forbidden", "invalid_api_key"), answer
        refused = trip.http.post("/v1/chat/completions", json={
            "model": stack.CREDIT_ALIAS, "messages": world.TEXT},
            headers={"Authorization": f"Bearer {trip.world.operator_secret}"})
        assert refused.status_code in (401, 403), refused.text
        (key_id, org_id), = trip.db("select id::text, org_id::text from public.api_keys where "
                                    "audience = 'operator' and revoked_at is null")
        status, answer = world.cli(trip, "revoke-key", "--org", org_id, "--key-id", key_id,
                                   "--idempotency-key", "r-operator", "--reason", "e3c")
        assert status == 0, answer
        status, answer = world.cli(trip, "grant", "--user", alpha.user_id, "--idempotency-key",
                                   "g-after-revoke", "--reason", "e3c")
        assert status == 1, f"a revoked operator key still operates: {answer}"


# ------------------------------------------------------------------ s13


def discovery(trip) -> dict:
    answer = trip.http.get("/v1/models", headers=trip.headers(trip.world.alpha))
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_s13_discovery_claims_nothing_serving_contradicts(workdir):
    """No zero-data-retention claim (serving stores payloads, media, results: RV-01), no
    concurrency promise, no advertised parameter that admission refuses."""
    with world.composed(workdir) as trip:
        doc, problems = discovery(trip), []
        for model in doc["data"]:
            if (model.get("compliance") or {}).get("zdr") or \
                    (model.get("retention") or {}).get("zero_data_retention"):
                problems.append(f"{model['id']}: claims zero data retention")
            for cap in model.get("capacity", []):
                if cap.get("type") == "concurrency":
                    problems.append(f"{model['id']}: promises concurrency {cap.get('value')}")
            for output in model.get("output_modalities", []):
                for name in (output.get("supported_parameters") or {}):
                    extra = {"tools": [{"type": "function", "function": {"name": "f",
                                                                          "parameters": {}}}],
                             "seed": 7, "stop_sequences": None}.get(name)
                    if extra is None:
                        continue
                    answer = trip.send(trip.world.alpha, "sync", world.TEXT, None,
                                       **{name: extra})
                    if answer.status_code == 400:
                        problems.append(f"{model['id']}: advertises `{name}`, admission "
                                        f"refuses it ({world.code(answer)})")
        assert not problems, problems


def test_s13_discovery_matches_the_running_serving_profile(workdir):
    """F2C-C's `violations(published, serving_profile(...))` is empty for what G7 serves."""
    try:
        from infrx.contracts.v2 import published_model   # noqa: F401
    except ImportError:
        world.blocked("F2C", "G7", why="no published-model projection in this tree (F2C-C)")
    world.blocked("G7", why="the route does not yet serve the projection with the running "
                            "profile it was checked against; wire G7's hook here (phase 2)")
