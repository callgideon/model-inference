"""E8L layer 1: the runner's own decisions, with no stack (`pytest tests/integration` collects
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


runner = _load("e8l_runner", "runner.py")

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


def test_e8l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases():
    """The cells are exactly tasks.json E8L's test_ids, each carried by a scenario; the brief's
    legs are all there: R1 off, shadow, canary/A-B, the rollback and restart drills, the B2
    non-inferiority/cost decision, the promoted alias, R3's variant, and the three legs that
    wait (GPU parity, the I7 process, the UI)."""
    tasks = json.loads((REPO / "research/plan/tasks.json").read_text())
    e8l = next(task for task in tasks["tasks"] if task["id"] == "E8L")
    assert tuple(e8l["test_ids"]) == runner.TEST_IDS
    for tid in runner.TEST_IDS:
        assert any(tid in spec["test_ids"] for spec in runner.SCENARIOS.values()), tid
    assert set(runner.REQUIRED) == set(runner.SCENARIOS)
    assert len(runner.SCENARIOS) == 10
    assert runner.SCENARIOS["k08"]["lanes"] == ["P-08"]
    assert runner.SCENARIOS["k09"]["lanes"] == ["composition-2"]
    assert runner.SCENARIOS["k10"]["lanes"] == ["lab-ui-swap"]
    assert not any(runner.SCENARIOS[sid]["lanes"] for sid in
                   ("k01", "k02", "k03", "k04", "k05", "k06", "k07"))


def test_e8l_the_required_cases_are_exactly_what_the_scenario_modules_define():
    """A renamed or deleted case would leave its scenario judged on what remains."""
    defined = {name for path in HERE.glob("scenarios_*.py")
               for name in re.findall(r"^def (test_k\d\d_\w+)\(", path.read_text(), re.M)}
    assert defined == {name for names in runner.REQUIRED.values() for name in names}


def test_e8l_a_skip_is_never_a_pass_and_names_its_kind():
    result = runner.classify(junit(
        *everything("k01"),
        *everything("k09", "skipped", "NOT RUN[composition-2] waits; rerun after the merge: x"),
        *everything("k03", "skipped", "INVALID[premise] no grant"),
        *everything("k04", "skipped", "BLOCKED[stack] no stack"),
        *everything("k05", "xfail", "flaky")))
    status = {sid: entry["status"] for sid, entry in result.items()}
    assert (status["k01"], status["k09"], status["k03"], status["k04"], status["k05"]) == \
        ("PASS", "NOT RUN", "INVALID", "BLOCKED", "NOT RUN")
    assert status["k02"] == "NOT RUN", "a scenario with no case in the report is NOT RUN"


def test_e8l_a_scenario_missing_a_required_case_is_not_run():
    result = runner.classify(junit(*everything("k05")[:2]))
    assert result["k05"]["status"] == "NOT RUN"
    assert "required case absent" in result["k05"]["reasons"][-1]


@pytest.mark.parametrize("message,expected", [
    ("psycopg.OperationalError: connection refused", "INVALID"),
    ("RuntimeError: [Errno 98] address already in use", "INVALID"),
    ("AssertionError: a leaked family", "FAIL")])
def test_e8l_a_harness_error_is_invalid_not_a_product_fail(message, expected):
    cases = everything("k04")
    cases[0] = (cases[0][0], "error", message)
    assert runner.classify(junit(*cases))["k04"]["status"] == expected


def test_e8l_the_gate_and_the_cells_are_the_worst_status_and_exit_as_e2c_does():
    result = runner.classify(junit(*everything("k07"), *everything("k08"), *everything("k04")))
    assert runner.cells(result)["OPT-PARITY"] == "PASS"
    assert runner.cells(result)["ROLLOUT-RECOVER"] == "NOT RUN"      # k05 absent
    for sid in runner.SCENARIOS:
        result[sid]["status"] = "PASS"
    assert runner.gate(result) == "PASS" and runner.EXIT["PASS"] == 0
    result["k06"]["status"] = "FAIL"
    assert runner.cells(result)["ROLLOUT-RECOVER"] == "FAIL" and runner.gate(result) == "FAIL"
    assert runner.cells(result)["ROLLOUT-PIN"] == "PASS"
    assert (runner.EXIT["FAIL"], runner.EXIT["NOT RUN"], runner.EXIT["BLOCKED"],
            runner.EXIT["INVALID"]) == (1, 3, 3, 4)


def test_e8l_no_stack_blocks_every_scenario():
    result = runner.blocked_all(runner.classify("<testsuites/>"), "preflight: port held")
    assert {entry["status"] for entry in result.values()} == {"BLOCKED"}
    assert runner.gate(result) == "BLOCKED"


def test_e8l_the_namespace_is_the_reserved_block():
    """e8l = tasklocal's compose block 57400-57499 and PostgreSQL mirror 57432 (LW0); the
    synthetic endpoints and the dead store port sit inside it, clear of every E2 service
    port."""
    sys.path.insert(0, str(REPO / "apps" / "infrx-api"))
    from infrx.contracts.tasklocal import TASK_BLOCKS, TASK_PORTS
    assert runner.NAMESPACE == "e8l"
    assert TASK_BLOCKS["e8l"]["compose"][0] == 57400
    assert TASK_PORTS["e8l"]["postgres"] == 57432
    sys.path.insert(0, str(HERE.parent))
    import harness
    assert harness.ports_for("e8l")["postgres"] == 57432
    assert list(harness.range_for("e8l")) == list(range(57400, 57500))
    ports = set(runner.ENDPOINT_PORTS.values()) | set(runner.SPARE_PORTS) | {runner.DEAD_PORT}
    assert len(ports) == len(runner.ENDPOINT_PORTS) + len(runner.SPARE_PORTS) + 1
    assert ports <= set(harness.range_for("e8l"))
    assert not ports & set(harness.ports_for("e8l").values())


def test_e8l_a_not_run_case_names_its_lanes_and_the_exact_rerun():
    """The coordinator reruns exactly what a NOT RUN reason says after the merge."""
    lab_world = _load(f"{HERE.name}.lab_world", "lab_world.py")
    with pytest.raises(pytest.skip.Exception) as skipped:
        lab_world.not_run("k10", "lab-ui-swap", why="absent")
    message = str(skipped.value)
    assert message.startswith("NOT RUN[lab-ui-swap] absent")
    assert message.endswith(f"{runner.PY} {runner.RUNNER} --out <dir> --only k10")
