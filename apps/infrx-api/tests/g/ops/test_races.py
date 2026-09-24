"""G8 point 4 at the seam the mutants edit: a same-key race answers the recorded result.

`test_races_pg.py` proves it with real concurrent connections; here the race is staged
exactly - the twin's lookup misses the row its original has already written.
"""
from __future__ import annotations

import asyncio

import pytest

from infrx.contracts import errors

from . import fakes
from .fakes import USER_A

R = "callback retry"


def test_api_ops__a_same_key_race_answers_the_recorded_result_and_writes_nothing_twice():
    """The losing twin of a same-key race gets the result its original recorded (and a
    different request under that key is IdempotencyConflict), with one ledger entry and
    one audit row. Oracle: without the recorded-row answer the twin surfaces the audit
    relation's raw `Conflict`."""
    w = fakes.world()
    real = w.audit.by_idempotency_key
    missed = {"n": 0}

    async def stale(key):                     # the twin looked before its original wrote
        if missed["n"]:
            missed["n"] -= 1
            return None
        return await real(key)
    w.audit.by_idempotency_key = stale

    async def go():
        op = await w.ops.operator(w.operator_secret)
        await op.grant_initial(USER_A, idempotency_key="g", reason=R)
        first = await op.adjust(USER_A, "5", idempotency_key="adj", reason=R)
        missed["n"] = 1
        twin = await op.adjust(USER_A, "5", idempotency_key="adj", reason=R)
        missed["n"] = 1
        with pytest.raises(errors.IdempotencyConflict):
            await op.adjust(USER_A, "6", idempotency_key="adj", reason=R)
        return first, twin
    first, twin = asyncio.run(go())
    assert twin == first
    assert len(w.ledger.entries) == 2                       # the grant and one adjustment
    assert [e.idempotency_key for e in w.audit.entries] == ["g", "adj"]
