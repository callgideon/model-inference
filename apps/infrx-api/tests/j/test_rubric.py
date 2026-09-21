#!/usr/bin/env python3
"""JUDGE-SCORES: the rubric as versioned data, and the validator for judge output.

A judge result is untrusted input on its way into a scores table, so these cases are
input-validation cases: structure, closed key set, score ranges, rationale bounds,
duplicate delivery and the no-media rule.

    uv run --frozen pytest -q tests/j/test_rubric.py
"""
from __future__ import annotations

import pytest
from infrx.contracts import limits
from infrx.judge import (MARLIN_VIDEO_V1, Criterion, JudgeScores, Rejected, Rubric, ScoreLedger,
                         dedupe_key, validate_output)

from . import fakes

RUN, SAMPLE = fakes.uuid(900), fakes.uuid(1)
RUBRIC = MARLIN_VIDEO_V1


def check(payload, *, media: bool = True, run: str = RUN, sample: str = SAMPLE, rubric=RUBRIC):
    return validate_output(rubric, payload, run_id=run, sample_id=sample, media_available=media)


# --- the rubric is versioned data -------------------------------------------------
def test_a_rubric_version_is_an_integer_in_range():
    """r1 R43: an integer everywhere - runs, samples, scores, labels - matching
    `research/traces/04`'s `UInt16`. `"rubric_v1"` and `True` are not versions."""
    assert RUBRIC.version == 1 and isinstance(RUBRIC.version, int)
    for bad in ("1", True, 1.0, 0, limits.MAX_RUBRIC_VERSION + 1, None):
        with pytest.raises(ValueError):
            Rubric(rubric_id="r", version=bad, criteria=RUBRIC.criteria)
    assert Rubric(rubric_id="r", version=limits.MAX_RUBRIC_VERSION,
                  criteria=RUBRIC.criteria).version == 1000


def test_a_rubric_needs_criteria_with_distinct_names_and_real_ranges():
    with pytest.raises(ValueError):
        Rubric(rubric_id="r", version=1, criteria=())
    with pytest.raises(ValueError):
        Rubric(rubric_id="r", version=1, criteria=(Criterion("a"), Criterion("a")))
    with pytest.raises(ValueError):
        Rubric(rubric_id="r", version=1, criteria=(Criterion("a", min_score=5, max_score=1),))


def test_the_pass_rule_belongs_to_the_rubric_version():
    """`06` §3.3: pass iff relevance >= 4, groundedness >= 4, format >= 3, refusal >= 3.
    Completeness is scored but not part of the rule."""
    good = {"relevance": 4, "groundedness": 4, "completeness": 1, "format": 3, "refusal": 3}
    assert RUBRIC.passes(good, media=True) is True
    for name in ("relevance", "groundedness"):
        assert RUBRIC.passes({**good, name: 3}, media=True) is False
    assert RUBRIC.passes({**good, "format": 2}, media=True) is False
    assert RUBRIC.passes({**good, "completeness": 1}, media=True) is True


# --- structure --------------------------------------------------------------------
def test_a_well_formed_result_is_accepted_with_its_rubric_version():
    accepted = check(fakes.result())
    assert isinstance(accepted, JudgeScores) and accepted.accepted
    assert accepted.rubric_version == RUBRIC.version == 1
    assert accepted.by_name == {"relevance": 4, "groundedness": 4, "completeness": 4,
                                "format": 4, "refusal": 4}
    assert accepted.overall_pass is True and accepted.limited is False


@pytest.mark.parametrize("payload,reason", [
    ("{}", "not_an_object"),
    (None, "not_an_object"),
    ([], "not_an_object"),
    (42, "not_an_object"),
])
def test_a_result_that_is_not_an_object_is_rejected(payload, reason):
    rejected = check(payload)
    assert isinstance(rejected, Rejected) and rejected.reason == reason
    assert rejected.accepted is False


