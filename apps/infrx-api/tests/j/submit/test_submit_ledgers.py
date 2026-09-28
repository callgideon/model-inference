#!/usr/bin/env python3
"""WR-LSQ-4: J2's submission and collection over BOTH ledgers - the fake (`fakes.py`) and
D6J's `PgJudgeLedger` (0036, `infrx.state.lab_consent`) with the `lab_submission` flag on, on
the lane's task-local PostgreSQL (INFRX_D_TASK=j2, 57511). The permission half is `LabAccess`
over the matching world (the PostgreSQL one checks 0027/0030 grants, and 0036 checks the same
grant again at reservation and when the samples leaving are recorded). Asserted only through
the ledger port and the payer's committed amount, so one case runs on both.

    INFRX_D_TASK=j2 uv run --frozen pytest -q tests/j/submit/test_submit_ledgers.py
"""
from __future__ import annotations

import asyncio
import json

import pytest
from infrx.contracts import errors
from infrx.contracts.v2 import records as v2
from infrx.contracts.v2.money_units import ProviderUsd
from infrx.judge.submit import JudgeJob, JudgeWiring, collect, reconcile, submit
from infrx.media.store import InMemoryObjectStore

from tests.d import pgharness
from tests.j import fakes as j1
from tests.l.access.worlds import FakeWorld

from . import fakes
from .conftest import CASE
from .test_submit import CEILINGS, JUDGING, LIVE, PER_RUN


class Judging:
    """Provider A's developer judges three of C1's traces, paid by A's named payer with room
    for `runs` worst cases, on the fake ledger or on `PgJudgeLedger`."""

    def __init__(self, world, pg: bool, *, runs: int = 5, mode: str = "ok") -> None:
        self.w, self.pg = world, pg
        fakes.grant(world, world.C1, world.A, JUDGING)
        self.payer = fakes.payer(world.A)
        limit = ProviderUsd(PER_RUN.raw("PROVIDER_USD") * runs)
        if pg:
            from infrx.state.jobstore import connector
            from infrx.state.lab_consent import PgJudgeLedger
            world.conn.execute("insert into infrx.feature_flags (name, enabled, updated_by, "
                               "reason) values ('lab_submission', true, 'j2', 'WR-LSQ-4')")
            world.conn.execute("select infrx.lab_put_budget(%s::jsonb)", (json.dumps({
                "provider_org_id": world.A, "payer_ref": self.payer,
                "limit": str(limit.raw("PROVIDER_USD")), "actor": "ops@a", "reason": "j2"}),))
            self.ledger = PgJudgeLedger(connector(pgharness.dsn(CASE)))
        else:
            self.ledger = fakes.FakeJudgeLedger({self.payer: limit})
        self.provider = fakes.FakeProvider(mode)
        self.projection, self.objects = fakes.Projection(), InMemoryObjectStore()
        self.ids = tuple(fakes.rid(n) for n in (1, 2, 3))
        for n, request_id in enumerate(self.ids):
            asyncio.run(fakes.trace(self.projection, self.objects, world.C1, request_id,
                                    body=b'{"n":%d}' % n))
        self.wiring = JudgeWiring(access=world.access, ledger=self.ledger,
                                  provider=self.provider, projection=self.projection,
                                  objects=self.objects, rates=j1.TEST_RATES, settings=LIVE)

    def job(self, n: int = 1, **kw) -> JudgeJob:
        fields = dict(run_id=fakes.rid(100 + n), provider_org_id=self.w.A,
                      grantor_org_id=self.w.C1, model_id=self.w.MODELS[self.w.A],
                      judge_model=j1.JUDGE_MODEL, payer_ref=self.payer, request_ids=self.ids,
                      media_ids=frozenset(self.ids[:1]), ceilings=CEILINGS)
        return JudgeJob(**{**fields, **kw})

    def submit(self, job=None):
        return submit(job or self.job(), user_id=self.w.DEV_A, wiring=self.wiring)

    def committed(self) -> ProviderUsd:
        """Held reservations plus settled actuals of the payer (0031 `lab_budgets`)."""
        if not self.pg:
            return self.ledger.committed(self.payer)
        reserved, settled = self.w.conn.execute(
            "select reserved, settled from infrx.lab_budgets where provider_org_id = %s and "
            "payer_ref = %s", (self.w.A, self.payer)).fetchone()
        return ProviderUsd(reserved + settled)

    def stored(self, run_id: str) -> list[str]:
        """The sample ids with a stored result, one per (run, sample, rubric version)."""
        if not self.pg:
            return sorted(key[1] for key in self.ledger.results if key[0] == run_id)
        return [s for (s,) in self.w.conn.execute(
            "select sample_id::text from infrx.lab_judge_results where run_id = %s "
            "order by 1", (run_id,))]


