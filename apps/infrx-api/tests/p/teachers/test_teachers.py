#!/usr/bin/env python3
"""P2 PIPELINE-BUDGET / PIPELINE-LINEAGE: bounded teacher annotation batches.

A batch is a manifest over one N2 dataset version, chunked into D6J runs paid in PROVIDER_USD by
the provider's named payer; each chunk leaves through J2's one egress path (`judge.submit.send`)
and comes back through J2's reconcile; labels reconcile into D8 through P1's `import_labels` as
`model` (synthetic) rows. The ledger and provider are J2's fakes, the dataset store and P1's
import are `fakes.py`, the local teacher fake is J2's HTTP fake on p2's port (57529).

    uv run --frozen pytest -q tests/p/teachers
"""
from __future__ import annotations

import asyncio
import dataclasses
import uuid

import pytest
from infrx.contracts import errors
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.tasklocal import local_services
from infrx.contracts.v2.money_units import ProviderUsd
from infrx.judge import StaticRateTable, TokenCeilings, worst_case
from infrx.judge.submit import HttpJudgeProvider, reconcile
from infrx.pipelines.teachers import (TeacherBatch, TeacherWiring, collect, parse_label, plan,
                                      run_batch)

from tests.j import fakes as j1
from tests.j.submit import fakes as j2
from tests.j.submit.judge_fake import JudgeFake
from tests.l.access.worlds import FakeWorld

from . import fakes
from .fakes import sid

LIVE = DEFAULTS.replace(judge_mode="live")
CEILINGS = TokenCeilings(input_tokens=1_000, output_tokens=1_000, reasoning_tokens=0)
TEACHER_PORT = local_services("p2")["teacher-fake"].host_port


def usd(samples: int) -> ProviderUsd:
    return ProviderUsd(worst_case(j1.TEST_RATE, CEILINGS, samples))


class Case:
    """Provider A's developer runs a teacher over a dataset of 5 train, 1 validation and 2 holdout
    samples; sample 5 may not leave for an external model. Chunks of 2: (1,2), (3,4), (5,6),
    and a live run skips sample 5."""

    def __init__(self, *, budget: ProviderUsd | None = None, mode: str = "ok", on_submit=None,
                 settings=LIVE, rates=j1.TEST_RATES, provider=None) -> None:
        self.w = FakeWorld()
        self.store = fakes.Store(self.w.A, {"train": [sid(n) for n in range(1, 6)],
                                            "validation": [sid(6)],
                                            "holdout": [sid(7), sid(8)]})
        self.store.revoke(sid(5), purposes=("external_judging",))
        self.objects = asyncio.run(fakes.objects_for(self.store))
        self.payer = j2.payer(self.w.A)
        budget = budget if budget is not None else usd(100)
        self.ledger = fakes.TeacherLedger({self.payer: budget})
        self.provider = provider or fakes.Provider(mode, on_submit)
        self.labels = fakes.Labels()
        self.wiring = TeacherWiring(members=self.w.store, ledger=self.ledger,
                                    provider=self.provider, store=self.store,
                                    objects=self.objects, labels=self.labels, log=object(),
                                    rates=rates, settings=settings,
                                    redact=fakes.redact)
        self.batch = TeacherBatch(
            batch_id=str(uuid.UUID(int=77, version=4)), provider_org_id=self.w.A,
            requested_by=self.w.DEV_A, dataset_ref=fakes.DATASET_REF, rubric_ref=fakes.RUBRIC_REF,
            teacher_model=j1.JUDGE_MODEL, prompt_version="teach-v1", payer_ref=self.payer,
            chunk_size=2, ceilings=CEILINGS)

    def run(self, **kw):
        return asyncio.run(run_batch(dataclasses.replace(self.batch, **kw),
                                     wiring=self.wiring))

    def refused(self, exc, **kw):
        with pytest.raises(exc):
            self.run(**kw)
        assert self.provider.calls == [] and self.ledger.runs == {}

    def collect(self, run_id):
        return asyncio.run(collect(self.batch, run_id, wiring=self.wiring))

    def run_ids(self):
        return [run_id for run_id, _ in asyncio.run(plan(self.batch, store=self.store,
                                                         rates=j1.TEST_RATES,
                                                         now=j2.T0)).chunks]


# --- P2.a: the manifest and its dry run ---------------------------------------------------------
def test_p2__a_dry_run_plans_and_prices_without_a_rate_or_egress():
    case = Case()
    priced = asyncio.run(plan(case.batch, store=case.store, rates=j1.TEST_RATES, now=j2.T0))
    assert [ids for _, ids in priced.chunks] == [(sid(1), sid(2)), (sid(3), sid(4)),
                                                 (sid(5), sid(6))]
    assert len({run_id for run_id, _ in priced.chunks}) == 3
    assert priced.omitted == ({"sample_id": sid(7), "reason": "holdout"},
                              {"sample_id": sid(8), "reason": "holdout"})
    assert priced.not_permitted == (sid(5),)
    assert priced.price_version == j1.TEST_RATE.price_version
    assert priced.worst_case == usd(2) + usd(2) + usd(2)
    unpriced = asyncio.run(plan(case.batch, store=case.store, rates=StaticRateTable(()),
                                now=j2.T0))
    assert unpriced.chunks == priced.chunks
    assert unpriced.worst_case is None and unpriced.price_version is None
    assert case.provider.calls == [] and case.ledger.runs == {}


