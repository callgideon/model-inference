"""Layer 1: the orchestration itself, with docker and the shells stubbed out.

r1 review B2: `run.py` carries the invariants the whole task rests on - PENDING is never a
pass, a failing stage is never a pass, an undetected canary fails the run, teardown always
happens - and NOTHING imported it, so all seven reviewer mutants of its exit mapping
survived. Nor did anything exercise `harness.up`/`down`'s refusal to touch foreign resources,
`busy_ports`, `docker_available`, `apply_migrations(require_fresh=)` or the fake server's
loopback default. They are all guarded here, and none of it needs a container: the stubs
replace `harness.*` and the subprocess layer.

No mocking framework: a plain function or a tiny object assigned onto the module, restored in
`finally`. That is the same injection the contracts' own fakes use.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness                                          # noqa: E402
import pgstate                                          # noqa: E402
import run as runner                                    # noqa: E402


@contextmanager
def patched(target, **attributes):
    """Assign attributes onto a module, restore them afterwards. Restores a missing
    attribute by deleting it, so a typo cannot leave a stub behind."""
    missing = object()
    previous = {name: getattr(target, name, missing) for name in attributes}
    for name, value in attributes.items():
        setattr(target, name, value)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is missing:
                delattr(target, name)
            else:
                setattr(target, name, value)


def fresh_report() -> runner.Report:
    return runner.Report()


# ------------------------------------------------------------------ exit mapping

def test_the_exit_code_maps_pass_pending_and_fail_and_never_confuses_them():
    """The whole honesty contract in one function: 0 only when everything passed, 3 when a
    service-backed stage could not run, 1 when anything failed - and FAIL wins over PENDING,
    because a run with both is a failed run, not a pending one."""
    report = fresh_report()
    assert report.exit_code == 0, "an empty report is vacuously fine"
    report.add("a", runner.PASS, "ok")
    assert report.exit_code == 0
    report.add("b", runner.SKIP, "not applicable")
    assert report.exit_code == 0, "a skip is not a pending"
    report.add("c", runner.PENDING, "no docker")
    assert report.exit_code == 3, "PENDING must never be reported as a pass"
    report.add("d", runner.FAIL, "broken")
    assert report.exit_code == 1, "a failure outranks a pending"

    only_fail = fresh_report()
    only_fail.add("x", runner.FAIL, "broken")
    assert only_fail.exit_code == 1
    assert {runner.PASS, runner.FAIL, runner.PENDING, runner.SKIP} == {
        "PASS", "FAIL", "PENDING", "SKIP"}


def test_the_json_report_carries_every_stage_and_its_status():
    report = fresh_report()
    report.add("one", runner.PASS, {"n": 1})
    report.add("two", runner.PENDING, "why not")
    import json
    payload = json.loads(report.as_json())
    assert [stage["stage"] for stage in payload["stages"]] == ["one", "two"]
    assert [stage["status"] for stage in payload["stages"]] == ["PASS", "PENDING"]
    assert payload["exit_code"] == 3


# ------------------------------------------------------------------ preflight

def test_preflight_reports_pending_when_docker_is_unusable_and_never_pass():
    report = fresh_report()
    with patched(harness, docker_available=lambda: (False, "daemon unreachable")):
        assert runner.preflight(report, want_services=True) is False
    entry = report.stages[-1]
    assert entry["status"] == runner.PENDING and "daemon unreachable" in str(entry["detail"])
    assert report.exit_code == 3


def test_preflight_fails_on_a_foreign_resource_and_touches_nothing():
    """r1 B1 + the same-pass traceback: a label-only stranger must be a REPORTED FAIL, and
    nothing may be removed on the way."""
    removed = []
    stranger = [{"kind": "volume", "name": "infrx-e2_postgres-data", "project": "infrx-e2",
                 "checkout": None, "why": "no infrx-e2 checkout label"}]
    report = fresh_report()
    with patched(harness,
                 docker_available=lambda: (True, "29.6.2"),
                 foreign_resources=lambda: stranger,
                 owned_containers=lambda: ["infrx-e2-postgres"],
                 down=lambda: removed.append("down") or [],
                 busy_ports=lambda: {},
                 images_present=lambda: []):
        assert runner.preflight(report, want_services=True) is False
    entry = report.stages[-1]
    assert entry["status"] == runner.FAIL
    assert "infrx-e2_postgres-data" in str(entry["detail"])
    assert removed == [], "a foreign resource must not lead to any removal"


def test_preflight_turns_a_harness_error_into_a_reported_failure():
    """Same pass: a label-only foreign container used to wedge the run with an uncaught
    HarnessError traceback instead of a stage."""
    report = fresh_report()
    def explode():
        raise harness.HarnessError("refusing to touch 'infrx-e2-postgres'")
    with patched(harness, docker_available=lambda: (True, "29.6.2"),
                 foreign_resources=explode):
        assert runner.preflight(report, want_services=True) is False
    assert report.stages[-1]["status"] == runner.FAIL
    assert "refusing to touch" in str(report.stages[-1]["detail"])


def test_preflight_fails_on_a_busy_task_local_port():
    report = fresh_report()
    with patched(harness, docker_available=lambda: (True, "29.6.2"),
                 foreign_resources=lambda: [], owned_containers=lambda: [],
                 images_present=lambda: [], busy_ports=lambda: {"postgres": 55532}):
        with patched(runner, fake_server_orphans=lambda: []):
            assert runner.preflight(report, want_services=True) is False
    assert report.stages[-1]["status"] == runner.FAIL
    assert "55532" in str(report.stages[-1]["detail"])


def test_preflight_fails_on_an_orphaned_fake_server_this_harness_owns():
    """Same pass: run.py killed by SIGTERM used to orphan a fake vLLM on 55581; the next run
    must say so rather than fail to bind."""
    report = fresh_report()
    with patched(harness, docker_available=lambda: (True, "29.6.2"),
                 foreign_resources=lambda: [], owned_containers=lambda: [],
                 images_present=lambda: [], busy_ports=lambda: {}):
        with patched(runner, fake_server_orphans=lambda: [4242]):
            assert runner.preflight(report, want_services=True) is False
    assert report.stages[-1]["status"] == runner.FAIL
    assert "4242" in str(report.stages[-1]["detail"])


def test_preflight_skips_cleanly_for_layer_one():
    report = fresh_report()
    assert runner.preflight(report, want_services=False) is False
    assert report.stages[-1]["status"] == runner.SKIP
    assert report.exit_code == 0, "layer 1 is not pending"


def test_preflight_passes_and_records_the_checkout_it_owns():
    report = fresh_report()
    with patched(harness, docker_available=lambda: (True, "29.6.2"),
                 foreign_resources=lambda: [], owned_containers=lambda: [],
                 images_present=lambda: [], busy_ports=lambda: {}):
        with patched(runner, fake_server_orphans=lambda: []):
            assert runner.preflight(report, want_services=True) is True
    entry = report.stages[-1]
    assert entry["status"] == runner.PASS
    assert entry["detail"]["working_dir"] == harness.working_dir()


# ------------------------------------------------------------------ services

def test_a_provisioning_failure_is_pending_and_never_a_pass():
    report = fresh_report()
    def refuse(**_):
        raise harness.HarnessError("port bind failed twice (ephemeral-range collision, R-c)")
    with patched(harness, up=refuse):
        assert runner.services(report, pull=False) is False
    entry = report.stages[-1]
    assert entry["status"] == runner.PENDING and "bind failed twice" in str(entry["detail"])
    assert report.exit_code == 3, "a stack we could not create is pending, not passed"


def test_services_reports_the_database_it_provisioned():
    report = fresh_report()
    with patched(harness, up=lambda **_: None,
                 wait_all=lambda *a, **k: {"postgres": "17.6"},
                 provision_database=lambda: {"database": "infrx_e2", "template": "postgres"},
                 owned_containers=lambda: ["infrx-e2-postgres"]):
        assert runner.services(report, pull=False) is True
    assert report.stages[-1]["detail"]["database"]["database"] == "infrx_e2"


# ------------------------------------------------------------------ canary

def test_an_undetected_canary_fails_the_run():
    """The canary proves failures are seen. If the runner cannot see the canary either, that
    is the worst possible outcome and must not be a pass."""
    report = fresh_report()
    green = {"argv": "pnpm test", "cwd": ".", "exit": 0, "seconds": 1.0, "counts": {},
             "named": False, "tail": "all good"}
    with patched(runner, shell=lambda *a, **k: green):
        runner.canary(report)
    entry = report.stages[-1]
    assert entry["status"] == runner.FAIL
    assert any("NOT detected" in problem for problem in entry["detail"]["problems"])
    assert report.exit_code == 1


def test_a_canary_that_fails_without_naming_itself_also_fails_the_run():
    report = fresh_report()
    unnamed = {"argv": "pytest", "cwd": ".", "exit": 1, "seconds": 1.0, "counts": {"failed": 1},
               "named": False, "tail": "1 failed"}
    with patched(runner, shell=lambda *a, **k: unnamed):
        runner.canary(report)
    assert report.stages[-1]["status"] == runner.FAIL
    assert any("no canary named" in problem
               for problem in report.stages[-1]["detail"]["problems"])


def test_a_detected_and_named_canary_passes():
    report = fresh_report()
    detected = {"argv": "pytest", "cwd": ".", "exit": 1, "seconds": 1.0,
                "counts": {"failed": 3}, "named": True, "tail": "E2 canary: intentional"}
    with patched(runner, shell=lambda *a, **k: detected):
        runner.canary(report)
    assert report.stages[-1]["status"] == runner.PASS
    assert report.stages[-1]["detail"]["problems"] is None


def test_the_needle_is_searched_in_the_whole_output_not_the_tail():
    """The bug this replaced: node --test prints a failure's detail long before its summary
    block, so a tail-only search reported a perfectly named canary as unnamed."""
    long_output = "E2 canary: intentional\n" + "\n".join(f"filler {i}" for i in range(60))
    with patched(runner.subprocess, run=lambda *a, **k: _Completed(1, long_output)):
        result = runner.shell(["true"], cwd=harness.REPO_ROOT, needle="E2 canary")
    assert result["named"] is True
    assert "E2 canary" not in result["tail"], "the needle really was outside the tail"


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_counts_are_parsed_from_the_runner_output_not_typed():
    assert runner.counts("41 passed in 9.45s") == {"passed": 41}
    assert runner.counts("1 failed, 39 passed in 8s")["failed"] == 1
    assert runner.counts("# pass 131\n# fail 0\n")["node_pass"] == 131
    assert runner.counts("# pass 130\n# fail 1\n")["node_fail"] == 1
    assert runner.counts("no numbers here") == {}


# ------------------------------------------------------------------ suites and teardown

def test_a_failing_suite_fails_the_run():
    report = fresh_report()
    def one(argv, **_):
        failing = argv[:2] == ["make", "api-test"]
        return {"argv": " ".join(argv), "cwd": ".", "exit": 2 if failing else 0,
                "seconds": 1.0, "counts": {"failed": 1} if failing else {"passed": 41},
                "named": None, "tail": "1 failed" if failing else ""}

    with patched(runner, shell=one):
        runner.suites(report, own_only=False)
    entry = report.stages[-1]
    assert entry["status"] == runner.FAIL
    assert report.exit_code == 1
    assert len(entry["detail"]["runs"]) == 4, "all four runners are still reported"

    green = fresh_report()
    with patched(runner, shell=lambda argv, **_: {
            "argv": " ".join(argv), "cwd": ".", "exit": 0, "seconds": 1.0,
            "counts": {"passed": 1}, "named": None, "tail": ""}):
        runner.suites(green, own_only=False)
    assert green.stages[-1]["status"] == runner.PASS


def test_teardown_is_reported_and_its_failure_is_a_failure():
    report = fresh_report()
    with patched(harness, down=lambda: ["infrx-e2-postgres"],
                 foreign_containers=lambda: [], clear_state=lambda: None):
        runner.teardown(report)
    assert report.stages[-1]["status"] == runner.PASS

    report = fresh_report()
    def wedged():
        raise harness.HarnessError("teardown left volumes behind")
    with patched(harness, down=wedged, foreign_containers=lambda: [],
                 clear_state=lambda: None):
        runner.teardown(report)
    assert report.stages[-1]["status"] == runner.FAIL
    assert report.exit_code == 1


def test_teardown_runs_even_when_a_stage_raises():
    """`main`'s `finally` is the only thing standing between a crash and a leaked stack."""
    calls = []
    report_holder = {}

    def boom(report, **_):
        report_holder["report"] = report
        raise RuntimeError("engine stage exploded")

    with patched(runner, preflight=lambda report, **k: True,
                 services=lambda report, **k: True,
                 migrate=lambda report, seed: None,
                 rls=lambda *a, **k: None,
                 engine=boom,
                 suites=lambda *a, **k: None,
                 mutation=lambda *a, **k: None,
                 canary=lambda *a, **k: None,
                 teardown=lambda report: calls.append("teardown")):
        with pytest.raises(RuntimeError, match="engine stage exploded"):
            runner.main(["--layer", "all", "--no-mutants"])
    assert calls == ["teardown"], "teardown must run even when a stage raises"


