"""A1: the invariants migration 0015 and `infrx/state/signup.py` claim, against PostgreSQL.

Same contract as `checks.py` / `checks_credit.py`: each check raises `AssertionError` on
failure and returns a one-line summary, so `signup_mutants.py` can run it against a
single-edit mutant of 0015 (R32/R40). Every check runs on the D1R "credit" database
(`checks.seed_fixtures` + `checks_credit.seed_credit`) and creates its own individuals,
so the checks are independent of each other and of their order.

A claim that RAISES where the operation promises an answer is reported as an assertion
(`claim`): "answers with a status" is the invariant, so a crash breaks it.
"""
from __future__ import annotations

import threading
import uuid

import psycopg
from infrx.state import signup

from . import checks, checks_credit, pgharness
from .checks_credit import attempt, personal_org, set_flag, wallet_of

CONFIRMED = "2026-09-22T00:00:00+00:00"
GRANT = "10000.00000000"


def uid(group: int, n: int) -> str:
    """A v4-shaped id per check (`group`) and individual (`n`)."""
    return f"a100000{group:x}-0000-4000-8000-{n:012d}"


# --- GoTrue's columns and individuals -------------------------------------------
def gotrue_columns(conn) -> None:
    """`email_confirmed_at` and `deleted_at` exist on every hosted `auth.users` (GoTrue's
    own migrations); the shim has the first, the bare `supabase/postgres` image neither.
    Added here exactly as GoTrue has them - on the Supabase image as `supabase_admin`,
    since `postgres` does not own `auth.users` there."""
    for column in ("email_confirmed_at", "deleted_at"):
        statement = f"alter table auth.users add column if not exists {column} timestamptz"
        try:
            conn.execute(statement)
        except psycopg.errors.InsufficientPrivilege:
            pgharness._sb(conn.info.dbname, statement)


def individual(conn, user: str, email: str, *, confirmed: bool = True) -> None:
    """One auth user; 0001's trigger makes the profile and the personal organization."""
    conn.execute("insert into auth.users (id, email, email_confirmed_at) values (%s, %s, %s)",
                 (user, email, CONFIRMED if confirmed else None))


def claim(conn, user: str, campaign: str = "launch_2026_09", op: str | None = None) -> tuple:
    try:
        return conn.execute("select * from public.claim_signup_grant(%s, %s, %s)",
                            (user, campaign, op)).fetchone()
    except psycopg.Error as raised:
        raise AssertionError(f"claim for {user} raised instead of answering: "
                             f"{raised.sqlstate} {str(raised).splitlines()[0][:160]}") from None


def one(conn, sql: str, params=()):
    return conn.execute(sql, params).fetchone()[0]


def ledger_rows(conn, user: str) -> int:
    return one(conn, "select count(*) from infrx.credit_ledger l join infrx.credit_wallets w "
                     "using (wallet_id) where w.owner_user_id = %s", (user,))


def entitlements(conn, user: str) -> int:
    return one(conn, "select count(*) from infrx.signup_entitlements where user_id = %s",
               (user,))


def denial(conn, user: str, reason: str) -> int:
    row = conn.execute("select attempts from infrx.signup_denials where user_id = %s and "
                       "reason = %s", (user, reason)).fetchone()
    return row[0] if row else 0


def refused(conn, label: str, sql: str, code: str, contains: str = ""):
    why = attempt(conn, sql)
    assert why is not None and why.startswith(code) and contains in why, \
        f"{label}: expected {code} {contains}, got {why!r}"


class _Allowed(Exception):
    pass


def _as(conn, session: str, sql: str) -> str | None:
    """Run `sql` under a session statement (anon, service or a JWT), rolled back."""
    try:
        with conn.transaction():
            conn.execute(session)
            conn.execute(sql)
            raise _Allowed()
    except _Allowed:
        return None
    except psycopg.Error as failed:
        return f"{failed.sqlstate} {str(failed).splitlines()[0][:120]}"


