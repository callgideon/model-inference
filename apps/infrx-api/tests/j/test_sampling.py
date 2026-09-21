#!/usr/bin/env python3
"""JUDGE-SCORES: consented, deterministic, stratified selection.

    uv run --frozen pytest -q tests/j/test_sampling.py
"""
from __future__ import annotations

import pytest
from infrx.contracts import errors
from infrx.contracts.records import AuthorRole, ContentState, FeedbackName, TraceMode
from infrx.judge import CalibrationDesign, DEFAULT_DESIGN, Exclusion, Stratum, select

from . import fakes

SEED = "j1-calibration-2026-09"


def draw(candidates, *, consent=None, seed: str = SEED, rubric_version: int = 1,
         design: CalibrationDesign = DEFAULT_DESIGN, org_id: str = fakes.ORG_A):
    return select(org_id, consent or fakes.consent(org_id), tuple(candidates),
                  rubric_version=rubric_version, seed=seed, now=fakes.NOW, design=design)


# --- consent ---------------------------------------------------------------------
def test_trace_opt_in_is_not_evaluation_consent():
    """A `full`-mode trace is storage consent. Evaluation consent is a separate,
    *current* record, and without it nothing is selected at all - the refusal comes
    before any candidate is looked at, so there is no partial draw to leak."""
    candidates = fakes.population()
    assert len(draw(candidates).samples) == DEFAULT_DESIGN.total

    for withheld in (fakes.consent(evaluation=False),
                     fakes.consent(mode=TraceMode.minimal),
                     fakes.consent(mode=TraceMode.off),
                     fakes.consent(revoked_at="2026-09-10T00:00:00Z"),
                     fakes.consent(effective_at="2026-12-01T00:00:00Z")):
        with pytest.raises(errors.ConsentMissing):
            draw(candidates, consent=withheld)


def test_one_orgs_consent_never_authorizes_another_orgs_traces():
    """r1 R10: two tenant-bearing arguments must name the same organization."""
    with pytest.raises(errors.NotFound):
        draw(fakes.population(), consent=fakes.consent(fakes.ORG_B))


def test_another_tenants_trace_is_never_a_sample():
    """Belt and braces over the source's own filter: the port is trusted to answer
    fast, not to be the tenant boundary."""
    mine = fakes.candidate(1)
    theirs = fakes.candidate(2, org_id=fakes.ORG_B)
    selection = draw((mine, theirs))
    assert selection.sample_ids == (mine.request_id,)
    assert [(e.request_id, e.reason) for e in selection.excluded] == [
        (theirs.request_id, Exclusion.not_owned)]


# --- exclusions -------------------------------------------------------------------
@pytest.mark.parametrize("state,reason", [
    (ContentState.lost, Exclusion.content_lost),
    (ContentState.expired, Exclusion.content_expired),
    (ContentState.pending, Exclusion.content_missing),
    (ContentState.metadata_only, Exclusion.content_missing),
    (ContentState.off, Exclusion.content_missing),
])
def test_content_that_cannot_be_read_is_excluded_with_its_reason(state, reason):
    """02: "revoked/expired/missing content is skipped before egress". Each reason is
    kept apart, because an expired trace is a retention decision and a lost one is a
    capture failure - and the console shows the difference."""
    selection = draw((fakes.candidate(1, content=state),))
    assert selection.samples == ()
    assert [(e.request_id, e.reason) for e in selection.excluded] == [(fakes.uuid(1), reason)]


def test_a_non_full_trace_is_excluded_even_with_consent():
    selection = draw((fakes.candidate(1, mode=TraceMode.minimal),))
    assert selection.samples == ()
    assert selection.excluded[0].reason is Exclusion.trace_mode_not_full


def test_a_trace_already_labelled_at_this_rubric_version_is_excluded():
    """J4: a new rubric version is a new series, so the same trace is a candidate
    again there and only there."""
    labelled = fakes.candidate(1, entries=(fakes.label(fakes.uuid(1), rubric_version=1),))
    assert draw((labelled,), rubric_version=1).excluded[0].reason is Exclusion.already_calibrated
    assert draw((labelled,), rubric_version=2).sample_ids == (fakes.uuid(1),)


# --- stratification ---------------------------------------------------------------
def test_the_design_is_25_uniform_15_failures_10_feedback():
    """`research/traces/06` §3.9's plan, and its bounds bite: an oversupplied stratum
    is cut to its size and the overflow is reported, never spilled into another."""
    selection = draw(fakes.population())
    assert selection.counts == {Stratum.failures: 15, Stratum.feedback: 10, Stratum.uniform: 25}
    assert len(selection.samples) == 50 == DEFAULT_DESIGN.total
    assert all(e.reason is Exclusion.stratum_full for e in selection.excluded)
    assert len(selection.excluded) == 80 - 50


