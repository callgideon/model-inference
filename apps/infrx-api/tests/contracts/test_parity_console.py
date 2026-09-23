#!/usr/bin/env python3
"""G0 parity: the Python and console halves of contracts v1 agree.

Coordinator-owned (research/plan/08-contracts-v1-encoding.md §3, §4, R11, R25).
Reads the console sources by relative path; no Node needed. A literal the
parser cannot find is a failure, never a skip: a renamed constant must break
this test, not silently stop being compared.

    uv run --frozen pytest -q tests/contracts/test_parity_console.py
"""
import json
import re
import types
import typing

import pytest
from pathlib import Path

from infrx.contracts import fixtures, limits, money, records, wire

API = Path(__file__).resolve().parents[2]            # apps/infrx-api
CONSOLE = API.parent / "app"
TYPES = CONSOLE / "lib" / "contracts" / "types.ts"
FIXTURES = API / "infrx" / "contracts" / "fixtures" / "v1"

# console constant -> Python vocabulary; every one both halves declare. The value is
# an enum, or an ordered tuple where the Python side is a subset of an enum.
SHARED_ENUMS = {
    "JOB_STATES": records.JobState,
    "EXECUTION_MODES": records.ExecutionMode,
    "TERMINAL_CAUSES": records.TerminalCause,
    "USAGE_CERTAINTIES": records.UsageCertainty,
    "SETTLEMENT_STATES": records.SettlementState,
    "TRACE_MODES": records.TraceMode,
    "TRACE_LOSS_REASONS": records.TraceLossReason,
    "FEEDBACK_CHANNELS": records.FeedbackChannel,
    "AUTHOR_ROLES": records.AuthorRole,
    # r1 R43: the console splits the submittable names from the stored entry names, and
    # so does Python. Both halves are compared, because the split is the thing that
    # makes "a client cannot author an operator label" structural.
    "FEEDBACK_NAMES": records.FEEDBACK_INPUT_NAMES,
    "FEEDBACK_ENTRY_NAMES": records.FeedbackName,
    "CALIBRATION_LABELS": records.CalibrationLabel,
    "JUDGE_RUN_STATES": records.JudgeRunState,
    "ROLES": records.Role,
    # 08 §9's closed availability set, which Python spells `ContentState`.
    "TRACE_CONTENT_AVAILABILITY": records.ContentState,
    "ENTITLEMENT_LIMIT_NAMES": limits.ENTITLEMENT_LIMIT_NAMES,
    # D1's `usage_events.settlement_regime` is `legacy`/`pilot`; both halves spell the
    # regime `legacy_usd`/`pilot` (F2R coordinator addition 9).
    "ACCOUNTING_REGIMES": records.AccountingRegime,
}


def ts_string_array(source, name, _seen=()):
    """The literal values of an `as const` array, expanding `...OTHER` in place.

    A spread used to be refused outright, which meant the console could hide a
    vocabulary from this test simply by composing it. Expanding it keeps the
    comparison exact while letting the console declare one list in terms of another;
    a spread of something that is not itself a literal array still fails.
    """
    m = re.search(rf"export const {name}\b[^=]*=\s*\[(.*?)\]\s*as const", source, re.S)
    assert m, f"{name}: literal array not found in {TYPES}"
    values = []
    for token in re.findall(r'"([^"]+)"|\.\.\.([A-Z_][A-Z0-9_]*)', m.group(1)):
        literal, spread = token
        if literal:
            values.append(literal)
            continue
        assert spread not in _seen, f"{name}: spread cycle through {spread}"
        values.extend(ts_string_array(source, spread, _seen + (name,)))
    assert values, f"{name}: no values parsed"
    return values


def ts_number(source, name):
    """One exported numeric constant. A renamed or non-numeric constant fails."""
    m = re.search(rf"export const {name}\s*(?::[^=]+)?=\s*([0-9_]+)\s*;", source)
    assert m, f"{name}: numeric constant not found in {TYPES}"
    return int(m.group(1).replace("_", ""))


