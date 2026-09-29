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
    assert runner.SCENARIOS["k09"]["lanes"] == [], (
        "k09's I7 entry point landed (composition-2): it runs for real now, NOT RUN only "
        "internally (WR-R2-3, the pass loop), not as a whole-scenario merge wait")
    assert runner.SCENARIOS["k10"]["lanes"] == [], (
        "WR-R4-2 is composed (merge #50): k10's port half runs pilot.lab_releases and "
        "`rollout decide` for real, its UI half apps/lab/tests/e2e/rollout (LAB-E2E, R238)")
    assert "test_k10_the_composed_releases_route_proposes_and_the_operator_decides" in \
        runner.REQUIRED["k10"], "k10's port half (WR-C6-K10) is required"
    assert not any(runner.SCENARIOS[sid]["lanes"] for sid in
                   ("k01", "k02", "k03", "k04", "k05", "k06", "k07", "k09", "k10"))


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


def test_e8l_k09s_breach_half_is_bound_and_no_longer_a_sub_cell():
    """WR-LIVE-K09 (R244): k09's pass-loop case now sees a breach in D9's Live (0054) and rolls
    it back once, so the breach half is k09's own case, not a NOT RUN sub-cell beside it."""
    result = runner.classify(junit(*everything("k09")))
    assert result["k09"]["status"] == "PASS"
    assert "k09-breach" not in {c["id"] for c in runner.sub_cells(result)}
    assert "k09-breach" not in runner.SUB_CELLS


def test_e8l_a_sub_cell_is_not_run_naming_its_lanes_and_its_parents_rerun(monkeypatch):
    """A half of a scenario that is not bound yet is recorded in verdict.json as NOT RUN with
    its lanes and its parent's exact rerun (R222: never prose-only), and never lowers its
    parent."""
    monkeypatch.setattr(runner, "SUB_CELLS", {"k09-fixture": {
        "parent": "k09", "lanes": ["WR-X", "WR-Y"], "title": "t", "note": "n"}})
    result = runner.classify(junit(*everything("k09")))
    assert result["k09"]["status"] == "PASS", "the sub-cell never lowers its parent"
    [cell] = runner.sub_cells(result)
    assert (cell["id"], cell["parent"], cell["parent_status"], cell["status"], cell["reason"]) \
        == ("k09-fixture", "k09", "PASS", "NOT RUN", "NOT RUN[WR-X,WR-Y]")
    assert cell["lanes"] == ["WR-X", "WR-Y"]
    assert cell["reproduce"] == f"{runner.PY} {runner.RUNNER} --out <dir> --only k09"


def test_e8l_k10s_composed_ui_journey_is_bound_and_no_longer_a_sub_cell():
    """WR-LR6-VERDICT + WR-LIVE-DECIDE (lab-rollout-7): the page's journey reads R2's verdict
    from the composed records (read time, over D9's Live and the B2 report) and an expansion is
    approved by `rollout decide` itself, so no stand-in waits on a product WR: k10's UI half is
    k10's own case and no scenario has a NOT RUN sub-cell beside it."""
    result = runner.classify(junit(*everything("k10")))
    assert result["k10"]["status"] == "PASS"
    assert runner.sub_cells(result) == [] and runner.SUB_CELLS == {}


def test_e8l_k10s_plan_path_handed_to_the_worker_is_absolute(tmp_path, monkeypatch):
    """WR-LR5-RV2: k10's `rollout launch` runs with cwd=apps/infrx-api, so the plan path the
    case hands it is absolute whatever `runner.py --out` was given."""
    lab_world = _load(f"{HERE.name}.lab_world", "lab_world.py")
    monkeypatch.chdir(tmp_path)
    got = lab_world.plan_file(Path("out") / "k10")
    assert got.is_absolute(), got
    assert got == (tmp_path / "out" / "k10" / "plan.json").resolve()


