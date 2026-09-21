#!/usr/bin/env python3
"""JUDGE-SCORES: the rubric as versioned data, and the validator for judge output.

A judge result is untrusted input on its way into a scores table, so these are
input-validation cases: structure, closed key set, score ranges, rationale bounds,
duplicate delivery, the no-media rule, and "never raises, whatever arrives".

    uv run --frozen pytest -q tests/j/test_rubric.py
"""
from __future__ import annotations

import json
import random

import pytest
from infrx.contracts import limits
from infrx.judge import (MARLIN_VIDEO_V1, MAX_DETAIL_CHARS, Criterion, DuplicateKey, JudgeScores,
                         Rejected, Rubric, ScoreLedger, dedupe_key, describe,
                         is_storable_text, parse_judge_json, validate_json, validate_output)
from infrx.judge.rubric import NOTES

from . import fakes

RUN, SAMPLE = fakes.uuid(900), fakes.uuid(1)
RUBRIC = MARLIN_VIDEO_V1
reject = fakes.rejection


def check(payload, *, media: bool = True, run: str = RUN, sample: str = SAMPLE, rubric=RUBRIC):
    return validate_output(rubric, payload, run_id=run, sample_id=sample, media_available=media)


def ledger(sample_ids=(SAMPLE,), *, run: str = RUN, rubric_version: int = 1) -> ScoreLedger:
    return ScoreLedger(run, rubric_version, sample_ids)


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
    with pytest.raises(ValueError):
        Rubric(rubric_id="", version=1, criteria=RUBRIC.criteria)
    with pytest.raises(ValueError):
        Rubric(rubric_id="r", version=1, criteria=("relevance",))


@pytest.mark.parametrize("kw", [{"name": "overall_pass"}, {"name": "notes"}, {"name": ""},
                                {"name": 5}, {"min_score": True}, {"max_score": 1.5},
                                {"pass_at": 9}, {"pass_at": 0}, {"pass_at": True},
                                {"requires_media": "yes"}])
def test_a_criterion_is_validated(kw):
    """A criterion called `overall_pass` would collide with the envelope's own key; a
    `pass_at` outside the range is a rule that always fires or never does, i.e. a pass bar
    nobody set; `True` is not a score of 1."""
    with pytest.raises(ValueError):
        Criterion(**{"name": "relevance", **kw})


@pytest.mark.parametrize("kw", [{"max_rationale_chars": 0}, {"min_rationale_chars": 0},
                                {"max_rationale_chars": limits.MAX_FEEDBACK_TEXT_CHARS + 1},
                                {"max_notes_chars": 0}, {"max_notes_chars": True},
                                {"min_rationale_chars": 10, "max_rationale_chars": 5}])
def test_the_text_bounds_are_validated(kw):
    """An unbounded rationale bound is not a bound: the shared 4,000-character ceiling
    (r1 R43) is the ceiling here too."""
    with pytest.raises(ValueError):
        Rubric(rubric_id="r", version=1, criteria=RUBRIC.criteria, **kw)


def test_the_pass_rule_belongs_to_the_rubric_version():
    """`06` §3.3: pass iff relevance >= 4, groundedness >= 4, format >= 3, refusal >= 3.
    Completeness is scored but not part of the rule."""
    good = {"relevance": 4, "groundedness": 4, "completeness": 1, "format": 3, "refusal": 3}
    assert RUBRIC.passes(good, media=True) is True
    for name in ("relevance", "groundedness"):
        assert RUBRIC.passes({**good, name: 3}, media=True) is False
    assert RUBRIC.passes({**good, "format": 2}, media=True) is False
    assert RUBRIC.passes({**good, "completeness": 1}, media=True) is True
    # A criterion in the rule with **no score** fails it, which is the general form of
    # "no media, no groundedness pass": the criterion it needs was never scored.
    for name in ("relevance", "groundedness", "format", "refusal"):
        assert RUBRIC.passes({k: v for k, v in good.items() if k != name}, media=True) is False
    assert RUBRIC.passes({k: v for k, v in good.items() if k != "completeness"},
                         media=True) is True
    # And a media-dependent criterion cannot pass without media, whatever a caller's
    # dictionary claims - J3 calls this on its own rows, not only through the validator.
    assert RUBRIC.passes(good, media=False) is False


