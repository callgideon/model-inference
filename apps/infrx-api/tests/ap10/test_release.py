#!/usr/bin/env python3
"""AP-10 10e: `infrx.lab.improve.release` - reviewed labels through the one supported
external-training path (P3's manual bundle) to an eligible checkpoint, a new serving revision
ONLY through AP-04's import (the checkpoint's identity preserved), and the release evidence
record. P3's fake world (`tests/p/training`: D7 + receipts, D8 ledger, B3 evaluations, P1's
reviewed-label export) and a recording stand-in for AP-04's `LabArtifacts` (its real
`ImportRequest` body, operation and artifact rows).

    uv run --frozen pytest -q tests/ap10/test_release.py
"""
from __future__ import annotations

import json
import types
from datetime import UTC, datetime

import pytest

from infrx.contracts import api, errors
from infrx.lab.artifacts.imports import ImportRequest
from infrx.lab.artifacts.projects import Unsupported
from infrx.lab.improve import release
from infrx.pipelines import training as p3

from ..n.imports.world import NEMO, run
from ..n.versions.test_versions import uid
from ..p.annotations.world import NOW
from ..p.training.test_training import CONFIG, EXT, World
from ..p.training.world import PAYER, digest

COMMIT = "fd111fca4fc7897876fb0d7e9df22ca5ac8ab965"
FILES = ("adapter_config.json", "adapter_model.safetensors")
PROJECT = "pr000000-0000-4000-8000-0000000000p1"
CKPT = uid(1, 0xc9)
ACTOR = api.Actor(audience="session", user_id="dev@nemo", provider_org_id=NEMO)


def entry(path: str) -> dict:
    return {"relative_path": path, "bytes": 3, "sha256": "sha256:" + "a" * 64,
            "media_type": "application/octet-stream"}


class Artifacts:
    """AP-04's `LabArtifacts` as release.py calls it: `imports.start` (replay by key),
    `ops.get`, `projects.store.get`; the worker's verification is `verify`."""

    def __init__(self) -> None:
        self.started: dict[str, tuple[api.Actor, ImportRequest]] = {}
        self.docs: dict[str, api.OperationDoc] = {}
        self.rows: dict[tuple[str, str], object] = {}
        self.imports = types.SimpleNamespace(start=self._start)
        self.ops = types.SimpleNamespace(get=self._get)
        self.projects = types.SimpleNamespace(store=types.SimpleNamespace(get=self._row))

    async def _start(self, actor, body, key):
        self.started.setdefault(key, (actor, body))
        doc = self.docs.setdefault(key, api.OperationDoc(
            operation_id=f"op-{key}", kind="artifact.import", state="queued",
            resource_id=f"art-{key}", created_at="t", updated_at="t"))
        return types.SimpleNamespace(doc=doc)

    async def _get(self, operation_id):
        return next(types.SimpleNamespace(doc=d) for d in self.docs.values()
                    if d.operation_id == operation_id)

    async def _row(self, model, provider, artifact_id):
        return self.rows.get((provider, artifact_id))

    def verify(self, key: str, *, files=FILES, commit=COMMIT, provider=NEMO, state="succeeded"):
        doc = self.docs[key]
        self.docs[key] = doc.model_copy(update={"state": state})
        _, body = self.started[key]
        self.rows[(provider, doc.resource_id)] = types.SimpleNamespace(
            artifact_id=doc.resource_id, source="import", source_repo=body.source.repo,
            source_commit=commit, files=tuple(types.SimpleNamespace(relative_path=f)
                                              for f in files),
            manifest_sha256=body.digest)


def descriptor(files=FILES) -> bytes:
    return json.dumps({"format": p3.DESCRIPTOR, "base_model": "marlin-2b",
                       "adaptation": "lora", "files": list(files)}).encode()


class Rig:
    def __init__(self) -> None:
        self.w = World()
        self.a = Artifacts()
        store = self.w.store

        async def receipt(checkpoint_id, *, provider_org_id):       # D7 0053's read
            row = store.receipts.get(checkpoint_id)
            return None if row is None or row["provider_org_id"] != provider_org_id else (
                row["external_run_ref"], row["artifact_digest"])
        store.checkpoint_receipt = receipt
        self.label = self.w.label_export()

    def prepare(self, trainer=release.MANUAL, config=CONFIG, export_id=None) -> dict:
        return run(release.export_for_training(
            self.w.store, self.w.objects, self.w.ledger, trainer=trainer, provider_org_id=NEMO,
            actor="dev@nemo", external_run_id=EXT, dataset_ref=self.w.ref, config=config,
            label_export_id=export_id or self.label["export_id"], payer_ref=PAYER,
            limit="25.00000000", now=NOW))

    def eligible(self, files=FILES) -> str:
        self.prepare()
        self.w.submit()
        data = descriptor(files)
        key = self.w.put(data, "ckpt-1")
        self.w.checkpoint(1, key=key, data=data)
        self.w.evals.runs[CKPT]["state"] = "succeeded"
        self.w.approve()
        return key

    def register(self, key="imp-1", files=FILES, artifact_key=None, actor=ACTOR,
                 checkpoint=CKPT):
        return run(release.register_candidate(
            self.w.store, self.w.objects, self.w.ledger, self.a, actor, key,
            provider_org_id=NEMO, external_run_id=EXT, checkpoint_id=checkpoint,
            artifact_key=artifact_key or self.ckpt_key, project_id=PROJECT,
            source={"host": "huggingface.co", "repo": "nemo/marlin-2b-sop-lora",
                    "commit": COMMIT},
            files=[entry(f) for f in files]))

    def evidence(self, at=datetime(2026, 10, 2, tzinfo=UTC)):
        return run(release.release_evidence(self.w.objects, self.w.ledger, self.a,
                                            provider_org_id=NEMO, external_run_id=EXT,
                                            checkpoint_id=CKPT, now=at))