# ------------------------------------------------------------------ harness lifecycle

def test_up_and_down_refuse_while_anything_foreign_exists_and_call_no_compose():
    """r1 B1: the gate must be in front of BOTH, and proving it must not destroy a stack -
    hence a stubbed `compose`, which must never be called."""
    called = []
    stranger = [{"kind": "volume", "name": "infrx-e2_s3-data", "why": "no label"}]
    with patched(harness, compose=lambda *a, **k: called.append(a),
                 foreign_resources=lambda: stranger):
        with pytest.raises(harness.HarnessError, match="refusing to provision or tear down"):
            harness.up()
        with pytest.raises(harness.HarnessError, match="refusing to provision or tear down"):
            harness.down()
    assert called == [], "not one compose command may run while a stranger is present"


def test_down_records_ids_and_fails_if_anything_of_ours_survives():
    calls = []
    with patched(harness, foreign_resources=lambda: [],
                 compose=lambda *a, **k: calls.append(a),
                 owned=lambda kind="container": ["infrx-e2-postgres"] if kind == "container" else [],
                 _resource_id=lambda kind, name: "deadbeef"):
        with pytest.raises(harness.HarnessError, match="teardown left"):
            harness.down()
    assert ("down", "-v", "--remove-orphans") in calls, "it really tried to remove them"


