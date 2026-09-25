"""E2C: the preflight and the gate wrappers never turn a missing prerequisite, an invalid
profile, a skip or a broken seam into a pass.

    apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_preflight.py
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gates                                             # noqa: E402
import preflight as pf                                   # noqa: E402

PY = sys.executable
ENV = pf.load()
GOOD_PROFILE = {"identity": {"run_id": "e2c-test"}, "target_ownership": {"hosts": ["h"]},
                "bounds": {"max_requests": 1}, "workload": {"manifest_sha256": "ab" * 32},
                "measurement": {"arrival": "open"}, "cleanup": {"policy": "drain"}}


def without_docker(tmp_path: Path) -> dict:
    """A PATH holding every tool the consumer-local profile names except docker."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ENV["profiles"]["consumer-local"]["tools"]:
        exe = ENV["tools"][name]["argv"][0]
        found = shutil.which(exe)
        if "/" not in exe and name != "docker" and found:
            (bin_ / exe).symlink_to(found)
    return {**os.environ, "PATH": str(bin_)}


def test_the_environment_manifest_is_self_consistent():
    """Every profile names only tools, images and namespaces the manifest defines, and no
    two namespaces share a port - a typo would otherwise be a KeyError at preflight time."""
    for name, profile in ENV["profiles"].items():
        assert set(profile["tools"]) <= set(ENV["tools"]), name
        assert set(profile["images"]) <= set(ENV["images"]), name
        assert set(profile["namespaces"]) <= set(ENV["namespaces"]), name
    ports = [p for spec in ENV["namespaces"].values() for p in pf.namespace(spec)[0]]
    assert len(ports) == len(set(ports))
    from infrx.contracts.tasklocal import local_services     # the one registry, not a copy
    own = local_services("e2c")
    assert pf.namespace(ENV["namespaces"]["e2c"]) == \
        ([own["postgres"].host_port, own["valkey"].host_port, own["s3"].host_port],
         ["infrx-e2c-postgres", "infrx-e2c-valkey", "infrx-e2c-s3"])
    assert all("@sha256:" in image["ref"] for image in ENV["images"].values())
    # layer 3 (integration-l3, backend-certify) also binds the E3B PostgREST pair
    sys.path.insert(0, str(HERE / "backend"))
    import stack
    assert {stack.POSTGREST_PORT, stack.JOURNEY_POSTGREST_PORT} < \
        set(pf.namespace(ENV["namespaces"]["e2"])[0])


def test_missing_docker_is_blocked_with_exit_3(tmp_path):
    """The acceptance case: no docker means no database, so BLOCKED (3), never PASS, and
    every image is reported missing rather than silently assumed."""
    done = subprocess.run([PY, str(HERE / "preflight.py"), "consumer-local"],
                          env=without_docker(tmp_path), capture_output=True, text=True)
    result = json.loads(done.stdout)
    assert (done.returncode, result["verdict"]) == (3, "BLOCKED"), done.stdout
    rows = {c["check"]: c for c in result["checks"]}
    assert rows["tool:docker"]["status"] == "missing"
    assert all(rows[f"image:{name}"]["status"] == "missing"
               for name in ENV["profiles"]["consumer-local"]["images"])


def test_the_gate_stops_at_a_blocked_preflight(tmp_path):
    """consumer-local.sh without docker: exit 3, a BLOCKED verdict file, and no suite ran."""
    out = tmp_path / "out"
    done = subprocess.run(["bash", str(HERE / "consumer-local.sh"), "--out", str(out)],
                          env=without_docker(tmp_path), capture_output=True, text=True)
    verdict = json.loads((out / "verdict.json").read_text())
    assert (done.returncode, verdict["verdict"], verdict["exit"]) == (3, "BLOCKED", 3)
    assert [s["stage"] for s in verdict["stages"]] == ["preflight:consumer-local"]