# =============================================================================
# item 1/2: the eligibility operation
# =============================================================================
def check_eligibility(conn) -> str:
    """CREDIT-GRANT / CREDIT-IDENTITY / R71 / R72 through `claim_signup_grant`: a verified
    individual receives exactly one +10000.00000000 grant whose evidence the database
    derived; every retry (another campaign, operation id, organization, or after the
    verification is withdrawn) answers the same grant; unverified, soft-deleted and
    unknown users receive nothing and read alike; the same verified address cannot fund
    a second individual; a shared personal org or a nonzero legacy USD balance is a
    rollout hold (USD untouched); denials are recorded; flag off is maintenance."""
    gotrue_columns(conn)
    v, u, d, r, h, z = (uid(1, n) for n in range(1, 7))
    individual(conn, v, "v1@example.com")
    individual(conn, u, "u1@example.com", confirmed=False)
    individual(conn, d, "d1@example.com")
    conn.execute("update auth.users set deleted_at = now() where id = %s", (d,))
    individual(conn, r, "  V1@Example.COM ")          # the same address, another account
    individual(conn, h, "h1@example.com")
    individual(conn, z, "z1@example.com")
    neg = uid(1, 7)                                   # a single negative legacy USD row
    individual(conn, neg, "neg1@example.com")

    first = claim(conn, v)
    assert first[0] == "granted" and first[4] == GRANT and str(first[1]) == v, \
        f"verified individual: {first}"
    evidence, campaign = conn.execute(
        "select verification_evidence_ref, campaign_version from infrx.signup_entitlements "
        "where user_id = %s", (v,)).fetchone()
    derived = one(conn, "select verification_evidence_ref from infrx.verified_user(%s)", (v,))
    assert evidence == derived and evidence.startswith("email_confirmed_at/"), \
        f"the evidence is not the database's own: {evidence!r} vs {derived!r}"
    assert campaign == "launch_2026_09", f"campaign not recorded as audit: {campaign!r}"
    conn.execute("insert into public.organizations (name, slug, created_by) values "
                 "('second', 'a1-second-org', %s)", (v,))
    again = claim(conn, v, campaign="relaunch_2027", op="00000000-0000-4000-8000-00000000a101")
    assert again[0] == "replayed" and again[3] == first[3] and again[2] == first[2], \
        f"a retry (campaign/op/org) minted or moved the grant: {again}"
    conn.execute("update auth.users set email_confirmed_at = null where id = %s", (v,))
    withdrawn = claim(conn, v)
    conn.execute("update auth.users set email_confirmed_at = %s where id = %s", (CONFIRMED, v))
    assert withdrawn[0] == "replayed" and withdrawn[3] == first[3], \
        f"a granted individual whose verification was withdrawn: {withdrawn}"
    assert ledger_rows(conn, v) == 1 and entitlements(conn, v) == 1, "more than one grant"

    for label, user in (("unverified", u), ("soft-deleted", d)):
        got = claim(conn, user)
        assert got[0] == "unverified" and got[2] is None, f"{label}: {got}"
        assert wallet_of(conn, user) is None and entitlements(conn, user) == 0, \
            f"{label} left a wallet or entitlement"
    claim(conn, u)
    assert denial(conn, u, "unverified") == 2, "the unverified denial is not recorded"
    stranger = str(uuid.uuid4())
    got = claim(conn, stranger)
    assert got[0] == "unverified" and got[2] is None, f"unknown user: {got}"
    assert not conn.execute("select 1 from infrx.signup_denials where user_id = %s",
                            (stranger,)).fetchone(), "a denial row confirms an unknown id"
    conn.execute("update auth.users set email_confirmed_at = %s where id = %s", (CONFIRMED, u))
    later = claim(conn, u)
    assert later[0] == "granted" and ledger_rows(conn, u) == 1, f"verified later: {later}"

    reused = claim(conn, r)
    assert reused[0] == "identity_reused" and wallet_of(conn, r) is None, \
        f"a second account with the same verified address: {reused}"
    assert denial(conn, r, "identity_reused") == 1, "identity reuse not recorded"

    shared = checks_credit.SHARED
    conn.execute("update auth.users set email_confirmed_at = %s where id = %s",
                 (CONFIRMED, shared))
    held = claim(conn, shared)
    assert held[0] == "rollout_hold" and wallet_of(conn, shared) is None, \
        f"shared personal org: {held}"
    assert not conn.execute("select 1 from infrx.signup_identity_claims where user_id = %s",
                            (shared,)).fetchone(), "a refused grant kept its identity claim"

    usd = ("insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
           "values (%s, %s, %s, 'legacy')")
    conn.execute(usd, (personal_org(conn, h), "5.000000", "grant"))
    conn.execute(usd, (personal_org(conn, z), "5.000000", "grant"))
    conn.execute(usd, (personal_org(conn, z), "-5.000000", "usage"))
    conn.execute(usd, (personal_org(conn, neg), "-0.000001", "usage"))
    before = conn.execute("select org_id, delta_usd::text from public.credit_ledger "
                          "order by id").fetchall()
    hold = claim(conn, h)
    assert hold[0] == "rollout_hold" and wallet_of(conn, h) is None, \
        f"nonzero legacy USD (R72): {hold}"
    assert denial(conn, h, "rollout_hold") == 1, "rollout hold not recorded"
    owed = claim(conn, neg)
    assert owed[0] == "rollout_hold" and wallet_of(conn, neg) is None, \
        f"a negative legacy USD balance is nonzero too (R72): {owed}"
    settled = claim(conn, z)
    assert settled[0] == "granted", f"a zero legacy USD balance is no hold: {settled}"
    after = conn.execute("select org_id, delta_usd::text from public.credit_ledger "
                         "order by id").fetchall()
    assert after == before, "the grant touched USD history"

    with conn.transaction():
        set_flag(conn, "signup_grant", False)
        why = attempt(conn, "select * from public.claim_signup_grant(%s)", (h,))
        raise psycopg.Rollback()
    assert why is not None and why.startswith("55000"), f"flag off: {why!r}"
    refused(conn, "no user", "select * from public.claim_signup_grant(null)", "22023")
    return ("eligibility: 1 grant per individual, 3 replays, unverified/soft-deleted/unknown "
            "alike, identity reuse, 3 rollout holds (+, -), USD untouched, flag and null "
            "refused")