def test_down_succeeds_and_returns_what_it_removed():
    state = {"owned": ["infrx-e2-postgres", "infrx-e2-valkey"]}

    def owned(kind="container"):
        return state["owned"] if kind == "container" else []

    def compose(*args, **kwargs):
        if args[:1] == ("down",):
            state["owned"] = []
        return _Completed(0)

    with patched(harness, foreign_resources=lambda: [], compose=compose, owned=owned,
                 _resource_id=lambda kind, name: "id-" + name):
        removed = harness.down()
    assert removed == ["infrx-e2-postgres", "infrx-e2-valkey"]


def test_a_bind_failure_is_retried_once_and_then_refused():
    """r1 review R-c: 55500-55599 is inside the kernel ephemeral range, so one retry - and
    then PENDING through the caller, never a pass."""
    attempts = []

    def flaky(*args, **kwargs):
        if args[:1] == ("up",):
            attempts.append("up")
            raise harness.HarnessError("Error response from daemon: ... bind: address already in use")
        return _Completed(0)

    with patched(harness, foreign_resources=lambda: [], compose=flaky,
                 owned=lambda kind="container": [], _resource_id=lambda kind, name: ""):
        with pytest.raises(harness.HarnessError, match="bind failed twice"):
            harness.up()
    assert attempts == ["up", "up"], "exactly one retry, not zero and not a loop"

    other = []

    def broken(*args, **kwargs):
        if args[:1] == ("up",):
            other.append("up")
            raise harness.HarnessError("no such image")
        return _Completed(0)

    with patched(harness, foreign_resources=lambda: [], compose=broken,
                 owned=lambda kind="container": [], _resource_id=lambda kind, name: ""):
        with pytest.raises(harness.HarnessError, match="no such image"):
            harness.up()
    assert other == ["up"], "a failure that is not a bind collision is not retried"


