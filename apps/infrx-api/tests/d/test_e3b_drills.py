#!/usr/bin/env python3
"""E3B's pending drills dr01/dr02 (`tests/integration/backend/test_drills.py` on the E3B
branch), run here against the real PostgreSQL store with their assertions VERBATIM, so
they can turn green there by swapping `rig("postgres", …)` for this rig
(`infrx.state.pgtesting.make_jobstore_factory` on E2's PostgreSQL - integration request).
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
from infrx.contracts.records import OutboxKind, SettlementState

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