# =============================================================================
# item 1/4: the frozen personal-org binding, provider roles, cross-user access
# =============================================================================
def check_binding(conn) -> str:
    """CREDIT-IDENTITY: once a personal org funds a wallet, nobody joins it and its owner
    is neither removed, demoted nor moved; joining another organization or gaining and
    losing a provider role issues no grant and changes no wallet; another individual's
    wallet cannot be spent through one's own organization or read in a session."""
    gotrue_columns(conn)
    b, w, p = uid(2, 1), uid(2, 2), uid(2, 3)
    for user, email in ((b, "b2@example.com"), (w, "w2@example.com"), (p, "p2@example.com")):
        individual(conn, user, email)
        assert claim(conn, user)[0] == "granted", f"{user} not granted"
    ob, ow, wb = personal_org(conn, b), personal_org(conn, w), wallet_of(conn, b)
    for label, sql in (
        ("another member joins a bound personal org",
         f"insert into public.org_members (org_id, user_id, role) values ('{ob}', '{w}', "
         "'member')"),
        ("the owner leaves", f"delete from public.org_members where org_id = '{ob}'"),
        ("the owner is demoted",
         f"update public.org_members set role = 'member' where org_id = '{ob}'"),
        ("the owner's membership is moved to another org",
         f"update public.org_members set org_id = '{checks.ORG_A}' where org_id = '{ob}'"),
    ):
        refused(conn, label, sql, "23514")
    # Joining another (non-personal) organization is allowed and changes nothing.
    conn.execute("insert into public.org_members (org_id, user_id, role) values (%s, %s, "
                 "'member')", (checks.ORG_A, b))
    conn.execute("insert into infrx.provider_memberships (provider_org_id, user_id, role, "
                 "granted_by) values (%s, %s, 'developer', 'ops@infrx')",
                 (checks_credit.NEMO, p))
    provider_total = one(conn, "select ledger_total::text from infrx.credit_wallets "
                               "where wallet_id = %s", (checks_credit.PROVIDER_WALLET,))
    for user in (b, p):
        got = claim(conn, user, campaign="provider_launch")
        assert got[0] == "replayed", f"{user}: a new membership re-opened eligibility: {got}"
    conn.execute("update infrx.provider_memberships set revoked_at = infrx.now() "
                 "where user_id = %s", (p,))
    assert claim(conn, p)[0] == "replayed", "a revoked provider role re-opened eligibility"
    assert one(conn, "select ledger_total::text from infrx.credit_wallets where wallet_id = %s",
               (checks_credit.PROVIDER_WALLET,)) == provider_total, \
        "a consumer grant touched the provider dev wallet"
    for user in (b, w, p):
        assert ledger_rows(conn, user) == 1 and entitlements(conn, user) == 1, user
    assert wallet_of(conn, b) == wb and one(
        conn, "select personal_org_id from infrx.credit_wallets where wallet_id = %s",
        (wb,)) == uuid.UUID(ob), "the wallet binding moved"
    # Cross-user: W's organization cannot spend B's wallet; B's own org can (control).
    job = checks_credit.credit_job(uid(2, 90), "job_a1_cross", ow, wb)
    refused(conn, "B's wallet admitted through W's organization", job, "23514")
    assert attempt(conn, checks_credit.credit_job(uid(2, 91), "job_a1_own", ob, wb)) is None, \
        "control: the owner's own organization could not admit"
    seen = _rows_as(conn, checks._jwt(w), "select wallet_id from public.console_credit_wallets")
    assert seen == [(uuid.UUID(wallet_of(conn, w)),)], f"W's session sees: {seen}"
    return "binding: 4 membership changes refused, 2 memberships no regrant, cross-user refused"


