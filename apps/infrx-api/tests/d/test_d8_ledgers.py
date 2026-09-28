#!/usr/bin/env python3
"""D8 (wave-5 LW3, lab-sql; 13-lab-improvement-handoffs §D8): the pipeline ledgers of
`0042_lab_d8_ledgers.sql` on real PostgreSQL - SR-P1-1's label log, SR-P3-1's run ledger
(D6J's PROVIDER_USD budget), SR-P2-1's teacher runs on J2's judge ledger and WR-B3-1's
checkpoint ledger (PIPELINE-LINEAGE, PIPELINE-BUDGET, CHECKPOINT-IDEM, DUR-RLS), composed
with `PgLabelLog`, `PgRunLedger`, `PgTeacherLedger` and `PgCheckpointLedger`.

The executable contracts are the lanes' fakes (FakeLabelLog, FakeRunLedger,
FakeCheckpointLedger); each check asserts their answers on the RPCs. World: test_d7_lab_data's
role matrix (two consumers, NEMO and OTHER, BOTH in both products) with Lab data, the flag
`lab_submission` on, a 100 PROVIDER_USD budget for NEMO's payer, and C1's grant to NEMO also
naming external_judging (a teacher batch needs it and training). Each `check_*` is the check a
mutant in `code_mutants_d8.py` must break; rolled-back checks leave nothing, the race commits.

    INFRX_D_TASK=dlab uv run --frozen pytest -q tests/d/test_d8_ledgers.py
"""
from __future__ import annotations

import asyncio
import threading

import psycopg
import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.contracts.v2.money_units import ProviderUsd
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_consent import Consent
from infrx.state.lab_pipeline import (PgCheckpointLedger, PgLabelLog, PgRunLedger,
                                      PgTeacherLedger)

from . import checks_credit as cc
from . import pgharness
from . import test_d7_lab_data as t
from .test_d8_units import b3_models

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_d8"

l2, NEMO, OTHER, C1, BOTH = t.l2, t.NEMO, t.OTHER, t.C1, t.BOTH
call, ok, refusal, rolled_back, uid = t.call, t.ok, t.refusal, t.rolled_back, t.uid
PAYER, DEV = t.PAYER, t.l2.DEV
OTHER_PAYER = f"lab:payer:{OTHER}:0000003e-0000-4000-8000-00000000003e@sha256:{'a' * 64}"
RUBRIC = f"lab:rubric:{NEMO}:000000cb-0000-4000-8000-0000000000cb@sha256:{'c' * 64}"
TABLES = ("lab_label_events", "lab_external_runs", "lab_external_run_events",
          "lab_run_reservations", "lab_pipeline_notes", "lab_judge_run_consents",
          "lab_teacher_failures", "lab_checkpoint_events", "lab_checkpoint_rejections",
          "lab_checkpoint_subscriptions", "lab_checkpoint_decisions")
RPCS = ("lab_label_append", "lab_label_events", "lab_external_run_get",
        "lab_external_run_move", "lab_run_reserve", "lab_run_settle", "lab_run_release",
        "lab_pipeline_note", "lab_pipeline_noted", "lab_teacher_reserve",
        "lab_teacher_record_sent", "lab_teacher_record_failures", "lab_teacher_failures",
        "lab_checkpoint_record_event", "lab_checkpoint_event", "lab_checkpoint_events",
        "lab_checkpoint_reject", "lab_checkpoint_subscribe", "lab_checkpoint_subscriptions",
        "lab_checkpoint_decisions", "lab_checkpoint_decide")
W = t.W


def budget(conn, payer: str = PAYER) -> tuple[str, str]:
    b = ok(conn, "lab_budget", {"provider_org_id": NEMO, "payer_ref": payer})
    return b["reserved"], b["settled"]


def dataset(conn, tag: int, n: int = 3) -> str:
    return t.publish(conn, t.manifest(uid(1, tag), n=n, tag=tag))


def annotation(dataset_ref: str, sample: str, n: int = 1, *, method: str = "imported",
               ground_truth: bool = False, reviewer: str | None = None) -> dict:
    return {"schema": "lab.annotation.1", "provider_org_id": NEMO,
            "annotation_id": uid(n, 0xa1), "dataset_ref": dataset_ref, "sample_id": sample,
            "method": method, "method_version": "v1", "reviewer_id": reviewer,
            "rubric_ref": RUBRIC, "evidence_ref": W["source"], "label": {"value": "yes"},
            "ground_truth": ground_truth, "state": "submitted"}


def label(key: str, dataset_ref: str, annotation_ref: str, sample: str) -> dict:
    return {"key": key, "kind": "label", "dataset_ref": dataset_ref, "sample_id": sample,
            "annotation_ref": annotation_ref, "actor": DEV}


def append(conn, event: dict, provider: str = NEMO):
    return ok(conn, "lab_label_append", {"provider_org_id": provider, "event": event})


def external(conn, tag: int) -> tuple[str, str]:
    """A published external run record of NEMO: (external_run_id, ref)."""
    run_id = uid(9, tag)
    return run_id, t.publish(conn, t.external_run(run_id, dataset(conn, tag, 1)))


