#!/usr/bin/env python3
"""E2R item 1 / audit A11: the D harness owns its container before it uses it.

`08 §8` gives this task ONE container name and ONE host port, so every checkout of this
repository wants the same two. The harness used to `docker inspect`, use whatever answered,
and `docker rm -f` it at exit - which is one checkout deleting another's database. The four
drills below are the proof that it no longer can:

* a container without this checkout's label is reported, refused, and still there afterwards;
* a second concurrent run is refused by the port lock and alters nothing;
* a run killed mid-provision leaves its container, and the next run of the same checkout
  replaces it (nothing else would ever clean it up);
* only what a run created is removed.

Nothing here touches the real `infrx-d1-postgres`: the drills drive the same code with a
decoy name and a decoy port, so they cannot collide with D's own suite, with another
checkout, or with each other. `pgharness` is loaded **by path** rather than imported as
`tests.d.pgharness` for the same reason - module state set here must not be D's.
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from infrx.contracts.tasklocal import TASK_PORTS, all_host_ports, local_services

HARNESS_PATH = Path(__file__).resolve().parent / "pgharness.py"
API_ROOT = HARNESS_PATH.parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("d_pgharness_under_test", HARNESS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pgh = _load()

_reason = pgh.unavailable()
needs_docker = pytest.mark.skipif(_reason is not None,
                                  reason=f"task-local PostgreSQL unavailable: {_reason}")

# A decoy name and port the real harness never uses. D3 request 6 / D2 round 3: they are the
# TASK's (`INFRX_D_TASK`), because every lane's sweep sharing one literal pair collided on it
# twice. D1 keeps the original pair byte-identical (E2R's namespace, a port inside E's
# 55500-55599 block that E2 does not publish); any other task gets its own namespace and a
# port 40 above its PostgreSQL port (d4: 55475), clear of every reserved port and E's block.
def decoy(task: str) -> tuple[str, int]:
    if task == "d1":
        return "infrx-e2r-dharness-postgres", 55598
    return f"infrx-{task}-dharness-postgres", local_services(task)["postgres"].host_port + 40


DECOY, DECOY_PORT = decoy(os.environ.get("INFRX_D_TASK", "d1").lower())

# Runs `pgharness.ensure()` against the decoy under a chosen checkout identity, then reports.
# `hold` keeps the lock and the container so a second run has something to collide with.
CHILD = """
import importlib.util, json, sys, time
spec = importlib.util.spec_from_file_location("pgh_child", sys.argv[1])
pgh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pgh)
pgh.CONTAINER, pgh.PORT = sys.argv[2], int(sys.argv[3])
try:
    pgh.ensure()
except BaseException as exc:                       # noqa: BLE001 - reported, then non-zero
    print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), flush=True)
    raise SystemExit(3)
print(json.dumps({"ready": True, "labels": pgh.labels(),
                  "id": pgh._docker("inspect", "-f", "{{.Id}}", pgh.CONTAINER,
                                    check=False).stdout.strip()}), flush=True)
if sys.argv[4] == "hold":
    time.sleep(120)