def test_no_media_no_pass_does_not_depend_on_a_threshold():
    """R56, in code rather than in rubric data: a rubric whose media criterion carries no
    `pass_at` still cannot pass without media. Deriving the rule from the thresholds alone
    made a one-word rubric edit (`pass_at=None`) enough to turn every unscored, ungrounded
    answer into a pass."""
    thresholdless = Rubric(rubric_id="r", version=1, criteria=(
        Criterion("relevance", pass_at=4), Criterion("groundedness", requires_media=True)))
    assert thresholdless.requires_media is True
    assert thresholdless.passes({"relevance": 5}, media=True) is True
    assert thresholdless.passes({"relevance": 5}, media=False) is False
    # ...and the validator agrees: a claimed pass on the limited path is refused
    limited = {"relevance": {"score": 5, "rationale": "yes"}, "overall_pass": True, "notes": ""}
    assert reject(check(limited, media=False, rubric=thresholdless)).reason == \
        "overall_pass_inconsistent"
    limited["overall_pass"] = False
    accepted = check(limited, media=False, rubric=thresholdless)
    assert isinstance(accepted, JudgeScores) and accepted.limited is True


def test_the_scores_record_refuses_a_no_media_pass():
    """R56 at the record too, so a projection replay or J3's own report cannot assemble
    one without going through the validator."""
    with pytest.raises(ValueError):
        JudgeScores(run_id=RUN, sample_id=SAMPLE, rubric_version=1, scores=(), overall_pass=True,
                    notes="", limited=True, media_required=True)
    # a rubric with no media criterion at all is not constrained by this rule
    assert JudgeScores(run_id=RUN, sample_id=SAMPLE, rubric_version=1, scores=(),
                       overall_pass=True, notes="", limited=True,
                       media_required=False).overall_pass is True


# --- structure --------------------------------------------------------------------
def test_a_well_formed_result_is_accepted_with_its_rubric_version():
    accepted = check(fakes.result())
    assert isinstance(accepted, JudgeScores) and accepted.accepted
    assert accepted.rubric_version == RUBRIC.version == 1
    assert accepted.by_name == {"relevance": 4, "groundedness": 4, "completeness": 4,
                                "format": 4, "refusal": 4}
    assert accepted.overall_pass is True and accepted.limited is False
    assert accepted.media_required is True


@pytest.mark.parametrize("payload", ["{}", None, [], 42, b"{}", ("a", 1)])
def test_a_result_that_is_not_an_object_is_rejected(payload):
    rejected = reject(check(payload))
    assert rejected.reason == "not_an_object"
    assert rejected.accepted is False


def test_a_missing_or_extra_field_is_rejected_by_name():
    """Closed key set, like every record in this repo (`extra="forbid"`). An extra key is
    a prompt or schema change nobody reviewed."""
    short = fakes.result(); short.pop("refusal")
    assert reject(check(short)).reason == "missing_field"
    assert "refusal" in reject(check(short)).detail
    wide = fakes.result(); wide["confidence"] = {"score": 5, "rationale": "sure"}
    rejected = reject(check(wide))
    assert rejected.reason == "unexpected_field"
    # R2-B3: the *count and type*, never the key itself - an unexpected key is the
    # judge's text and the judge's text quotes the customer's.
    assert rejected.detail == "1 unexpected key(s), the first a str of 10 characters"
    assert "confidence" not in rejected.detail
    # a missing field names *our* criterion, which comes from the rubric and not the
    # payload, so it stays: that is the one thing an operator needs to see.
    assert "refusal" in reject(check(short)).detail


def test_a_criterion_needs_exactly_a_score_and_a_rationale():
    for broken in ({"score": 4}, {"rationale": "x"}, {"score": 4, "rationale": "x", "extra": 1},
                   4, None, [4, "x"]):
        payload = fakes.result(); payload["relevance"] = broken
        assert reject(check(payload)).reason == "malformed_criterion", broken