def test_e8l_r222_accepts_only_a_not_run_out_of_local_scope(monkeypatch):
    """R222/R234/R235: the gate is accepted locally with no FAIL and every NOT RUN (sub-cells
    included) waiting only on out-of-local-scope work - k08 on a GPU (P-08), the one lane
    left out of scope: the product WRs k10's journey waited on landed (WR-LIVE-DECIDE,
    WR-LR6-VERDICT) - by its own NOT RUN reason. A NOT RUN on in-scope work (k10, composed
    since merge #50), a sub-cell on a lane not ruled out of scope, a FAIL, or a scenario NOT
    RUN for another reason (deselected, never run) stays open."""
    assert runner.OUT_OF_SCOPE == {"P-08": "GPU (P-08 staging target)"}
    k08 = "NOT RUN[P-08] no allocated GPU; rerun after the merge: x --only k08"
    others = [c for sid in runner.SCENARIOS if sid != "k08" for c in everything(sid)]
    accepted = runner.classify(junit(*others, *everything("k08", "skipped", k08)))
    assert runner.r222(accepted) == {"accepted": True, "open": {}}
    assert runner.gate(accepted) == "NOT RUN", "accepted is not a PASS"
    with monkeypatch.context() as patch:            # a lane not ruled out of scope stays open
        patch.delitem(runner.OUT_OF_SCOPE, "P-08")
        assert runner.r222(accepted) == {"accepted": False, "open": {"k08": "NOT RUN"}}
    with monkeypatch.context() as patch:            # a sub-cell is judged too
        patch.setattr(runner, "SUB_CELLS", {"k10-fixture": {
            "parent": "k10", "lanes": ["WR-LR6-VERDICT"], "title": "t", "note": "n"}})
        assert runner.r222(accepted) == {"accepted": False, "open": {"k10-fixture": "NOT RUN"}}
        patch.setitem(runner.SUB_CELLS["k10-fixture"], "lanes", ["P-08"])
        assert runner.r222(accepted) == {"accepted": True, "open": {}}
    failed = runner.classify(junit(*others, *everything("k08", "failure", k08)))
    assert runner.r222(failed) == {"accepted": False, "open": {"k08": "FAIL"}}, \
        "R234: an in-scope FAIL is never excused, whatever its message says"
    ui = "NOT RUN[WR-R4-2:records] the suite passed; rerun after the merge: x --only k10"
    rest = [c for sid in runner.SCENARIOS if sid not in ("k08", "k10") for c in everything(sid)]
    waiting = runner.classify(junit(*rest, *everything("k08", "skipped", k08),
                                    *everything("k10", "skipped", ui)))
    assert runner.r222(waiting)["open"] == {"k10": "NOT RUN"}, "k10 is in-scope work now"
    assert "k08" in runner.r222(runner.classify(junit(*everything("k01"))))["open"], \
        "a scenario never run is open"
    deselected = runner.classify(junit(*others, *everything("k08", "skipped", k08)),
                                 only={"k01"})
    assert "k08" in runner.r222(deselected)["open"], "every reason must be the lane's wait"


def test_e8l_the_ui_suites_record_is_read_back_from_a_relative_out(tmp_path, monkeypatch):
    """WR-LR6-GATE-OUT: `runner.py --out` may be relative to the caller's directory, while node
    runs the suite in apps/lab - the gate hands node the record's directory absolute, so a green
    suite's record is read back (never None: a false FAIL of the UI cell)."""
    import importlib.util
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location(
        "e8l_e2e_gate", REPO / "apps" / "lab" / "tests" / "e2e" / "gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    lab = tmp_path / "lab"
    (lab / "node_modules").mkdir(parents=True)          # an installed checkout (RV-4)
    monkeypatch.setattr(gate, "LAB", lab)
    monkeypatch.syspath_prepend(str(REPO / "apps" / "infrx-api"))    # the gate reads infrx's path
    monkeypatch.chdir(tmp_path)

    def node(argv, *, cwd, env, **_):
        """Node's side: the suite writes its record under LAB_E2E_OUT, from its own cwd."""
        record = Path(cwd) / env["LAB_E2E_OUT"] / "rollout.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"composed": {"records": True}}))
        return SimpleNamespace(returncode=0, stdout="# pass 6\n# fail 0\n# cancelled 0\n"
                                                    "# skipped 0\n", stderr="")
    monkeypatch.setattr(gate.subprocess, "run", node)
    got = gate.run("rollout", Path("out"))
    assert got["record"] == {"composed": {"records": True}}, got
    assert gate.missing(got) == []


def test_e8l_an_environment_enoent_is_blocked_harness_not_a_product_fail(tmp_path, monkeypatch):
    """RV-4 (merge #62): an environment ENOENT - apps/lab/node_modules not installed, or no
    `node` on PATH - is LAB-E2E's `EnvironmentBlocked` before any suite runs, and the runner
    classifies a case failing on it BLOCKED[harness], never a product FAIL (nor a pass)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "e8l_e2e_gate_env", REPO / "apps" / "lab" / "tests" / "e2e" / "gate.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    lab = tmp_path / "lab"
    lab.mkdir()
    monkeypatch.setattr(gate, "LAB", lab)
    monkeypatch.syspath_prepend(str(REPO / "apps" / "infrx-api"))
    ran = []
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(gate.EnvironmentBlocked, match="ENOENT .*node_modules"):
        gate.run("rollout", tmp_path / "out")
    assert ran == [], "no suite runs without apps/lab/node_modules"
    (lab / "node_modules").mkdir()

    def no_node(*_, **__):
        raise FileNotFoundError(2, "No such file or directory", "node")
    monkeypatch.setattr(gate.subprocess, "run", no_node)
    with pytest.raises(gate.EnvironmentBlocked, match="ENOENT"):
        gate.run("rollout", tmp_path / "out")
    cases = everything("k10")
    cases[0] = (cases[0][0], "failure",
                "lab_rollout.lab_e2e_gate.EnvironmentBlocked: ENOENT /x/apps/lab/node_modules")
    k10 = runner.classify(junit(*cases))["k10"]
    assert k10["status"] == "BLOCKED" and "BLOCKED[harness] " in k10["reasons"][0], k10