def other_external(conn, tag: int) -> str:
    """OTHER's own published external run record (its dataset on BOTH's grant)."""
    ds = t.publish(conn, t.manifest(uid(1, tag), n=1, tag=tag, provider=OTHER,
                                    source=W["other_source"], grant=W["other_grant"]), OTHER)
    doc = {**t.external_run(uid(9, tag), ds), "provider_org_id": OTHER}
    doc["budget"] = {**doc["budget"], "payer_ref": OTHER_PAYER}
    return t.publish(conn, doc, OTHER)


def full_subscription(conn, sub: str, run_ref: str) -> dict:
    """B3's whole `Subscription` (the adapter validates it)."""
    ds = dataset(conn, 0x8e, 1)
    harness = t.publish(conn, t.harness(uid(3, 0x8e)))
    credit = {"unit": "CREDIT", "value": "10.00000000"}
    return {**subscription(sub, run_ref), "dataset_ref": ds, "harness_ref": harness,
            "evaluator_ref": t.EVALUATOR, "evaluator": t.EVALUATOR_SPEC, "seed": 7,
            "max_cases": 10, "run_limit": credit, "limit": credit}


def create(run_id: str, ref: str, **over) -> dict:
    fields = {"run_ref": ref, "connector": "manual-bundle", "payer_ref": PAYER,
              "limit": "40.00000000", **over}
    return {"provider_org_id": NEMO, "external_run_id": run_id, "expected": None,
            "target": "prepared", "fields": fields}


def move(run_id: str, expected: str, target: str, **fields) -> dict:
    return {"provider_org_id": NEMO, "external_run_id": run_id, "expected": expected,
            "target": target, "fields": fields}


def reserve(key: str, limit: str = "25.00000000", payer: str = PAYER) -> dict:
    return {"provider_org_id": NEMO, "key": key, "payer_ref": payer, "limit": limit}


def settle(key: str, cost: str | None) -> dict:
    return {"provider_org_id": NEMO, "key": key, "cost": cost}


def teacher(run: str, dataset_ref: str, samples: list[str], cost: str = "30.00000000") -> dict:
    return {"run_id": run, "provider_org_id": NEMO, "payer_ref": PAYER,
            "dataset_ref": dataset_ref, "sample_ids": samples,
            "price_version": "teacher-rates-2026-09", "max_cost": cost}


def event(checkpoint: str, run_ref: str, step: int = 1, provider: str = NEMO,
          digest: str = "sha256:" + "a" * 64) -> dict:
    return {"schema": "infrx.checkpoint_event.1", "provider_org_id": provider,
            "key_id": "key-1", "checkpoint_id": checkpoint, "external_run_ref": run_ref,
            "step": step, "artifact": {"uri": f"mem://{checkpoint}", "digest": digest},
            "issued_at": "2026-09-28T10:00:00Z"}


def subscription(sub: str, run_ref: str, provider: str = NEMO, owner: str = DEV) -> dict:
    return {"subscription_id": sub, "provider_org_id": provider, "owner_user_id": owner,
            "external_run_ref": run_ref, "policy": "latest_only", "max_active": 1}


def revoke(conn) -> None:
    ok(conn, "lab_revoke_access_grant", {"actor_user_id": C1, "grantor_org_id": l2.org(conn, C1),
                                         "recipient_provider_org_id": NEMO})


def teacher_grant(conn) -> dict:
    return ok(conn, "lab_put_access_grant", l2.scope(
        conn, purposes=["provider_sharing", "training", "external_judging"]))


def seed(conn) -> None:
    t.seed(conn)
    conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, reason) values "
                 "('lab_submission', true, 'd8-test', 'test')")
    for provider, payer in ((NEMO, PAYER), (OTHER, OTHER_PAYER)):
        ok(conn, "lab_put_budget", {"provider_org_id": provider, "payer_ref": payer,
                                    "limit": "100.00000000", "actor": "ops", "reason": "q4"})
    teacher_grant(conn)