def test_compose_always_carries_the_checkout_label_value():
    """compose.yaml uses `${INFRX_E2_CHECKOUT:?}`, so a missing value is a refusal rather than
    an unlabelled resource - but only if every invocation exports it."""
    seen = {}
    with patched(harness, run=lambda argv, **kwargs: seen.update(kwargs) or _Completed(0)):
        harness.compose("ps")
    assert seen["env"] == {"INFRX_E2_CHECKOUT": harness.working_dir()}
    assert "INFRX_E2_CHECKOUT" in harness.COMPOSE_FILE.read_text()
    assert ":?" in harness.COMPOSE_FILE.read_text(), "compose must refuse without the value"


def test_docker_available_reports_why_not_rather_than_raising():
    with patched(harness.shutil, which=lambda name: None):
        usable, why = harness.docker_available()
    assert usable is False and "PATH" in why
    with patched(harness, run=lambda argv, **kwargs: _Completed(1, "", "Cannot connect")):
        usable, why = harness.docker_available()
    assert usable is False and "unreachable" in why


def test_busy_ports_finds_a_listener_on_a_task_local_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", harness.PORTS["fake_vllm"]))
        server.listen(1)
        busy = harness.busy_ports()
    assert busy.get("fake_vllm") == harness.PORTS["fake_vllm"]
    assert harness.PORTS["fake_vllm"] not in harness.busy_ports().values(), \
        "and it is free again once the listener is gone"


