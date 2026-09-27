#!/usr/bin/env python3
"""P3 (13-lab-improvement-handoffs §P3; TRAIN-RECOVER, PIPELINE-LINEAGE): the manual training
bundle, the automatic connector protocol (hidden until P-11) with ambiguous-submit
reconciliation, and checkpoint import through B3 with a held-out evaluation before a candidate
is eligible - in the fake world of `world.py` (the protocol server in process).
`test_training_services.py` reruns the core on the real D7 store and on the protocol server
over TCP (p3's port 57531). Every case is named by a mutant in `mutants.py`.

    uv run --frozen pytest -q tests/p/training/test_training.py
"""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.datasets import versions
from infrx.media.store import InMemoryObjectStore
from infrx.pipelines import training as p3

from ...n.imports.world import GRANT_ID, NEMO, grant_ref, run
from ...n.versions.test_versions import derive, imported, uid
from ..annotations.world import DEV, GRANT_2, GRANT_3, NOW, VIEWER, members, rows
from .world import (PAYER, CheckpointStore, FakeEvaluations, FakeRunLedger, client,
                    descriptor, digest, protocol_app)

CONFIG = {"objective": "sft", "adaptation": "lora", "base_model": "marlin-2b",
          "hyperparameters": {"lr": 0.0002, "epochs": 2}, "environment": {"image": "trainer:1"}}
EXT = uid(1, 0xe0)


class World:
    def __init__(self, grant: str = GRANT_ID, items=None) -> None:
        self.store, self.objects = CheckpointStore(), InMemoryObjectStore()
        self.store.add_grant()
        self.store.add_grant(GRANT_2)
        self.store.add_grant(GRANT_3, purposes=("provider_sharing",))
        self.ref = imported(self.store, self.objects,
                            items or rows(9, splits=("train", "holdout", "validation")), 1,
                            grant=grant, split=True)
        self.ledger, self.evals, self.access = FakeRunLedger(), FakeEvaluations(), members()

    def manifest(self, ref=None):
        return run(self.store.resolve(ref or self.ref, provider_org_id=NEMO))

    def export(self, ref=None, n=1):
        return run(versions.export(self.store, self.objects, provider_org_id=NEMO,
                                   dataset_ref=ref or self.ref, export_id=uid(n, 0xee),
                                   now=NOW, ttl_s=3600))

    def prepare(self, ext=EXT, ref=None, connector=p3.MANUAL, **over):
        ref = ref or self.ref
        return run(p3.prepare(self.store, self.objects, self.ledger, provider_org_id=NEMO,
                              actor="dev@nemo", external_run_id=ext, dataset_ref=ref,
                              config=over.pop("config", CONFIG),
                              export=over.pop("export", None) or self.export(ref),
                              payer_ref=PAYER, limit="25.00000000", connector=connector))

    def submit(self, connector=None, ext=EXT, user=DEV, **kw):
        return run(p3.submit(self.store, self.objects, self.ledger,
                             connector or p3.ManualConnector(), self.access,
                             provider_org_id=NEMO, user_id=user, external_run_id=ext, **kw))

    def run_state(self, ext=EXT):
        return run(self.ledger.get(ext, provider_org_id=NEMO))

    def reservation(self, ext=EXT):
        return self.ledger.reservations.get((NEMO, f"submit:{ext}"), {})

    def put(self, data: bytes, name="ckpt-1", ext=EXT) -> str:
        key = f"lab/{NEMO}/training/{ext}/checkpoints/{name}"
        self.objects.seed(key, data)
        return key

    def checkpoint(self, n=1, key=None, data=None, ext=EXT, declared=None):
        data = descriptor() if data is None else data
        key = key or self.put(data, f"ckpt-{n}", ext)
        return run(p3.import_checkpoint(
            self.store, self.objects, self.ledger, self.evals, provider_org_id=NEMO,
            external_run_id=ext, checkpoint_id=uid(n, 0xc9), artifact_key=key,
            artifact_digest=declared or digest(data)))

    def approve(self, n=1, user=DEV, ext=EXT):
        return run(p3.approve(self.objects, self.ledger, self.evals, self.access,
                              provider_org_id=NEMO, user_id=user, external_run_id=ext,
                              checkpoint_id=uid(n, 0xc9)))


