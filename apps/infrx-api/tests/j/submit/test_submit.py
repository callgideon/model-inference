#!/usr/bin/env python3
"""J2 JUDGE-BUDGET / JUDGE-SCORES / LAB-ACCESS: consented, budgeted submission and collection.

The permission half is the real L2 service (`LabAccess` over the fake of lab-sql's seam, the
LAB-ACCESS world of `tests/l/access`); the content half is T2I's real `read_content` over a
projection in memory; the D6J ledger and the provider are fakes (`fakes.py`), the provider's
HTTP adapter is proved against the local judge fake in `test_http.py`.

    uv run --frozen pytest -q tests/j/submit
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta

import pytest
from infrx.contracts import errors
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.v2 import records as v2
from infrx.contracts.v2.money_units import ProviderUsd
from infrx.judge import StaticRateTable, TokenCeilings, worst_case
from infrx.judge.submit import JudgeJob, JudgeWiring, collect, reconcile, submit
from infrx.media.store import InMemoryObjectStore

from tests.j import fakes as j1
from tests.l.access.worlds import FakeWorld

from . import fakes

LIVE = DEFAULTS.replace(judge_mode="live")
JUDGING = v2.DataPurpose.external_judging
CEILINGS = TokenCeilings(input_tokens=1_000, output_tokens=1_000, reasoning_tokens=0)
PER_RUN = ProviderUsd(worst_case(j1.TEST_RATE, CEILINGS, 3))        # three samples


class Case:
    """Provider A's developer judges three of CONSUMER_1's traces under an external_judging
    grant, paid by a named payer with room for `runs` worst cases."""

    def __init__(self, *, runs: int = 5, mode: str = "ok", settings=LIVE,
                 rates=j1.TEST_RATES) -> None:
        self.w = FakeWorld()
        fakes.grant(self.w, self.w.C1, self.w.A, JUDGING)
        self.payer = fakes.payer(self.w.A)
        self.ledger = fakes.FakeJudgeLedger({self.payer: ProviderUsd(PER_RUN.raw("PROVIDER_USD") * runs)})
        self.provider = fakes.FakeProvider(mode)
        self.projection, self.objects = fakes.Projection(), InMemoryObjectStore()
        self.ids = tuple(fakes.rid(n) for n in (1, 2, 3))
        for n, request_id in enumerate(self.ids):
            asyncio.run(fakes.trace(self.projection, self.objects, self.w.C1, request_id,
                                    body=b'{"n":%d}' % n))
        self.wiring = JudgeWiring(access=self.w.access, ledger=self.ledger,
                                  provider=self.provider, projection=self.projection,
                                  objects=self.objects, rates=rates, settings=settings)

    def job(self, n: int = 1, **kw) -> JudgeJob:
        fields = dict(run_id=fakes.rid(100 + n), provider_org_id=self.w.A,
                      grantor_org_id=self.w.C1, model_id=self.w.MODELS[self.w.A],
                      judge_model=j1.JUDGE_MODEL, payer_ref=self.payer, request_ids=self.ids,
                      media_ids=frozenset(self.ids[:1]), ceilings=CEILINGS)
        return JudgeJob(**{**fields, **kw})

    def submit_async(self, job=None, user=None):
        return submit(job or self.job(), user_id=user or self.w.DEV_A, wiring=self.wiring)

    def submit(self, job=None, user=None):
        return asyncio.run(self.submit_async(job, user))

    def refused(self, exc, job=None, user=None):
        with pytest.raises(exc):
            self.submit(job, user)
        assert self.provider.calls == [], "nothing may leave on a refusal"


# --- before egress: mode, permission, price, budget ------------------------------------------
def test_j2__dry_run_mode_never_reserves_or_egresses():
    case = Case(settings=DEFAULTS)
    case.refused(errors.BudgetExceeded)
    assert case.ledger.runs == {} and case.projection.reads == []


def test_j2__no_current_judging_grant_no_reservation_and_no_content_read():
    """LAB-ACCESS: provider_sharing is not external_judging, and a grant of the questions alone
    does not send the answers; a viewer and a foreign provider's developer are refused too,
    all before the ledger or the traces are touched."""
    case = Case()
    case.w.store.put_grant(case.w.store.grants[(case.w.C1, case.w.A)].model_copy(
        update={"version": 9, "purposes": (v2.DataPurpose.provider_sharing,)}))
    case.refused(errors.Forbidden)
    case = Case()
    fakes.grant(case.w, case.w.C1, case.w.A, JUDGING, categories=(v2.DataCategory.request_content,))
    case.refused(errors.Forbidden)
    case = Case()
    case.refused(errors.Forbidden, user=case.w.VIEWER_A)
    case.refused(errors.Forbidden, user=case.w.DEV_B)
    assert case.ledger.runs == {} and case.projection.reads == []


def test_j2__the_judge_is_checked_against_the_current_grant(world):
    """Both stores (PostgreSQL: 0027/0030 through `PgAccessStore`, on `infrx.now()`): a
    developer under a current external_judging grant submits, a viewer never does, and after
    the grantor revokes the next run is refused before any reservation or egress."""
    fakes.grant(world, world.C1, world.A, JUDGING)
    ledger = fakes.FakeJudgeLedger({fakes.payer(world.A): ProviderUsd(PER_RUN.raw("PROVIDER_USD") * 5)})
    provider, projection, objects = fakes.FakeProvider(), fakes.Projection(), InMemoryObjectStore()
    ids = (fakes.rid(1),)
    asyncio.run(fakes.trace(projection, objects, world.C1, ids[0]))
    wiring = JudgeWiring(access=world.access, ledger=ledger, provider=provider,
                         projection=projection, objects=objects, rates=j1.TEST_RATES,
                         settings=LIVE)

    def run(n, user):
        return asyncio.run(submit(JudgeJob(
            run_id=fakes.rid(200 + n), provider_org_id=world.A, grantor_org_id=world.C1,
            model_id=world.MODELS[world.A], judge_model=j1.JUDGE_MODEL,
            payer_ref=fakes.payer(world.A),
            request_ids=ids, ceilings=CEILINGS), user_id=user, wiring=wiring))

    assert run(1, world.DEV_A).state == "submitted"
    with pytest.raises(errors.Forbidden):
        run(2, world.VIEWER_A)
    world.revoke_grant(world.C1, world.A)
    with pytest.raises(errors.Forbidden):
        run(3, world.DEV_A)
    assert set(ledger.runs) == {fakes.rid(201)} and len(provider.calls) == 1


def test_j2__only_the_providers_own_named_payer_pays():
    """PROVIDER_USD work names its payer (R159), and it is this provider's: another
    provider's payer, a non-payer ref or no ref at all is refused before anything happens."""
    case = Case()
    other = fakes.payer(case.w.B)
    case.ledger.budgets[other] = PER_RUN
    for ref in (other, fakes.payer(case.w.A).replace("lab:payer:", "lab:grant:"), "", None):
        case.refused(errors.Forbidden, job=case.job(payer_ref=ref))
    assert case.ledger.runs == {} and case.projection.reads == []


