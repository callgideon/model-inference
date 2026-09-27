"""B3 (CHECKPOINT-IDEM) in the fake world: signed checkpoint events, registration, a
suite subscription queuing B1 runs within its budget, dedup of duplicate and out-of-order
events, crash recovery. `test_checkpoints_pg.py` repeats the receipt half on real D7.

    uv run --frozen pytest -q tests/b/checkpoints/test_checkpoints.py
"""
from __future__ import annotations

import asyncio
import json

import pytest
from infrx.contracts import errors
from infrx.evaluation import checkpoints
from infrx.evaluation.runner import Limits, Runner

from ..runner.world import DEPLOYMENT, VIEWER, DevWallet
from .world import DEV, NEMO, NOW, OTHER, Crash, World, safetensors, timedelta


def run(coro):
    return asyncio.run(coro)


def receive(w: World, event: dict, secret: bytes | None = None):
    body, signature = w.body(event, secret)
    return run(checkpoints.receive(body, signature, keys=w.keys.get, ledger=w.ledger,
                                   store=w.store))


def subscribe(w: World, payload: dict, user: str = DEV):
    return run(checkpoints.subscribe(w.ledger, w.store, payload, access=w.access, user_id=user))


def handle(w: World, event: dict):
    return run(checkpoints.on_checkpoint(
        event["checkpoint_id"], provider_org_id=NEMO, ledger=w.ledger, store=w.store,
        registries={"mem": w.registry.fetch}, deployer=w.deployer, access=w.access))


def states(w: World, sub: int) -> dict:
    """checkpoint n -> (state, reason) for subscription `sub`."""
    decided = run(w.ledger.decisions(w.subscription(sub)["subscription_id"]))
    return {int(c[-12:], 16): (d["state"], d["reason"]) for c, d in decided.items()}


# ------------------------------------------------------------------------- B3.a receive
def test_b3_a_signed_event_is_received_once_and_a_replay_is_the_same_receipt() -> None:
    """CHECKPOINT-IDEM: a verified event is one receipt and ONE outbox event; the same
    event again (a retry, a replay inside the window) is the same receipt and no second
    event; the same checkpoint id naming other bytes is a conflict, not a new checkpoint."""
    w = World()
    first = receive(w, w.event(1))
    assert first["state"] == "received" and first["artifact_digest"] == w.event(1)["artifact"]["digest"]
    assert receive(w, w.event(1)) == first and len(w.store.outbox) == 1
    assert w.store.outbox[0]["payload"] == {"checkpoint_id": w.event(1)["checkpoint_id"]}
    with pytest.raises(errors.IdempotencyConflict):
        receive(w, w.event(1, data=safetensors(99), uri="mem://other"))
    assert len(w.store.receipts) == 1 and len(w.store.outbox) == 1


def test_b3_forged_foreign_stale_and_unbound_events_are_refused() -> None:
    """CHECKPOINT-IDEM: a body whose signature does not verify, a key nobody registered, a
    provider signing for another (provider binding), an event issued outside the replay
    window either way, and an external run the provider does not have are all refused
    before anything is recorded."""
    w = World()
    event = w.event(1)
    body, signature = w.body(event)
    tampered = body.replace(b'"step": 1', b'"step": 9')
    with pytest.raises(errors.InvalidApiKey):
        run(checkpoints.receive(tampered, signature, keys=w.keys.get, ledger=w.ledger,
                                store=w.store))
    for bad in ({**event, "key_id": "key-unknown"}, {**event, "key_id": "key-other"}):
        with pytest.raises(errors.InvalidApiKey):
            receive(w, bad, w.other_secret)
    with pytest.raises(errors.InvalidApiKey):              # OTHER's key cannot name NEMO
        receive(w, {**event, "key_id": "key-other", "provider_org_id": OTHER}, w.other_secret)
    for skew in (timedelta(seconds=301), -timedelta(seconds=301)):
        with pytest.raises(errors.InvalidRequest, match="window"):
            receive(w, w.event(1, issued=NOW - skew))
    assert receive(w, w.event(2, issued=NOW - timedelta(seconds=299)))["state"] == "received"
    foreign = run(w.store.publish(w.external_run("e3000009-0000-4000-8000-000000000009"),
                                  provider_org_id=NEMO, actor=DEV))
    with pytest.raises(errors.NotFound):
        receive(w, {**w.event(3), "external_run_ref": foreign.replace(
            "e3000009-0000-4000-8000-000000000009", "e3000008-0000-4000-8000-000000000008")})
    with pytest.raises(errors.InvalidApiKey, match="not signed"):
        run(checkpoints.receive(b"not json", "sha256:0", keys=w.keys.get, ledger=w.ledger,
                                store=w.store))
    assert list(w.store.receipts) == [w.event(2)["checkpoint_id"]]
    assert list(w.ledger.by_id) == [w.event(2)["checkpoint_id"]]