def automatic(w: World, mode="ok"):
    app = protocol_app(mode)
    return app, p3.HttpConnector(client(app), "protocol-test")


ON = frozenset({p3.MANUAL, "protocol-test"})


# --- P3.a: the manual bundle ---------------------------------------------------------------
def test_p3_a_bundle_names_train_and_dev_and_pins_the_holdout_without_shipping_it() -> None:
    """Oracle (PIPELINE-LINEAGE, DATA-SPLIT): the bundle lists the train and dev ids, the
    config and environment, the export and the external run published in D7 (training,
    PROVIDER_USD, named payer); the holdout is only a size and digest; a replay is the same
    bytes and the ledger run is `prepared`."""
    w = World()
    m = w.manifest()
    bundle = w.prepare()
    assert (bundle["train"], bundle["dev"]) == (list(m.splits.train), list(m.splits.validation))
    assert not any(h in json.dumps(bundle) for h in m.splits.holdout)
    assert bundle["holdout"] == {"size": 3, "sha256": hashlib.sha256(
        records.canonical(sorted(m.splits.holdout))).hexdigest()}
    assert bundle["config"] == CONFIG and bundle["omitted"] == []
    assert bundle["export"]["sha256"] == w.export()["content_sha256"]
    record = run(w.store.resolve(bundle["external_run_ref"], provider_org_id=NEMO))
    assert (record.purpose, record.state, record.budget.limit.unit, record.budget.payer_ref) == (
        "training", "prepared", "PROVIDER_USD", PAYER)
    assert w.prepare() == bundle and w.run_state()["state"] == "prepared"
    with pytest.raises(errors.Conflict):                   # the bundle is write-once
        w.prepare(config={**CONFIG, "base_model": "other"})


def test_p3_a_bundle_refuses_bad_config_other_exports_and_untrainable_data() -> None:
    """Oracle: LoRA is an adaptation, not an objective; an unknown config key, an export of
    another dataset, or a dataset with no train sample under a training grant is refused;
    samples whose training grant is gone are left out and listed."""
    w = World()
    for config in ({**CONFIG, "objective": "lora"}, {**CONFIG, "adaptation": "rlhf"},
                   {**CONFIG, "extra": 1}):
        with pytest.raises(errors.InvalidRequest):
            w.prepare(config=config)
    b = imported(w.store, w.objects, rows(4, "b", splits=("train", "validation")), 2,
                 grant=GRANT_2, split=True)
    with pytest.raises(errors.InvalidRequest):
        w.prepare(export=w.export(b, n=2))
    both = derive(w.store, w.objects, add=[w.ref, b]).dataset_ref
    w.store.revoke(grant_ref(GRANT_2))
    lost = {s.sample_id for s in w.manifest(b).samples}
    bundle = w.prepare(ext=uid(2, 0xe0), ref=both, export=w.export(both, n=3))
    assert set(bundle["omitted"]) == lost - set(w.manifest(both).splits.holdout)
    assert not lost & set(bundle["train"] + bundle["dev"])
    sharing = World(grant=GRANT_3)
    with pytest.raises(errors.Forbidden):
        sharing.prepare(export={"format": "x", "export_id": "x", "sha256": "0",
                                "dataset_ref": sharing.ref})


# --- P3.b: submission, reconciliation, recovery --------------------------------------------
def test_p3_the_manual_workflow_round_trips_to_an_eligible_candidate() -> None:
    """Oracle (TRAIN-RECOVER acceptance): bundle -> the provider trains -> a checkpoint is
    imported, validated and queued on the frozen holdout -> eligible only after that
    evaluation succeeds; a viewer can neither submit nor approve; one reservation."""
    w = World()
    bundle = w.prepare()
    with pytest.raises(errors.Forbidden):
        w.submit(user=VIEWER)
    assert w.submit()["state"] == "submitted" and w.reservation().get("state") == "held"
    got = w.checkpoint()
    assert got["state"] == "validated" and w.evals.calls == 1
    queued = w.evals.runs[uid(1, 0xc9)]
    assert (queued["split"], queued["holdout_sha256"], queued["dataset_ref"]) == (
        "holdout", bundle["holdout"]["sha256"], w.ref)
    with pytest.raises(errors.StateConflict):
        w.approve()
    queued["state"] = "succeeded"
    with pytest.raises(errors.Forbidden):
        w.approve(user=VIEWER)
    eligible = w.approve()
    assert eligible["evaluation"] == queued["run_ref"] and eligible["approved_by"] == DEV
    assert w.approve() == eligible and w.submit()["state"] == "submitted"