def test_a_missing_tool_or_image_blocks_and_a_complete_host_passes():
    """The checker can pass at all (else an always-BLOCKED preflight passes every case
    above), and one missing tool or image flips it."""
    env = json.loads(json.dumps(ENV))
    env["profiles"]["probe"] = {"tools": ["python"], "images": [], "namespaces": []}
    assert pf.preflight("probe", env=env)["verdict"] == "PASS"
    env["tools"]["absent"] = {"argv": ["infrx-e2c-no-such-tool"], "match": "."}
    env["profiles"]["probe"]["tools"].append("absent")
    assert pf.preflight("probe", env=env)["not_ok"] == ["tool:absent"]
    env["profiles"]["probe"] = {"tools": ["python"], "images": ["valkey"], "namespaces": []}
    result = pf.preflight("probe", env=env)            # no docker tool checked: image unknown
    assert (result["verdict"], result["not_ok"]) == ("BLOCKED", ["image:valkey"])


def test_a_busy_namespace_port_is_blocked():
    """A port another run holds is BLOCKED before anything binds it (not a later FAIL)."""
    block = ENV["namespaces"]["e2c-selftest"]["ports"]
    port = next((p for p in block if pf.port_free(p)), None)   # a source port may sit on one
    assert port is not None, f"every port of {block} is taken"
    with socket.socket() as held:
        held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        held.bind(("127.0.0.1", port))
        held.listen()
        result = pf.preflight("self-test")
    assert result["verdict"] == "BLOCKED"
    assert [c.get("busy_ports") for c in result["checks"]
            if c["check"] == "namespace:e2c-selftest"] == [[port]]


def test_a_port_lingering_in_time_wait_is_not_busy():
    """Measured: a run killed with its containers left 55448/55474 in TIME_WAIT and the next
    preflight called them busy (BLOCKED), though a server binds them fine."""
    block = ENV["namespaces"]["e2c-selftest"]["ports"]
    port = next(p for p in reversed(block) if pf.port_free(p))
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen()
        with socket.create_connection(("127.0.0.1", port)):
            accepted, _ = server.accept()
            accepted.close()                        # the server side closes first: TIME_WAIT
    assert pf.port_free(port)


def test_an_ephemeral_port_overlap_is_a_risk_not_a_block(tmp_path):
    """Measured on this host (E2C evidence): a D container failed to bind 55432 because an
    outgoing connection held it as its source port. The check names every exposed port,
    honours ip_local_reserved_ports, and never flips the verdict (it cannot cause a pass)."""
    (tmp_path / "ip_local_port_range").write_text("32768\t60999\n")
    (tmp_path / "ip_local_reserved_ports").write_text("55400-55449,55474\n")
    row = pf.ephemeral_overlap([1234, 55448, 55474, 55500, 56932], tmp_path)
    assert (row["status"], row["ports"]) == ("risk", [55500, 56932])
    (tmp_path / "ip_local_reserved_ports").write_text("55400-57000\n")
    assert pf.ephemeral_overlap([55448, 56932], tmp_path)["status"] == "ok"
    result = pf.preflight("self-test")
    assert "not_ok" in result and "ephemeral-overlap" not in result["not_ok"]


@pytest.mark.parametrize("profile,problem", [
    ({k: v for k, v in GOOD_PROFILE.items() if k != "bounds"}, "['bounds']"),
    ({**GOOD_PROFILE, "cleanup": {}}, "['cleanup']"),
    ({**GOOD_PROFILE, "target_ownership": {"hosts": ["h"], "api_key": "sk-live-x"}},
     "target_ownership.api_key"),
    ("not an object", "not a JSON object"),
], ids=["group-missing", "group-empty", "literal-secret", "not-an-object"])
def test_an_invalid_certify_profile_exits_4(tmp_path, profile, problem):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    done = subprocess.run([PY, str(HERE / "preflight.py"), "self-test", "--certify-profile",
                           str(path)], capture_output=True, text=True)
    result = json.loads(done.stdout)
    assert (done.returncode, result["verdict"]) == (4, "INVALID")
    assert problem in next(c["detail"] for c in result["checks"]
                           if c["check"] == "certify-profile")


def test_a_valid_certify_profile_is_accepted(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(GOOD_PROFILE))
    check = pf.validate_certify_profile(path, ENV["certify_profile_required"])
    assert check["status"] == "ok", check


