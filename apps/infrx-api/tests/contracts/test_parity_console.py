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
from pathlib import Path

from infrx.contracts import limits, records

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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