def test_p3_automatic_connectors_stay_hidden_until_p11() -> None:
    """Oracle: with the default advertisement only the manual connector submits; a run
    prepared for one connector is never sent through another; nothing reaches the server."""
    w = World()
    app, http = automatic(w)
    w.prepare(connector="protocol-test")
    with pytest.raises(errors.Forbidden):
        w.submit(http)
    with pytest.raises(errors.Forbidden):
        w.submit(p3.ManualConnector(), advertised=ON)
    assert app.state.posts == 0 and w.run_state()["state"] == "prepared"
    assert w.reservation() == {}


def test_p3_an_ambiguous_submit_is_reconciled_without_a_second_paid_job() -> None:
    """Oracle (TRAIN-RECOVER): the server accepts, then answers 503: the run is `ambiguous`
    with its reservation held; a resume looks the key up and finds the one job - no second
    POST, one job, one reservation."""
    w = World()
    app, http = automatic(w, "accept_503")
    w.prepare(connector="protocol-test")
    assert w.submit(http, advertised=ON)["state"] == "ambiguous"
    assert w.reservation().get("state") == "held"
    resumed = w.submit(http, advertised=ON)
    assert (resumed["state"], resumed.get("job_id")) == ("submitted", "job-1")
    assert app.state.posts == 1 and len(app.state.jobs) == 1
    assert w.submit(http, advertised=ON) == resumed and app.state.posts == 1


def test_p3_a_crash_mid_submit_never_resubmits() -> None:
    """Oracle (TRAIN-RECOVER): a worker dies after `submitting` and before the connector
    answers; a resume turns it `ambiguous` and only looks it up - the server never saw it, so
    it stays ambiguous (unknown, reservation held) and nothing is ever posted."""

    class Dies(p3.HttpConnector):
        async def submit(self, bundle, *, key):
            raise KeyboardInterrupt                      # a process dying, not an error

    w = World()
    app, http = automatic(w)
    w.prepare(connector="protocol-test")
    with pytest.raises(KeyboardInterrupt):
        w.submit(Dies(http.client, "protocol-test"), advertised=ON)
    assert w.run_state()["state"] == "submitting"
    for _ in range(2):
        assert w.submit(http, advertised=ON)["state"] == "ambiguous"
    assert app.state.posts == 0 and w.reservation().get("state") == "held"
    with pytest.raises(errors.StateConflict):
        run(p3.cancel(w.ledger, http, provider_org_id=NEMO, external_run_id=EXT))


def test_p3_a_refused_submit_fails_and_releases_its_budget() -> None:
    """Oracle: a definite 4xx is `failed` with the reservation released; a later submit of
    the failed run posts nothing."""
    w = World()
    app, http = automatic(w, "reject")
    w.prepare(connector="protocol-test")
    assert w.submit(http, advertised=ON)["state"] == "failed"
    assert w.reservation().get("state") == "released"
    assert w.submit(http, advertised=ON)["state"] == "failed" and app.state.posts == 1


def test_p3_revoked_consent_stops_a_prepared_submission() -> None:
    """Oracle (DATA-RIGHTS): a grant narrowed to provider_sharing after the bundle was made
    refuses the submission at the training gate: no reservation, no post, still `prepared`."""
    w = World()
    app, http = automatic(w)
    w.prepare(connector="protocol-test")
    w.store.grants[grant_ref()]["purposes"] = {"provider_sharing"}
    with pytest.raises(errors.Forbidden):
        w.submit(http, advertised=ON)
    assert (w.run_state()["state"], w.reservation(), app.state.posts) == ("prepared", {}, 0)


