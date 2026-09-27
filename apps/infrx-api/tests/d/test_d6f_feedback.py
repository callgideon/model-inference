#!/usr/bin/env python3
"""D6F (wave-5 LW1, lab-sql; 09-amendment-workstreams §D6F): durable feedback and immutable
author provenance on real PostgreSQL - FEEDBACK-ACK and DUR-RLS.

Two layers:
* `check_*(conn)` - the SQL boundary on D1's fixture world (`checks.seed_fixtures`, the
  `feedback` flag row enabled), each rolled back; the checks `code_mutants_d6f.py`'s SQL
  mutants must break. Drills: a spoofed author, a duplicate (sequential and concurrent), a
  kill after commit and before the acknowledgment (replayed once), a customer label.
* the exported v1 `FeedbackService` conformance suite on `PgFeedbackService` over a fresh
  migrated database per case (`pgstore`), jobs admitted through the real `PgJobStore`.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d6f_feedback.py
"""
from __future__ import annotations

import asyncio
import threading

import psycopg
import pytest
from infrx.contracts.conformance import Harness, feedback_cases
from infrx.state import migrations
from infrx.state.feedback import PgFeedbackService
from infrx.state.jobstore import connector, domain_error
from psycopg.types.json import Jsonb

from . import checks, pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d6f"
JOB, JOB_B = checks.JOB_QUEUED, checks.JOB_B
DIGEST = "sha256:" + "cd" * 32
UNKNOWN = "9f000000-0000-4000-8000-00000000009f"          # no job has this id
WRITES = ("accept_feedback", "label_calibration", "request_feedback")


def enable(conn) -> None:
    conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                 "values ('feedback', true, 'rig', 'D6F tests') on conflict (name) do update "
                 "set enabled = true")


def seed(conn) -> None:
    conn.execute("select infrx_test.freeze('2026-09-27T00:00:00Z')")
    checks.seed_fixtures(conn)
    enable(conn)


def accept_args(key: str = "fb-1", *, org: str = checks.ORG_A, job: str = JOB,
                body: dict | None = None, digest: str = DIGEST, n: int = 1, **over) -> dict:
    return {"org_id": org, "principal": checks.KEY_A, "by_operator": False, "channel": "api",
            "request_id": job, "feedback_id": f"fb_{n:026d}",
            "body": {"name": "rating", "value": 4} if body is None else body,
            "idem": {"org_id": org, "operation": "feedback", "key": key,
                     "payload_hash": digest}, **over}


def label_args(key: str = "cal-1", *, n: int = 900, **over) -> dict:
    return {"principal": "ops@infrx", "is_operator": True, "request_id": JOB,
            "feedback_id": f"fb_{n:026d}", "label": "incorrect", "rubric_version": 2,
            "idem": {"org_id": checks.ORG_A, "operation": "calibration.label", "key": key,
                     "payload_hash": DIGEST}, **over}


def call(conn, name: str, args: dict):
    return conn.execute(f"select infrx.{name}(%s)", (Jsonb(args),)).fetchone()[0]


def refusal(conn, name: str, args: dict) -> str | None:
    try:
        with conn.transaction():
            call(conn, name, args)
    except psycopg.Error as failed:
        return getattr(domain_error(failed), "code", f"sqlstate {failed.sqlstate}")
    return None


def count(conn, sql: str, *params) -> int:
    return conn.execute(sql, params).fetchone()[0]


def rolled_back(check):
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
def check_the_writes_fail_closed_until_the_flag_is_on(conn) -> str:
    """09 §D6F: a separate `feedback` flag, OFF when missing or disabled - 0004's
    `feature_not_supported`, so nothing reachable writes feedback by default."""
    conn.execute("delete from infrx.feature_flags where name = 'feedback'")
    for enabled in (None, False):
        if enabled is False:
            conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                         "values ('feedback', false, 'rig', 'off')")
        for name, args in (("accept_feedback", accept_args()), ("label_calibration",
                                                                  label_args())):
            try:
                with conn.transaction():
                    call(conn, name, args)
            except psycopg.errors.FeatureNotSupported:
                continue
            raise AssertionError(f"{name} ran with the feedback flag {enabled}")
    return "accept and label refuse 0A000 with the flag row missing and disabled"


