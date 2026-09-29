"""LAB-LOCAL layer 1: the E4-ON runner's own decisions and the composition's switch list, with
no stack (`pytest tests/integration` collects this file; the scenarios run only through
runner.py). Each case names what a broken runner would report falsely."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
if str(REPO / "apps" / "infrx-api") not in sys.path:      # `infrx`, as harness.api_on_path
    sys.path.append(str(REPO / "apps" / "infrx-api"))


def _load(name: str, file: str):
    """By path under a unique name: `runner`/`lab_world` are also the other gates' names."""
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load("lab_local_runner", "runner.py")


def world():
    return sys.modules.get("lab_local_world") or _load("lab_local_world", "lab_world.py")


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


def everything(kind: str = "", message: str = "") -> list[tuple[str, str, str]]:
    return [(name, kind, message) for names in runner.REQUIRED.values() for name in names]


def test_lab_local_every_deployment_switch_is_on_in_the_composition():
    """E4-ON means EVERY switch: the composition's lists are exactly DeploymentSettings'
    boolean fields (a switch added later and forgotten here fails), and a switch the brief
    names that no setting reads is recorded as pending, never claimed ON."""
    from infrx.config import DeploymentSettings, env_name
    lw = world()
    switches = {env_name(f.name) for f in dataclasses.fields(DeploymentSettings)
                if f.type in (bool, "bool")}
    on = set(lw.GATEWAY_SWITCHES) | set(lw.WORKER_SWITCHES)
    assert on == switches, (sorted(on - switches), sorted(switches - on))
    assert not set(lw.PENDING_SWITCHES) & switches
    env = lw.switch_env(Path("/nonexistent-spool"))
    assert all(env[name] == "true" for name in on)
    assert env.get("LAB_TEACHER_URL") == f"http://127.0.0.1:{lw.TEACHER_PORT}"   # the local fake


def test_lab_local_a_skip_is_never_a_pass_and_names_its_kind():
    """A NOT RUN skip, a BLOCKED[..] skip and an xfail are never PASS."""
    name = runner.REQUIRED["o03"][1]
    for kind, message, want in (("skipped", "NOT RUN[WR-B3-3] pending", runner.NOT_RUN),
                                ("skipped", "BLOCKED[gateway] down", runner.BLOCKED),
                                ("skipped", "INVALID[fake-only] x", runner.INVALID),
                                ("xfail", "known", runner.NOT_RUN)):
        cases = [c for c in everything() if c[0] != name] + [(name, kind, message)]
        assert runner.classify(junit(*cases))["o03"]["status"] == want, (kind, message)


def test_lab_local_a_scenario_missing_a_required_case_is_not_run():
    """A deselected or deleted required case cannot let its scenario pass."""
    dropped = runner.REQUIRED["o05"][0]
    result = runner.classify(junit(*[c for c in everything() if c[0] != dropped]))
    assert result["o05"]["status"] == runner.NOT_RUN
    assert result["o01"]["status"] == runner.PASS


def test_lab_local_a_harness_error_is_invalid_not_a_product_fail():
    name = runner.REQUIRED["o01"][0]
    cases = [c for c in everything() if c[0] != name]
    broken = runner.classify(junit(*cases, (name, "error", "psycopg.OperationalError: gone")))
    failed = runner.classify(junit(*cases, (name, "failure", "AssertionError: refused")))
    assert broken["o01"]["status"] == runner.INVALID
    assert failed["o01"]["status"] == runner.FAIL


def test_lab_local_the_gate_is_the_worst_stage_and_exits_as_e2c_does():
    pass_, fail, not_run = ({"status": s} for s in (runner.PASS, runner.FAIL, runner.NOT_RUN))
    assert runner.gate([pass_, not_run]) == runner.NOT_RUN
    assert runner.gate([not_run, fail, pass_]) == runner.FAIL
    assert runner.EXIT == {runner.PASS: 0, runner.FAIL: 1, runner.BLOCKED: 3,
                           runner.NOT_RUN: 3, runner.INVALID: 4}


def test_lab_local_no_stack_blocks_every_stage():
    rows = runner.blocked_all("preflight: docker missing")
    assert [row["stage"] for row in rows] == list(runner.STAGES)
    assert {row["status"] for row in rows} == {runner.BLOCKED}