# ------------------------------------------------------------------ migration guard

def test_apply_migrations_refuses_a_database_that_is_not_fresh():
    """A migration test that silently ran against an already-migrated database proves
    nothing, so `require_fresh` is on by default (08 §10)."""
    class Conn:
        def __init__(self) -> None:
            self.executed = []

        def execute(self, sql, *args):
            self.executed.append(sql)
            return _Row(False)

        def transaction(self):
            raise AssertionError("no migration may be applied to a dirty database")

    conn = Conn()
    with pytest.raises(pgstate.MigrationError, match="expected a fresh one"):
        pgstate.apply_migrations(conn)
    assert len(conn.executed) == 1, "it asked exactly once, then refused"


class _Row:
    def __init__(self, value) -> None:
        self.value = value

    def fetchone(self):
        return (self.value,)


# ------------------------------------------------------------------ fake server defaults

def test_the_fake_server_binds_loopback_unless_explicitly_allowed():
    """Same pass: `--host` other than loopback must be refused without an explicit flag, so a
    scripted engine cannot be reachable from the network by a typo."""
    import fake_vllm
    parsed = fake_vllm.parse_args([])
    assert parsed.host == "127.0.0.1"
    served = []
    # `SERVE` is stubbed so this case can never open a socket: with the guard mutated away it
    # would otherwise bind every interface and block until the runner's timeout.
    with patched(fake_vllm, SERVE=lambda app, host, port: served.append((host, port))):
        with pytest.raises(SystemExit, match="refusing to bind"):
            fake_vllm.main(["--host", "0.0.0.0", "--port", str(harness.PORTS["fake_vllm"])])
        assert served == [], "a refused bind must not reach the server at all"
        assert fake_vllm.main(["--host", "127.0.0.1", "--port", "1"]) == 0
        assert served == [("127.0.0.1", 1)], "loopback is served without a flag"
        served.clear()
        assert fake_vllm.main(["--host", "0.0.0.0", "--port", "1",
                               "--allow-non-loopback"]) == 0
        assert served == [("0.0.0.0", 1)], "and only an explicit flag permits anything else"
    allowed = fake_vllm.parse_args(["--host", "0.0.0.0", "--allow-non-loopback"])
    assert allowed.host == "0.0.0.0" and allowed.allow_non_loopback is True