# ------------------------------------------------------------------ B3.b subscriptions
def test_b3_subscribe_needs_a_scheduling_member_a_credit_budget_and_its_evaluator() -> None:
    """A subscription spends provider_dev CREDIT on its owner's behalf: a viewer cannot
    subscribe, a PROVIDER_USD limit or a run limit above the total is refused, the evaluator
    spec must be the one its ref names, and the external run must be the provider's."""
    w = World()
    with pytest.raises(errors.Forbidden):
        subscribe(w, w.subscription(1), user=VIEWER)
    usd = {"unit": "PROVIDER_USD", "value": "10.00000000"}
    for payload in (w.subscription(1) | {"run_limit": usd, "limit": usd},
                    w.subscription(1) | {"run_limit": usd},
                    w.subscription(1, run_limit="20", limit="10"),
                    w.subscription(1, evaluator={**w.subscription(1)["evaluator"],
                                                 "max_requests": 9}),
                    w.subscription(1) | {"max_active": 0}):
        with pytest.raises(errors.InvalidRequest):
            subscribe(w, payload)
    missing = w.external.replace(w.external.split(":")[3].split("@")[0],
                                 "e3000007-0000-4000-8000-000000000007")
    with pytest.raises(errors.NotFound):
        subscribe(w, w.subscription(1) | {"external_run_ref": missing})
    assert w.ledger.subs == {}
    assert subscribe(w, w.subscription(1)).owner_user_id == DEV


def test_b3_a_valid_checkpoint_is_one_dev_evaluation_per_subscription_never_promoted() -> None:
    """CHECKPOINT-IDEM: a validated checkpoint is deployed once, to a private dev
    deployment pinned by its digest, and queued as one B1 run per subscription - the run
    pins the subscription's suite and budget and targets that deployment; a redelivery is
    the same decisions, the same runs and no second deployment. The checkpoint ends
    `evaluated`: nothing publishes or promotes it. The run then scores like any B1 run."""
    w = World()
    subscribe(w, w.subscription(1))
    subscribe(w, w.subscription(2, policy="every", run_limit="5"))
    receive(w, w.event(1))
    out = handle(w, w.event(1))
    assert {d["state"] for d in out.values()} == {"queued"} and len(w.store.runs) == 2
    assert [c["digest"] for c in w.deployer.calls] == [w.event(1)["artifact"]["digest"]]
    assert w.deployer.deployed[w.event(1)["checkpoint_id"]]["environment"] == "dev"
    assert w.store.receipts[w.event(1)["checkpoint_id"]]["state"] == "evaluated"
    again = handle(w, w.event(1))
    assert again == out and len(w.store.runs) == 2 and w.registry.fetched == ["mem://ckpt-1"]
    run_id = out[w.subscription(2)["subscription_id"]]["run_id"]
    assert run_id == checkpoints.run_id_of(w.subscription(2)["subscription_id"],
                                           w.event(1)["checkpoint_id"])
    record = run(w.store.resolve(w.store.runs[run_id]["ref"], provider_org_id=NEMO))
    assert record.serving_ref.split(":")[3].startswith(w.event(1)["checkpoint_id"])
    assert record.budgets[0].limit.value == "5.00000000" and record.environment == "dev"
    assert (record.dataset_ref, record.harness_ref, record.seed, record.max_cases) == (
        w.dataset, w.harness, 7, 3)
    frozen = run(checkpoints.runner.resume(w.store, run_id, evaluator=w.subscription(2)["evaluator"],
                                           provider_org_id=NEMO))
    report = run(Runner(w.store, w.objects, DevWallet("1000"), DEPLOYMENT, worker_id="w",
                        limits=Limits(30, 2, 2, 1)).run(frozen))
    assert report["cases"] == {"done": 3} and report["state"] == "succeeded"


