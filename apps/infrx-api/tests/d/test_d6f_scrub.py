#!/usr/bin/env python3
"""The feedback scrub (T3's request to lab-sql; `0035_feedback_scrub.sql`) and WR-G4F-2 on
real PostgreSQL: a trace deletion removes the request's durable feedback text under a receipt,
and nothing else can rewrite a feedback row; `PgFeedbackService` reports a replay.

World: test_d6f_feedback's (D1's fixtures, the `feedback` flag on). Each `check_*` is the
check a mutant in `code_mutants_d6f.py`'s `SCRUB` list must break; each rolls back.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d6f_scrub.py
"""
from __future__ import annotations

import asyncio

import psycopg
import pytest
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import FeedbackName, IdempotencyRef
from infrx.state import migrations
from infrx.state.feedback import PgFeedbackService
from infrx.state.jobstore import connector

from . import checks, checks_credit as cc, pgharness
from . import test_d6f_feedback as f

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d6fs"
JOB, JOB_B = f.JOB, f.JOB_B
call, refusal, rolled_back, count, seed = f.call, f.refusal, f.rolled_back, f.count, f.seed


def ok(conn, name: str, args: dict):
    """The answer of a call that must succeed; its failure is the assertion (R40)."""
    try:
        with conn.transaction():
            return call(conn, name, args)
    except psycopg.Error as failed:
        raise AssertionError(f"{name} refused: {failed.sqlstate} "
                             f"{str(failed).splitlines()[0][:120]}") from None


def scrub_args(org: str = checks.ORG_A, job: str = JOB, **over) -> dict:
    return {"org_id": org, "request_id": job, "actor": "t3-retention", "reason": "deleted",
            **over}


def signals(conn) -> None:
    """A rating with a comment, a correction, a thumb, and an operator label with a note."""
    for n, body in enumerate(({"name": "rating", "value": 4, "comment": "slow on clip 3"},
                              {"name": "correction", "value": "the cat is black"},
                              {"name": "thumb", "value": True}), 1):
        call(conn, "accept_feedback", f.accept_args(f"fb-{n}", body=body, n=n))
    call(conn, "label_calibration", f.label_args(comment="matches the customer's note"))


def rows(conn, job: str = JOB) -> list:
    return conn.execute("select name, value_bool, value_int, value_text, comment, author_role "
                        "from infrx.feedback where request_id = %s order by entry_seq",
                        (job,)).fetchall()


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_a_scrub_removes_the_text_and_leaves_a_receipt(conn) -> str:
    """T3: every comment of the request goes, a correction's text becomes '[scrubbed]',
    thumbs, ratings and verdicts stay; one receipt names the count, actor and reason; a
    repeat scrubs nothing and writes no second receipt."""
    signals(conn)
    assert ok(conn, "scrub_feedback", scrub_args()) == {"scrubbed": 3}
    assert rows(conn) == [("rating", None, 4, None, None, "customer"),
                          ("correction", None, None, "[scrubbed]", None, "customer"),
                          ("thumb", True, None, None, None, "customer"),
                          ("calibration_label", None, None, "incorrect", None, "operator")], \
        rows(conn)
    receipts = conn.execute("select request_id::text, scrubbed, actor, reason "
                            "from infrx.feedback_scrubs").fetchall()
    assert receipts == [(JOB, 3, "t3-retention", "deleted")], receipts
    assert ok(conn, "scrub_feedback", scrub_args()) == {"scrubbed": 0}
    assert count(conn, "select count(*) from infrx.feedback_scrubs") == 1
    return "3 rows scrubbed; verdicts kept; one receipt; repeat is 0"


@rolled_back
def check_only_the_orgs_own_request_is_scrubbed(conn) -> str:
    """A scrub names an org and one of its requests: another org's request is `not_found`,
    and another request's feedback - the same org's or not - keeps its text."""
    signals(conn)
    call(conn, "accept_feedback", f.accept_args("b-1", org=checks.ORG_B, job=JOB_B, n=50,
                                                body={"name": "comment", "value": "keep me"}))
    call(conn, "accept_feedback", f.accept_args("a-2", job=checks.JOB_RUNNING, n=51,
                                                body={"name": "comment", "value": "mine too"}))
    assert refusal(conn, "scrub_feedback", scrub_args(org=checks.ORG_B)) == "not_found"
    assert rows(conn)[1][3] == "the cat is black", "a foreign scrub reached the text"
    ok(conn, "scrub_feedback", scrub_args())
    assert ("comment", None, None, "keep me", None, "customer") in rows(conn, JOB_B), \
        rows(conn, JOB_B)
    assert rows(conn, checks.JOB_RUNNING) == [("comment", None, None, "mine too", None,
                                               "customer")], "a sibling request was scrubbed"
    return "foreign org not_found; other requests untouched"


