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
