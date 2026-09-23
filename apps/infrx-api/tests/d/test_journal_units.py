#!/usr/bin/env python3
"""D4: the Python half of `PgStreamStore`, with NO database - what the adapter (not the SQL)
decides: the limits it sends (the store's), that a refusal after a committed R29
terminalization is raised as its type (R39), that `append` answers the COMMITTED rows and not
its input, how replay cursors travel, and that `finalize_in_transaction` treats the outcome as
a lookup key. `tests/d/code_mutants_d4.py` runs here, so it needs no Docker; the SQL is
`test_journal.py` / `test_journal_races.py`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import Chunk, Cursor, EngineEvent, TerminalOutcome
from infrx.state.journal import PgStreamStore

from .test_adapter_units import _Conn, _refused
from .test_lease_units import LEASE, OUTCOME, REFUSED

ROW = {"job_id": b.ORG_A, "generation": 2, "sequence": 4, "event_type": "delta",
       "payload": {"content": "Hello"}, "bytes": 20,
       "persisted_at": "2026-09-20T12:00:07+00:00", "expires_at": "2026-09-20T13:00:07+00:00"}
WHEN = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _stream(*answers, limits=DEFAULTS):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return PgStreamStore(connect, limits=limits), conn


def _sent(conn, n: int = 0) -> dict:
    return conn.sent[n][1][0].obj


def test_append__sends_the_stores_own_limits_and_raises_a_committed_refusal() -> None:
    """The event limit, the chunk TTL and the reconciliation window are the store's (as
    retuned); an R29 refusal answered as data after its commit is raised as its type."""
    limits = DEFAULTS.replace(journal_event_max_bytes=77, journal_chunk_ttl_s=9.0,
                              unknown_usage_reconcile_s=5.0)
    store, conn = _stream(REFUSED, limits=limits)
    _refused(errors.AlreadyTerminal, store.append(LEASE, b.events("late")))
    assert _sent(conn) == {"lease": LEASE.model_dump(mode="json"),
                           "events": [{"type": "delta", "payload": {"content": "late"}}],
                           "limits": {"journal_event_max_bytes": 77, "journal_chunk_ttl_s": 9.0,
                                      "unknown_usage_reconcile_s": 5.0}}, _sent(conn)


def test_append__answers_the_committed_rows_not_its_input() -> None:
    """The chunks relayed are the rows the database committed - their cursors and the byte
    measure the database stored - never a record rebuilt from the caller's events."""
    later = dict(ROW, sequence=5, payload={"content": " world"}, bytes=21)
    store, _ = _stream({"chunks": [ROW, later]})
    chunks = asyncio.run(store.append(LEASE, b.events("Hello", " world")))
    assert chunks == (Chunk.model_validate(ROW), Chunk.model_validate(later)), chunks


def test_read_owned__sends_the_cursor_and_returns_the_next_one() -> None:
    """The next cursor is the last chunk's, or the one passed when the page is empty; a limit
    that is not an integer is refused before anything is sent."""
    store, conn = _stream({"chunks": [ROW]}, {"chunks": []})
    page, cursor = asyncio.run(store.read_owned(b.ORG_A, "job_x", Cursor.parse("2-3"), 5))
    assert page == (Chunk.model_validate(ROW),) and cursor == Cursor.parse("2-4"), cursor
    assert _sent(conn) == {"org_id": b.ORG_A, "job_handle": "job_x", "limit": 5,
                           "cursor": {"generation": 2, "sequence": 3}}, _sent(conn)
    head = Cursor.parse("2-4")
    assert asyncio.run(store.read_owned(b.ORG_A, "job_x", head)) == ((), head)
    for bad in (2.5, True, "10"):
        _refused(errors.InvalidRequest, store.read_owned(b.ORG_A, "job_x", None, bad))
    assert len(conn.sent) == 2, "a malformed limit reached the database"


