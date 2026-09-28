"""E5L layer 1: the runner's own decisions, with no stack (`pytest tests/integration` collects
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
REPO = HERE.parents[2]


def _load(name: str, file: str):
    """By path under a unique name: `runner`/`mutants` are also E3C's and E2's module names."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load("e5l_runner", "runner.py")


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


def scenario_sources() -> dict[str, str]:
    return {path.name: path.read_text() for path in HERE.glob("scenarios_*.py")}


def test_e5l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases():
    """The cells are exactly tasks.json E5L's test_ids, each carried by a scenario, and the
    brief's faults are all present (revoke mid-queue, expire content, drop the projection,
    time out a submit, restart a worker)."""
    tasks = json.loads((REPO / "research/plan/tasks.json").read_text())
    e5l = next(task for task in tasks["tasks"] if task["id"] == "E5L")
    assert tuple(e5l["test_ids"]) == runner.TEST_IDS
    for tid in runner.TEST_IDS:
        assert any(tid in spec["test_ids"] for spec in runner.SCENARIOS.values()), tid
    assert set(runner.REQUIRED) == set(runner.SCENARIOS) and len(runner.SCENARIOS) == 9
    titles = " ".join(spec["title"] for spec in runner.SCENARIOS.values())
    for fault in ("revoked mid-queue", "expired or deleted", "ClickHouse killed",
                  "timed out", "worker restarted"):
        assert fault in titles, fault


def test_e5l_the_required_cases_are_exactly_what_the_scenario_modules_define():
    """A renamed or deleted case would leave its scenario judged on what remains."""
    defined = {name for text in scenario_sources().values()
               for name in re.findall(r"^def (test_o\d\d_\w+)\(", text, re.M)}
    assert defined == {name for names in runner.REQUIRED.values() for name in names}


def test_e5l_a_scenarios_declared_lanes_are_the_lanes_its_not_run_case_names():
    """A scenario waiting on a lane says so in a NOT RUN case, and only such a scenario
    declares lanes: a dropped NOT RUN case would let the scenario pass on what remains."""
    named: dict[str, set[str]] = {}
    for text in scenario_sources().values():
        for sid, args in re.findall(r'not_run\("(o\d\d)", ((?:"[A-Z0-9-]+", )+)', text):
            named.setdefault(sid, set()).update(re.findall(r'"([A-Z0-9-]+)"', args))
    assert named == {sid: set(spec["lanes"]) for sid, spec in runner.SCENARIOS.items()
                     if spec["lanes"]}


def test_e5l_every_unbound_case_is_not_run_without_touching_a_stack():
    """The cases waiting on a lane skip NOT RUN before any stack call, with the exact rerun."""
    import scenarios_judge
    import scenarios_trace
    unbound = [getattr(module, name) for module in (scenarios_trace, scenarios_judge)
               for name in dir(module) if name.startswith("test_o")
               and "not_run(" in (HERE / f"{module.__name__}.py").read_text().split(
                   f"def {name}(")[1].split("\ndef ")[0]]
    assert len(unbound) == 3, [case.__name__ for case in unbound]
    for case in unbound:
        with pytest.raises(pytest.skip.Exception) as skipped:
            case(None)
        assert str(skipped.value).startswith("NOT RUN["), case.__name__
        assert f"--only {case.__name__[5:8]}" in str(skipped.value), case.__name__


def test_e5l_a_skip_is_never_a_pass_and_names_its_kind():
    result = runner.classify(junit(
        *everything("o02"),
        *everything("o04", "skipped", "NOT RUN[D6J] waits; rerun after the merge: x --only o04"),
        *everything("o05", "skipped", "INVALID[premise] no box"),
        *everything("o07", "skipped", "BLOCKED[E2C] no stack"),
        *everything("o08", "xfail", "flaky")))
    status = {sid: entry["status"] for sid, entry in result.items()}
    assert (status["o02"], status["o04"], status["o05"], status["o07"], status["o08"]) == \
        ("PASS", "NOT RUN", "INVALID", "BLOCKED", "NOT RUN")
    assert status["o03"] == "NOT RUN", "a scenario with no case in the report is NOT RUN"


def test_e5l_a_scenario_missing_a_required_case_is_not_run():
    result = runner.classify(junit(*everything("o09")[:1]))
    assert result["o09"]["status"] == "NOT RUN"
    assert "required case absent" in result["o09"]["reasons"][-1]