def test_p2__only_a_live_member_with_its_own_payer_and_a_rate_runs_a_batch():
    Case(settings=DEFAULTS).refused(errors.BudgetExceeded)
    Case().refused(errors.Forbidden, requested_by=FakeWorld.VIEWER_A)
    Case().refused(errors.NotFound, requested_by=FakeWorld.DEV_B)          # not A's member
    Case().refused(errors.Forbidden, payer_ref=j2.payer(FakeWorld.B))
    Case(rates=StaticRateTable(())).refused(errors.BudgetExceeded)
    Case().refused(errors.InvalidRequest, chunk_size=201)             # J1's scan bound
    Case().refused(errors.InvalidRequest, chunk_size=0)


# --- P2.b: the J2 protocol, chunk by chunk ------------------------------------------------------
def test_p2__each_chunk_leaves_once_through_the_j2_path():
    case = Case()
    report = case.run()
    assert [run.state for run in report.runs] == ["submitted"] * 3 and report.stopped is None
    assert [[item["sample_id"] for item in items] for _, items in case.provider.calls] == [
        [sid(1), sid(2)], [sid(3), sid(4)], [sid(6)]]
    assert case.provider.calls[0][1][0] == {"sample_id": sid(1), "content": f"q [redacted] {sid(1)}",
                                            "prompt_version": "teach-v1"}
    assert [case.ledger.runs[r].reserved for r in case.run_ids()] == [usd(2)] * 3
    assert {run.payer_ref for run in case.ledger.runs.values()} == {case.payer}
    again = case.run()                                      # a resumed worker: nothing new leaves
    assert len(case.provider.calls) == 3 and [r.state for r in again.runs] == ["submitted"] * 3


def test_p2__the_budget_stops_the_batch_before_the_chunk_it_cannot_cover():
    case = Case(budget=usd(2) + usd(2))
    report = case.run()
    assert report.stopped == "budget" and report.unsent == tuple(case.run_ids()[2:])
    assert len(case.provider.calls) == 2 and len(case.ledger.runs) == 2
    assert case.ledger.committed(case.payer) == usd(2) + usd(2)


def test_p2__an_ambiguous_submit_is_held_never_resubmitted_and_reconciled_from_evidence():
    case = Case(mode="lost")
    report = case.run()
    assert [run.state for run in report.runs] == ["ambiguous"] * 3
    assert case.ledger.committed(case.payer) == usd(2) + usd(2) + usd(2)   # the holds stay
    case.run()
    assert len(case.provider.calls) == 3                     # never a second batch
    first = case.run_ids()[0]
    adopted = asyncio.run(reconcile(first, wiring=case.wiring))
    assert adopted.state == "submitted" and adopted.external_id == "batch-1"


def test_p2__a_sample_revoked_mid_batch_never_leaves_and_its_label_is_not_imported():
    case = Case()
    case.provider.on_submit = lambda n: case.store.revoke(sid(3)) if n == 1 else None
    first, second, _ = case.run_ids()
    report = case.run()
    assert report.stopped is None
    assert [[i["sample_id"] for i in items] for _, items in case.provider.calls] == [
        [sid(1), sid(2)], [sid(4)], [sid(6)]]
    assert case.ledger.runs[second].sent_ids == (sid(4),)
    case.store.revoke(sid(1), purposes=("training",))       # a sent sample is revoked too
    case.provider.outputs["batch-1"] = [(sid(1), '{"label": "a"}'), (sid(2), '{"label": "b"}')]
    got = case.collect(first)
    assert [row["sample_id"] for row in case.labels.calls[0]["rows"]] == [sid(2)]
    assert got.failures == ((sid(1), "grant_not_current"),)
    case.run()                                              # a resume re-sends nothing
    assert len(case.provider.calls) == 3


def test_p2__a_revocation_between_the_check_and_egress_releases_the_chunk_and_stops():
    case = Case()
    # reads: the plan (1-2), chunk 1's filter (3-4) and recheck (5-6), chunk 2's filter
    # (7-8); sample 4 is revoked as chunk 2's recheck reads (9), after its filter kept it
    case.store.revoke_at = {9: (sid(4),)}
    first, second, third = case.run_ids()
    case.store.reads = 0
    report = case.run()
    assert report.stopped == "permission" and report.unsent == (third,)
    assert len(case.provider.calls) == 1 and third not in case.ledger.runs
    assert (second, "failed", "permission withdrawn before egress") in case.ledger.audit
    assert case.ledger.committed(case.payer) == usd(2)      # only the chunk that left is held