def ts_status_table(source):
    """The whole `ERROR_CODE_HTTP_STATUS` table, or a failure naming the line.

    A row the parser cannot read used to be dropped silently, so a quoted key
    (`"not_found": 404`) or a numeric separator would have removed a code from the
    comparison rather than breaking it. Every non-blank, non-comment line inside the
    object must now parse.
    """
    m = re.search(r"export const ERROR_CODE_HTTP_STATUS\b[^=]*=\s*\{(.*?)\n\}", source, re.S)
    assert m, f"ERROR_CODE_HTTP_STATUS not found in {TYPES}"
    row = re.compile(r"""^\s*(?:"([a-z_]+)"|'([a-z_]+)'|([a-z_]+))\s*:\s*"""
                     r"""(null|[0-9][0-9_]*)\s*,?\s*(?://.*)?$""")
    table = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.strip().startswith(("//", "/*", "*")):
            continue
        parsed = row.match(line)
        assert parsed, f"ERROR_CODE_HTTP_STATUS: cannot parse {line.strip()!r}"
        code = parsed.group(1) or parsed.group(2) or parsed.group(3)
        status = parsed.group(4)
        assert code not in table, f"ERROR_CODE_HTTP_STATUS: {code} declared twice"
        table[code] = None if status == "null" else int(status.replace("_", ""))
    assert table, "ERROR_CODE_HTTP_STATUS: no entries parsed"
    return table


def test_shared_vocabularies_are_identical():
    source = TYPES.read_text(encoding="utf-8")
    for name, vocabulary in SHARED_ENUMS.items():
        expected = [getattr(value, "value", value) for value in vocabulary]
        assert ts_string_array(source, name) == expected, name


def test_error_code_tables_are_identical():
    source = TYPES.read_text(encoding="utf-8")
    python = json.loads((FIXTURES / "error_codes.json").read_text(encoding="utf-8"))
    table = ts_status_table(source)
    assert {c: s for c, s in table.items() if s is not None} == {
        c: row["status"] for c, row in python["http"].items()}
    assert sorted(ts_string_array(source, "IN_STREAM_ONLY_CODES")) == sorted(python["in_stream_only"])
    assert sorted(ts_string_array(source, "INTERNAL_ONLY_CODES")) == sorted(python["internal_only"])
    no_status = {c for c, s in table.items() if s is None}
    assert no_status == set(python["in_stream_only"]) | set(python["internal_only"])


def test_money_cases_are_byte_identical():
    console = (CONSOLE / "tests" / "contracts" / "money_cases.json").read_bytes()
    assert console == (FIXTURES / "money_cases.json").read_bytes()


def test_text_bound_cases_are_byte_identical():
    """r1 R54: the third parity table. Both halves read the same boundary strings."""
    console = (CONSOLE / "tests" / "contracts" / "text_bounds.json").read_bytes()
    assert console == (FIXTURES / "text_bounds.json").read_bytes()


def test_python_classifies_every_text_bound_case_by_code_points():
    """r1 R54: `len()` counts code points, and the table says what that means for each
    case. The console half asserts the same table with `[...text].length`; a half using
    UTF-16 units would disagree on every astral row."""
    cases = json.loads((FIXTURES / "text_bounds.json").read_text(encoding="utf-8"))
    assert cases, "the table must not be empty"
    astral = [case for case in cases if case["utf16_units"] > case["code_points"]]
    assert astral, "the table must contain astral cases, or it proves nothing"
    bounds = {"feedback_text": limits.MAX_FEEDBACK_TEXT_CHARS,
              "key_name": limits.MAX_KEY_NAME_CHARS,
              "grant_reason": limits.MAX_GRANT_REASON_CHARS,
              "idempotency_key": limits.MAX_IDEMPOTENCY_KEY_CHARS}
    for case in cases:
        assert bounds[case["bound"]] == case["limit"], case["bound"]
        assert len(case["text"]) == case["code_points"], case
        assert (len(case["text"]) <= case["limit"]) is case["within"], case