def test_lab_local_a_pytest_stage_with_a_skip_or_no_case_is_blocked_not_passed():
    """E2C's rule for the E4-ON stage: a skipped required case proves nothing."""
    zero = {"tests": 10, "passed": 10, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0}
    assert runner.pytest_verdict(0, zero)[0] == runner.PASS
    assert runner.pytest_verdict(0, {**zero, "skipped": 1})[0] == runner.BLOCKED
    assert runner.pytest_verdict(0, {**zero, "xfailed": 1})[0] == runner.BLOCKED
    assert runner.pytest_verdict(0, {**zero, "tests": 0, "passed": 0})[0] == runner.BLOCKED
    assert runner.pytest_verdict(1, {**zero, "failed": 1})[0] == runner.FAIL


def test_lab_local_the_e4_subset_is_the_composition_lanes_regression():
    """The E4 subset the composition lanes ran with the switches OFF (COMPOSITION-3 row 8)."""
    assert runner.E4_SUITES == ("tests/g", "tests/w", "tests/contracts",
                                "tests/i/test_packaging.py")


def test_lab_local_a_key_pinned_journey_is_not_run_with_its_owners_exact_rerun():
    """WR-LDP-1 landed: every Lab journey whose backend accepts lab-on runs on it; one whose
    backend binds another key's resource (p2's teacher-fake port, t2i's ClickHouse) is NOT RUN
    naming its WR with the owner's exact rerun (never skipped silently or run on that key)."""
    for name, spec in runner.JOURNEYS.items():
        row = runner.journey_row(name, spec)
        if spec["foreign"]:
            assert row["status"] == runner.NOT_RUN, name
            assert f"INFRX_D_TASK={spec['key']}" in row["rerun"]
            assert row["reason"].startswith(f"NOT RUN[{spec['foreign'][0]}] "), name
        else:
            assert row["status"] is None and f"INFRX_D_TASK={runner.KEY}" in row["rerun"]
    assert {n for n, s in runner.JOURNEYS.items() if not s["foreign"]} == \
        {"datasets", "releases", "evaluations"}


def test_lab_local_the_required_cases_are_what_the_scenario_module_defines():
    defined = set(re.findall(r"^def (test_o\d\d_\w+)\(",
                             (HERE / "scenarios_on.py").read_text(), re.M))
    assert {name for names in runner.REQUIRED.values() for name in names} == defined
    assert set(runner.REQUIRED) == set(runner.SCENARIOS)


def test_lab_local_the_key_is_tasklocal_in_the_lab_band_and_the_block_is_e3ls_lock():
    """`lab-on` is a tasklocal key in 57500-57599 colliding with nothing; the composition
    borrows E3L's block only under E3L's own runner lock (one user of the block at a time)."""
    from infrx.contracts import tasklocal
    services = tasklocal.local_services(runner.KEY)
    assert set(services) == {"postgres", "valkey", "valkey-q"}
    assert all(57500 <= s.host_port <= 57599 for s in services.values())
    reserved = tasklocal.all_host_ports()
    assert {reserved[s.host_port] for s in services.values()} == \
        {f"{runner.KEY}/{name}" for name in services}
    assert runner.NAMESPACE == "e3l" and runner.LOCK.name == "infrx-e3l.runner.lock"


def test_lab_local_the_lab_web_gets_lab_jsons_names_and_no_service_key():
    """The Lab web runs on exactly the names I2L declares for it (plus process basics), every
    API URL set; never a service-role key."""
    lw = world()
    declared = {row["name"] for row in json.loads(
        (REPO / "infra" / "lab" / "app" / "lab.json").read_text())["env"]["lab-web"]}
    env = lw.lab_web_env("http://gw", "http://sb")
    assert set(env) - {"PATH", "HOME", "NEXT_TELEMETRY_DISABLED"} == declared
    assert env["LAB_CONTROL_URL"] == lw.control_url()
    assert not any("SERVICE_ROLE" in name for name in env)
    assert env["NEXT_PUBLIC_LAB_URL"].startswith("https://")


