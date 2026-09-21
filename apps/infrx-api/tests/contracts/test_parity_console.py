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

from infrx.contracts import records

API = Path(__file__).resolve().parents[2]            # apps/infrx-api
CONSOLE = API.parent / "app"
TYPES = CONSOLE / "lib" / "contracts" / "types.ts"
FIXTURES = API / "infrx" / "contracts" / "fixtures" / "v1"

# console constant -> Python enum; every vocabulary both halves declare
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
    "FEEDBACK_NAMES": records.FeedbackName,
    "JUDGE_RUN_STATES": records.JudgeRunState,
    "ROLES": records.Role,
}


def ts_string_array(source, name):
    m = re.search(rf"export const {name}\b[^=]*=\s*\[(.*?)\]\s*as const", source, re.S)
    assert m, f"{name}: literal array not found in {TYPES}"
    body = m.group(1)
    assert "..." not in body, f"{name}: spread is not comparable; declare the literals"
    return re.findall(r'"([^"]+)"', body)


def ts_status_table(source):
    m = re.search(r"export const ERROR_CODE_HTTP_STATUS\b[^=]*=\s*\{(.*?)\n\}", source, re.S)
    assert m, f"ERROR_CODE_HTTP_STATUS not found in {TYPES}"
    rows = re.findall(r"^\s*([a-z_]+):\s*(null|\d+),?\s*(?://.*)?$", m.group(1), re.M)
    assert rows, "ERROR_CODE_HTTP_STATUS: no entries parsed"
    return {code: (None if status == "null" else int(status)) for code, status in rows}


def test_shared_vocabularies_are_identical():
    source = TYPES.read_text(encoding="utf-8")
    for name, enum in SHARED_ENUMS.items():
        assert ts_string_array(source, name) == [e.value for e in enum], name


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
