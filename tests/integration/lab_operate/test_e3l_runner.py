"""E3L layer 1: the runner's own decisions, with no stack (`pytest tests/integration` collects
this file; the scenarios run only through runner.py). Each case names what a broken runner
would report falsely."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _load(name: str, file: str):
    """By path under a unique name: `runner`/`mutants` are also E3C's and E2's module names."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load("e3l_runner", "runner.py")

REPO = HERE.parents[2]


def junit(*cases: tuple[str, str, str]) -> str:
    """(name, kind, message): kind '' pass, 'failure', 'error', 'skipped', 'xfail'."""
    body = []
    for name, kind, message in cases:
        inner = ""
        if kind == "xfail":
            inner = f'<skipped type="pytest.xfail" message="{message}"/>'
        elif kind:
            inner = f'<{kind} message="{message}"/>'
        body.append(f'<testcase classname="x" name="{name}">{inner}</testcase>')
    return f"<testsuites><testsuite>{''.join(body)}</testsuite></testsuites>"


def everything(sid: str, kind: str = "", message: str = "") -> list[tuple[str, str, str]]:
    return [(name, kind, message) for name in runner.REQUIRED[sid]]


def test_e3l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases():
    """The cells are exactly tasks.json E3L's test_ids, each carried by a scenario; the brief's
    cases are all present (two-provider, dev exclusion, registry, publication, discovery,
    rollback, consumer keys, consumer data, CAS faults, control restart)."""
    tasks = json.loads((REPO / "research/plan/tasks.json").read_text())
    e3l = next(task for task in tasks["tasks"] if task["id"] == "E3L")
    assert tuple(e3l["test_ids"]) == runner.TEST_IDS
    for tid in runner.TEST_IDS:
        assert any(tid in spec["test_ids"] for spec in runner.SCENARIOS.values()), tid
    assert set(runner.REQUIRED) == set(runner.SCENARIOS)
    assert len(runner.SCENARIOS) == 12


def test_e3l_the_required_cases_are_exactly_what_the_scenario_modules_define():
    """A renamed or deleted case would leave its scenario judged on what remains."""
    defined = {name for path in HERE.glob("scenarios_*.py")
               for name in re.findall(r"^def (test_l\d\d_\w+)\(", path.read_text(), re.M)}
    assert defined == {name for names in runner.REQUIRED.values() for name in names}


def test_e3l_a_skip_is_never_a_pass_and_names_its_kind():
    result = runner.classify(junit(
        *everything("l01"),
        *everything("l03", "skipped", "NOT RUN[L3] waits; rerun after the merge: x --only l03"),
        *everything("l07", "skipped", "INVALID[premise] no box"),
        *everything("l08", "skipped", "BLOCKED[E2C] no stack"),
        *everything("l11", "xfail", "flaky")))
    status = {sid: entry["status"] for sid, entry in result.items()}
    assert (status["l01"], status["l03"], status["l07"], status["l08"], status["l11"]) == \
        ("PASS", "NOT RUN", "INVALID", "BLOCKED", "NOT RUN")
    assert status["l02"] == "NOT RUN", "a scenario with no case in the report is NOT RUN"


def test_e3l_a_scenario_missing_a_required_case_is_not_run():
    result = runner.classify(junit(*everything("l01")[:2]))
    assert result["l01"]["status"] == "NOT RUN"
    assert "required case absent" in result["l01"]["reasons"][-1]


@pytest.mark.parametrize("message,expected", [
    ("psycopg.OperationalError: connection refused", "INVALID"),
    ("RuntimeError: [Errno 98] address already in use", "INVALID"),
    ("AssertionError: a_reads_b_health allowed", "FAIL")])
def test_e3l_a_harness_error_is_invalid_not_a_product_fail(message, expected):
    cases = everything("l01")
    cases[0] = (cases[0][0], "error", message)
    assert runner.classify(junit(*cases))["l01"]["status"] == expected


def test_e3l_the_gate_and_the_cells_are_the_worst_status_and_exit_as_e2c_does():
    result = runner.classify(junit(*everything("l01"), *everything("l07"), *everything("l08"),
                                   *everything("l11")))
    assert runner.cells(result) == {"LAB-ACCESS": "NOT RUN", "LAB-PUBLISH": "NOT RUN",
                                    "SPLIT-CONTRACT": "NOT RUN"}
    for sid in runner.SCENARIOS:
        result[sid]["status"] = "PASS"
    assert runner.gate(result) == "PASS" and runner.EXIT["PASS"] == 0
    result["l11"]["status"] = "FAIL"
    assert runner.cells(result)["SPLIT-CONTRACT"] == "FAIL" and runner.gate(result) == "FAIL"
    assert runner.cells(result)["LAB-ACCESS"] == "PASS"
    assert (runner.EXIT["FAIL"], runner.EXIT["NOT RUN"], runner.EXIT["BLOCKED"],
            runner.EXIT["INVALID"]) == (1, 3, 3, 4)


def test_e3l_no_stack_blocks_every_scenario():
    result = runner.blocked_all(runner.classify("<testsuites/>"), "preflight: port held")
    assert {entry["status"] for entry in result.values()} == {"BLOCKED"}
    assert runner.gate(result) == "BLOCKED"


def test_e3l_the_namespace_is_the_reserved_block():
    """e3l = tasklocal's compose block 57000-57099 and PostgreSQL mirror 57032 (LW0)."""
    sys.path.insert(0, str(REPO / "apps" / "infrx-api"))
    from infrx.contracts.tasklocal import TASK_BLOCKS, TASK_PORTS
    assert runner.NAMESPACE == "e3l"
    assert TASK_BLOCKS["e3l"]["compose"][0] == 57000
    assert TASK_PORTS["e3l"]["postgres"] == 57032
    sys.path.insert(0, str(HERE.parent))
    import harness
    assert harness.ports_for("e3l")["postgres"] == 57032
    assert list(harness.range_for("e3l")) == list(range(57000, 57100))


def test_e3l_a_not_run_case_names_its_lanes_and_the_exact_rerun():
    """The coordinator reruns exactly what a NOT RUN reason says after the LW2 merges."""
    import lab_world
    with pytest.raises(pytest.skip.Exception) as skipped:
        lab_world.not_run("l06", "L3", why="absent")
    message = str(skipped.value)
    assert message.startswith("NOT RUN[L3] absent")
    assert message.endswith(f"{runner.PY} {runner.RUNNER} --out <dir> --only l06")
