#!/usr/bin/env python3
"""A1: the individual signup grant (migration 0015, `infrx/state/signup.py`).

Database cases run in the D harness's labelled container, on either image:

    uv run --frozen pytest -q tests/d/test_signup.py
    INFRX_D1_IMAGE=supabase uv run --frozen pytest -q tests/d/test_signup.py

The `test_answer__*`, `test_identity_from__*`, `test_grant_initial__*` and
`test_backfill__*` cases need no database: they are what the code mutants name. A
`HarnessBusy` refusal means another checkout holds the port: retry, never remove it.
"""
from __future__ import annotations

import asyncio
import contextlib
import operator
import os
import re
import uuid
from datetime import datetime, timezone

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance.v2_fakes import FakeWalletDirectory
from infrx.contracts.v2.records import INITIAL_SIGNUP_GRANT
from infrx.operations import service
from infrx.operations.ports import VerifiedIdentity
from infrx.state import migrations, signup

from ..contracts import mutants as shared
from . import checks, checks_credit, checks_signup, pgharness, signup_mutants

SIGNUP_DB = f"{pgharness.DATABASE}_signup"
HOSTED_DB = f"{pgharness.DATABASE}_signup_hosted"
_reason = pgharness.unavailable()
needs_pg = pytest.mark.skipif(_reason is not None,
                              reason=f"task-local PostgreSQL unavailable: {_reason}")

USER = "a1000009-0000-4000-8000-000000000001"
ORG = "a1000009-0000-4000-8000-000000000002"
OTHER_ORG = "a1000009-0000-4000-8000-000000000003"
WALLET = "a1000009-0000-4000-8000-000000000004"
OP = "a1000009-0000-4000-8000-000000000005"
AT = datetime(2026, 9, 22, tzinfo=timezone.utc)
IDENTITY = VerifiedIdentity(USER, ORG, "email_confirmed_at/2026-09-22")
ROW = (uuid.UUID(USER), uuid.UUID(WALLET), "10000.00000000", "email_confirmed_at/2026-09-22",
       uuid.UUID(OP), "launch", AT, uuid.UUID(ORG))


# =============================================================================
# pure cases (no database): the port mapping and the backfill's control flow
# =============================================================================
def test_answer__granted_and_replayed() -> None:
    grant, replayed = signup.answer("granted", ROW, IDENTITY)
    assert not replayed and grant.amount == INITIAL_SIGNUP_GRANT
    assert (grant.user_id, grant.wallet_id, grant.ledger_operation_id) == (USER, WALLET, OP)
    _, replayed = signup.answer("replayed", ROW, IDENTITY)
    assert replayed, "a replay was reported as a new grant"


def test_answer__denials_are_not_found_or_forbidden() -> None:
    with pytest.raises(errors.NotFound):
        signup.answer("unverified", None, IDENTITY)
    for status in ("identity_reused", "rollout_hold", "retired"):
        with pytest.raises(errors.Forbidden, match=status):
            signup.answer(status, None, IDENTITY)


def test_answer__a_grant_bound_elsewhere_is_forbidden() -> None:
    with pytest.raises(errors.Forbidden, match="another organization"):
        signup.answer("granted", ROW, VerifiedIdentity(USER, OTHER_ORG, "e"))


def test_identity_from__unverified_and_orgless_are_none() -> None:
    assert signup.identity_from(None) is None
    assert signup.identity_from((uuid.UUID(USER), uuid.UUID(ORG), None)) is None
    assert signup.identity_from((uuid.UUID(USER), None, "e")) is None
    assert signup.identity_from((uuid.UUID(USER), uuid.UUID(ORG), "e")) == \
        VerifiedIdentity(USER, ORG, "e")


class _Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _AsyncConn:
    """Enough of psycopg's AsyncConnection to see what the adapter runs, and where."""

    def __init__(self, status: str, row):
        self.status, self.row, self.log = status, row, []

    async def execute(self, sql, params):
        self.log.append((sql, params))
        return _Cursor((self.status,) if sql == signup.CLAIM else self.row)

    @contextlib.asynccontextmanager
    async def transaction(self):
        self.log.append("begin")
        try:
            yield
        except BaseException:
            self.log.append("rollback")
            raise
        self.log.append("commit")


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @contextlib.asynccontextmanager
    async def connection(self):
        yield self.conn


