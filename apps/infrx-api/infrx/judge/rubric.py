"""The rubric as versioned data, and the validator for what the judge sends back.

A judge result is **untrusted input**: a language model's JSON, from a third party, on
its way into a scores table the console renders and J3 grades the judge with. So it is
validated the way a request body is - structure, closed key set, score ranges,
rationale bounds - and a malformed result is *rejected with a bounded reason* rather
than coerced into something storable (02: "Validate JSON structure and allowed scores
before projection"). `validate_output` **never raises**, whatever arrives: not on a
non-string key, not on an integer with 5,000 digits, not on a 5 MB key.

Three invariants here are not shape checks:

* **No media, no overall pass** (02, R56). Enforced in *code*, not by rubric data: if
  any criterion requires media, a media-less evaluation cannot pass, whatever its
  `pass_at` thresholds happen to be. A judge that guessed groundedness from the text
  would otherwise write a pass onto a trace nobody could check.
* **Duplicate delivery is deduped by (run, sample, rubric version)** (02). Not by
  (run, sample): a new rubric version is a new series (J4).
* **A ledger belongs to one run.** It accepts only that run's rubric version and its
  planned sample ids, so a late result from another run cannot append a row and the
  ledger is bounded by the plan.
"""
from __future__ import annotations

import decimal
import json
from dataclasses import dataclass
from typing import Iterable, Mapping

from ..contracts import limits

# The judge's non-criterion fields (`research/traces/06` §3.3's output schema).
OVERALL_PASS = "overall_pass"
NOTES = "notes"
RESERVED_NAMES = frozenset({OVERALL_PASS, NOTES})
#: A rejection reason is for an operator's eyes and a log line, not a payload echo.
MAX_DETAIL_CHARS = 200


def describe(value: object) -> str:
    """What an untrusted value *is*, never what it says (R2-B3).

    A rejection detail is a log field. Judge output quotes the customer's request and the
    model's answer, so echoing any of it - a key, a string, an out-of-range number - puts
    customer content into operator logs: a fuzz run found 909 echoes in 30,000 payloads,
    including a key spelled like a social-security number appearing verbatim. So this
    reports the type and the size and nothing else.

    It also never raises: `repr` of a 5,000-digit integer raises `ValueError` under
    Python's int/str conversion limit, and `len`/`repr` of a hostile object can raise
    anything, so the one place we render untrusted values catches it here instead of
    every caller remembering.
    """
    try:
        if type(value) is str:
            return f"str of {len(value)} characters"
        if type(value) is int:
            # `str(value)` raises past 4,300 digits; `Decimal` counts them exactly
            # without going through text.
            digits = 1 if value == 0 else decimal.Decimal(value).adjusted() + 1
            return f"{'negative' if value < 0 else 'positive'} int of {digits} digits"
        if type(value) in (list, tuple, dict, set):
            return f"{type(value).__name__} of {len(value)} items"
        return type(value).__name__
    except Exception:                                  # noqa: BLE001 - see docstring
        # A fixed string, because the fallback cannot itself touch the value or its type:
        # a metaclass whose `__name__` raises would make the handler raise too.
        return "<undescribable>"


def is_storable_text(value: str) -> bool:
    """Whether this string survives the trip to a UTF-8 column.

    `json.loads` happily produces a **lone surrogate** (`"\\ud800"` decodes to one), and
    every length and range check passes it - then `.encode("utf-8")` raises at
    persistence, on the collect path, after the money has been spent. It is refused at
    the boundary instead, like every other unstorable input.
    """
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _bounded_int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"{name} must be in {low}..{high}")
    return value