def test_sigterm_tears_down_and_orphans_no_fake_server():
    """r1 review, same pass: SIGTERM used to kill run.py with exit 143, no teardown, and a
    fake vLLM left holding 55581. Driven for real: a subprocess whose engine stage blocks,
    SIGTERM, then check the process group is gone and teardown was reported.
    """
    import json as _json
    import subprocess as _subprocess
    import time as _time
    marker = Path(os.environ.get("TMPDIR", "/tmp")) / "infrx-e2-sigterm-drill.json"
    marker.unlink(missing_ok=True)
    port = harness.PORTS["fake_vllm"] + 3
    driver = f"""
import json, pathlib, signal, sys, time
sys.path.insert(0, {str(harness.HERE)!r})
import fake_vllm, harness, run
marker = pathlib.Path({str(marker)!r})
server = fake_vllm.FakeVllmServer({port}).start()
pid = server.process.pid
run.LIVE_SERVERS.append(server)
report = run.Report()
def teardown(rep):
    rep.add("teardown", run.PASS, "drill")
    marker.write_text(json.dumps({{"teardown": True, "server_pid": pid}}))
run.teardown = teardown
run.preflight = lambda rep, **k: True
run.services = lambda rep, **k: True
run.migrate = lambda rep, seed: None
run.rls = lambda *a, **k: None
run.suites = lambda *a, **k: None
run.mutation = lambda *a, **k: None
run.canary = lambda *a, **k: None
def engine(rep):
    marker.write_text(json.dumps({{"ready": True, "server_pid": pid}}))
    time.sleep(120)
run.engine = engine
sys.exit(run.main(["--layer", "all", "--no-mutants"]))
"""
    child = _subprocess.Popen([sys.executable, "-c", driver], stdout=_subprocess.PIPE,
                              stderr=_subprocess.STDOUT, text=True)
    try:
        server_pid = None
        deadline = _time.monotonic() + 60
        while _time.monotonic() < deadline:
            if marker.exists():
                try:
                    server_pid = _json.loads(marker.read_text()).get("server_pid")
                except _json.JSONDecodeError:
                    server_pid = None
                if server_pid:
                    break
            assert child.poll() is None, f"the driver exited early: {child.communicate()[0]}"
            _time.sleep(0.2)
        assert server_pid, "the drill never reached the engine stage"

        child.terminate()                                     # SIGTERM, not SIGINT
        code = child.wait(timeout=60)
        output = child.communicate()[0] or ""
    finally:
        if child.poll() is None:
            child.kill()
    assert code == 128 + 15, f"expected 143 through the handler, got {code}\n{output[-2000:]}"
    state = _json.loads(marker.read_text())
    assert state.get("teardown") is True, f"teardown never ran: {state}"
    marker.unlink(missing_ok=True)
    # And the server it started is gone, process group included.
    with pytest.raises(ProcessLookupError):
        os.kill(server_pid, 0)


