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

UNPAIRED_CONSOLE_TYPES = ("WalletBalance", "TraceListItem")


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
        fields[field] = bool(optional) or bool(re.search(r"\bnull\b", _strip_groups(kind)))
    assert fields, f"{name}: no members parsed"
    return fields


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
        assert console[ts_field] == python[py_field], \
            (f"{name}.{ts_field} is {'nullable' if console[ts_field] else 'non-null'} in "
             f"TypeScript but {'nullable' if python[py_field] else 'non-null'} as "
             f"{pair['model'].__name__}.{py_field}")


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