def test_finalize__the_outcome_is_a_lookup_key_not_content() -> None:
    """Unknown job `not_found`; not terminal `state_conflict`; an expired journal
    `journal_expired`; an outcome that is not the committed one `state_conflict`; the
    committed one reads back the terminal chunk."""
    outcome = TerminalOutcome(**OUTCOME)
    terminal = (b.ORG_A, 1, 3, "terminal", {"state": "cancelled"}, 87, WHEN, WHEN)
    lying = outcome.model_copy(update={"cause": "client_disconnected"})
    store, _ = _stream([], [(None, False, *[None] * 8)], [(OUTCOME, True, *terminal)],
                       [(OUTCOME, False, *terminal)], [(OUTCOME, False, *terminal)])
    _refused(errors.NotFound, store.finalize_in_transaction(outcome))
    _refused(errors.StateConflict, store.finalize_in_transaction(outcome))
    _refused(errors.JournalExpired, store.finalize_in_transaction(outcome))
    _refused(errors.StateConflict, store.finalize_in_transaction(lying))
    chunk = asyncio.run(store.finalize_in_transaction(outcome))
    assert (chunk.cursor.token, chunk.event_type.value, chunk.bytes) == ("1-3", "terminal", 87)


def test_expire__passes_the_callers_bound_and_counts() -> None:
    """A caller's `now` travels as a bound (the SQL never goes past its own clock, R7), and
    so does the per-pass job bound (review H1)."""
    store, conn = _stream(3, 0)
    assert asyncio.run(store.expire(WHEN)) == 3
    assert _sent(conn) == {"now": WHEN.isoformat(), "limit": 1000}, _sent(conn)
    assert asyncio.run(store.expire()) == 0 and _sent(conn, 1) == {"now": None, "limit": 1000}


UNJOURNALABLE = ({"content": "a\x00b"}, {"a\x00b": "key"}, {"logprob": float("nan")},
                 {"logprob": float("inf")}, {"top": [{"logprob": float("-inf")}]},
                 {"top": [["\x00"]]})
#: Confirmation A2: jsonb refuses a lone UTF-16 surrogate in a string or key (22P02). The
#: fake cannot even measure one (`compact_bytes` raises UnicodeEncodeError), so it is not in
#: the pinned fake delta below.
SURROGATES = ({"content": "x\ud800"}, {"\udc00": 1}, {"top": ["\udfff"]})


def test_append__refuses_what_jsonb_cannot_store_before_sending_it() -> None:
    """Review M1: a NUL character (in a key or a value, at any depth) or a non-finite number
    is `journal_write_failed` for the whole batch and nothing reaches the database (jsonb
    would raise an untyped 22P05/22P02); the literal text `\\u0000` and finite floats are
    journalable. The FAKE stores all six today (pinned below): when F makes it refuse them
    too (coordinator request) this assertion is the one to flip."""
    store, conn = _stream({"chunks": [ROW]})
    for payload in (*UNJOURNALABLE, *SURROGATES):
        bad = EngineEvent(type="delta", payload=payload)
        # confirmation A1: wherever the bad event sits - last, first, in the middle
        for batch in ((*b.events("fine"), bad), (bad, *b.events("fine")),
                      (*b.events("a"), bad, *b.events("b"))):
            _refused(errors.JournalWriteFailed, store.append(LEASE, batch))
    assert conn.sent == [], "an unjournalable batch reached the database"
    # confirmation A4: every other JSON value is journalable - str, float, int, bool, null,
    # list, object, and the literal text of an escape
    asyncio.run(store.append(LEASE, (EngineEvent(type="delta", payload={
        "content": "\\u0000 caf\u00e9", "logprob": -3.2e-07, "n": 1, "b": True, "z": None,
        "l": [0, "x", [1.5]], "o": {"k": False}}),)))
    assert len(conn.sent) == 1, "a journalable payload was refused"

    async def fake_stores_them():
        from infrx.contracts.conformance.jobs import _stream_job
        from infrx.contracts.fakes.factories import streamstore_factory
        harness = streamstore_factory()
        harness.extra["grant"](b.ORG_A, "25")
        _, _, lease = await _stream_job(harness)
        return [len(await harness.port.append(lease, (EngineEvent(type="delta",
                                                                  payload=payload),)))
                for payload in UNJOURNALABLE]
    assert asyncio.run(fake_stores_them()) == [1] * len(UNJOURNALABLE), \
        "the fake now refuses unjournalable payloads: drop this delta and its request"