def test_a_live_certify_never_starts_from_the_wrapper(tmp_path):
    """--box/--target without a profile is INVALID (4); with a valid one it is NOT RUN and
    certify.py is never invoked (no certify log exists)."""
    out = tmp_path / "a"
    done = subprocess.run(["bash", str(HERE / "backend-certify.sh"), "--out", str(out), "--",
                           "--box", "--target", "http://127.0.0.1:1/v1"],
                          capture_output=True, text=True)
    assert done.returncode == 4 and json.loads((out / "verdict.json").read_text())["verdict"] \
        == "INVALID"
    (tmp_path / "p.json").write_text(json.dumps(GOOD_PROFILE))
    out = tmp_path / "b"
    done = subprocess.run(["bash", str(HERE / "backend-certify.sh"), "--out", str(out),
                           "--certify-profile", str(tmp_path / "p.json"), "--", "--box",
                           "--target", "http://127.0.0.1:1/v1"], capture_output=True, text=True)
    stages = json.loads((out / "verdict.json").read_text())["stages"]
    assert done.returncode == 3 and stages[-1]["verdict"] == "NOT RUN", stages
    assert not (out / "certify.log").exists()


URL, ENGINE = "http://127.0.0.1:1/v1", "http://127.0.0.1:2"


@pytest.mark.parametrize("spelling", [
    ["--target", URL, "--engine-url", ENGINE],                 # --target without --box
    [f"--target={URL}", f"--engine-url={ENGINE}"],             # argparse's = form
    ["--tar", URL, "--engine-url", ENGINE],                    # an abbreviation (allow_abbrev)
    ["--b", f"--t={URL}", "--engine-url", ENGINE],
], ids=["target-alone", "target-equals", "target-abbreviated", "box-and-target-abbreviated"])
def test_a_remote_certify_flag_in_any_spelling_never_starts_a_run(tmp_path, monkeypatch,
                                                                   spelling):
    """certify.py's parser accepts `--target=URL` and unambiguous prefixes; each is a remote
    run, so it needs a profile (INVALID without one) and is NOT RUN with one. The oracle is
    a stub for the process launcher that fails the test if anything is started."""
    def started(name, argv, *rest, **kw):
        raise AssertionError(f"LIVE RUN STARTED: {argv}")
    monkeypatch.setattr(gates, "run", started)
    monkeypatch.setattr(gates, "preflight_stage", lambda profile, out, certify=None: {
        "stage": f"preflight:{profile}", "verdict": "PASS", "exit": 0})
    out = tmp_path / "a"
    assert gates.main(["backend-certify", "--out", str(out), "--", *spelling]) == 4
    assert [s["stage"] for s in json.loads((out / "verdict.json").read_text())["stages"]] \
        == ["certify-profile"]
    (tmp_path / "p.json").write_text(json.dumps(GOOD_PROFILE))
    out = tmp_path / "b"
    assert gates.main(["backend-certify", "--out", str(out), "--certify-profile",
                       str(tmp_path / "p.json"), "--", *spelling]) == 3
    assert json.loads((out / "verdict.json").read_text())["stages"][-1]["verdict"] == "NOT RUN"


def test_a_local_certify_run_is_started(tmp_path, monkeypatch):
    """The other half: flags that are not a remote target do reach certify.py (else a
    detector calling everything remote would pass the case above)."""
    seen = []
    def started(name, argv, cwd, out, env=None):
        seen.append(argv)
        log = out / f"{name}.log"
        log.write_text("")
        return 0, 0.0, log
    monkeypatch.setattr(gates, "run", started)
    monkeypatch.setattr(gates, "preflight_stage", lambda profile, out, certify=None: {
        "stage": f"preflight:{profile}", "verdict": "PASS", "exit": 0})
    gates.main(["backend-certify", "--out", str(tmp_path), "--", "--no-stack", "--scale",
                "tiny", "--engine-url", ENGINE])
    assert [argv[1] for argv in seen] == ["tests/integration/backend/certify.py"]