@rolled_back
def check_acceptance_commits_row_key_and_outbox_together(conn) -> str:
    """FEEDBACK-ACK: one call stores the row (customer role, the server's channel and
    marker), its idempotency record and ONE `feedback_projection` event naming it; a replay
    after that commit (the kill between commit and 201) returns the same row and writes
    nothing; a changed payload under the key is `idempotency_conflict`."""
    first = call(conn, "accept_feedback", accept_args(by_operator=True, channel="console"))
    assert (first["author_role"], first["channel"], first["by_operator"]) == \
        ("customer", "console", True), first
    events = conn.execute("select org_id::text, payload from infrx.outbox where aggregate_id "
                          "= %s and kind = 'feedback_projection'", (JOB,)).fetchall()
    assert events == [(checks.ORG_A, {"feedback_id": first["feedback_id"]})], events
    assert count(conn, "select count(*) from infrx.idempotency where feedback_id = %s",
                 first["feedback_id"]) == 1
    try:
        with conn.transaction():
            again = call(conn, "accept_feedback", accept_args(n=2))
    except psycopg.Error as failed:
        raise AssertionError(f"the replay after commit failed: {failed.sqlstate}") from None
    assert again == first, "a replay is not the stored row"
    assert count(conn, "select count(*) from infrx.feedback where request_id = %s", JOB) == 1
    assert count(conn, "select count(*) from infrx.outbox where aggregate_id = %s and kind = "
                 "'feedback_projection'", JOB) == 1, "a replay queued a second projection"
    assert refusal(conn, "accept_feedback", accept_args(digest="sha256:" + "ef" * 32, n=3)) \
        == "idempotency_conflict"
    return "row + idempotency + 1 outbox event in one call; replay returns it; changed payload 409"


@rolled_back
def check_provenance_cannot_be_supplied_by_the_client(conn) -> str:
    """FEEDBACK-ACK spoof drill: a body carrying any provenance key, a stored-only name, a
    value of the wrong type, or no key is refused; only an operator labels."""
    bodies = [{"name": "rating", "value": 4, extra: val} for extra, val in (
        ("author_role", "operator"), ("author_principal", "ops@infrx"), ("channel", "console"),
        ("by_operator", True), ("calibration_set", True), ("rubric_version", 3))]
    bodies += [{"name": "calibration_label", "value": "correct"}, {"name": "thumb", "value": 3},
               {"name": "rating", "value": 1.5}, {"name": "rating", "value": 9},
               {"name": "comment", "value": "  "}, {"value": 4}, []]
    before = count(conn, "select count(*) from infrx.feedback")
    for n, body in enumerate(bodies):
        got = refusal(conn, "accept_feedback", accept_args(f"spoof-{n}", body=body, n=10 + n))
        assert got == "invalid_request", f"{body}: {got}"
    keyless = accept_args(n=30)
    keyless["idem"] = {**keyless["idem"], "key": None}
    assert refusal(conn, "accept_feedback", keyless) == "invalid_request"
    assert refusal(conn, "label_calibration", label_args(is_operator=False)) == "forbidden"
    assert count(conn, "select count(*) from infrx.feedback") == before
    return f"{len(bodies) + 2} spoofed or malformed submissions refused; no row written"


@rolled_back
def check_ownership_is_the_durable_job(conn) -> str:
    """FEEDBACK-ACK: ownership is `infrx.jobs` (no trace row exists here at all): another
    organization's request and an unknown one are `not_found`, for writes and reads; a
    suspended organization cannot submit but still reads."""
    assert count(conn, "select count(*) from infrx.jobs where request_id = %s", JOB) == 1
    other = accept_args(org=checks.ORG_B)
    assert refusal(conn, "accept_feedback", other) == "not_found"
    assert refusal(conn, "accept_feedback", accept_args(job=UNKNOWN)) == "not_found"
    assert refusal(conn, "request_feedback", {"request_id": JOB, "org_id": checks.ORG_B}) \
        == "not_found"
    call(conn, "accept_feedback", accept_args())
    conn.execute("update public.organizations set suspended = true, suspended_at = "
                 "infrx.now(), suspension_reason = 'other' where id = %s", (checks.ORG_A,))
    assert refusal(conn, "accept_feedback", accept_args("fb-late", n=5)) == "org_suspended"
    assert len(call(conn, "request_feedback", {"request_id": JOB, "org_id": checks.ORG_A})) == 1
    return "foreign/unknown requests not_found; suspended org refused, still reads"


