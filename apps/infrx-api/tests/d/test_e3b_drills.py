#!/usr/bin/env python3
"""E3B's pending drills dr01/dr02 (D2) and dr03/dr04/dr09 (D3)
(`tests/integration/backend/test_drills.py` on the E3B branch), run here against the real
PostgreSQL store with their assertions VERBATIM, so they can turn green there by swapping
`rig("postgres", …)` for this rig (`infrx.state.pgtesting.make_jobstore_factory` on E2's
PostgreSQL - integration request). dr05 (stale append) also needs D4's StreamStore
(`append`/`read_owned`), so it is not here: its D3 half - a stale generation refused by the
fence every append must call first - is `tests/d/test_lease_races.py`.

D4 adds dr05, dr06, dr08 and dr10 below (verbatim bodies; `h.extra["stream"]` is the real
`PgStreamStore` on the same database - `pgtesting.make_jobstore_factory`'s `stream` hook).
"""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import CrashAfterCommit
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import IndexEvent, JobState, OutboxKind, SettlementState
from infrx.contracts.records import TerminalCause

from . import pgharness, pgstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
GRANT = "5"


def rig(**limits):
    h = pgstore.factory(limits=DEFAULTS.replace(**limits) if limits else None)
    h.extra["grant"](b.ORG_A, GRANT)
    h.extra["grant"](b.ORG_B, GRANT)
    return h


async def admit(h, *, org=b.ORG_A, key=b.KEY_A, idem_key="k1"):
    request = b.request(h, org_id=org, key_id=key)
    return request, await h.port.admit(request, b.idem(request, idem_key))


async def assert_conserved(h, org, handles):
    debit, reserved = Decimal(0), Decimal(0)
    for handle in handles:
        admission, outcome = await h.port.get_owned(org, handle)
        if outcome is None or outcome.settlement_state is SettlementState.held_unknown:
            reserved += admission.maximum_hold
        if outcome is not None:
            debit += outcome.debit
    balance = h.extra["balance"](org)
    assert balance["ledger"] == Decimal(GRANT) - debit, (balance, debit)
    assert balance["reserved"] == reserved, (balance, reserved)
    assert balance["available"] >= 0, balance


def test_e3b_dr01_acceptance_crash_after_commit_retries_to_one_identity() -> None:
    h = rig()

    async def body():
        h.failures.crash_after_commit("admit")
        request = b.request(h)
        idem = b.idem(request, "k1")
        with pytest.raises(CrashAfterCommit):
            await h.port.admit(request, idem)
        again = await h.port.admit(request, idem)
        assert again.replayed and again.request_id == request.request_id
        assert len(h.extra["active_jobs"](b.ORG_A)) == 1
        assert h.extra["outbox_kinds"](request.request_id) == [OutboxKind.prepare_dispatch]
        assert h.extra["balance"](b.ORG_A)["reserved"] == again.maximum_hold > 0
        await assert_conserved(h, b.ORG_A, [again.job_handle])
    asyncio.run(body())


def test_e3b_dr02_a_refused_admission_leaves_nothing_behind() -> None:
    h = rig(max_active_jobs_per_key=1)

    async def refused(expected, **kw):
        request = b.request(h, **kw)
        with pytest.raises(expected):
            await h.port.admit(request, b.idem(request, str(uuid.uuid4())))
        assert h.extra["outbox"](request.request_id) == []

    async def body():
        journal_before = h.extra["journal_bytes"]()
        h.extra["revoke_key"](b.KEY_B)
        await refused(errors.InvalidApiKey, org_id=b.ORG_B, key_id=b.KEY_B)
        h.extra["unrevoke_key"](b.KEY_B)
        h.extra["set_price"](b.MODEL, None)
        await refused(errors.InvalidRequest)
        h.extra["set_price"](b.MODEL, b.DEFAULT_PRICE)
        _, kept = await admit(h)                          # fills the key's one slot
        await refused(errors.CapacityExhausted)
        assert h.extra["balance"](b.ORG_B)["reserved"] == 0
        assert len(h.extra["active_jobs"]()) == 1
        assert h.extra["journal_bytes"]() - journal_before == \
            DEFAULTS.journal_job_reserve_bytes            # the one kept job's reservation
        await assert_conserved(h, b.ORG_A, [kept.job_handle])
        await assert_conserved(h, b.ORG_B, [])
    asyncio.run(body())


async def running(h, admission, worker="w1"):
    lease = await h.port.claim_preparation(admission.request_id, worker)
    await h.port.prepared(lease, ())
    return await h.port.claim(admission.request_id, worker)


def usage_projections(h, job_id):
    return h.extra["outbox_kinds"](job_id).count(OutboxKind.usage_projection)