def test_the_mutation_stage_and_the_cli_count_the_verdict_the_same_way():
    """A control that must SURVIVE is a PASS. The stage used to apply its own rule and report
    both controls as survivors, failing a run whose mutation list was perfectly healthy - so
    `mutants.summarise` is now the single definition and this pins it."""
    import mutants
    healthy = [{"id": "e2c01", "status": "SURVIVED", "must_survive": True},
               {"id": "e2m01", "status": "killed", "must_survive": False}]
    summary = mutants.summarise(healthy)
    assert summary["problems"] is None and summary["controls_survived"] == 1
    assert summary["killed"] == 1 and summary["not_killed"] == 0

    for broken in ({"id": "e2m02", "status": "SURVIVED", "must_survive": False},
                   {"id": "e2c01", "status": "CONTROL-KILLED", "must_survive": True},
                   {"id": "e2m03", "status": "stale", "must_survive": False},
                   {"id": "e2m04", "status": "setup-error", "must_survive": False},
                   {"id": "e2m05", "status": "no-cases", "must_survive": False}):
        assert mutants.summarise([broken])["problems"] == [broken["id"]], broken

    pending = mutants.summarise([{"id": "e2m16", "status": "pending", "must_survive": False}])
    assert pending["problems"] is None and pending["pending"] == 1, \
        "a layer-2 mutant with no stack is pending, neither killed nor survived"

    report = fresh_report()
    with patched(mutants, run_one=lambda mutant, stack_available: {
            "id": mutant.id, "status": "SURVIVED" if mutant.must_survive else "killed",
            "must_survive": mutant.must_survive}):
        runner.mutation(report, layer="1")
    assert report.stages[-1]["status"] == runner.PASS, report.stages[-1]["detail"]


def test_provision_database_statements_are_the_ones_r_a_requires():
    """r1 review R-a, at layer 1: the three statements and their order, with docker stubbed.

    Checked here rather than against the live database because re-provisioning to prove it
    would drop the fixtures every later case needs. `OWNER postgres` is the one that bites:
    `public` is owned by `pg_database_owner`, so a copy owned by `supabase_admin` refuses the
    migration with `permission denied for schema public`.
    """
    issued = []

    def fake_run(argv, **kwargs):
        issued.append(argv)
        return _Completed(0, "CREATE DATABASE")

    with patched(harness, run=fake_run, assert_ours=lambda name: name):
        detail = harness.provision_database()
    assert len(issued) == 1, issued
    argv = issued[0]
    assert argv[:3] == ["docker", "exec", "-i"], argv[:3]
    assert argv[3] == f"{harness.PREFIX}postgres"
    assert argv[4:8] == ["psql", "-U", harness.PG_ADMIN_ROLE, "-d"], argv[4:8]
    assert harness.PG_ADMIN_ROLE == "supabase_admin", "`postgres` is not a superuser here"
    statements = [argv[index + 1] for index, token in enumerate(argv) if token == "-c"]
    assert len(statements) == 3, "each in its own -c: DROP/CREATE DATABASE cannot run in a txn"
    assert statements[0] == f"drop database if exists {harness.PG_DATABASE}"
    assert "pg_terminate_backend" in statements[1] and harness.PG_TEMPLATE_SOURCE in statements[1]
    assert statements[2] == (f"create database {harness.PG_DATABASE} "
                            f"template {harness.PG_TEMPLATE_SOURCE} owner {harness.PG_USER}")
    assert f"owner {harness.PG_USER}" in statements[2], \
        "without OWNER postgres the migration fails: permission denied for schema public"
    assert harness.PG_DATABASE.startswith("infrx_"), "D1 gates the test clock on this name"
    assert detail["owner"] == harness.PG_USER and detail["template"] == "postgres"


def test_canary_intentional_failure_is_detected_in_the_orchestration_suite():
    if os.environ.get("INFRX_E2_CANARY") == "fail":
        raise AssertionError("E2 canary: this failure is intentional (INFRX_E2_CANARY=fail)")
    assert os.environ.get("INFRX_E2_CANARY") in (None, "", "off")