def test_grant_initial__one_transaction_rolled_back_on_refusal() -> None:
    conn = _AsyncConn("granted", ROW)
    grant, replayed = asyncio.run(signup.PgSignup(_Pool(conn)).grant_initial(IDENTITY, OP, AT))
    assert (grant.wallet_id, replayed) == (WALLET, False)
    assert conn.log[0] == "begin" and conn.log[-1] == "commit", conn.log
    assert conn.log[1] == (signup.CLAIM, (USER, "", OP)), "the G6B operation id was not passed"
    conn = _AsyncConn("granted", ROW)
    with pytest.raises(errors.Forbidden):
        asyncio.run(signup.PgSignup(_Pool(conn)).grant_initial(
            VerifiedIdentity(USER, OTHER_ORG, "e"), OP, AT))
    assert conn.log[0] == "begin" and conn.log[-1] == "rollback", \
        f"a refused answer left the grant committed: {conn.log}"


def test_grant_initial__a_denial_commits_its_recorded_reason() -> None:
    for status, error in (("unverified", errors.NotFound), ("rollout_hold", errors.Forbidden)):
        conn = _AsyncConn(status, None)
        with pytest.raises(error):
            asyncio.run(signup.PgSignup(_Pool(conn)).grant_initial(IDENTITY, OP, AT))
        assert conn.log[0] == "begin" and conn.log[-1] == "commit", \
            f"{status}: the denial row was rolled back with the answer: {conn.log}"


class FakeRefusal(Exception):
    def __init__(self, sqlstate: str, message: str = "refused"):
        super().__init__(message)
        self.sqlstate = sqlstate


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


#: The keyset comparison `signup.PAGE` states, applied by the fake as the database would.
_KEYSET = re.compile(r"where id (>=|>) %s order by id limit %s$")
_OPS = {">": operator.gt, ">=": operator.ge}