# ----------------------------------------------------------------------------- checks
@rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS: no browser session reads a D8 table or executes a D8 RPC; the platform role
    reads them and writes only through the RPCs; row security is on everywhere."""
    probes = [f"select count(*) from infrx.{name}" for name in TABLES]
    probes += [f"select infrx.{name}('{{}}'::jsonb)" for name in RPCS]
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached D8 state: {reached}"
    unread = [n for n in TABLES if cc.refused_as(conn, "service", probes[TABLES.index(n)])]
    assert not unread, f"the platform role cannot read {unread}"
    written = [n for n in TABLES if not (cc.refused_as(conn, "service", f"delete from infrx.{n}")
                                         or "").startswith("42501")]
    assert not written, f"the platform role writes {written} around the RPCs"
    unguarded = [n for n in TABLES if not conn.execute(
        "select relrowsecurity from pg_class where oid = %s::regclass", (f"infrx.{n}",)
    ).fetchone()[0]]
    assert not unguarded, f"row security is off on {unguarded}"
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused; service reads"


@rolled_back
def check_labels_are_append_only_one_per_key(conn) -> str:
    """SR-P1-1 / PIPELINE-LINEAGE: one event per (provider, key) for ever - the same body
    replays the stored event, another is `idempotency_conflict`; events come back in append
    order under their dataset; a label names the provider's annotation of THAT dataset; an
    unknown dataset or another provider's is `not_found`; nothing is edited."""
    ds, other_ds = dataset(conn, 0x81), dataset(conn, 0x82)
    s1 = uid(1, 0x81)
    a1 = t.publish(conn, annotation(ds, s1))
    first = label("label:1", ds, a1, s1)
    assert append(conn, first) == first
    assert append(conn, first) == first, "a replay is the stored event"
    assert refusal(conn, "lab_label_append", {"provider_org_id": NEMO, "event": {
        **first, "actor": "someone-else"}}) == "idempotency_conflict"
    review = {"key": "review:1", "kind": "review", "dataset_ref": ds, "sample_id": s1,
              "annotation_ref": a1, "decision": "accepted", "actor": DEV}
    append(conn, review)
    assert ok(conn, "lab_label_events", {"provider_org_id": NEMO, "dataset_ref": ds}) == \
        [first, review]
    assert ok(conn, "lab_label_events", {"provider_org_id": OTHER, "dataset_ref": ds}) == []
    missing = {"foreign_annotation": label("label:2", other_ds, a1, s1),
               "no_annotation": {**label("label:3", ds, a1, s1), "annotation_ref": None},
               "unknown_dataset": label("label:4", f"lab:dataset:{NEMO}:{uid(99, 0x81)}"
                                        f"@sha256:{'0' * 64}", a1, s1)}
    got = {k: refusal(conn, "lab_label_append", {"provider_org_id": NEMO, "event": v})
           for k, v in missing.items()}
    assert got == dict.fromkeys(missing, "not_found"), got
    assert refusal(conn, "lab_label_append", {"provider_org_id": OTHER, "event": {
        **first, "key": "label:x"}}) == "not_found", "another provider's dataset"
    assign = {"key": "assign:1", "kind": "assign", "dataset_ref": ds, "sample_id": s1,
              "reviewer_id": DEV, "rubric_ref": RUBRIC, "actor": DEV}
    assert refusal(conn, "lab_label_append", {"provider_org_id": OTHER, "event": assign}) == \
        "not_found", "an assignment under another provider's dataset"
    assert refusal(conn, "lab_label_append", {"provider_org_id": NEMO, "event": {
        **assign, "dataset_ref": f"lab:dataset:{NEMO}:{uid(98, 0x81)}@sha256:{'0' * 64}"}}) == \
        "not_found", "an assignment under no dataset"
    bad = {"kind": {**first, "key": "k5", "kind": "vote"}, "key": {**first, "key": 5},
           "not_object": "label"}
    got = {k: refusal(conn, "lab_label_append", {"provider_org_id": NEMO, "event": v})
           for k, v in bad.items()}
    assert got == dict.fromkeys(bad, "invalid_request"), got
    edited = cc.attempt(conn, "update infrx.lab_label_events set body = body || '{\"x\": 1}'")
    assert edited is not None, "a label event was edited"
    return "one event per key; append order; dataset-bound labels; immutable"


@rolled_back
def check_ground_truth_and_synthetic_stay_distinct(conn) -> str:
    """D8: an annotation record claiming ground truth from a synthetic (teacher) label, or a
    human label with no reviewer, is refused at publication; a synthetic label that is not
    ground truth and a reviewed human ground-truth label are published."""
    ds, s1 = dataset(conn, 0x83), uid(1, 0x83)
    forged = {"synthetic_truth": annotation(ds, s1, 1, method="synthetic", ground_truth=True),
              "unreviewed_human": annotation(ds, s1, 2, method="human")}
    got = {k: t.refused_publish(conn, v) for k, v in forged.items()}
    assert got == dict.fromkeys(forged, "invalid_request"), got
    t.publish(conn, annotation(ds, s1, 3, method="synthetic"))
    t.publish(conn, annotation(ds, s1, 4, method="human", ground_truth=True, reviewer=DEV))
    return "synthetic never ground truth; human names its reviewer"


@rolled_back
def check_an_external_run_is_its_record_and_moves_by_cas(conn) -> str:
    """SR-P3-1 / PIPELINE-LINEAGE: a run is created `prepared` from the provider's own
    published record, with the record's connector, payer and limit; an identical create
    replays, a different one is `state_conflict`; every move is a CAS on the expected state
    along the external_run machine (a stale or undeclared move is `state_conflict`) that
    merges its fields but never the record's own; every move is kept."""
    run_id, ref = external(conn, 0x84)
    created = ok(conn, "lab_external_run_move", create(run_id, ref))
    assert created == {"state": "prepared", **create(run_id, ref)["fields"]}, created
    assert ok(conn, "lab_external_run_move", create(run_id, ref)) == created, "replay"
    assert refusal(conn, "lab_external_run_move", create(run_id, ref, connector="http")) == \
        "invalid_request", "a row unlike its record"
    other_id, other_ref = external(conn, 0x85)
    assert refusal(conn, "lab_external_run_move", {**create(other_id, other_ref),
                                                   "provider_org_id": OTHER}) == "not_found"
    assert refusal(conn, "lab_external_run_move", create(other_id, ref)) == "not_found", \
        "a record named under another run id"
    assert refusal(conn, "lab_external_run_move", move(run_id, "submitting", "submitted")) == \
        "state_conflict", "stale expected"
    assert refusal(conn, "lab_external_run_move", move(run_id, "prepared", "completed")) == \
        "state_conflict", "undeclared move"
    assert refusal(conn, "lab_external_run_move", move(run_id, "prepared", "submitting",
                                                       limit="1.00000000")) == "invalid_request"
    ok(conn, "lab_external_run_move", move(run_id, "prepared", "submitting"))
    assert refusal(conn, "lab_external_run_move", create(run_id, ref)) == "state_conflict", \
        "a re-create after it moved on"
    ok(conn, "lab_external_run_move", move(run_id, "submitting", "ambiguous"))
    ok(conn, "lab_external_run_move", move(run_id, "ambiguous", "submitted", job_id="job-1"))
    assert refusal(conn, "lab_external_run_move", move(run_id, "ambiguous", "submitted",
                                                       job_id="job-2")) == "state_conflict"
    done = ok(conn, "lab_external_run_move", move(run_id, "submitted", "completed",
                                                  cost="10.00000000"))
    assert (done["state"], done.get("job_id"), done.get("cost")) == \
        ("completed", "job-1", "10.00000000"), done
    assert ok(conn, "lab_external_run_get", {"provider_org_id": NEMO,
                                             "external_run_id": run_id}) == done
    assert ok(conn, "lab_external_run_get", {"provider_org_id": OTHER,
                                             "external_run_id": run_id}) is None
    moves = conn.execute("select from_state, to_state from infrx.lab_external_run_events "
                         "where external_run_id = %s order by event_id", (run_id,)).fetchall()
    assert moves == [(None, "prepared"), ("prepared", "submitting"), ("submitting", "ambiguous"),
                     ("ambiguous", "submitted"), ("submitted", "completed")], moves
    return "created from its record; CAS moves; history kept"


