#!/usr/bin/env python3
"""D9: the Python half of `PgLabRolloutStore`, with NO database - what it sends to the RPCs of
`0033_lab_rollout.sql`. `code_mutants_d9.py`'s Python list runs here; the SQL is
`test_d9_rollout.py`.

    uv run --frozen pytest -q tests/d/test_d9_units.py
"""
from __future__ import annotations

from infrx.state.lab_rollout import PgLabRolloutStore

from .test_adapter_units import _Conn
from .test_d7_units import NEMO, _ok, _sent


def _store(*answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return PgLabRolloutStore(connect), conn


def test_calls__carry_the_provider_the_fence_and_expansion_evidence_only() -> None:
    store, conn = _store(*({},) * 6)
    _ok(store.start("v1", provider_org_id=NEMO, decided_by="u", reason="go"))
    _ok(store.transition("p", "pause", fence=1, provider_org_id=NEMO, decided_by="u",
                         reason="hold", policy_ref="ignored", evidence_refs=["ignored"]))
    _ok(store.transition("p", "expand", fence=2, provider_org_id=NEMO, decided_by="u",
                         reason="up", policy_ref="v2", evidence_refs=("run",)))
    _ok(store.assign("p", "r", "acct", provider_org_id=NEMO))
    _ok(store.assign("p", "r", "acct", provider_org_id=NEMO, explicit_serving_ref="s"))
    _ok(store.rollout("p", provider_org_id=NEMO))
    move = {"provider_org_id": NEMO, "policy_id": "p", "decided_by": "u"}
    assign = {"provider_org_id": NEMO, "policy_id": "p", "request_id": "r", "subject_key": "acct"}
    assert [_sent(conn, n) for n in range(6)] == [
        ("lab_rollout_start", {"provider_org_id": NEMO, "policy_ref": "v1", "decided_by": "u",
                               "reason": "go"}),
        ("lab_rollout_transition", {**move, "fence": 1, "action": "pause", "reason": "hold"}),
        ("lab_rollout_transition", {**move, "fence": 2, "action": "expand", "reason": "up",
                                    "policy_ref": "v2", "evidence_refs": ["run"]}),
        ("lab_rollout_assign", assign),
        ("lab_rollout_assign", {**assign, "explicit_serving_ref": "s"}),
        ("lab_rollout", {"provider_org_id": NEMO, "policy_id": "p"})]