def _rows_as(conn, session: str, sql: str) -> list:
    with conn.transaction():
        conn.execute(session)
        out = conn.execute(sql).fetchall()
        raise psycopg.Rollback()
    return out


# =============================================================================
# item 3: direct browser financial writes stay denied
# =============================================================================
def check_signup_privileges(conn) -> str:
    """R59: no browser role may claim, retire, grant, or touch the signup relations; the
    platform role may call the two operations and read the relations, never write them."""
    gotrue_columns(conn)
    me = uid(3, 1)
    individual(conn, me, "me3@example.com", confirmed=False)
    sessions = {"anon": checks.SESSIONS["anon"], "self": checks._jwt(me),
                "granted": checks._jwt(checks_credit.CONSUMER_1)}
    attacks = (
        ("claim", f"select * from public.claim_signup_grant('{me}')"),
        ("retire", f"select infrx.retire_individual('{me}', 'x', 'x', 'x')"),
        ("grant seam", f"select * from infrx.grant_signup_credit('{me}', 'e')"),
        ("record denial", f"select infrx.record_signup_denial('{me}', 'unverified')"),
        ("insert claim", "insert into infrx.signup_identity_claims values "
                         f"(repeat('a', 64), '{me}')"),
        ("read denials", "select * from infrx.signup_denials"),
        ("insert retirement", f"insert into infrx.retired_individuals values ('{me}', 'x', 'x')"),
        ("insert ledger", "insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
                          "operation_id, actor) values (gen_random_uuid(), 'consumer', "
                          "'signup_grant', 10000, gen_random_uuid(), 'me')"),
    )
    n = 0
    for name, session in sessions.items():
        for label, sql in attacks:
            why = _as(conn, session, sql)
            assert why is not None and why.startswith("42501"), \
                f"{name} may {label}: {why!r}"
            n += 1
    service = checks.SESSIONS["service"]
    assert _as(conn, service, f"select * from public.claim_signup_grant('{me}')") is None, \
        "service_role may not call the eligibility operation"
    assert _as(conn, service, "select * from infrx.signup_denials") is None, \
        "service_role may not read the denials"
    for label, sql in (
        ("insert claim", f"insert into infrx.signup_identity_claims values (repeat('b', 64), "
                         f"'{me}')"),
        ("insert retirement", f"insert into infrx.retired_individuals values ('{me}', 'x', 'x')"),
        ("edit denials", "update infrx.signup_denials set attempts = 1"),
        ("record denial", f"select infrx.record_signup_denial('{me}', 'unverified')"),
    ):
        why = _as(conn, service, sql)
        assert why is not None and why.startswith("42501"), f"service_role may {label}: {why!r}"
        n += 1
    for fn, callers in (("public.claim_signup_grant(uuid,text,uuid)", {"service_role"}),
                        ("infrx.retire_individual(uuid,text,text,text)", {"service_role"})):
        for role in ("anon", "authenticated", "service_role"):
            may = one(conn, "select has_function_privilege(%s, %s, 'execute')", (role, fn))
            assert may == (role in callers), f"{role} execute {fn}: {may}"
    return f"privileges: {n} direct writes/reads refused; service_role calls the 2 operations"