def test_lab_local_pending_roles_are_proven_to_refuse_by_name():
    """A pending role is NOT RUN only on its named exit-2 refusal (R198/R211); any other
    failure of the same role is a FAIL."""
    lw = world()
    assert set(lw.PENDING_ROLES) < set(lw.ROLES)
    assert {"eval", "judge", "datasets"}.isdisjoint(lw.PENDING_ROLES)
    for lane, marker in lw.PENDING_ROLES.values():
        assert lane and marker


def test_lab_local_a_journey_passes_only_when_every_case_ran_and_passed(tmp_path, monkeypatch):
    """journeys() through a stubbed `node`: a non-zero exit is FAIL; a skipped, todo or
    cancelled case, no case at all, or no summary is BLOCKED; only all-passed is PASS."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "node").write_text('#!/bin/sh\nprintf "%b" "$JOURNEY_OUT"\nexit "$JOURNEY_EXIT"\n')
    (stub / "node").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}{os.environ['PATH']}")
    (tmp_path / "apps" / "lab").mkdir(parents=True)          # node's cwd, as in the tree
    monkeypatch.setattr(runner, "REPO", tmp_path)
    summary = "# tests {}\\n# pass {}\\n# fail {}\\n# skipped {}\\n# todo 0\\n"
    for code, text, want in ((1, summary.format(2, 1, 1, 0), runner.FAIL),
                             (0, summary.format(2, 0, 0, 2), runner.BLOCKED),
                             (0, summary.format(0, 0, 0, 0), runner.BLOCKED),
                             (0, "no summary\\n", runner.BLOCKED),
                             (0, summary.format(3, 3, 0, 0), runner.PASS)):
        monkeypatch.setenv("JOURNEY_OUT", text)
        monkeypatch.setenv("JOURNEY_EXIT", str(code))
        rows = {row["stage"]: row for row in runner.journeys(tmp_path)}
        assert rows["journey:datasets"]["status"] == want, (code, text)


def test_lab_local_the_control_factory_runs_on_its_own_login_never_the_owner():
    """LDP-R4: the control DSN is 0043's `infrx_lab_control` with a fresh password set as the
    operator does (ALTER ROLE ... LOGIN PASSWORD), never the owner login."""
    lw = world()
    done = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, statement):
            done.append(statement)

    dsn = lw.lab_control_dsn("lab_local_x", connect=lambda *a, **k: Conn())
    owner = lw.harness.pg_dsn("lab_local_x")
    assert dsn.startswith("postgresql://infrx_lab_control:") and dsn != owner
    assert dsn.split("@", 1)[1] == owner.split("@", 1)[1]
    assert len(done) == 1 and "infrx_lab_control" in repr(done[0]) and "login" in repr(done[0])


RECORDED = REPO / "research" / "plan" / "evidence" / "e" / "E4ON-raw-28c9c2cc" / "verdict.json"
R198_CASE = "tests.w.test_worker_main::" \
    "test_worker_main_pg__the_pilot_box_starts_the_real_worker_and_waits_for_it"


def recorded(path: Path = RECORDED) -> tuple[list, dict]:
    payload = json.loads(path.read_text())
    return payload["stages"], {entry["id"]: entry for entry in payload["scenarios"]}


def test_lab_local_r222_the_recorded_28c9c2cc_verdict_is_not_accepted():
    """R222/R235 over the recorded E4-ON at 28c9c2cc: o04's login FAIL (LDP-F7), o05 without
    its control-factory case, the e4-on pins (WR-LDP-5) and the WR-LDP-1 journeys keep it open;
    o03's refusing roles (product WRs, P-11) and o07's typed-unavailable ports do not."""
    check = runner.r222(*recorded())
    assert check["accepted"] is False
    assert set(check["open"]) == {"o04", "o05", "e4-on", "journey:releases",
                                  "journey:pipelines", "journey:evaluations", "journey:traces"}


def _green() -> tuple[list, dict]:
    """A verdict where every stage and scenario PASSes (the r222 fixtures edit one thing)."""
    scenarios = runner.classify(junit(*everything()))
    stages = [{"stage": name, "status": runner.PASS} for name in
              ("lab-build", "e4-on", "scenarios", *(f"journey:{j}" for j in runner.JOURNEYS))]
    return stages, scenarios


def test_lab_local_r222_excuses_only_the_ruled_classes():
    """accepted only when every non-PASS cell is a NOT RUN naming ruled lanes only, or a
    FAIL that is exactly a by-design case (R198's pilot-box worker, R237's all-switches App
    gateway); an in-scope FAIL, BLOCKED, INVALID or an absent required case stays open."""
    stages, scenarios = _green()
    assert runner.r222(stages, scenarios) == {"accepted": True, "open": {}, "by_design": {}}
    o03, o05 = runner.REQUIRED["o03"][1], runner.REQUIRED["o05"][0]

    def one(name, kind, message):
        return runner.classify(junit(*[c for c in everything() if c[0] != name],
                                     (name, kind, message)))
    assert runner.r222(stages, one(o03, "skipped", "NOT RUN[WR-B3-3] refuses"))["accepted"]
    assert runner.r222(stages, one(o03, "skipped", "NOT RUN[P-11] no pass"))["accepted"]
    for message in ("NOT RUN[WR-ZZZ] unruled", "NOT RUN[WR-B3-3,WR-ZZZ] mixed",
                    "skipped with no class", "", "BLOCKED[gateway] down"):
        assert runner.r222(stages, one(o03, "skipped", message))["open"] == \
            {"o03": runner.classify(junit(*[c for c in everything() if c[0] != o03],
                                            (o03, "skipped", message)))["o03"]["status"]}
    assert runner.r222(stages, one(o03, "failure", "AssertionError: x"))["open"] == \
        {"o03": runner.FAIL}
    known = runner.r222(stages, one(o05, "failure", "AssertionError: 503 on every family"))
    assert known["accepted"] and known["by_design"] == {o05: runner.BY_DESIGN[o05]}
    dropped = runner.classify(junit(*[c for c in everything() if c[0] != o05]))
    assert runner.r222(stages, dropped)["open"] == {"o05": runner.NOT_RUN}


def test_lab_local_r222_the_e4_stage_is_excused_only_for_its_by_design_case():
    """The e4-on stage's FAIL is excused only when every failed id is R198's (LDP-F4: the
    pilot-box worker inherits LAB_EVAL_WORKER=true and refuses by name)."""
    stages, scenarios = _green()

    def e4(failed_ids, status=runner.FAIL):
        rows = [row for row in stages if row["stage"] != "e4-on"]
        return rows + [{"stage": "e4-on", "status": status,
                        "counts": {"failed_ids": failed_ids, "errors": 0}}]
    by_design = runner.r222(e4([R198_CASE]), scenarios)
    assert by_design["accepted"] and R198_CASE in by_design["by_design"]
    assert runner.r222(e4([R198_CASE, "tests.g.x::test_y"]), scenarios)["open"] == \
        {"e4-on": runner.FAIL}
    assert runner.r222(e4([]), scenarios)["open"] == {"e4-on": runner.FAIL}
    assert runner.r222(e4([], runner.BLOCKED), scenarios)["open"] == {"e4-on": runner.BLOCKED}


def test_lab_local_the_control_login_answers_as_the_owner_login():
    """R251/WR-LW8-2: on infrx_lab_control each family answers exactly as the same factory on
    the owner login; a family that is not 200 is only its pending typed 503 (NOT RUN), never a
    difference (LCR-F1's shape), a 500 or a typed 503 of a family that is not pending."""
    lw = world()
    typed = '503 {"refusal":"unavailable"}'
    failed = '503 {"detail":"the datasets service failed"}'
    assert lw.judge_login({"evals": typed}, {"evals": typed}, {"evals"}) == ({}, {"evals": typed})
    wrong, pending = lw.judge_login({"datasets": failed}, {}, {"datasets"})
    assert set(wrong) == {"datasets"} and pending == {}
    assert set(lw.judge_login({"datasets": failed}, {"datasets": failed}, {"datasets"})[0]) \
        == {"datasets"}
    assert set(lw.judge_login({"control": typed}, {"control": typed}, {"evals"})[0]) == {"control"}
