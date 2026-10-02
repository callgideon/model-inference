"""E5L layer 1: the runner's own decisions, with no stack (`pytest tests/integration` collects
this file; the scenarios run only through runner.py). Each case names what a broken runner
would report falsely."""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
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
    return {path.name: path.read_text()
            for path in (*HERE.glob("scenarios_*.py"), HERE / runner.BOX_VARIANT)}


def test_e5l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases():
    """The cells are exactly tasks.json E5L's test_ids, each carried by a scenario, and the
    brief's faults are all present (revoke mid-queue, expire content, drop the projection,
    time out a submit, restart a worker)."""
    tasks = json.loads((REPO / "research/plan/tasks.json").read_text())
    e5l = next(task for task in tasks["tasks"] if task["id"] == "E5L")
    assert tuple(e5l["test_ids"]) == runner.TEST_IDS
    for tid in runner.TEST_IDS:
        assert any(tid in spec["test_ids"] for spec in runner.SCENARIOS.values()), tid
    assert set(runner.REQUIRED) == set(runner.SCENARIOS) and len(runner.SCENARIOS) == 11
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


RECORDED_DF = REPO / "research/plan/evidence/e/E5L-raw-df837faf/verdict.json"


def recorded(path: Path = RECORDED_DF) -> dict:
    """A recorded verdict's scenarios, as r222 reads them (each with its own lanes)."""
    return {s["id"]: {k: s[k] for k in ("status", "cases", "reasons", "lanes")}
            for s in json.loads(path.read_text())["scenarios"]}


def test_e5l_r222_accepts_only_a_not_run_out_of_local_scope(monkeypatch):
    """R222/R234/R235/R253, as lab_evaluate's runner computes it: the verdict's `r222` is
    accepted with no FAIL and every NOT RUN waiting only on a ruled out-of-scope class, by its
    own reason and with its rerun. o01 is bound (WR-C6-CAPTURE merged, #54): it declares no
    lane, so its NOT RUN is now in scope and open; the recorded df837faf verdict, whose o01
    waited on WR-C6-CAPTURE, is still judged on its own lanes and stays accepted. A FAIL
    whatever its message, a NOT RUN on in-scope work, a scenario never run or NOT RUN for
    another reason stays open."""
    assert runner.OUT_OF_SCOPE == {"LAB-E2E": "lab-e2e UI",
                                   "WR-C6-CAPTURE": "product WR: WR-C6-CAPTURE"}
    assert runner.SCENARIOS["o01"]["lanes"] == [], "o01 is bound: it waits on no lane"
    assert runner.reproduce("o01") == ("apps/infrx-api/.venv/bin/python "
                                       "tests/integration/lab_observe/runner.py --out <dir> --only o01")
    wait = "NOT RUN[WR-C6-CAPTURE] no seam; rerun after the merge: x --only o01"
    others = [c for sid in runner.SCENARIOS if sid != "o01" for c in everything(sid)]
    o01 = [*everything("o01")[:2], (runner.REQUIRED["o01"][2], "skipped", wait),
           *everything("o01")[3:]]
    result = runner.classify(junit(*others, *o01))
    assert runner.gate(result) == "NOT RUN"
    assert runner.r222(result) == {"accepted": False, "open": {"o01": "NOT RUN"}}, \
        "a bound o01 skipping on the old wait is excused"
    assert runner.r222(runner.classify(junit(*others, *everything("o01")))) == \
        {"accepted": True, "open": {}}
    assert "o01" in runner.r222(runner.classify(junit(*others)))["open"], "never run is open"
    # the recorded df837faf verdict: o01 NOT RUN[WR-C6-CAPTURE], judged on its own lanes
    statuses = recorded()
    assert statuses["o01"]["lanes"] == ["WR-C6-CAPTURE"]
    assert runner.r222(statuses) == {"accepted": True, "open": {}}
    with monkeypatch.context() as patch:            # a NOT RUN on in-scope work stays open
        patch.delitem(runner.OUT_OF_SCOPE, "WR-C6-CAPTURE")
        assert runner.r222(statuses) == {"accepted": False, "open": {"o01": "NOT RUN"}}
    assert runner.r222({**statuses, "o01": {**statuses["o01"], "status": "FAIL"}}) == \
        {"accepted": False, "open": {"o01": "FAIL"}}, "R234: a FAIL is never excused"
    assert "o01" in runner.r222({**statuses, "o01": {**statuses["o01"], "cases": {}}})["open"]
    # the recorded bd13f72 verdict: o10's LAB-E2E wait is out of scope, COMPOSITION never was
    statuses = recorded(REPO / "research/plan/evidence/e/E5L-raw-bd13f72/gate/verdict.json")
    assert runner.r222(statuses) == {"accepted": False, "open": {"o01": "NOT RUN"}}
    statuses["o01"] = {"status": "PASS", "cases": {}, "reasons": [], "lanes": []}
    assert runner.r222(statuses) == {"accepted": True, "open": {}}
    # WR-LO3-RV4: the reason names the scenario's own lanes, not any NOT RUN (a stale class) ...
    stale = {"status": "NOT RUN", "cases": {"c": "NOT RUN"}, "lanes": ["WR-C6-CAPTURE"],
             "reasons": ["c: " + wait.replace("WR-C6-CAPTURE", "COMPOSITION")]}
    assert runner.r222({**statuses, "o01": stale})["open"] == {"o01": "NOT RUN"}
    # ... and every lane is a ruled class, not merely one of them
    mixed = {"status": "NOT RUN", "cases": {"c": "NOT RUN"},
             "lanes": ["WR-C6-CAPTURE", "COMPOSITION"],
             "reasons": ["c: NOT RUN[WR-C6-CAPTURE,COMPOSITION] x --only o01"]}
    assert runner.r222({**statuses, "o01": mixed})["open"] == {"o01": "NOT RUN"}
    # every reason, each carrying the rerun, must be the wait (not just one of them)
    other = {**mixed, "lanes": ["WR-C6-CAPTURE"],
             "reasons": [f"c: {wait}", "d: skipped for another reason; x --only o01"]}
    assert runner.r222({**statuses, "o01": other})["open"] == {"o01": "NOT RUN"}