# --- shared numeric bounds (R17, R43) -------------------------------------------
# Every number both halves enforce. A bound one language widened would otherwise be a
# 400 on one side and an accepted document on the other, which is how a 4000-character
# rule becomes advisory. `money.MAX_VALUE` is compared through its digit count because
# TypeScript states the domain as a character bound on the string form.
SHARED_NUMBERS = {
    "MAX_IDEMPOTENCY_KEY_CHARS": limits.MAX_IDEMPOTENCY_KEY_CHARS,
    "MAX_PAGE_LIMIT": limits.MAX_PAGE_LIMIT,
    "MAX_KEY_NAME_CHARS": limits.MAX_KEY_NAME_CHARS,
    "MAX_GRANT_REASON_CHARS": limits.MAX_GRANT_REASON_CHARS,
    "MAX_FEEDBACK_TEXT_CHARS": limits.MAX_FEEDBACK_TEXT_CHARS,
    "MAX_RUBRIC_VERSION": limits.MAX_RUBRIC_VERSION,
    "MAX_CONTENT_RETENTION_DAYS": limits.MAX_CONTENT_RETENTION_DAYS,
    "MAX_ENTITLEMENT_LIMIT": limits.MAX_ENTITLEMENT_LIMIT,
    "FEEDBACK_RATING_MIN": 1,
    "FEEDBACK_RATING_MAX": 5,
}


def test_shared_numeric_bounds_are_identical():
    source = TYPES.read_text(encoding="utf-8")
    for name, expected in SHARED_NUMBERS.items():
        assert ts_number(source, name) == expected, name


def test_the_records_enforce_the_shared_bounds():
    """The numbers above are not decoration: the Python records refuse past them, so a
    parity match means both halves reject the same inputs rather than agreeing on a
    constant neither reads."""
    feedback = records.Feedback.model_validate(
        json.loads((FIXTURES / "feedback.json").read_text(encoding="utf-8")))
    for field, length in (("value", limits.MAX_FEEDBACK_TEXT_CHARS + 1),
                          ("comment", limits.MAX_FEEDBACK_TEXT_CHARS + 1)):
        try:
            feedback.model_copy(update={field: "x" * length}).model_validate(
                {**feedback.model_dump(mode="json"), field: "x" * length})
        except Exception:
            pass
        else:
            raise AssertionError(f"Feedback accepted {length} characters of {field}")
    for version in (0, limits.MAX_RUBRIC_VERSION + 1):
        label = json.loads((FIXTURES / "feedback_calibration_label.json").read_text("utf-8"))
        try:
            records.Feedback.model_validate({**label, "rubric_version": version})
        except Exception:
            pass
        else:
            raise AssertionError(f"rubric_version {version} was accepted")
    consent = json.loads((FIXTURES / "consent_snapshot.json").read_text(encoding="utf-8"))
    for days in (0, limits.MAX_CONTENT_RETENTION_DAYS + 1):
        try:
            records.ConsentSnapshot.model_validate({**consent, "content_retention_days": days})
        except Exception:
            pass
        else:
            raise AssertionError(f"content_retention_days {days} was accepted")
    long_key = "k" * (limits.MAX_IDEMPOTENCY_KEY_CHARS + 1)
    idem = json.loads((FIXTURES / "idempotency_ref.json").read_text(encoding="utf-8"))
    try:
        records.IdempotencyRef.model_validate({**idem, "key": long_key})
    except Exception:
        pass
    else:
        raise AssertionError("an over-long idempotency key was accepted")