# =============================================================================
# item 4: races
# =============================================================================
def check_claim_race(connect, database: str, attempts: int = 8, rounds: int = 10) -> str:
    """CREDIT-GRANT under a race: each round, one fresh verified individual is claimed by
    `attempts - 1` concurrent callback retries and one concurrent backfill run; exactly
    one grant and one issuer. Then 3 rounds of `attempts` DIFFERENT individuals sharing
    one verified address: exactly one of them is granted, the rest `identity_reused`."""
    with connect(database) as c:
        gotrue_columns(c)

    def race(users: list[str], with_backfill: bool) -> tuple[list, list, list]:
        barrier = threading.Barrier(len(users) + with_backfill)
        out, runs, errors = [], [], []

        def call(user) -> None:
            try:
                with connect(database) as c:
                    barrier.wait()
                    out.append(claim(c, user))
            except Exception as failed:               # reported, never swallowed
                errors.append(f"{type(failed).__name__}: {str(failed)[:200]}")

        def run_backfill() -> None:
            try:
                with connect(database) as c:
                    barrier.wait()
                    runs.append(signup.backfill(c, campaign="race_backfill"))
            except Exception as failed:
                errors.append(f"backfill {type(failed).__name__}: {str(failed)[:200]}")

        threads = [threading.Thread(target=call, args=(u,)) for u in users]
        if with_backfill:
            threads.append(threading.Thread(target=run_backfill))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return out, runs, errors

    for n in range(rounds):
        user = uid(4, n)
        with connect(database) as c:
            individual(c, user, f"race{n}@example.com")
        out, runs, errors = race([user] * (attempts - 1), True)
        assert not errors and len(out) == attempts - 1 and len(runs) == 1, \
            f"round {n}: callers failed: {errors}"
        # One issuer: at most one callback saw `granted`; if none did, the backfill issued.
        callers = sum(1 for row in out if row[0] == "granted")
        assert callers <= 1 and (callers or runs[0].get("granted", 0) >= 1), \
            f"round {n}: {callers} issuing callers ({out}, backfill {runs[0]})"
        assert {row[3] for row in out} == {out[0][3]} and out[0][3] is not None, \
            f"round {n}: callers got different grants"
        with connect(database) as c:
            assert ledger_rows(c, user) == 1, f"round {n}: not exactly one ledger row"
    for n in range(3):
        users = [uid(5, n * 100 + k) for k in range(attempts)]
        with connect(database) as c:
            for k, user in enumerate(users):
                # Distinct strings (GoTrue's auth.users keeps addresses unique), one address.
                individual(c, user, " " * k + (f"Same{n}@example.com" if k % 2
                                               else f"same{n}@example.com"))
        out, _, errors = race(users, False)
        assert not errors and len(out) == attempts, f"identity round {n}: {errors}"
        statuses = sorted(row[0] for row in out)
        assert statuses == ["granted"] + ["identity_reused"] * (attempts - 1), \
            f"identity round {n}: {statuses}"
    return (f"{rounds} rounds x {attempts} (retries + backfill) -> 1 issuer; 3 rounds x "
            f"{attempts} accounts on one address -> 1 grant")


# =============================================================================
# item 5: retention
# =============================================================================
def retire(conn, user: str, key: str = "retire-1") -> object:
    try:
        return one(conn, "select infrx.retire_individual(%s, 'ops@infrx', 'account deletion "
                         "request', %s)", (user, key))
    except psycopg.Error as raised:
        raise AssertionError(f"retire {user} raised: {raised.sqlstate} "
                             f"{str(raised).splitlines()[0][:160]}") from None