def test_e5l_o01s_recorded_reason_keeps_its_rerun_inside_the_cut():
    """WR-LO3-RV2 (R253): o01's recorded NOT RUN (df837faf's reason, rebuilt through
    `not_run` and recorded as pytest's JUnit does: the message, then the skip's location text)
    goes through case_status and classify: the recorded reason still carries reproduce('o01')
    after the 400-char cut, so r222 excuses it under its recorded lane; a reason that lost its
    rerun stays open, never silently excused."""
    import observe_world
    [reason] = recorded()["o01"]["reasons"]
    why = reason.split("NOT RUN[WR-C6-CAPTURE] ")[1].split("; rerun after the merge")[0]
    with pytest.raises(pytest.skip.Exception) as skipped:
        observe_world.not_run("o01", "WR-C6-CAPTURE", why=why)
    message = str(skipped.value)
    case = ET.Element("testcase", classname="x", name=runner.REQUIRED["o01"][2])
    ET.SubElement(case, "skipped", type="pytest.skip", message=message).text = \
        f"{HERE / 'scenarios_trace.py'}:59: {message}"
    status, reason = runner.case_status(case)
    assert status == "NOT RUN" and runner.reproduce("o01") in reason, reason
    others = [c for sid in runner.SCENARIOS if sid != "o01" for c in everything(sid)]
    suites = ET.fromstring(junit(*others, *everything("o01")[:2], *everything("o01")[3:]))
    suites.find("testsuite").append(case)
    result = runner.classify(ET.tostring(suites, encoding="unicode"))
    [recorded_reason] = result["o01"]["reasons"]
    assert runner.reproduce("o01") in recorded_reason, recorded_reason
    result["o01"]["lanes"] = ["WR-C6-CAPTURE"]                      # as df837faf recorded it
    assert runner.r222(result) == {"accepted": True, "open": {}}
    result["o01"]["reasons"] = [recorded_reason.split(" --only o01")[0]]  # cut before its rerun
    assert runner.r222(result)["open"] == {"o01": "NOT RUN"}