# --- record-field parity ---------------------------------------------------------
# Chosen over exporting a JSON shape manifest from the console tests: a manifest is a
# generated artefact that can go stale between the type and the file it was generated
# from, and nothing would notice. Parsing the `export type X = {...}` literal compares
# against the type the console actually compiles, and the parser below **fails** on any
# member it cannot read rather than skipping it, which is the property that matters.
#
# Each pair lists only the fields both halves carry, with the reason for every field
# left out, so a new field on either side has to be triaged here instead of drifting.
RECORD_PAIRS = {
    "FeedbackEntry": {
        "model": records.Feedback,
        "fields": {"id": "feedback_id", "request_id": "request_id", "created_at": "created_at",
                   "channel": "channel", "author_role": "author_role",
                   "author_principal": "author_principal", "name": "name", "value": "value",
                   "comment": "comment", "calibration_set": "calibration_set",
                   "rubric_version": "rubric_version"},
        # `org_id` is the tenant binding a console session already carries in its
        # `SessionContext`, so the DTO never repeats it; `schema_version` is on the
        # persisted record, not on a rendered row; `by_operator` is R50's server-set
        # marker, which **never leaves either service** (asserted below against every
        # wire model and against the console DTO).
        "python_only": {"org_id", "schema_version", "by_operator"},
        "console_only": set(),
    },
    "OrgEntitlements": {
        "model": records.OrgEntitlements,
        "fields": {"org_id": "org_id", "model_ids": "model_ids", "limits": "limits",
                   "updated_at": "updated_at", "updated_by": "updated_by"},
        "python_only": {"schema_version"},
        "console_only": set(),
    },
    "JudgeRun": {
        "model": records.JudgeRun,
        # The console row is a *projection* of the run plus its samples, so only the
        # fields both halves carry are compared; the rest are named below.
        "fields": {"id": "run_id", "created_at": "created_at", "state": "state",
                   "rubric_version": "rubric_version",
                   "external_batch_id": "external_batch_id"},
        # `consent`/`sample_ids`/`submit_intent`/`reconciled_at` are durable run state J
        # owns; `org_id` is the session's; costs are projected as `budget_*` below.
        "python_only": {"schema_version", "org_id", "sample_ids", "consent", "model_revision",
                        "reserved_cost", "actual_cost", "submit_intent", "reconciled_at"},
        # `mode`, `judge_model*`, the counts, `samples` and `quarantine_reason` are J's
        # projection; `budget_reserved`/`budget_settled` are `reserved_cost`/`actual_cost`
        # renamed for the console and are compared by money type, not by name.
        "console_only": {"mode", "judge_model", "judge_model_version", "sample_count",
                         "limited_evaluation_count", "budget_reserved", "budget_settled",
                         "consent_snapshot_at", "quarantine_reason", "samples"},
    },
    "TraceContentBody": {
        "model": wire.TraceContentBody,
        "fields": {"v": "v", "request": "request", "response": "response"},
        "python_only": set(),
        "console_only": set(),
    },
}

# Console DTOs with no Python counterpart, recorded so "not compared" is a decision
# rather than an oversight. `WalletBalance` is derived by the console from the wallet
# columns (06) and has no record in `records.py`; `TraceListItem` is a ClickHouse
# projection T owns, whose Python side is a query result rather than a contract record.
# r1 R50: server-set markers that must never leave *either* service. They live on the
# persisted record and on the console's stored shape, and on neither DTO.
INTERNAL_MARKERS = ("by_operator",)

UNPAIRED_CONSOLE_TYPES = ("WalletBalance", "TraceListItem",
                          # r2 (F2R item 6): the console rows whose nullability the wave-2 audit
                          # found wrong (A07). They are read models over D1 views rather than
                          # records, so there is nothing in `records.py` to compare them with -
                          # `test_the_legacy_nullable_console_fields_are_frozen` pins the fields
                          # that decision turns on instead.
                          "UsageRow", "ApiKeySummary", "AuditEntry", "JudgeSample")

# The nullability the legacy projection turns on, field by field. A field that stops being nullable
# here starts rejecting real D1 history again (the A07 defect), and a field that *becomes* nullable
# without a decision would let a projection hand the console a null it does not handle. `False`
# means "must NOT be nullable": `accounting_regime` is the field that says which rules apply, so a
# null there would make the whole regime distinction unreadable.
LEGACY_NULLABLE_CONSOLE_FIELDS = {
    "UsageRow": {"key_id": True, "key_name": True, "accounting_regime": False,
                 "execution_mode": True, "job_state": True, "usage_certainty": True,
                 "trace_mode": True, "settlement_state": True, "max_hold": True,
                 "request_id": False, "created_at": False, "http_status": False, "cost": False},
    "ApiKeySummary": {"trace_mode": True, "id": False, "name": False},
    "AuditEntry": {"target_org_id": True, "actor_principal": False, "action": False},
    "JudgeRun": {"judge_model_version": True, "consent_snapshot_at": True,
                 "judge_model": False, "sample_count": False, "rubric_version": False},
    # Frozen to D1's `console_judge_runs` shape and nothing else: the field *set* is the ruling.
    "JudgeSample": {"sample_id": False, "rubric_version": False, "request_id": True,
                    "scores": False},
}