def check_retirement(conn) -> str:
    """The retention policy: a wallet owner is retired, never deleted - profile
    anonymised, keys revoked, personal org suspended (audited), wallet frozen (no hold,
    no signup grant), ledger and entitlement kept; idempotent; a hard delete is refused;
    the same verified address on a new account is not a new individual."""
    gotrue_columns(conn)
    t, n, o, t2 = uid(6, 1), uid(6, 2), uid(6, 3), uid(6, 4)
    individual(conn, t, "t6@example.com")
    individual(conn, n, "n6@example.com")
    individual(conn, o, "o6@example.com")
    for user in (t, o):
        assert claim(conn, user)[0] == "granted"
    ot, wt, oo, wo = (personal_org(conn, t), wallet_of(conn, t), personal_org(conn, o),
                      wallet_of(conn, o))
    conn.execute("insert into public.api_keys (org_id, created_by, name, prefix, key_hash) "
                 "values (%s, %s, 'k', 'sk-infrx-a1000006', 'a1-retire-hash')", (ot, t))
    money = ("select ledger_total::text, (select count(*) from infrx.credit_ledger l "
             "where l.wallet_id = w.wallet_id), (select count(*) from infrx.signup_entitlements "
             "e where e.wallet_id = w.wallet_id) from infrx.credit_wallets w where wallet_id = %s")
    before = conn.execute(money, (wt,)).fetchone()
    spend = (checks_credit.credit_job(uid(6, 90), "job_a1_frozen", ot, wt) + ";\n"
             + checks_credit.hold(uid(6, 90), ot, wt))
    assert attempt(conn, spend) is None, "control: the wallet could not reserve before"
    # A hold admitted BEFORE retirement, committed: it must still settle afterwards.
    pre = uid(6, 92)
    conn.execute(checks_credit.credit_job(pre, "job_a1_pre", ot, wt) + ";\n"
                 + checks_credit.hold(pre, ot, wt))

    first = retire(conn, t)
    assert retire(conn, t, "retire-2") == first, "a second retirement changed the record"
    profile = conn.execute("select email, full_name from public.profiles where id = %s",
                           (t,)).fetchone()
    assert profile == (f"retired+{t}@invalid", None), f"profile not anonymised: {profile}"
    assert one(conn, "select count(*) from public.api_keys where created_by = %s and "
                     "revoked_at is null", (t,)) == 0, "a retired individual's key still works"
    assert one(conn, "select suspended from public.organizations where id = %s", (ot,)), \
        "the personal organization is not suspended"
    assert one(conn, "select count(*) from infrx.audit_entries where target_org_id = %s and "
                     "action = 'admin_set_suspension'", (ot,)) == 1, "suspension not audited"
    refused(conn, "a retired wallet reserves", spend, "23514", "frozen")
    assert attempt(conn, checks_credit.credit_job(uid(6, 91), "job_a1_other", oo, wo) + ";\n"
                   + checks_credit.hold(uid(6, 91), oo, wo)) is None, \
        "control: another individual's wallet was frozen too"
    assert claim(conn, t)[0] == "retired" and denial(conn, t, "retired") == 1, \
        "a retired individual's claim"
    assert conn.execute(money, (wt,)).fetchone() == before, "retirement moved money history"
    # The acceptance half of "frozen": in-flight work settles, D5 corrections land.
    ledger = ("insert into infrx.credit_ledger (wallet_id, wallet_kind, kind, amount, "
              "operation_id, request_id, actor, reason) values ")
    settle = (f"update infrx.credit_wallet_holds set state = 'settled' where request_id = "
              f"'{pre}'; " + ledger + f"('{wt}', 'consumer', 'inference_debit', -9.976, "
              f"gen_random_uuid(), '{pre}', 'svc', '')")
    why = attempt(conn, settle)
    assert why is None, f"a hold admitted before retirement could not settle: {why}"
    why = attempt(conn, ledger + f"('{wt}', 'consumer', 'operator_adjustment', 5, "
                                 "gen_random_uuid(), null, 'ops@infrx', 'goodwill')")
    assert why is None, f"a D5 compensating entry into a frozen wallet was refused: {why}"

    retire(conn, n)
    assert claim(conn, n)[0] == "retired" and wallet_of(conn, n) is None, \
        "a retired, never-granted individual was granted"
    refused(conn, "the grant seam into a retired individual's wallet",
            f"select * from infrx.grant_signup_credit('{n}', 'e')", "23514", "frozen")

    refused(conn, "a hard delete of a wallet owner", f"delete from auth.users where id = '{t}'",
            "23503")
    # GoTrue's soft delete keeps the row and obfuscates the address; the address comes back.
    conn.execute("update auth.users set deleted_at = now(), email = %s where id = %s",
                 (f"deleted-{t}@invalid", t))
    individual(conn, t2, "T6@example.com")
    again = claim(conn, t2)
    assert again[0] == "identity_reused" and wallet_of(conn, t2) is None, \
        f"a re-created account with a granted address: {again}"
    refused(conn, "retiring nobody", f"select infrx.retire_individual('{uuid.uuid4()}', 'o', "
                                     "'r', 'k')", "P0002")
    return ("retirement: anonymised, keys revoked, org suspended, wallet frozen (pre-retirement "
            "hold settles, adjustment lands), money kept, "
            "idempotent, hard delete refused, re-created address refused")


