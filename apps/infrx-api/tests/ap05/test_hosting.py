"""AP-05 (API-DEPLOY): durable private deployments, through the routes as mounted and the
hosting controller as the worker runs it (research/plan/api-lifecycle/contracts.md §5,
implementation.md AP-05 05a-05e). Every case runs on the fake world; the `pg`-marked ones
(and every `world` case's pg half) on ap5's PostgreSQL with a real engine process.

    uv run --frozen pytest -q tests/ap05                                  # fake world
    INFRX_D_TASK=ap5 uv run --frozen pytest -q tests/ap05                 # + PostgreSQL
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import signal
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import pytest

from infrx.contracts import errors
from infrx.lab.hosting import PROFILE
from infrx.lab.hosting.engine import Runtime, options_digest
from infrx.lab.hosting.store import Hold

from .conftest import ENGINE_PORT, REPO_ROOT, clip, entries, run

PROC = pathlib.Path(__file__).with_name("controller_proc.py")
NOW = datetime(2026, 10, 2, tzinfo=UTC)

DEPLOYMENTS = "/lab/v1/control/deployments"


# ============================================================ 05a: the operation ===
def test_ap05__a_deploy_request_is_one_operation_and_one_draft(world):
    key = world.key()
    first = world.deploy(key=key)
    assert first.status_code == 202, first.text
    doc = first.json()
    assert first.headers["Location"] == f"/lab/v1/operations/{doc['operation_id']}"
    assert first.headers["Cache-Control"] == "no-store"
    assert (doc["kind"], doc["state"], doc["error"]) == ("deployment.create", "queued", None)
    deployment = doc["resource_id"]
    detail = world.detail(deployment)
    assert (detail["state"], detail["allocation"], detail["ready"]) == ("draft", None, False)
    assert detail["serving_version_id"] == world.serving
    assert detail["operations"] == [doc["operation_id"]]
    again = world.deploy(key=key)              # a lost response: the same operation, no 2nd draft
    assert again.status_code == 202 and again.json()["operation_id"] == doc["operation_id"]
    assert again.json()["resource_id"] == deployment
    other = world.deploy(key=key, max_output_tokens=512)
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_conflict"
    assert world.deploy(key=world.key()).json()["resource_id"] != deployment


def test_ap05__membership_and_role_decide_every_door(world):
    _, deployment = world.deployed()
    assert world.deploy(actor="viewer_a").status_code == 403
    assert world.call("GET", f"{DEPLOYMENTS}/{deployment}", "viewer_a").status_code == 200
    for path in (f"{DEPLOYMENTS}/{deployment}", f"{DEPLOYMENTS}/{deployment}/readiness"):
        assert world.call("GET", path, "dev_b").status_code == 404
        assert world.call("GET", path, "outsider").status_code == 404     # not a member
        assert world.call("GET", path, "nobody").status_code == 401
    for verb in ("smoke", "retire"):
        assert world.call("POST", f"{DEPLOYMENTS}/{deployment}/{verb}", "dev_b",
                          world.key()).status_code == 404
        assert world.call("POST", f"{DEPLOYMENTS}/{deployment}/{verb}", "viewer_a",
                          world.key()).status_code == 403
    assert world.call("GET", f"{DEPLOYMENTS}/not-a-uuid").status_code == 404
    profiles = world.call("GET", "/lab/v1/hosting-profiles", "viewer_a")
    assert profiles.status_code == 200
    assert [p["profile_id"] for p in profiles.json()["data"]] == [PROFILE.profile_id]
    assert profiles.json()["data"][0]["availability"]["state"] == "configured"
    assert world.call("GET", "/lab/v1/hosting-profiles", "outsider").status_code == 404


def test_ap05__an_unsupported_request_names_its_reasons(world):
    bad = world.deploy(hosting_profile="sglang-h100-fp8-v1")
    assert bad.status_code == 422
    assert [f["code"] for f in bad.json()["error"]["field_errors"]] == ["unsupported_profile"]
    long = world.deploy(max_input_tokens=32768, max_output_tokens=1)
    assert long.status_code == 422
    assert [f["field"] for f in long.json()["error"]["field_errors"]] == ["max_input_tokens"]
    # the production Marlin revision has no verified artifact behind it: nothing to install
    prod = world.deploy(serving_version_id=l3_serving())
    assert prod.status_code == 422
    assert [f["code"] for f in prod.json()["error"]["field_errors"]] == ["no_verified_artifact"]
    assert world.deploy(actor="dev_b").status_code == 404      # another provider's revision
    assert world.deploy(max_replicas=2).status_code == 422     # one replica, no autoscaling
    assert world.deploy(warm_policy="scale_to_zero").status_code == 422
    assert world.deploy(expire_after_s=60).status_code == 422
    unkeyed = world.call("POST", DEPLOYMENTS, json=world.body())
    assert unkeyed.status_code == 422
    assert world.drafts() == 0                          # no refusal left a draft behind


def l3_serving() -> str:
    from infrx.contracts.v2 import fixtures as v2fix
    return v2fix.BUILDERS["serving_revision.json"]().serving_version_id


def test_ap05__a_queued_deployment_cancels_and_the_controller_retires_it(world):
    operation, deployment = world.deployed()
    cancelled = world.call("POST", f"/lab/v1/operations/{operation}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    assert world.call("POST", f"/lab/v1/operations/{operation}/cancel").json()["state"] == \
        "cancelled"                                               # repeated: the same answer
    world.drive()
    assert world.state(deployment) == "retired"
    assert world.launcher.starts == [] and world.detail(deployment)["operations"] == []


def test_ap05__a_running_deployment_cancelled_is_reconciled_and_torn_down(world):
    operation, deployment = world.deployed()
    crashed = []

    def stop_after(name):
        if name == "launched":
            crashed.append(name)
            raise KeyboardInterrupt("the controller stops here")
    with pytest.raises(KeyboardInterrupt):
        world.drive(world.controller(boundary=stop_after))
    assert world.op(operation).state == "running" and world.state(deployment) == "validating"
    answer = world.call("POST", f"/lab/v1/operations/{operation}/cancel")
    assert answer.status_code == 200 and answer.json()["state"] == "cancel_requested"
    world.advance(120)                                     # the stopped holder's lease expires
    world.drive()
    assert world.op(operation).state == "cancelled"
    assert world.state(deployment) == "retired"
    assert world.launcher.running == {} and len(world.launcher.starts) == 1
    assert world.detail(deployment)["allocation"]["state"] == "released"
    done = world.call("POST", f"/lab/v1/operations/{operation}/cancel")
    assert done.status_code == 200 and done.json()["state"] == "cancelled"


def test_ap05__operations_are_cancelled_only_inside_their_workspace(world):
    operation, _ = world.deployed()
    assert world.call("POST", f"/lab/v1/operations/{operation}/cancel", "dev_b").status_code \
        == 404
    assert world.call("POST", f"/lab/v1/operations/{operation}/cancel", "viewer_a"
                      ).status_code == 403
    assert world.call("POST", "/lab/v1/operations/not-a-uuid/cancel").status_code == 404
    assert world.op(operation).state == "queued"


BOUNDARIES = ("allocated", "installed", "launched", "identity")


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_ap05__a_controller_stopped_at_each_boundary_resumes_once(world, boundary):
    operation, deployment = world.deployed()

    def stop(name):
        if name == boundary:
            raise KeyboardInterrupt(name)
    with pytest.raises(KeyboardInterrupt):
        world.drive(world.controller(owner="first", boundary=stop))
    world.drive(world.controller(owner="second"))           # its lease is still live: waits
    assert world.op(operation).state == "running"
    world.advance(120)
    world.drive(world.controller(owner="second"))
    doc = world.op(operation)
    assert (doc.state, doc.error) == ("succeeded", None), doc
    assert world.state(deployment) == "validating"
    assert len(world.launcher.starts) == 1 and len(world.launcher.running) == 1
    detail = world.detail(deployment)
    assert detail["allocation"]["state"] == "launched"


def test_ap05__a_stale_fence_writes_nothing(world):
    operation, deployment = world.deployed()
    first = run(world.ops.lease(operation, "first", 60))
    world.advance(120)
    second = run(world.ops.lease(operation, "second", 60))
    assert second.fence == first.fence + 1
    stale = Hold(operation, first.fence, deployment)
    with pytest.raises(errors.Conflict):
        run(world.hosting.store.transition(stale, deployment, world.A, "draft", "retired",
                                           "a stale controller"))
    assert world.state(deployment) == "draft"
    with pytest.raises(errors.NotFound):                 # a hold of another deployment
        run(world.hosting.store.transition(Hold(operation, second.fence, world.serving),
                                           deployment, world.A, "draft", "retired", "x"))


# ================================================== 05b: the allocator and launcher ===
def test_ap05__a_taken_slot_is_capacity_unavailable_never_an_eviction(world):
    first_op, first = world.deployed()
    world.drive()
    assert world.op(first_op).state == "succeeded" and world.state(first) == "validating"
    second_op, second = world.deployed()
    world.drive()
    failed = world.op(second_op)
    assert (failed.state, failed.error.code, failed.error.retryable) == \
        ("failed", "capacity_unavailable", True)
    assert world.state(second) == "retired"
    assert world.state(first) == "validating"               # the holder is untouched
    assert world.detail(first)["allocation"]["state"] == "launched"
    assert list(world.launcher.running) == [f"infrx-hosting-{first}"]
    retire = world.call("POST", f"{DEPLOYMENTS}/{first}/retire", key=world.key())
    assert retire.status_code == 202
    world.drive()
    third_op, third = world.deployed()                     # the slot is free again
    world.drive()
    assert world.op(third_op).state == "succeeded"
    assert list(world.launcher.running) == [f"infrx-hosting-{third}"]


@pytest.mark.pg
@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_ap05__a_controller_process_killed_at_each_boundary_resumes_once(pg_world, boundary):
    """SIGKILL, not an exception: the dead holder's lease, engine and half-done work remain;
    the next holder finds the engine by its tag (no second start), re-uses what it recorded,
    and the dead holder's fence writes nothing."""
    w = pg_world
    operation, deployment = w.deployed()
    died = subprocess.run((sys.executable, str(PROC), w.service_dsn, w.dsn, str(w.tmp),
                           "doomed", boundary), timeout=240, capture_output=True)
    assert died.returncode == -signal.SIGKILL, died.stderr.decode()[-2000:]
    dead = run(w.ops.get(operation, w.actors["dev_a"]))
    assert (dead.state, dead.lease_owner) == ("running", "doomed")
    already = len(w.launcher.running)
    assert already == (1 if boundary in ("launched", "identity") else 0)
    w.advance(120)                                         # the dead holder's lease expires
    w.drive(w.controller(owner="resumer"))
    doc = w.op(operation)
    assert (doc.state, doc.error) == ("succeeded", None), doc
    assert w.state(deployment) == "validating"
    assert len(w.launcher.running) == 1                    # exactly one engine, ever
    assert w.launcher.starts == ([] if already else [f"infrx-hosting-{deployment}"])
    identities = [r for r in run(w.store.receipts(deployment)) if r.kind == "identity"]
    assert len(identities) == 1 and identities[0].passed
    with pytest.raises(errors.Conflict):                   # the dead holder's fence
        run(w.store.transition(Hold(operation, dead.fence, deployment), deployment, w.A,
                               "validating", "retired", "a dead controller"))