class _TsField(typing.NamedTuple):
    nullable: bool
    kind: str


def ts_type_fields(source, name):
    """`{field: nullable}` for an `export type NAME = { ... }` object literal.

    Brace-matched rather than regex-terminated, and every non-comment member must parse:
    a member this cannot read is a failure, because silently dropping one is how a field
    stops being compared without anybody deciding that it should.
    """
    head = re.search(rf"export type {name}\s*=\s*\{{", source)
    assert head, f"{name}: object type literal not found in {TYPES}"
    depth, start = 0, head.end() - 1
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                body = source[start + 1:index]
                break
    else:
        raise AssertionError(f"{name}: unbalanced braces")
    # strip comments, then split members on top-level `;`
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    members, depth, current = [], 0, ""
    for char in body:
        if char in "{[(<":
            depth += 1
        elif char in "}])>":
            depth -= 1
        if char == ";" and depth == 0:
            members.append(current)
            current = ""
        else:
            current += char
    assert not current.strip(), f"{name}: trailing member {current.strip()!r} has no `;`"
    fields = {}
    for member in members:
        if not member.strip():
            continue
        parsed = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)(\?)?\s*:\s*(.+)\s*$", member, re.S)
        assert parsed, f"{name}: cannot parse member {member.strip()!r}"
        field, optional, kind = parsed.group(1), parsed.group(2), parsed.group(3)
        # `null` has to be a *top-level* alternative: `response: { error: … | null }` is
        # not a nullable response, and taking the whole type text as one string said it
        # was. Nested groups are removed before the union is read.
        fields[field] = _TsField(nullable=bool(optional)
                                 or bool(re.search(r"\bnull\b", _strip_groups(kind))),
                                 kind=" ".join(kind.split()))
    assert fields, f"{name}: no members parsed"
    return fields


# r1 R54 / the original B1 defect class: a field whose **type** changed is the drift that
# actually bit - `rubric_version` was a string in Python and a number in the console, and a
# comparison of names and nullability could not see it. Each entry says which TypeScript
# types a Python annotation is allowed to mean.
TYPE_EQUIVALENTS = {
    # The console spells the *entry* vocabulary `FeedbackEntryName` (R43's stored-name
    # set); Python's `FeedbackName` **is** that set, with `FEEDBACK_INPUT_NAMES` as the
    # submittable subset. `test_shared_vocabularies_are_identical` compares the values.
    "FeedbackName": ("FeedbackEntryName",),
    # An inline object literal against a nested wire model: the members are compared
    # field for field by `test_the_trace_content_body_matches_member_for_member`.
    "TraceContentRequest": ("object",),
    "TraceContentResponse": ("object",),
    # `FeedbackValue` is the console's alias for exactly `boolean | number | string`,
    # which is what `bool | int | str` means; the alias's own definition is pinned below.
    "bool": ("boolean", "FeedbackValue"),
    "int": ("number", "FeedbackValue"),
    "str": ("string", "FeedbackValue"),
    "StrictInt": ("number",),
    "float": ("number",),
    "datetime": ("string",),          # RFC 3339 text on the wire
    "Decimal": ("Money", "string"),   # branded money string
    "Literal[1]": ("1",),
}


def _python_type_names(annotation) -> set[str]:
    """The non-`None` members of an annotation, by name.

    Origin first, then args: `dict[str, StrictInt]` is a *mapping*, not "str and
    StrictInt", and reading the args before the origin reported the latter.
    """
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        names: set[str] = set()
        for member in typing.get_args(annotation):
            if member is not type(None):
                names |= _python_type_names(member)
        return names
    if origin in (tuple, list, set, frozenset):
        return {"sequence"}
    if origin is dict:
        return {"mapping"}
    if origin is typing.Literal:
        return {str(value) for value in typing.get_args(annotation)}
    if hasattr(annotation, "__metadata__"):          # Annotated[T, ...]
        return _python_type_names(annotation.__origin__)
    return {getattr(annotation, "__name__", str(annotation))}