@rolled_back
def check_one_reservation_per_key_settled_once(conn) -> str:
    """PIPELINE-BUDGET (USD reused from D6J): one PROVIDER_USD hold per submit key on the
    payer's budget (a replay is the row, another payer or limit `idempotency_conflict`);
    settled once at the reported cost (a resumed poll gets the same row and moves nothing;
    another cost is `idempotency_conflict`), or unknown - then the whole hold counts as
    spent; a cost above the hold, a hold past the cap or a payer with no budget is
    `budget_exceeded`; release returns the hold and never follows a settlement (nor a
    settlement a release); with the flag off nothing new is reserved, what is held still
    settles."""
    held = ok(conn, "lab_run_reserve", reserve("submit:a"))
    assert held == {"payer_ref": PAYER, "limit": "25.00000000", "state": "held", "cost": None}
    assert budget(conn) == ("25.00000000", "0.00000000")
    assert ok(conn, "lab_run_reserve", reserve("submit:a")) == held, "replay"
    assert budget(conn) == ("25.00000000", "0.00000000"), "a replay reserved twice"
    assert refusal(conn, "lab_run_reserve", reserve("submit:a", "30.00000000")) == \
        "idempotency_conflict"
    done = ok(conn, "lab_run_settle", settle("submit:a", "10.00000000"))
    assert (done["state"], done["cost"]) == ("settled", "10.00000000"), done
    assert budget(conn) == ("0.00000000", "10.00000000")
    assert ok(conn, "lab_run_settle", settle("submit:a", "10.00000000")) == done, "resumed"
    assert budget(conn) == ("0.00000000", "10.00000000"), "a resumed poll settled twice"
    assert refusal(conn, "lab_run_settle", settle("submit:a", "11.00000000")) == \
        "idempotency_conflict"
    assert refusal(conn, "lab_run_release", {"provider_org_id": NEMO, "key": "submit:a"}) == \
        "state_conflict"
    ok(conn, "lab_run_reserve", reserve("submit:b"))
    released = ok(conn, "lab_run_release", {"provider_org_id": NEMO, "key": "submit:b"})
    assert released["state"] == "released" and budget(conn) == ("0.00000000", "10.00000000")
    assert refusal(conn, "lab_run_settle", settle("submit:b", "1.00000000")) == "state_conflict"
    ok(conn, "lab_run_reserve", reserve("submit:c", "20.00000000"))
    assert refusal(conn, "lab_run_settle", settle("submit:c", "20.00000001")) == \
        "budget_exceeded", "a cost above the hold"
    unknown = ok(conn, "lab_run_settle", settle("submit:c", None))
    assert (unknown["state"], unknown["cost"]) == ("settled", None), unknown
    assert budget(conn) == ("0.00000000", "30.00000000"), "unknown spent the whole hold"
    assert refusal(conn, "lab_run_reserve", reserve("submit:d", "70.00000001")) == \
        "budget_exceeded"
    assert refusal(conn, "lab_run_reserve", reserve("submit:e", payer=OTHER_PAYER)) == \
        "budget_exceeded", "another provider's payer"
    assert refusal(conn, "lab_run_settle", settle("submit:none", "1.00000000")) == "not_found"
    ok(conn, "lab_run_reserve", reserve("submit:f", "5.00000000"))
    conn.execute("update infrx.feature_flags set enabled = false where name = 'lab_submission'")
    assert refusal(conn, "lab_run_reserve", reserve("submit:g")) == "dependency_unavailable"
    assert ok(conn, "lab_run_settle", settle("submit:f", "5.00000000"))["state"] == "settled"
    return "one hold per key; settled once (resumed poll = same row); unknown = whole hold"