"""


def _child(action: str, *, checkout: str | None = None) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(API_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    if checkout is not None:
        env["INFRX_D1_CHECKOUT"] = checkout
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(HARNESS_PATH), DECOY, str(DECOY_PORT), action],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("docker", *args), capture_output=True, text=True, timeout=120)


def _container_id(name: str = DECOY) -> str:
    probe = _docker("inspect", "-f", "{{.Id}}", name)
    return probe.stdout.strip() if probe.returncode == 0 else ""


def _running(name: str = DECOY) -> str:
    probe = _docker("inspect", "-f", "{{.State.Running}}", name)
    return probe.stdout.strip() if probe.returncode == 0 else ""


def _wait_for_container(deadline_s: float = 60.0) -> str:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        found = _container_id()
        if found:
            return found
        time.sleep(0.1)
    return ""


@pytest.fixture(autouse=True)
def _no_decoy_left_behind():
    """Every drill's own cleanup: the decoy container and the decoy lock are this test's, and
    nothing else on this host is allowed to be touched."""
    yield
    _docker("rm", "-f", "-v", DECOY)
    real_port, pgh.PORT = pgh.PORT, DECOY_PORT       # the decoy's lock, by the one definition
    try:
        pgh.lock_path().unlink(missing_ok=True)
    finally:
        pgh.PORT = real_port


# ------------------------------------------------------------------ no docker needed

def test_the_creator_of_a_container_is_identified_by_a_label(monkeypatch):
    """Ownership is a label carrying this checkout's root, and the classification of every
    other case is explicit: no label, another checkout's label, no such container."""
    assert pgh.CHECKOUT_LABEL == "ai.infrx.d1.checkout"
    assert pgh.checkout() == str(HARNESS_PATH.parents[4]), \
        "the identity must be the repository root that holds this harness"
    monkeypatch.setenv("INFRX_D1_CHECKOUT", "/somewhere/else")
    assert pgh.checkout() == "/somewhere/else", "the test seam the drills below need"
    monkeypatch.delenv("INFRX_D1_CHECKOUT")

    ours = pgh.checkout()
    for described, found, why in (
            ("created by this checkout", {pgh.CHECKOUT_LABEL: ours}, None),
            ("no label at all", {}, "not created by this harness"),
            ("another checkout's", {pgh.CHECKOUT_LABEL: "/elsewhere"},
             "another checkout's run (/elsewhere)"),
            ("does not exist", None, None)):
        monkeypatch.setattr(pgh, "labels", lambda _name=None, f=found: f)
        stranger = pgh.foreign("whatever")
        if why is None:
            assert stranger is None, described
        else:
            assert stranger is not None and why in stranger["why"], (described, stranger)
            with pytest.raises(pgh.ForeignContainer, match="refusing to use it"):
                pgh.assert_ours("use it", "whatever")
    # "It does not exist" is not ownership either: nothing may act on an absent container.
    monkeypatch.setattr(pgh, "labels", lambda _name=None: None)
    with pytest.raises(pgh.ForeignContainer, match="does not exist"):
        pgh.assert_ours("use it", "whatever")
    monkeypatch.setattr(pgh, "labels", lambda _name=None: {pgh.CHECKOUT_LABEL: ours})
    assert pgh.assert_ours("use it", "whatever") == "whatever"


def test_remove_only_ever_removes_what_this_run_created(monkeypatch):
    """`_created` is the record of what this run made. A run that adopted nothing removes
    nothing, whatever is on the host - the atexit hook must not be a `docker rm` by name.

    Both directions, or "removes nothing" would also be satisfied by a `remove()` that never
    removes anything. `docker` is stubbed with an answer that says the container IS ours, so
    the only thing standing between the stub and a `rm` is `_created`.
    """
    calls = []

    class Answer:
        returncode = 0
        stdout = json.dumps({pgh.CHECKOUT_LABEL: pgh.checkout()})
        stderr = ""

    monkeypatch.setattr(pgh, "_docker", lambda *args, **kw: (calls.append(args), Answer())[1])
    monkeypatch.setattr(pgh, "_created", False)
    pgh.remove()
    assert calls == [], f"a run that created nothing must remove nothing: {calls}"

    monkeypatch.setattr(pgh, "_created", True)
    pgh.remove()
    assert ("rm", "-f", "-v", pgh.CONTAINER) in calls, \
        f"and a run that DID create it must remove exactly that: {calls}"


def test_the_decoy_is_the_tasks_own_and_d1s_is_unchanged():
    """D3 request 6: no two D tasks share a decoy, none sits on a reserved port or in E2's
    published 55500-55599 block, and D1's pair is byte-identical to the one it always had."""
    assert decoy("d1") == ("infrx-e2r-dharness-postgres", 55598)
    tasks = [task for task, ports in TASK_PORTS.items()
             if task.startswith("d") and "postgres" in ports and task != "d1"]
    assert "d4" in tasks
    pairs = {task: decoy(task) for task in tasks}
    reserved = all_host_ports()
    for task, (name, port) in pairs.items():
        assert name.startswith(f"infrx-{task}-"), (task, name)
        assert port not in reserved and not 55500 <= port <= 55599, (task, port)
    assert len({port for _, port in pairs.values()} | {55598}) == len(pairs) + 1, pairs


