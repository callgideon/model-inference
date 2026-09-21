#!/usr/bin/env python3
"""JUDGE-SCORES / F-CONTRACT: the dry run end to end, against the frozen contract.

The plan is built through the injected candidate-source port, projected into the
frozen `JudgeRun` record, and handed to the shared `FakeJudgeCoordinator` - the same
boundary D6 will implement - so "it fits the contract" is executed rather than
asserted. The exported `judge` conformance suite is run here too, against that fake:
that proves the boundary J consumes, **not** a J-owned adapter (J owns none; D6 does).

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
from infrx.judge import (APPROVED_RATES, DEFAULT_CEILINGS, MARLIN_VIDEO_V1, MAX_CANDIDATES,
                         CalibrationDesign, Stratum, plan_dry_run)

from . import fakes

LIVE = DEFAULTS.replace(judge_mode="live", judge_live_budget_usd=Decimal("100"))


def plan(candidates=None, *, consent=None, settings=DEFAULTS, rates=APPROVED_RATES,
         seed: str = "j1-dry-run", design=CalibrationDesign(), org_id: str = fakes.ORG_A,
         limit: int = MAX_CANDIDATES):
    source = fakes.FakeCandidateSource(fakes.population() if candidates is None else candidates)
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
    assert dry.selection.counts == {Stratum.failures: 15, Stratum.feedback: 10,
                                    Stratum.uniform: 25}
    # unpriced, because there is no approved provider rate row yet (see test_cost)
    assert dry.cost.samples == 50 and dry.cost.priced is False and "TO BE VERIFIED" in dry.cost.note
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


def test_a_dry_run_without_current_evaluation_consent_refuses():
    with pytest.raises(errors.ConsentMissing):
        plan(consent=fakes.consent(evaluation=False))
    with pytest.raises(errors.ConsentMissing):
        plan(consent=fakes.consent(mode=TraceMode.minimal))
    with pytest.raises(errors.NotFound):
        plan(consent=fakes.consent(fakes.ORG_B))


def test_unreadable_content_never_reaches_the_plan():
    candidates = (fakes.candidate(1),
                  fakes.candidate(2, content=ContentState.expired),
                  fakes.candidate(3, content=ContentState.lost),
                  fakes.candidate(4, mode=TraceMode.minimal))
    _, dry = plan(candidates)
    assert dry.sample_ids == (fakes.uuid(1),)
    assert len(dry.selection.excluded) == 3


def test_the_candidate_scan_is_bounded():
    """`06` §3.4: 200 items is the batch ceiling, so the scan is bounded too - an
    unbounded candidate query is a memory bound nobody set."""
    source, dry = plan(tuple(fakes.candidate(n) for n in range(1, 400)))
    assert source.calls[0][2] == MAX_CANDIDATES == 200
    assert len(dry.selection.samples) + len(dry.selection.excluded) == MAX_CANDIDATES


# --- the frozen contract ----------------------------------------------------------
def test_the_plan_projects_into_the_frozen_judge_run_record():
    """01/r1 R43: `JudgeRun` with an **integer** `rubric_version`, `state=dry_run` and
    a zero reservation - an estimate reserves nothing, so it cannot consume another
    run's budget or authorize a submission."""
    _, dry = plan(rates=fakes.TEST_RATES)
    consent = fakes.consent()
    run = dry.judge_run(run_id=fakes.uuid(900), consent=consent, model_revision=fakes.MODEL,
                        created_at=fakes.NOW)
    assert isinstance(run, JudgeRun)
    assert run.state is JudgeRunState.dry_run
    assert run.reserved_cost == money.ZERO
    assert run.rubric_version == 1 and isinstance(run.rubric_version, int)
    assert run.sample_ids == dry.sample_ids
    assert run.submit_intent is None and run.external_batch_id is None
    # and it serializes through the canonical codec like every other record
    assert JudgeRun.model_validate_json(run.model_dump_json()) == run


def test_the_shared_coordinator_keeps_a_dry_run_reservationless():
    """The boundary J consumes, exercised: reserving a dry-run plan through the shared
    `JudgeCoordinator` fake yields `dry_run` with zero reserved cost, and
    `begin_submit` refuses. J keeps no second budget table (03)."""
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
    integrated. Skips are collected and asserted empty, so a missing hook can never be
    read as a pass (r1 R32)."""
    skipped: list[MissingHook] = []
    cases = judge_cases()
    ran = run_cases(cases, FACTORIES["judge"], skipped=skipped)
    assert [s.hook for s in skipped] == [], "a skipped case is never a pass"
    assert ran == len(cases) == 13
    assert OPTIONAL_HOOKS["judge"] <= set(FACTORIES["judge"]().extra)
