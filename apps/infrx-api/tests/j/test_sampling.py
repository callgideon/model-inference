#!/usr/bin/env python3
"""JUDGE-SCORES: consented, deterministic, stratified selection (rulings R43, R56).

    uv run --frozen pytest -q tests/j/test_sampling.py
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from infrx.contracts import errors, ids
from infrx.contracts.records import AuthorRole, ContentState, FeedbackName, TraceMode
from infrx.judge import (CalibrationDesign, DEFAULT_DESIGN, Exclusion, Stratum, TraceCandidate,
                         deduplicate, select, within_consent_window)

from . import fakes

SEED = "j1-calibration-2026-09"


def draw(candidates, *, consent=None, seed: str = SEED, rubric_version: int = 1,
         design: CalibrationDesign = DEFAULT_DESIGN, org_id: str = fakes.ORG_A):
    return select(org_id, consent or fakes.consent(org_id), tuple(candidates),
                  rubric_version=rubric_version, seed=seed, now=fakes.NOW, design=design)


def reasons(selection) -> list[tuple[str, Exclusion]]:
    return [(e.request_id, e.reason) for e in selection.excluded]


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
    assert reasons(selection) == [(theirs.request_id, Exclusion.not_owned)]


def test_evaluation_consent_is_not_retroactive():
    """R56: consent granted today does not authorize last month's traces, and a trace
    created after the consent was revoked is outside it too. The customer agreed to send
    *future* requests for scoring, not the archive."""
    before = fakes.candidate(1, started_at=fakes.CONSENT_FROM - timedelta(seconds=1))
    at_the_edge = fakes.candidate(2, started_at=fakes.CONSENT_FROM)
    inside = fakes.candidate(3)
    selection = draw((before, at_the_edge, inside))
    assert set(selection.sample_ids) == {at_the_edge.request_id, inside.request_id}
    assert reasons(selection) == [(before.request_id, Exclusion.outside_consent_window)]

    # A revocation dated in the future leaves the record current now, but a trace made
    # after that instant is still outside the window.
    revoked_at = datetime(2026, 12, 1, tzinfo=timezone.utc)
    future_revocation = fakes.consent(revoked_at="2026-12-01T00:00:00Z")
    later = fakes.candidate(4, started_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    selection = draw((inside, later), consent=future_revocation)
    assert selection.sample_ids == (inside.request_id,)
    # ...reported as `malformed_row`, because a trace stamped after the plan's `now` is a
    # clock or a bug before it is anything else, and that rule fires first. The consent
    # window's own end bound is pinned directly in the next case.
    assert reasons(selection) == [(later.request_id, Exclusion.malformed_row)]


def test_the_consent_window_is_half_open_at_both_ends():
    """R2-B4: **at** `effective_at` is inside, **at** `revoked_at` is outside; tested only
    against a far-future trace, `<` and `<=` were indistinguishable at the end - and the end
    of a consent window is an instant the customer picked.

    Tested on the predicate directly, because the end bound cannot fire through `select`: a
    *current* consent has `now < revoked_at` and a well-formed candidate has
    `started_at <= now`. It stays in code because R56 states it and because J2's
    re-check at submission will not have that guarantee.
    """
    revoked_at = datetime(2026, 12, 1, tzinfo=timezone.utc)
    consent = fakes.consent(revoked_at="2026-12-01T00:00:00Z")
    micro = timedelta(microseconds=1)
    assert within_consent_window(fakes.CONSENT_FROM, consent) is True
    assert within_consent_window(fakes.CONSENT_FROM - micro, consent) is False
    assert within_consent_window(revoked_at - micro, consent) is True
    assert within_consent_window(revoked_at, consent) is False
    assert within_consent_window(revoked_at + micro, consent) is False
    # and with no revocation the window has no end
    assert within_consent_window(revoked_at + micro, fakes.consent()) is True
    # the start bound is the one reachable through the draw
    assert draw((fakes.candidate(7, started_at=fakes.CONSENT_FROM),)).sample_ids == (fakes.uuid(7),)


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
    assert reasons(selection) == [(fakes.uuid(1), reason)]


def test_a_non_full_trace_is_excluded_even_with_consent():
    selection = draw((fakes.candidate(1, mode=TraceMode.minimal),))
    assert selection.samples == ()
    assert reasons(selection) == [(fakes.uuid(1), Exclusion.trace_mode_not_full)]


def test_a_request_that_produced_no_output_is_skipped():
    """R56 / `06` §3.1 step 3: a 5xx has no answer to grade, so it is `skipped_no_output`
    rather than a failure sample - grading an empty response would score the platform's
    outage as the model's quality."""
    selection = draw((fakes.candidate(1, http_status=500),
                      fakes.candidate(2, http_status=503, finish_reason="length"),
                      fakes.candidate(3, http_status=499)))
    assert selection.sample_ids == (fakes.uuid(3),)
    assert reasons(selection) == [(fakes.uuid(1), Exclusion.skipped_no_output),
                                  (fakes.uuid(2), Exclusion.skipped_no_output)]


