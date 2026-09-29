"""LAB-LOCAL layer 1: the E4-ON runner's own decisions and the composition's switch list, with
no stack (`pytest tests/integration` collects this file; the scenarios run only through
runner.py). Each case names what a broken runner would report falsely."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
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
    """A Lab journey whose backend refuses this key is NOT RUN (never skipped silently or
    run on another lane's port); an unpinned one runs on lab-on."""
    for name, spec in runner.JOURNEYS.items():
        row = runner.journey_row(name, spec)
        if spec["pinned"]:
            assert row["status"] == runner.NOT_RUN, name
            assert f"INFRX_D_TASK={spec['pinned']}" in row["rerun"] and "WR-LDP-1" in row["reason"]
        else:
            assert row["status"] is None and f"INFRX_D_TASK={runner.KEY}" in row["rerun"]
    assert runner.JOURNEYS["datasets"]["pinned"] is None


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
