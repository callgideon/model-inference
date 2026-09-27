#!/usr/bin/env python3
"""G4F on real PostgreSQL - FEEDBACK-ACK through the route over D6F's `PgFeedbackService`.

    INFRX_D_TASK=g4f uv run --frozen pytest -q tests/g/feedback/test_feedback_pg.py

The task-local PostgreSQL of the `g4f` key (57507) only: without `INFRX_D_TASK` these
cases skip visibly rather than fall back to D1's shared default. The acknowledgment is a
committed row + idempotency key + outbox event; a lost acknowledgment replays the one row;
another org's (or an unknown) request is `not_found`; the disabled flag is a 503 that
writes nothing. Ownership is the durable job: the seeded jobs have no trace projection at
all, so every row here was accepted before any trace landed (projection independence).
"""
from __future__ import annotations

import os

import pytest

from .. import support
from ...d import checks, pgharness
from .test_feedback import SIGNAL, code, mounted, post

_reason = ("INFRX_D_TASK is not set (this suite runs only on a named task-local key)"
           if not os.environ.get("INFRX_D_TASK") else pgharness.unavailable())
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_g4f"
PG_TOKEN, PG_TOKEN_B = "sk-infrx-g4f-a", "sk-infrx-g4f-b"
PG_ROWS = {PG_TOKEN: {**support.ROW, "id": checks.KEY_A, "org_id": checks.ORG_A},
           PG_TOKEN_B: {**support.ROW, "id": checks.KEY_B, "org_id": checks.ORG_B}}


class LostAck:
    """The real service, whose acknowledgment is lost after the commit (a crash between
    PostgreSQL and the 201): the first call commits and then raises."""

    def __init__(self, service) -> None:
        self.service, self.lose = service, True

    async def accept(self, *args):
        stored = await self.service.accept(*args)
        if self.lose:
            self.lose = False
            raise RuntimeError("process died after the commit")
        return stored


@pytest.fixture(scope="module")
def db():
    from infrx.state import migrations
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        conn.execute("select infrx_test.freeze('2026-09-27T00:00:00Z')")
        checks.seed_fixtures(conn)
        conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                     "values ('feedback', true, 'rig', 'G4F tests')")
        yield conn


def pg_app(wrap=lambda service: service):
    from infrx.state.feedback import PgFeedbackService
    from infrx.state.jobstore import connector
    service = wrap(PgFeedbackService(connector(pgharness.dsn(DB))))
    return mounted(service, rows=PG_ROWS)[0]


def rows(conn, key):
    return conn.execute("select f.feedback_id, f.author_principal, f.author_role, f.channel, "
                        "(select count(*) from infrx.outbox o where o.kind = "
                        "'feedback_projection' and o.payload->>'feedback_id' = f.feedback_id) "
                        "from infrx.feedback f where f.idempotency_key = %s", (key,)).fetchall()


def test_feedback_ack_pg__the_ack_is_a_committed_customer_row_before_any_trace(db):
    signal = {**SIGNAL, "request_id": checks.JOB_QUEUED}
    answer = post(pg_app(), signal, token=PG_TOKEN, key="g4f-durable")
    assert answer.status_code == 201, answer.text
    stored = rows(db, "g4f-durable")
    assert stored == [(answer.json()["feedback_id"], checks.KEY_A, "customer", "api", 1)]


def test_feedback_ack_pg__a_lost_ack_replays_the_one_row(db):
    signal = {**SIGNAL, "request_id": checks.JOB_QUEUED, "value": 2}
    app = pg_app(LostAck)
    lost = post(app, signal, token=PG_TOKEN, key="g4f-lost")
    assert lost.status_code == 500 and "feedback_id" not in lost.text
    (committed,) = rows(db, "g4f-lost")
    retried = post(app, signal, token=PG_TOKEN, key="g4f-lost")
    assert retried.status_code == 201 and retried.json()["feedback_id"] == committed[0]
    assert rows(db, "g4f-lost") == [committed]                  # one row, one outbox event
    changed = post(app, {**signal, "value": 3}, token=PG_TOKEN, key="g4f-lost")
    assert changed.status_code == 409 and code(changed) == "idempotency_conflict"
    assert rows(db, "g4f-lost") == [committed]


def test_feedback_ack_pg__another_orgs_request_is_not_found(db):
    for token, job in ((PG_TOKEN, checks.JOB_B), (PG_TOKEN_B, checks.JOB_QUEUED),
                       (PG_TOKEN, "9f000000-0000-4000-8000-00000000009f")):
        answer = post(pg_app(), {**SIGNAL, "request_id": job}, token=token, key="g4f-foreign")
        assert answer.status_code == 404 and code(answer) == "not_found"
    assert rows(db, "g4f-foreign") == []


def test_feedback_ack_pg__the_disabled_flag_is_a_503_and_writes_nothing(db):
    db.execute("update infrx.feature_flags set enabled = false where name = 'feedback'")
    try:
        answer = post(pg_app(), {**SIGNAL, "request_id": checks.JOB_QUEUED}, token=PG_TOKEN,
                      key="g4f-off")
    finally:
        db.execute("update infrx.feature_flags set enabled = true where name = 'feedback'")
    assert answer.status_code == 503 and code(answer) == "dependency_unavailable"
    assert rows(db, "g4f-off") == []