@rolled_back
def check_an_ambiguous_run_fails_only_on_the_providers_confirmation(conn) -> str:
    """R184: an ambiguous run is never released by the platform - its hold stays while the
    run is ambiguous; only an operator's move backed by the provider's written confirmation
    fails it, and that move releases the hold with it (both kept in the run's history); a
    lookup may still settle it as submitted, and a rejected submit still fails."""
    run_id, ref = external(conn, 0x86)
    key = records.submit_key(run_id)
    operator = uid(5, 0x86)
    conn.execute("insert into auth.users (id, email) values (%s, 'ops-d8@example.com')",
                 (operator,))
    conn.execute("update public.profiles set is_operator = true where id = %s", (operator,))
    ok(conn, "lab_external_run_move", create(run_id, ref))
    ok(conn, "lab_run_reserve", reserve(key))
    ok(conn, "lab_external_run_move", move(run_id, "prepared", "submitting"))
    ok(conn, "lab_external_run_move", move(run_id, "submitting", "ambiguous"))
    assert refusal(conn, "lab_run_release", {"provider_org_id": NEMO, "key": key}) == \
        "state_conflict", "the platform released an ambiguous run's hold"
    assert budget(conn) == ("25.00000000", "0.00000000")
    confirmed = {"operator": operator, "confirmation_ref": "ticket:4411 key never received"}
    assert refusal(conn, "lab_external_run_move", move(run_id, "ambiguous", "failed")) == \
        "forbidden", "a worker failed an ambiguous run"
    assert refusal(conn, "lab_external_run_move", move(run_id, "ambiguous", "failed", **{
        **confirmed, "operator": DEV})) == "forbidden", "a non-operator failed it"
    assert refusal(conn, "lab_external_run_move", move(run_id, "ambiguous", "failed",
                                                       operator=operator)) == \
        "invalid_request", "an operator failed it with no confirmation"
    failed = ok(conn, "lab_external_run_move", move(run_id, "ambiguous", "failed", **confirmed))
    assert failed["state"] == "failed", failed
    assert budget(conn) == ("0.00000000", "0.00000000"), "the confirmed failure kept the hold"
    assert ok(conn, "lab_run_release", {"provider_org_id": NEMO, "key": key})["state"] == \
        "released"
    last = conn.execute("select from_state, fields from infrx.lab_external_run_events where "
                        "external_run_id = %s order by event_id desc limit 1",
                        (run_id,)).fetchone()
    assert last == ("ambiguous", confirmed), last
    other, other_ref = external(conn, 0x8f)
    ok(conn, "lab_external_run_move", create(other, other_ref))
    ok(conn, "lab_run_reserve", reserve(records.submit_key(other)))
    ok(conn, "lab_external_run_move", move(other, "prepared", "submitting"))
    assert ok(conn, "lab_run_release", {"provider_org_id": NEMO,
                                        "key": records.submit_key(other)})["state"] == \
        "released", "a rejected submit's hold"
    assert ok(conn, "lab_external_run_move", move(other, "submitting", "failed",
                                                  reason="422"))["state"] == "failed"
    return "ambiguous holds; operator + written confirmation fails and releases"


@rolled_back
def check_notes_are_write_once(conn) -> str:
    """SR-P3-1: a note is one body per (provider, key) for ever - a replay is the body,
    another body `idempotency_conflict`; `noted` is provider-scoped and None when absent."""
    body = {"checkpoint_id": uid(1, 0x86), "state": "queued"}
    args = {"provider_org_id": NEMO, "key": "checkpoint:1", "body": body}
    assert ok(conn, "lab_pipeline_note", args) == body
    assert ok(conn, "lab_pipeline_note", args) == body
    assert refusal(conn, "lab_pipeline_note", {**args, "body": {"state": "rejected"}}) == \
        "idempotency_conflict"
    assert ok(conn, "lab_pipeline_noted", {"provider_org_id": NEMO, "key": "checkpoint:1"}) == \
        body
    assert ok(conn, "lab_pipeline_noted", {"provider_org_id": OTHER,
                                           "key": "checkpoint:1"}) is None
    return "one note per key; provider-scoped read"


