#!/usr/bin/env python3
"""D2: the Python half of the adapter, with NO database - what the code (not the SQL)
decides: which `DomainError` a database refusal becomes, the R39 refusal-as-data, what
the store sends (its own limits, never a caller's), the M3/W2 guards, and the relay's
enqueue-then-acknowledge order. The code mutants (`tests/d/code_mutants.py`) run here,
so they need no Docker; the same behaviour on PostgreSQL is `test_admission.py`,
`test_outbox_relay.py` and `test_store_requests.py`.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import psycopg
import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import FailurePlan, FakeClock, SequentialIds
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import IndexEvent, OutboxKind
from infrx.state.jobstore import PgJobStore, domain_error
from infrx.state.outbox import OutboxRelay
from infrx.state.pgtesting import CrashAfterCommit, FailingJobStore


class _Diag(SimpleNamespace):
    message_primary: str = ""
    message_hint: str = ""
    constraint_name: str | None = None


def _db_error(sqlstate: str, message: str, *, hint: str = "", constraint=None):
    """A psycopg error with a chosen SQLSTATE and diagnostics, as the server sends them."""
    diag = _Diag(message_primary=message, message_hint=hint, constraint_name=constraint)
    cls = type("PgError", (psycopg.Error,),
               {"sqlstate": sqlstate, "diag": property(lambda self: diag)})
    return cls(message)


# --- the refusal mapping ------------------------------------------------------------
@pytest.mark.parametrize(("sqlstate", "message", "hint", "constraint", "expected", "retry"), (
    ("P0001", "capacity_exhausted: key active job limit reached", "retry_after=5", None,
     errors.CapacityExhausted, 5),
    ("P0001", "journal_capacity_exhausted: full", "retry_after=30", None,
     errors.JournalCapacityExhausted, 30),
    ("P0001", "state_conflict: request r is already an admitted job", "", None,
     errors.StateConflict, None),
    ("P0001", "idempotency_expired: at t", "", None, errors.IdempotencyExpired, None),
    ("P0001", "invalid_api_key: key k is revoked", "", None, errors.InvalidApiKey, None),
    ("P0001", "stale_lease: generation 2 != 1", "", None, errors.StaleLease, None),
    ("23514", "new row violates check constraint", "", "credit_wallets_reserved_within_total",
     errors.InsufficientCredit, None),
    ("55000", "maintenance: credit_admission is not enabled", "", None,
     errors.DependencyUnavailable, 30),
    ("P0002", "not_found: model", "", None, errors.NotFound, None),
    ("22023", "invalid_request: model x has no active rate card", "", None,
     errors.InvalidRequest, None),
))
def test_domain_error__each_refusal_is_its_most_specific_type(sqlstate, message, hint,
                                                              constraint, expected, retry):
    mapped = domain_error(_db_error(sqlstate, message, hint=hint, constraint=constraint))
    assert type(mapped) is expected, (type(mapped), expected)
    assert mapped.retry_after_s == retry


def test_domain_error__anything_else_is_the_bug_it_is() -> None:
    for exc in (_db_error("23514", "some other check", constraint="jobs_x"),
                _db_error("23505", "duplicate key"), _db_error("P0001", "no_such_code: x")):
        assert domain_error(exc) is exc, "an unknown database error became a typed refusal"


# --- the store's requests ------------------------------------------------------------
class _Conn:
    """Records each statement and answers with the next scripted value or error."""

    def __init__(self, answers: list) -> None:
        self.answers, self.sent = answers, []

    async def execute(self, sql, params=()):
        self.sent.append((sql, params))
        answer = self.answers.pop(0) if self.answers else None
        if isinstance(answer, BaseException):
            raise answer
        return SimpleNamespace(fetchone=_async((answer,)), fetchall=_async(answer or []))

    async def close(self) -> None:
        pass


def _async(value):
    async def get():
        return value
    return get


def _store(*answers, limits=DEFAULTS):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return PgJobStore(connect, limits=limits), conn


def _refused(cls, coro) -> None:
    """The call is refused with exactly `cls`; any other outcome is an assertion, so a
    defect that makes the adapter crash is reported as the refusal it failed to give."""
    try:
        asyncio.run(coro)
    except cls:
        return
    except Exception as other:
        raise AssertionError(f"expected {cls.__name__}, got {type(other).__name__}: "
                             f"{other}") from None
    raise AssertionError(f"{cls.__name__} was not raised")


def _harness():
    return SimpleNamespace(clock=FakeClock(), ids=SequentialIds())


def test_admit__sends_the_stores_own_limits_and_budgets_never_the_callers() -> None:
    limits = DEFAULTS.replace(max_active_jobs_per_key=3, preparation_timeout_s=77.0)
    store, conn = _store(_db_error("P0001", "invalid_request: stop"), limits=limits)
    request = b.request(_harness())
    with pytest.raises(errors.InvalidRequest):
        asyncio.run(store.admit(request, b.idem(request, "k")))
    args = conn.sent[0][1][0].obj
    assert args["regime"] == "legacy_usd"
    assert args["limits"]["max_active_jobs_per_key"] == 3
    assert args["budgets"]["preparation_s"] == 77.0
    assert "price_snapshot" not in args["request"]["parameters"]
    _refused(errors.InvalidRequest, store.admit(request, b.idem(request, "k"), caps=("bogus",)))


def test_admit_credit__names_the_credit_regime() -> None:
    store, conn = _store(_db_error("P0002", "not_found: model"))
    request = b.request(_harness())
    with pytest.raises(errors.NotFound):
        asyncio.run(store.admit_credit(request, b.idem(request, "k")))
    assert conn.sent[0][1][0].obj["regime"] == "credit"


def test_a_committed_refusal_is_raised_as_its_type() -> None:
    """R39: a terminalize-then-refuse answer arrives as data and still raises."""
    refusal = {"refusal": {"code": "already_terminal", "detail": "past its deadline"}}
    store, _ = _store(refusal)
    _refused(errors.AlreadyTerminal, store.claim_preparation(b.ORG_A, "w"))


def test_get_owned__a_credit_job_is_not_a_v1_admission() -> None:
    store, _ = _store([({"accounting_regime": "credit"},)])
    _refused(errors.NotFound, store.get_owned(b.ORG_A, "job_x"))


def test_is_live__unknown_and_malformed_are_not_live() -> None:
    store, conn = _store([], [(True,)], [(False,)])
    assert asyncio.run(store.is_live("not-a-uuid")) is False
    assert conn.sent == [], "a malformed id reached the database"
    assert asyncio.run(store.is_live(b.ORG_A)) is False          # no row
    assert asyncio.run(store.is_live(b.ORG_A)) is True
    assert asyncio.run(store.is_live(b.ORG_A)) is False          # terminal


def test_prepared__a_prompt_count_must_be_an_integer() -> None:
    store, conn = _store()
    lease = SimpleNamespace(model_dump=lambda mode: {})
    for bad in (True, 1.5, "7"):
        _refused(errors.InvalidRequest, store.prepared(lease, (), prompt_tokens=bad))
    assert conn.sent == [], "a malformed prompt count reached the database"


# --- the relay ------------------------------------------------------------------------
def _event(n: int) -> IndexEvent:
    return IndexEvent(event_id=f"{n:08x}-0000-4000-8000-{n:012x}", job_id=b.ORG_A,
                      org_id=b.ORG_A, key_id=b.KEY_A, kind=OutboxKind.prepare_dispatch,
                      execution_mode="stream", available_at="2026-09-20T12:00:00Z")


class _Store:
    def __init__(self, events) -> None:
        self.events, self.acked = events, []

    async def dispatch_pending(self, **_):
        return tuple(self.events)

    async def acknowledge_dispatch(self, ids):
        self.acked.append(list(ids))
        return len(ids)


class _Index:
    def __init__(self, refuse=(), crash=()) -> None:
        self.seen, self.refuse, self.crash = [], set(refuse), set(crash)

    async def enqueue(self, event):
        if event.event_id in self.crash:
            raise RuntimeError("the index is gone")
        if event.event_id in self.refuse:
            raise errors.CapacityExhausted("full")
        self.seen.append(event.event_id)
        return True


def test_relay__acknowledges_exactly_what_the_index_took() -> None:
    events = [_event(1), _event(2), _event(3)]
    store, index = _Store(events), _Index(refuse={events[1].event_id})
    report = asyncio.run(OutboxRelay(store, index).pump())
    assert store.acked == [[events[0].event_id, events[2].event_id]], store.acked
    assert report == {"read": 3, "indexed": 2, "acknowledged": 2, "deferred": 1}


def test_relay__a_failing_index_acknowledges_nothing() -> None:
    """Enqueue before acknowledge: an index that dies leaves every row pending."""
    events = [_event(1), _event(2)]
    store = _Store(events)
    with pytest.raises(RuntimeError):
        asyncio.run(OutboxRelay(store, _Index(crash={events[1].event_id})).pump())
    assert store.acked == [], "a row was acknowledged although the index never took it"
    empty = _Store([])
    asyncio.run(OutboxRelay(empty, _Index()).pump())
    assert empty.acked == [], "an empty pump wrote an acknowledgment"


# --- the harness's failure plan --------------------------------------------------------
def test_crash_after_commit_commits_then_loses_the_answer() -> None:
    calls = []

    class _Real:
        async def admit(self, *a):
            calls.append("committed")
            return "admission"

    plan = FailurePlan().crash_after_commit("admit")
    port = FailingJobStore(_Real(), plan)
    with pytest.raises(CrashAfterCommit):
        asyncio.run(port.admit())
    assert calls == ["committed"], "the crash happened before the commit"
    assert asyncio.run(port.admit()) == "admission"


def test_relay__a_rebuild_fences_the_acknowledgments_it_may_have_erased() -> None:
    """OB-1: the store clock is read BEFORE the snapshot, the rows acknowledged since then
    are reopened AFTER the index is replaced, and a pump re-sends them."""
    calls = []

    class _Fenced(_Store):
        async def db_now(self):
            calls.append("since")
            return "t0"

        async def dispatch_snapshot(self):
            calls.append("snapshot")
            return ()

        async def reopen_dispatch(self, since):
            calls.append(f"reopen:{since}")
            return 0

        async def dispatch_pending(self, **_):
            calls.append("pump")
            return ()

    class _Rebuilding(_Index):
        async def rebuild(self, snapshot):
            calls.append("rebuild")
            return 0

    asyncio.run(OutboxRelay(_Fenced([]), _Rebuilding()).rebuild())
    assert calls == ["since", "snapshot", "rebuild", "reopen:t0", "pump"], calls