@rolled_back
def check_a_label_is_an_audited_operator_row_of_the_jobs_tenant(conn) -> str:
    """R26/R31/R34/R43/R54: a label is `operator`, marked, calibration, on the job's org, with
    one audit row and one projection; the two operations' keys never replay each other; the
    request's lists keep signals and labels apart."""
    label = call(conn, "label_calibration", label_args())
    assert (label["author_role"], label["by_operator"], label["calibration_set"],
            label["rubric_version"], label["org_id"]) == ("operator", True, True, 2,
                                                          checks.ORG_A), label
    audit = conn.execute("select actor_principal, target_org_id::text, after->>'label' from "
                         "infrx.audit_entries where action = 'calibration_label'").fetchall()
    assert audit == [("ops@infrx", checks.ORG_A, "incorrect")], audit
    assert conn.execute("select payload from infrx.outbox where kind = 'feedback_projection' "
                        "and aggregate_id = %s", (JOB,)).fetchone()[0] == {
        "feedback_id": label["feedback_id"], "calibration_set": True, "rubric_version": 2}
    customer = call(conn, "accept_feedback", accept_args())
    swapped = accept_args("cal-1", n=7)
    swapped["idem"]["operation"] = "calibration.label"
    assert refusal(conn, "accept_feedback", swapped) == "idempotency_conflict"
    reused = label_args("fb-1", n=8)
    reused["idem"]["operation"] = "feedback"
    assert refusal(conn, "label_calibration", reused) == "idempotency_conflict"
    assert refusal(conn, "label_calibration", label_args(
        "cal-2", n=9, idem={**label_args()["idem"], "org_id": checks.ORG_B, "key": "cal-2"})) \
        == "forbidden"
    assert refusal(conn, "label_calibration", label_args("cal-3", n=10, label="golden")) \
        == "invalid_request"
    signals = call(conn, "request_feedback", {"request_id": JOB, "org_id": checks.ORG_A})
    labels = call(conn, "request_feedback", {"request_id": JOB, "calibration": True})
    assert [r["feedback_id"] for r in signals] == [customer["feedback_id"]]
    assert [r["feedback_id"] for r in labels] == [label["feedback_id"]]
    return "label: operator row on the job's org, 1 audit, 1 projection; keys never cross"


@rolled_back
def check_feedback_rows_are_immutable(conn) -> str:
    """Author, channel and role are derived once: no UPDATE, DELETE or TRUNCATE, even as the
    owner."""
    call(conn, "accept_feedback", accept_args())
    for sql in ("update infrx.feedback set author_role = 'operator', by_operator = true",
                "update infrx.feedback set author_principal = 'someone-else'",
                "delete from infrx.feedback", "truncate infrx.feedback cascade"):
        try:
            with conn.transaction():
                conn.execute(sql)
        except psycopg.Error as refused:
            assert refused.sqlstate == "23514", (sql, refused.sqlstate)
            continue
        raise AssertionError(f"`{sql}` was allowed")
    return "update, delete and truncate refused"


