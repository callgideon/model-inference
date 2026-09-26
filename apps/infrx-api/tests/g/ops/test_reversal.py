"""RUNBOOK-3: the W7f reversal (`credit-transition --to legacy_usd`) at the seam the mutants
edit - `transition.apply` over a scripted store holding CREDIT work in flight, and the
operator session's audited-once rule. `test_reversal_pg.py` drills the same reversal end to
end on PostgreSQL (outside the mutant runner, like every `*_pg.py`).
"""
from __future__ import annotations

import asyncio

import pytest

from infrx.operations import transition

from . import fakes
from .test_transition import R, ScriptedStore

CREDIT_ON = {"legacy_usd_admission": False, "credit_admission": True, "signup_grant": True}


def back(w, store, key, timeout=10.0):
    """`credit-transition --to legacy_usd` as the CLI calls it; (result, the principal)."""
    ticks = iter(range(10_000))

    async def go():
        op = await w.ops.operator(w.operator_secret)
        result = await transition.apply(op, store, target="legacy_usd", idempotency_key=key,
                                        reason=R, drain_timeout_s=timeout, poll_s=1.0,
                                        sleep=lambda _: asyncio.sleep(0),
                                        monotonic=lambda: float(next(ticks)))
        return result, op.principal
    return asyncio.run(go())


def test_reversal__credit_is_frozen_drained_then_legacy_enabled_audited_once_as_the_reversal():
    """After W7f the way back freezes `credit_admission`, waits until no CREDIT job is in
    flight, and only then enables `legacy_usd_admission`; `signup_grant` is left as it is.
    The run is audited once under its key, as the operator, recording the reversal (target
    `legacy_usd`, no card) with the flags it started from; a replay of the key answers the
    recorded result without touching the store. Past the drain bound the run stops with
    CREDIT frozen, legacy still off and nothing audited.
    Oracle: a reversal that skipped the drain reopens USD with CREDIT work running; one
    audited as the forward move misreads in the audit; a replay that re-ran the write would
    freeze CREDIT again after a later roll-forward re-activated it."""
    w = fakes.world()
    store = ScriptedStore(flying=[1, 0], regime="credit", flags=dict(CREDIT_ON))
    result, principal = back(w, store, "revert-w1")
    sets = [c for c in store.calls if c[0] == "set"]
    assert sets == [("set", "credit_admission", False), ("set", "legacy_usd_admission", True)]
    assert store.calls.index(sets[0]) < store.calls.index(("inventory", 0)) \
        < store.calls.index(sets[1]), store.calls
    assert result["applied"] == [{"flag": "credit_admission", "enabled": False},
                                 {"flag": "legacy_usd_admission", "enabled": True}]
    assert result["flags"] == {"legacy_usd_admission": True, "credit_admission": False,
                               "signup_grant": True}
    assert result["restart_with"] == {"ACCOUNTING_REGIME": "legacy_usd"}
    assert result["public_card"] is None
    [entry] = w.audit.entries
    assert (entry.idempotency_key, entry.actor_principal, entry.after["operation"]) == \
        ("revert-w1", principal, "transition")
    assert entry.before == {"flags": CREDIT_ON}
    assert entry.after["request"] == {"target": "legacy_usd", "card": None, "input_rate": None,
                                      "output_rate": None, "freeze_only": False}
    assert entry.after["result"] == result
    seen = len(store.calls)
    assert back(w, store, "revert-w1")[0] == result
    assert store.calls[seen:] == [] and len(w.audit.entries) == 1       # the key's replay

    stuck, audited = ScriptedStore(flying=[1], regime="credit", flags=dict(CREDIT_ON)), \
        fakes.world()
    with pytest.raises(transition.TransitionBlocked) as blocked:
        back(audited, stuck, "revert-w2", timeout=3.0)
    assert [b["code"] for b in blocked.value.report["blockers"]] == ["in_flight"]
    assert blocked.value.report["applied"] == [{"flag": "credit_admission", "enabled": False}]
    assert stuck.flags == {**CREDIT_ON, "credit_admission": False}
    assert audited.audit.entries == []