@dataclass(frozen=True)
class Criterion:
    """One scored dimension. `pass_at` makes it part of the rubric's pass rule;
    `requires_media` says it cannot be scored from text alone."""

    name: str
    min_score: int = 1
    max_score: int = 5
    pass_at: int | None = None
    requires_media: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("a criterion needs a name")
        if self.name in RESERVED_NAMES:
            # `overall_pass` and `notes` are the envelope's own keys: a criterion by
            # either name would collide with them in the payload and in the key set.
            raise ValueError(f"{self.name} is reserved for the result envelope")
        _bounded_int(self.min_score, f"{self.name} min_score", -1_000, 1_000)
        _bounded_int(self.max_score, f"{self.name} max_score", -1_000, 1_000)
        if self.min_score > self.max_score:
            raise ValueError(f"{self.name} has an empty score range")
        if self.pass_at is not None:
            # A threshold outside the range is a rule that can never fire (or always
            # fires), i.e. a pass bar nobody set.
            _bounded_int(self.pass_at, f"{self.name} pass_at", self.min_score, self.max_score)
        if not isinstance(self.requires_media, bool):
            raise ValueError(f"{self.name} requires_media must be a boolean")


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
        if not isinstance(self.rubric_id, str) or not self.rubric_id.strip():
            raise ValueError("a rubric needs an id")
        _bounded_int(self.version, "a rubric version", limits.MIN_RUBRIC_VERSION,
                     limits.MAX_RUBRIC_VERSION)
        if not self.criteria or not all(isinstance(c, Criterion) for c in self.criteria):
            raise ValueError("a rubric scores at least one criterion")
        names = [criterion.name for criterion in self.criteria]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate criterion names: {sorted(names)}")
        _bounded_int(self.min_rationale_chars, "min_rationale_chars", 1,
                     limits.MAX_FEEDBACK_TEXT_CHARS)
        _bounded_int(self.max_rationale_chars, "max_rationale_chars", self.min_rationale_chars,
                     limits.MAX_FEEDBACK_TEXT_CHARS)
        _bounded_int(self.max_notes_chars, "max_notes_chars", 1, limits.MAX_FEEDBACK_TEXT_CHARS)

    @property
    def requires_media(self) -> bool:
        return any(criterion.requires_media for criterion in self.criteria)

    def criteria_for(self, *, media: bool) -> tuple[Criterion, ...]:
        """What may be scored. Without media the media-dependent criteria are gone -
        not optional, gone: an absent groundedness score is honest, an invented one is
        the failure 02 forbids."""
        return tuple(c for c in self.criteria if media or not c.requires_media)

    def required_keys(self, *, media: bool) -> frozenset[str]:
        return frozenset({c.name for c in self.criteria_for(media=media)}) | RESERVED_NAMES

    def passes(self, scores: Mapping[str, int], *, media: bool) -> bool:
        """The rubric's pass rule (`06` §3.3), with R56's floor underneath it.

        R56, in code: when the rubric has **any** media-dependent criterion, a
        media-less evaluation can never pass - not because of the thresholds, which a
        future rubric edit could set to `None`, but because nothing graded the frames.
        Separately, a criterion in the rule with no score fails it.
        """
        if not media and self.requires_media:
            return False
        for criterion in self.criteria:
            if criterion.pass_at is None:
                continue
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
    """An accepted result. `limited` is carried, not derived at read time, so a console
    tile cannot lose the fact that no frames were graded; `media_required` is carried
    with it so R56's floor is checkable without the rubric in hand."""

    run_id: str
    sample_id: str
    rubric_version: int
    scores: tuple[Score, ...]
    overall_pass: bool
    notes: str
    limited: bool
    media_required: bool

    def __post_init__(self) -> None:
        # R56, enforced in code at the record too: whoever constructs scores directly
        # (J3's report, a projection replay) cannot assemble a no-media pass.
        if self.limited and self.overall_pass and self.media_required:
            raise ValueError("a limited evaluation of a media-dependent rubric never passes")

    @property
    def accepted(self) -> bool:
        return True

    @property
    def by_name(self) -> dict[str, int]:
        return {score.name: score.score for score in self.scores}


@dataclass(frozen=True)
class Rejected:
    """A refused result, with a **bounded** reason kept. `06` §3.5 stores the error on
    the item so a schema regression is a query rather than a silent gap in the scores -
    which means the detail is a log field and must not be able to carry a 5 MB key."""

    run_id: str
    sample_id: str
    rubric_version: int
    reason: str
    detail: str

    def __post_init__(self) -> None:
        # R2-B3: **total** length at most MAX_DETAIL_CHARS. The old cap produced 203,
        # because the ellipsis was added after the slice rather than inside the budget.
        if len(self.detail) > MAX_DETAIL_CHARS:
            object.__setattr__(self, "detail",
                               self.detail[:MAX_DETAIL_CHARS - 3] + "...")

    @property
    def accepted(self) -> bool:
        return False


Result = JudgeScores | Rejected


def dedupe_key(result: Result) -> tuple[str, str, int]:
    """02: late results deduplicate by run, sample **and rubric version**."""
    return (result.run_id, result.sample_id, result.rubric_version)


