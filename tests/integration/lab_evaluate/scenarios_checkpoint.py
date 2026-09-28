"""E6L j08: B3 on the real D7 store (receipts, the Lab outbox, runs) - an externally trained
checkpoint benchmarked on the pinned suite, once however often it is delivered.

The checkpoint ledger stays B3's fake (`FakeCheckpointLedger`) until lab-sql persists it
(WR-B3-1); the dev deployer is L3's stand-in (`Deployer`: a private dev serving ref, never a
public target); the signing key is generated here and never written anywhere. The run the
checkpoint queues is worked by B1's `Runner` in-process - the I5 worker process is j09's.
"""
from __future__ import annotations

import hashlib
import json
import secrets

import lab_world as lw
import pytest
from lab_world import run

KEY_ID = "e6l-ckpt-key"


class Checkpoints:
    """One external training run with one latest_only subscription on the text benchmark."""

    def __init__(self, lab, n: int) -> None:
        from infrx.contracts.lab import records
        from tests.b.checkpoints.world import Deployer, FakeCheckpointLedger, Registry
        self.lab, self.n = lab, n
        now = lab.sql("select infrx.now()")[0][0]
        self.ledger, self.registry, self.deployer = FakeCheckpointLedger(now), Registry(), \
            Deployer()
        self.secret = secrets.token_bytes(32)
        self.keys = {KEY_ID: (lab.NEMO, self.secret)}.get
        self.issued = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        dataset = lab.dataset("text", 1).dataset_ref
        external_id = lw.uid(n, 0xe3e)
        self.external = lab.publish({
            "schema": "lab.external_run.1", "provider_org_id": lab.NEMO,
            "external_run_id": external_id, "purpose": "training", "connector": "manual-bundle",
            "dataset_ref": dataset, "submit_key": records.submit_key(external_id),
            "state": "prepared",
            "budget": {"limit": {"unit": "PROVIDER_USD", "value": "40.00000000"},
                       "reserved": {"unit": "PROVIDER_USD", "value": "0.00000000"},
                       "payer_ref": f"lab:payer:{lab.NEMO}:{lw.uid(n, 0xfa6)}@sha256:"
                                    + "0" * 64}})
        from infrx.evaluation import checkpoints
        self.sub = run(checkpoints.subscribe(self.ledger, lab.store, {
            "subscription_id": lw.uid(n, 0x5b6), "provider_org_id": lab.NEMO,
            "external_run_ref": self.external, "dataset_ref": dataset,
            "harness_ref": lab.harness_ref(1), "evaluator_ref": lw.EVALUATOR(lab.NEMO),
            "evaluator": lw.SPEC, "seed": 7, "max_cases": 20,
            "run_limit": {"unit": "CREDIT", "value": "100.00000000"},
            "limit": {"unit": "CREDIT", "value": "1000.00000000"}, "max_active": 5,
            "policy": "latest_only"}, access=lab.access, user_id=lab.DEV))

    def event(self, k: int) -> tuple[bytes, str, str]:
        """A signed event for a well-formed checkpoint `k`: (body, signature, id)."""
        from infrx.evaluation import checkpoints
        from tests.b.checkpoints.world import safetensors
        data = safetensors(self.n * 100 + k)
        uri = f"mem://e6l-{self.n}-{k}"
        self.registry.blobs[uri] = data
        checkpoint_id = lw.uid(self.n * 100 + k, 0xc3e)
        body = json.dumps({
            "schema": checkpoints.EVENT_SCHEMA, "provider_org_id": self.lab.NEMO,
            "key_id": KEY_ID, "checkpoint_id": checkpoint_id,
            "external_run_ref": self.external, "step": k,
            "artifact": {"uri": uri, "digest": "sha256:" + hashlib.sha256(data).hexdigest()},
            "issued_at": self.issued}).encode()
        return body, checkpoints.sign(body, self.secret), checkpoint_id

    def receive(self, body: bytes, signature: str) -> dict:
        from infrx.evaluation import checkpoints
        return run(checkpoints.receive(body, signature, keys=self.keys, ledger=self.ledger,
                                       store=self.lab.store))

    def deliver(self, checkpoint_id: str) -> dict:
        """The `checkpoint_received` handler, as the checkpoints worker runs it."""
        from infrx.evaluation import checkpoints
        return run(checkpoints.on_checkpoint(
            checkpoint_id, provider_org_id=self.lab.NEMO, ledger=self.ledger,
            store=self.lab.store, registries={"mem": self.registry.fetch},
            deployer=self.deployer, access=self.lab.access))

    def counts(self, checkpoint_id: str, run_id: str) -> tuple[int, int, int, int]:
        """(receipts, checkpoint_received events, D7 runs, eval_run events)."""
        lab = self.lab
        return tuple(lab.sql(sql, key)[0][0] for sql, key in (
            ("select count(*) from infrx.lab_checkpoint_receipts where checkpoint_id = %s",
             checkpoint_id),
            ("select count(*) from infrx.lab_outbox where kind = 'checkpoint_received' "
             "and payload->>'checkpoint_id' = %s", checkpoint_id),
            ("select count(*) from infrx.lab_eval_runs where run_id = %s", run_id),
            ("select count(*) from infrx.lab_outbox where kind = 'eval_run' "
             "and payload->>'run_id' = %s", run_id)))


