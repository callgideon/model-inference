"""E6L layer 1: the runner's own decisions, with no stack (`pytest tests/integration` collects
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


runner = _load("e6l_runner", "runner.py")

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


def test_e6l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases():
    """The cells are exactly tasks.json E6L's test_ids, each carried by a scenario; the brief's
    journey is all there: text/tool and finite-video import, split traps, rights, the durable
    run with its faults, two serving versions, compare/decide, checkpoints, the I5 workers
    and the UI leg."""
    tasks = json.loads((REPO / "research/plan/tasks.json").read_text())
    e6l = next(task for task in tasks["tasks"] if task["id"] == "E6L")
    assert tuple(e6l["test_ids"]) == runner.TEST_IDS
    for tid in runner.TEST_IDS:
        assert any(tid in spec["test_ids"] for spec in runner.SCENARIOS.values()), tid
    assert set(runner.REQUIRED) == set(runner.SCENARIOS)
    assert len(runner.SCENARIOS) == 11
    assert runner.SCENARIOS["j09"]["lanes"] == ["L3"]
    assert runner.SCENARIOS["j10"]["lanes"] == ["B4", "lab-api-2"]


def test_e6l_the_required_cases_are_exactly_what_the_scenario_modules_define():
    """A renamed or deleted case would leave its scenario judged on what remains."""
    defined = {name for path in HERE.glob("scenarios_*.py")
               for name in re.findall(r"^def (test_j\d\d_\w+)\(", path.read_text(), re.M)}
    assert defined == {name for names in runner.REQUIRED.values() for name in names}


def test_e6l_a_skip_is_never_a_pass_and_names_its_kind():
    result = runner.classify(junit(
        *everything("j01"),
        *everything("j09", "skipped", "NOT RUN[composition] waits; rerun after the merge: x"),
        *everything("j03", "skipped", "INVALID[premise] no grant"),
        *everything("j04", "skipped", "BLOCKED[stack] no stack"),
        *everything("j05", "xfail", "flaky")))
    status = {sid: entry["status"] for sid, entry in result.items()}
    assert (status["j01"], status["j09"], status["j03"], status["j04"], status["j05"]) == \
        ("PASS", "NOT RUN", "INVALID", "BLOCKED", "NOT RUN")
    assert status["j02"] == "NOT RUN", "a scenario with no case in the report is NOT RUN"


def test_e6l_a_scenario_missing_a_required_case_is_not_run():
    result = runner.classify(junit(*everything("j05")[:2]))
    assert result["j05"]["status"] == "NOT RUN"
    assert "required case absent" in result["j05"]["reasons"][-1]


@pytest.mark.parametrize("message,expected", [
    ("psycopg.OperationalError: connection refused", "INVALID"),
    ("RuntimeError: [Errno 98] address already in use", "INVALID"),
    ("AssertionError: a leaked family", "FAIL")])
def test_e6l_a_harness_error_is_invalid_not_a_product_fail(message, expected):
    cases = everything("j04")
    cases[0] = (cases[0][0], "error", message)
    assert runner.classify(junit(*cases))["j04"]["status"] == expected


def test_e6l_the_gate_and_the_cells_are_the_worst_status_and_exit_as_e2c_does():
    result = runner.classify(junit(*everything("j01"), *everything("j03"), *everything("j04")))
    assert runner.cells(result)["DATA-SPLIT"] == "PASS"
    assert runner.cells(result)["DATA-IMPORT"] == "NOT RUN"          # j02 absent
    for sid in runner.SCENARIOS:
        result[sid]["status"] = "PASS"
    assert runner.gate(result) == "PASS" and runner.EXIT["PASS"] == 0
    result["j08"]["status"] = "FAIL"
    assert runner.cells(result)["CHECKPOINT-IDEM"] == "FAIL" and runner.gate(result) == "FAIL"
    assert runner.cells(result)["DATA-RIGHTS"] == "PASS"
    assert (runner.EXIT["FAIL"], runner.EXIT["NOT RUN"], runner.EXIT["BLOCKED"],
            runner.EXIT["INVALID"]) == (1, 3, 3, 4)


def test_e6l_no_stack_blocks_every_scenario():
    result = runner.blocked_all(runner.classify("<testsuites/>"), "preflight: port held")
    assert {entry["status"] for entry in result.values()} == {"BLOCKED"}
    assert runner.gate(result) == "BLOCKED"


def test_e6l_the_namespace_is_the_reserved_block():
    """e6l = tasklocal's compose block 57200-57299 and PostgreSQL mirror 57232 (LW0); the
    synthetic endpoints sit inside it, clear of every E2 service port."""
    sys.path.insert(0, str(REPO / "apps" / "infrx-api"))
    from infrx.contracts.tasklocal import TASK_BLOCKS, TASK_PORTS
    assert runner.NAMESPACE == "e6l"
    assert TASK_BLOCKS["e6l"]["compose"][0] == 57200
    assert TASK_PORTS["e6l"]["postgres"] == 57232
    sys.path.insert(0, str(HERE.parent))
    import harness
    assert harness.ports_for("e6l")["postgres"] == 57232
    assert list(harness.range_for("e6l")) == list(range(57200, 57300))
    ports = set(runner.ENDPOINT_PORTS.values()) | set(runner.SPARE_PORTS)
    assert not set(runner.ENDPOINT_PORTS.values()) & set(runner.SPARE_PORTS)
    assert ports <= set(harness.range_for("e6l"))
    assert not ports & set(harness.ports_for("e6l").values())


def test_e6l_a_not_run_case_names_its_lanes_and_the_exact_rerun():
    """The coordinator reruns exactly what a NOT RUN reason says after the merge."""
    import lab_world
    with pytest.raises(pytest.skip.Exception) as skipped:
        lab_world.not_run("j10", "B4", why="absent")
    message = str(skipped.value)
    assert message.startswith("NOT RUN[B4] absent")
    assert message.endswith(f"{runner.PY} {runner.RUNNER} --out <dir> --only j10")