def test_p2__a_sample_without_content_is_skipped_before_egress():
    case = Case()
    digest = case.store.samples[0].content_digest                      # sample 1
    asyncio.run(case.objects.delete(fakes.sample_key(case.w.A, digest)))
    case.run()
    assert [i["sample_id"] for i in case.provider.calls[0][1]] == [sid(2)]


# --- P2.c: collection into D8 ---------------------------------------------------------------------
def test_p2__collected_labels_import_once_as_model_labels_never_ground_truth():
    case = Case()
    case.run()
    first = case.run_ids()[0]
    case.provider.outputs["batch-1"] = [
        (sid(1), '{"label": "cat", "confidence": 0.9}'), (sid(2), "not json"),
        (sid(1), '{"label": "dog"}'), (sid(6), '{"label": "x"}')]
    got = case.collect(first)
    assert case.labels.calls == [{
        "provider_org_id": case.w.A, "actor": case.w.DEV_A, "dataset_ref": fakes.DATASET_REF,
        "rubric_ref": fakes.RUBRIC_REF, "rows": [{
            "sample_id": sid(1), "method": "model", "method_version": "teach-v1", "label": "cat",
            "model": j1.JUDGE_MODEL, "prompt": "teach-v1", "confidence": 0.9}]}]
    assert got.failures == ((sid(2), "malformed_label"), (sid(1), "duplicate"),
                            (sid(6), "not_sent"))
    assert asyncio.run(case.ledger.failures(first)) == list(got.failures)   # D8's log (WR-P2-D8)
    assert got.run.state == "completed" and case.ledger.spent[case.payer] == case.provider.cost
    again = case.collect(first)
    assert again.run.state == "completed" and len(case.labels.calls) == 1
    assert case.ledger.spent[case.payer] == case.provider.cost


def test_p2__an_unfinished_batch_imports_what_arrived_and_settles_later():
    case = Case()
    case.run()
    first = case.run_ids()[0]
    case.provider.done = False
    assert case.collect(first).run.state == "submitted" and case.labels.calls == []
    case.provider.outputs["batch-1"] = [(sid(1), '{"label": "a"}')]
    assert case.collect(first).run.state == "submitted" and case.ledger.spent == {}
    assert case.labels.calls[0]["rows"] == [{"sample_id": sid(1), "method": "model",
                                             "method_version": "teach-v1", "label": "a",
                                             "model": j1.JUDGE_MODEL, "prompt": "teach-v1"}]
    case.provider.done = True
    assert case.collect(first).run.state == "completed"
    assert case.ledger.spent[case.payer] == case.provider.cost


def test_p2__a_provider_id_that_is_no_sample_id_never_blocks_the_import_or_the_settlement():
    """0-LSI2-F1: the teacher's ids are untrusted; one D8 cannot store stays in the result only."""
    case = Case()
    case.run()
    first = case.run_ids()[0]
    case.provider.outputs["batch-1"] = [(sid(1), '{"label": "a"}'), ("bogus-id", '{"label": "x"}')]
    got = case.collect(first)
    assert got.run.state == "completed" and got.failures == (("bogus-id", "not_sent"),)
    assert case.collect(first).run.state == "completed"
    assert len(case.labels.calls) == 1 and case.labels.calls[0]["rows"][0]["sample_id"] == sid(1)
    assert asyncio.run(case.ledger.failures(first)) == []
    assert case.ledger.spent[case.payer] == case.provider.cost


def test_p2__a_label_is_one_short_text_with_an_optional_confidence():
    assert parse_label('{"label": "cat"}') == ("cat", None)
    assert parse_label('{"label": "cat", "confidence": 1}') == ("cat", 1)
    for bad in ('{"label": "cat", "why": "x"}', '{"label": 3}', '{"label": "  "}',
                '{"label": "' + "x" * 4001 + '"}', '{"label": "a", "confidence": 1.5}',
                '{"label": "a", "confidence": true}', '["cat"]', '["label"]',
                '{"confidence": 0.5}', "[" * 100_000, "nope"):
        assert parse_label(bad) is None, bad[:40]


# --- the one egress adapter, against the local teacher fake (p2: 57529) --------------------------
def test_p2_http__a_batch_round_trips_through_the_local_teacher_fake():
    fake = JudgeFake(port=TEACHER_PORT)
    try:
        case = Case(provider=HttpJudgeProvider(fake.url))
        report = case.run()
        assert [run.state for run in report.runs] == ["submitted"] * 3 and len(fake.posts) == 3
        assert fake.posts[0]["submit_key"] == report.runs[0].submit_key
        fake.outputs["batch-1"] = [[sid(1), '{"label": "a"}']]
        got = case.collect(report.runs[0].run_id)
        assert got.run.state == "completed" and case.labels.calls[0]["rows"][0]["label"] == "a"
    finally:
        fake.close()