def test_ap10_release_an_unsupported_trainer_is_an_explicit_refusal():
    """Oracle: a trainer other than P3's manual bundle (an automatic connector stays hidden
    until P-11) is `unsupported` naming the field, and nothing is bundled; a training
    objective that is not the reviewed export's adapter, and an export id that is not one,
    are refused before anything is written. The supported trainer bundles exactly P1's
    reviewed-label export."""
    r = Rig()
    for trainer in ("protocol-test", "hosted-tinker"):
        with pytest.raises(Unsupported) as refused:
            r.prepare(trainer=trainer)
        assert [(e.field, e.code) for e in refused.value.reasons] == [("trainer", "unsupported")]
    with pytest.raises(Unsupported) as refused:
        r.prepare(config={**CONFIG, "objective": "preference"})
    assert [(e.field, e.code) for e in refused.value.reasons] == \
        [("config.objective", "adapter_mismatch")]
    with pytest.raises(errors.InvalidRequest):
        r.prepare(export_id="../../x")
    assert run(r.w.ledger.get(EXT, provider_org_id=NEMO)) is None, "nothing bundled"
    bundle = r.prepare()
    assert bundle["export"]["format"] == "infrx.label_export.1"
    assert bundle["export"]["export_id"] == r.label["export_id"]


def test_ap10_release_a_candidate_is_registered_only_through_ap04s_import():
    """Oracle: only an approved (eligible) checkpoint of this run starts AP-04's import, as the
    actor's own workspace, with exactly the files the received descriptor declares (the
    descriptor's bytes must be the receipt's digest); a replay is the same operation; another
    import for the same checkpoint is a 409. Anything else - not yet eligible, another
    workspace, other files, a descriptor that is not the received one - starts nothing."""
    r = Rig()
    r.ckpt_key = r.w.put(descriptor(), "pre")
    with pytest.raises(errors.StateConflict):
        r.register()                                    # no bundle / not eligible yet
    r.ckpt_key = r.eligible()
    other = ACTOR.model_copy(update={"provider_org_id": uid(2, 0xb0)})
    with pytest.raises(errors.Forbidden):
        r.register(actor=other)
    with pytest.raises(Unsupported) as refused:
        r.register(files=("adapter_model.safetensors",))
    assert [(e.field, e.code) for e in refused.value.reasons] == [("files", "not_the_checkpoint")]
    forged = r.w.put(descriptor(("model.safetensors",)), "forged")
    with pytest.raises(errors.Conflict):
        r.register(artifact_key=forged)
    outside = f"lab/{NEMO}/elsewhere/descriptor"         # the same bytes, not the run's
    r.w.objects.seed(outside, descriptor())
    with pytest.raises(errors.Conflict):
        r.register(artifact_key=outside)
    assert r.a.started == {}, "nothing reached AP-04"
    op = r.register()
    actor, body = r.a.started["imp-1"]
    assert actor == ACTOR and isinstance(body, ImportRequest)
    assert sorted(f.relative_path for f in body.files) == sorted(FILES)
    assert (body.project_id, body.source.commit) == (PROJECT, COMMIT)
    assert r.register().doc.operation_id == op.doc.operation_id
    with pytest.raises(errors.IdempotencyConflict):
        r.register(key="imp-2")


def test_ap10_release_evidence_pins_the_lineage_once_the_import_is_verified():
    """Oracle: the evidence record is written once AP-04's operation succeeded and its
    verified artifact is exactly the registered import (source repo, commit, files): it pins
    the dataset, the reviewed export and its label methods, the frozen holdout, the config,
    the checkpoint digest, the holdout evaluation, the approver and the artifact - and stays
    `qualification: pending` (a new revision is qualified by AP-05 readiness and the 10d
    report, never here). A queued import, or an artifact that is not the registration,
    writes nothing."""
    r = Rig()
    r.ckpt_key = r.eligible()
    with pytest.raises(errors.StateConflict):
        r.evidence()                                    # nothing registered
    r.register()
    with pytest.raises(errors.StateConflict):
        r.evidence()                                    # AP-04 has not verified it
    r.a.verify("imp-1", commit="0" * 40)
    with pytest.raises(errors.Conflict):
        r.evidence()                                    # not the registered identity
    r.a.verify("imp-1", files=FILES[:1])
    with pytest.raises(errors.Conflict):
        r.evidence()
    r.a.verify("imp-1")
    got = r.evidence()
    bundle = run(p3._bundle(r.w.objects, NEMO, EXT))
    assert got["format"] == release.EVIDENCE
    assert (got["dataset_ref"], got["export"], got["holdout"]) == \
        (r.w.ref, bundle["export"], bundle["holdout"])
    # P1's fixture labels are model-made and accepted on review: recorded as `synthetic`,
    # never as human ground truth
    assert got["label_methods"] == {"synthetic": len(r.label["lineage"])} != {}
    assert got["checkpoint"] == {"checkpoint_id": CKPT, "artifact_digest": digest(descriptor())}
    assert got["evaluation"] == r.w.evals.runs[CKPT]["run_ref"]
    assert got["artifact"]["artifact_id"] == "art-imp-1"
    assert got["qualification"]["state"] == "pending"
    assert r.evidence(at=datetime(2027, 1, 1, tzinfo=UTC)) == got, "written once"