def test_the_port_lock_is_shared_by_both_image_variants():
    """The `-supabase` variant answers to another container name but binds the SAME port, so
    a lock keyed on the name would let the two suites race for port 55432."""
    assert pgh.lock_path().name == f"{pgh.SERVICE.container}-{pgh.PORT}.lock"
    assert pgh.SERVICE.container in pgh.CONTAINER, \
        "both variants must derive from one name, so one lock covers both"


# ------------------------------------------------------------------ real docker drills

@needs_docker
def test_a_container_this_run_did_not_create_is_refused_and_survives():
    """Finding A11 exactly: something else owns the name. It must be reported with the owner
    it claims, must make `ensure()` refuse, and must still be there - not started, not
    stopped, not removed - afterwards."""
    created = _docker("create", "--name", DECOY,
                      "--label", f"{pgh.CHECKOUT_LABEL}=/another/checkout",
                      pgh.PLAIN_IMAGE, "true")
    assert created.returncode == 0, created.stderr
    before = _container_id()
    assert before

    child = _child("provision")
    stdout, stderr = child.communicate(timeout=120)
    assert child.returncode == 3, (child.returncode, stdout, stderr)
    reported = json.loads(stdout.strip().splitlines()[-1])
    assert reported["error"] == "ForeignContainer", reported
    assert "another checkout's run (/another/checkout)" in reported["message"], reported

    assert _container_id() == before, "the foreign container was replaced"
    assert _running() == "false", "it was started, which is also touching it"
    assert _docker("logs", DECOY).returncode == 0, "it must still exist"


@needs_docker
def test_a_second_concurrent_run_is_refused_and_alters_nothing():
    """Two checkouts, one port. The second run is refused by the lock BEFORE it inspects
    anything, and the first run's container is untouched and still serving."""
    first = _child("hold")
    try:
        ready = json.loads(first.stdout.readline())
        assert ready.get("ready") is True, f"the first run never provisioned: {ready}"
        assert ready["labels"][pgh.CHECKOUT_LABEL] == pgh.checkout(), ready
        running_id = _container_id()
        assert running_id and _running() == "true"

        second = _child("provision", checkout="/another/checkout")
        stdout, stderr = second.communicate(timeout=120)
        assert second.returncode == 3, (second.returncode, stdout, stderr)
        refused = json.loads(stdout.strip().splitlines()[-1])
        assert refused["error"] == "HarnessBusy", refused
        assert f"pid {first.pid}" in refused["message"], \
            f"the refusal must name the run that holds the lock: {refused}"
        assert "Nothing was altered" in refused["message"], refused

        assert _container_id() == running_id, "the first run's container was replaced"
        assert _running() == "true", "the first run's container was stopped"
    finally:
        first.kill()
        first.wait(timeout=30)


@needs_docker
def test_a_run_killed_mid_provision_is_cleaned_up_by_the_next_one():
    """SIGKILL during `ensure()`: no atexit, so the container is left behind and the lock is
    released by the kernel. The next run of the SAME checkout must replace it - adopting a
    database whose state nobody knows is how a crashed run poisons the next one, and nothing
    else on this host would ever remove it."""
    crashing = _child("hold")
    leftover = _wait_for_container()
    assert leftover, "the child never got as far as creating the container"
    crashing.send_signal(signal.SIGKILL)
    crashing.wait(timeout=30)
    assert _container_id() == leftover, "a SIGKILLed run leaves its container behind"
    assert pgh.labels(DECOY)[pgh.CHECKOUT_LABEL] == pgh.checkout(), \
        "and the leftover carries this checkout's label, which is what makes it ours to clear"

    survivor = _child("provision")
    stdout, stderr = survivor.communicate(timeout=180)
    assert survivor.returncode == 0, (survivor.returncode, stdout, stderr)
    replaced = json.loads(stdout.strip().splitlines()[-1])
    assert replaced.get("ready") is True, stdout
    assert replaced["id"] and replaced["id"] != leftover, \
        f"the leftover was adopted rather than replaced: {replaced['id']} vs {leftover}"
    assert _container_id() == "", \
        "and the replacement is removed at exit, leaving nothing behind"