class DuplicateKey(ValueError):
    """A JSON object with the same key twice: `json.loads` keeps the last silently."""


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise DuplicateKey(f"a key appears twice ({describe(key)})")
        seen.add(key)
    return dict(pairs)


def parse_judge_json(text: str | bytes) -> object:
    """Parse one judge result. Raises `DuplicateKey` / `ValueError` on bad input.

    `json.loads` resolves `{"score": 1, "score": 5}` to the *last* value without a
    word, so a payload could carry two scores and we would store whichever the parser
    preferred. `object_pairs_hook` makes that a refusal.
    """
    return json.loads(text, object_pairs_hook=_no_duplicate_keys)


def validate_json(rubric: Rubric, text: str | bytes, *, run_id: str, sample_id: str,
                  media_available: bool) -> Result:
    """`parse_judge_json` then `validate_output`, never raising for either.

    `RecursionError` is caught by name because it is a `RuntimeError`, not a
    `ValueError`: `"["*10000 + "]"*10000` is a 20 KB payload that blew straight through
    the `(ValueError, TypeError)` clause and out of J2's untrusted-input entry point.
    """
    def reject(reason: str, detail: str) -> Rejected:
        return Rejected(run_id=run_id, sample_id=sample_id, rubric_version=rubric.version,
                        reason=reason, detail=detail)

    try:
        payload = parse_judge_json(text)
    except DuplicateKey as exc:
        return reject("duplicate_key", str(exc))
    except RecursionError:
        # Deliberately not re-raised and deliberately not rendered: the stack is nearly
        # exhausted, so the handler does the least work it can.
        return reject("too_deeply_nested", "the result is nested too deeply to parse")
    except json.JSONDecodeError as exc:
        # `msg` is one of CPython's fixed strings and `pos` is an offset, so the detail
        # says what went wrong and where without echoing a byte of the document.
        return reject("malformed_json", f"{exc.msg} at position {exc.pos}")
    except (ValueError, TypeError) as exc:
        return reject("malformed_json", f"unparsable input ({type(exc).__name__})")
    return validate_output(rubric, payload, run_id=run_id, sample_id=sample_id,
                           media_available=media_available)