# --- score ranges -----------------------------------------------------------------
@pytest.mark.parametrize("score", [0, 6, -1, 99])
def test_a_score_outside_the_allowed_range_is_rejected(score):
    """The range is the rubric's, and it is checked before projection (02). A 0 or a 6
    would be stored and then averaged into a quality tile."""
    payload = fakes.result(); payload["relevance"] = {"score": score, "rationale": "x"}
    rejected = reject(check(payload))
    assert rejected.reason == "score_out_of_range" and "relevance" in rejected.detail


@pytest.mark.parametrize("score", [True, False, 4.0, "4", None])
def test_a_score_that_is_not_an_integer_is_rejected(score):
    """`True` is not 1 and `4.0` is not 4: a float would round into the projection and a
    boolean would compare as one."""
    payload = fakes.result(); payload["relevance"] = {"score": score, "rationale": "x"}
    assert reject(check(payload)).reason == "score_not_an_integer"


def test_the_range_boundaries_themselves_are_accepted():
    assert isinstance(check(fakes.result(relevance=1, overall_pass=False)), JudgeScores)
    assert isinstance(check(fakes.result(relevance=5)), JudgeScores)


# --- rationale and notes bounds ---------------------------------------------------
def test_a_rationale_is_required_and_bounded():
    """Required, because a score with no reason cannot be audited; bounded, because a
    rationale is a sentence and not an upload channel."""
    for empty in ("", "   ", None, 5):
        payload = fakes.result(); payload["relevance"] = {"score": 4, "rationale": empty}
        assert reject(check(payload)).reason == "rationale_missing", empty
    payload = fakes.result()
    payload["relevance"] = {"score": 4, "rationale": "x" * RUBRIC.max_rationale_chars}
    assert isinstance(check(payload), JudgeScores)
    payload["relevance"] = {"score": 4, "rationale": "x" * (RUBRIC.max_rationale_chars + 1)}
    assert reject(check(payload)).reason == "rationale_out_of_bounds"


def test_a_rationale_bound_counts_code_points():
    """r1 R54: every shared character bound counts Unicode code points."""
    payload = fakes.result()
    emoji = "\U0001f600" * RUBRIC.max_rationale_chars
    payload["relevance"] = {"score": 4, "rationale": emoji}
    assert len(emoji) == RUBRIC.max_rationale_chars
    assert isinstance(check(payload), JudgeScores)
    payload["relevance"] = {"score": 4, "rationale": emoji + "\U0001f600"}
    assert reject(check(payload)).reason == "rationale_out_of_bounds"


def test_notes_are_bounded_text():
    assert isinstance(check(fakes.result(notes="x" * RUBRIC.max_notes_chars)), JudgeScores)
    assert reject(check(fakes.result(notes="x" * (RUBRIC.max_notes_chars + 1)))).reason == \
        "notes_out_of_bounds"
    assert reject(check(fakes.result(notes=None))).reason == "notes_out_of_bounds"


# --- overall_pass -----------------------------------------------------------------
def test_overall_pass_must_be_a_boolean_and_must_match_the_rubric():
    """The pass bar belongs to the rubric version, not the model: storing the model's
    claim would let a prompt change move it silently."""
    assert reject(check(fakes.result(overall_pass="true"))).reason == "overall_pass_not_a_boolean"
    assert reject(check(fakes.result(overall_pass=1))).reason == "overall_pass_not_a_boolean"
    assert reject(check(fakes.result(relevance=2, overall_pass=True))).reason == \
        "overall_pass_inconsistent"
    assert reject(check(fakes.result(relevance=5, overall_pass=False))).reason == \
        "overall_pass_inconsistent"


