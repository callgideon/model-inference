"""T2F on real PostgreSQL: the relay half of the feedback projection over D6F's outbox.

Each `check_*(pg, world)` takes the relay module (`infrx.traces.feedback.pg`) as an argument,
so `mutants.py` kills its SQL in-process: the same check, a mutated copy of the module, one
fresh `pgworld` database (all migrations through 0028, the admission seed) per run. Feedback
is written only through D6F's own functions (`accept_feedback`, `label_calibration`) with the
`feedback` flag row on, and read back through `request_feedback`: the durable truth.

    INFRX_D_TASK=t2f uv run --frozen pytest -q tests/t/feedback/test_feedback_pg.py
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from psycopg.types.json import Jsonb

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import Feedback
from infrx.state.jobstore import connector
from infrx.traces import feedback
from infrx.traces.feedback import pg as relay

from ...d import pgharness
from ...g.ops import pgworld
from .test_feedback import MemoryProjection

pytestmark = pgworld.needs_pg
WORKER, OTHER = "relay-t2f-a", "relay-t2f-b"


def run(coroutine):
    return asyncio.run(coroutine)


def world():
    w = pgworld.world("t2f")
    w.owner.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                    "values ('feedback', true, 'rig', 'T2F') on conflict (name) do update "
                    "set enabled = true")
    return w


def job(w, key: str, *, org: str = b.ORG_A, api_key: str = b.KEY_A) -> str:
    request, _ = run(pgworld.admit_legacy(w, key, org=org, key=api_key))
    return request.request_id


def accept(w, request_id: str, key: str, *, org: str = b.ORG_A, by_operator: bool = False,
           body: dict | None = None) -> str:
    return w.one("select infrx.accept_feedback(%s)->>'feedback_id'", (Jsonb({
        "org_id": org, "principal": b.KEY_A, "by_operator": by_operator, "channel": "api",
        "request_id": request_id, "feedback_id": f"fb_{uuid.uuid4().hex[:26]}",
        "body": body or {"name": "rating", "value": 4},
        "idem": {"org_id": org, "operation": "feedback", "key": key,
                 "payload_hash": "sha256:" + "cd" * 32}}),))


def label(w, request_id: str, key: str) -> str:
    return w.one("select infrx.label_calibration(%s)->>'feedback_id'", (Jsonb({
        "principal": "ops@infrx", "is_operator": True, "request_id": request_id,
        "feedback_id": f"fb_{uuid.uuid4().hex[:26]}", "label": "incorrect",
        "rubric_version": 2, "idem": {"org_id": b.ORG_A, "operation": "calibration.label",
                                      "key": key, "payload_hash": "sha256:" + "ab" * 32}}),))


def forge(w, org: str, request_id: str, payload: dict) -> None:
    """An outbox event nobody's transaction wrote, straight into the table."""
    w.owner.execute("insert into infrx.outbox (event_id, aggregate_id, org_id, kind, payload, "
                    "available_at) values (gen_random_uuid(), %s, %s, 'feedback_projection', "
                    "%s, infrx.now())", (request_id, org, Jsonb(payload)))


def durable(w, request_id: str) -> list[Feedback]:
    rows = [Feedback.model_validate(doc) for calibration in (False, True)
            for doc in w.one("select infrx.request_feedback(%s)", (Jsonb(
                {"request_id": request_id, "calibration": calibration}),))]
    return sorted(rows, key=lambda r: (r.created_at, r.feedback_id))


def pending_events(w) -> int:
    return w.one("select count(*) from infrx.outbox where kind = 'feedback_projection' "
                 "and acknowledged_at is null")