def validate_output(rubric: Rubric, payload: object, *, run_id: str, sample_id: str,
                    media_available: bool) -> Result:
    """One judge result against one rubric version. Never raises on bad input."""
    limited = not media_available

    def reject(reason: str, detail: str) -> Rejected:
        return Rejected(run_id=run_id, sample_id=sample_id, rubric_version=rubric.version,
                        reason=reason, detail=detail)

    # Exact types throughout, not `isinstance`: a `dict` subclass whose `__iter__` or
    # `keys` raises would otherwise get past the door of a function documented never to
    # raise, and `True` is not a score of 1. JSON only ever produces the exact types.
    if type(payload) is not dict:
        return reject("not_an_object",
                      f"a judge result is a JSON object, not {type(payload).__name__}")
    unnamed = [key for key in payload if type(key) is not str]
    if unnamed:
        # Not reachable from JSON, but `validate_output` also takes a dict built in
        # process, and a non-string key made the key-set arithmetic raise `TypeError`
        # out of a function documented never to raise.
        return reject("non_string_key", f"keys must be text, not {describe(unnamed[0])}")
    keys = set(payload)
    scorable = rubric.criteria_for(media=media_available)
    if limited:
        forbidden = sorted(keys & {c.name for c in rubric.criteria if c.requires_media})
        if forbidden:
            # 02: no media means no groundedness pass. A score for it is not a rounding
            # error, it is a claim about frames that were never sent.
            return reject("media_dependent_score_without_media",
                          f"{', '.join(forbidden)} cannot be scored without media")
    required = rubric.required_keys(media=media_available)
    missing = sorted(required - keys)
    if missing:
        return reject("missing_field", f"missing {', '.join(missing)}")
    unexpected = sorted(keys - required)
    if unexpected:
        return reject("unexpected_field",
                      f"{len(unexpected)} unexpected key(s), the first a "
                      f"{describe(unexpected[0])}")

    scores: list[Score] = []
    for criterion in scorable:
        entry = payload[criterion.name]
        if type(entry) is not dict or set(entry) != {"score", "rationale"}:
            return reject("malformed_criterion",
                          f"{criterion.name} needs exactly a score and a rationale")
        score = entry["score"]
        if type(score) is not int:
            # `True` is not 1 and `4.0` is not 4: a float score would round into the
            # projection and a boolean would compare as one.
            return reject("score_not_an_integer", f"{criterion.name} score must be an integer")
        if not criterion.min_score <= score <= criterion.max_score:
            return reject("score_out_of_range",
                          f"{criterion.name} score ({describe(score)}) is outside "
                          f"{criterion.min_score}..{criterion.max_score}")
        rationale = entry["rationale"]
        if type(rationale) is not str or not rationale.strip():
            return reject("rationale_missing", f"{criterion.name} needs a rationale")
        # r1 R54: bounds count Unicode code points, which is what `len` counts here.
        if not rubric.min_rationale_chars <= len(rationale) <= rubric.max_rationale_chars:
            return reject("rationale_out_of_bounds",
                          f"{criterion.name} rationale is {len(rationale)} characters, not "
                          f"{rubric.min_rationale_chars}..{rubric.max_rationale_chars}")
        if not is_storable_text(rationale):
            return reject("unstorable_text", f"{criterion.name} rationale is not valid UTF-8")
        scores.append(Score(name=criterion.name, score=score, rationale=rationale))

    notes = payload[NOTES]
    if type(notes) is not str or len(notes) > rubric.max_notes_chars:
        return reject("notes_out_of_bounds",
                      f"notes must be text of at most {rubric.max_notes_chars} characters")
    if not is_storable_text(notes):
        return reject("unstorable_text", "notes are not valid UTF-8")
    claimed = payload[OVERALL_PASS]
    if type(claimed) is not bool:
        return reject("overall_pass_not_a_boolean", "overall_pass must be a boolean")
    by_name = {score.name: score.score for score in scores}
    expected = rubric.passes(by_name, media=media_available)
    if claimed != expected:
        # The pass rule belongs to the rubric version, not to the model. Storing the
        # model's claim would let a prompt change silently move the pass bar - and on a
        # limited evaluation it is how a no-media pass gets written (02).
        return reject("overall_pass_inconsistent",
                      f"overall_pass {claimed} contradicts rubric {rubric.rubric_id} "
                      f"v{rubric.version}")
    try:
        return JudgeScores(run_id=run_id, sample_id=sample_id, rubric_version=rubric.version,
                           scores=tuple(scores), overall_pass=claimed, notes=notes,
                           limited=limited, media_required=rubric.requires_media)
    except ValueError as exc:
        # The record's own invariants (R56's no-media floor) are the last line, and a
        # function documented never to raise has to answer with a rejection even when they
        # fire - otherwise a checked-twice invariant becomes a 500 on the collect path.
        return reject("inconsistent_result", str(exc))


class ScoreLedger:
    """One run's collected results, deduped by `dedupe_key` and bounded by the plan.

    The provider may deliver a batch twice (a re-collect, a replayed outbox row), so
    the *first* outcome for a key wins and a later delivery neither overwrites it nor
    adds a second row. A rejection is an outcome too: re-delivering the same malformed
    result must not turn into two error rows.

    A ledger belongs to one `(run_id, rubric_version)` and to that run's planned sample
    ids, so a result for another run, another rubric version or a trace the plan never
    selected is refused and stored nowhere - which is both the tenant-safety property
    and the bound: at most one row per planned sample.
    """

    def __init__(self, run_id: str, rubric_version: int, sample_ids: Iterable[str]) -> None:
        self.run_id = run_id
        self.rubric_version = rubric_version
        self.sample_ids = frozenset(sample_ids)
        self._by_key: dict[tuple[str, str, int], Result] = {}

    def _unexpected(self, result: Result, reason: str, detail: str) -> tuple[bool, Rejected]:
        return False, Rejected(run_id=result.run_id, sample_id=result.sample_id,
                               rubric_version=result.rubric_version, reason=reason, detail=detail)

    def deliver(self, result: Result) -> tuple[bool, Result]:
        """`(first, stored)`. `first` is False for a duplicate or a refused delivery."""
        if result.run_id != self.run_id:
            return self._unexpected(result, "unexpected_run", f"this ledger is run {self.run_id}")
        if result.rubric_version != self.rubric_version:
            return self._unexpected(result, "unexpected_rubric_version",
                                    f"this ledger is rubric version {self.rubric_version}")
        if result.sample_id not in self.sample_ids:
            return self._unexpected(result, "unexpected_sample",
                                    "not a planned sample of this run")
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