@pytest.mark.pg
def test_ap05__foreign_processes_are_never_stopped_or_adopted(pg_world, tmp_path):
    """The serving engine and any process without this deployment's tag in its argv are not
    a candidate's: a state file naming such a pid (a reused pid, a forged file) is ignored."""
    w = pg_world
    serving = subprocess.Popen((sys.executable, "-c", "import time; time.sleep(600)",
                                "marlin2b-8000"), start_new_session=True)
    try:
        operation, deployment = w.deployed()
        engines = w.launcher.inner.state_dir
        engines.mkdir(parents=True, exist_ok=True)
        (engines / f"infrx-hosting-{deployment}.json").write_text(json.dumps(
            {"pid": serving.pid, "port": ENGINE_PORT, "tag": f"infrx-hosting-{deployment}"}))
        w.drive()
        assert w.op(operation).state == "succeeded"
        assert w.launcher.starts == [f"infrx-hosting-{deployment}"]   # not adopted
        w.call("POST", f"{DEPLOYMENTS}/{deployment}/retire", key=w.key())
        w.drive()
        assert w.state(deployment) == "retired" and w.launcher.running == {}
        assert serving.poll() is None                      # never signalled
    finally:
        serving.kill()
        serving.wait()


def test_ap05__the_box_launcher_runs_only_its_own_unit(tmp_path):
    from infrx.lab.hosting.engine import BoxLauncher
    from infrx.lab.hosting.store import Allocation
    calls = []
    inspected = {"State": {"Running": True}, "Image": PROFILE.runtime_image_ref.split("@")[1],
                 "Args": ["/model", *PROFILE.flags],
                 "Mounts": [{"Source": "/opt/dlami/nvme/hosting/x", "Destination": "/model"}]}

    def fake_run(argv, **kw):
        calls.append(tuple(argv))
        out = json.dumps([inspected]) if argv[:2] == ["docker", "inspect"] else ""
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
    box = BoxLauncher(tmp_path / "etc", run=fake_run)
    mine = Allocation(allocation_id=str(uuid.uuid4()), deployment_revision_id=str(uuid.uuid4()),
                      slot="pilot-l40s/candidate-0", port=8100, operation_id=str(uuid.uuid4()),
                      resource_tag=f"infrx-hosting-{uuid.uuid4()}", reserved_at=NOW)
    run(box.start(mine, pathlib.Path("/opt/dlami/nvme/hosting/x")))
    env = (tmp_path / "etc" / "8100.env").read_text()
    assert f"INFRX_HOSTING_TAG={mine.resource_tag}\n" in env
    assert "WEIGHTS=/opt/dlami/nvme/hosting/x\n" in env
    assert calls == [("systemctl", "start", "infrx-candidate@8100.service")]
    seen = run(box.inspect(mine))
    assert seen == Runtime(image=inspected["Image"], flags=PROFILE.flags,
                           model_dir="/opt/dlami/nvme/hosting/x", declared=False)
    assert calls[-1] == ("docker", "inspect", "marlin2b-8100")
    other = mine.model_copy(update={"resource_tag": f"infrx-hosting-{uuid.uuid4()}"})
    before = len(calls)
    assert run(box.inspect(other)) is None and run(box.stop(other)) is False
    assert len(calls) == before                            # nothing run for another's tag
    inspected["State"]["Running"] = False
    assert run(box.inspect(mine)) is None
    assert run(box.stop(mine)) is True
    assert calls[-1] == ("systemctl", "stop", "infrx-candidate@8100.service")
    assert not (tmp_path / "etc" / "8100.env").exists()
    before = len(calls)
    serving = mine.model_copy(update={"port": 8000})
    with pytest.raises(ValueError):                        # the serving engine's port
        run(box.start(serving, pathlib.Path("/x")))
    assert run(box.stop(serving)) is False and len(calls) == before


STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "{log}"
for rc in "$0.$1.$2.rc" "$0.$1.rc" "$0.rc"; do [ -f "$rc" ] && exit "$(cat "$rc")"; done
[ -f "$0.out" ] && cat "$0.out"
exit 0
"""


def window(tmp_path, state: str, **env) -> tuple[subprocess.CompletedProcess, list[str]]:
    """infra/lab/hosting/window.sh in a sandbox root, every box tool a recording stub."""
    stub, root = tmp_path / "bin", tmp_path / "root"
    if not stub.exists():
        stub.mkdir()
        (root / "etc" / "systemd" / "system").mkdir(parents=True)
        for tool in ("systemctl", "nvidia-smi", "docker", "curl", "install", "git"):
            (stub / tool).write_text(STUB.format(log=tmp_path / "calls.log"))
            (stub / tool).chmod(0o755)
        (stub / "git.out").write_text("a" * 40 + "\n")
        (stub / "systemctl.is-active.rc").write_text("3")      # the gateway: inactive
    (tmp_path / "calls.log").write_text("")
    done = subprocess.run(("bash", str(REPO_ROOT / "infra" / "lab" / "hosting" / "window.sh")),
                          capture_output=True, text=True, timeout=60, env={
                              "PATH": f"{stub}:/usr/bin:/bin", "HOME": str(root),
                              "REPO": str(REPO_ROOT), "INFRX_ROOT": str(root),
                              "RELEASE": "a" * 40, "STATE": state, "GPU_FREE_S": "1",
                              "READY_S": "1", "READY_SLEEP_S": "0", **env})
    return done, (tmp_path / "calls.log").read_text().splitlines()


def test_ap05__the_box_window_never_runs_two_engines_on_the_gpu(tmp_path):
    done, calls = window(tmp_path, "open", PORT="8000")
    assert done.returncode == 3 and not any(c.startswith("systemctl stop") for c in calls)
    (tmp_path / "bin" / "systemctl.is-active.rc").write_text("0")   # the gateway serves
    done, calls = window(tmp_path, "open", PORT="8100")
    assert done.returncode == 3 and "maintenance" in done.stderr
    assert not any(c.startswith("systemctl stop") for c in calls)
    (tmp_path / "bin" / "systemctl.is-active.rc").write_text("3")
    done, calls = window(tmp_path, "open", PORT="8100")
    assert done.returncode == 0, done.stderr
    stop = calls.index("systemctl stop marlin2b-vllm.service")
    assert stop < next(i for i, c in enumerate(calls) if c.startswith("install -m 0644"))
    assert calls[-1] == "systemctl daemon-reload"
    etc = tmp_path / "root" / "etc" / "infrx-lab" / "hosting"
    etc.mkdir(parents=True)
    (etc / "8100.env").write_text("INFRX_HOSTING_TAG=x\n")
    (etc / "8000.env").write_text("INFRX_HOSTING_TAG=forged\n")
    done, calls = window(tmp_path, "close", HOSTING_ROOT=str(tmp_path / "models"))
    assert done.returncode == 0, done.stderr
    assert "systemctl stop infrx-candidate@8100.service" in calls
    assert "docker rm -f marlin2b-8100" in calls
    assert not any("8000" in c for c in calls if c.startswith(("systemctl stop", "docker")))
    assert calls.index("docker rm -f marlin2b-8100") < calls.index(
        "systemctl start marlin2b-vllm.service")
    (tmp_path / "bin" / "curl.rc").write_text("7")                 # the engine never answers
    done, _ = window(tmp_path, "close", HOSTING_ROOT=str(tmp_path / "models"))
    assert done.returncode == 4 and "do not reopen" in done.stderr


# ===================================================== 05c: install and identity ===
def test_ap05__the_engine_serves_exactly_the_requested_revision(world):
    operation, deployment = world.deployed()
    world.drive()
    assert world.op(operation).state == "succeeded"
    identity = world.readiness(deployment)["identity"]
    assert identity["passed"] and identity["reasons"] == [] and identity["operation_id"] == \
        operation
    seen = identity["observed"]
    assert seen["files"] == {e.relative_path: e.sha256 for e in entries(world.files)}
    assert seen["image"] == PROFILE.runtime_image_ref.partition("@")[2]
    assert seen["options_digest"] == PROFILE.engine_options_digest
    assert seen["served_models"] == [PROFILE.served_model_name]
    assert seen["model_dir"].endswith(f"infrx-hosting-{deployment}")
    assert seen["serving_version_id"] == world.serving


def _tamper(world, how: str):
    def boundary(name: str) -> None:
        if name != "launched":
            return
        model = next((world.target.model_root).iterdir())
        if how == "bytes":
            (model / "model-00001-of-00002.safetensors").write_bytes(b"\x00other weights")
        elif how == "extra":
            (model / "modeling_marlin.py").write_text("import os  # remote code")
    return boundary


MISMATCHES = {
    "runtime_image": lambda w: setattr(w.launcher, "image", "sha256:" + "0" * 64),
    "engine_options": lambda w: setattr(w.launcher, "flags", tuple(
        "32" if f == "8" else f for f in PROFILE.flags)),
    "served_model": lambda w: setattr(w.engine, "models", ["marlin2b-other"]),
    "files.model-00001-of-00002.safetensors": "bytes",
    "files.modeling_marlin.py": "extra",
}


@pytest.mark.parametrize("field", MISMATCHES)
def test_ap05__a_mismatched_identity_never_becomes_ready(fake_world, field):
    w = fake_world
    how = MISMATCHES[field]
    if callable(how):
        how(w)
    operation, deployment = w.deployed()
    w.drive(w.controller(boundary=None if callable(how) else _tamper(w, how)))
    doc = w.op(operation)
    assert (doc.state, doc.error.code) == ("failed", "identity_mismatch")
    identity = w.readiness(deployment)["identity"]
    assert not identity["passed"]
    expected = [field, "weights"] if how == "bytes" else [field]   # the manifest and the pin
    assert [r["field"] for r in identity["reasons"]] == expected
    assert w.state(deployment) == "retired" and w.launcher.running == {}
    assert w.detail(deployment)["allocation"]["state"] == "released"
    assert not any(w.target.model_root.iterdir())          # its installed model is removed


def test_ap05__the_source_must_hold_the_verified_bytes(world):
    (world.target.source_dir / "tokenizer.json").write_bytes(b'{"model": "swapped"}')
    operation, deployment = world.deployed()
    world.drive()
    doc = world.op(operation)
    assert (doc.state, doc.error.code) == ("failed", "artifact_unavailable")
    assert "tokenizer.json" in doc.error.message
    assert world.launcher.starts == [] and world.state(deployment) == "retired"
    (world.target.source_dir / "tokenizer.json").unlink()
    operation, _ = world.deployed()
    world.drive()
    assert world.op(operation).error.message.endswith("missing:tokenizer.json")


def test_ap05__the_profile_is_the_measured_marlin_serving_version():
    record = json.loads((REPO_ROOT / "models" / "marlin2b" / "serving-version.json").read_text())
    flags = [f.replace("${ENGINE_MAX_NUM_SEQS}", record["settings"]["ENGINE_MAX_NUM_SEQS"])
             .replace("${PROCESSING_CACHE_DIR}", record["settings"]["PROCESSING_CACHE_DIR"])
             for f in record["flags"]]
    assert list(PROFILE.flags) == flags
    assert options_digest(PROFILE.flags) == record["engine_options_digest"] == \
        PROFILE.engine_options_digest
    assert PROFILE.runtime_image_ref == record["runtime_image"]["ref"]
    assert PROFILE.served_model_name == flags[flags.index("--served-model-name") + 1]


# ================================================== 05d: the smoke and promotion ===
def created(world) -> tuple[str, str]:
    """A deployment its create operation brought to `validating` (engine up, identity)."""
    operation, deployment = world.deployed()
    world.drive()
    assert world.op(operation).state == "succeeded" and world.state(deployment) == "validating"
    return operation, deployment


def smoked(world, deployment: str, key: str | None = None) -> str:
    answer = world.call("POST", f"{DEPLOYMENTS}/{deployment}/smoke", key=key or world.key())
    assert answer.status_code == 202, answer.text
    assert answer.json()["kind"] == "deployment.smoke"
    return answer.json()["operation_id"]


def engine_requests(world) -> list[dict]:
    """The chat requests the candidate engine received."""
    if world.real:
        import httpx
        state = httpx.get(f"http://127.0.0.1:{ENGINE_PORT}/_control", timeout=5).json()
        return [{}] * state["requests"]
    return world.engine.seen


def test_ap05__a_passing_smoke_promotes_the_exact_deployment(world):
    _, deployment = created(world)
    operation = smoked(world, deployment)
    world.drive()
    assert world.op(operation).state == "succeeded"
    assert world.state(deployment) == "ready_private"
    ready = world.readiness(deployment)
    smoke, identity = ready["smoke"], ready["identity"]
    assert smoke["passed"] and smoke["operation_id"] == operation
    assert identity["passed"] and identity["operation_id"] == operation     # re-observed
    seen = smoke["observed"]
    assert seen["status"] == 200 and seen["served_model"] == PROFILE.served_model_name
    assert seen["prompt_tokens"] >= 256 and seen["completion_tokens"] >= 1
    assert seen["finish_reason"] in ("stop", "length") and 0 <= seen["elapsed_ms"]
    assert seen["video_sha256"] == "sha256:" + hashlib.sha256(clip()).hexdigest()
    assert len(engine_requests(world)) == 1                  # one bounded request
    if not world.real:
        part = world.engine.seen[0]["messages"][0]["content"][0]
        assert part["type"] == "video_url"
        assert part["video_url"]["url"].startswith("data:video/mp4;base64,")
    detail = world.detail(deployment)
    assert detail["actions"] == ["retire"] and detail["state"] == "ready_private"


SMOKE_FAILURES = {
    "smoke.status": {"status": 500},
    "smoke.content": {"content": "  "},
    "smoke.video": {"prompt_tokens": 20},                 # a text-only answer: no video tokens
    "smoke.finish_reason": {"finish_reason": "abort"},
    "smoke.usage": {"completion_tokens": 0},
}


@pytest.mark.parametrize("field", SMOKE_FAILURES)
def test_ap05__a_failing_smoke_never_promotes(fake_world, field):
    w = fake_world
    _, deployment = created(w)
    good = dict(w.engine.answer)
    w.engine.answer.update(SMOKE_FAILURES[field])
    operation = smoked(w, deployment)
    w.drive()
    doc = w.op(operation)
    assert (doc.state, doc.error.code) == ("failed", "smoke_failed")
    assert w.state(deployment) == "validating"            # actionable: smoke again or retire
    ready = w.readiness(deployment)
    assert not ready["ready"] and "smoke_failed" in ready["reasons"]
    assert [r["field"] for r in ready["smoke"]["reasons"]] == [field]
    w.engine.answer = good
    again = smoked(w, deployment)
    w.drive()
    assert w.op(again).state == "succeeded" and w.state(deployment) == "ready_private"


def test_ap05__an_unreachable_or_swapped_engine_never_promotes(fake_world):
    w = fake_world
    _, deployment = created(w)
    w.engine.up = False
    operation = smoked(w, deployment)
    w.drive()
    doc = w.op(operation)
    assert (doc.state, doc.error.code) == ("failed", "identity_mismatch")   # not even asked
    assert w.engine.seen == [] and w.state(deployment) == "validating"
    w.engine.up = True
    running = w.launcher.running[f"infrx-hosting-{deployment}"]   # swapped since the create
    w.launcher.running[f"infrx-hosting-{deployment}"] = running.model_copy(
        update={"image": "sha256:" + "9" * 64})
    operation = smoked(w, deployment)
    w.drive()
    assert w.op(operation).error.code == "identity_mismatch" and w.engine.seen == []
    assert w.readiness(deployment)["identity"]["reasons"][0]["field"] == "runtime_image"


def test_ap05__a_smoke_needs_a_validating_deployment_with_nothing_running(world):
    _, deployment = world.deployed()
    early = world.call("POST", f"{DEPLOYMENTS}/{deployment}/smoke", key=world.key())
    assert early.status_code == 409 and early.json()["error"]["code"] == "state_conflict"
    world.drive()
    key = world.key()
    first = smoked(world, deployment, key)
    assert smoked(world, deployment, key) == first          # the same key while it runs
    world.drive()
    late = world.call("POST", f"{DEPLOYMENTS}/{deployment}/smoke", key=world.key())
    assert late.status_code == 409


SMOKE_BOUNDARIES = ("identity", "smoke", "promoted")


@pytest.mark.parametrize("boundary", SMOKE_BOUNDARIES)
def test_ap05__a_controller_stopped_mid_smoke_never_smokes_twice(fake_world, boundary):
    w = fake_world
    _, deployment = created(w)
    operation = smoked(w, deployment)

    def stop(name):
        if name == boundary:
            raise KeyboardInterrupt(name)
    with pytest.raises(KeyboardInterrupt):
        w.drive(w.controller(owner="first", boundary=stop))
    w.advance(120)
    w.drive(w.controller(owner="second"))
    assert w.op(operation).state == "succeeded" and w.state(deployment) == "ready_private"
    assert len(w.engine.seen) == 1
    mine = [r for r in run(w.store.receipts(deployment)) if r.operation_id == operation]
    assert sorted(r.kind for r in mine) == ["identity", "smoke"]