def test_j2__an_expired_grant_refuses_before_the_reservation():
    case = Case()
    fakes.grant(case.w, case.w.C1, case.w.A, JUDGING, expires_in=timedelta(seconds=30))
    case.w.advance(31)
    case.refused(errors.Forbidden)
    assert case.ledger.runs == {}


def test_j2__a_grant_revoked_after_the_reservation_blocks_egress_and_releases_it():
    """Current permission is rechecked immediately before egress, not only at the start."""
    case = Case()
    reads = case.projection.find

    async def revoke_mid_run(org_id, request_id):
        case.w.revoke_grant(case.w.C1, case.w.A)
        return await reads(org_id, request_id)
    case.projection.find = revoke_mid_run
    case.refused(errors.Forbidden)
    run = case.ledger.runs[case.job().run_id]
    assert run.state == "failed" and case.ledger.committed(case.payer).is_zero


def test_j2__an_unpriced_judge_model_never_reserves():
    case = Case(rates=StaticRateTable())
    case.refused(errors.BudgetExceeded)
    assert case.ledger.runs == {}


def test_j2__the_reservation_is_the_worst_case_in_provider_usd_for_the_named_payer():
    case = Case()
    run = case.submit()
    assert run.reserved == PER_RUN and run.payer_ref == case.payer
    assert run.price_version == j1.TEST_RATE.price_version
    grant = case.w.store.grants[(case.w.C1, case.w.A)]
    assert (run.consent.grant_id, run.consent.version) == (grant.grant_id, grant.version)
    assert run.sample_ids == case.ids and run.media_ids == frozenset(case.ids[:1])


def test_j2__concurrent_runs_stay_under_the_payers_budget():
    case = Case(runs=2)

    async def many():
        return await asyncio.gather(*(submit(case.job(n), user_id=case.w.DEV_A,
                                             wiring=case.wiring) for n in range(6)),
                                    return_exceptions=True)
    outcomes = asyncio.run(many())
    assert sum(not isinstance(o, Exception) for o in outcomes) == 2
    assert all(isinstance(o, errors.BudgetExceeded) for o in outcomes if isinstance(o, Exception))
    assert len(case.provider.calls) == 2


# --- the one submission ------------------------------------------------------------------------
def test_j2__a_double_submit_is_one_intent_and_one_provider_batch():
    case = Case()

    async def twice():
        return await asyncio.gather(*(submit(case.job(), user_id=case.w.DEV_A,
                                             wiring=case.wiring) for _ in range(2)))
    first, second = asyncio.run(twice())
    assert len(case.provider.calls) == 1
    assert case.ledger.runs[case.job().run_id].state == "submitted"
    assert first.submit_key == second.submit_key
    again = case.submit()
    assert again.external_id == "batch-1" and len(case.provider.calls) == 1