# --- no media, no groundedness pass (02) ------------------------------------------
def test_without_media_groundedness_is_not_scored_at_all():
    """02: "No media means no groundedness pass: record limited evaluation." The criterion
    is not optional, it is refused: a groundedness score derived from the text alone is a
    claim about frames that were never sent."""
    smuggled = fakes.result(groundedness=5, overall_pass=False)
    rejected = reject(check(smuggled, media=False))
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
    assert reject(check(claimed, media=False)).reason == "overall_pass_inconsistent"
    # even with every other criterion at 5
    perfect = fakes.result(groundedness=None, relevance=5, completeness=5, fmt=5, refusal=5,
                           overall_pass=True)
    assert reject(check(perfect, media=False)).reason == "overall_pass_inconsistent"
    assert RUBRIC.passes({"relevance": 5, "format": 5, "refusal": 5}, media=False) is False


def test_with_media_groundedness_is_required():
    assert reject(check(fakes.result(groundedness=None, overall_pass=False),
                        media=True)).reason == "missing_field"


# --- the validator never raises ----------------------------------------------------
def test_a_hostile_payload_is_rejected_rather_than_raised():
    """`validate_output` is documented never to raise, and these are the three inputs
    that made it: an integer whose `repr` exceeds Python's int/str conversion limit, a
    non-string key (the key-set arithmetic raised `TypeError`), and a key so large that
    echoing it into the detail would copy megabytes into a log row."""
    huge = fakes.result(); huge["relevance"] = {"score": 10 ** 5000, "rationale": "x"}
    assert reject(check(huge)).reason == "score_out_of_range"

    # A non-string key **beside** a string one, because that is the shape that made the
    # key-set arithmetic raise: `sorted({7, "confidence"})` cannot order the two.
    keyed = fakes.result(); keyed[7] = "not a name"; keyed["confidence"] = 1
    assert reject(check(keyed)).reason == "non_string_key"

    enormous = fakes.result(); enormous["k" * 5_000_000] = 1
    rejected = reject(check(enormous))
    assert rejected.reason == "unexpected_field"
    assert len(rejected.detail) <= MAX_DETAIL_CHARS, "a rejection detail is a log field"


def test_every_rejection_detail_is_bounded():
    rejected = Rejected(run_id=RUN, sample_id=SAMPLE, rubric_version=1, reason="r",
                        detail="d" * 10_000)
    assert len(rejected.detail) == MAX_DETAIL_CHARS
    assert rejected.detail.endswith("...")


def test_describe_reports_what_a_value_is_never_what_it_says():
    """R2-B3: the one renderer for untrusted values. Type and size, nothing else - and it
    never raises, including on the integer whose `repr` Python refuses to build."""
    marker = "CUSTOMER-SSN-123-45-6789"
    assert describe(marker) == f"str of {len(marker)} characters"
    assert marker not in describe(marker)
    # zero is neither positive nor negative; "positive int of 1 digits" was simply wrong
    assert describe(0) == "int zero"
    assert describe(-12345) == "negative int of 5 digits"
    assert describe(10 ** 5000) == "positive int of 5001 digits"
    assert describe([1, 2]) == "list of 2 items" and describe({"a": 1}) == "dict of 1 items"
    assert describe(None) == "NoneType" and describe(4.5) == "float"

    class Hostile:
        def __repr__(self):
            raise RuntimeError("boom")

        def __len__(self):
            raise RuntimeError("boom")

    assert describe(Hostile()) == "Hostile"

    class Meta(type):
        @property
        def __name__(cls):                             # noqa: A003 - that is the point
            raise RuntimeError("boom")

    class Nameless(metaclass=Meta):
        pass

    # The last resort: even the *type name* can raise, so the fallback is a constant.
    assert describe(Nameless()) == "<undescribable>"
    payload = fakes.result(); payload["relevance"] = {"score": 4, "rationale": Nameless()}
    assert reject(check(payload)).reason == "rationale_missing"