def _ts_type_members(kind: str) -> set[str]:
    """Top-level union members of a TypeScript type, `null` removed."""
    stripped = _strip_groups(kind).strip()
    if not stripped or stripped in {"", "|"}:
        return {"object"}
    members = {part.strip() for part in stripped.split("|") if part.strip()}
    members.discard("null")
    return {m.removesuffix("[]") if m.endswith("[]") else m for m in members} or {"object"}


def _strip_groups(text):
    """The type text with every nested `{...}`, `[...]`, `(...)` and `<...>` removed."""
    out, depth = [], 0
    for char in text:
        if char in "{[(<":
            depth += 1
        elif char in "}])>":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return "".join(out)


def python_field_nullability(model):
    """`{field: nullable}` from pydantic, where nullable means "may be None".

    Read through `typing.get_args` rather than off the repr: `str | None` stringifies as
    `"str | None"`, so a `"NoneType" in str(annotation)` test called every optional field
    non-null and the whole comparison passed by accident.
    """
    nullable = {}
    for field, info in model.model_fields.items():
        annotation = info.annotation
        args = typing.get_args(annotation)
        nullable[field] = annotation is type(None) or type(None) in args
    return nullable


@pytest.mark.parametrize("name", sorted(RECORD_PAIRS))
def test_record_fields_and_nullability_match(name):
    source = TYPES.read_text(encoding="utf-8")
    pair = RECORD_PAIRS[name]
    console = ts_type_fields(source, name)
    python = python_field_nullability(pair["model"])
    mapping = pair["fields"]
    # every field on either side is either mapped or deliberately excused
    assert set(console) - set(mapping) == pair["console_only"], \
        f"{name}: console fields nobody triaged: {sorted(set(console) - set(mapping) - pair['console_only'])}"
    assert set(python) - set(mapping.values()) == pair["python_only"], \
        f"{name}: Python fields nobody triaged: " \
        f"{sorted(set(python) - set(mapping.values()) - pair['python_only'])}"
    for ts_field, py_field in mapping.items():
        assert ts_field in console, f"{name}.{ts_field} is gone from the console type"
        assert py_field in python, f"{pair['model'].__name__}.{py_field} is gone"
        assert console[ts_field].nullable == python[py_field], \
            (f"{name}.{ts_field} is {'nullable' if console[ts_field].nullable else 'non-null'} "
             f"in TypeScript but {'nullable' if python[py_field] else 'non-null'} as "
             f"{pair['model'].__name__}.{py_field}")
        # and the **type**, which is the drift that actually bit: `rubric_version` was a
        # string here and a number there, and names plus nullability could not see it.
        annotation = pair["model"].model_fields[py_field].annotation
        python_names = _python_type_names(annotation)
        ts_members = _ts_type_members(console[ts_field].kind)
        if {"sequence", "mapping"} & python_names:
            continue            # containers: compared by name and nullability only
        # A TypeScript member is explained by an equivalent, or by *being* the Python
        # name - the two halves spell the shared enums and branded types identically, and
        # that convention is itself worth pinning.
        allowed = set(python_names)
        for python_name in python_names:
            allowed |= set(TYPE_EQUIVALENTS.get(python_name, ()))
        unexplained = {m for m in ts_members
                       if not any(m == a or m.startswith(a) for a in allowed)}
        assert not unexplained, \
            (f"{name}.{ts_field} is `{console[ts_field].kind}` in TypeScript, which "
             f"{pair['model'].__name__}.{py_field} ({sorted(python_names)}) does not explain")


@pytest.mark.parametrize("name", sorted(LEGACY_NULLABLE_CONSOLE_FIELDS))
def test_the_legacy_nullable_console_fields_are_frozen(name):
    """r2 (F2R item 6): the nullability D1's real history needs, pinned field by field."""
    fields = ts_type_fields(TYPES.read_text(encoding="utf-8"), name)
    for field, nullable in LEGACY_NULLABLE_CONSOLE_FIELDS[name].items():
        assert field in fields, f"{name}.{field} is gone"
        assert fields[field].nullable is nullable, \
            f"{name}.{field} nullability changed: {fields[field].kind}"


