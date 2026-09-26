"""D10-0025 (U3 WR-U3-1, R143): the operator console's audited writes, as SQL-level checks.

`public.operator_adjust_credit` / `operator_set_suspension` / `operator_revoke_key` run with
the signed-in operator's OWN JWT: `public.is_operator()` and the audit actor
`operator:<auth.uid()>` are decided in the database; each runs the audited `infrx` operation
the CLI uses (0018 `grant_credit`, 0009 `set_suspension`, 0009 `revoke_key`); console keys
live in `app-operator:<operation>:<key>`; a key reused for another change is
`idempotency_conflict`; a concurrent retry is a replay. Plus two operator-only reads,
`public.operator_wallet_drift` and `public.operator_unknown_usage`.

Same contract as `checks_port.py`: the "admission" scenario, each check in a transaction it
rolls back (the race check commits, in its own connections), the callers as the real
browser principal (`checks._jwt`).
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb
from infrx.operations.service import stable_id
from infrx.state.jobstore import domain_error

from . import checks, checks_admission as ca
from . import checks_credit as cc
from . import checks_operations as co
from . import checks_reads
from . import checks_settle as cs
from .checks_leases import waiting_on_a_lock

#: The platform operator of these checks (profiles.is_operator), created by `make_operator`.
OPERATOR = "0d100000-0000-4000-8000-0000000000e1"
ADJUST = "public.operator_adjust_credit(uuid,text,text,text)"
SUSPEND = "public.operator_set_suspension(uuid,boolean,text,text)"
REVOKE = "public.operator_revoke_key(uuid,text,text)"
RPCS = (ADJUST, SUSPEND, REVOKE)
HELPER = "infrx.console_operator(text,text)"
VIEWS = ("public.operator_wallet_drift", "public.operator_unknown_usage")
NOBODY = "0d100000-0000-4000-8000-0000000000ff"
ROLES = ("anon", "authenticated", "service_role", "infrx_runtime", "infrx_monitor")

_ADJUST = "select public.operator_adjust_credit(%s, %s, %s, %s)"
_SUSPEND = "select public.operator_set_suspension(%s, %s, %s, %s)"
_REVOKE = "select public.operator_revoke_key(%s, %s, %s)"
_RESET = ("reset role; select set_config('request.jwt.claims', '', true), "
          "set_config('request.jwt.claim.sub', '', true), "
          "set_config('request.jwt.claim.role', '', true)")


def make_operator(conn, user: str = OPERATOR) -> str:
    conn.execute("insert into auth.users (id, email) select %s, 'd10-operator@example.com' "
                 "where not exists (select 1 from auth.users where id = %s)", (user, user))
    conn.execute("update public.profiles set is_operator = true where id = %s", (user,))
    return user


def call(conn, who: str | None, sql: str, params=()) -> tuple[str | None, object, str]:
    """(code, answer, message) of one RPC as `who` - an individual's uuid (their JWT),
    'anon', 'service_role' - in a savepoint that KEEPS the effect (released on success).
    A refusal is its typed code (`domain_error`) or its SQLSTATE."""
    session = {"anon": "set local role anon",
               "service_role": "set local role service_role"}.get(who) or checks._jwt(who)
    try:
        with conn.transaction():
            conn.execute(session)
            row = conn.execute(sql, params).fetchone()
            conn.execute(_RESET)
    except psycopg.Error as failed:
        mapped = domain_error(failed)
        message = (failed.diag.message_primary or "") if failed.diag else str(failed)
        return getattr(mapped, "code", failed.sqlstate), None, message
    return None, row[0], ""


def rows_as(conn, who: str | None, sql: str, params=()) -> tuple[str | None, list | None]:
    """(code, rows) of a read as `who`, rolled back."""
    if who == "service_role":
        return _as_service(conn, sql, params)
    return checks_reads.as_user(conn, None if who == "anon" else who, sql, params)


def _as_service(conn, sql, params):
    try:
        with conn.transaction():
            conn.execute("set local role service_role")
            rows = conn.execute(sql, params).fetchall()
            conn.execute("reset role")
            return None, rows
    except psycopg.Error as failed:
        return failed.sqlstate, None


def adjust(conn, who, user, amount, key, reason="d10 operator case"):
    return call(conn, who, _ADJUST, (user, amount, reason, key))


def suspend(conn, who, org, suspended, key, reason="d10 operator case"):
    return call(conn, who, _SUSPEND, (org, suspended, reason, key))


def revoke(conn, who, key_id, key, reason="d10 operator case"):
    return call(conn, who, _REVOKE, (key_id, reason, key))


def op_id(key: str) -> str:
    """The ledger operation id a console adjustment under `key` records."""
    return str(uuid.UUID(bytes=hashlib.md5(f"app-operator:adjust:{key}".encode()).digest()))


def totals(conn, wallet: str) -> tuple[Decimal, Decimal]:
    return conn.execute("select ledger_total, reserved_total from infrx.credit_wallets "
                        "where wallet_id = %s", (wallet,)).fetchone()


def audits(conn, key: str) -> list[tuple]:
    return conn.execute("select actor_principal, action, target_org_id::text, reason, after "
                        "from infrx.audit_entries where idempotency_key = %s", (key,)).fetchall()


def suspended(conn, org: str) -> tuple:
    return conn.execute("select suspended, suspension_reason from public.organizations "
                        "where id = %s", (org,)).fetchone()


def revoked_at(conn, key_id: str):
    return conn.execute("select revoked_at from public.api_keys where id = %s",
                        (key_id,)).fetchone()[0]


def new_key(conn, user: str, audience: str = "consumer") -> str:
    key_id = str(uuid.uuid4())
    conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash, "
                 "audience) values (%s, %s, %s, 'k', 'sk-infrx-op00000', %s, %s)",
                 (key_id, cc.personal_org(conn, user), user, f"hash-{key_id}", audience))
    return key_id


# --------------------------------------------------------------------- checks
def check_operator_authority(conn) -> str:
    """DUR-RLS: a consumer (another individual, or one with no profile) crafting any of the
    three RPCs is refused 42501 BY THE FUNCTION (`forbidden: operator authority …`); anon
    and service_role hold no EXECUTE at all (42501 permission denied); nothing moved. The
    operator succeeds and every row it writes names `operator:<auth.uid()>` - the RPCs take
    no actor, and a reason that names another principal does not become the actor. A
    reason or key outside 1..500 / 1..200 is `invalid_request`, nothing written."""
    def body():
        make_operator(conn)
        c1, c2 = cc.CONSUMER_1, cc.CONSUMER_2
        wallet, org2 = cc.wallet_of(conn, c1), cc.personal_org(conn, c2)
        before = totals(conn, wallet)
        for who in (c1, c2, str(uuid.uuid4()), "anon", "service_role"):
            for name, (code, _answer, message) in (
                    ("adjust", adjust(conn, who, c1, "1000000", f"deny-{uuid.uuid4()}")),
                    ("suspend", suspend(conn, who, org2, True, f"deny-{uuid.uuid4()}")),
                    ("revoke", revoke(conn, who, ca.C2_KEY, f"deny-{uuid.uuid4()}"))):
                assert code == "42501", f"{who} {name}: {code} {message}"
                if who not in ("anon", "service_role"):
                    assert message.startswith("forbidden"), \
                        f"{who} {name} was refused by something other than is_operator(): " \
                        f"{message}"
                else:
                    assert "permission denied" in message, f"{who} {name}: {message}"
        assert totals(conn, wallet) == before, "a non-operator moved credit"
        assert suspended(conn, org2) == (False, None), "a non-operator suspended an org"
        assert revoked_at(conn, ca.C2_KEY) is None, "a non-operator revoked a key"
        for fn in RPCS + (HELPER,):
            args, = conn.execute("select pg_get_function_arguments(%s::regprocedure)",
                                 (fn,)).fetchone()
            assert "actor" not in args, f"{fn} takes an actor: {args}"
        key = f"who-{uuid.uuid4()}"
        code, answer, message = adjust(conn, OPERATOR, c1, "5.00000000", key,
                                       reason="  on behalf of operator:someone-else  ")
        assert code is None and answer == {"replayed": False, "unit": "CREDIT",
                                           "amount": "5.00000000"}, (code, answer, message)
        actor = f"operator:{OPERATOR}"
        ledger = conn.execute("select actor, reason, kind, amount::text from "
                              "infrx.credit_ledger where operation_id = %s",
                              (op_id(key),)).fetchall()
        assert ledger == [(actor, "on behalf of operator:someone-else", "operator_adjustment",
                           "5.00000000")], ledger
        rows = audits(conn, f"grant_credit:{op_id(key)}")
        assert [(r[0], r[1]) for r in rows] == [(actor, "admin_adjust")], rows
        key = f"who-{uuid.uuid4()}"
        assert suspend(conn, OPERATOR, org2, True, key)[0] is None
        assert [r[0] for r in audits(conn, f"app-operator:suspension:{key}")] == [actor]
        key = f"who-{uuid.uuid4()}"
        assert revoke(conn, OPERATOR, ca.C2_KEY, key)[0] is None
        assert [r[0] for r in audits(conn, f"app-operator:revoke:{key}")] == [actor]
        written = conn.execute("select count(*) from infrx.audit_entries").fetchone()[0]
        for reason, key in (("", "k"), ("   ", "k"), ("r" * 501, "k"), (None, "k"),
                            ("ok", ""), ("ok", "k" * 201), ("ok", None)):
            for name, (code, _a, message) in (
                    ("adjust", adjust(conn, OPERATOR, c1, "1", key, reason=reason)),
                    ("suspend", suspend(conn, OPERATOR, org2, False, key, reason=reason)),
                    ("revoke", revoke(conn, OPERATOR, ca.C1_KEY, key, reason=reason))):
                assert code == "invalid_request", \
                    f"{name} reason {reason!r:.12} key {key!r:.12}: {code} {message}"
        assert conn.execute("select count(*) from infrx.audit_entries").fetchone()[0] == written
        return (f"consumers refused 42501 by is_operator(); anon/service_role hold no EXECUTE; "
                f"actor {actor} on ledger and audit rows")
    return ca._in_rollback(conn, body)


def check_operator_idempotency(conn) -> str:
    """R143: the same key and change is `replayed: true` with no second effect; the same key
    for another target or payload is `idempotency_conflict`; console keys live in
    `app-operator:<operation>:<key>` - a CLI call that used the SAME key text (its audit row
    keyed by the raw text, its ledger operation `stable_id('adjustment', key)`) never
    replays, conflicts with, or blocks a console change."""
    def body():
        make_operator(conn)
        c1, c2 = cc.CONSUMER_1, cc.CONSUMER_2
        org1, org2 = cc.personal_org(conn, c1), cc.personal_org(conn, c2)
        w1 = cc.wallet_of(conn, c1)
        # adjust
        key = f"idem-{uuid.uuid4()}"
        start = totals(conn, w1)[0]
        assert adjust(conn, OPERATOR, c1, "7.5", key)[1]["replayed"] is False
        assert adjust(conn, OPERATOR, c1, "7.5", key)[1] == {
            "replayed": True, "unit": "CREDIT", "amount": "7.50000000"}
        assert adjust(conn, OPERATOR, c1, "7.50000000", key)[1]["replayed"] is True
        for other in ((c1, "7.6"), (c2, "7.5"), (c1, "-7.5")):
            code = adjust(conn, OPERATOR, *other, key)[0]
            assert code == "idempotency_conflict", f"adjust {other} under a used key: {code}"
        assert totals(conn, w1)[0] - start == Decimal("7.5"), "an adjustment applied twice"
        assert conn.execute("select count(*) from infrx.credit_ledger where operation_id = %s",
                            (op_id(key),)).fetchone()[0] == 1
        # suspension
        key = f"idem-{uuid.uuid4()}"
        assert suspend(conn, OPERATOR, org2, True, key)[1] == {"replayed": False,
                                                               "suspended": True}
        assert suspend(conn, OPERATOR, org2, True, key)[1] == {"replayed": True,
                                                               "suspended": True}
        for org, status in ((org2, False), (org1, True)):
            code = suspend(conn, OPERATOR, org, status, key)[0]
            assert code == "idempotency_conflict", f"suspension {org} {status}: {code}"
        assert suspended(conn, org1) == (False, None), "a conflicting key suspended an org"
        assert len(audits(conn, f"app-operator:suspension:{key}")) == 1
        # revoke
        key = f"idem-{uuid.uuid4()}"
        assert revoke(conn, OPERATOR, ca.C1_KEY, key)[1] == {"replayed": False}
        at = revoked_at(conn, ca.C1_KEY)
        assert revoke(conn, OPERATOR, ca.C1_KEY, key)[1] == {"replayed": True}
        assert revoke(conn, OPERATOR, ca.C2_KEY, key)[0] == "idempotency_conflict"
        assert revoked_at(conn, ca.C2_KEY) is None, "a conflicting key revoked another key"
        assert revoked_at(conn, ca.C1_KEY) == at
        # the CLI used the same key text first: every console change still applies, once
        shared = f"shared-{uuid.uuid4()}"
        cli_op = stable_id("adjustment", shared)
        cli = conn.execute("select infrx.grant_credit(%s)", (Jsonb({
            "wallet_id": w1, "kind": "operator_adjustment", "amount": "1",
            "operation_id": cli_op, "actor": "operations", "reason": "cli",
            "at": "2026-09-26T00:00:00+00:00"}),)).fetchone()[0]
        assert cli["replayed"] is False
        conn.execute("insert into infrx.audit_entries (id, actor_principal, action, "
                     "target_org_id, reason, after, idempotency_key) values (%s, 'operations', "
                     "'admin_set_suspension', %s, 'cli', %s, %s)",
                     (cli_op, org1, Jsonb({"operation": "suspension",
                                           "request": {"org_id": org1}}), shared))
        key3 = new_key(conn, c1)
        assert adjust(conn, OPERATOR, c1, "1", shared)[1]["replayed"] is False, \
            "a console adjustment replayed the CLI's"
        assert op_id(shared) != cli_op and conn.execute(
            "select count(*) from infrx.credit_ledger where operation_id in (%s, %s)",
            (op_id(shared), cli_op)).fetchone()[0] == 2
        for name, (code, answer, message) in (
                ("suspension", suspend(conn, OPERATOR, org1, True, shared)),
                ("revoke", revoke(conn, OPERATOR, key3, shared))):
            assert code is None and answer["replayed"] is False, \
                f"a console {name} under the CLI's key text: {code} {answer} {message}"
        assert suspended(conn, org1) == (True, "other") and revoked_at(conn, key3) is not None
        assert [r[1] for r in audits(conn, f"app-operator:suspension:{shared}")] == \
            ["admin_set_suspension"]
        assert [r[1] for r in audits(conn, f"app-operator:revoke:{shared}")] == \
            ["admin_key_revoke"]
        return "replay, conflict on target and payload, and the CLI's key text kept apart"
    return ca._in_rollback(conn, body)


def check_operator_adjust(conn) -> str:
    """Money moves only through 0018's `grant_credit` as an `operator_adjustment`: signed
    exact text, nonzero, at most 8 places; a correction that would take available below
    zero is `invalid_request` with nothing written; one that fits exactly (available -> 0)
    commits, and so does its restore; the wallet total moves by exactly the amount and the
    detectors stay clean. The signup grant is not reachable (no row of another kind, no
    entitlement). A user with no consumer wallet, or no user, is `not_found`."""
    def body():
        make_operator(conn)
        c1 = cc.CONSUMER_1
        w1 = cc.wallet_of(conn, c1)
        kinds_sql = "select kind, count(*) from infrx.credit_ledger group by kind"
        kinds = dict(conn.execute(kinds_sql).fetchall())
        entitlements = conn.execute("select count(*) from infrx.signup_entitlements"
                                    ).fetchone()[0]
        for bad in ("0", "0.00000000", "-0", "1.123456789", "1e3", "abc", "", "1000000000000",
                    None, " 1"):
            code, _a, message = adjust(conn, OPERATOR, c1, bad, f"bad-{uuid.uuid4()}")
            assert code == "invalid_request", f"amount {bad!r}: {code} {message}"
        ledger, reserved = totals(conn, w1)
        available = ledger - reserved
        assert available > 0, "the scenario funds CONSUMER_1"
        code, _a, message = adjust(conn, OPERATOR, c1, str(-(available + Decimal("0.00000001"))),
                                   f"over-{uuid.uuid4()}")
        assert code == "invalid_request", f"a correction below zero: {code} {message}"
        assert totals(conn, w1) == (ledger, reserved), "a refused correction moved the wallet"
        code, answer, message = adjust(conn, OPERATOR, c1, "0.00000001", f"tiny-{uuid.uuid4()}")
        assert (code, answer["amount"]) == (None, "0.00000001"), (code, answer, message)
        assert totals(conn, w1)[0] == ledger + Decimal("0.00000001")
        take = f"{-(available + Decimal('0.00000001')):.8f}"
        code, answer, message = adjust(conn, OPERATOR, c1, take, f"fit-{uuid.uuid4()}")
        assert code is None and answer["amount"] == take, (code, answer, message)
        now = totals(conn, w1)
        assert now[0] - now[1] == 0, f"the fitting correction left {now[0] - now[1]} available"
        assert adjust(conn, OPERATOR, c1, "-0.00000001", f"under-{uuid.uuid4()}")[0] == \
            "invalid_request", "available went below zero"
        code, answer, _m = adjust(conn, OPERATOR, c1, f"{available:.8f}", f"back-{uuid.uuid4()}")
        assert code is None and totals(conn, w1) == (ledger, reserved), totals(conn, w1)
        cs.assert_no_drift(conn, "after the console adjustments")
        after = dict(conn.execute(kinds_sql).fetchall())
        grew = {k: after.get(k, 0) - kinds.get(k, 0) for k in set(kinds) | set(after)}
        assert {k: n for k, n in grew.items() if n} == {"operator_adjustment": 3}, grew
        assert conn.execute("select count(*) from infrx.signup_entitlements").fetchone()[0] == \
            entitlements, "an operator change touched the signup grant"
        for user in (NOBODY, cc.UNGRANTED, None):
            code = adjust(conn, OPERATOR, user, "1", f"nf-{uuid.uuid4()}")[0]
            assert code == "not_found", f"{user}: {code}"
        return f"available {available} -> refused below zero, 0 exactly, restored; no drift"
    return ca._in_rollback(conn, body)


def check_operator_suspension(conn) -> str:
    """0009's audited `set_suspension` with the closed code `other` and the operator's prose
    as the audit reason; replay-safe both ways; restore clears the code; an unknown
    organization is `not_found`, a missing organization or status `invalid_request`."""
    def body():
        make_operator(conn)
        org = cc.personal_org(conn, cc.CONSUMER_2)
        key = f"sus-{uuid.uuid4()}"
        assert suspend(conn, OPERATOR, org, True, key, reason=" abuse report 7 ")[1] == \
            {"replayed": False, "suspended": True}
        assert suspended(conn, org) == (True, "other")
        (actor, action, target, reason, after), = audits(conn, f"app-operator:suspension:{key}")
        assert (actor, action, target, reason, after) == (
            f"operator:{OPERATOR}", "admin_set_suspension", org, "abuse report 7",
            {"suspended": True, "reason": "other"}), (actor, action, target, reason, after)
        assert suspend(conn, OPERATOR, org, True, key)[1] == {"replayed": True,
                                                              "suspended": True}
        back = f"restore-{uuid.uuid4()}"
        assert suspend(conn, OPERATOR, org, False, back)[1] == {"replayed": False,
                                                                "suspended": False}
        assert suspended(conn, org) == (False, None)
        assert suspend(conn, OPERATOR, org, False, back)[1] == {"replayed": True,
                                                                "suspended": False}
        assert suspended(conn, org) == (False, None), "a replay changed the state"
        assert suspend(conn, OPERATOR, NOBODY, True, f"x-{uuid.uuid4()}")[0] == "not_found"
        for org_, status in ((None, True), (org, None)):
            assert suspend(conn, OPERATOR, org_, status, f"x-{uuid.uuid4()}")[0] == \
                "invalid_request"
        return "suspend (other), replay, restore, replay; unknown org not_found"
    return ca._in_rollback(conn, body)


def check_operator_revoke(conn) -> str:
    """0009's one-way `revoke_key`, consumer keys only: once, then `replayed: true` for the
    same key AND for a new key on the already revoked credential - nothing changes twice
    (one audit row, the first `revoked_at`). An operator-audience key or an unknown id is
    `not_found`."""
    def body():
        make_operator(conn)
        key = f"rev-{uuid.uuid4()}"
        assert revoke(conn, OPERATOR, ca.C1_KEY, key)[1] == {"replayed": False}
        at = revoked_at(conn, ca.C1_KEY)
        assert at is not None
        (actor, action, _t, _r, after), = audits(conn, f"app-operator:revoke:{key}")
        assert (actor, action, after["key_id"]) == (f"operator:{OPERATOR}", "admin_key_revoke",
                                                    ca.C1_KEY)
        assert revoke(conn, OPERATOR, ca.C1_KEY, key)[1] == {"replayed": True}
        again = f"rev-{uuid.uuid4()}"
        assert revoke(conn, OPERATOR, ca.C1_KEY, again)[1] == {"replayed": True}, \
            "revoking a revoked key applied again"
        assert revoked_at(conn, ca.C1_KEY) == at and audits(conn, f"app-operator:revoke:{again}"
                                                            ) == []
        assert conn.execute("select count(*) from infrx.audit_entries where action = "
                            "'admin_key_revoke' and after->>'key_id' = %s",
                            (ca.C1_KEY,)).fetchone()[0] == 1, "a revocation audited twice"
        for key_id in (ca.OPERATOR_KEY, NOBODY, None):
            code = revoke(conn, OPERATOR, key_id, f"nf-{uuid.uuid4()}")[0]
            assert code == "not_found", f"{key_id}: {code}"
        assert revoked_at(conn, ca.OPERATOR_KEY) is None, "the operator key was revoked"
        return "revoked once; replay and re-revoke answer replayed; operator key not_found"
    return ca._in_rollback(conn, body)


def check_operator_views(conn) -> str:
    """The two operator-only reads: an operator reads the drifting wallet (drift as exact
    text) and the unknown-usage queue (the hold in its own unit, exact text); a consumer -
    even the owner of the queued job - reads neither, anon is refused, and no
    platform-side write reaches a wallet through them."""
    def body():
        make_operator(conn)
        world = ca.World(conn)
        job = co.unknown_job(conn, world, "d10-op-unknown")
        w2 = cc.wallet_of(conn, cc.CONSUMER_2)
        conn.execute("alter table infrx.credit_wallets disable trigger user")
        conn.execute("update infrx.credit_wallets set ledger_total = ledger_total + 1 "
                     "where wallet_id = %s", (w2,))
        conn.execute("alter table infrx.credit_wallets enable trigger user")
        drift_sql = ("select wallet_id::text, kind, ledger_drift, reserved_drift "
                     "from public.operator_wallet_drift")
        code, rows = rows_as(conn, OPERATOR, drift_sql)
        assert (code, rows) == (None, [(w2, "consumer", "1.00000000", "0.00000000")]), \
            (code, rows)
        queue_sql = ("select request_id::text, org_id::text, unit, hold, reconcile_after "
                     "from public.operator_unknown_usage")
        code, rows = rows_as(conn, OPERATOR, queue_sql)
        mine = [r for r in rows or () if r[0] == str(job.request_id)]
        assert code is None and len(mine) == 1, (code, rows)
        assert mine[0][1] == str(job.org_id) and mine[0][2] == "CREDIT" and \
            isinstance(mine[0][3], str) and Decimal(mine[0][3]) > 0 and \
            mine[0][4] is not None, mine
        for who in (cc.CONSUMER_1, cc.CONSUMER_2):
            for sql in (drift_sql, queue_sql):
                assert rows_as(conn, who, sql) == (None, []), f"{who} read {sql}"
        for sql in (drift_sql, queue_sql):
            assert rows_as(conn, "anon", sql)[0] == "42501", f"anon read {sql}"
        assert rows_as(conn, "service_role", drift_sql)[1], "the platform reads the drift"
        for write in ("update public.operator_wallet_drift set kind = kind",
                      "delete from public.operator_wallet_drift"):
            for who in ("service_role", OPERATOR):
                code, _rows = rows_as(conn, who, write + " returning wallet_id")
                assert code == "42501", f"{who}: {write} -> {code}"
        return f"operator reads drift {w2} and the queue; consumers, anon read nothing"
    return ca._in_rollback(conn, body)


def check_operator_privileges(conn) -> str:
    """Least privilege: EXECUTE on the three RPCs is `authenticated`'s only (never anon,
    service_role, the runtime or monitor logins); the helper is nobody's; each is SECURITY
    DEFINER with a pinned search_path, owned by the migration owner, the helper STABLE and
    the writers VOLATILE. The views are SELECT-only for authenticated and service_role and
    security_barrier; `infrx_runtime`'s function set is unchanged (DOOR-REVOKE's 41)."""
    owner, = conn.execute("select proowner::regrole::text from pg_proc where oid = "
                          "'public.consumer_credit_ledger(text,integer)'::regprocedure"
                          ).fetchone()
    for fn in RPCS + (HELPER,):
        for role in ROLES:
            may, = conn.execute("select has_function_privilege(%s, %s, 'execute')",
                                (role, fn)).fetchone()
            assert may == (role == "authenticated" and fn != HELPER), (fn, role, may)
        definer, config, volatility, fowner = conn.execute(
            "select prosecdef, proconfig, provolatile, proowner::regrole::text from pg_proc "
            "where oid = %s::regprocedure", (fn,)).fetchone()
        assert definer and any(c.startswith("search_path=") and "pg_temp" in c
                               for c in config or ()), (fn, definer, config)
        assert volatility == ("s" if fn == HELPER else "v"), (fn, volatility)
        assert fowner == owner, (fn, fowner, owner)
    verbs = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
    for view in VIEWS:
        for role in ROLES:
            for verb in verbs:
                has, = conn.execute("select has_table_privilege(%s, %s, %s)",
                                    (role, view, verb)).fetchone()
                assert has == (verb == "SELECT" and role in ("authenticated", "service_role")), \
                    (view, role, verb, has)
        options, vowner = conn.execute("select array_to_string(reloptions, ','), "
                                       "relowner::regrole::text from pg_class where oid = "
                                       "%s::regclass", (view,)).fetchone()
        assert "security_barrier=true" in (options or "") and vowner == owner, (view, options)
    held = {f for f, in conn.execute(
        "select p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid = "
        "p.pronamespace where n.nspname in ('infrx', 'public') "
        "and has_function_privilege('infrx_runtime', p.oid, 'execute') and not exists "
        "(select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')")}
    assert held == checks_reads.RUNTIME_FUNCTIONS and len(held) == 41, sorted(held)
    return (f"3 RPCs authenticated-only, helper nobody's, definer/{owner}; 2 views SELECT "
            f"for authenticated+service_role; runtime set {len(held)} unchanged")


# ---------------------------------------------------------------------- races
def _principal(connect, database: str, who: str):
    conn = connect(database)
    conn.execute("set statement_timeout = '30s'")
    conn.execute("set role authenticated")
    conn.execute("select set_config('request.jwt.claims', %s, false), "
                 "set_config('request.jwt.claim.sub', %s, false), "
                 "set_config('request.jwt.claim.role', 'authenticated', false)",
                 (json.dumps({"sub": who, "role": "authenticated"}), who))
    return conn


def _run(conn, sql: str, params) -> tuple[str | None, object]:
    try:
        return None, conn.execute(sql, params).fetchone()[0]
    except psycopg.Error as failed:
        mapped = domain_error(failed)
        return getattr(mapped, "code", failed.sqlstate), None


def race(owner, conns, calls) -> list[tuple]:
    """calls[0] runs in an OPEN transaction on conns[0]; the rest start in threads and must
    each be seen WAITING on a lock; then the first commits. Every answer, in order."""
    first = conns[0]
    first.execute("begin")
    out: list = [_run(first, *calls[0])] + [None] * (len(calls) - 1)

    def go(i):
        out[i] = _run(conns[i], *calls[i])
    threads = [threading.Thread(target=go, args=(i,)) for i in range(1, len(calls))]
    for thread in threads:
        thread.start()
    try:
        for conn in conns[1:len(calls)]:
            waiting_on_a_lock(owner, conn.info.backend_pid)
    finally:
        first.execute("commit")
        for thread in threads:
            thread.join(20)
    assert not any(t.is_alive() for t in threads), "a racing call never finished"
    return out


def applied_once(out) -> None:
    assert all(code is None for code, _a in out), f"a racing retry was refused: {out}"
    assert sorted(a["replayed"] for _c, a in out) == [False] + [True] * (len(out) - 1), out


def check_operator_races(connect, database: str) -> str:
    """DUR-CAP under real transactions (8 connections): the SAME console change racing 8
    ways applies exactly once (one `replayed: false`, 7 replays, one ledger/audit row) for
    an adjustment, a suspension and a revocation; the same key racing for ANOTHER target is
    `idempotency_conflict` for each - never a raw 23505; 5 corrections each taking more
    than half of what is available: exactly one fits, available never below zero."""
    owner = connect(database)
    make_operator(owner)
    conns = [_principal(connect, database, OPERATOR) for _ in range(8)]
    c1, c2 = cc.CONSUMER_1, cc.CONSUMER_2
    w1 = cc.wallet_of(owner, c1)
    org1, org2 = cc.personal_org(owner, c1), cc.personal_org(owner, c2)
    k1, k2 = new_key(owner, c1), new_key(owner, c2)
    report = []
    try:
        start = totals(owner, w1)[0]
        key = f"race-{uuid.uuid4()}"
        out = race(owner, conns, [(_ADJUST, (c1, "1", "race", key))] * 8)
        applied_once(out)
        assert totals(owner, w1)[0] - start == 1 and owner.execute(
            "select count(*) from infrx.credit_ledger where operation_id = %s",
            (op_id(key),)).fetchone()[0] == 1, "a racing adjustment applied twice"
        key = f"race-{uuid.uuid4()}"
        out = race(owner, conns, [(_SUSPEND, (org2, True, "race", key))] * 8)
        applied_once(out)
        assert len(audits(owner, f"app-operator:suspension:{key}")) == 1
        key = f"race-{uuid.uuid4()}"
        out = race(owner, conns, [(_REVOKE, (k1, "race", key))] * 8)
        applied_once(out)
        assert owner.execute("select count(*) from infrx.audit_entries where action = "
                             "'admin_key_revoke' and after->>'key_id' = %s",
                             (k1,)).fetchone()[0] == 1
        report.append("8-way: one effect each")
        for name, calls in (
                ("adjust", [(_ADJUST, (c1, "1", "race", "K")), (_ADJUST, (c2, "1", "race", "K"))]),
                ("suspension", [(_SUSPEND, (org1, True, "race", "K")),
                                (_SUSPEND, (org2, False, "race", "K"))]),
                ("revoke", [(_REVOKE, (k2, "race", "K")),
                            (_REVOKE, (new_key(owner, c1), "race", "K"))])):
            key = f"other-{uuid.uuid4()}"
            calls = [(sql, tuple(key if p == "K" else p for p in params))
                     for sql, params in calls]
            out = race(owner, conns, calls)
            assert out[0][0] is None and out[0][1]["replayed"] is False, (name, out)
            assert out[1][0] == "idempotency_conflict", \
                f"{name}: the same key for another target racing answered {out[1]}"
        report.append("another target: idempotency_conflict x3")
        ledger, reserved = totals(owner, w1)
        take = f"{-((ledger - reserved) / 2 + 1):.8f}"
        out = race(owner, conns[:5], [(_ADJUST, (c1, take, "race", f"take-{uuid.uuid4()}"))
                                      for _ in range(5)])
        codes = [c for c, _a in out]
        assert codes.count(None) == 1 and set(codes) <= {None, "invalid_request"}, codes
        now = totals(owner, w1)
        assert now[0] - now[1] >= 0 and now[0] == ledger + Decimal(take), now
        report.append("5 corrections: one fits")
        cs.assert_no_drift(owner, "after the operator races")
    finally:
        for conn in conns:
            conn.close()
        # restore what later checks on this database read
        owner.execute("update public.organizations set suspended = false, suspended_at = null, "
                      "suspension_reason = null where id in (%s, %s)", (org1, org2))
    return "; ".join(report)


__all__ = ["check_operator_adjust", "check_operator_authority", "check_operator_idempotency",
           "check_operator_privileges", "check_operator_races", "check_operator_revoke",
           "check_operator_suspension", "check_operator_views"]