@rolled_back
def check_a_teacher_run_is_consented_per_sample(conn) -> str:
    """SR-P2-1 (amended) / JUDGE-BUDGET: a teacher run reserves only the dataset's own samples
    while each sample's grant is in force for external_judging AND training, snapshotting
    each grant's current version; its hold counts on the payer's budget (quarantined too);
    record_sent re-checks every snapshot unchanged (a re-grant or a revocation since is
    `consent_missing`), and 0036's judge-only record_sent refuses a teacher run."""
    ds = dataset(conn, 0x87, n=3)
    ids = [uid(i, 0x87) for i in (1, 2, 3)]
    run = ok(conn, "lab_teacher_reserve", teacher(uid(1, 0x70), ds, ids[:2]))
    assert (run["purpose"], run["dataset_ref"], run["grant_id"], run["state"]) == \
        ("teacher_annotation", ds, None, "prepared"), run
    assert ok(conn, "lab_teacher_reserve", teacher(uid(1, 0x70), ds, ids[:2])) == run
    assert budget(conn) == ("30.00000000", "0.00000000")
    snap = conn.execute("select grant_id::text, grant_version from infrx.lab_judge_run_consents "
                        "where run_id = %s", (uid(1, 0x70),)).fetchall()
    assert snap == [(W["grant_id"], 2)], snap
    assert refusal(conn, "lab_teacher_reserve", teacher(uid(2, 0x70), ds, [uid(9, 0x87)])) == \
        "invalid_request", "a sample of another dataset"
    assert refusal(conn, "lab_teacher_reserve", {**teacher(uid(3, 0x70), ds, ids),
                                                 "provider_org_id": OTHER}) == "not_found"
    assert refusal(conn, "lab_teacher_reserve", {**teacher(uid(1, 0x70), ds, ids[:2]),
                                                 "provider_org_id": OTHER}) == "not_found", \
        "another provider replayed a teacher run"
    ok(conn, "lab_judge_begin_submit", {"run_id": uid(1, 0x70)})
    assert refusal(conn, "lab_judge_record_sent", {"run_id": uid(1, 0x70),
                                                   "sample_ids": ids[:2]}) == "consent_missing"
    with conn.transaction(force_rollback=True):
        teacher_grant(conn)
        assert refusal(conn, "lab_teacher_record_sent", {"run_id": uid(1, 0x70),
                                                         "sample_ids": ids[:2]}) == \
            "consent_missing", "a re-grant since the snapshot"
    with conn.transaction(force_rollback=True):
        ok(conn, "lab_put_access_grant", l2.scope(
            conn, purposes=["provider_sharing", "training", "external_judging"],
            expires_at=conn.execute("select infrx.now() + interval '60 seconds'"
                                    ).fetchone()[0].isoformat()))
        ok(conn, "lab_teacher_reserve", teacher(uid(7, 0x70), ds, ids[2:], "1.00000000"))
        ok(conn, "lab_judge_begin_submit", {"run_id": uid(7, 0x70)})
        t.advance(conn, 61)
        assert refusal(conn, "lab_teacher_record_sent", {"run_id": uid(7, 0x70),
                                                         "sample_ids": ids[2:]}) == \
            "consent_missing", "a grant expired since the reservation"
    sent = ok(conn, "lab_teacher_record_sent", {"run_id": uid(1, 0x70), "sample_ids": ids[:1]})
    assert sent["sent_sample_ids"] == ids[:1], sent
    ok(conn, "lab_judge_quarantine", {"run_id": uid(1, 0x70), "reason": "timeout"})
    assert budget(conn) == ("30.00000000", "0.00000000"), "a quarantined hold stopped counting"
    ok(conn, "lab_teacher_reserve", teacher(uid(4, 0x70), ds, ids[2:], "20.00000000"))
    ok(conn, "lab_judge_begin_submit", {"run_id": uid(4, 0x70)})
    ok(conn, "lab_put_access_grant", l2.scope(conn, purposes=["provider_sharing",
                                                              "external_judging"]))
    assert refusal(conn, "lab_teacher_record_sent", {"run_id": uid(4, 0x70),
                                                     "sample_ids": ids[2:]}) == \
        "consent_missing", "training dropped since the reservation"
    assert refusal(conn, "lab_teacher_reserve", teacher(uid(5, 0x70), ds, ids[2:])) == \
        "consent_missing", "a sample not granted for training"
    revoke(conn)
    assert refusal(conn, "lab_teacher_reserve", teacher(uid(6, 0x70), ds, ids[2:])) == \
        "consent_missing", "a revoked grant"
    return "per-sample consent snapshot; holds count; record_sent rechecks"


@rolled_back
def check_teacher_failures_are_an_append_only_log(conn) -> str:
    """SR-P2-1: every item a teacher batch could not import is logged once per (run, sample,
    reason) - a repeated collect adds nothing - only for a teacher run."""
    ds = dataset(conn, 0x88, n=2)
    ok(conn, "lab_teacher_reserve", teacher(uid(1, 0x71), ds, [uid(1, 0x88)]))
    fails = {"run_id": uid(1, 0x71), "failures": [
        {"sample_id": uid(1, 0x88), "reason": "malformed_label"},
        {"sample_id": uid(7, 0x88), "reason": "not_sent"}]}
    assert ok(conn, "lab_teacher_record_failures", fails) == {"inserted": 2}
    assert ok(conn, "lab_teacher_record_failures", fails) == {"inserted": 0}, "logged twice"
    got = [(f["sample_id"], f["reason"])
           for f in ok(conn, "lab_teacher_failures", {"run_id": uid(1, 0x71)})]
    assert sorted(got) == sorted([(uid(1, 0x88), "malformed_label"),
                                  (uid(7, 0x88), "not_sent")]), got
    assert refusal(conn, "lab_teacher_record_failures", {**fails, "run_id": uid(2, 0x71)}) == \
        "not_found"
    g = ok(conn, "lab_put_access_grant", l2.scope(
        conn, purposes=["external_judging"], categories=["request_content", "response_content"]))
    ok(conn, "lab_judge_reserve", {"run_id": uid(3, 0x71), "provider_org_id": NEMO,
                                   "payer_ref": PAYER, "grant_id": g["grant_id"],
                                   "grant_version": g["version"], "sample_ids": [uid(1, 0x88)],
                                   "media_ids": [], "price_version": "p",
                                   "max_cost": "1.00000000"})
    assert refusal(conn, "lab_teacher_record_failures", {**fails, "run_id": uid(3, 0x71)}) == \
        "not_found", "a judge run's failure log"
    assert refusal(conn, "lab_teacher_record_failures", {"run_id": uid(1, 0x71), "failures": [
        {"sample_id": uid(1, 0x88), "reason": "Bad Reason"}]}) == "invalid_request"
    return "failures once per (run, sample, reason)"