def test_a_trace_already_labelled_at_this_rubric_version_is_excluded():
    """J4: a new rubric version is a new series, so the same trace is a candidate again
    there and only there."""
    labelled = fakes.candidate(1, entries=(fakes.label(fakes.uuid(1), rubric_version=1),))
    assert reasons(draw((labelled,), rubric_version=1)) == [(fakes.uuid(1),
                                                            Exclusion.already_calibrated)]
    assert draw((labelled,), rubric_version=2).sample_ids == (fakes.uuid(1),)


@pytest.mark.parametrize("version", [True, "1", 1.0, 0, 5000, None])
def test_the_rubric_version_is_validated(version):
    """A string version or 5000 would silently never match a stored label, so every
    trace would look uncalibrated for ever."""
    with pytest.raises(errors.InvalidRequest):
        draw((fakes.candidate(1),), rubric_version=version)


# --- duplicates from the projection (R56) ------------------------------------------
def test_a_repeated_row_is_sampled_once():
    """A ClickHouse projection returns the same trace twice until it merges, which is
    why `06` §3.1 queries `FINAL`. Sampling it twice would grade and bill it twice and
    put a duplicate into `JudgeRun.sample_ids`."""
    row = fakes.candidate(1)
    selection = draw((row, row, row))
    assert selection.sample_ids == (row.request_id,)
    assert len(set(selection.sample_ids)) == len(selection.sample_ids)
    assert reasons(selection) == [(row.request_id, Exclusion.duplicate_row)] * 2


def test_a_conflicting_duplicate_is_excluded_rather_than_sampled_twice():
    """R56: two rows for one trace that *disagree* - one truncated, one not - would land
    the same trace in two strata. We cannot tell which is true, so neither is sampled."""
    row = fakes.candidate(1)
    conflicting = fakes.truncated(1)
    selection = draw((row, conflicting))
    assert selection.samples == ()
    assert reasons(selection) == [(row.request_id, Exclusion.conflicting_duplicate)] * 2

    # and it does not poison the rest of the draw
    selection = draw((row, conflicting, fakes.candidate(2)))
    assert selection.sample_ids == (fakes.uuid(2),)


def test_another_orgs_row_cannot_exclude_my_trace_as_a_duplicate():
    """The tenant filter runs **before** deduplication. With dedupe first, a foreign row
    carrying one of my request ids disagreed with mine and excluded *my* trace as a
    conflicting duplicate - another tenant deciding what I get calibrated on."""
    mine = fakes.candidate(1)
    impostor = fakes.truncated(1, org_id=fakes.ORG_B)      # same request id, different org
    selection = draw((mine, impostor))
    assert selection.sample_ids == (mine.request_id,)
    assert reasons(selection) == [(impostor.request_id, Exclusion.not_owned)]
    # ...and in either arrival order
    selection = draw((impostor, mine))
    assert selection.sample_ids == (mine.request_id,)


RESPELLINGS = ("upper", "padded", "not-a-uuid'; DROP TABLE--", "megabyte", "empty", "nil")


def _respell(candidate, how: str):
    raw = {"upper": candidate.request_id.upper(),
           "padded": f"  {candidate.request_id}  ",
           "not-a-uuid'; DROP TABLE--": "not-a-uuid'; DROP TABLE--",
           "megabyte": "a" * 1_000_000,
           "empty": "",
           "nil": None}[how]
    return TraceCandidate(**{**candidate.__dict__, "request_id": raw})