def _fuzz_payloads(marker: str):
    """Every shape that reaches a `reject(...)` with a value from the payload in it."""
    rng = random.Random(0)
    shapes = []
    for width in (1, 3, 40, 300):
        text = (marker * width)[: max(len(marker), width)]
        shapes.append({**fakes.result(), text: 1})                       # unexpected_field
        shapes.append({**fakes.result(), text: 1, "also" + text: 2})
        shapes.append({**fakes.result(), NOTES: text * 40})              # notes_out_of_bounds
        bad = fakes.result(); bad["relevance"] = {"score": 4, "rationale": text * 40}
        shapes.append(bad)                                               # rationale bounds
        bad = fakes.result(); bad["relevance"] = {"score": 4, "rationale": text + "\ud800"}
        shapes.append(bad)                                               # unstorable_text
        bad = fakes.result(); bad["relevance"] = {"score": 4, "rationale": ""}
        bad[text] = text
        shapes.append(bad)
        bad = fakes.result(); bad["relevance"] = {text: 4, "rationale": text}
        shapes.append(bad)                                               # malformed_criterion
    for score in (0, 6, -(10 ** 40), 10 ** 400, int(marker.replace("-", "").encode().hex(), 16)):
        bad = fakes.result(); bad["relevance"] = {"score": score, "rationale": marker}
        shapes.append(bad)                                               # score_out_of_range
    for key in (7, 1.5, True, None):
        bad = fakes.result(); bad[key] = marker
        shapes.append(bad)                                               # non_string_key
    for _ in range(400):                                                 # random recombinations
        base = dict(rng.choice(shapes))
        if rng.random() < 0.5:
            base.pop(rng.choice(sorted(k for k in base if isinstance(k, str))), None)
        if rng.random() < 0.5:
            base[marker + str(rng.randrange(1000))] = marker
        shapes.append(base)
    return shapes


def test_no_rejection_ever_echoes_the_payload():
    """R2-B3, the fuzz the review asked for. Judge output quotes the customer's request and
    the model's answer, so a detail that echoes any of it puts customer content in operator
    logs - a key spelled `CUSTOMER-SSN-…` appeared verbatim in 909 of 30,000 payloads."""
    marker = "CUSTOMER-SSN-123-45-6789"
    checked = 0
    for media in (True, False):
        for payload in _fuzz_payloads(marker):
            answer = check(payload, media=media)
            checked += 1
            if isinstance(answer, JudgeScores):
                continue
            assert marker not in answer.detail, answer.reason
            assert marker not in answer.reason
            assert len(answer.detail) <= MAX_DETAIL_CHARS, answer.reason
            # no fragment of it either: eight characters is already an identifier
            assert marker[:8] not in answer.detail, answer.reason
    assert checked >= 800, f"the fuzz only ran {checked} payloads"

    # the JSON door too, including the duplicate-key and parse paths
    for text in (json.dumps({marker: 1}), '{"' + marker + '": 1, "' + marker + '": 2}',
                 '{"relevance": ' + json.dumps(marker), json.dumps(fakes.result()) + marker,
                 marker, '{"notes": "' + marker + '"'):
        answer = validate_json(RUBRIC, text, run_id=RUN, sample_id=SAMPLE, media_available=True)
        if isinstance(answer, JudgeScores):
            continue
        assert marker not in answer.detail and marker[:8] not in answer.detail, answer.reason
        assert len(answer.detail) <= MAX_DETAIL_CHARS


# --- parsing ------------------------------------------------------------------------
def test_parsing_refuses_a_duplicate_key():
    """`json.loads` resolves `{"score": 1, "score": 5}` to the last value without a word,
    so a payload could carry two scores and we would store whichever the parser
    preferred."""
    with pytest.raises(DuplicateKey):
        parse_judge_json('{"a": 1, "a": 2}')
    assert parse_judge_json('{"a": 1}') == {"a": 1}

    doubled = '{"relevance": {"score": 1, "score": 5, "rationale": "x"}}'
    rejected = reject(validate_json(RUBRIC, doubled, run_id=RUN, sample_id=SAMPLE,
                                    media_available=True))
    assert rejected.reason == "duplicate_key"