@rolled_back
def check_browser_roles_reach_no_write(conn) -> str:
    """DUR-RLS: no browser session executes the feedback functions or writes the relation
    (a browser reads its own feedback only through 0005's masked `public.feedback` view)."""
    probes = [f"select infrx.{name}('{{}}'::jsonb)" for name in WRITES]
    probes.append(f"insert into infrx.feedback (feedback_id, org_id, request_id, "
                  f"author_principal, author_role, channel, name, value_bool) values ('fb_x', "
                  f"'{checks.ORG_A}', '{JOB}', 'x', 'operator', 'api', 'thumb', true)")
    for session in checks.BROWSER_SESSIONS:
        for sql in probes:
            try:
                with conn.transaction():
                    conn.execute(checks.SESSIONS[session])
                    conn.execute(sql)
            except psycopg.errors.InsufficientPrivilege:
                continue
            except psycopg.Error as other:
                raise AssertionError(f"{session} `{sql[:50]}`: {other.sqlstate}") from None
            raise AssertionError(f"{session} ran `{sql[:50]}`")
    for name in WRITES:
        acl = conn.execute("select coalesce(proacl::text, '') from pg_proc where oid = "
                           "%s::regprocedure", (f"infrx.{name}(jsonb)",)).fetchone()[0]
        grantees = {item.split("=", 1)[0] for item in acl.strip("{}").split(",") if item}
        assert "service_role" in grantees and grantees <= {
            "postgres", "service_role", "infrx_runtime"}, f"infrx.{name}: {acl}"
    return f"{len(checks.BROWSER_SESSIONS)} browser sessions x {len(probes)} probes refused"


def check_a_concurrent_duplicate_replays_the_first_row(conn) -> str:
    """FEEDBACK-ACK duplicate drill: two sessions submit the same key at once; the second
    waits for the first commit and answers its row - one row, one projection."""
    key = f"race-{conn.execute('select gen_random_uuid()').fetchone()[0]}"
    first = psycopg.connect(pgharness.dsn(conn.info.dbname))
    second = psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True)
    answers = {}
    try:
        answers["first"] = call(first, "accept_feedback", accept_args(key, n=40))
        racer = threading.Thread(target=lambda: answers.__setitem__(
            "second", call(second, "accept_feedback", accept_args(key, n=41))))
        racer.start()
        racer.join(1.0)
        assert racer.is_alive(), "the duplicate did not wait for the first transaction"
        first.commit()
        racer.join(10.0)
    finally:
        first.close()
    second.close()
    assert answers.get("second") == answers["first"], answers
    assert count(conn, "select count(*) from infrx.idempotency where key = %s", key) == 1
    assert count(conn, "select count(*) from infrx.outbox o join infrx.feedback f on "
                 "o.payload->>'feedback_id' = f.feedback_id where f.idempotency_key = %s",
                 key) == 1
    return "the concurrent duplicate waited, then replayed the first row"


CHECKS = {c.__name__: c for c in (
    check_the_writes_fail_closed_until_the_flag_is_on,
    check_acceptance_commits_row_key_and_outbox_together,
    check_provenance_cannot_be_supplied_by_the_client, check_ownership_is_the_durable_job,
    check_a_label_is_an_audited_operator_row_of_the_jobs_tenant,
    check_feedback_rows_are_immutable, check_browser_roles_reach_no_write,
    check_a_concurrent_duplicate_replays_the_first_row)}


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
def test_d6f(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")


def feedback_factory(limits=None, **_) -> Harness:
    """The conformance harness: the jobstore rig's fresh database with the flag on, the
    feedback service on it, `jobs` for admission and `audit` from `infrx.audit_entries`."""
    jobs = pgstore.factory(limits)
    owner = jobs.extra["conn"]
    enable(owner)

    def audit() -> list[dict]:
        return [{"event": "label_calibration", "label": after["label"], "rubric_version": after["rubric_version"],
                 "operator": who, "org_id": org, "request_id": after["request_id"],
                 "feedback_id": after["feedback_id"]}
                for who, org, after in owner.execute(
                    "select a.actor_principal, a.target_org_id::text, a.after from "
                    "infrx.audit_entries a join infrx.feedback f on f.feedback_id = "
                    "a.after->>'feedback_id' where a.action = 'calibration_label' "
                    "order by f.entry_seq").fetchall()]
    service = PgFeedbackService(connector(pgharness.dsn(jobs.extra["database"])))
    return Harness(port=service, clock=jobs.clock, ids=jobs.ids, failures=jobs.failures,
                   extra={**jobs.extra, "jobs": jobs.port, "audit": audit})


@pytest.mark.parametrize("case", feedback_cases(), ids=lambda c: c.__name__)
def test_feedback_conformance_on_postgres(case) -> None:
    asyncio.run(case(feedback_factory))