def test_b3_a_malformed_or_changed_artifact_is_rejected_and_never_deploys() -> None:
    """CHECKPOINT-IDEM: bytes changed under the signed URL (digest mismatch), a file that is
    not a well-formed safetensors checkpoint, and a registry no adapter serves are each
    `rejected` with the reason, visible as a skip per subscription; nothing is deployed or
    queued, and a redelivery neither refetches nor deploys."""
    w = World()
    subscribe(w, w.subscription(1, policy="every"))
    header = json.dumps({"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 64]}}).encode()
    cases = {1: w.event(1),
             2: w.event(2, data=b"\x08" + b"\x00" * 7 + b"{not json"),
             3: w.event(3, data=len(header).to_bytes(8, "little") + header + b"\x00" * 8),
             4: w.event(4, uri="ftp://ckpt-4")}
    w.registry.blobs["mem://ckpt-1"] = safetensors(1) + b"!"      # mutated after signing
    for n, event in cases.items():
        receive(w, event)
        handle(w, event)
    assert w.ledger.rejected == {cases[1]["checkpoint_id"]: "digest_mismatch",
                                 cases[2]["checkpoint_id"]: "malformed",
                                 cases[3]["checkpoint_id"]: "malformed",
                                 cases[4]["checkpoint_id"]: "unsupported_registry"}
    assert {r["state"] for r in w.store.receipts.values()} == {"rejected"}
    assert states(w, 1) == {n: ("skipped", "rejected") for n in cases}
    fetched = list(w.registry.fetched)
    handle(w, cases[1])
    assert w.deployer.calls == [] and w.store.runs == {} and w.registry.fetched == fetched


def test_b3_out_of_order_events_never_redefine_latest() -> None:
    """CHECKPOINT-IDEM: a `latest_only` subscription evaluates the highest step received,
    whatever the arrival order - an older step arriving after a newer one is `superseded`
    (and so is one handled after a newer one arrived); a newer step that was rejected does
    not supersede anything; an `every` subscription evaluates each."""
    w = World()
    subscribe(w, w.subscription(1))
    subscribe(w, w.subscription(2, policy="every"))
    for n in (2, 1):                      # step 2 arrives first, then step 1
        receive(w, w.event(n))
    for n in (1, 2):                      # handled in the other order
        handle(w, w.event(n))
    assert states(w, 1) == {1: ("skipped", "superseded"), 2: ("queued", None)}
    assert states(w, 2) == {1: ("queued", None), 2: ("queued", None)}
    receive(w, w.event(5, data=b"junk"))  # a newer step, malformed
    receive(w, w.event(3))
    handle(w, w.event(5))
    handle(w, w.event(3))
    assert states(w, 1)[3] == ("queued", None) and states(w, 1)[5] == ("skipped", "rejected")


def test_b3_a_burst_is_bounded_by_concurrency_and_budget_with_visible_skips() -> None:
    """CHECKPOINT-IDEM: at `max_active` unfinished runs a checkpoint is handed back undecided
    (`CapacityExhausted`, the relay retries it); once a run ends it is queued; a checkpoint
    that would take the queued runs past the subscription's CREDIT total is a visible
    `budget` skip - a burst never spends past the total."""
    w = World()
    subscribe(w, w.subscription(1, policy="every", run_limit="10", limit="20", max_active=1))
    for n in (1, 2, 3, 4):
        receive(w, w.event(n))
    handle(w, w.event(1))
    with pytest.raises(errors.CapacityExhausted):
        handle(w, w.event(2))
    assert states(w, 1) == {1: ("queued", None)} and len(w.store.runs) == 1
    (first,) = w.store.runs
    run(w.store.cancel_run(first, provider_org_id=NEMO))
    handle(w, w.event(2))
    handle(w, w.event(3))
    handle(w, w.event(4))
    assert states(w, 1) == {1: ("queued", None), 2: ("queued", None),
                            3: ("skipped", "budget"), 4: ("skipped", "budget")}
    assert len(w.store.runs) == 2 and len(w.deployer.deployed) == 2
    handle(w, w.event(3))                 # a redelivered skip stays a skip
    assert states(w, 1)[3] == ("skipped", "budget") and len(w.store.runs) == 2


def test_b3_a_crash_between_receipt_and_dispatch_never_queues_paid_work_twice() -> None:
    """CHECKPOINT-IDEM: a worker dying after the run was created and before its decision
    was recorded leaves the outbox event to be redelivered; the redelivery derives the same
    run id and record, so D7 answers the same run - one run, one decision - and the
    checkpoint still ends `evaluated`."""
    w = World()
    subscribe(w, w.subscription(1))
    receive(w, w.event(1))
    w.ledger.crash_on_decide = 1
    with pytest.raises(Crash):
        handle(w, w.event(1))
    assert len(w.store.runs) == 1 and w.ledger.decided == {}
    out = handle(w, w.event(1))
    (decision,) = out.values()
    assert decision["state"] == "queued" and list(w.store.runs) == [decision["run_id"]]
    assert w.store.receipts[w.event(1)["checkpoint_id"]]["state"] == "evaluated"


def test_b3_a_revoked_owner_or_grant_is_a_visible_skip() -> None:
    """R160 at every queue: the subscription's owner must still be a scheduling member (the
    L2 port) and the dataset's grant still current (D7); otherwise the checkpoint is skipped
    with the refusal's code, not queued, and not retried."""
    w = World()
    subscribe(w, w.subscription(1))
    store = w.access.store
    store.memberships[(NEMO, DEV)] = store.memberships[(NEMO, DEV)].model_copy(
        update={"revoked_at": store.now})
    receive(w, w.event(1))
    handle(w, w.event(1))
    assert states(w, 1) == {1: ("skipped", "not_found")}
    store.memberships[(NEMO, DEV)] = store.memberships[(NEMO, DEV)].model_copy(
        update={"revoked_at": None})
    w.store.revoke(w.grant)
    receive(w, w.event(2))
    handle(w, w.event(2))
    assert states(w, 1)[2] == ("skipped", "forbidden") and w.store.runs == {}