def test_parsing_refuses_malformed_json_without_raising():
    for text in ("not json", "", "{", b"\xff", "[1, 2", "{'a': 1}"):
        rejected = reject(validate_json(RUBRIC, text, run_id=RUN, sample_id=SAMPLE,
                                        media_available=True))
        assert rejected.reason == "malformed_json", text
    assert len(reject(validate_json(RUBRIC, "x" * 10_000, run_id=RUN, sample_id=SAMPLE,
                                    media_available=True)).detail) <= MAX_DETAIL_CHARS


def test_a_deeply_nested_payload_is_rejected_rather_than_raised():
    """R2-B1: `json.loads` raises `RecursionError`, which is a `RuntimeError` and not a
    `ValueError`, so it went straight through the parse guard and out of J2's
    untrusted-input entry point. A 20 KB payload was enough."""
    deep = reject(validate_json(RUBRIC, "[" * 10_000 + "]" * 10_000, run_id=RUN,
                                sample_id=SAMPLE, media_available=True))
    assert deep.reason == "too_deeply_nested"
    # The other two shapes the review named. Some depths the C scanner handles, so the
    # assertion is the one that matters: an answer, never an exception.
    for text in ('{"a":' * 5_000 + "1" + "}" * 5_000,
                 '{"relevance": {"score": 4, "rationale": '
                 + "[" * 6_000 + "]" * 6_000 + "}}",
                 '{"a":' * 40_000 + "1" + "}" * 40_000):
        answer = validate_json(RUBRIC, text, run_id=RUN, sample_id=SAMPLE, media_available=True)
        assert isinstance(answer, (JudgeScores, Rejected)), text[:24]
    assert reject(validate_json(RUBRIC, '{"a":' * 40_000 + "1" + "}" * 40_000, run_id=RUN,
                                sample_id=SAMPLE, media_available=True)).reason == \
        "too_deeply_nested"


def test_a_lone_surrogate_is_refused_at_the_boundary():
    """R2-B1: `"\\ud800"` decodes to a lone surrogate, passes every length and range
    check, and then raises at persistence - on the collect path, after the money is
    spent. `is_storable_text` refuses it here instead."""
    assert is_storable_text("ok") is True and is_storable_text("\ud800") is False
    payload = fakes.result()
    payload["relevance"] = {"score": 4, "rationale": "frame \ud800 shows it"}
    assert reject(check(payload)).reason == "unstorable_text"
    assert reject(check(fakes.result(notes="\udfff"))).reason == "unstorable_text"
    # and through the JSON door, which is how it actually arrives
    text = json.dumps(fakes.result()).replace('"fine"', '"\\ud800"')
    assert reject(validate_json(RUBRIC, text, run_id=RUN, sample_id=SAMPLE,
                                media_available=True)).reason == "unstorable_text"


def test_a_hostile_subclass_cannot_make_the_validator_raise():
    """R2-B1: the checks are exact-type, so a `dict`/`str`/`int` subclass whose hooks
    raise is refused at the door rather than invited past it."""
    class Exploding(dict):
        def __iter__(self):
            raise RuntimeError("boom")

        def keys(self):
            raise RuntimeError("boom")

    class Weird(str):
        def strip(self, *args):
            raise RuntimeError("boom")

    class Sneaky(int):
        def __le__(self, other):
            raise RuntimeError("boom")

    assert reject(check(Exploding())).reason == "not_an_object"
    # R3-B2: the **criterion entry** is exact-typed too. A `dict` subclass whose `keys` or
    # `__iter__` raises reached `set(entry)` and blew up inside the loop.
    payload = fakes.result(); payload["relevance"] = Exploding(score=4, rationale="x")
    assert reject(check(payload)).reason == "malformed_criterion"
    # R3-B2: and so is a **key**. A `str` subclass got past the key scan and then into the
    # key-set arithmetic and the detail.
    class Sly(str):
        def __hash__(self):
            return hash(str(self))

        def __eq__(self, other):
            raise RuntimeError("boom")

    payload = fakes.result(); payload[Sly("confidence")] = {"score": 4, "rationale": "x"}
    assert reject(check(payload)).reason == "non_string_key"

    payload = fakes.result(); payload["relevance"] = {"score": 4, "rationale": Weird("x")}
    assert reject(check(payload)).reason == "rationale_missing"
    payload = fakes.result(); payload["relevance"] = {"score": Sneaky(4), "rationale": "x"}
    assert reject(check(payload)).reason == "score_not_an_integer"
    payload = fakes.result(); payload[NOTES] = Weird("x")
    assert reject(check(payload)).reason == "notes_out_of_bounds"


