"""AP-02 slice 02c: key summaries, account members and the operator projections (ap2 PG).

Oracles: keys are the actor's org's, newest first, never a secret or hash, and another org's
are not listed; members are the account's own (no invented team); every operator projection
needs operator authority from the server actor AND the database (`is_operator()`), lists exact
CREDIT strings, pages without loss, and is refused to a consumer; an outage is 503.
"""
from __future__ import annotations

import pytest

from infrx.console.reads import ConsoleReads
from infrx.state.jobstore import connector

from tests.d import checks_admission as ca
from tests.d import checks_credit as cc

from . import world
from .conftest import SECRET, client, session

pytestmark = pytest.mark.pg
OPERATOR_PATHS = ("/operator/v1/accounts", "/operator/v1/wallet-drift",
                  "/operator/v1/unknown-usage", "/operator/v1/audit")


def _walk(http, path, limit=1):
    seen, cursor = [], None
    for _ in range(50):
        r = http.get(path, params={"limit": str(limit)} | ({"cursor": cursor} if cursor else {}))
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
        seen += r.json()["data"]
        cursor = r.json()["next_cursor"]
        if cursor is None:
            return seen
    raise AssertionError(f"{path} never ended: {seen}")


def _operator(dsn, seeded):
    return client(dsn, session(seeded.operator, operator=True))


def test_keys__the_orgs_newest_first_without_secrets(db, seeded):
    dsn, conn = db
    want = [str(i) for (i,) in conn.execute(
        "select id from public.api_keys where org_id = %s order by created_at desc, id desc",
        (seeded.my_org,))]
    got = _walk(client(dsn, session(seeded.me, seeded.my_org)), "/console/v1/keys")
    assert [k["id"] for k in got] == want and set(want) == {ca.C1_KEY, world.SECOND_KEY}
    assert set(got[0]) == {"id", "name", "prefix", "created_at", "last_used_at", "revoked_at"}
    foreign = _walk(client(dsn, session(seeded.me, seeded.their_org)), "/console/v1/keys")
    assert foreign == [], "another organization's keys were listed"


def test_members__the_accounts_own(db, seeded):
    dsn, _ = db
    shared = _walk(client(dsn, session(cc.SHARED, seeded.shared_org)),
                   "/console/v1/account/members")
    assert sorted((m["user_id"], m["role"]) for m in shared) == \
        sorted([(cc.SHARED, "owner"), (seeded.them, "member")])
    assert all(m["email"] for m in shared)
    mine = _walk(client(dsn, session(seeded.me, seeded.my_org)), "/console/v1/account/members")
    assert [(m["user_id"], m["role"]) for m in mine] == [(seeded.me, "owner")]
    # CONSUMER_2 also belongs to SHARED's org; their own account lists only them
    theirs = _walk(client(dsn, session(seeded.them, seeded.their_org)),
                   "/console/v1/account/members")
    assert [(m["user_id"], m["role"]) for m in theirs] == [(seeded.them, "owner")]


@pytest.mark.parametrize("path", OPERATOR_PATHS)
def test_operator__needs_authority_from_the_actor_and_the_database(db, seeded, path):
    dsn, _ = db
    for actor in (session(seeded.me, seeded.my_org),                  # a consumer
                  session(seeded.me, seeded.my_org, operator=True),   # forged: not in profiles
                  session(seeded.operator)):                          # the server says no
        r = client(dsn, actor).get(path)
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", (actor, r.text)
    assert _operator(dsn, seeded).get(path).status_code == 200


def test_operator__accounts_are_exact_and_paged(db, seeded):
    dsn, conn = db
    got = _walk(_operator(dsn, seeded), "/operator/v1/accounts")
    want = conn.execute("select owner_user_id::text, ledger_total::text, available::text from "
                        "infrx.credit_wallets where kind = 'consumer' order by updated_at desc, "
                        "wallet_id desc").fetchall()
    assert [(a["user_id"], a["ledger_total"]["amount"], a["available"]["amount"])
            for a in got] == [tuple(w) for w in want]
    mine = next(a for a in got if a["user_id"] == seeded.me)
    assert mine["org_id"] == seeded.my_org and mine["suspended"] is False and mine["email"]


def test_operator__unknown_usage_and_drift(db, seeded):
    dsn, conn = db
    http = _operator(dsn, seeded)
    held = _walk(http, "/operator/v1/unknown-usage")
    assert [u["request_id"] for u in held] == [seeded.unknown]
    assert held[0]["hold"]["unit"] == "CREDIT" and held[0]["reconcile_after"]
    assert _walk(http, "/operator/v1/wallet-drift") == []
    wallets = sorted(cc.wallet_of(conn, user) for user in (seeded.me, seeded.them))
    conn.execute("alter table infrx.credit_wallets disable trigger user")
    conn.execute("update infrx.credit_wallets set ledger_total = ledger_total + 1 "
                 "where wallet_id = any(%s::uuid[])", (wallets,))
    conn.execute("alter table infrx.credit_wallets enable trigger user")
    drift = _walk(http, "/operator/v1/wallet-drift")
    assert [d["wallet_id"] for d in drift] == wallets
    assert drift[0]["ledger_drift"]["unit"] == "CREDIT" and \
        drift[0]["ledger_drift"]["amount"].lstrip("-") == "1.00000000"


def test_operator__audit_newest_first(db, seeded):
    dsn, conn = db
    for n in range(3):
        conn.execute("insert into infrx.audit_entries (id, actor_principal, action, "
                     "target_org_id, reason, idempotency_key) values (gen_random_uuid(), "
                     "'operator:x', 'admin_grant', %s, %s, %s)",
                     (seeded.my_org, f"reason {n}", f"ap02-audit-{n}"))
    want = [str(i) for (i,) in conn.execute("select id from infrx.audit_entries "
                                            "order by at desc, id desc")]
    got = _walk(_operator(dsn, seeded), "/operator/v1/audit")
    assert [a["id"] for a in got] == want and len(want) >= 3
    assert {a["idempotency_key"] for a in got} >= {"ap02-audit-0", "ap02-audit-2"}


@pytest.mark.parametrize("path", ("/console/v1/keys", "/console/v1/account/members")
                         + OPERATOR_PATHS)
def test_projections__503_on_an_outage(seeded, path):
    down = ConsoleReads(connector("postgresql://nobody@127.0.0.1:1/none?connect_timeout=1"),
                        SECRET)
    actor = session(seeded.operator, seeded.my_org, operator=True)
    r = client("", actor, reads=down).get(path)
    assert r.status_code == 503 and r.json()["error"]["retryable"] is True, r.text
