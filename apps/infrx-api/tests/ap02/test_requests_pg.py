"""AP-02 slice 02b: the request list, one request and its result over `consumer_jobs` /
`consumer_job_result` on the ap2 PostgreSQL.

Oracles: only the individual's own requests, newest first, execution and settlement states as
separate fields, money in each request's own unit (a USD job is never CREDIT); the 0024 filters
narrow and are part of the cursor's scope; a record created between pages is neither lost nor
repeated; 25 by default and a full page at the 100 cap still carries a cursor; a foreign,
malformed or unknown id is 404; the result is served only when available - pending/withheld 409,
no result 404, no persisted expiry 503, expired 410 (by the database's clock) - never content
otherwise; an outage is 503.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from infrx.console.reads import ConsoleReads
from infrx.contracts import api
from infrx.state.jobstore import connector

from tests.d import checks_admission as ca
from tests.d import checks_content as ck
from tests.d import checks_leases as cl
from tests.d.checks_reads import _copy_jobs

from . import world
from .conftest import SECRET, client, session

pytestmark = pytest.mark.pg


def _order(conn, org):
    return [str(i) for (i,) in conn.execute(
        "select request_id from infrx.jobs where org_id = %s order by created_at desc, "
        "request_id", (org,))]


def _walk(http, limit, **filters):
    seen, cursor = [], None
    while True:
        params = {"limit": str(limit), **filters} | ({"cursor": cursor} if cursor else {})
        r = http.get("/console/v1/requests", params=params)
        assert r.status_code == 200, r.text
        seen += r.json()["data"]
        cursor = r.json()["next_cursor"]
        if cursor is None:
            return seen


def test_requests__are_mine_newest_first_with_separate_states(db, seeded):
    dsn, conn = db
    got = _walk(client(dsn, session(seeded.me, seeded.my_org)), 2)
    assert [j["request_id"] for j in got] == _order(conn, seeded.my_org)
    assert seeded.theirs not in {j["request_id"] for j in got}
    by = {j["request_id"]: j for j in got}
    settled, running, unknown = by[seeded.settled], by[seeded.running], by[seeded.unknown]
    assert (settled["state"], settled["settlement_state"], settled["result_access"]) == \
        ("succeeded", "settled", "available")
    (debit,) = conn.execute("select (-amount)::text from infrx.credit_ledger where request_id = "
                            "%s and kind = 'inference_debit'", (seeded.settled,)).fetchone()
    assert settled["charged"] == {"amount": debit, "unit": "CREDIT"}
    assert (running["state"], running["settlement_state"], running["hold_state"],
            running["charged"], running["result_access"]) == \
        ("running", None, "held", None, "pending")
    assert running["hold"]["unit"] == "CREDIT"
    assert (unknown["settlement_state"], unknown["usage_certainty"], unknown["charged"],
            unknown["result_access"]) == ("held_unknown", "unknown", None, "held_unknown")
    assert by[seeded.legacy]["hold"]["unit"] == "USD" and \
        by[seeded.legacy]["accounting_regime"] == "legacy_usd"


def test_requests__filters_narrow_and_scope_the_cursor(db, seeded):
    dsn, conn = db
    http = client(dsn, session(seeded.me, seeded.my_org))
    mine = set(_order(conn, seeded.my_org))
    assert {j["request_id"] for j in _walk(http, 2, key_id=ca.C1_KEY)} == mine
    assert _walk(http, 2, key_id=world.SECOND_KEY) == []
    (first,) = conn.execute("select min(created_at) from infrx.jobs where org_id = %s",
                            (seeded.my_org,)).fetchone()
    window = {"from": first.isoformat(), "to": (first + timedelta(microseconds=1)).isoformat()}
    assert [j["request_id"] for j in _walk(http, 2, **window)] == _order(conn, seeded.my_org)[-1:]
    page = http.get("/console/v1/requests", params={"limit": "1", "key_id": ca.C1_KEY}).json()
    r = http.get("/console/v1/requests", params={"cursor": page["next_cursor"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_cursor", r.text


def test_requests__a_record_created_between_pages_is_neither_lost_nor_repeated(db, seeded):
    dsn, conn = db
    http = client(dsn, session(seeded.me, seeded.my_org))
    before = _order(conn, seeded.my_org)
    first = http.get("/console/v1/requests", params={"limit": "2"}).json()
    ck.at(conn, conn.execute("select infrx.now() + interval '1 second'").fetchone()[0])
    new, _ = cl.credit_running(conn, ca.World(conn), worker="ap02-between")
    seen, cursor = [j["request_id"] for j in first["data"]], first["next_cursor"]
    while cursor:
        page = http.get("/console/v1/requests", params={"limit": "2", "cursor": cursor}).json()
        seen += [j["request_id"] for j in page["data"]]
        cursor = page["next_cursor"]
    assert seen == before and new.request_id not in seen
    assert http.get("/console/v1/requests").json()["data"][0]["request_id"] == new.request_id


def test_requests__25_by_default_and_a_full_page_at_the_cap_carries_a_cursor(db, seeded):
    dsn, conn = db
    _copy_jobs(conn, seeded.settled, 101)
    http = client(dsn, session(seeded.me, seeded.my_org))
    assert len(http.get("/console/v1/requests").json()["data"]) == 25
    got = _walk(http, 100)
    ids = [j["request_id"] for j in got]
    assert len(ids) == len(set(ids)) == len(_order(conn, seeded.my_org)) == 106


def test_request__mine_by_id_and_nothing_else(db, seeded):
    dsn, conn = db
    http = client(dsn, session(seeded.me, seeded.my_org))
    r = http.get(f"/console/v1/requests/{seeded.settled.upper()}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
    assert r.json()["request_id"] == seeded.settled and r.json()["result_access"] == "available"
    for other in (seeded.theirs, "not-a-uuid", "00000000-0000-4000-8000-000000000000"):
        r = http.get(f"/console/v1/requests/{other}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", (other, r.text)


def test_result__served_only_when_available(db, seeded):
    dsn, conn = db
    http = client(dsn, session(seeded.me, seeded.my_org))

    def result(request_id):
        r = http.get(f"/console/v1/requests/{request_id}/result")
        assert r.headers["cache-control"] == "no-store"
        return r.status_code, r.json()
    status, body = result(seeded.settled)
    assert status == 200 and body["text"] == f"result of {seeded.settled}", body
    for request_id, want in ((seeded.running, (409, "result_pending")),
                             (seeded.unknown, (409, "result_pending")),
                             (seeded.failed, (404, "not_found")),
                             (seeded.theirs, (404, "not_found"))):
        status, body = result(request_id)
        assert (status, body["error"]["code"]) == want and "text" not in body, (request_id, body)


def test_result__expired_by_the_database_clock_is_410(db, seeded):
    dsn, conn = db
    (expires,) = conn.execute("select result_expires_at from infrx.jobs where request_id = %s",
                              (seeded.settled,)).fetchone()
    ck.at(conn, expires)
    http = client(dsn, session(seeded.me, seeded.my_org))
    r = http.get(f"/console/v1/requests/{seeded.settled}/result")
    assert r.status_code == 410 and r.json()["error"]["code"] == "result_expired", r.text
    assert http.get(f"/console/v1/requests/{seeded.settled}").json()["result_access"] == "expired"


def test_result__no_persisted_expiry_is_unavailable(db, seeded):
    dsn, conn = db
    conn.execute("alter table infrx.jobs disable trigger user")
    conn.execute("update infrx.jobs set result_expires_at = null where request_id = %s",
                 (seeded.settled,))
    conn.execute("alter table infrx.jobs enable trigger user")
    http = client(dsn, session(seeded.me, seeded.my_org))
    r = http.get(f"/console/v1/requests/{seeded.settled}/result")
    assert r.status_code == 503 and "text" not in r.json(), r.text
    assert http.get(f"/console/v1/requests/{seeded.settled}").json()["result_access"] == \
        "unavailable"


@pytest.mark.parametrize("path", ["/console/v1/requests", "/console/v1/requests/{id}",
                                  "/console/v1/requests/{id}/result"])
def test_requests__refused_to_a_key_and_503_on_an_outage(db, seeded, path):
    dsn, _ = db
    path = path.format(id=seeded.settled)
    key = api.Actor(audience="consumer", user_id=seeded.me, org_id=seeded.my_org)
    assert client(dsn, key).get(path).status_code == 403
    down = ConsoleReads(connector("postgresql://nobody@127.0.0.1:1/none?connect_timeout=1"),
                        SECRET)
    r = client("", session(seeded.me, seeded.my_org), reads=down).get(path)
    assert r.status_code == 503 and r.json()["error"]["retryable"] is True, r.text