def test_the_judge_sample_carries_exactly_d1s_field_set():
    """The frozen shape of `console_judge_runs`. A richer sample is one no query can produce, so
    the console would depend on something only a fake could fill."""
    fields = ts_type_fields(TYPES.read_text(encoding="utf-8"), "JudgeSample")
    assert set(fields) == set(LEGACY_NULLABLE_CONSOLE_FIELDS["JudgeSample"])


@pytest.mark.parametrize("name", UNPAIRED_CONSOLE_TYPES)
def test_the_unpaired_console_types_still_exist(name):
    """They are not compared, but they are not forgotten: if one of them gains a Python
    record it should be added to `RECORD_PAIRS`, and if it is renamed this says so."""
    assert ts_type_fields(TYPES.read_text(encoding="utf-8"), name)


def test_the_money_domain_is_the_same_in_both_languages():
    """08 §4: scale 1e-8 and `numeric(20, 8)`. TypeScript states the domain as a
    character bound on the fixed-point string; Python as a digit count, so the two are
    compared by construction rather than by eye."""
    source = (CONSOLE / "lib" / "contracts" / "money.ts").read_text(encoding="utf-8")
    assert ts_number(source, "MONEY_SCALE") == -money.SCALE.as_tuple().exponent == 8
    # "-" + 12 integer digits + "." + 8 fractional digits = 22 characters
    integer_digits = len(str(int(money.MAX_VALUE))) - 1
    assert ts_number(source, "MAX_MONEY_CHARS") == 1 + integer_digits + 1 + 8
    assert money.MAX_VALUE == 10 ** integer_digits


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


@pytest.mark.parametrize("marker", INTERNAL_MARKERS)
def test_an_internal_marker_is_on_no_public_shape(marker):
    """r1 R50: `by_operator` records that a platform operator acted. A customer may learn
    that the platform acted - the principal reads `platform` - and never more, so the
    marker is absent from every wire model, every wire fixture and the console DTOs."""
    source = TYPES.read_text(encoding="utf-8")
    assert marker in records.Feedback.model_fields, f"{marker} must be a persisted field"
    for name, model in sorted(fixtures.MODELS.items()):
        if not model.__module__.endswith("contracts.wire"):
            continue
        assert marker not in model.model_fields, f"{model.__name__} declares {marker}"
        assert marker not in (FIXTURES / name).read_text(encoding="utf-8"), \
            f"{name} carries {marker}"
    assert marker not in wire.FeedbackEntry.model_fields, "the public entry declares it"
    for dto in ("FeedbackEntry", "ConsentHistoryEntry", "LedgerEntry"):
        assert marker not in ts_type_fields(source, dto), f"the console {dto} declares it"


def test_the_feedback_value_alias_is_exactly_the_python_union():
    """`FeedbackValue` is allowed to stand for `bool | int | str` above, so what it stands
    for is pinned here rather than assumed."""
    source = TYPES.read_text(encoding="utf-8")
    m = re.search(r"export type FeedbackValue = ([^;]+);", source)
    assert m, "FeedbackValue not found"
    assert {part.strip() for part in m.group(1).split("|")} == {"boolean", "number", "string"}


def test_more_shared_vocabularies_and_constants():
    """The cheap parity the S1 follow-up asked for: `PLATFORM_ACTOR`, judge modes and the
    ledger kinds, all of which one half could rename without the other noticing."""
    source = TYPES.read_text(encoding="utf-8")
    m = re.search(r'export const PLATFORM_ACTOR = "([^"]+)";', source)
    assert m, "PLATFORM_ACTOR not found"
    assert m.group(1) == records.PLATFORM_ACTOR == wire.PLATFORM_ACTOR
    assert ts_string_array(source, "JUDGE_MODES") == list(limits.JUDGE_MODES)
    # `purchase` is legacy and read-only (R13): every kind must render, and the creatable
    # set deliberately excludes it.
    kinds = ts_string_array(source, "LEDGER_ENTRY_KINDS")
    creatable = ts_string_array(source, "CREATABLE_LEDGER_ENTRY_KINDS")
    assert set(creatable) < set(kinds) and "purchase" in set(kinds) - set(creatable)
    assert kinds == ["grant", "usage", "adjustment", "purchase"]