@pytest.mark.parametrize("how", RESPELLINGS)
def test_an_id_that_is_not_the_frozen_request_id_form_is_malformed(how):
    """R3-B1: `request_id` must be the lower-case UUIDv4 every contract record uses
    (`ids.is_request_id`). An earlier pass canonicalized it *for grouping only*, which left
    the raw spelling deciding the sample id, the seeded rank and which feedback rows
    matched - so an UPPER-case row carrying its own label was sampled again, and an
    upper-case row with its own customer feedback landed in `uniform`."""
    valid = fakes.candidate(0xABCDEF)
    assert ids.is_request_id(valid.request_id)
    bad = _respell(valid, how)
    assert not ids.is_request_id(bad.request_id)
    selection = draw((bad,))
    assert selection.samples == (), f"a {how} id was sampled"
    assert [e.reason for e in selection.excluded] == [Exclusion.malformed_row]


@pytest.mark.parametrize("how", RESPELLINGS)
def test_an_invalid_id_is_described_in_the_exclusion_not_echoed(how):
    """R3-B1: an invalid id is attacker-shaped data - SQL-ish text, a megabyte - and an
    exclusion row is a log row, so it is described like a rejection detail."""
    bad = _respell(fakes.candidate(0xABCDEF), how)
    selection = draw((bad,))
    assert len(selection.excluded) == 1 and selection.samples == ()
    excluded = selection.excluded[0]
    assert excluded.request_id.startswith("<invalid request_id:")
    assert len(excluded.request_id) <= 80
    if isinstance(bad.request_id, str) and bad.request_id:
        assert bad.request_id not in excluded.request_id
        assert bad.request_id[:12] not in excluded.request_id


@pytest.mark.parametrize("how", ("upper", "padded"))
def test_a_respelled_row_can_never_be_sampled_twice(how):
    """The two repros the review named, now answered by exclusion rather than by
    normalization: a respelled row carrying its own label is not a second chance at that
    trace, and a respelled row with its own feedback is not a `uniform` sample."""
    labelled = fakes.candidate(0xABCDEF, entries=(fakes.label(fakes.uuid(0xABCDEF)),))
    respelled = _respell(labelled, how)
    assert draw((respelled,), rubric_version=1).samples == ()
    # and the valid spelling is still excluded as already calibrated, not sampled
    assert [e.reason for e in draw((labelled,), rubric_version=1).excluded] == \
        [Exclusion.already_calibrated]

    with_feedback = fakes.candidate(0xABCDEF, entries=(fakes.feedback(fakes.uuid(0xABCDEF)),))
    assert [s.stratum for s in draw((with_feedback,)).samples] == [Stratum.feedback]
    assert draw((_respell(with_feedback, how),)).samples == ()


def test_the_draw_and_the_ids_it_emits_are_independent_of_the_scan_order():
    """"The seed decides, not the scan order" - as a property over seeds and shuffles, which
    is how the review caught it: with 40 candidates plus 10 respelled duplicates the
    selection changed with input order in 28 of 40 trials, because the *raw* spelling reached
    `rank` and `sample_id`."""
    rng = random.Random(7)
    population = [fakes.candidate(n) for n in range(0x100, 0x128)]
    population += [_respell(c, "upper") for c in population[:10]]
    for trial in range(40):
        seed = f"order-{trial}"
        first = draw(population, seed=seed)
        shuffled = list(population)
        rng.shuffle(shuffled)
        again = draw(shuffled, seed=seed)
        assert again.sample_ids == first.sample_ids, f"trial {trial}: the draw moved"
        assert again.counts == first.counts
        assert sorted(e.reason for e in again.excluded) == \
            sorted(e.reason for e in first.excluded)
        assert all(ids.is_request_id(sample_id) for sample_id in first.sample_ids)


@pytest.mark.parametrize("field,value", [
    ("http_status", None), ("http_status", "500"), ("http_status", True), ("http_status", 5.0),
    ("http_status", 0), ("http_status", 99), ("http_status", 600), ("http_status", 99999),
    ("started_at", datetime(2026, 9, 20, 12, 0)),                       # naive
    ("started_at", datetime(2027, 1, 1, tzinfo=timezone.utc)),          # in the future
    ("started_at", "2026-09-20T00:00:00Z"),
    ("schema_valid", "no"), ("media_available", 1), ("finish_reason", 7),
    ("trace_mode", "full"), ("content_state", "available"),
    ("request_id", ""), ("request_id", None),
    ("model_revision", None), ("feedback", []), ("feedback", ("not a feedback row",)),
    ("feedback", (7,)),
])
def test_one_bad_row_is_excluded_and_never_interpreted(field, value):
    """A projection can hand back a row whose facts are the wrong shape. Interpreting it is
    worse than dropping it: `"500" >= 500` raises, `True` is not a status code, a naive
    `started_at` cannot be compared with the consent window, and a future timestamp is a
    clock or a bug. Each is excluded on its own and the rest of the draw goes on."""
    good = fakes.candidate(1)
    bad = TraceCandidate(**{**fakes.candidate(2).__dict__, field: value})
    selection = draw((good, bad))
    assert selection.sample_ids == (good.request_id,), f"{field}={value!r} aborted the draw"
    assert [e.reason for e in selection.excluded] == [Exclusion.malformed_row]