def test_a_missing_or_extra_field_is_rejected_by_name():
    """Closed key set, like every record in this repo (`extra="forbid"`). An extra key
    is a prompt or schema change nobody reviewed."""
    short = fakes.result(); short.pop("refusal")
    assert check(short).reason == "missing_field"
    assert "refusal" in check(short).detail
    wide = fakes.result(); wide["confidence"] = {"score": 5, "rationale": "sure"}
    assert check(wide).reason == "unexpected_field"
    assert "confidence" in check(wide).detail


def test_a_criterion_needs_exactly_a_score_and_a_rationale():
    for broken in ({"score": 4}, {"rationale": "x"}, {"score": 4, "rationale": "x", "extra": 1},
                   4, None, [4, "x"]):
        payload = fakes.result(); payload["relevance"] = broken
        assert check(payload).reason == "malformed_criterion", broken


# --- score ranges -----------------------------------------------------------------
@pytest.mark.parametrize("score", [0, 6, -1, 99])
def test_a_score_outside_the_allowed_range_is_rejected(score):
    """The range is the rubric's, and it is checked before projection (02). A 0 or a 6
    would be stored and then averaged into a quality tile."""
    payload = fakes.result(); payload["relevance"] = {"score": score, "rationale": "x"}
    rejected = check(payload)
    assert rejected.reason == "score_out_of_range" and "relevance" in rejected.detail


@pytest.mark.parametrize("score", [True, False, 4.0, "4", None])
def test_a_score_that_is_not_an_integer_is_rejected(score):
    """`True` is not 1 and `4.0` is not 4: a float would round into the projection and
    a boolean would compare as one."""
    payload = fakes.result(); payload["relevance"] = {"score": score, "rationale": "x"}
    assert check(payload).reason == "score_not_an_integer"


def test_the_range_boundaries_themselves_are_accepted():
    assert isinstance(check(fakes.result(relevance=1, overall_pass=False)), JudgeScores)
    assert isinstance(check(fakes.result(relevance=5)), JudgeScores)


# --- rationale and notes bounds ---------------------------------------------------
def test_a_rationale_is_required_and_bounded():
    """Required, because a score with no reason cannot be audited; bounded, because a
    rationale is a sentence and not an upload channel."""
    for empty in ("", "   ", None, 5):
        payload = fakes.result(); payload["relevance"] = {"score": 4, "rationale": empty}
        assert check(payload).reason in ("rationale_missing",), empty
    payload = fakes.result()
    payload["relevance"] = {"score": 4, "rationale": "x" * RUBRIC.max_rationale_chars}
    assert isinstance(check(payload), JudgeScores)
    payload["relevance"] = {"score": 4, "rationale": "x" * (RUBRIC.max_rationale_chars + 1)}
    assert check(payload).reason == "rationale_out_of_bounds"


def test_a_rationale_bound_counts_code_points():
    """r1 R54: every shared character bound counts Unicode code points."""
    payload = fakes.result()
    emoji = "\U0001f600" * RUBRIC.max_rationale_chars
    payload["relevance"] = {"score": 4, "rationale": emoji}
    assert len(emoji) == RUBRIC.max_rationale_chars
    assert isinstance(check(payload), JudgeScores)
    payload["relevance"] = {"score": 4, "rationale": emoji + "\U0001f600"}
    assert check(payload).reason == "rationale_out_of_bounds"


def test_notes_are_bounded_text():
    assert isinstance(check(fakes.result(notes="x" * RUBRIC.max_notes_chars)), JudgeScores)
    assert check(fakes.result(notes="x" * (RUBRIC.max_notes_chars + 1))).reason == \
        "notes_out_of_bounds"
    assert check(fakes.result(notes=None)).reason == "notes_out_of_bounds"