def test_p3_reported_cost_is_settled_exactly_or_unknown() -> None:
    """Oracle (PIPELINE-BUDGET): a completed job settles the provider-reported PROVIDER_USD
    cost; a cost the provider did not report exactly is unknown (None), never estimated;
    training metrics are recorded nowhere as a promotion; cancelling a submitted job keeps
    its reported cost, a prepared one needs no connector, and one the provider refuses to
    cancel stays submitted."""
    w = World()
    app, http = automatic(w)
    for n, cost in ((1, "12.50000000"), (2, "about 12"), (3, None)):
        ext = uid(n, 0xe0)
        w.prepare(ext=ext, connector="protocol-test")
        w.submit(http, ext=ext, advertised=ON)
        assert run(p3.poll(w.ledger, http, provider_org_id=NEMO,
                           external_run_id=ext))["state"] == "submitted"
        app.state.jobs[f"job-{n}"].update(state="completed", cost=cost,
                                          metrics={"loss": 0.01}, promote=True)
        done = run(p3.poll(w.ledger, http, provider_org_id=NEMO, external_run_id=ext))
        assert done["state"] == "completed"
        assert done["cost"] == w.reservation(ext).get("cost") == ("12.50000000" if n == 1 else None)
    assert not any(k[1].startswith("eligible:") for k in w.ledger.notes)
    ext = uid(4, 0xe0)
    w.prepare(ext=ext, connector="protocol-test")
    w.submit(http, ext=ext, advertised=ON)
    app.state.jobs["job-4"]["cost"] = "3.00000000"
    gone = run(p3.cancel(w.ledger, http, provider_org_id=NEMO, external_run_id=ext))
    assert (gone["state"], gone["cost"], w.reservation(ext).get("state")) == (
        "cancelled", "3.00000000", "settled")
    ext = uid(5, 0xe0)
    w.prepare(ext=ext, connector="protocol-test")
    assert run(p3.cancel(w.ledger, http, provider_org_id=NEMO,
                         external_run_id=ext))["state"] == "cancelled"
    ext = uid(6, 0xe0)
    w.prepare(ext=ext, connector="protocol-test")
    w.submit(http, ext=ext, advertised=ON)
    app.state.jobs["job-5"]["uncancellable"] = True
    with pytest.raises(httpx.HTTPStatusError):
        run(p3.cancel(w.ledger, http, provider_org_id=NEMO, external_run_id=ext))
    assert w.run_state(ext)["state"] == "submitted" and app.state.posts == 5


# --- P3.c: checkpoints and eligibility -----------------------------------------------------
def test_p3_a_redelivered_checkpoint_is_one_receipt_and_one_evaluation() -> None:
    """Oracle (CHECKPOINT-IDEM via B3): the same delivery again is the first outcome and
    queues nothing; the same checkpoint id with other bytes is a conflict."""
    w = World()
    w.prepare()
    w.submit()
    first = w.checkpoint()
    assert w.checkpoint() == first and w.evals.calls == 1
    with pytest.raises(errors.IdempotencyConflict):
        w.checkpoint(data=descriptor("other-model"))


