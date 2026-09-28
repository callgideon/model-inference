#!/usr/bin/env python3
"""D8: the Python half of `PgLabelLog`, `PgRunLedger`, `PgTeacherLedger` and
`PgCheckpointLedger`, with NO database - what they send to the RPCs of
`0042_lab_d8_ledgers.sql` and what they hand back. `code_mutants_d8.py`'s Python list runs
here; the SQL is `test_d8_ledgers.py`.

B3's `CheckpointEvent`/`Subscription` are the eval-ops lane's (`infrx.evaluation.checkpoints`,
merging beside this lane); on a tree without them `b3_models` registers stand-ins with their
exact fields (B3 582c7e4), and the real models are used wherever they exist.

    uv run --frozen pytest -q tests/d/test_d8_units.py
"""
from __future__ import annotations

import sys
import types
from typing import Any, Literal

from infrx.contracts.v2.money_units import ProviderUsd
from infrx.state.lab_consent import Consent
from infrx.state.lab_pipeline import (PgCheckpointLedger, PgLabelLog, PgRunLedger,
                                      PgTeacherLedger)

from .test_adapter_units import _Conn
from .test_d7_units import NEMO, _ok, _sent

RUN_REF = f"lab:external_run:{NEMO}:00000099-0000-4000-8000-000000000099@sha256:{'e' * 64}"
DS = f"lab:dataset:{NEMO}:00000098-0000-4000-8000-000000000098@sha256:{'d' * 64}"
CP = "000000ce-0000-4000-8000-0000000000ce"
EVENT = {"schema": "infrx.checkpoint_event.1", "provider_org_id": NEMO, "key_id": "k",
         "checkpoint_id": CP, "external_run_ref": RUN_REF, "step": 3,
         "artifact": {"uri": "mem://x", "digest": "sha256:" + "a" * 64},
         "issued_at": "2026-09-28T10:00:00Z"}
TEACHER = {"run_id": "r", "provider_org_id": NEMO, "payer_ref": "p", "grant_id": None,
           "grant_version": None, "sample_ids": ["s"], "media_ids": [], "sent_sample_ids": [],
           "price_version": "v", "reserved": "2.00000000", "actual": None,
           "state": "prepared", "submit_key": None, "external_id": None,
           "purpose": "teacher_annotation", "dataset_ref": DS}


def b3_models() -> tuple[Any, Any]:
    try:
        from infrx.evaluation.checkpoints import CheckpointEvent, Subscription
        return CheckpointEvent, Subscription
    except ImportError:
        pass
    from pydantic import Field

    from infrx.contracts.lab import records as lab

    class Artifact(lab.LabModel):
        uri: lab.Text
        digest: lab.Sha256

    class CheckpointEvent(lab.LabModel):
        schema_id: Literal["infrx.checkpoint_event.1"] = Field(alias="schema")
        provider_org_id: lab.Uuid
        key_id: lab.Text
        checkpoint_id: lab.Uuid
        external_run_ref: lab.RefOf("external_run")
        step: int = Field(ge=0)
        artifact: Artifact
        issued_at: lab.Ts

    class Subscription(lab.LabModel):
        subscription_id: lab.Uuid
        provider_org_id: lab.Uuid
        owner_user_id: lab.Uuid
        external_run_ref: lab.RefOf("external_run")
        dataset_ref: lab.RefOf("dataset")
        harness_ref: lab.RefOf("harness")
        evaluator_ref: lab.RefOf("evaluator")
        evaluator: dict[str, Any]
        seed: int = Field(ge=0)
        max_cases: int = Field(ge=1)
        run_limit: lab.Amount
        limit: lab.Amount
        max_active: int = Field(ge=1)
        policy: Literal["latest_only", "every"]

    package = sys.modules.setdefault("infrx.evaluation", types.ModuleType("infrx.evaluation"))
    package.__path__ = []
    module = types.ModuleType("infrx.evaluation.checkpoints")
    module.CheckpointEvent, module.Subscription = CheckpointEvent, Subscription
    sys.modules["infrx.evaluation.checkpoints"] = module
    return CheckpointEvent, Subscription