# --- overall_pass -----------------------------------------------------------------
def test_overall_pass_must_be_a_boolean_and_must_match_the_rubric():
    """The pass bar belongs to the rubric version, not to the model: storing the
    model's claim would let a prompt change move it silently."""
    assert check(fakes.result(overall_pass="true")).reason == "overall_pass_not_a_boolean"
    assert check(fakes.result(overall_pass=1)).reason == "overall_pass_not_a_boolean"
    assert check(fakes.result(relevance=2, overall_pass=True)).reason == "overall_pass_inconsistent"
    assert check(fakes.result(relevance=5, overall_pass=False)).reason == "overall_pass_inconsistent"


# --- no media, no groundedness pass (02) ------------------------------------------
def test_without_media_groundedness_is_not_scored_at_all():
    """02: "No media means no groundedness pass: record limited evaluation." The
    criterion is not optional, it is refused: a groundedness score derived from the
    text alone is a claim about frames that were never sent."""
    smuggled = fakes.result(groundedness=5, overall_pass=False)
    rejected = check(smuggled, media=False)
    assert rejected.reason == "media_dependent_score_without_media"
    assert "groundedness" in rejected.detail


def test_a_limited_evaluation_is_accepted_and_can_never_pass():
    limited = fakes.result(groundedness=None, overall_pass=False)
    accepted = check(limited, media=False)
    assert isinstance(accepted, JudgeScores)
    assert accepted.limited is True
    assert "groundedness" not in accepted.by_name
    assert accepted.overall_pass is False
    # and a claimed pass on a limited evaluation is refused, not silently downgraded
    claimed = fakes.result(groundedness=None, overall_pass=True)
    assert check(claimed, media=False).reason == "overall_pass_inconsistent"
    # even with every other criterion at 5
    perfect = fakes.result(groundedness=None, relevance=5, completeness=5, fmt=5, refusal=5,
                           overall_pass=True)
    assert check(perfect, media=False).reason == "overall_pass_inconsistent"
    assert RUBRIC.passes({"relevance": 5, "format": 5, "refusal": 5}, media=False) is False


def test_with_media_groundedness_is_required():
    assert check(fakes.result(groundedness=None, overall_pass=False), media=True).reason == \
        "missing_field"


# --- duplicate delivery ------------------------------------------------------------
def test_results_deduplicate_by_run_sample_and_rubric_version():
    """02: "Late results deduplicate by run/sample/rubric version." Not by
    (run, sample): a new rubric version is a new series (J4), so dropping the version
    from the key would discard the second version's score."""
    assert dedupe_key(check(fakes.result())) == (RUN, SAMPLE, 1)

    ledger = ScoreLedger()
    first_time, stored = ledger.deliver(check(fakes.result(relevance=5)))
    assert first_time is True and len(ledger) == 1

    again, replayed = ledger.deliver(check(fakes.result(relevance=4)))
    assert again is False, "a duplicate delivery must not be recorded twice"
    assert replayed is stored and replayed.by_name["relevance"] == 5, "the first outcome wins"
    assert len(ledger) == 1

    v2 = Rubric(rubric_id=RUBRIC.rubric_id, version=2, criteria=RUBRIC.criteria)
    fresh, _ = ledger.deliver(check(fakes.result(), rubric=v2))
    assert fresh is True and len(ledger) == 2, "a new rubric version is a new series"

    other_sample, _ = ledger.deliver(check(fakes.result(), sample=fakes.uuid(2)))
    assert other_sample is True and len(ledger) == 3
    other_run, _ = ledger.deliver(check(fakes.result(), run=fakes.uuid(901)))
    assert other_run is True and len(ledger) == 4


def test_a_rejection_is_an_outcome_and_dedupes_like_one():
    """Otherwise a replayed malformed batch writes one error row per delivery."""
    ledger = ScoreLedger()
    assert ledger.deliver(check({}))[0] is True
    assert ledger.deliver(check({}))[0] is False
    assert len(ledger) == 1 and len(ledger.rejected) == 1 and ledger.accepted == ()