def test_a_row_that_is_not_a_candidate_at_all_is_excluded():
    """A source can hand back anything - a string from a mis-sliced answer, a dict, `None`.
    Reading `.org_id` off it turned one bad element into an `AttributeError` that aborted the
    whole selection, so the type gate runs before the tenant check."""
    for junk in ("row", 7, None, {"request_id": fakes.uuid(1)}, object()):
        selection = draw((fakes.candidate(1), junk))
        assert selection.sample_ids == (fakes.uuid(1),), f"{junk!r} aborted the draw"
        assert [e.reason for e in selection.excluded] == [Exclusion.malformed_row]


def test_a_row_with_no_organization_is_not_mine():
    """The tenant filter runs first, so a row whose `org_id` is missing is `not_owned`
    rather than `malformed_row` - it cannot be mine, and that is the stronger statement."""
    bad = TraceCandidate(**{**fakes.candidate(2).__dict__, "org_id": None})
    selection = draw((fakes.candidate(1), bad))
    assert selection.sample_ids == (fakes.uuid(1),)
    assert [e.reason for e in selection.excluded] == [Exclusion.not_owned]


def test_the_finish_reason_is_exact_lower_case_text():
    """Stated rather than folded: the sampler compares `finish_reason` to `"length"`
    exactly, so `"LENGTH"` is not a truncation. T2's adapter owns the spelling, and the
    port's docstring says so."""
    assert fakes.candidate(1, finish_reason="LENGTH").failed is False
    assert fakes.candidate(1, finish_reason="length").failed is True
    strata = {s.request_id: s.stratum for s in
              draw((fakes.candidate(1, finish_reason="LENGTH"),
                    fakes.candidate(2, finish_reason="length"))).samples}
    assert strata == {fakes.uuid(1): Stratum.uniform, fakes.uuid(2): Stratum.failures}


def test_deduplication_accounts_for_every_row():
    """Each input row is either a sample or an exclusion; nothing is silently dropped."""
    rows = (fakes.candidate(1), fakes.candidate(1), fakes.candidate(2), fakes.truncated(2),
            fakes.candidate(3))
    selection = draw(rows)
    assert len(selection.samples) + len(selection.excluded) == len(rows)
    kept, excluded = deduplicate(rows)
    assert [c.request_id for c in kept] == [fakes.uuid(1), fakes.uuid(3)]
    assert len(kept) + len(excluded) == len(rows)


# --- stratification ---------------------------------------------------------------
def test_the_design_is_25_uniform_15_failures_10_feedback():
    """`research/traces/06` §3.9's plan, and its bounds bite: an oversupplied stratum is
    cut to its size and the overflow is reported, never spilled into another."""
    selection = draw(fakes.population())
    assert selection.counts == {Stratum.failures: 15, Stratum.feedback: 10, Stratum.uniform: 25}
    assert len(selection.samples) == 50 == DEFAULT_DESIGN.total
    assert len(set(selection.sample_ids)) == 50
    assert all(e.reason is Exclusion.stratum_full for e in selection.excluded)
    assert len(selection.excluded) == 80 - 50


def test_a_stratum_that_cannot_be_filled_is_reported_not_backfilled():
    """A calibration run of 50 uniform samples is not the design. J3 grades per stratum,
    so the shortfall is data, not something to paper over."""
    selection = draw(fakes.population(uniform=40, failures=2, feedback_bearing=0))
    assert selection.counts == {Stratum.failures: 2, Stratum.feedback: 0, Stratum.uniform: 25}
    assert selection.shortfall == {Stratum.failures: 13, Stratum.feedback: 10, Stratum.uniform: 0}
    assert len(selection.samples) == 27


def test_the_failure_stratum_is_derived_from_the_raw_facts():
    """R56 / `06` §3.1: truncation or an invalid structured output, derived here rather
    than trusted from a flag the query computed."""
    assert fakes.truncated(1).failed is True
    assert fakes.candidate(2, schema_valid=False).failed is True
    assert fakes.candidate(3).failed is False
    selection = draw((fakes.truncated(1), fakes.candidate(2, schema_valid=False),
                      fakes.candidate(3)))
    by_id = {s.request_id: s.stratum for s in selection.samples}
    assert by_id == {fakes.uuid(1): Stratum.failures, fakes.uuid(2): Stratum.failures,
                     fakes.uuid(3): Stratum.uniform}


