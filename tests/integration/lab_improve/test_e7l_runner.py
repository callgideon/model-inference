"""E7L layer 1: the runner's own decisions, with no stack (`pytest tests/integration` collects
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


runner = _load("e7l_runner", "runner.py")

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


def test_e7l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases():
    """The cells are exactly tasks.json E7L's test_ids, each carried by a scenario; the brief's
    journey is all there: iteration 1's labels and training, iteration 2 over permitted
    traces, the three faults (revocation, duplicate teacher submit, lost training poll), the
    I6 workers, the UI leg and the staging promotion."""
    tasks = json.loads((REPO / "research/plan/tasks.json").read_text())
    e7l = next(task for task in tasks["tasks"] if task["id"] == "E7L")
    assert tuple(e7l["test_ids"]) == runner.TEST_IDS
    for tid in runner.TEST_IDS:
        assert any(tid in spec["test_ids"] for spec in runner.SCENARIOS.values()), tid
    assert set(runner.REQUIRED) == set(runner.SCENARIOS)
    assert len(runner.SCENARIOS) == 9
    assert runner.SCENARIOS["i07"]["lanes"] == ["composition-2", "WR-P2-4"]
    assert runner.SCENARIOS["i08"]["lanes"] == ["LAB_PIPELINES", "P3-evaluations"]
    assert runner.SCENARIOS["i09"]["lanes"] == ["staging-target"]
    assert not [sid for sid in ("i01", "i02", "i03", "i04", "i05", "i06")
                if runner.SCENARIOS[sid]["lanes"]], "a merged-code cell waits on a lane"


def test_e7l_the_required_cases_are_exactly_what_the_scenario_modules_define():
    """A renamed or deleted case would leave its scenario judged on what remains."""
    defined = {name for path in HERE.glob("scenarios_*.py")
               for name in re.findall(r"^def (test_i\d\d_\w+)\(", path.read_text(), re.M)}
    assert defined == {name for names in runner.REQUIRED.values() for name in names}


def test_e7l_a_skip_is_never_a_pass_and_names_its_kind():
    result = runner.classify(junit(
        *everything("i01"),
        *everything("i07", "skipped", "NOT RUN[composition-2] waits; rerun after the merge: x"),
        *everything("i03", "skipped", "INVALID[premise] no grant"),
        *everything("i04", "skipped", "BLOCKED[stack] no stack"),
        *everything("i05", "xfail", "flaky")))
    status = {sid: entry["status"] for sid, entry in result.items()}
    assert (status["i01"], status["i07"], status["i03"], status["i04"], status["i05"]) == \
        ("PASS", "NOT RUN", "INVALID", "BLOCKED", "NOT RUN")
    assert status["i02"] == "NOT RUN", "a scenario with no case in the report is NOT RUN"


def test_e7l_a_scenario_missing_a_required_case_is_not_run():
    result = runner.classify(junit(*everything("i05")[:2]))
    assert result["i05"]["status"] == "NOT RUN"
    assert "required case absent" in result["i05"]["reasons"][-1]


@pytest.mark.parametrize("message,expected", [
    ("psycopg.OperationalError: connection refused", "INVALID"),
    ("OSError: address already in use: the protocol server did not start on 57365", "INVALID"),
    ("AssertionError: 0-E7L-1 missing transitive revocation", "FAIL")])
def test_e7l_a_harness_error_is_invalid_not_a_product_fail(message, expected):
    cases = everything("i04")
    cases[0] = (cases[0][0], "error", message)
    assert runner.classify(junit(*cases))["i04"]["status"] == expected


def test_e7l_the_gate_and_the_cells_are_the_worst_status_and_exit_as_e2c_does():
    result = runner.classify(junit(*everything("i01"), *everything("i02"),
                                   *everything("i05"), *everything("i06")))
    assert runner.cells(result)["TRAIN-RECOVER"] == "NOT RUN"         # i07 absent
    assert runner.cells(result)["DATA-LINEAGE"] == "NOT RUN"          # i03, i04, i09 absent
    for sid in runner.SCENARIOS:
        result[sid]["status"] = "PASS"
    assert runner.gate(result) == "PASS" and runner.EXIT["PASS"] == 0
    result["i04"]["status"] = "FAIL"
    assert runner.cells(result)["DATA-LINEAGE"] == "FAIL" and runner.gate(result) == "FAIL"
    assert runner.cells(result)["PIPELINE-BUDGET"] == "PASS"
    assert (runner.EXIT["FAIL"], runner.EXIT["NOT RUN"], runner.EXIT["BLOCKED"],
            runner.EXIT["INVALID"]) == (1, 3, 3, 4)


def test_e7l_no_stack_blocks_every_scenario():
    result = runner.blocked_all(runner.classify("<testsuites/>"), "preflight: port held")
    assert {entry["status"] for entry in result.values()} == {"BLOCKED"}
    assert runner.gate(result) == "BLOCKED"


def test_e7l_the_namespace_is_the_reserved_block():
    """e7l = tasklocal's compose block 57300-57399 and PostgreSQL mirror 57332 (LW0); the
    endpoints, the teacher fake and the protocol server sit inside it, clear of every E2
    service port and of each other."""
    sys.path.insert(0, str(REPO / "apps" / "infrx-api"))
    from infrx.contracts.tasklocal import TASK_BLOCKS, TASK_PORTS
    assert runner.NAMESPACE == "e7l"
    assert TASK_BLOCKS["e7l"]["compose"][0] == 57300
    assert TASK_PORTS["e7l"]["postgres"] == 57332
    sys.path.insert(0, str(HERE.parent))
    import harness
    assert harness.ports_for("e7l")["postgres"] == 57332
    assert list(harness.range_for("e7l")) == list(range(57300, 57400))
    own = [*runner.ENDPOINT_PORTS.values(), *runner.SERVICE_PORTS.values(), *runner.SPARE_PORTS]
    assert len(own) == len(set(own)), "two of the scenarios' host processes share a port"
    assert set(own) <= set(harness.range_for("e7l"))
    assert not set(own) & set(harness.ports_for("e7l").values())


def test_e7l_a_not_run_case_names_its_lanes_and_the_exact_rerun():
    """The coordinator reruns exactly what a NOT RUN reason says after the merge."""
    lab_world = _load(f"{HERE.name}.lab_world", "lab_world.py")
    with pytest.raises(pytest.skip.Exception) as skipped:
        lab_world.not_run("i08", "LAB_PIPELINES", why="absent")
    message = str(skipped.value)
    assert message.startswith("NOT RUN[LAB_PIPELINES] absent")
    assert message.endswith(f"{runner.PY} {runner.RUNNER} --out <dir> --only i08")


def test_e7l_the_i07_tripwire_fires_on_a_worker_pass_not_the_module(tmp_path, monkeypatch):
    """composition-2's `python -m infrx.lab.workers` refuses `annotation`/`training` by name:
    its merge alone must not turn i07 into a FAIL, but a real pass must (never a silent NOT
    RUN once the worker exists)."""
    lab_world = _load(f"{HERE.name}.lab_world", "lab_world.py")
    pending = _load("e7l_pending", "scenarios_pending.py")
    monkeypatch.setattr(lab_world, "API", tmp_path)
    assert not pending.pass_landed("annotation")                    # no module
    main = tmp_path / "infrx" / "lab" / "workers" / "__main__.py"
    main.parent.mkdir(parents=True)
    main.write_text('detail="annotation has no worker pass: ..."\n'
                    'detail="training has no worker pass: ..."\n')
    assert not pending.pass_landed("annotation") and not pending.pass_landed("training")
    main.write_text('detail="training has no worker pass: ..."\n')
    assert pending.pass_landed("annotation") and not pending.pass_landed("training")
