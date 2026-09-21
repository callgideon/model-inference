#!/usr/bin/env python3
"""JUDGE-SCORES / F-CONTRACT: the dry run end to end, against the frozen contract.

The plan is built through the injected candidate-source port, projected into the frozen
`JudgeRun` record, and handed to the shared `FakeJudgeCoordinator` - the same boundary D6
will implement - so "it fits the contract" is executed rather than asserted. The exported
`judge` conformance suite is run here too, against that fake: that proves the boundary J
consumes, **not** a J-owned adapter (J owns none; D6 does).

    uv run --frozen pytest -q tests/j/test_dryrun.py
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from infrx.contracts import errors, money
from infrx.contracts.conformance import MissingHook, OPTIONAL_HOOKS, judge_cases, run_cases
from infrx.contracts.fakes import FACTORIES
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import ContentState, JudgeRun, JudgeRunState, TraceMode
from infrx.judge import (APPROVED_RATES, MAX_CANDIDATES, CalibrationDesign, Stratum,
                         plan_dry_run, scan_bound)

from . import fakes

LIVE = DEFAULTS.replace(judge_mode="live", judge_live_budget_usd=Decimal("100"))


def plan(candidates=None, *, consent=None, settings=DEFAULTS, rates=APPROVED_RATES,
         seed: str = "j1-dry-run", design=CalibrationDesign(), org_id: str = fakes.ORG_A,
         limit: int = MAX_CANDIDATES, source=None):
    rows = fakes.population() if candidates is None else tuple(candidates)
    source = source if source is not None else fakes.FakeCandidateSource(rows)
    result = asyncio.run(plan_dry_run(
        source, org_id=org_id, consent=consent or fakes.consent(org_id),
        model=fakes.JUDGE_MODEL, since=fakes.SINCE, now=fakes.NOW, seed=seed,
        settings=settings, rates=rates, design=design, limit=limit))
    return source, result


def test_a_dry_run_reports_samples_costs_and_why_it_will_not_submit():
    source, dry = plan()
    assert dry.mode == "dry_run" and dry.live_submission_enabled is False
    assert dry.rubric_id == "marlin-video-v1" and dry.rubric_version == 1
    assert len(dry.sample_ids) == 50
    assert len(set(dry.sample_ids)) == 50, "R56: JudgeRun.sample_ids are unique"
    assert dry.selection.counts == {Stratum.failures: 15, Stratum.feedback: 10,
                                    Stratum.uniform: 25}
    # unpriced, because there is no approved provider rate row yet (see test_cost)
    assert dry.cost.samples == 50 and dry.cost.priced is False
    assert "TO BE VERIFIED" in dry.cost.note
    # the port was called with this tenant and this lookback, and nothing wider
    assert source.calls == [(fakes.ORG_A, fakes.SINCE, MAX_CANDIDATES)]


def test_a_dry_run_is_repeatable():
    first = plan()[1]
    assert plan()[1].sample_ids == first.sample_ids
    assert plan(seed="other")[1].sample_ids != first.sample_ids


def test_a_priced_dry_run_states_the_worst_case_and_can_be_authorized():
    _, dry = plan(rates=fakes.TEST_RATES, settings=LIVE)
    assert dry.cost.priced and dry.cost.worst_case_total > money.ZERO
    assert dry.cost.price_version == "test-rates-v1"
    assert dry.live_submission_enabled is True, "live mode, funded budget, priced estimate"
    # the same plan in the shipped defaults still refuses
    assert plan(rates=fakes.TEST_RATES)[1].live_submission_enabled is False


def test_the_plan_prices_at_now_not_at_the_lookback_start():
    """R2-B4: `at=since` survived twice, because the suite only ever used a single-row rate
    table. `RATE_HISTORY`'s newer, dearer row takes effect **between** `SINCE` and `NOW`, so
    pricing at the wrong instant is now a different number - and under-reserving by a rate
    change that has already happened is exactly what R57 exists to stop."""
    assert fakes.SINCE < fakes.NEWER_RATE.effective_at < fakes.NOW
    assert fakes.RATE_HISTORY.rate_for(fakes.JUDGE_MODEL, fakes.SINCE) is fakes.TEST_RATE
    assert fakes.RATE_HISTORY.rate_for(fakes.JUDGE_MODEL, fakes.NOW) is fakes.NEWER_RATE

    _, dry = plan(rates=fakes.RATE_HISTORY, settings=LIVE)
    assert dry.cost.price_version == "test-rates-v2", "the plan priced at `since`, not `now`"
    cheaper = plan(rates=fakes.TEST_RATES, settings=LIVE)[1]
    assert dry.cost.worst_case_total > cheaper.cost.worst_case_total
    # ...and the guard is asked at the same instant, so the estimate and the authorization
    # agree. Asked at `since` it would find v1, refuse the version and disable submission.
    assert dry.live_submission_enabled is True


# --- consent and ownership are checked before anything is read (B5) -----------------
def test_a_refusal_never_reads_the_traces():
    """A refusal that queries the trace store first has already touched the data it was
    refusing to touch. The source must not be called at all - which `select`'s own check
    cannot achieve, because by then the rows are in memory."""
    for consent, expected in ((fakes.consent(evaluation=False), errors.ConsentMissing),
                              (fakes.consent(mode=TraceMode.minimal), errors.ConsentMissing),
                              (fakes.consent(revoked_at="2026-09-10T00:00:00Z"),
                               errors.ConsentMissing),
                              (fakes.consent(fakes.ORG_B), errors.NotFound)):
        source = fakes.FakeCandidateSource(fakes.population())
        with pytest.raises(expected):
            plan(consent=consent, source=source)
        assert source.calls == [], f"the source was read before refusing {expected.__name__}"


# --- the scan bound is enforced, not requested (B1) ---------------------------------
def test_the_scan_bound_holds_against_a_source_that_ignores_it():
    """A query without a `LIMIT`, or a projection replaying a backlog, must not decide how
    much work this process does. The well-behaved fake truncates for us, so only a source
    that **ignores** `limit` can show the bound is ours."""
    rows = tuple(fakes.candidate(n) for n in range(1, 5_001))
    source = fakes.OverReturningSource(rows)
    _, dry = plan(source=source, limit=MAX_CANDIDATES)
    assert source.calls[0][2] == MAX_CANDIDATES == 200
    considered = len(dry.selection.samples) + len(dry.selection.excluded)
    assert considered == MAX_CANDIDATES, f"{considered} rows were processed, not 200"
    # every row here is a uniform candidate, so the draw is that stratum's 25
    assert dry.selection.counts == {Stratum.failures: 0, Stratum.feedback: 0, Stratum.uniform: 25}

    smaller = fakes.OverReturningSource(rows)
    _, dry = plan(source=smaller, limit=10)
    assert len(dry.selection.samples) + len(dry.selection.excluded) == 10


def test_the_plan_consumes_no_more_rows_than_the_bound():
    """The bound is on **consumption**, not just on the result: `tuple(...)[:limit]` drained
    a 100,000-row answer into this process before throwing all but 200 away. The source here
    yields a million rows and fails loudly if anything reads past the bound."""
    source = fakes.UnboundedSource(fakes.candidate(1))
    _, dry = plan(source=source, limit=25)
    assert source.consumed <= 26, f"consumed {source.consumed} rows for a bound of 25"
    assert len(dry.selection.samples) + len(dry.selection.excluded) == 25


@pytest.mark.parametrize("limit", [0, -5, True, 2.5, "10", None, MAX_CANDIDATES + 1, 10 ** 9])
def test_a_scan_bound_that_is_not_one_is_refused(limit):
    """`True` is not 1, `10**9` is not a bound anybody chose, and a zero or negative limit
    would ask for nothing and then report an empty calibration as a result."""
    with pytest.raises(errors.InvalidRequest):
        scan_bound(limit)
    source = fakes.FakeCandidateSource(fakes.population())
    with pytest.raises(errors.InvalidRequest):
        plan(source=source, limit=limit)
    assert source.calls == [], "an invalid bound is refused before the source is read"


def test_the_boundary_values_of_the_scan_bound_are_accepted():
    assert scan_bound(1) == 1
    assert scan_bound(MAX_CANDIDATES) == MAX_CANDIDATES


@pytest.mark.parametrize("answer", [None, 7, object(), "row", b"row"])
def test_a_source_that_does_not_answer_an_iterable_is_refused(answer):
    """A typed refusal, not a `TypeError` out of `islice`: a coroutine nobody awaited, a
    `None` from an adapter that logged instead of returning. `str` and `bytes` are iterable
    and would silently yield characters, so they are refused too."""
    class Wrong:
        def __init__(self):
            self.calls: list = []

        async def candidates(self, org_id, *, since, limit):
            self.calls.append((org_id, since, limit))
            return answer

    with pytest.raises(errors.InvalidRequest, match="synchronous iterable"):
        plan(source=Wrong())


def test_an_async_generator_source_is_refused():
    """The wrong shape most likely to be written by mistake, named on its own."""
    class AsyncGen:
        def candidates(self, org_id, *, since, limit):
            async def rows():
                yield fakes.candidate(1)
            return rows()

    async def call():
        return await plan_dry_run(AsyncGen(), org_id=fakes.ORG_A, consent=fakes.consent(),
                                  model=fakes.JUDGE_MODEL, since=fakes.SINCE, now=fakes.NOW,
                                  seed="s")

    with pytest.raises(errors.InvalidRequest, match="async function"):
        asyncio.run(call())


def test_the_plan_reports_the_mode_it_ran_under():
    """The report field, pinned: `mode` is the configured `JUDGE_MODE`, not a literal. A plan
    that always said `dry_run` would hide a live run in its own audit record."""
    assert plan()[1].mode == "dry_run"
    assert plan(settings=LIVE)[1].mode == "live"
    assert plan(settings=DEFAULTS.replace(judge_mode="test"))[1].mode == "test"


def test_unreadable_content_never_reaches_the_plan():
    candidates = (fakes.candidate(1),
                  fakes.candidate(2, content=ContentState.expired),
                  fakes.candidate(3, content=ContentState.lost),
                  fakes.candidate(4, mode=TraceMode.minimal),
                  fakes.candidate(5, http_status=500))
    _, dry = plan(candidates)
    assert dry.sample_ids == (fakes.uuid(1),)
    assert len(dry.selection.excluded) == 4


def test_duplicate_rows_from_the_source_never_become_duplicate_samples():
    """R56, through the composed path: `JudgeRun.sample_ids` must be unique, so a
    projection returning a trace twice cannot make the run grade and bill it twice."""
    row = fakes.candidate(1)
    _, dry = plan((row, row, fakes.candidate(2), fakes.truncated(2)))
    assert dry.sample_ids == (row.request_id,)
    assert len(set(dry.sample_ids)) == len(dry.sample_ids)


# --- the frozen contract ----------------------------------------------------------
def test_the_plan_projects_into_the_frozen_judge_run_record():
    """01/r1 R43: `JudgeRun` with an **integer** `rubric_version`, `state=dry_run` and a
    zero reservation - an estimate reserves nothing, so it cannot consume another run's
    budget or authorize a submission."""
    _, dry = plan(rates=fakes.TEST_RATES)
    consent = fakes.consent()
    run = dry.judge_run(run_id=fakes.uuid(900), consent=consent, model_revision=fakes.MODEL,
                        created_at=fakes.NOW)
    assert isinstance(run, JudgeRun)
    assert run.state is JudgeRunState.dry_run
    assert run.reserved_cost == money.ZERO
    assert run.rubric_version == 1 and isinstance(run.rubric_version, int)
    assert run.sample_ids == dry.sample_ids
    assert len(set(run.sample_ids)) == len(run.sample_ids)
    assert run.submit_intent is None and run.external_batch_id is None
    # and it serializes through the canonical codec like every other record
    assert JudgeRun.model_validate_json(run.model_dump_json()) == run


def test_the_shared_coordinator_keeps_a_dry_run_reservationless():
    """The boundary J consumes, exercised: reserving a dry-run plan through the shared
    `JudgeCoordinator` fake yields `dry_run` with zero reserved cost, and `begin_submit`
    refuses. J keeps no second budget table (03)."""
    harness = FACTORIES["judge"]()
    _, dry = plan(rates=fakes.TEST_RATES)
    consent = fakes.consent()
    run = dry.judge_run(run_id=fakes.uuid(900), consent=consent, model_revision=fakes.MODEL,
                        created_at=harness.clock.now())
    stored = asyncio.run(harness.port.reserve(run, consent, dry.cost.worst_case_total))
    assert stored.state is JudgeRunState.dry_run and stored.reserved_cost == money.ZERO
    assert harness.extra["available"]() == money.ZERO
    with pytest.raises(errors.BudgetExceeded):
        asyncio.run(harness.port.begin_submit(run.run_id))


def test_the_exported_judge_conformance_suite_passes_against_the_shared_fake():
    """F-CONTRACT. This is the boundary J1 *consumes*; J1 ships no `JudgeCoordinator`
    adapter of its own (D6 owns the real one), so this is **implemented**, never
    integrated. Skips are collected and asserted empty, so a missing hook can never be read
    as a pass (r1 R32)."""
    skipped: list[MissingHook] = []
    cases = judge_cases()
    ran = run_cases(cases, FACTORIES["judge"], skipped=skipped)
    assert [s.hook for s in skipped] == [], "a skipped case is never a pass"
    assert ran == len(cases) == 13
    assert OPTIONAL_HOOKS["judge"] <= set(FACTORIES["judge"]().extra)
