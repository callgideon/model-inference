"""AP-05 (API-DEPLOY): durable private deployments, through the routes as mounted and the
hosting controller as the worker runs it (research/plan/api-lifecycle/contracts.md §5,
implementation.md AP-05 05a-05e). Every case runs on the fake world; the `pg`-marked ones
(and every `world` case's pg half) on ap5's PostgreSQL with a real engine process.

    uv run --frozen pytest -q tests/ap05                                  # fake world
    INFRX_D_TASK=ap5 uv run --frozen pytest -q tests/ap05                 # + PostgreSQL
"""
from __future__ import annotations

import pytest

from infrx.contracts import errors
from infrx.lab.hosting import PROFILE
from infrx.lab.hosting.store import Hold

from .conftest import run

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