@pytest.fixture(params=["fake", pytest.param("pg", marks=pytest.mark.pg)])
def judging(request):
    if request.param == "fake":
        return lambda **kw: Judging(FakeWorld(), False, **kw)
    world = request.getfixturevalue("pg_world")
    return lambda **kw: Judging(world, True, **kw)


def test_j2_ledger__one_intent_one_batch_and_the_worst_case_held(judging):
    """JUDGE-BUDGET: a double submit is one intent and one provider batch; the hold is the
    worst case over every sample in PROVIDER_USD, under the grantor's current grant."""
    case = judging()

    async def twice():
        return await asyncio.gather(case.submit(), case.submit())
    first, second = asyncio.run(twice())
    assert len(case.provider.calls) == 1 and first.submit_key == second.submit_key
    run = asyncio.run(case.ledger.run(case.job().run_id))
    assert run.state == "submitted" and run.external_id == "batch-1"
    assert run.reserved == PER_RUN and case.committed() == PER_RUN
    assert run.sent_ids == case.ids and run.consent.version >= 1


def test_j2_ledger__concurrent_runs_stay_under_the_payers_budget(judging):
    case = judging(runs=2)

    async def many():
        return await asyncio.gather(*(case.submit(case.job(n)) for n in range(6)),
                                    return_exceptions=True)
    outcomes = asyncio.run(many())
    assert sum(not isinstance(o, Exception) for o in outcomes) == 2, outcomes
    assert all(isinstance(o, errors.BudgetExceeded) for o in outcomes if isinstance(o, Exception))
    assert len(case.provider.calls) == 2 and case.committed() == PER_RUN + PER_RUN


def test_j2_ledger__a_revocation_before_egress_releases_the_hold(judging):
    """LAB-ACCESS: a grant revoked after the reservation stops egress and frees the budget."""
    case = judging()
    reads = case.projection.find

    async def revoke_mid_run(org_id, request_id):
        if not case.projection.reads:
            case.w.revoke_grant(case.w.C1, case.w.A)
        return await reads(org_id, request_id)
    case.projection.find = revoke_mid_run
    with pytest.raises(errors.Forbidden):
        asyncio.run(case.submit())
    assert case.provider.calls == []
    assert asyncio.run(case.ledger.run(case.job().run_id)).state == "failed"
    assert case.committed().is_zero


def test_j2_ledger__an_unknown_outcome_is_held_then_reconciled_from_evidence(judging):
    """A lost answer keeps its hold (`ambiguous`) and is adopted from the provider's record;
    a batch the provider never took is released. Nothing is ever sent twice."""
    case = judging(mode="lost")
    run = asyncio.run(case.submit())
    assert run.state == "ambiguous" and case.committed() == PER_RUN
    run = asyncio.run(reconcile(run.run_id, wiring=case.wiring))
    assert run.state == "submitted" and run.external_id == "batch-1"
    case.provider.mode = "timeout"
    other = asyncio.run(case.submit(case.job(2)))
    assert other.state == "ambiguous" and case.committed() == PER_RUN + PER_RUN
    assert asyncio.run(reconcile(other.run_id, wiring=case.wiring)).state == "failed"
    assert case.committed() == PER_RUN and len(case.provider.calls) == 2


def test_j2_ledger__results_of_the_sent_samples_are_stored_and_settled_once(judging):
    """JUDGE-SCORES: a sample with no stored content never leaves and its result is stored
    nowhere; a duplicate result is stored once; the run settles its actual once."""
    case = judging()
    skipped = fakes.rid(7)
    asyncio.run(fakes.trace(case.projection, case.objects, case.w.C1, skipped, stored=False))
    run = asyncio.run(case.submit(case.job(request_ids=case.ids + (skipped,))))
    assert run.sent_ids == case.ids
    good = json.dumps(j1.result())
    case.provider.outputs["batch-1"] = [(s, good) for s in case.ids + (skipped, case.ids[0])]
    for _ in range(2):
        run = asyncio.run(collect(run.run_id, wiring=case.wiring))
    assert run.state == "completed" and run.actual == case.provider.cost
    assert case.stored(run.run_id) == sorted(case.ids)
    assert case.committed() == case.provider.cost, "settled once, the hold released"


def test_j2_ledger__a_developer_without_a_judging_grant_reserves_nothing(judging):
    """A viewer, and a grant over the questions alone, reserve nothing on either ledger."""
    case = judging()
    with pytest.raises(errors.Forbidden):
        asyncio.run(submit(case.job(), user_id=case.w.VIEWER_A, wiring=case.wiring))
    fakes.grant(case.w, case.w.C1, case.w.A, JUDGING,
                categories=(v2.DataCategory.request_content,))
    with pytest.raises(errors.Forbidden):
        asyncio.run(case.submit())
    assert asyncio.run(case.ledger.run(case.job().run_id)) is None and case.committed().is_zero