def test_p3_invalid_artifacts_are_rejected_and_never_evaluated() -> None:
    """Oracle (TRAIN-RECOVER): a missing artifact, bytes other than the declared digest, a
    descriptor for another base model or adaptation, or bytes that are no descriptor are
    `rejected` with their reason, never queued and never eligible (even if B3 evaluated it
    anyway); an artifact outside the
    run's prefix is refused outright."""
    w = World()
    bundle = w.prepare()
    w.submit()
    good = descriptor()
    missing = f"lab/{NEMO}/training/{EXT}/checkpoints/none"
    cases = [(1, missing, None, None, "missing_artifact"),
             (2, w.put(b"tampered", "c2"), good, digest(good), "digest_mismatch"),
             (3, None, descriptor("other-model"), None, "incompatible"),
             (4, None, descriptor(adaptation="full"), None, "incompatible"),
             (5, None, b"\x00weights", None, "incompatible"),
             (6, None, b"[1, 2]", None, "incompatible"),
             (7, None, json.dumps({"base_model": "marlin-2b", "adaptation": "lora"}).encode(),
              None, "incompatible")]
    for n, key, data, declared, reason in cases:
        got = w.checkpoint(n, key=key, data=data, declared=declared)
        assert (got["state"], got.get("reason")) == ("rejected", reason), (n, got)
        assert w.store.receipts[uid(n, 0xc9)]["state"] == "rejected"
    assert w.evals.calls == 0
    run(w.evals.evaluate(provider_org_id=NEMO, checkpoint_id=uid(2, 0xc9), dataset_ref=w.ref,
                         split="holdout", holdout_sha256=bundle["holdout"]["sha256"]))
    w.evals.runs[uid(2, 0xc9)]["state"] = "succeeded"        # B3 evaluated it on its own
    with pytest.raises(errors.StateConflict):
        w.approve(2)
    with pytest.raises(errors.InvalidRequest):
        w.checkpoint(9, key=f"lab/{NEMO}/training/{uid(7, 0xe0)}/x")


def test_p3_a_late_checkpoint_after_cancel_is_rejected() -> None:
    """Oracle (TRAIN-RECOVER): a cancelled run's late checkpoint, or one for a run never
    submitted, is recorded and rejected (`run_cancelled`, `run_prepared`), never evaluated."""
    w = World()
    w.prepare()
    w.prepare(ext=uid(2, 0xe0))
    w.submit()
    run(p3.cancel(w.ledger, p3.ManualConnector(), provider_org_id=NEMO, external_run_id=EXT))
    assert w.checkpoint().get("reason") == "run_cancelled"
    assert w.checkpoint(2, ext=uid(2, 0xe0)).get("reason") == "run_prepared"
    assert w.evals.calls == 0


def test_p3_only_the_frozen_holdout_evaluation_makes_a_candidate_eligible() -> None:
    """Oracle (TRAIN-RECOVER): an evaluation that failed, ran on another split or on another
    holdout, or a checkpoint of another run, never makes the candidate eligible."""
    w = World()
    w.prepare()
    w.prepare(ext=uid(2, 0xe0))
    w.submit()
    w.submit(ext=uid(2, 0xe0))
    w.checkpoint()
    result = w.evals.runs[uid(1, 0xc9)]
    for change in ({"state": "failed"}, {"state": "succeeded", "split": "validation"},
                   {"state": "succeeded", "holdout_sha256": "0" * 64},
                   {"state": "succeeded", "dataset_ref": w.ref + "x"}):
        saved = dict(result)
        result.update(change)
        with pytest.raises(errors.StateConflict):
            w.approve()
        result.clear()
        result.update(saved)
    result["state"] = "succeeded"
    with pytest.raises(errors.StateConflict):              # another run's checkpoint
        w.approve(ext=uid(2, 0xe0))
    assert w.approve()["checkpoint_id"] == uid(1, 0xc9)


def test_p3_a_checkpoint_outcome_lost_in_a_crash_keeps_the_receipts_state() -> None:
    """Oracle (TRAIN-RECOVER): a worker dies after moving the receipt and before noting the
    outcome; the redelivery takes the receipt's state - a validated one is queued (B3 keeps
    one evaluation), a rejected one stays rejected and is never evaluated."""

    class DiesOnce(FakeRunLedger):
        async def note(self, key, body, *, provider_org_id):
            if key.startswith("checkpoint:") and not getattr(self, "died", False):
                self.died = True
                raise KeyboardInterrupt
            return await super().note(key, body, provider_org_id=provider_org_id)

    for data, declared, state in ((descriptor(), None, "validated"),
                                  (b"tampered", digest(descriptor()), "rejected")):
        w = World()
        w.ledger = DiesOnce()
        w.prepare()
        w.submit()
        with pytest.raises(KeyboardInterrupt):
            w.checkpoint(data=data, declared=declared)
        got = w.checkpoint(data=data, declared=declared)
        assert got["state"] == state and w.store.receipts[uid(1, 0xc9)]["state"] == state
        assert len(w.evals.runs) == (state == "validated")
