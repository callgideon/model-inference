#!/usr/bin/env python3
"""D3: the Python half of the lease operations, with NO database - what the adapter (not
the SQL) decides: which limits it sends (the store's, never a caller's), that a refusal
after a committed terminalization is raised as its type (R39), how `load_work` builds the
work (and refuses a CREDIT job it cannot carry), that `complete` fails closed after the
fence, and how a `recover` sweep is read. `tests/d/code_mutants_d3.py` runs here, so it
needs no Docker; the SQL is `test_leases.py` / `test_lease_races.py`.
"""
from __future__ import annotations

import asyncio

import psycopg
import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import IndexEvent, Lease, MediaKind, TerminalOutcome
from infrx.state.jobstore import PreparedWork, domain_error

from .test_adapter_units import _db_error, _harness, _refused, _store

LEASE = Lease(job_id=b.ORG_A, kind="inference", generation=2, worker_id="w1",
              acquired_at="2026-09-20T12:00:00Z", expires_at="2026-09-20T12:02:00Z",
              generation_deadline_at="2026-09-20T12:05:00Z",
              first_token_deadline_at="2026-09-20T12:01:00Z")
OUTCOME = {"job_id": b.ORG_A, "state": "cancelled", "cause": "client_cancelled",
           "result_ref": None, "settlement_state": "released_free", "debit": "0.00000000",
           "settled_at": "2026-09-20T12:00:00Z", "reconcile_after": None}
REFUSED = {"refusal": {"code": "already_terminal", "detail": "past its deadline"}}


def _args(conn, n: int = 0) -> dict:
    return conn.sent[n][1][0].obj


def test_domain_error__lease_conflicts_are_their_types() -> None:
    for message, cls in (("already_terminal: job j is failed", errors.AlreadyTerminal),
                         ("not_claimable: job j is running", errors.NotClaimable),
                         ("stale_lease: generation 1 != 2", errors.StaleLease)):
        assert type(domain_error(_db_error("P0001", message))) is cls, message


def test_lease_calls__send_the_stores_own_lease_limits() -> None:
    """The TTLs, the retry bound and the reconciliation window are the store's configuration
    (as retuned), sent with every call - never the defaults, never the caller's."""
    limits = DEFAULTS.replace(lease_ttl_s=77.0, preparation_lease_ttl_s=11.0,
                              max_prepublication_retries=5, unknown_usage_reconcile_s=9.0)
    want = {"lease_ttl_s": 77.0, "preparation_lease_ttl_s": 11.0,
            "max_prepublication_retries": 5, "unknown_usage_reconcile_s": 9.0}
    store, conn = _store({"lease": LEASE.model_dump(mode="json")},
                         {"lease": LEASE.model_dump(mode="json")}, OUTCOME, [],
                         limits=limits)
    assert asyncio.run(store.claim(b.ORG_A, "w1")) == LEASE
    assert asyncio.run(store.heartbeat(LEASE)) == LEASE
    asyncio.run(store.cancel(b.ORG_B, "job_x"))
    asyncio.run(store.recover())
    for n in range(4):
        assert _args(conn, n)["limits"] == want, (n, _args(conn, n))
    assert (_args(conn)["job_id"], _args(conn)["worker_id"]) == (b.ORG_A, "w1")
    assert _args(conn, 1)["lease"] == LEASE.model_dump(mode="json")
    assert (_args(conn, 2)["org_id"], _args(conn, 2)["job_handle"]) == (b.ORG_B, "job_x")


def test_fenced_calls__a_committed_refusal_is_raised_as_its_type() -> None:
    """R39: heartbeat, load_work and complete answer an R29 terminalization as data (so it
    commits); the adapter raises it as `AlreadyTerminal`."""
    store, _ = _store(REFUSED, REFUSED, REFUSED)
    _refused(errors.AlreadyTerminal, store.heartbeat(LEASE))
    _refused(errors.AlreadyTerminal, store.load_work(LEASE))
    _refused(errors.AlreadyTerminal, store.complete(LEASE, TerminalOutcome(**OUTCOME)))


def _work_doc(regime: str = "legacy_usd"):
    request = b.request(_harness(), refs=(b.media(b.ORG_A),))
    prepared = b.media(b.ORG_A, kind=MediaKind.upload)
    return request, prepared, {
        "request": request.model_dump(mode="json"),
        "prepared_refs": [prepared.model_dump(mode="json")],
        "admission": {"accounting_regime": regime,
                      "price_snapshot": b.DEFAULT_PRICE.model_dump(mode="json"),
                      "budgets": {"preparation_s": 1, "queue_wait_s": 2, "generation_s": 3,
                                  "first_token_s": 1, "stall_s": 1},
                      "prepared_prompt_tokens": 1234}}


def test_load_work__the_admitted_work_with_the_prompt_count() -> None:
    request, prepared, doc = _work_doc()
    store, _ = _store(doc)
    work = asyncio.run(store.load_work(LEASE))
    assert isinstance(work, PreparedWork) and work.prompt_tokens == 1234
    assert work.request == request and work.media_refs == request.media
    assert work.prepared_refs == (prepared,), work.prepared_refs
    assert work.price_snapshot == b.DEFAULT_PRICE and work.budgets.generation_s == 3


def test_load_work__a_credit_job_is_refused_not_invented() -> None:
    _, _, doc = _work_doc("credit")
    doc["admission"]["price_snapshot"] = None
    store, _ = _store(doc)
    _refused(errors.InvalidRequest, store.load_work(LEASE))


def test_complete__fails_closed_after_the_fence_and_raises_the_fences_refusals() -> None:
    """A lease that holds reaches D5's stub (0A000): NotImplementedError, never a success.
    A fence refusal is its own type, never swallowed into that."""
    store, conn = _store(psycopg.errors.FeatureNotSupported("D5"),
                         _db_error("P0001", "stale_lease: generation 1 != 2"))
    outcome = TerminalOutcome(**OUTCOME)
    _refused(NotImplementedError, store.complete(LEASE, outcome))
    assert _args(conn)["outcome"] == outcome.model_dump(mode="json")
    _refused(errors.StaleLease, store.complete(LEASE, outcome))


def test_recover__outcomes_events_and_the_unsettleable_backlog() -> None:
    """A sweep answers outcomes and index events (in order); a job it could not reap is the
    store's backlog, never a returned record, and each sweep reports its own backlog."""
    event = {"event_id": "00000009-0000-4000-8000-000000000009", "job_id": b.ORG_A,
             "org_id": b.ORG_A, "key_id": b.KEY_A, "kind": "inference_dispatch",
             "execution_mode": "stream", "available_at": "2026-09-20T12:00:00Z",
             "attempt": 1}
    stuck = {"job_id": b.ORG_B, "code": "P0003", "detail": "stuck"}
    store, _ = _store([{"outcome": OUTCOME}, {"index_event": event},
                       {"unsettleable": stuck}], [])
    produced = asyncio.run(store.recover())
    assert produced == (TerminalOutcome(**OUTCOME), IndexEvent(**event)), produced
    assert store.unsettleable == {b.ORG_B: "P0003: stuck"}, store.unsettleable
    assert asyncio.run(store.recover()) == ()
    assert store.unsettleable == {}, "a previous sweep's backlog was reported again"


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
