#!/usr/bin/env python3
"""Prove the runtime's database login is least-privilege, THROUGH the pooler it uses
(I8 slice 2; the login itself is D10's). Coordinator-run against hosted; tests run it on
tests/i/pooler.py's local stand-in with a stand-in role.

    read -rs PROBE_DATABASE_URL; export PROBE_DATABASE_URL   # the runtime login's DSN, :6543
    apps/infrx-api/.venv/bin/python infra/runbooks/privilege_probe.py --role <D10 login> \
        [--allow-functions <file: one signature per line, D10's list>] [--pooler-semantics]

Every operation below is ATTEMPTED - not looked up in a catalog - inside its own
transaction that is always rolled back, with `SET LOCAL` timeouts (so the probe is itself
transaction-pooler safe). A denial is SQLSTATE 42501 and nothing else: success is a
failure of the check, and any other error is INCONCLUSIVE (a probe that cannot tell must
not pass). The identity checks are catalog reads of the login's own row. Output: one JSON
object per check, then a verdict; the DSN, passwords and row data are never printed.
Exit 0 all as expected, 1 any failed or inconclusive check, 2 usage/connection.

`--pooler-semantics` adds read-only observations of the pooler itself (hosted Supavisor on
6543 vs the local PgBouncer stand-in): whether psycopg's automatic prepared statements
survive repeated execution and whether a session SET is still in force a few statements
later. They are reported, not judged: routing is the pooler's and may vary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

DENIED = "42501"
ZERO = "'00000000-0000-0000-0000-000000000000'::uuid"

#: (name, SQL) - each must be refused with 42501. Written against the 0001-0018 schema.
MUST_DENY = (
    ("read identities (auth.users)", "select 1 from auth.users limit 1"),
    ("read role secrets (pg_authid)", "select rolpassword from pg_authid limit 1"),
    ("write money directly (public.credit_ledger)",
     f"insert into public.credit_ledger (org_id, delta_usd, kind) values ({ZERO}, 0, 'adjustment')"),
    ("write a wallet directly (infrx.wallets)",
     "update infrx.wallets set revision = revision where false"),
    ("write a credit wallet directly (infrx.credit_wallets)",
     "delete from infrx.credit_wallets where false"),
    ("flip a feature flag (infrx.feature_flags)",
     "update infrx.feature_flags set enabled = enabled where false"),
    ("delete jobs (infrx.jobs)", "delete from infrx.jobs where false"),
    ("truncate the outbox", "truncate infrx.outbox"),
    ("rewrite a price (infrx.price_versions)",
     "update infrx.price_versions set effective_to = effective_to where false"),
    ("DDL in public", "create table public.infrx_privilege_probe (x int)"),
    ("DDL in infrx", "create table infrx.infrx_privilege_probe (x int)"),
    ("create a role", "create role infrx_privilege_probe_role"),
    ("become service_role", "set local role service_role"),
    ("become postgres", "set local role postgres"),
    ("read a server file", "select pg_read_file('/etc/hostname')"),
    ("run a server program", "copy (select 1) to program 'true'"),
    ("mint an operator key (infrx.bootstrap_operator_key)",
     "select infrx.bootstrap_operator_key(null::uuid, null, null, null, null, null)"),
)

#: Roles the runtime login must not be (a member of): each one would undo the least privilege.
PRIVILEGED = ("postgres", "service_role", "supabase_admin", "authenticator",
              "pg_read_server_files", "pg_write_server_files", "pg_execute_server_program",
              "pg_read_all_data", "pg_write_all_data")

IDENTITY = (
    ("the login is the runtime role, with no SET ROLE",
     "select current_user = %(role)s and session_user = %(role)s"),
    ("the login is not superuser, bypassrls, createrole, createdb or replication",
     "select not (rolsuper or rolbypassrls or rolcreaterole or rolcreatedb or rolreplication) "
     "from pg_roles where rolname = current_user"),
    ("the login is no member of a privileged role",
     "select not exists (select 1 from pg_roles r where r.rolname = any(%(privileged)s) "
     "and pg_has_role(current_user, r.oid, 'member'))"),
    ("statements are bounded by the login's own default (survives transaction pooling)",
     "select current_setting('statement_timeout') not in ('0', '0ms')"),
)


def attempt(conn, sql: str, params=None) -> tuple[str, object]:
    """('ok', first value or None) or ('error', SQLSTATE), always rolled back."""
    import psycopg
    try:
        with conn.transaction(force_rollback=True):
            conn.execute("set local statement_timeout = '5s'")
            conn.execute("set local lock_timeout = '1s'")
            cur = conn.execute(sql, params)
            row = cur.fetchone() if cur.description else None
            return "ok", (row[0] if row else None)
    except psycopg.Error as failed:
        rls = "row-level security" in (failed.diag.message_primary or "")
        return "error", (failed.sqlstate or "unknown") + (" rls" if rls else "")


def probe(conn, role: str, functions: list[str]) -> list[dict]:
    results = []
    params = {"role": role, "privileged": list(PRIVILEGED)}
    for name, sql in IDENTITY:
        kind, value = attempt(conn, sql, params)
        results.append({"check": name, "expect": True,
                        "got": value if kind == "ok" else f"error {value}",
                        "pass": kind == "ok" and value is True})
    for name, sql in MUST_DENY:
        kind, value = attempt(conn, sql)
        # A row-level-security refusal is a denial too (same SQLSTATE), but it means the
        # TABLE privilege exists and only a policy stands in the way: reported apart, for D10.
        got = ("allowed" if kind == "ok" else "denied" if value == DENIED
               else "denied (row-level security only)" if value == DENIED + " rls"
               else f"error {value}")
        results.append({"check": f"denied: {name}", "expect": "denied", "got": got,
                        "pass": got.startswith("denied"), "inconclusive": got.startswith("error")})
    for signature in functions:
        kind, value = attempt(conn, "select has_function_privilege(current_user, %(f)s, "
                                    "'execute')", {"f": signature})
        results.append({"check": f"executes {signature}", "expect": True,
                        "got": value if kind == "ok" else f"error {value}",
                        "pass": kind == "ok" and value is True})
    if not functions:
        results.append({"check": "executes the runtime's functions", "expect": "D10's list",
                        "got": "PENDING: no --allow-functions list given", "pass": False,
                        "inconclusive": True})
    return results


def pooler_semantics(dsn: str) -> list[dict]:
    """Observations, not judgements (module docstring)."""
    import psycopg
    seen = []
    with psycopg.connect(dsn, autocommit=True) as conn:       # psycopg's default threshold
        try:
            for _ in range(8):
                conn.execute("select count(*) from pg_roles where rolname = %s",
                             ("postgres",)).fetchone()
            seen.append({"observe": "8 executions with automatic prepared statements",
                         "got": "no error"})
        except psycopg.Error as failed:
            seen.append({"observe": "8 executions with automatic prepared statements",
                         "got": f"error {failed.sqlstate}"})
    with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as conn:
        # a custom setting: no pooler tracks and replays it the way some replay
        # application_name, so what comes back is the server connection's own state
        conn.execute("set infrx.privilege_probe = 'set'")
        names = [conn.execute("select current_setting('infrx.privilege_probe', true)")
                 .fetchone()[0] for _ in range(5)]
        seen.append({"observe": "a session SET, read back 5 times",
                     "got": f"{sum(n == 'set' for n in names)}/5 still set"})
    return seen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--role", required=True, help="the login D10 provisions for the runtime")
    ap.add_argument("--allow-functions", help="file: function signatures it must EXECUTE")
    ap.add_argument("--pooler-semantics", action="store_true")
    a = ap.parse_args(argv)
    dsn = os.environ.get("PROBE_DATABASE_URL", "")
    if not dsn:
        print("PROBE_DATABASE_URL is not set (read it with read -rs; never on a command line)",
              file=sys.stderr)
        return 2
    functions = []
    if a.allow_functions:
        functions = [line.strip() for line in open(a.allow_functions)
                     if line.strip() and not line.startswith("#")]
    import psycopg
    try:
        conn = psycopg.connect(dsn, autocommit=True, prepare_threshold=None)
    except psycopg.Error as failed:
        print(f"cannot connect: {type(failed).__name__} {failed.sqlstate or ''}", file=sys.stderr)
        return 2
    with conn:
        results = probe(conn, a.role, functions)
    for result in results:
        print(json.dumps(result, sort_keys=True))
    if a.pooler_semantics:
        for observation in pooler_semantics(dsn):
            print(json.dumps(observation, sort_keys=True))
    failed = [r["check"] for r in results if not r["pass"]]
    print(json.dumps({"verdict": "PASS" if not failed else "FAIL", "failed": failed}))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