def _store(cls, *answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return cls(connect), conn


def test_label_log__sends_the_provider_and_the_event_whole() -> None:
    event = {"key": "k", "kind": "label", "dataset_ref": DS}
    store, conn = _store(PgLabelLog, event, [event])
    assert _ok(store.append(event, provider_org_id=NEMO)) == event
    assert _ok(store.events(DS, provider_org_id=NEMO)) == [event]
    assert [_sent(conn, n) for n in range(2)] == [
        ("lab_label_append", {"provider_org_id": NEMO, "event": event}),
        ("lab_label_events", {"provider_org_id": NEMO, "dataset_ref": DS})]


def test_run_ledger__sends_the_cas_the_key_and_the_cost_as_given() -> None:
    store, conn = _store(PgRunLedger, None, {"state": "prepared"}, {"state": "held"},
                         {"state": "settled"}, {"state": "released"}, {"a": 1}, None)
    assert _ok(store.get("e", provider_org_id=NEMO)) is None
    assert _ok(store.move("e", provider_org_id=NEMO, expected=None, target="prepared",
                          run_ref=RUN_REF, limit="1.00000000")) == {"state": "prepared"}
    _ok(store.reserve("submit:e", provider_org_id=NEMO, payer_ref="p", limit="1.00000000"))
    _ok(store.settle("submit:e", provider_org_id=NEMO, cost=None))
    _ok(store.release("submit:e", provider_org_id=NEMO))
    assert _ok(store.note("n", {"a": 1}, provider_org_id=NEMO)) == {"a": 1}
    assert _ok(store.noted("n", provider_org_id=NEMO)) is None
    key = {"provider_org_id": NEMO, "key": "submit:e"}
    assert [_sent(conn, n) for n in range(7)] == [
        ("lab_external_run_get", {"provider_org_id": NEMO, "external_run_id": "e"}),
        ("lab_external_run_move", {"provider_org_id": NEMO, "external_run_id": "e",
                                   "expected": None, "target": "prepared",
                                   "fields": {"run_ref": RUN_REF, "limit": "1.00000000"}}),
        ("lab_run_reserve", {**key, "payer_ref": "p", "limit": "1.00000000"}),
        ("lab_run_settle", {**key, "cost": None}),
        ("lab_run_release", key),
        ("lab_pipeline_note", {"provider_org_id": NEMO, "key": "n", "body": {"a": 1}}),
        ("lab_pipeline_noted", {"provider_org_id": NEMO, "key": "n"})]


def test_teacher_ledger__reserves_the_dataset_and_reads_its_consent_back() -> None:
    store, conn = _store(PgTeacherLedger, TEACHER, TEACHER, {"inserted": 1},
                         [{"sample_id": "s", "reason": "not_sent", "recorded_at": "t"}])
    run = _ok(store.reserve(run_id="r", provider_org_id=NEMO, payer_ref="p",
                            consent=Consent(DS, 1), sample_ids=("s",), media_ids=frozenset(),
                            price_version="v", max_cost=ProviderUsd("2.00000000")))
    assert (run.consent, run.state, run.sample_ids) == (Consent(DS, 1), "prepared", ("s",))
    _ok(store.record_sent("r", ("s",)))
    assert _ok(store.record_failures("r", [("s", "not_sent")])) == 1
    assert _ok(store.failures("r")) == [("s", "not_sent")]
    assert [_sent(conn, n) for n in range(4)] == [
        ("lab_teacher_reserve", {"run_id": "r", "provider_org_id": NEMO, "payer_ref": "p",
                                 "dataset_ref": DS, "sample_ids": ["s"],
                                 "price_version": "v", "max_cost": "2.00000000"}),
        ("lab_teacher_record_sent", {"run_id": "r", "sample_ids": ["s"]}),
        ("lab_teacher_record_failures", {"run_id": "r", "failures": [
            {"sample_id": "s", "reason": "not_sent"}]}),
        ("lab_teacher_failures", {"run_id": "r"})]


def test_checkpoint_ledger__round_trips_b3s_models_provider_scoped() -> None:
    event_type, _ = b3_models()
    event = event_type.model_validate(EVENT)
    store, conn = _store(PgCheckpointLedger, EVENT, EVENT, [{"event": EVENT, "rejected": True}],
                         {"reason": "x"}, {CP: {"state": "queued"}},
                         {"state": "skipped", "reason": "budget", "run_id": None})
    assert _ok(store.record_event(event)) == event
    assert _ok(store.event(CP, provider_org_id=NEMO)) == event
    assert _ok(store.events(RUN_REF, provider_org_id=NEMO)) == [(event, True)]
    _ok(store.reject(CP, "x", provider_org_id=NEMO))
    assert _ok(store.decisions("sub")) == {CP: {"state": "queued"}}
    _ok(store.decide("sub", CP, state="queued", reason=None, run_id="run-1"))
    assert [_sent(conn, n) for n in range(6)] == [
        ("lab_checkpoint_record_event", {"event": EVENT}),
        ("lab_checkpoint_event", {"provider_org_id": NEMO, "checkpoint_id": CP}),
        ("lab_checkpoint_events", {"provider_org_id": NEMO, "external_run_ref": RUN_REF}),
        ("lab_checkpoint_reject", {"provider_org_id": NEMO, "checkpoint_id": CP,
                                   "reason": "x"}),
        ("lab_checkpoint_decisions", {"subscription_id": "sub"}),
        ("lab_checkpoint_decide", {"subscription_id": "sub", "checkpoint_id": CP,
                                   "state": "queued", "reason": None, "run_id": "run-1"})]


# --- the remaining LW3 requests (0043) --------------------------------------------------------
POLICY = {"schema": "lab.rollout_policy.1", "provider_org_id": NEMO,
          "policy_id": "000000b0-0000-4000-8000-0000000000b0", "version": 1,
          "created_at": "2026-09-27T10:00:00Z", "endpoint_id": "000000e0-0000-4000-8000-0000000000e0",
          "baseline_ref": f"lab:serving:{NEMO}:0000005e-0000-4000-8000-000000000001@sha256:{'e' * 64}",
          "mode": "canary", "cohort": "account", "candidates": [
              {"serving_ref": f"lab:serving:{NEMO}:0000005e-0000-4000-8000-000000000002"
                              f"@sha256:{'e' * 64}", "weight_bp": 2500}]}
POLICY_REF = f"lab:policy:{NEMO}:000000b0-0000-4000-8000-0000000000b0@sha256:{'a' * 64}"


def r1_release():
    """R1's `Release` (`infrx.rollouts.routing`), or a stand-in with its fields."""
    try:
        from infrx.rollouts.routing import Release
        return Release
    except ImportError:
        pass
    from dataclasses import dataclass
    from typing import Mapping

    @dataclass(frozen=True)
    class Release:
        policy: Any
        policy_ref: str
        revisions: Mapping[str, str]
        shadow_limit: int = 0
    package = sys.modules.setdefault("infrx.rollouts", types.ModuleType("infrx.rollouts"))
    package.__path__ = []
    module = types.ModuleType("infrx.rollouts.routing")
    module.Release = Release
    sys.modules["infrx.rollouts.routing"] = module
    return Release


class _Rows:
    """A connection answering whole rows (the routing port reads a table function)."""

    def __init__(self, rows: list) -> None:
        self.rows, self.sent = rows, []

    async def execute(self, sql, params=()):
        self.sent.append((sql, params))
        row = self.rows.pop(0)

        async def one():
            return row
        return types.SimpleNamespace(fetchone=one)

    async def close(self) -> None:
        pass


def test_routing__reads_the_head_as_rs_release_eligibility_and_records_once() -> None:
    from infrx.contracts.lab import records
    from infrx.state.lab_rollout import PgRoutingReleases
    release_type = r1_release()
    pins = {POLICY["candidates"][0]["serving_ref"]: "nemostation/marlin-2b@cand"}
    conn = _Rows([(POLICY, POLICY_REF, pins, 4), None, (True,), (None,)])

    async def connect():
        return conn
    store = PgRoutingReleases(connect)
    got = _ok(store.active("nemostation/marlin-2b"))
    assert type(got) is release_type, got
    assert (got.policy.policy_id, got.policy_ref, dict(got.revisions), got.shadow_limit) == \
        (POLICY["policy_id"], POLICY_REF, pins, 4), got
    assert _ok(store.active("nemostation/other")) is None
    assert _ok(store.eligible("p", types.SimpleNamespace(org_id="o"))) is True
    assignment = records.parse({"schema": "lab.rollout_assignment.1", "provider_org_id": NEMO,
                                "policy_ref": POLICY_REF, "request_id": CP,
                                "cohort_digest": "sha256:" + "c" * 64,
                                "serving_ref": POLICY["baseline_ref"], "pinned_by": "cohort"})
    _ok(store.record(assignment))
    assert [p for _, p in conn.sent[:3]] == [("nemostation/marlin-2b",), ("nemostation/other",),
                                             ("p", "o")]
    sent = conn.sent[3][1][0].obj
    assert (sent.get("request_id"), sent.get("schema")) == (CP, "lab.rollout_assignment.1"), sent


def test_reads_and_proposals__send_the_provider_the_fence_and_a_validated_decision() -> None:
    import asyncio

    import pytest
    from infrx.contracts.lab import records
    from infrx.state.lab_consent import PgJudgeLedger
    from infrx.state.lab_data import PgLabReads
    from infrx.state.lab_rollout import PgReleaseProposals
    conn = _Conn([[], {"experiment_id": "e"}, [], {"state": "proposed"}, {"state": "approved"},
                  [], [], {"calibration_id": 3}])

    async def connect():
        return conn
    reads, proposals = PgLabReads(connect), PgReleaseProposals(connect)
    ledger, cps = PgJudgeLedger(connect), PgCheckpointLedger(connect)
    decision = {"schema": "lab.rollout_decision.1", "provider_org_id": NEMO,
                "policy_ref": POLICY_REF, "decision": "expand", "evidence_refs": [
                    f"lab:run:{NEMO}:000000b1-0000-4000-8000-0000000000b1@sha256:{'b' * 64}"],
                "decided_by": CP, "decided_at": "2026-09-28T12:00:00Z"}
    _ok(reads.datasets(provider_org_id=NEMO))
    _ok(reads.put_experiment("e", provider_org_id=NEMO, protocol={"k": 1},
                             protocol_digest="sha256:x", baseline_run_ref="b",
                             candidate_run_ref="c", actor="dev"))
    _ok(reads.experiments(provider_org_id=NEMO))
    _ok(proposals.propose(POLICY_REF, provider_org_id=NEMO, proposal_id="q", kind="expand",
                          fence=2, proposed_by="u"))
    _ok(proposals.decide("q", approve=True, decided_by="u", decision=decision,
                         reasons=("b2",)))
    with pytest.raises(records.LabRejected):
        asyncio.run(proposals.decide("q", approve=True, decided_by="u",
                                     decision={**decision, "decision": "maybe"}))
    _ok(proposals.proposals(provider_org_id=NEMO))
    _ok(cps.listing(provider_org_id=NEMO))
    assert _ok(ledger.put_calibration({"state": "uncalibrated"}, provider_org_id=NEMO,
                                      grantor_org_id="g", judge_model="j",
                                      rubric_version=1)) == 3
    assert [_sent(conn, n) for n in range(8)] == [
        ("lab_list_datasets", {"provider_org_id": NEMO}),
        ("lab_put_experiment", {"provider_org_id": NEMO, "experiment_id": "e",
                                "protocol": {"k": 1}, "protocol_digest": "sha256:x",
                                "baseline_run_ref": "b", "candidate_run_ref": "c",
                                "actor": "dev"}),
        ("lab_experiments", {"provider_org_id": NEMO}),
        ("lab_propose_release", {"provider_org_id": NEMO, "proposal_id": "q",
                                 "policy_ref": POLICY_REF, "kind": "expand", "fence": 2,
                                 "proposed_by": "u"}),
        ("lab_decide_release_proposal", {"proposal_id": "q", "approve": True,
                                         "decided_by": "u", "decision": decision,
                                         "reasons": ["b2"]}),
        ("lab_release_proposals", {"provider_org_id": NEMO}),
        ("lab_checkpoint_listing", {"provider_org_id": NEMO}),
        ("lab_put_judge_calibration", {"provider_org_id": NEMO, "grantor_org_id": "g",
                                       "judge_model": "j", "rubric_version": 1,
                                       "calibration": {"state": "uncalibrated"}})]
    assert len(conn.sent) == 8, "a malformed decision reached the database"
