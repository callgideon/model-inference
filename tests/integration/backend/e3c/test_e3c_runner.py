"""E3C s12 (VERIFY-REPRO): the verdict's own rules, with no stack - a missing service is
BLOCKED, a skip is never a pass, a red case fails the gate, a negative control counts only
over a passing scenario; and the matrix covers the brief. Collected by `pytest
tests/integration` (layer 1) and by the runner (these are s12's cases)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import runner                                           # noqa: E402

TASKS = json.loads((HERE.parents[3] / "research/plan/tasks.json").read_text())["tasks"]


def junit(*cases: tuple[str, str, str]) -> str:
    """(name, outcome, message) -> a JUnit document as pytest writes it."""
    body = []
    for name, outcome, message in cases:
        inner = {"pass": "", "fail": f'<failure message="{message}"/>',
                 "error": f'<error message="{message}"/>',
                 "skip": f'<skipped type="pytest.skip" message="{message}"/>',
                 "xfail": f'<skipped type="pytest.xfail" message="{message}"/>'}[outcome]
        body.append(f'<testcase classname="m" name="{name}">{inner}</testcase>')
    return f"<testsuites><testsuite>{''.join(body)}</testsuite></testsuites>"


def test_s12_a_missing_or_skipped_case_is_never_a_pass():
    result = runner.classify(junit(
        ("test_s01_journey", "pass", ""),
        ("test_s02_two_gateways", "skip", "BLOCKED[D10,M5] upload port absent"),
        ("test_s03_upload", "skip", "no stack"),
        ("test_s04_ready", "skip", "INVALID[premise] engine answered nothing"),
        ("test_s05_crash[claim]", "xfail", "known"),
        ("test_s06_retention", "error", "boom")))
    status = {sid: entry["status"] for sid, entry in result["scenarios"].items()}
    assert (status["s01"], status["s02"], status["s03"], status["s04"], status["s05"],
            status["s06"]) == ("PASS", "BLOCKED", "NOT RUN", "INVALID", "NOT RUN", "FAIL")
    assert status["s07"] == "NOT RUN", "a scenario with no case in the report ran nothing"
    assert runner.gate(result) == "FAIL"


def test_s12_the_gate_is_the_worst_status_and_exits_as_e2c_does():
    only_pass = junit(*((f"test_{sid}_x", "pass", "") for sid in runner.SCENARIOS),
                      *((f"test_nc_{nc[3:].replace('-', '_')}__{c['scenario']}_x", "pass", "")
                        for nc, c in runner.CONTROLS.items()))
    result = runner.classify(only_pass)
    # the two revert-type controls cannot run on one tree: the gate stays open
    assert {nc for nc, entry in result["controls"].items() if entry["status"] != "PASS"} == \
        {nc for nc, c in runner.CONTROLS.items() if c.get("revert")}
    assert runner.gate(result) == "NOT RUN" and runner.EXIT["NOT RUN"] == 3
    blocked = runner.classify(only_pass.replace(
        '<testcase classname="m" name="test_s03_x"></testcase>',
        '<testcase classname="m" name="test_s03_x"><skipped message="BLOCKED[M5] x"/>'
        '</testcase>'))
    assert blocked["scenarios"]["s03"]["status"] == "BLOCKED"
    assert blocked["controls"]["nc-upload-restart"]["status"] == "NOT RUN", \
        "a control over a scenario that did not pass proves nothing"
    assert runner.gate(blocked) == "BLOCKED"
    assert (runner.EXIT["PASS"], runner.EXIT["FAIL"], runner.EXIT["BLOCKED"],
            runner.EXIT["INVALID"]) == (0, 1, 3, 4)


def test_s12_a_control_that_is_not_detected_fails_the_gate():
    result = runner.classify(junit(("test_s01_journey", "pass", ""),
                                   ("test_nc_journey_revoke__s01_x", "fail", "not detected")))
    assert result["controls"]["nc-journey-revoke"]["status"] == "FAIL"
    assert runner.gate(result) == "FAIL"


def test_s12_the_matrix_covers_the_brief_and_the_task():
    task = next(t for t in TASKS if t["id"] == "E3C")
    covered = {test_id for s in runner.SCENARIOS.values() for test_id in s["test_ids"]}
    assert set(task["test_ids"]) <= covered, set(task["test_ids"]) - covered
    assert {s["row"] for s in runner.SCENARIOS.values()} >= set(range(1, 10)), \
        "every row of the brief's scenario table has a scenario"
    lanes = {lane for s in runner.SCENARIOS.values() for lane in s["lanes"]}
    assert lanes <= set(task["integration_dependencies"]) | set(task["start_dependencies"])
    oracles = {c["oracle"] for c in runner.CONTROLS.values()}
    corrective = {"UPLOAD-RESTART", "RETENTION-DURABLE", "ADMISSION-READY", "RESULT-EXPIRY",
                  "CREDIT-CUTOVER", "BACKEND-JOURNEY", "VERIFY-REPRO"}
    assert corrective <= oracles, corrective - oracles


def test_s12_scenario_modules_are_not_collected_by_the_default_suites():
    """`pytest tests/integration` (E2's layer 1 and E3B's backend stage) must not collect a
    red scenario: they run only through the runner, which names them."""
    files = [Path(f).name for f in runner.scenario_files()]
    assert files[:-1] and all(name.startswith("scenarios_") for name in files[:-1])


def test_s12_every_fault_point_and_bypass_names_code_that_exists():
    """A renamed seam must fail HERE, loudly, not turn a crash drill into a no-op."""
    import world
    for point, candidates in world.POINTS.items():
        assert all(c[3] in ("before", "after") for c in candidates), point
        # `readiness` may legitimately vanish (F2C-L R110: one-phase admission); every other
        # step of the brief's crash list must exist in the tree
        assert world.has_point(point) or point == "readiness", point
    assert set(world.BYPASSES) >= {"upload-local", "expiry-recompute", "revoke-ignored",
                                   "tenant-blind"}


def test_s12_the_namespace_is_the_reserved_block():
    """WR-1's row, however it is provided: the e3c layout is tasklocal's 56900-56999."""
    import world
    from infrx.contracts import tasklocal
    block = tasklocal.local_services("e3c")
    assert block["compose"].host_port == 55500 + world.OFFSET
    assert block["postgres"].host_port == 55532 + world.OFFSET


@pytest.mark.parametrize("lanes", [(), ("Q9",)])
def test_s12_blocked_must_name_known_lanes(lanes):
    import world
    with pytest.raises(AssertionError, match="known lanes"):
        world.blocked(*lanes, why="x")