def test_e5l_the_verdict_carries_r222_and_each_scenarios_scope(monkeypatch, tmp_path):
    """WR-LO3-RV3 (R235): verdict.json carries `r222` and, per scenario, the ruled class of each
    lane it waits on. Through main with the namespace lock held elsewhere: no stack call."""
    def busy(*_):
        raise BlockingIOError
    monkeypatch.setattr(runner.fcntl, "flock", busy)
    monkeypatch.setattr(runner, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(runner, "pins", lambda harness: {})
    monkeypatch.setenv("INFRX_E2_NAMESPACE", "e5l")
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert runner.main(["--out", str(tmp_path / "v")]) == runner.EXIT["BLOCKED"]
    verdict = json.loads((tmp_path / "v" / "verdict.json").read_text())
    assert verdict.get("r222") == {"accepted": False,
                                   "open": {sid: "BLOCKED" for sid in runner.SCENARIOS}}
    scope = {entry["id"]: entry.get("scope") for entry in verdict["scenarios"]}
    assert scope == {sid: {lane: runner.OUT_OF_SCOPE.get(lane, "local (in scope)")
                           for lane in spec["lanes"]} for sid, spec in runner.SCENARIOS.items()}
    assert scope["o01"] == {}, "o01 is bound (WR-C6-CAPTURE): it waits on nothing"


def test_e5l_every_unbound_case_is_not_run_without_touching_a_stack():
    """The cases waiting on a lane skip NOT RUN before any stack call, with the exact rerun."""
    import scenarios_judge
    import scenarios_trace
    unbound = [getattr(module, name) for module in (scenarios_trace, scenarios_judge)
               for name in dir(module) if name.startswith("test_o")
               and "not_run(" in (HERE / f"{module.__name__}.py").read_text().split(
                   f"def {name}(")[1].split("\ndef ")[0]]
    assert unbound == [], [case.__name__ for case in unbound]   # o01 (WR-C6-CAPTURE), o10 bound
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


PROBE = """
import json, sys
sys.path[:0] = [{here!r}, {integration!r}]
import observe_world as ow
h = ow.harness
seen = []
h.run = lambda argv, **kw: seen.append((argv, kw.get("env") or dict()))
h.compose("ps")
h._docker_ls = lambda kind: ["infrx-e5l_postgres-data", "infrx-e5l_s3-data",
                             h.PROJECT + "_postgres-data"]
h._labels = lambda kind, name: dict()
print(json.dumps(dict(project=h.PROJECT, prefix=h.PREFIX, network=h.NETWORK,
                      volumes=list(h.PROJECT_VOLUMES), namespace=h.NAMESPACE, ports=h.PORTS,
                      database=h.PG_DATABASE, objects=h.OBJECT_PREFIX, keys=h.VALKEY_PREFIX,
                      postgres=h.container_of("postgres"), argv=seen[0][0],
                      compose_project=seen[0][1]["INFRX_E2_PROJECT"],
                      candidates=h._candidates("volume"))))
"""


def probe(project: str | None):
    """observe_world's harness in a fresh process (it is process-global), `INFRX_E5L_PROJECT`
    set or not: its names, the compose argv it would run, and the volumes it would consider."""
    import os
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "INFRX_E5L_PROJECT"}
    env |= {"INFRX_E2_NAMESPACE": "e5l", **({"INFRX_E5L_PROJECT": project} if project else {})}
    done = subprocess.run([sys.executable, "-c", PROBE.format(
        here=str(HERE), integration=str(HERE.parent))], env=env, cwd=REPO,
        capture_output=True, text=True, timeout=120)
    return done.returncode, (json.loads(done.stdout.strip().splitlines()[-1])
                             if done.returncode == 0 else done.stderr[-600:])


def test_e5l_a_compose_project_override_moves_only_the_compose_names():
    """`INFRX_E5L_PROJECT=e5l2` (the foreign `infrx-e5l_*` volumes a finished clone left must
    never be touched): the compose project, containers, volumes and network are `infrx-e5l2*`
    and `infrx-e5l_*` is not even a candidate; the namespace, the tasklocal ports, the database
    and the key/object prefixes stay e5l's. Unset, every name is e5l's. Oracles: an ignored
    override provisions over the foreign volumes (refused, every cell BLOCKED); one that moves
    the namespace moves the ports out of the e5l block."""
    code, default = probe(None)
    assert code == 0, default
    code, moved = probe("e5l2")
    assert code == 0, moved
    assert default["project"] == "infrx-e5l" and default["compose_project"] == "infrx-e5l"
    assert (moved["project"], moved["prefix"], moved["network"], moved["postgres"]) == (
        "infrx-e5l2", "infrx-e5l2-", "infrx-e5l2_default", "infrx-e5l2-postgres")
    assert moved["volumes"] == [f"infrx-e5l2_{v}" for v in ("postgres-data", "clickhouse-data",
                                                            "s3-data")]
    assert moved["argv"][:4] == ["docker", "compose", "-p", "infrx-e5l2"]
    assert moved["compose_project"] == "infrx-e5l2"
    assert moved["candidates"] == ["infrx-e5l2_postgres-data"], "the foreign volumes are seen"
    for same in ("namespace", "ports", "database", "objects", "keys"):
        assert moved[same] == default[same], same
    assert moved["ports"]["postgres"] == 57132 and moved["namespace"] == "e5l"
    code, refused = probe("e3c")
    assert code != 0 and "INFRX_E5L_PROJECT" in refused, "another gate's project accepted"


