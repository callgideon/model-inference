"""The rubric as versioned data, and the validator for what the judge sends back.

A judge result is **untrusted input**: it is a language model's JSON, arriving from a
third party, on its way into a scores table the console renders and J3 grades the
judge with. So it is validated the way a request body is - structure, closed key set,
score ranges, rationale bounds - and a malformed result is *rejected with a reason*
rather than coerced into something storable (02: "Validate JSON structure and allowed
scores before projection").

Two invariants here are not shape checks:

* **No media, no groundedness pass** (02). Groundedness needs frames, so on a
  `limited` evaluation the criterion is not merely absent from the required keys - it
  is *refused* if present, and the rubric's pass rule cannot be satisfied without it.
  A judge that guesses groundedness from the text alone would otherwise write a pass
  onto a trace nobody could check.
* **Duplicate delivery is deduped by (run, sample, rubric version)** (02). Not by
  (run, sample): a new rubric version is a new series (J4), so dropping the version
  from the key would silently discard the second version's score.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..contracts import limits

# The judge's non-criterion fields (`research/traces/06` §3.3's output schema).
OVERALL_PASS = "overall_pass"
NOTES = "notes"


@dataclass(frozen=True)
class Criterion:
    """One scored dimension. `pass_at` makes it part of the rubric's pass rule;
    `requires_media` says it cannot be scored from text alone."""

    name: str
    min_score: int = 1
    max_score: int = 5
    pass_at: int | None = None
    requires_media: bool = False


@dataclass(frozen=True)
class Rubric:
    """Versioned data, never code. `version` is an **integer** in 1..1000 on runs,
    samples, scores and labels alike (r1 R43), matching `research/traces/04`'s
    `UInt16`; `"rubric_v1"` is not a version.
    """

    rubric_id: str
    version: int
    criteria: tuple[Criterion, ...]
    max_rationale_chars: int = 300
    min_rationale_chars: int = 1
    max_notes_chars: int = 500

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise ValueError("a rubric version is an integer (r1 R43)")
        if not limits.MIN_RUBRIC_VERSION <= self.version <= limits.MAX_RUBRIC_VERSION:
            raise ValueError(f"a rubric version is in {limits.MIN_RUBRIC_VERSION}.."
                             f"{limits.MAX_RUBRIC_VERSION}")
        if not self.criteria:
            raise ValueError("a rubric scores at least one criterion")
        names = [criterion.name for criterion in self.criteria]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate criterion names: {sorted(names)}")
        for criterion in self.criteria:
            if criterion.min_score > criterion.max_score:
                raise ValueError(f"{criterion.name} has an empty score range")

    def criteria_for(self, *, media: bool) -> tuple[Criterion, ...]:
        """What may be scored. Without media the media-dependent criteria are gone -
        not optional, gone: an absent groundedness score is honest, an invented one
        is the failure 02 forbids."""
        return tuple(c for c in self.criteria if media or not c.requires_media)

    def required_keys(self, *, media: bool) -> frozenset[str]:
        return frozenset({c.name for c in self.criteria_for(media=media)}) | {OVERALL_PASS, NOTES}

    def passes(self, scores: Mapping[str, int], *, media: bool) -> bool:
        """The rubric's pass rule (`06` §3.3). A criterion in the rule with **no
        score** fails it, which is what makes a limited evaluation never a pass: the
        media-dependent criterion it needs was never scored."""
        for criterion in self.criteria:
            if criterion.pass_at is None:
                continue
            if criterion.requires_media and not media:
                return False
            score = scores.get(criterion.name)
            if score is None or score < criterion.pass_at:
                return False
        return True


# `research/traces/06` §3.3's first-draft rubric. Draft until J3 validates it on the
# ~50 human labels; the thresholds are `06`'s pass rule verbatim.
MARLIN_VIDEO_V1 = Rubric(
    rubric_id="marlin-video-v1",
    version=1,
    criteria=(
        Criterion("relevance", pass_at=4),
        Criterion("groundedness", pass_at=4, requires_media=True),
        Criterion("completeness"),
        Criterion("format", pass_at=3),
        Criterion("refusal", pass_at=3),
    ),
)


@dataclass(frozen=True)
class Score:
    name: str
    score: int
    rationale: str


@dataclass(frozen=True)
class JudgeScores:
    """An accepted result. `limited` is carried, not derived at read time, so a
    console tile cannot lose the fact that no frames were graded."""

    run_id: str
    sample_id: str
    rubric_version: int
    scores: tuple[Score, ...]
    overall_pass: bool
    notes: str
    limited: bool

    @property
    def accepted(self) -> bool:
        return True

    @property
    def by_name(self) -> dict[str, int]:
        return {score.name: score.score for score in self.scores}


@dataclass(frozen=True)
class Rejected:
    """A refused result, with the reason kept. `06` §3.5 stores the error on the item
    so a schema regression is a query rather than a silent gap in the scores."""

    run_id: str
    sample_id: str
    rubric_version: int
    reason: str
    detail: str

    @property
    def accepted(self) -> bool:
        return False


Result = JudgeScores | Rejected


def dedupe_key(result: Result) -> tuple[str, str, int]:
    """02: late results deduplicate by run, sample **and rubric version**."""
    return (result.run_id, result.sample_id, result.rubric_version)


def validate_output(rubric: Rubric, payload: object, *, run_id: str, sample_id: str,
                    media_available: bool) -> Result:
    """One judge result against one rubric version. Never raises on bad input."""
    limited = not media_available

    def reject(reason: str, detail: str) -> Rejected:
        return Rejected(run_id=run_id, sample_id=sample_id, rubric_version=rubric.version,
                        reason=reason, detail=detail)

    if not isinstance(payload, dict):
        return reject("not_an_object", f"a judge result is a JSON object, not {type(payload).__name__}")
    keys = set(payload)
    scorable = rubric.criteria_for(media=media_available)
    if limited:
        forbidden = sorted(keys & {c.name for c in rubric.criteria if c.requires_media})
        if forbidden:
            # 02: no media means no groundedness pass. A score for it is not a
            # rounding error, it is a claim about frames that were never sent.
            return reject("media_dependent_score_without_media",
                          f"{', '.join(forbidden)} cannot be scored without media")
    required = rubric.required_keys(media=media_available)
    missing = sorted(required - keys)
    if missing:
        return reject("missing_field", f"missing {', '.join(missing)}")
    unexpected = sorted(keys - required)
    if unexpected:
        return reject("unexpected_field", f"unexpected {', '.join(unexpected)}")

    scores: list[Score] = []
    for criterion in scorable:
        entry = payload[criterion.name]
        if not isinstance(entry, dict) or set(entry) != {"score", "rationale"}:
            return reject("malformed_criterion",
                          f"{criterion.name} needs exactly a score and a rationale")
        score = entry["score"]
        if isinstance(score, bool) or not isinstance(score, int):
            # `True` is not 1 and `4.0` is not 4: a float score would round into the
            # projection and a boolean would compare as one.
            return reject("score_not_an_integer", f"{criterion.name} score must be an integer")
        if not criterion.min_score <= score <= criterion.max_score:
            return reject("score_out_of_range",
                          f"{criterion.name} score {score} is outside "
                          f"{criterion.min_score}..{criterion.max_score}")
        rationale = entry["rationale"]
        if not isinstance(rationale, str) or not rationale.strip():
            return reject("rationale_missing", f"{criterion.name} needs a rationale")
        # r1 R54: bounds count Unicode code points, which is what `len` counts here.
        if not rubric.min_rationale_chars <= len(rationale) <= rubric.max_rationale_chars:
            return reject("rationale_out_of_bounds",
                          f"{criterion.name} rationale is {len(rationale)} characters, not "
                          f"{rubric.min_rationale_chars}..{rubric.max_rationale_chars}")
        scores.append(Score(name=criterion.name, score=score, rationale=rationale))

    notes = payload[NOTES]
    if not isinstance(notes, str) or len(notes) > rubric.max_notes_chars:
        return reject("notes_out_of_bounds",
                      f"notes must be text of at most {rubric.max_notes_chars} characters")
    claimed = payload[OVERALL_PASS]
    if not isinstance(claimed, bool):
        return reject("overall_pass_not_a_boolean", "overall_pass must be a boolean")
    by_name = {score.name: score.score for score in scores}
    expected = rubric.passes(by_name, media=media_available)
    if claimed != expected:
        # The pass rule belongs to the rubric version, not to the model. Storing the
        # model's claim would let a prompt change silently move the pass bar - and on
        # a limited evaluation it is how a no-media pass gets written (02).
        return reject("overall_pass_inconsistent",
                      f"overall_pass {claimed} contradicts rubric {rubric.rubric_id} "
                      f"v{rubric.version}")
    return JudgeScores(run_id=run_id, sample_id=sample_id, rubric_version=rubric.version,
                       scores=tuple(scores), overall_pass=claimed, notes=notes, limited=limited)


class ScoreLedger:
    """Collected results, deduped by `dedupe_key`.

    The provider may deliver a batch twice (a re-collect, a replayed outbox row), so
    the *first* outcome for a key wins and a later delivery neither overwrites it nor
    adds a second row. A rejection is an outcome too: re-delivering the same malformed
    result must not turn into two error rows.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, int], Result] = {}

    def deliver(self, result: Result) -> tuple[bool, Result]:
        """`(first, stored)`. `first` is False for a duplicate delivery."""
        key = dedupe_key(result)
        stored = self._by_key.get(key)
        if stored is not None:
            return False, stored
        self._by_key[key] = result
        return True, result

    @property
    def results(self) -> tuple[Result, ...]:
        return tuple(self._by_key.values())

    @property
    def accepted(self) -> tuple[JudgeScores, ...]:
        return tuple(r for r in self._by_key.values() if isinstance(r, JudgeScores))

    @property
    def rejected(self) -> tuple[Rejected, ...]:
        return tuple(r for r in self._by_key.values() if isinstance(r, Rejected))

    def __len__(self) -> int:
        return len(self._by_key)