@rolled_back
def check_checkpoint_events_are_signed_once_per_provider(conn) -> str:
    """WR-B3-1 / CHECKPOINT-IDEM: an event is kept once per (provider, checkpoint id) with
    its external run the provider's own record; the same body replays, another body is
    `idempotency_conflict`; another provider's same id is its own; the first rejection
    reason stands; the run's events come back by step with whether each was rejected."""
    _, run_ref = external(conn, 0x89)
    other_run = other_external(conn, 0x8a)
    c1, c2 = uid(1, 0xce), uid(2, 0xce)
    e1 = event(c1, run_ref, step=5)
    assert ok(conn, "lab_checkpoint_record_event", {"event": e1}) == e1
    assert ok(conn, "lab_checkpoint_record_event", {"event": e1}) == e1
    assert refusal(conn, "lab_checkpoint_record_event", {"event": {**e1, "step": 6}}) == \
        "idempotency_conflict"
    ok(conn, "lab_checkpoint_record_event", {"event": event(c2, run_ref, step=3)})
    ok(conn, "lab_checkpoint_record_event", {"event": event(c2, other_run, step=1,
                                                            provider=OTHER)})
    assert refusal(conn, "lab_checkpoint_record_event", {"event": event(
        uid(3, 0xce), run_ref, provider=OTHER)}) == "not_found", "another provider's run"
    assert refusal(conn, "lab_checkpoint_event", {"provider_org_id": OTHER,
                                                  "checkpoint_id": c1}) == "not_found"
    assert refusal(conn, "lab_checkpoint_record_event", {"event": {
        **event(uid(4, 0xce), run_ref), "artifact": {"uri": "x", "digest": "md5:1"}}}) == \
        "invalid_request"
    ok(conn, "lab_checkpoint_reject", {"provider_org_id": NEMO, "checkpoint_id": c2,
                                       "reason": "not safetensors"})
    again = ok(conn, "lab_checkpoint_reject", {"provider_org_id": NEMO, "checkpoint_id": c2,
                                               "reason": "digest mismatch"})
    assert again == {"reason": "not safetensors"}, again
    got = [(r["event"]["checkpoint_id"], r["event"]["step"], r["rejected"])
           for r in ok(conn, "lab_checkpoint_events", {"provider_org_id": NEMO,
                                                        "external_run_ref": run_ref})]
    assert got == [(c2, 3, True), (c1, 5, False)], got
    assert refusal(conn, "lab_checkpoint_reject", {"provider_org_id": OTHER,
                                                   "checkpoint_id": c1,
                                                   "reason": "x"}) == "not_found"
    return "once per provider and id; first rejection stands; by step"


@rolled_back
def check_subscriptions_and_decisions_are_first_write(conn) -> str:
    """WR-B3-1: a subscription is its provider's, on its own external run, owned by a user;
    the first write stands; a decision per (subscription, checkpoint) is written once -
    queued with a run id or skipped with a reason - and a redelivery gets the first."""
    _, run_ref = external(conn, 0x8b)
    sub = subscription(uid(1, 0x5b), run_ref)
    assert ok(conn, "lab_checkpoint_subscribe", {"subscription": sub}) == sub
    assert ok(conn, "lab_checkpoint_subscribe", {"subscription": {**sub, "max_active": 9}}) == \
        sub, "the first write stands"
    assert refusal(conn, "lab_checkpoint_subscribe", {"subscription": subscription(
        uid(2, 0x5b), run_ref, provider=OTHER)}) == "not_found"
    assert ok(conn, "lab_checkpoint_subscriptions", {"provider_org_id": NEMO,
                                                     "external_run_ref": run_ref}) == [sub]
    assert ok(conn, "lab_checkpoint_subscriptions", {"provider_org_id": OTHER,
                                                     "external_run_ref": run_ref}) == []
    decide = {"subscription_id": uid(1, 0x5b), "checkpoint_id": uid(1, 0xcf),
              "state": "queued", "reason": None, "run_id": uid(1, 0xb1)}
    first = ok(conn, "lab_checkpoint_decide", decide)
    assert first == {"state": "queued", "reason": None, "run_id": uid(1, 0xb1)}, first
    assert ok(conn, "lab_checkpoint_decide", {**decide, "state": "skipped",
                                              "reason": "budget", "run_id": None}) == first
    assert refusal(conn, "lab_checkpoint_decide", {**decide, "checkpoint_id": uid(2, 0xcf),
                                                   "run_id": None}) == "invalid_request"
    assert refusal(conn, "lab_checkpoint_decide", {**decide, "subscription_id": uid(9, 0x5b)}) \
        == "not_found"
    assert ok(conn, "lab_checkpoint_decisions", {"subscription_id": uid(1, 0x5b)}) == \
        {uid(1, 0xcf): first}
    return "first subscription and decision stand; provider-scoped listing"