def test_a_failing_trace_claims_the_failures_stratum_before_feedback():
    """`06` §3.1's ordering: always -> feedback -> sample. A truncated answer that also
    has a thumbs-down is the failures stratum's, and is counted once."""
    both = fakes.truncated(1, entries=(fakes.feedback(fakes.uuid(1)),))
    selection = draw((both,))
    assert [s.stratum for s in selection.samples] == [Stratum.failures]


# --- what counts as feedback, and what counts as calibration (r1 R43, R56) ----------
def test_ordinary_customer_feedback_is_a_stratum_not_a_calibration_label():
    """The heart of R43. A thumb, a rating, a correction or a comment puts a trace in the
    feedback stratum and is **never** calibration membership; only a `calibration_label`
    entry with `calibration_set` is. Conflating them would let the console's thumbs-up
    exclude the trace as "already labelled" and let J3 grade the judge against customer
    sentiment."""
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


def test_an_ordinary_entry_the_platform_made_is_neither_signal_nor_label():
    """R56/R50. `by_operator` is server-set when the accepting session is a platform
    operator, while `author_role` stays `customer` (R31) - so such a row is an ordinary
    entry *we* typed. It is not the customer telling us anything, so it must not create a
    feedback-stratum sample; and it is not a verdict against a rubric, so it must not
    exclude the trace as already calibrated."""
    ours = fakes.feedback(fakes.uuid(1), name=FeedbackName.comment, value="reproduced for the ticket",
                          role=AuthorRole.customer, by_operator=True)
    assert ours.calibration_set is False and ours.by_operator is True
    candidate = fakes.candidate(1, entries=(ours,))
    assert candidate.has_customer_feedback is False
    assert candidate.calibration_labels == ()
    selection = draw((candidate,), rubric_version=1)
    assert [s.stratum for s in selection.samples] == [Stratum.uniform]


# --- feedback rows must belong to the candidate (B4 / R56) --------------------------
def test_another_orgs_label_cannot_exclude_this_orgs_trace():
    """R56: a row counts only when its organization matches. Otherwise one tenant's
    calibration label decides another tenant's eligibility - a cross-tenant fact
    silently shrinking someone's calibration set."""
    foreign = fakes.label(fakes.uuid(1), org_id=fakes.ORG_B, rubric_version=1)
    candidate = fakes.candidate(1, entries=(foreign,))
    assert candidate.own_feedback == () and candidate.calibration_labels == ()
    assert draw((candidate,), rubric_version=1).sample_ids == (fakes.uuid(1),)


def test_a_row_for_another_request_does_not_move_a_trace_into_the_feedback_stratum():
    """R56: the request must match too. A thumb on trace 2 is not signal about trace 1,
    and a projection join that returns the wrong rows must not redesign the strata."""
    wrong_request = fakes.feedback(fakes.uuid(999), name=FeedbackName.thumb, value=True)
    candidate = fakes.candidate(1, entries=(wrong_request,))
    assert candidate.own_feedback == () and candidate.has_customer_feedback is False
    assert [s.stratum for s in draw((candidate,)).samples] == [Stratum.uniform]

    # and a label for another request cannot exclude this one either
    other_label = fakes.label(fakes.uuid(999), rubric_version=1)
    assert draw((fakes.candidate(1, entries=(other_label,)),),
                rubric_version=1).sample_ids == (fakes.uuid(1),)


# --- determinism ------------------------------------------------------------------
def test_the_same_seed_picks_the_same_samples_whatever_the_scan_order():
    """"Seeded" means reproducible, not merely shuffled: a run that dies half way through
    re-selects the same traces instead of re-rolling the dice (`06` §3.1)."""
    candidates = fakes.population()
    first = draw(candidates)
    assert draw(candidates).sample_ids == first.sample_ids
    assert draw(tuple(reversed(candidates))).sample_ids == first.sample_ids
    # and a different seed is a different draw, so the seed is actually read
    assert draw(candidates, seed="another-seed").sample_ids != first.sample_ids


def test_selection_is_stable_when_the_population_grows():
    """A trace already drawn stays drawn when new traces arrive with worse ranks, which
    is what makes an interrupted calibration resumable."""
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