# =============================================================================
# item 3: existing-user backfill over the hosted accounts' shape (I1B)
# =============================================================================
HOSTED = tuple(uid(7, n) for n in range(1, 5))        # 4 accounts, the 4th unconfirmed


def seed_hosted(conn) -> None:
    """The four hosted accounts as I1B inventoried them (2026-09-22): 4 auth users (3
    confirmed), 4 personal orgs each with its single owner, 2 active keys, 1 usage event
    ($0.00019660), 0 USD ledger rows. Written with 0001's columns only, so it runs on the
    0001-0002 schema before the upgrade as well as after it."""
    gotrue_columns(conn)
    for n, user in enumerate(HOSTED, 1):
        individual(conn, user, f"hosted{n}@example.com", confirmed=n < 4)
    keys = []
    for n, user in enumerate(HOSTED[:2], 1):
        keys.append(one(conn, "insert into public.api_keys (org_id, created_by, name, prefix, "
                              "key_hash) values (%s, %s, 'default', %s, %s) returning id",
                        (personal_org(conn, user), user, f"sk-infrx-hosted{n:02d}",
                         f"a1-hosted-hash-{n}")))
    conn.execute("insert into public.usage_events (id, org_id, api_key_id, model_id, status, "
                 "prompt_tokens, completion_tokens, cost_usd) values (%s, %s, %s, "
                 "'nemostation/marlin-2b', 200, 900, 28, 0.00019660)",
                 (uid(7, 90), personal_org(conn, HOSTED[0]), keys[0]))


def _hosted_usd(conn) -> tuple:
    orgs = [personal_org(conn, u) for u in HOSTED]
    return (conn.execute("select org_id, delta_usd::text from public.credit_ledger "
                         "where org_id = any(%s::uuid[]) order by id", (orgs,)).fetchall(),
            conn.execute("select id, cost_usd::text from public.usage_events "
                         "where org_id = any(%s::uuid[]) order by id", (orgs,)).fetchall(),
            conn.execute("select id, revoked_at from public.api_keys "
                         "where org_id = any(%s::uuid[]) order by id", (orgs,)).fetchall())


def check_backfill(conn, page: int = 3, expect_first: dict | None = None) -> str:
    """CREDIT-GRANT/UNITS over pre-cutover rows: the backfill (the same operation, one
    transaction per individual, keyset pages) grants each confirmed hosted account exactly
    once, the unconfirmed one nothing (recorded); legacy USD, usage and keys are unchanged
    and the USD statement stays 0 with no hold; a second run grants nothing."""
    set_flag(conn, "signup_grant", True)
    before = _hosted_usd(conn)
    first = signup.backfill(conn, campaign="backfill_2026_09", page=page)
    assert expect_first is None or first == expect_first, f"first backfill: {dict(first)}"
    for user in HOSTED[:3]:
        assert entitlements(conn, user) == 1 and ledger_rows(conn, user) == 1, \
            f"{user}: not granted exactly once by the backfill ({first})"
        assert one(conn, "select ledger_total::text from infrx.credit_wallets "
                         "where owner_user_id = %s", (user,)) == GRANT
    assert wallet_of(conn, HOSTED[3]) is None and denial(conn, HOSTED[3], "unverified") == 1, \
        "the unconfirmed account was granted or its denial not recorded"
    assert _hosted_usd(conn) == before, "the backfill changed legacy USD, usage or keys"
    with conn.transaction():
        conn.execute(checks.SESSIONS["service"])
        for user in HOSTED:
            balance, hold = conn.execute(
                "select balance, rollout_hold from public.console_legacy_usd_statement(%s)",
                (personal_org(conn, user),)).fetchone()
            assert (balance, hold) == ("0.00000000", False), f"{user}: USD {balance} {hold}"
        raise psycopg.Rollback()
    rows = one(conn, "select count(*) from infrx.credit_ledger")
    second = signup.backfill(conn, campaign="backfill_2026_09", page=page)
    assert "granted" not in second and one(conn, "select count(*) from infrx.credit_ledger") \
        == rows, f"a second backfill granted again: {second}"
    return f"backfill: first {dict(first)}, second {dict(second)}; USD/usage/keys unchanged"
