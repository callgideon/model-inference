"""AP-02 slice 02a: wallet, CREDIT ledger and legacy USD statement over the real views (ap2 PG).

Oracles: the figures are the database's exact strings with their unit (never a float, never a
CREDIT/USD mix); "spent" is Σ(grant + adjustments) - total exactly; the ledger pages without loss
or repeat and a cursor resumes only its own actor's list; another org's statement is refused; a
key audience or no session is refused; a database outage is 503, never an empty 200; a suspended
account still reads (R33).
"""
from __future__ import annotations

import pytest

from infrx.console.reads import ConsoleReads
from infrx.contracts import api
from infrx.state.jobstore import connector

from .conftest import SECRET, client, session

pytestmark = pytest.mark.pg


def _wallet(conn, user):
    return conn.execute("select wallet_id::text, ledger_total::text, reserved_total::text, "
                        "available::text from infrx.credit_wallets where owner_user_id = %s "
                        "and kind = 'consumer'", (user,)).fetchone()


def _walk(http, path, limit):
    seen, cursor = [], None
    while True:
        params = {"limit": str(limit)} | ({"cursor": cursor} if cursor else {})
        r = http.get(path, params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        seen += body["data"]
        cursor = body["next_cursor"]
        if cursor is None:
            return seen


def test_credits__are_the_individuals_exact_wallet(db, seeded):
    dsn, conn = db
    r = client(dsn, session(seeded.me, seeded.my_org)).get("/console/v1/credits")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
    body = r.json()
    wallet, total, reserved, available = _wallet(conn, seeded.me)
    assert body["wallet_id"] == wallet
    assert body["ledger_total"] == {"amount": total, "unit": "CREDIT"}
    assert body["reserved_total"] == {"amount": reserved, "unit": "CREDIT"}
    assert body["available"] == {"amount": available, "unit": "CREDIT"}
    (came_in,) = conn.execute("select sum(amount) from infrx.credit_ledger where wallet_id = %s "
                              "and kind <> 'inference_debit'", (wallet,)).fetchone()
    from decimal import Decimal
    assert body["spent"] == {"amount": f"{came_in - Decimal(total):.8f}", "unit": "CREDIT"}
    assert body["signup_granted_at"] is not None


def test_credits__no_wallet_reads_zero_and_no_spent(db, seeded):
    dsn, _ = db
    body = client(dsn, session(seeded.nobody)).get("/console/v1/credits").json()
    zero = {"amount": "0.00000000", "unit": "CREDIT"}
    assert body == {"wallet_id": None, "ledger_total": zero, "reserved_total": zero,
                    "available": zero, "spent": None, "signup_granted_at": None}


def test_credits__a_suspended_account_still_reads(db, seeded):
    dsn, conn = db
    conn.execute("update public.organizations set suspended = true, suspension_reason = 'abuse', "
                 "suspended_at = now() "
                 "where id = %s", (seeded.my_org,))
    r = client(dsn, session(seeded.me, seeded.my_org)).get("/console/v1/credits")
    assert r.status_code == 200 and r.json()["wallet_id"] is not None, r.text


def test_credit_ledger__pages_without_loss_or_repeat(db, seeded):
    dsn, conn = db
    wallet = _wallet(conn, seeded.me)[0]
    want = [str(i) for (i,) in conn.execute(
        "select entry_id from infrx.credit_ledger where wallet_id = %s "
        "order by created_at desc, entry_id desc", (wallet,))]
    http = client(dsn, session(seeded.me, seeded.my_org))
    for limit in (1, 2, 100):
        got = _walk(http, "/console/v1/credit-ledger", limit)
        assert [e["entry_id"] for e in got] == want, (limit, got)
    kinds = {e["kind"]: e for e in got}
    assert kinds["signup_grant"]["amount"] == {"amount": "10000.00000000", "unit": "CREDIT"}
    assert kinds["operator_adjustment"]["amount"]["amount"] == "-5.00000000"


@pytest.mark.parametrize("limit", ["0", "101", "x", "-1"])
def test_credit_ledger__a_limit_outside_the_page_bounds_is_refused(db, seeded, limit):
    dsn, _ = db
    r = client(dsn, session(seeded.me)).get("/console/v1/credit-ledger", params={"limit": limit})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request", r.text


def test_credit_ledger__a_cursor_from_another_actor_is_refused(db, seeded):
    dsn, _ = db
    mine = client(dsn, session(seeded.me)).get("/console/v1/credit-ledger",
                                               params={"limit": "1"}).json()
    assert mine["next_cursor"]
    r = client(dsn, session(seeded.them)).get("/console/v1/credit-ledger",
                                              params={"cursor": mine["next_cursor"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor", r.text


def test_legacy_statement__is_usd_and_separate(db, seeded):
    dsn, _ = db
    r = client(dsn, session(seeded.me, seeded.my_org)).get("/console/v1/legacy-statement")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["balance"] == {"amount": "25.00000000", "unit": "USD"}
    assert (body["entry_count"], body["rollout_hold"]) == (1, True)


def test_legacy_statement__another_org_is_refused(db, seeded):
    dsn, _ = db
    r = client(dsn, session(seeded.me, seeded.their_org)).get("/console/v1/legacy-statement")
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", r.text


@pytest.mark.parametrize("path", ["/console/v1/credits", "/console/v1/credit-ledger",
                                  "/console/v1/legacy-statement"])
def test_reads__a_key_audience_or_no_session_is_refused(db, seeded, path):
    dsn, _ = db
    key = api.Actor(audience="consumer", user_id=seeded.me, org_id=seeded.my_org)
    assert client(dsn, key).get(path).status_code == 403
    r = client(dsn, None).get(path)
    assert r.status_code == 401 and r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/console/v1/credits", "/console/v1/credit-ledger",
                                  "/console/v1/legacy-statement"])
def test_reads__a_database_outage_is_503_never_empty(seeded, path):
    down = ConsoleReads(connector("postgresql://nobody@127.0.0.1:1/none?connect_timeout=1"),
                        SECRET)
    r = client("", session(seeded.me, seeded.my_org), reads=down).get(path)
    assert r.status_code == 503, r.text
    assert r.json()["error"]["retryable"] is True and "data" not in r.json()


def test_reads__the_session_is_the_actor_read_only(db, seeded):
    """The database sees the verified user as `authenticated` (never the platform role, which
    would open every view) in a read-only transaction."""
    import asyncio
    dsn, _ = db
    reads = ConsoleReads(connector(dsn), SECRET)

    async def probe():
        async with reads._as(session(seeded.me)) as conn:
            cur = await conn.execute("select current_user::text, "
                                     "current_setting('transaction_read_only'), auth.uid()::text")
            return await cur.fetchone()
    assert asyncio.run(probe()) == ("authenticated", "on", seeded.me)


def test_router__mounts_nothing_without_the_port():
    """Default OFF: a composition without `rt.console_reads` serves none of these paths."""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from infrx.gateway.routes import console_reads
    app = FastAPI()
    console_reads.register(app, SimpleNamespace(console_reads=None))
    assert TestClient(app).get("/console/v1/credits").status_code == 404
    assert not [r for r in app.routes if "/console/" in getattr(r, "path", "")]