def test_j2__only_stored_content_of_the_grantor_leaves_by_durable_request_id():
    """T2I: a request with no stored content is skipped before egress; a row planted under
    another organization's prefix is never followed. Everything is read with the grantor's
    organization bound."""
    case = Case()
    missing, foreign = fakes.rid(7), fakes.rid(8)
    asyncio.run(fakes.trace(case.projection, case.objects, case.w.C1, missing, stored=False))
    asyncio.run(fakes.trace(case.projection, case.objects, case.w.C2, foreign))
    case.projection.rows[-1] = dataclasses.replace(case.projection.rows[-1], org_id=case.w.C1)
    ids = case.ids + (missing, foreign)
    case.submit(case.job(request_ids=ids))
    assert len(case.provider.calls) == 1
    [(key, items)] = case.provider.calls
    assert [item["sample_id"] for item in items] == list(case.ids)
    assert [item["content"] for item in items] == ['{"n":0}', '{"n":1}', '{"n":2}']
    assert {org for org, _ in case.projection.reads} == {case.w.C1}


def test_j2__a_run_with_no_content_left_is_cancelled_without_egress():
    case = Case()
    case.objects.objects.clear()
    run = case.submit()
    assert run.state == "failed" and case.provider.calls == []
    assert case.ledger.committed(case.payer).is_zero


def test_j2__a_definite_rejection_releases_the_reservation():
    case = Case(mode="reject")
    run = case.submit()
    assert run.state == "failed" and case.ledger.committed(case.payer).is_zero


def test_j2__an_unknown_submit_outcome_is_quarantined_and_never_retried():
    case = Case(mode="timeout")
    run = case.submit()
    assert run.state == "ambiguous" and run.reserved == PER_RUN
    assert case.ledger.committed(case.payer) == PER_RUN, "the hold stays until reconciled"
    case.provider.mode = "ok"
    assert case.submit().state == "ambiguous"
    assert len(case.provider.calls) == 1, "no blind retry"


# --- reconciliation and collection -----------------------------------------------------------
def test_j2__reconciliation_adopts_the_providers_evidence_without_resubmitting():
    case = Case(mode="lost")
    run = case.submit()
    assert run.state == "ambiguous"
    run = asyncio.run(reconcile(run.run_id, wiring=case.wiring))
    assert run.state == "submitted" and run.external_id == "batch-1"
    assert len(case.provider.calls) == 1


def test_j2__reconciliation_with_no_provider_record_releases_the_hold():
    case = Case(mode="timeout")
    run = asyncio.run(reconcile(case.submit().run_id, wiring=case.wiring))
    assert run.state == "failed" and case.ledger.committed(case.payer).is_zero
    assert len(case.provider.calls) == 1
    with pytest.raises(errors.StateConflict):
        asyncio.run(reconcile(run.run_id, wiring=case.wiring))


def test_j2__a_run_still_submitting_is_not_reconciled_under_its_sender():
    """Only `ambiguous` is reconciled: a `submitting` run's sender may still be sending, so
    a lookup that finds nothing yet must not release its hold."""
    case = Case()
    job = case.job()
    asyncio.run(case.ledger.reserve(
        run_id=job.run_id, provider_org_id=job.provider_org_id, payer_ref=job.payer_ref,
        consent=None, sample_ids=job.request_ids, media_ids=frozenset(), price_version="p",
        max_cost=PER_RUN))
    asyncio.run(case.ledger.begin_submit(job.run_id))
    with pytest.raises(errors.StateConflict):
        asyncio.run(reconcile(job.run_id, wiring=case.wiring))
    assert case.ledger.runs[job.run_id].state == "submitting"


def outputs(case, *pairs):
    case.provider.outputs["batch-1"] = [(sample, text) for sample, text in pairs]


def test_j2__duplicate_and_late_results_settle_and_project_once():
    import json
    case = Case()
    run = case.submit()
    good = json.dumps(j1.result())
    case.provider.done = False
    outputs(case, (case.ids[0], good))
    run = asyncio.run(collect(run.run_id, wiring=case.wiring))
    assert run.state == "submitted" and len(case.ledger.results) == 1
    case.provider.done = True
    outputs(case, (case.ids[0], good), (case.ids[0], good), (case.ids[1], good))
    run = asyncio.run(collect(run.run_id, wiring=case.wiring))
    run = asyncio.run(collect(run.run_id, wiring=case.wiring))
    assert run.state == "completed" and run.actual == case.provider.cost
    assert len(case.ledger.results) == 2
    assert case.ledger.spent[case.payer] == case.provider.cost, "settled once"


def test_j2__malformed_foreign_and_no_media_results_are_never_a_pass():
    """JUDGE-SCORES: a malformed result is a rejection, a result for a sample the run never
    sent is stored nowhere, and a sample sent without media is limited, never a pass."""
    import json
    case = Case()
    run = case.submit()
    outputs(case, (case.ids[0], json.dumps(j1.result())), (case.ids[1], "{not json"),
            (fakes.rid(99), json.dumps(j1.result())),
            (case.ids[2], json.dumps(j1.result())))
    asyncio.run(collect(run.run_id, wiring=case.wiring))
    stored = {key[1]: result for key, result in case.ledger.results.items()}
    assert set(stored) == set(case.ids)
    assert stored[case.ids[0]].accepted and not stored[case.ids[0]].limited
    assert not stored[case.ids[1]].accepted
    third = stored[case.ids[2]]
    assert not (third.accepted and third.overall_pass)