EMPTY = "import pytest\n@pytest.mark.parametrize('mutant', ())\ndef test_m(mutant):\n    pass\n"


def junit(tmp_path: Path, cases: str) -> Path:
    path = tmp_path / "j.xml"
    path.write_text(f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>')
    return path


def test_junit_counts_keep_xfails_apart_from_skips(tmp_path):
    counts = gates.junit_counts(junit(tmp_path, (
        '<testcase classname="a" name="ok"/>'
        '<testcase classname="a" name="s"><skipped type="pytest.skip" message="no docker"/></testcase>'
        '<testcase classname="a" name="x"><skipped type="pytest.xfail" message="F2 case"/></testcase>'
        '<testcase classname="a" name="f"><failure message="boom"/></testcase>'
        '<testcase classname="a" name="e"><error message="setup"/></testcase>')))
    assert {k: counts[k] for k in ("tests", "passed", "skipped", "xfailed", "failed", "errors")} \
        == {"tests": 5, "passed": 1, "skipped": 1, "xfailed": 1, "failed": 1, "errors": 1}
    assert counts["skip_reasons"] == {"no docker": 1} and counts["failed_ids"] == ["a::f", "a::e"]


@pytest.mark.parametrize("body,verdict", [
    ("def test_ok(tmp_path):\n    (tmp_path / 'f').write_text('x')\n", "PASS"),  # basetemp works
    ("import pytest\ndef test_ok():\n    pass\ndef test_s():\n    pytest.skip('no db')\n",
     "BLOCKED"),
    ("def test_bad():\n    assert False\n", "FAIL"),
    ("import pytest\n@pytest.mark.xfail(strict=True, reason='known gap')\n"
     "def test_q():\n    assert False\n", "BLOCKED"),
    # the missing-service path inside a stage: the fixture that connects raises
    ("import pytest\n@pytest.fixture\ndef db():\n"
     "    raise ConnectionRefusedError('postgres 127.0.0.1:55448 refused')\n"
     "def test_q(db):\n    pass\n", "FAIL"),
    # a list whose default subset is empty (INFRX_MUTANTS unset) has no case to run
    (EMPTY + "def test_ok():\n    pass\n", "PASS"),
    (EMPTY + "import pytest\ndef test_s():\n    pytest.skip('no db')\n", "BLOCKED"),
    (EMPTY, "BLOCKED"),                     # nothing ran at all
], ids=["pass", "skip-is-blocked", "fail", "quarantine-is-blocked", "setup-error-is-fail",
        "empty-parametrization-is-not-a-case", "empty-parametrization-hides-no-skip",
        "empty-parametrization-alone-is-blocked"])
def test_a_pytest_stage_that_skipped_is_not_a_pass(tmp_path, body, verdict):
    (tmp_path / "test_probe.py").write_text(body)
    out = tmp_path / "out"
    out.mkdir()
    stage = gates.pytest_stage("probe", [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                         "test_probe.py"], tmp_path, out)
    assert stage["verdict"] == verdict, stage
    if body.startswith(EMPTY):              # listed, not hidden
        assert stage["counts"]["empty_params"] == ["test_probe::test_m[NOTSET]"], stage


def test_a_pytest_stage_that_crashed_without_a_report_fails(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    stage = gates.pytest_stage("probe", [PY, "-c", "import sys; sys.exit(2)"], tmp_path, out)
    assert (stage["verdict"], stage["exit"]) == ("FAIL", 2), stage


FAKE_RUN = """import json, sys
report = sys.argv[sys.argv.index("--report") + 1]
if {write}:
    runs = [{{"argv": "pytest tests/integration", "counts": {{"passed": 5, "skipped": {skipped}}}}}]
    open(report, "w").write(json.dumps({{"stages": [{{"stage": "suites", "status": "PASS",
                                                      "runs": runs}}]}}))
sys.exit({code})
"""


@pytest.mark.parametrize("code,skipped,write,verdict", [
    (0, 0, True, "PASS"), (0, 103, True, "BLOCKED"), (3, 0, True, "BLOCKED"),
    (1, 0, True, "FAIL"), (0, 0, False, "FAIL"),
], ids=["clean", "skips-are-blocked", "pending", "fail", "no-report"])
def test_a_runner_stage_passes_only_when_every_case_ran(tmp_path, code, skipped, write, verdict):
    """run.py exits 0 with 103 skipped tests/integration cases at layer 1; a runner's exit
    code alone is not evidence that its cases ran."""
    script = tmp_path / "fake_run.py"
    script.write_text(FAKE_RUN.format(write=write, skipped=skipped, code=code))
    report = tmp_path / "r.json"
    stage = gates.runner_stage("probe", [PY, str(script), "--report", str(report)], tmp_path,
                               report)
    assert stage["verdict"] == verdict, stage


FAKE_RUNNER = """import json, pathlib, sys
out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]); out.mkdir(parents=True)
verdict, code = {verdict!r}, {code}
if verdict is not None:
    (out / "verdict.json").write_text(json.dumps({{"verdict": verdict}}))
sys.exit(code)
"""


@pytest.mark.parametrize("verdict,code,expected", [
    ("PASS", 0, "PASS"), ("FAIL", 1, "FAIL"), ("BLOCKED", 3, "BLOCKED"),
    ("PASS", 1, "INVALID"),                 # the runner contradicts itself
    (None, 0, "FAIL"),                      # exit 0 but no verdict file is not a pass
    ("GREEN", 0, "INVALID"),
], ids=["pass", "fail", "blocked", "contradiction", "no-verdict", "unknown"])
def test_the_e3c_stage_takes_the_runners_verdict_only_when_it_is_consistent(
        tmp_path, verdict, code, expected):
    runner = tmp_path / "runner.py"
    runner.write_text(FAKE_RUNNER.format(verdict=verdict, code=code))
    out = tmp_path / "out"
    out.mkdir()
    stage = gates.e3c_stage(out, runner)
    assert stage["verdict"] == expected, stage
    assert stage["command"].endswith(f"--out {out / 'e3c'}")


def test_an_absent_e3c_runner_is_not_run(tmp_path):
    assert gates.e3c_stage(tmp_path, tmp_path / "missing.py")["verdict"] == "NOT RUN"


def test_the_worst_stage_decides_the_gate():
    assert gates.worst(["PASS", "NOT RUN"]) == "NOT RUN"
    assert gates.worst(["NOT RUN", "BLOCKED", "PASS"]) == "BLOCKED"
    assert gates.worst(["BLOCKED", "INVALID"]) == "INVALID"
    assert gates.worst(["INVALID", "FAIL", "PASS"]) == "FAIL"
    assert gates.worst([]) == "PASS" and gates.EXIT["NOT RUN"] == 3


@pytest.mark.parametrize("seam", sorted(gates.SEAMS))
def test_a_deliberately_broken_seam_fails_the_gate(tmp_path, seam):
    """The acceptance case: with the readiness or expiry control removed (an existing
    single-edit mutant, pristine baseline first), the gate is FAIL because the suite killed
    it - not PASS (undetected) and not INVALID (a runner that proved nothing)."""
    out = tmp_path / "out"
    done = subprocess.run(["bash", str(HERE / "consumer-local.sh"), "--out", str(out),
                           "--break-seam", seam], capture_output=True, text=True)
    verdict = json.loads((out / "verdict.json").read_text())
    stage = verdict["stages"][-1]
    assert (done.returncode, verdict["verdict"]) == (1, "FAIL"), stage
    assert (stage["stage"], stage["outcome"]) == (f"seam:{seam}", "killed"), stage


STUB_DOCKER = """#!/bin/sh
echo "$*" >> "{log}"
case "$1 $2" in
  "image inspect") exit 1 ;;
  "version --format") echo 29.0.0; exit 0 ;;
  "run -d") exit {run} ;;
esac
exit 0
"""


def stub_docker(tmp_path: Path, monkeypatch, run: int = 0) -> Path:
    """A `docker` first on PATH that logs every argv; `image inspect` finds nothing and
    everything else (a pull included) succeeds, so a pull would be seen, not masked."""
    bin_, log = tmp_path / "stub-bin", tmp_path / "docker.log"
    bin_.mkdir()
    (bin_ / "docker").write_text(STUB_DOCKER.format(log=log, run=run))
    (bin_ / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    log.touch()
    return log


def test_an_image_that_is_not_local_is_blocked_and_never_pulled(tmp_path, monkeypatch):
    """E3C WR-2: a pin that is not on the host (the former quay MinIO answered 401) stays
    BLOCKED with its pull line, and preflight never runs `docker pull` - with a docker that
    WOULD pull successfully, so a pulling preflight fails here."""
    log = stub_docker(tmp_path, monkeypatch)
    env = json.loads(json.dumps(ENV))
    absent = "infrx-e2c-selftest/absent@sha256:" + "0" * 64
    env["images"]["absent"] = {"ref": absent}
    env["profiles"]["probe"] = {"tools": ["python", "docker"], "images": ["absent"],
                                "namespaces": []}
    result = pf.preflight("probe", env=env)
    rows = {c["check"]: c for c in result["checks"]}
    assert result["verdict"] == "BLOCKED"
    assert rows["image:absent"] == {"check": "image:absent", "want": absent,
                                    "status": "missing", "detail": f"docker pull {absent}"}
    calls = log.read_text().splitlines()
    assert f"image inspect {absent}" in calls
    assert not [call for call in calls if "pull" in call.split()], calls


def test_a_tool_at_the_wrong_version_is_blocked():
    """The pin is the version, not the name: a Python that is not 3.12 is a mismatch."""
    env = json.loads(json.dumps(ENV))
    env["tools"]["python"]["match"] = r"^Python 2\."
    env["profiles"]["probe"] = {"tools": ["python"], "images": [], "namespaces": []}
    result = pf.preflight("probe", env=env)
    row = next(c for c in result["checks"] if c["check"] == "tool:python")
    assert (result["verdict"], result["not_ok"], row["status"]) == \
        ("BLOCKED", ["tool:python"], "mismatch")
    assert row["found"].startswith("Python 3.12"), row


def test_a_host_that_is_not_linux_is_blocked(monkeypatch):
    env = json.loads(json.dumps(ENV))
    env["profiles"]["probe"] = {"tools": ["python"], "images": [], "namespaces": []}
    assert pf.preflight("probe", env=env)["verdict"] == "PASS"
    monkeypatch.setattr(sys, "platform", "darwin")
    result = pf.preflight("probe", env=env)
    assert (result["verdict"], result["not_ok"]) == ("BLOCKED", ["platform"])


def test_a_container_left_in_the_namespace_is_blocked(monkeypatch):
    """A crashed run's container holds the namespace even when its port is free - for a
    profile that needs no docker tool too (self-test)."""
    monkeypatch.setattr(pf, "containers", lambda: ["infrx-e2c-selftest-left", "unrelated"])
    result = pf.preflight("self-test")
    row = next(c for c in result["checks"] if c["check"] == "namespace:e2c-selftest")
    assert (result["verdict"], row["status"], row["existing_containers"]) == \
        ("BLOCKED", "busy", ["infrx-e2c-selftest-left"])


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                   # noqa: N802
        self.send_response(200 if self.path == "/minio/health/live" else 404)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_the_s3_endpoint_starts_from_the_local_pin_and_is_removed(tmp_path, monkeypatch):
    """The one service the gate starts: the manifest's MinIO pin with `--pull never` (an
    absent image fails the start, it is never fetched), the credential literals passed by
    name only, healthy before any suite uses it, and removed by name afterwards."""
    log = stub_docker(tmp_path, monkeypatch)
    block = ENV["namespaces"]["e2c-selftest"]["ports"]
    port = next(p for p in block[3:] if pf.port_free(p))
    server = http.server.HTTPServer(("127.0.0.1", port), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        row = gates.s3_up(port, "infrx-e2c-selftest-s3", tmp_path, wait_s=10)
    finally:
        server.shutdown()
        server.server_close()
    assert (row["verdict"], row["started"]) == ("PASS", True), row
    started = log.read_text().splitlines()[0].split()
    assert started[:4] == ["run", "-d", "--pull", "never"]
    assert ENV["images"]["minio"]["ref"] in started and f"127.0.0.1:{port}:9000" in started
    assert "infrx-e2-local-secret" not in row["command"]
    gates.s3_down("infrx-e2c-selftest-s3", tmp_path)
    assert log.read_text().splitlines()[-1] == "rm -f -v infrx-e2c-selftest-s3"
    assert gates.s3_up(port, "infrx-e2c-selftest-s3", tmp_path, wait_s=1)["verdict"] \
        == "BLOCKED"                                    # nothing healthy answers


def test_an_s3_endpoint_that_cannot_start_is_blocked(tmp_path, monkeypatch):
    stub_docker(tmp_path, monkeypatch, run=125)         # e.g. the pin is not local
    row = gates.s3_up(55519, "infrx-e2c-selftest-s3", tmp_path, wait_s=1)
    assert (row["verdict"], row["started"]) == ("BLOCKED", False), row


API_SUITES = sorted({p.relative_to(gates.API / "tests").parts[0]
                     for p in (gates.API / "tests").rglob("test_*.py")})


class Composed:
    """consumer_local with every stage replaced by a recorder: which stages, in which
    order, with which environment - the composition itself, not the stages."""

    def __init__(self, monkeypatch, tmp_path, *, l3_preflight="PASS", s3="PASS", raise_in=None,
                 d_runner="RUNNER = Runner(env=('INFRX_D_TASK', 'INFRX_D2_VALKEY_PORT'))\n"):
        self.calls: list[tuple] = []
        # the D mutant list's Runner as the wiring would leave it (default) or as it is today
        (tmp_path / "signup_mutants.py").write_text(d_runner)
        monkeypatch.setattr(gates, "D_RUNNER", str(tmp_path / "signup_mutants.py"))

        def preflight_stage(profile, out, certify_profile=None):
            self.calls.append(("preflight", profile))
            verdict = l3_preflight if profile == "integration-l3" else "PASS"
            return {"stage": f"preflight:{profile}", "verdict": verdict, "exit": 0}

        def pytest_stage(name, argv, cwd, out, env=None):
            self.calls.append(("pytest", name, argv, env))
            if name == raise_in:
                raise KeyboardInterrupt(name)
            return {"stage": name, "verdict": "PASS", "env": env}

        def runner_stage(name, argv, out, report=None):
            self.calls.append(("runner", name, argv))
            return {"stage": name, "verdict": "PASS"}

        def e3c_stage(out, runner=gates.E3C_RUNNER):
            self.calls.append(("e3c",))
            return {"stage": "backend-local", "verdict": "PASS"}

        def s3_up(port, name, out, wait_s=60.0):
            self.calls.append(("s3-up", port, name))
            return {"stage": "s3", "verdict": s3, "started": s3 == "PASS"}

        def s3_down(name, out):
            self.calls.append(("s3-down", name))

        for name, fake in (("preflight_stage", preflight_stage), ("pytest_stage", pytest_stage),
                           ("runner_stage", runner_stage), ("e3c_stage", e3c_stage),
                           ("s3_up", s3_up), ("s3_down", s3_down)):
            monkeypatch.setattr(gates, name, fake)

    def gate(self, tmp_path):
        out = tmp_path / "out"
        code = gates.main(["consumer-local", "--out", str(out)])
        return code, json.loads((out / "verdict.json").read_text())


def test_a_green_consumer_local_composition_passes_on_its_own_namespace(tmp_path, monkeypatch):
    from infrx.contracts.tasklocal import local_services
    own = local_services("e2c")
    composed = Composed(monkeypatch, tmp_path)
    code, verdict = composed.gate(tmp_path)
    assert (code, verdict["verdict"]) == (0, "PASS")
    names = [s["stage"] for s in verdict["stages"]]
    assert names == ["preflight:consumer-local", "s3", *[f"api-{s}" for s in API_SUITES],
                     "bench-test", "integration-l3", "backend-local"], names
    assert ("s3-up", own["s3"].host_port, own["s3"].container) in composed.calls
    api = [c for c in composed.calls if c[0] == "pytest" and c[1].startswith("api-")]
    for _, name, argv, env in api:
        assert env == {"INFRX_D_TASK": "e2c", "INFRX_D2_VALKEY_PORT": str(own["valkey"].host_port),
                       "INFRX_D2_VALKEY_CONTAINER": own["valkey"].container,
                       "INFRX_Q_VALKEY_PORT": str(own["valkey"].host_port),
                       "INFRX_M_S3_ENDPOINT": f"http://127.0.0.1:{own['s3'].host_port}",
                       "INFRX_M_S3_LOCAL_CREDS": "1"}, name
    down = composed.calls.index(("s3-down", own["s3"].container))
    assert down > composed.calls.index(api[-1])          # after the last suite that uses it
    runners = [c[2] for c in composed.calls if c[0] == "runner"]
    assert len(runners) == 1 and runners[0][2:5] == ["--layer", "3", "--only-suites"], runners
    assert all("--only-suites" in argv for argv in runners)   # never run.py's `make api-test`
    assert next(c[2] for c in api if c[1] == "api-d")[-1] == "tests/d"   # every case


def test_the_d_mutant_case_is_not_run_until_its_copies_inherit_the_task(tmp_path, monkeypatch):
    """tests/d/signup_mutants.py's Runner declares no env today, so its copies would run on
    d1's 55432 (another lane's port) whatever the gate exports: the case is deselected and
    reported NOT RUN, and the gate cannot PASS - not silently run on the shared default."""
    composed = Composed(monkeypatch, tmp_path, d_runner="RUNNER = shared.Runner(name='a1')\n")
    code, verdict = composed.gate(tmp_path)
    assert (code, verdict["verdict"]) == (3, "NOT RUN")
    names = [s["stage"] for s in verdict["stages"]]
    assert names[names.index("api-d") + 1] == "api-d-mutants"
    row = next(s for s in verdict["stages"] if s["stage"] == "api-d-mutants")
    assert (row["verdict"], row["case"]) == ("NOT RUN", gates.D_MUTANT_CASE)
    argv = next(c[2] for c in composed.calls if c[0] == "pytest" and c[1] == "api-d")
    assert argv[-3:] == ["tests/d", "--deselect", gates.D_MUTANT_CASE]


def test_a_consumer_local_without_the_stack_is_blocked_and_still_runs_layer_1(tmp_path,
                                                                            monkeypatch):
    composed = Composed(monkeypatch, tmp_path, l3_preflight="BLOCKED")
    code, verdict = composed.gate(tmp_path)
    assert (code, verdict["verdict"]) == (3, "BLOCKED")
    rows = {s["stage"]: s for s in verdict["stages"]}
    assert rows["integration-l3"]["verdict"] == "BLOCKED"
    runners = [c[2] for c in composed.calls if c[0] == "runner"]
    assert [argv[2:5] for argv in runners] == [["--layer", "1", "--only-suites"]]
    assert [s["stage"] for s in verdict["stages"]][-1] == "backend-local"


def test_a_consumer_local_without_its_s3_endpoint_is_blocked_and_sets_none(tmp_path,
                                                                          monkeypatch):
    composed = Composed(monkeypatch, tmp_path, s3="BLOCKED")
    code, verdict = composed.gate(tmp_path)
    assert (code, verdict["verdict"]) == (3, "BLOCKED")
    api = [c[3] for c in composed.calls if c[0] == "pytest" and c[1].startswith("api-")]
    assert api and not any("INFRX_M_S3_ENDPOINT" in env for env in api)
    assert not [c for c in composed.calls if c[0] == "s3-down"]     # not ours: not removed


def test_the_s3_endpoint_is_removed_when_a_suite_is_interrupted(tmp_path, monkeypatch):
    composed = Composed(monkeypatch, tmp_path, raise_in="api-m")
    with pytest.raises(KeyboardInterrupt):
        composed.gate(tmp_path)
    assert composed.calls[-1][0] == "s3-down"