def test_a_stratum_that_cannot_be_filled_is_reported_not_backfilled():
    """A calibration run of 50 uniform samples is not the design. J3 grades per
    stratum, so the shortfall is data, not something to paper over."""
    selection = draw(fakes.population(uniform=40, failures=2, feedback_bearing=0))
    assert selection.counts == {Stratum.failures: 2, Stratum.feedback: 0, Stratum.uniform: 25}
    assert selection.shortfall == {Stratum.failures: 13, Stratum.feedback: 10, Stratum.uniform: 0}
    assert len(selection.samples) == 27


def test_a_failing_trace_claims_the_failures_stratum_before_feedback():
    """`06` §3.1's ordering: always -> feedback -> sample. A truncated answer that also
    has a thumbs-down is the failures stratum's, and is counted once."""
    both = fakes.candidate(1, failure=True, entries=(fakes.feedback(fakes.uuid(1)),))
    selection = draw((both,))
    assert [s.stratum for s in selection.samples] == [Stratum.failures]


# --- what counts as feedback, and what counts as calibration (r1 R43) --------------
def test_ordinary_customer_feedback_is_a_stratum_not_a_calibration_label():
    """The heart of R43. A thumb, a rating, a correction or a comment puts a trace in
    the feedback stratum and is **never** calibration membership; only a
    `calibration_label` entry with `calibration_set` is. Conflating them would let the
    console's thumbs-up exclude the trace as "already labelled" and let J3 grade the
    judge against customer sentiment."""
    for name, value in ((FeedbackName.thumb, True), (FeedbackName.rating, 5),
                        (FeedbackName.correction, "it was a cat"),
                        (FeedbackName.comment, "close enough")):
        row = fakes.feedback(fakes.uuid(1), name=name, value=value)
        assert row.calibration_set is False and row.rubric_version is None
        candidate = fakes.candidate(1, entries=(row,))
        assert candidate.has_customer_feedback is True
        assert candidate.calibration_labels == ()
        selection = draw((candidate,), rubric_version=1)
        assert [s.stratum for s in selection.samples] == [Stratum.feedback], name


def test_a_judges_own_score_is_not_customer_feedback():
    """Otherwise the judge's first run moves every trace it scored into the
    feedback-bearing stratum and the design grades the judge on its own output."""
    judged = fakes.candidate(1, entries=(fakes.feedback(fakes.uuid(1), role=AuthorRole.judge,
                                                        name=FeedbackName.rating, value=3),))
    assert judged.has_customer_feedback is False
    assert [s.stratum for s in draw((judged,)).samples] == [Stratum.uniform]


def test_an_operator_label_is_not_the_feedback_stratum():
    """A label at another rubric version keeps the trace eligible, but as a *uniform*
    sample: it is operator data, not a customer signal."""
    labelled = fakes.candidate(1, entries=(fakes.label(fakes.uuid(1), rubric_version=7),))
    assert labelled.has_customer_feedback is False
    assert [s.stratum for s in draw((labelled,), rubric_version=1).samples] == [Stratum.uniform]


# --- determinism ------------------------------------------------------------------
def test_the_same_seed_picks_the_same_samples_whatever_the_scan_order():
    """"Seeded" means reproducible, not merely shuffled: a run that dies half way
    through re-selects the same traces instead of re-rolling the dice (`06` §3.1)."""
    candidates = fakes.population()
    first = draw(candidates)
    assert draw(candidates).sample_ids == first.sample_ids
    assert draw(tuple(reversed(candidates))).sample_ids == first.sample_ids
    # and a different seed is a different draw, so the seed is actually read
    assert draw(candidates, seed="another-seed").sample_ids != first.sample_ids


def test_selection_is_stable_when_the_population_grows():
    """A trace already drawn stays drawn when new traces arrive with worse ranks,
    which is what makes an interrupted calibration resumable."""
    base = fakes.population(uniform=25, failures=15, feedback_bearing=10)
    first = set(draw(base).sample_ids)
    grown = base + tuple(fakes.candidate(500 + i) for i in range(5))
    assert len(first & set(draw(grown).sample_ids)) >= 45


# --- the limited flag -------------------------------------------------------------
def test_a_sample_without_media_is_marked_limited():
    """02: no media means no groundedness pass. The flag rides on the sample so the
    prompt, the validator and the report read one fact rather than three guesses."""
    selection = draw((fakes.candidate(1, media=True), fakes.candidate(2, media=False)))
    by_id = {s.request_id: s for s in selection.samples}
    assert by_id[fakes.uuid(1)].limited is False
    assert by_id[fakes.uuid(2)].limited is True
    assert selection.limited == (by_id[fakes.uuid(2)],)


# --- the design itself ------------------------------------------------------------
@pytest.mark.parametrize("kw", [{"uniform": -1}, {"failures": True}, {"feedback": 1.5}])
def test_a_design_size_is_a_nonnegative_integer(kw):
    with pytest.raises(ValueError):
        CalibrationDesign(**kw)