def check_racing_reservations_hold_once(conn) -> str:
    """PIPELINE-BUDGET race (commits): two workers reserve one submit key at once - one row,
    the budget held once, both get the same answer."""
    gate, answers = threading.Barrier(2), [None, None]
    with conn.transaction():
        before = budget(conn)

    def one(i: int) -> None:
        with psycopg.connect(pgharness.dsn(conn.info.dbname), autocommit=True) as mine:
            gate.wait()
            try:
                answers[i] = call(mine, "lab_run_reserve", reserve("submit:race"))["state"]
            except psycopg.Error as failed:
                answers[i] = str(failed).split(":")[0]
    threads = [threading.Thread(target=one, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert answers == ["held", "held"], answers
    after = budget(conn)
    assert float(after[0]) - float(before[0]) == 25.0, (before, after)
    with conn.transaction():
        ok(conn, "lab_run_release", {"provider_org_id": NEMO, "key": "submit:race"})
    return f"racing reserves: {answers}, held once"


def check_the_adapters_compose(conn) -> str:
    """The four adapters are their lanes' ports: typed rows and typed refusals."""
    event_type, subscription_type = b3_models()
    dsn = connector(pgharness.dsn(conn.info.dbname))
    log, runs, teachers, cps = (PgLabelLog(dsn), PgRunLedger(dsn), PgTeacherLedger(dsn),
                                PgCheckpointLedger(dsn))
    with conn.transaction():
        ds = dataset(conn, 0x8c)
        a1 = t.publish(conn, annotation(ds, uid(1, 0x8c), 7))
        run_id, run_ref = external(conn, 0x8d)
        full = full_subscription(conn, uid(3, 0x5b), run_ref)

    async def refused(coro) -> str:
        try:
            await coro
        except errors.DomainError as failed:
            return type(failed).__name__
        return "answered"

    async def go() -> list:
        got = []
        ev = label("label:adapter", ds, a1, uid(1, 0x8c))
        got.append(await log.append(ev, provider_org_id=NEMO) == ev)
        got.append(await refused(log.append({**ev, "actor": "x"}, provider_org_id=NEMO)))
        got.append(len(await log.events(ds, provider_org_id=NEMO)))
        row = await runs.move(run_id, provider_org_id=NEMO, expected=None, target="prepared",
                              run_ref=run_ref, connector="manual-bundle", payer_ref=PAYER,
                              limit="40.00000000")
        got.append(row["state"])
        got.append(await refused(runs.move(run_id, provider_org_id=NEMO, expected="submitted",
                                           target="completed")))
        got.append((await runs.reserve("submit:adapter", provider_org_id=NEMO, payer_ref=PAYER,
                                       limit="1.00000000"))["state"])
        got.append((await runs.settle("submit:adapter", provider_org_id=NEMO,
                                      cost=None))["state"])
        got.append(await refused(runs.release("submit:adapter", provider_org_id=NEMO)))
        got.append(await runs.note("n", {"a": 1}, provider_org_id=NEMO))
        got.append(await runs.noted("m", provider_org_id=NEMO))
        tr = await teachers.reserve(
            run_id=uid(1, 0x72), provider_org_id=NEMO, payer_ref=PAYER,
            consent=Consent(ds, 1), sample_ids=[uid(1, 0x8c)], media_ids=frozenset(),
            price_version="p", max_cost=ProviderUsd("2.00000000"))
        got.append((tr.consent, tr.state))
        await teachers.begin_submit(tr.run_id)
        got.append((await teachers.record_sent(tr.run_id, [uid(1, 0x8c)])).sent_ids)
        got.append(await teachers.record_failures(tr.run_id, [(uid(1, 0x8c), "duplicate")]))
        got.append(await teachers.failures(tr.run_id))
        e = event_type.model_validate(event(uid(5, 0xce), run_ref, step=2))
        got.append(await cps.record_event(e) == e)
        got.append(await cps.event(uid(5, 0xce), provider_org_id=NEMO) == e)
        await cps.reject(uid(5, 0xce), "junk", provider_org_id=NEMO)
        got.append([r for _, r in await cps.events(run_ref, provider_org_id=NEMO)])
        got.append(await refused(cps.event(uid(5, 0xce), provider_org_id=OTHER)))
        s = subscription_type.model_validate(full)
        got.append(await cps.add_subscription(s) == s)
        got.append(await cps.subscriptions(run_ref, provider_org_id=NEMO) == [s])
        d = await cps.decide(uid(3, 0x5b), uid(5, 0xce), state="skipped", reason="rejected",
                             run_id=None)
        got.append(await cps.decisions(uid(3, 0x5b)) == {uid(5, 0xce): d})
        got.append(type(await cps.db_now()).__name__)
        return got
    try:
        got = asyncio.run(go())
    except Exception as failed:         # R40: a broken answer is this check's assertion
        raise AssertionError(f"{type(failed).__name__}: {failed}") from None
    assert got == [True, "IdempotencyConflict", 1, "prepared", "StateConflict", "held",
                   "settled", "StateConflict", {"a": 1}, None,
                   (Consent(ds, 1), "prepared"), (uid(1, 0x8c),), 1,
                   [(uid(1, 0x8c), "duplicate")], True, True, [True], "NotFound", True, True,
                   True, "datetime"], got
    return f"adapters: {len(got)} answers"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing,
    check_labels_are_append_only_one_per_key,
    check_ground_truth_and_synthetic_stay_distinct,
    check_an_external_run_is_its_record_and_moves_by_cas,
    check_one_reservation_per_key_settled_once,
    check_an_ambiguous_run_fails_only_on_the_providers_confirmation,
    check_notes_are_write_once,
    check_a_teacher_run_is_consented_per_sample,
    check_teacher_failures_are_an_append_only_log,
    check_checkpoint_events_are_signed_once_per_provider,
    check_subscriptions_and_decisions_are_first_write,
    check_racing_reservations_hold_once,
    check_the_adapters_compose)}


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_d8_ledgers(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")