def test_a_parsed_result_validates_like_a_built_one():
    text = json.dumps(fakes.result())
    accepted = validate_json(RUBRIC, text, run_id=RUN, sample_id=SAMPLE, media_available=True)
    assert isinstance(accepted, JudgeScores) and accepted.overall_pass is True


# --- duplicate delivery ------------------------------------------------------------
def test_results_deduplicate_by_run_sample_and_rubric_version():
    """02: "Late results deduplicate by run/sample/rubric version." Not by (run, sample):
    a new rubric version is a new series (J4), so the key carries the version - which is
    also what makes a ledger for version 2 a different series from version 1's."""
    assert dedupe_key(check(fakes.result())) == (RUN, SAMPLE, 1)
    v2 = Rubric(rubric_id=RUBRIC.rubric_id, version=2, criteria=RUBRIC.criteria)
    assert dedupe_key(check(fakes.result(), rubric=v2)) == (RUN, SAMPLE, 2)

    book = ledger()
    first_time, stored = book.deliver(check(fakes.result(relevance=5)))
    assert first_time is True and len(book) == 1

    again, replayed = book.deliver(check(fakes.result(relevance=4)))
    assert again is False, "a duplicate delivery must not be recorded twice"
    assert replayed is stored and replayed.by_name["relevance"] == 5, "the first outcome wins"
    assert len(book) == 1

    # the same sample under a different rubric version is a different series, and this
    # ledger is version 1's, so it refuses rather than overwriting
    other_series, refused = book.deliver(check(fakes.result(), rubric=v2))
    assert other_series is False and reject(refused).reason == "unexpected_rubric_version"
    assert len(book) == 1
    assert ledger(rubric_version=2).deliver(check(fakes.result(), rubric=v2))[0] is True


def test_a_rejection_is_an_outcome_and_dedupes_like_one():
    """Otherwise a replayed malformed batch writes one error row per delivery."""
    book = ledger()
    assert book.deliver(check({}))[0] is True
    assert book.deliver(check({}))[0] is False
    assert len(book) == 1 and len(book.rejected) == 1 and book.accepted == ()


def test_a_ledger_belongs_to_one_run_and_its_planned_samples():
    """A late result from another run, or for a trace this run never selected, is refused
    and stored nowhere - which is the tenant-safety property and the bound: at most one
    row per planned sample."""
    book = ledger((SAMPLE, fakes.uuid(2)))
    assert book.deliver(check(fakes.result()))[0] is True

    stranger = check(fakes.result(), sample=fakes.uuid(99))
    accepted, refused = book.deliver(stranger)
    assert accepted is False and reject(refused).reason == "unexpected_sample"

    other_run = check(fakes.result(), run=fakes.uuid(901))
    accepted, refused = book.deliver(other_run)
    assert accepted is False and reject(refused).reason == "unexpected_run"

    assert len(book) == 1
    for n in range(2, 60):                       # nothing unplanned can grow the ledger
        book.deliver(check(fakes.result(), sample=fakes.uuid(n)))
    assert len(book) <= len(book.sample_ids) == 2


def test_a_ledger_refuses_an_unusable_sample_id_rather_than_raising():
    """An unhashable sample id cannot be a planned one and cannot be a dict key either, so
    it is a typed refusal instead of a `TypeError` out of the collector."""
    book = ledger((SAMPLE,))
    for unusable in ([], {}, set(), bytearray(b"x")):
        first, refused = book.deliver(
            Rejected(run_id=RUN, sample_id=unusable, rubric_version=1, reason="r", detail="d"))
        assert first is False
        assert reject(refused).reason == "unexpected_sample"
    assert len(book) == 0