@pytest.mark.parametrize("message,expected", [
    ("psycopg.OperationalError: connection refused", "INVALID"),
    ("RuntimeError: [Errno 98] address already in use", "INVALID"),
    ("AssertionError: expired content reached the judge", "FAIL")])
def test_e5l_a_harness_error_is_invalid_not_a_product_fail(message, expected):
    cases = everything("o06")
    cases[0] = (cases[0][0], "error", message)
    assert runner.classify(junit(*cases))["o06"]["status"] == expected


def test_e5l_the_gate_and_the_cells_are_the_worst_status_and_exit_as_e2c_does():
    result = runner.classify(junit(*everything("o02"), *everything("o07")))
    assert set(runner.cells(result).values()) == {"NOT RUN"}
    for sid in runner.SCENARIOS:
        result[sid]["status"] = "PASS"
    assert runner.gate(result) == "PASS" and runner.EXIT["PASS"] == 0
    result["o07"]["status"] = "FAIL"
    assert runner.cells(result)["TRACE-RECOVER"] == "FAIL" and runner.gate(result) == "FAIL"
    assert runner.cells(result)["FEEDBACK-ACK"] == "PASS"
    assert (runner.EXIT["FAIL"], runner.EXIT["NOT RUN"], runner.EXIT["BLOCKED"],
            runner.EXIT["INVALID"]) == (1, 3, 3, 4)


def test_e5l_no_stack_blocks_every_scenario():
    result = runner.blocked_all(runner.classify("<testsuites/>"), "preflight: port held")
    assert {entry["status"] for entry in result.values()} == {"BLOCKED"}
    assert runner.gate(result) == "BLOCKED"


def test_e5l_the_namespace_is_the_reserved_block_and_the_judge_fake_port_is_free_in_it():
    """e5l = tasklocal's compose block 57100-57199 and PostgreSQL mirror 57132 (LW0); the judge
    fake sits in the block on a port E2's layout and the box do not use."""
    sys.path.insert(0, str(REPO / "apps" / "infrx-api"))
    from infrx.contracts.tasklocal import TASK_BLOCKS, TASK_PORTS
    assert runner.NAMESPACE == "e5l"
    assert TASK_BLOCKS["e5l"]["compose"][0] == 57100
    assert TASK_PORTS["e5l"]["postgres"] == 57132
    sys.path.insert(0, str(HERE.parent))
    import harness
    import observe_world
    assert harness.ports_for("e5l")["postgres"] == 57132
    block = harness.range_for("e5l")
    assert list(block) == list(range(57100, 57200))
    judge = 57100 + (observe_world.JUDGE_PORT - observe_world.harness.PORT_RANGE.start)
    assert judge in block and judge not in harness.ports_for("e5l").values()
    assert judge - 57100 not in (30, 31, 40, 41, 70, 95, 96)     # PostgREST, gateways, E3L's


def test_e5l_a_not_run_case_names_its_lanes_and_the_exact_rerun():
    """The coordinator reruns exactly what a NOT RUN reason says after the merges."""
    import observe_world
    with pytest.raises(pytest.skip.Exception) as skipped:
        observe_world.not_run("o04", "D6J", why="absent")
    message = str(skipped.value)
    assert message.startswith("NOT RUN[D6J] absent")
    assert message.endswith(f"{runner.PY} {runner.RUNNER} --out <dir> --only o04")


def test_e5l_the_judge_is_labelled_a_dry_run_never_a_live_run():
    """P-10: dry-run evidence is kept apart from any live-judge run: the runner's judge label
    says so, and the judge's one host is the loopback fake inside the e5l block."""
    assert "dry-run" in runner.JUDGE_LABEL and "never a live-judge run" in runner.JUDGE_LABEL
    import observe_world
    assert observe_world.JUDGE_PORT - observe_world.harness.PORT_RANGE.start == 65
    sys.path.insert(0, str(REPO / "apps" / "infrx-api"))
    from infrx.judge.submit import HttpJudgeProvider
    fake = HttpJudgeProvider(f"http://127.0.0.1:{observe_world.JUDGE_PORT}")
    assert fake.base_url.startswith("http://127.0.0.1:")