class _SyncConn:
    """psycopg's sync Connection as `backfill` uses it: keyset pages and one claim per
    individual, recording whether each claim ran inside its own transaction. The page
    query is read from `signup.PAGE` itself, so an edit to its comparison reaches here;
    `stuck` answers the first page forever (a keyset that never advances)."""

    def __init__(self, users, refuse=(), maintenance=False, stuck=False, denied=False):
        self.users = sorted(uuid.UUID(u) for u in users)
        self.refuse, self.maintenance, self.stuck = set(refuse), maintenance, stuck
        self.denied = denied
        self.depth, self.claims, self.pages = 0, [], 0

    def execute(self, sql, params):
        if sql == signup.PAGE:
            match = _KEYSET.search(sql)
            assert match, f"the page query is no longer a keyset: {sql!r}"
            after, limit = params
            self.pages += 1
            assert self.pages <= 2 * len(self.users) + 2, "the backfill loops on one page"
            keep = _OPS[match.group(1)]
            rows = [(u,) for u in self.users if self.stuck or keep(u, after)][:limit]
            return _Result(rows)
        user = str(params[0])
        self.claims.append((user, self.depth))
        if self.maintenance:
            raise FakeRefusal("55000", "maintenance: signup_grant is not enabled")
        if self.denied:
            raise FakeRefusal("42501", "permission denied for function claim_signup_grant")
        if user in self.refuse:
            raise FakeRefusal("P0002")
        return _Result([("granted",)])

    @contextlib.contextmanager
    def transaction(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1


def test_backfill__pages_per_user_transactions_and_errors() -> None:
    users = [f"a100000a-0000-4000-8000-{n:012d}" for n in range(1, 6)]
    for page in (1, 2, 500):
        conn = _SyncConn(users, refuse={users[2]})
        counts = signup.backfill(conn, page=page)
        assert counts == {"granted": 4, "error:P0002": 1}, (page, counts)
        assert [u for u, _ in conn.claims] == users, \
            f"page {page}: skipped or repeated: {conn.claims}"
        assert {depth for _, depth in conn.claims} == {1}, \
            "a claim ran outside its own transaction"


def test_backfill__a_keyset_that_does_not_advance_stops_loudly() -> None:
    with pytest.raises(RuntimeError, match="did not advance"):
        signup.backfill(_SyncConn(["a100000a-0000-4000-8000-000000000001"], stuck=True))


def test_backfill__maintenance_stops_the_run() -> None:
    with pytest.raises(FakeRefusal, match="maintenance"):
        signup.backfill(_SyncConn(["a100000a-0000-4000-8000-000000000001"], maintenance=True))


def test_backfill__a_role_without_execute_stops_the_run() -> None:
    conn = _SyncConn(["a100000a-0000-4000-8000-000000000001",
                      "a100000a-0000-4000-8000-000000000002"], denied=True)
    with pytest.raises(FakeRefusal, match="permission denied"):
        signup.backfill(conn)
    assert len(conn.claims) == 1, "the run kept going without the privilege to grant"


# --- R32/R40: the code mutants, through the shared runner (R83) ----------------------
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("unverified_is_forbidden", "binding_unchecked", "grant_outside_a_transaction")
CODE = tuple(m for m in signup_mutants.MUTANTS if FULL_RUN or m.name in SUBSET)


@pytest.mark.parametrize("mutant", CODE, ids=lambda m: m.name)
def test_code_mutant_is_killed(mutant) -> None:
    result = shared.run_mutant(mutant, signup_mutants.RUNNER)
    assert result.outcome is shared.Outcome.killed, \
        f"{mutant.name}: {result.outcome} - {result.detail}. Invariant: {mutant.invariant}"
    print(f"{mutant.name}: killed -> {result.detail}")


# =============================================================================
# database cases
# =============================================================================
_state: dict = {}


def _db():
    """D1's fixture plus the D1R CREDIT fixture, then every A1 check's own individuals."""
    if "signup" not in _state:
        pgharness.ensure()
        pgharness.recreate(SIGNUP_DB)
        pgharness.apply(SIGNUP_DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
        conn = pgharness.connect(SIGNUP_DB)
        checks.seed_fixtures(conn)
        checks_credit.seed_credit(conn)
        _state["signup"] = conn
    return _state["signup"]


@needs_pg
def test_eligibility__verified_once_denials_recorded_usd_untouched() -> None:
    """CREDIT-GRANT/IDENTITY/UNITS through the one eligibility operation."""
    print(checks_signup.check_eligibility(_db()))


@needs_pg
def test_binding__frozen_personal_org_and_no_cross_user_spend() -> None:
    """CREDIT-IDENTITY: memberships, provider roles and another individual's wallet."""
    print(checks_signup.check_binding(_db()))


@needs_pg
def test_privileges__browser_financial_writes_denied() -> None:
    """R59: browser roles reach none of it; the platform role only calls and reads."""
    print(checks_signup.check_signup_privileges(_db()))


@needs_pg
def test_retirement__wallet_owner_retired_never_deleted() -> None:
    """The retention policy (ruling proposal in the A1 evidence)."""
    print(checks_signup.check_retirement(_db()))


@needs_pg
def test_race__retries_backfill_and_shared_addresses() -> None:
    """CREDIT-GRANT: 10 rounds x 8 concurrent callers (7 retries + 1 backfill), one
    issuer; 3 rounds x 8 accounts on one verified address, one grant."""
    _db()
    print(checks_signup.check_claim_race(pgharness.connect, SIGNUP_DB))


@needs_pg
def test_retirement_race__a_racing_claim_waits_and_answers() -> None:
    """A claim racing an uncommitted retirement waits for it and answers `retired`."""
    _db()
    print(checks_signup.check_retirement_race(pgharness.connect, SIGNUP_DB))


@needs_pg
def test_backfill__hosted_accounts_upgraded_from_0002() -> None:
    """The I1B hosted shape written on the 0001-0002 schema, upgraded through 0015, then
    backfilled twice: 3 grants + 1 unverified, then nothing; USD unchanged."""
    files = migrations.sql_for(shim=pgharness.NEEDS_SHIM)
    first = tuple(f for f in files if f[0] in ("supabase_shim.sql", "0001_init.sql",
                                               "0002_seed_models.sql"))
    pgharness.ensure()
    pgharness.recreate(HOSTED_DB)
    pgharness.apply(HOSTED_DB, first)
    with pgharness.connect(HOSTED_DB) as conn:
        checks_signup.seed_hosted(conn)
        pgharness.apply(HOSTED_DB, tuple(f for f in files if f not in first))
        made = conn.execute("select (select count(*) from infrx.credit_wallets), "
                            "(select count(*) from infrx.signup_entitlements)").fetchone()
        assert made == (0, 0), f"the upgrade granted by itself: {made}"
        print(checks_signup.check_backfill(conn, expect_first={"granted": 3,
                                                               "unverified": 1}))


@needs_pg
def test_port__the_g6b_grant_over_postgres() -> None:
    """G6B's `OperatorSession.grant_initial` with the real A1 ports: a verified individual
    is granted once under any idempotency key; unverified and unknown are NotFound; an
    identity naming another org than the bound one is refused and rolled back."""
    from psycopg_pool import AsyncConnectionPool
    conn = _db()
    checks_signup.gotrue_columns(conn)
    x, y, z = (checks_signup.uid(8, n) for n in (1, 2, 3))
    checks_signup.individual(conn, x, "x8@example.com")
    checks_signup.individual(conn, y, "y8@example.com", confirmed=False)
    checks_signup.individual(conn, z, "z8@example.com")
    z_other = str(conn.execute("insert into public.organizations (name, slug, created_by) "
                               "values ('z2', 'a1-z-second', %s) returning id", (z,)).fetchone()[0])

    class Audit(dict):
        async def by_idempotency_key(self, key):
            return self.get(key)

        async def append(self, entry):
            self[entry.idempotency_key] = entry

    async def run():
        async with AsyncConnectionPool(pgharness.dsn(SIGNUP_DB), min_size=1, max_size=2,
                                       open=False) as pool:
            port = signup.PgSignup(pool)
            identity = await port.verified_user(x)
            assert identity == VerifiedIdentity(x, checks_credit.personal_org(conn, x),
                                                identity.verification_evidence_ref)
            assert identity.verification_evidence_ref.startswith("email_confirmed_at/")
            assert await port.verified_user(y) is None
            assert await port.verified_user(str(uuid.uuid4())) is None
            ops = service.Operations(identities=port, tenants=None, ledger=port, audit=Audit(),
                                     registry=None, wallets=FakeWalletDirectory(),
                                     catalog=None, jobs=None, accounts=None,
                                     clock=lambda: AT)
            session = service.OperatorSession(ops, "ops@infrx")
            first = await session.grant_initial(x, idempotency_key="a1-g-1", reason="login")
            again = await session.grant_initial(x, idempotency_key="a1-g-2", reason="retry")
            assert (first["replayed"], again["replayed"]) == (False, True), (first, again)
            assert first["amount"] == "10000.00000000"
            assert first["ledger_operation_id"] == again["ledger_operation_id"] == \
                service.stable_id("signup_grant", "a1-g-1")
            with pytest.raises(errors.NotFound):
                await session.grant_initial(y, idempotency_key="a1-g-3", reason="login")
            with pytest.raises(errors.NotFound):
                await port.grant_initial(VerifiedIdentity(y, z_other, "e"), OP, AT)
            with pytest.raises(errors.Forbidden):
                await port.grant_initial(VerifiedIdentity(x, z_other, "e"), OP, AT)
            with pytest.raises(errors.Forbidden):
                await port.grant_initial(VerifiedIdentity(z, z_other, "e"), OP, AT)

    asyncio.run(run())
    assert checks_signup.ledger_rows(conn, x) == 1
    assert checks_signup.denial(conn, y, "unverified") == 1, \
        "the G6B path rolled back the denial it answered"
    assert checks_credit.wallet_of(conn, z) is None and checks_signup.entitlements(conn, z) == 0, \
        "a refused port answer left z's grant committed"
