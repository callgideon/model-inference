#!/usr/bin/env python3
"""F cancel-cause: `JobStore.cancel(org, handle, *, cause)` (R21; G2's D-new, D5 item 3).

The port and the set of causes a canceller may name. The settlement each cause gets is
proved by the exported conformance cases (`dur_settle__cancel_records_*`,
`credit_settle__cancel_records_*`); what is here is the shape every adapter shares.

    uv run --frozen pytest -q tests/contracts/test_cancel_cause.py
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from infrx.contracts import errors, ports
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.state import FakeJobStore
from infrx.contracts.records import CANCEL_CAUSES, TerminalCause
from infrx.state.jobstore import PgJobStore

THE_THREE = {TerminalCause.client_cancelled, TerminalCause.client_disconnected,
             TerminalCause.sync_deadline}


def _cause(operation) -> inspect.Parameter:
    return inspect.signature(operation).parameters["cause"]


def test_dur_settle__cancel_takes_a_keyword_cause_that_defaults_to_client_cancelled():
    """Additive: keyword-only, defaulting to `client_cancelled`, so every existing
    `cancel(org, handle)` keeps its meaning; exactly the two client causes and the
    platform's synchronous deadline may be named."""
    for operation in (ports.JobStore.cancel, FakeJobStore.cancel, PgJobStore.cancel):
        cause = _cause(operation)
        assert cause.kind is inspect.Parameter.KEYWORD_ONLY, operation.__qualname__
        assert cause.default is TerminalCause.client_cancelled, operation.__qualname__
    assert set(CANCEL_CAUSES) == THE_THREE


def test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record():
    """0016's `infrx.cancel` records `client_cancelled` whatever it is sent, so until D5's
    0018 the adapter refuses every other cause - typed, `UnsupportedParameter` (an
    `InvalidRequest`) naming `cause`, before any SQL - rather than silently recording the
    wrong one. The default still reaches the database."""
    sent = []
    committed = {"job_id": b.ORG_A, "state": "cancelled", "cause": "client_cancelled",
                 "result_ref": None, "settlement_state": "released_free",
                 "debit": "0.00000000", "settled_at": "2026-09-20T12:00:00Z",
                 "reconcile_after": None}

    class Conn:
        async def execute(self, sql, params=()):
            sent.append(sql)

            async def one():
                return (committed,)
            return SimpleNamespace(fetchone=one)

        async def close(self):
            pass

    async def connect():
        return Conn()

    store = PgJobStore(connect)
    for cause in (TerminalCause.client_disconnected, TerminalCause.sync_deadline,
                  TerminalCause.completed, "bogus"):
        with pytest.raises(errors.UnsupportedParameter) as refused:
            asyncio.run(store.cancel(b.ORG_A, "job_x", cause=cause))
        assert refused.value.param == "cause" and isinstance(refused.value, errors.InvalidRequest)
    assert sent == [], "a refused cause reached the database"
    outcome = asyncio.run(store.cancel(b.ORG_A, "job_x"))
    assert outcome.cause is TerminalCause.client_cancelled and len(sent) == 1