# ----------------------------------------------------------------------------- checks
def check_each_signal_is_projected_once_as_its_durable_row(pg, w) -> None:
    """FEEDBACK-ACK: what arrives is the row PostgreSQL stored - the customer role even for
    an operator on the customer path, calibration only through the label - never what an
    event's payload claims; an event naming another organization's row projects nothing;
    dispatch events are not the relay's."""
    request, other = job(w, "t2f-a"), job(w, "t2f-b", org=b.ORG_B, api_key=b.KEY_B)
    signal = accept(w, request, "fb-1")
    accept(w, request, "fb-2", by_operator=True, body={"name": "thumb", "value": True})
    label(w, request, "cal-1")
    forge(w, b.ORG_A, request, {"feedback_id": signal, "calibration_set": True})
    forge(w, b.ORG_B, other, {"feedback_id": signal})
    store = MemoryProjection()
    report = run(feedback.FeedbackProjector(pg.PgFeedbackOutbox(w.connect()), store,
                                            worker_id=WORKER).pump())
    assert report == {"read": 5, "projected": 4, "acknowledged": 4, "orphaned": 1}, report
    assert run(store.find(b.ORG_A, request)) == durable(w, request)
    customer = [r for r in run(store.find(b.ORG_A, request)) if r.feedback_id == signal]
    assert [(r.calibration_set, r.author_role.value) for r in customer] == [(False, "customer")]
    assert run(store.find(b.ORG_B, other)) == run(store.find(b.ORG_B, request)) == []
    assert pending_events(w) == 1, "only the orphan stays unacknowledged"
    assert w.one("select count(*) from infrx.outbox where kind <> 'feedback_projection' "
                 "and claimed_by = %s", (WORKER,)) == 0, "the relay claimed a dispatch event"
    again = run(feedback.FeedbackProjector(pg.PgFeedbackOutbox(w.connect()), store,
                                           worker_id=WORKER).pump())
    assert again["read"] == 0, again


def check_a_lost_ack_is_redelivered_after_the_window_and_acked_by_its_claimer(pg, w) -> None:
    """TRACE-RECOVER: a claimed event is nobody else's until the redelivery window has
    passed; then it is handed out again, and only the current claimer's ack lands."""
    accept(w, job(w, "t2f-c"), "fb-1")
    a, b_ = pg.PgFeedbackOutbox(w.connect()), pg.PgFeedbackOutbox(w.connect())
    claimed = run(a.pending(worker_id=WORKER, limit=10, redelivery_s=30))
    assert len(claimed) == 1, claimed
    assert run(b_.pending(worker_id=OTHER, limit=10, redelivery_s=30)) == []
    redelivered = run(b_.pending(worker_id=OTHER, limit=10, redelivery_s=0))  # window passed
    assert len(redelivered) == 1, "a lost ack was never redelivered"
    first, again = claimed[0], redelivered[0]
    assert again.event_id == first.event_id and again.feedback == first.feedback
    assert run(a.acknowledge([first.event_id], worker_id=WORKER)) == 0, "a stale claimer acked"
    assert run(b_.acknowledge([again.event_id], worker_id=OTHER)) == 1
    assert run(a.pending(worker_id=WORKER, limit=10, redelivery_s=0)) == []
    assert pending_events(w) == 0


def check_lag_counts_the_unacknowledged_feedback_events(pg, w) -> None:
    """The lag gauge: pending feedback events and the oldest one's age on the database
    clock - dispatch events are not feedback lag; zero once projected."""
    request = job(w, "t2f-d")
    accept(w, request, "fb-1")
    accept(w, request, "fb-2")
    w.owner.execute("select infrx_test.advance(90)")
    outbox = pg.PgFeedbackOutbox(w.connect())
    lag = run(outbox.lag())
    assert lag["pending"] == 2 and lag["oldest_s"] >= 90, lag
    run(feedback.FeedbackProjector(outbox, MemoryProjection(), worker_id=WORKER).pump())
    assert run(outbox.lag()) == {"pending": 0, "oldest_s": 0.0}


def check_a_postgresql_failure_is_a_dependency_outage(pg, w) -> None:
    """A database that does not answer is `DependencyUnavailable` - never an empty batch,
    which would read as "nothing to project" while the lag grows unseen."""
    absent = pg.PgFeedbackOutbox(connector(pgharness.dsn(f"{w.database}_absent")))
    for call in (absent.pending(worker_id=WORKER, limit=1, redelivery_s=0), absent.lag(),
                 absent.acknowledge(["00000000-0000-4000-8000-000000000001"],
                                    worker_id=WORKER)):
        try:
            run(call)
        except errors.DependencyUnavailable:
            continue
        raise AssertionError("a PostgreSQL failure read as an answer")


CHECKS = {name: check for name, check in dict(globals()).items() if name.startswith("check_")}


@pytest.fixture
def pg_world():
    w = world()
    try:
        yield w
    finally:
        w.owner.close()


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_feedback_relay_on_postgresql(name, pg_world):
    CHECKS[name](relay, pg_world)