@rolled_back
def check_nothing_but_a_scrub_rewrites_feedback(conn) -> str:
    """0028's immutability holds for everything else, even with the scrub marker set by
    hand: provenance, a different text, or a deletion are refused (23514), and a scrub
    shape without the marker is refused too."""
    signals(conn)
    attempts = ("update infrx.feedback set author_role = 'operator', by_operator = true, "
                "comment = null where name = 'rating'",
                "update infrx.feedback set comment = 'rewritten' where name = 'rating'",
                "update infrx.feedback set value_text = 'another' where name = 'correction'",
                "delete from infrx.feedback")
    scrub_shape = ("update infrx.feedback set comment = null, value_text = case when name in "
                   "('correction', 'comment') then '[scrubbed]' else value_text end")
    for marker in ("on", ""):
        for sql in attempts + ((scrub_shape,) if not marker else ()):
            try:
                with conn.transaction():
                    conn.execute("select set_config('infrx.feedback_scrub', %s, true)",
                                 (marker,))
                    conn.execute(sql)
            except psycopg.Error as refused:
                assert refused.sqlstate == "23514", (sql, refused.sqlstate)
                continue
            raise AssertionError(f"marker {marker!r}: `{sql}` was allowed")
    assert refusal(conn, "scrub_feedback", scrub_args(actor="")) == "invalid_request"
    assert rows(conn)[0][4] == "slow on clip 3", "an unattributed scrub removed text"
    return "provenance, rewrites, deletes and unmarked scrubs refused"


@rolled_back
def check_browser_roles_cannot_scrub(conn) -> str:
    """DUR-RLS: no browser session scrubs or reads the receipts; the platform role reads
    them and writes them only through the scrub."""
    probes = ["select count(*) from infrx.feedback_scrubs",
              "select infrx.scrub_feedback('{}'::jsonb)"]
    for session in cc.BROWSER:
        for sql in probes:
            got = cc.refused_as(conn, session, sql)
            assert got is not None and got.startswith("42501"), f"{session}: {sql}: {got}"
    assert cc.refused_as(conn, "service", probes[0]) is None
    got = cc.refused_as(conn, "service", "delete from infrx.feedback_scrubs")
    assert got is not None and got.startswith("42501"), got
    return f"{len(cc.BROWSER)} browser sessions refused; service reads the receipts"


def check_the_service_reports_replays_and_scrubs(conn) -> str:
    """WR-G4F-2 and T3 through `PgFeedbackService`: a first acceptance is not a replay, the
    same key again is (the stored row), and the scrub answers its count (commits its rows)."""
    service = PgFeedbackService(connector(pgharness.dsn(conn.info.dbname)))
    auth = b.auth(org_id=checks.ORG_B, key_id=checks.KEY_B)
    idem = IdempotencyRef(org_id=checks.ORG_B, operation="feedback", key="svc-1",
                          payload_hash=f.DIGEST)
    signal = b.feedback(FeedbackName.comment, "slow")

    async def go() -> list:
        first = await service.accept_with_replay(auth, JOB_B, signal, idem)
        again = await service.accept_with_replay(auth, JOB_B, signal, idem)
        return [first[1], again[1], first[0].feedback_id == again[0].feedback_id,
                await service.scrub(checks.ORG_B, JOB_B, actor="t3", reason="deleted")]
    got = asyncio.run(go())
    assert got == [False, True, True, 1], got
    return f"replayed flags and scrub count: {got}"


CHECKS = {c.__name__: c for c in (
    check_a_scrub_removes_the_text_and_leaves_a_receipt,
    check_only_the_orgs_own_request_is_scrubbed, check_nothing_but_a_scrub_rewrites_feedback,
    check_browser_roles_cannot_scrub, check_the_service_reports_replays_and_scrubs)}


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
def test_d6f_scrub(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
