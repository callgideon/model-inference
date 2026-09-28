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


# --- WR-R2-1: PgReleaseStore (0039) --------------------------------------------------------
REF = f"lab:policy:{NEMO}:000000b0-0000-4000-8000-0000000000b0@sha256:{'a' * 64}"
DECISION = {"schema": "lab.rollout_decision.1", "provider_org_id": NEMO, "policy_ref": REF,
            "decision": "rollback", "evidence_refs": [],
            "decided_by": "000000c0-0000-4000-8000-0000000000c0",
            "decided_at": "2026-09-27T12:00:00Z"}
ROW = {"state": "running", "fence": 1, "plan_digest": "sha256:" + "a1" * 32,
       "started_at": "2026-09-27T10:00:00+00:00"}


def test_release__sends_the_revision_the_fence_and_a_validated_decision() -> None:
    import asyncio

    import pytest
    from infrx.contracts.lab import records
    from infrx.state.lab_rollout import PgReleaseStore
    conn = _Conn([ROW, ROW, {"fence": 2}, {"fence": 3}])

    async def connect():
        return conn
    store = PgReleaseStore(connect)
    launched = _ok(store.start(REF, provider_org_id=NEMO, plan_digest=ROW["plan_digest"],
                               decided_by="u", reason="go"))
    got = _ok(store.release(REF))
    assert (got.state, got.fence, got.plan_digest, got.started_at.year) == \
        ("running", 1, ROW["plan_digest"], 2026) and launched == got
    assert _ok(store.transition(REF, fence=1, to="rolled_back", decision=DECISION,
                                reasons=("p99",))) == 2
    with pytest.raises(records.LabRejected):
        asyncio.run(store.transition(REF, fence=2, to="approved",
                             decision={**DECISION, "decision": "expand"}, reasons=()))
    assert [_sent(conn, n) for n in range(3)] == [
        ("lab_release_start", {"provider_org_id": NEMO, "policy_ref": REF,
                               "plan_digest": ROW["plan_digest"], "decided_by": "u",
                               "reason": "go"}),
        ("lab_release", {"policy_ref": REF}),
        ("lab_release_transition", {"policy_ref": REF, "fence": 1, "to": "rolled_back",
                                    "decision": DECISION, "reasons": ["p99"]})]
    assert len(conn.sent) == 3, "a malformed decision reached the database"