# --- D3: the drills E3B names D3 for (verbatim bodies) --------------------------------
def test_e3b_dr03_a_lost_preparation_worker_is_redispatched_and_fenced() -> None:
    h = rig()

    async def body():
        _, admission = await admit(h)
        dead = await h.port.claim_preparation(admission.request_id, "w1")
        h.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
        await h.port.recover()
        kinds = h.extra["outbox_kinds"](admission.request_id)
        assert kinds == [OutboxKind.prepare_dispatch] * 2, kinds
        live = await h.port.claim_preparation(admission.request_id, "w2")
        with pytest.raises(errors.StaleLease):
            await h.port.prepared(dead, ())
        queued = await h.port.prepared(live, ())
        assert queued.state is JobState.queued
        assert h.extra["outbox_kinds"](admission.request_id).count(
            OutboxKind.inference_dispatch) == 1
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    asyncio.run(body())


def test_e3b_dr04_a_claim_whose_answer_was_lost_is_requeued_once() -> None:
    h = rig()

    async def body():
        _, admission = await admit(h)
        lease = await h.port.claim_preparation(admission.request_id, "w1")
        await h.port.prepared(lease, ())
        h.failures.crash_after_commit("claim")
        with pytest.raises(CrashAfterCommit):
            await h.port.claim(admission.request_id, "w1")
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        produced = await h.port.recover()
        events = [item for item in produced if isinstance(item, IndexEvent)]
        assert [(e.job_id, e.attempt) for e in events] == [(admission.request_id, 1)]
        second = await h.port.claim(admission.request_id, "w2")
        assert second.generation == 2
        assert h.extra["outbox_kinds"](admission.request_id).count(
            OutboxKind.inference_dispatch) == 2
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    asyncio.run(body())


def test_e3b_dr09_cancellation_beats_a_late_completion() -> None:
    h = rig()

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        cancelled = await h.port.cancel(b.ORG_A, admission.job_handle)
        assert cancelled.state is JobState.cancelled and cancelled.debit == 0
        with pytest.raises((errors.AlreadyTerminal, errors.StaleLease)):
            await h.port.complete(lease, b.outcome(admission.request_id, h))
        assert usage_projections(h, admission.request_id) == 1
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    asyncio.run(body())


# --- D4: the drills E3B names D4 for (verbatim bodies) --------------------------------
def test_e3b_dr05_a_stale_generation_cannot_append() -> None:
    h = rig()

    async def body():
        _, admission = await admit(h)
        old = await running(h, admission, "w1")
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        await h.port.recover()
        new = await h.port.claim(admission.request_id, "w1")
        assert new.generation == old.generation + 1
        stream = h.extra["stream"]
        with pytest.raises((errors.StaleLease, errors.AlreadyTerminal)):
            await stream.append(old, b.events("stale"))
        await stream.append(new, b.events("fresh"))
        chunks, _cursor = await stream.read_owned(b.ORG_A, admission.job_handle, None)
        assert {c.generation for c in chunks} == {new.generation}, chunks
        assert all("stale" not in str(c.payload) for c in chunks)
    asyncio.run(body())


def test_e3b_dr06_a_worker_lost_after_publication_is_never_regenerated() -> None:
    h = rig()

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        await h.extra["stream"].append(lease, b.events("first"))
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        produced = await h.port.recover()
        assert not [item for item in produced if isinstance(item, IndexEvent)]
        _, outcome = await h.port.get_owned(b.ORG_A, admission.job_handle)
        assert (outcome.state, outcome.cause) == (JobState.failed,
                                                  TerminalCause.lost_after_publication)
        assert outcome.settlement_state is SettlementState.held_unknown and outcome.debit == 0
        with pytest.raises((errors.StaleLease, errors.AlreadyTerminal)):
            await h.extra["stream"].append(lease, b.events("late"))
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    asyncio.run(body())


def test_e3b_dr08_a_foreign_tenant_cannot_read_cancel_or_see_a_result() -> None:
    h = rig()

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        await h.extra["stream"].append(lease, b.events("private"))
        handle = admission.job_handle
        with pytest.raises(errors.NotFound):
            await h.port.get_owned(b.ORG_B, handle)
        with pytest.raises(errors.NotFound):
            await h.extra["stream"].read_owned(b.ORG_B, handle, None)
        with pytest.raises(errors.NotFound):
            await h.port.cancel(b.ORG_B, handle)
        state, outcome = await h.port.get_owned(b.ORG_A, handle)
        assert (state.state, outcome) == (JobState.running, None)
        await assert_conserved(h, b.ORG_A, [handle])
        await assert_conserved(h, b.ORG_B, [])
    asyncio.run(body())


def test_e3b_dr10_journal_backpressure_refuses_an_oversized_event_whole() -> None:
    h = rig(journal_event_max_bytes=64)

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        with pytest.raises(errors.JournalWriteFailed):
            await h.extra["stream"].append(lease, b.events("x" * 200))
        chunks, _cursor = await h.extra["stream"].read_owned(b.ORG_A, admission.job_handle,
                                                             None)
        assert chunks == ()
    asyncio.run(body())