def _gate():
    """LAB-E2E's gate half (`apps/lab/tests/e2e/gate.py`), by path (R213): the four gates' UI
    cells (o10, j10, i08's UI, k10's UI) turn its `missing()` into PASS, FAIL or INVALID."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "e5l_e2e_gate", REPO / "apps" / "lab" / "tests" / "e2e" / "gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_e5l_the_ui_cell_passes_only_a_green_e2e_suite_and_names_its_uncomposed_ports():
    """0-F2. Oracles: a red suite (exit 1, fail 3, its record still written), a skipped or
    cancelled case, a missing record or a single pass is a FAIL, never a PASS; a busy l4 key or
    Docker is HarnessError (INVALID), never a FAIL; a green suite answers the ports its record
    says the gateway's own composition does not carry."""
    gate = _gate()
    green = {"command": "c", "exit": 0, "pass": 7, "fail": 0, "skipped": 0, "cancelled": 0,
             "harness": None, "tail": "", "record": {"composed": {"a": True, "b": False}}}
    assert gate.missing(green) == ["b"]
    assert gate.missing({**green, "record": {"composed": {}}}) == []
    for red in ({"exit": 1, "fail": 3, "pass": 4, "record": {"composed": {}}},
                {"skipped": 1}, {"cancelled": 1}, {"record": None}, {"pass": 1},
                {"exit": 1}, {"fail": 1}):
        with pytest.raises(AssertionError):
            gate.missing({**green, **red})
    busy = {**green, "exit": 1, "fail": 1, "harness": "HarnessBusy: l4 is held"}
    with pytest.raises(gate.HarnessError, match="HarnessBusy"):
        gate.missing(busy)
    with pytest.raises(AssertionError):
        gate.missing({**busy, "harness": None})


def test_e5l_the_echo_oracle_rejects_a_partial_scrub():
    """LO5-RV1 (HM2): o01's echo oracle rejects a record that still holds a 16-byte tail of the
    secret (a scrub that redacted only the key's head), not only the whole token; the failure
    names the secret's tail, never its bytes. The full KEY_SHAPE scrub passes."""
    from types import SimpleNamespace
    import scenarios_trace
    sys.path.insert(0, str(REPO / "apps" / "infrx-api"))
    from infrx.gateway.capture import KEY_SHAPE, REDACTED
    secret = "sk-infrx-" + "A1b2C3d4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0"
    tenant = SimpleNamespace(org_id="o", secret=secret)
    record = f'{{"q":"Describe the van. {secret}"}}\nthe van'.encode()

    def trip(content: bytes):
        async def read_content(_org, _request):
            return content
        return SimpleNamespace(traces=SimpleNamespace(
            retention=SimpleNamespace(read_content=read_content)))
    scenarios_trace.holds_no_token(trip(KEY_SHAPE.sub(REDACTED, record)), tenant, "r", "async")
    partial = re.compile(rb"sk-infrx-[A-Za-z0-9_-]{8}").sub(REDACTED, record)
    assert secret.encode() not in partial and REDACTED in partial     # the old oracle's view
    with pytest.raises(AssertionError, match="the secret's tail") as failed:
        scenarios_trace.holds_no_token(trip(partial), tenant, "r", "async")
    assert secret[-16:] not in str(failed.value)


def test_e5l_an_environment_blocked_ui_cell_is_not_run_env_never_a_fail():
    """LO5-RV2: LAB-E2E's `EnvironmentBlocked` (the Lab checkout without node_modules or a
    browser) makes o10 NOT RUN[ENV] naming its prerequisite and the rerun, never a product
    FAIL; it stays open under R222 (the gate is not accepted without o10)."""
    cases = everything("o10", "failure",
                       "lab_observe.lab_e2e_gate.EnvironmentBlocked: ENOENT "
                       "/x/apps/lab/node_modules: run pnpm install --frozen-lockfile in apps/lab")
    result = runner.classify(junit(*everything("o01"), *cases))
    o10 = result["o10"]
    assert o10["status"] == "NOT RUN", o10
    [reason] = o10["reasons"]
    assert "NOT RUN[ENV]" in reason and "pnpm install --frozen-lockfile" in reason
    assert f"{runner.RUNNER} --out <dir> --only o10" in reason
    assert runner.cells(result)["CONSOLE-FLOWS"] == "NOT RUN"
    assert "o10" in runner.r222(result)["open"]


def test_e5l_the_pinned_base_is_the_merge_base_with_the_integration_branch(monkeypatch):
    """LO5-RV3: verdict.json's pins.base is `git merge-base HEAD claude/consumer-v1` when that
    ref resolves, else the runner's build base - never a stale literal on a later tip."""
    from types import SimpleNamespace
    asked = []

    def git(argv, **_):
        asked.append(argv)
        known = argv[1] == "merge-base" and argv[-1] == "claude/consumer-v1"
        return SimpleNamespace(stdout="abc123\n" if known else "", returncode=0 if known else 1)
    harness = SimpleNamespace(compose_images=lambda: {})
    monkeypatch.setattr(runner.subprocess, "run", git)
    assert runner.pins(harness)["base"] == "abc123"
    assert ["git", "merge-base", "HEAD", "claude/consumer-v1"] in asked
    monkeypatch.setattr(runner, "BASE_REF", "no/such-ref")
    assert runner.pins(harness)["base"] == runner.BASE
