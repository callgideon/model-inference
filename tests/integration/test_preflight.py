"""E2C: the preflight and the gate wrappers never turn a missing prerequisite, an invalid
profile, a skip or a broken seam into a pass.

    apps/infrx-api/.venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/test_preflight.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
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
    assert pf.namespace(ENV["namespaces"]["e2c"]) == \
        ([55448, 55474], ["infrx-e2c-postgres", "infrx-e2c-valkey"])   # tasklocal's e2c
    assert all("@sha256:" in image["ref"] for image in ENV["images"].values())


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
    with socket.socket() as held:
        held.bind(("127.0.0.1", 55510))
        held.listen()
        result = pf.preflight("self-test")
    assert result["verdict"] == "BLOCKED"
    assert [c.get("busy_ports") for c in result["checks"]
            if c["check"] == "namespace:e2c-selftest"] == [[55510]]


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
    ("def test_ok():\n    pass\n", "PASS"),
    ("import pytest\ndef test_ok():\n    pass\ndef test_s():\n    pytest.skip('no db')\n",
     "BLOCKED"),
    ("def test_bad():\n    assert False\n", "FAIL"),
], ids=["pass", "skip-is-blocked", "fail"])
def test_a_pytest_stage_that_skipped_is_not_a_pass(tmp_path, body, verdict):
    (tmp_path / "test_probe.py").write_text(body)
    out = tmp_path / "out"
    out.mkdir()
    stage = gates.pytest_stage("probe", [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                         "test_probe.py"], tmp_path, out)
    assert stage["verdict"] == verdict, stage


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