def test_j08_a_checkpoint_delivered_twice_is_one_receipt_and_one_run(lab, workdir):
    """The same signed event received twice is one receipt and ONE `checkpoint_received`
    event; the handler delivered twice queues ONE run (one `eval_run` event) and the
    checkpoint ends `evaluated`; that run, worked, scores every case once."""
    from infrx.evaluation import checkpoints, runner
    world = Checkpoints(lab, 80)
    body, signature, checkpoint = world.event(1)
    first, again = world.receive(body, signature), world.receive(body, signature)
    assert first == again and first["state"] == "received"
    decided, redelivered = world.deliver(checkpoint), world.deliver(checkpoint)
    run_id = checkpoints.run_id_of(world.sub.subscription_id, checkpoint)
    assert decided == redelivered == {world.sub.subscription_id: {
        "state": "queued", "reason": None, "run_id": run_id}}
    assert world.counts(checkpoint, run_id) == (1, 1, 1, 1)
    assert lab.sql("select state from infrx.lab_checkpoint_receipts where checkpoint_id = %s",
                   checkpoint) == [("evaluated",)]
    frozen = run(runner.resume(lab.store, run_id, evaluator=lw.SPEC, provider_org_id=lab.NEMO))
    with lw.endpoint("checkpoint") as (wallet, http):
        report = run(lab.runner(http).run(frozen))
        again = run(lab.runner(http).run(frozen))
    assert report["cases"] == {"done": 20} and len(wallet.calls) == 20
    assert again["cases"] == {"done": 20} and len(wallet.calls) == 20
    assert sum(b["score"] for b in lab.results(run_id).values()) == 20.0
    lw.save(workdir, "checkpoint.json", {"checkpoint_id": checkpoint, "run_id": run_id,
                                         "receipt": first, "decision": decided,
                                         "deployer": world.deployer.calls})


def test_j08_a_crash_before_the_decision_is_one_run_on_redelivery(lab):
    """The handler dies after B1 created the run and before the decision is written: the
    redelivery decides the same run id, and D7 still holds one run and one `eval_run` event."""
    from infrx.evaluation import checkpoints
    from tests.b.checkpoints.world import Crash
    world = Checkpoints(lab, 81)
    body, signature, checkpoint = world.event(1)
    world.receive(body, signature)
    world.ledger.crash_on_decide = 1
    with pytest.raises(Crash):
        world.deliver(checkpoint)
    run_id = checkpoints.run_id_of(world.sub.subscription_id, checkpoint)
    assert world.counts(checkpoint, run_id) == (1, 1, 1, 1)
    assert world.deliver(checkpoint)[world.sub.subscription_id]["run_id"] == run_id
    assert world.counts(checkpoint, run_id) == (1, 1, 1, 1)