def test_the_trace_content_body_matches_member_for_member():
    """The `v: 1` literal and the nested request/response shapes, not just the three top
    names: a console `v: 2` or a renamed `params` would otherwise pass."""
    source = TYPES.read_text(encoding="utf-8")
    body = ts_type_fields(source, "TraceContentBody")
    assert body["v"].kind == "1", f"the schema literal moved: {body['v'].kind}"
    assert wire.TraceContentBody.model_fields["v"].annotation is typing.Literal[1]
    nested = re.search(r"export type TraceContentBody = \{(.*?)\n\};", source, re.S)
    assert nested, "TraceContentBody literal not found"
    for member, model in (("request", wire.TraceContentRequest),
                          ("response", wire.TraceContentResponse)):
        inner = re.search(rf"\s{member}: \{{(.*?)\n  \}};", nested.group(1), re.S)
        assert inner, f"TraceContentBody.{member} is not an inline object any more"
        names = set(re.findall(r"^\s+([a-z_]+)[?]?:", inner.group(1), re.M))
        assert names == set(model.model_fields), \
            f"TraceContentBody.{member}: {sorted(names)} vs {sorted(model.model_fields)}"


def test_the_v2_vocabulary_has_its_own_parity_module_and_one_seam_here():
    """F2P wire-in item 8. Every v2 vocabulary is compared value by value, in order, by
    `tests/contracts/v2/test_parity_v2.py` (which reuses `ts_string_array` from here); this
    test owns the seam between the two files. `types.ts` carries v2 only as the `v2`
    namespace, and the one constant both revisions declare - `ACCOUNTING_REGIMES` - is
    compared on both sides with its different values, so neither can shadow the other."""
    from infrx.contracts.v2 import money_units
    from .v2 import test_parity_v2
    source = TYPES.read_text()
    assert re.search(r'^export \* as v2 from "\./v2/types\.ts";$', source, re.M)
    v2_source = test_parity_v2.TYPES_SOURCE + test_parity_v2.UNITS_SOURCE
    assert re.search(r'^export \* from "\./money-units\.ts";$', test_parity_v2.TYPES_SOURCE,
                     re.M), "the v2 namespace must carry the unit vocabulary too"
    arrays = r"export const ([A-Z][A-Z0-9_]*)\b[^=]*=\s*\["
    shared = set(re.findall(arrays, source)) & set(re.findall(arrays, v2_source))
    assert shared == {"ACCOUNTING_REGIMES"}, sorted(shared)
    assert ts_string_array(source, "ACCOUNTING_REGIMES") == [r.value for r in records.AccountingRegime]
    assert ts_string_array(test_parity_v2.UNITS_SOURCE, "ACCOUNTING_REGIMES") == \
        list(money_units.ACCOUNTING_REGIMES)
    assert set(test_parity_v2.SHARED_ENUMS) | set(test_parity_v2.UNIT_ENUMS) >= {
        "CREDENTIAL_AUDIENCES", "WALLET_KINDS", "MONEY_UNITS", "ACCOUNTING_REGIMES"}


def test_the_console_audit_actions_are_the_ten_of_0009s_constraint():
    """F2P review HON-1: `AUDIT_ACTIONS` in the console is D1's ten spellings, in the order
    0009's `audit_entries_action_check` lists them - all ten, not only the one the fake
    writes. (The console's own copy of this check carries the mutant, AUDIT-ACTION-02.)"""
    sql = (CONSOLE / "supabase" / "migrations" / "0009_operator_seams.sql").read_text()
    check = re.search(r"add constraint audit_entries_action_check\s+check \(action in \(([^)]*)\)\)",
                      sql)
    assert check, "0009 no longer declares audit_entries_action_check"
    d1 = re.findall(r"'([a-z_]+)'", check.group(1))
    assert len(d1) == 10
    assert ts_string_array(TYPES.read_text(), "AUDIT_ACTIONS") == d1
