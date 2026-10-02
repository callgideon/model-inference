"""AP-10 10e: reviewed labels -> the supported external training -> a candidate revision only
through AP-04's import -> the release evidence record.

Everything here composes existing parts; it replaces nothing:

- **export** (`export_for_training`): the one supported trainer is P3's manual bundle
  (`training.ADVERTISED`; an automatic connector stays hidden until P-11). Any other trainer
  is an explicit `unsupported` refusal (AP-04's 422 `Unsupported`, field `trainer`). The
  bundle carries P1's export of REVIEWED labels (`infrx.label_export.1`: accepted labels
  only, train-only, the holdout excluded), and the config's objective must be that export's
  adapter (`sft.1` -> `sft`, `preference.1` -> `preference`). P3's `prepare` does the rest
  (rights now, the frozen holdout pin, write-once).
- **registration** (`register_candidate`): the provider trained on its own compute; the
  checkpoint came back through P3 (`import_checkpoint`, D7's receipt) and was approved on the
  frozen holdout (`approve`). Only such an eligible checkpoint, of this run and the actor's
  own workspace, starts AP-04's import (`LabArtifacts.imports.start`, the pinned-commit
  source and verification) - never a registry write, never a serving revision by itself. The
  import declares exactly the files the received descriptor names, and the descriptor's bytes
  must be D7's receipt digest. One import per checkpoint (D8's write-once note).
- **evidence** (`release_evidence`): once AP-04's operation succeeded and its verified
  artifact is exactly the registered import (repo, commit, files), one write-once record pins
  the lineage: dataset, reviewed export and its label methods, frozen holdout, config,
  checkpoint digest, holdout evaluation, approver, artifact. It says `qualification:
  pending`: a new revision is qualified by AP-05 readiness and the 10d SOP report, not here.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from typing import Any

from ...contracts import api, errors
from ...contracts.ids import UUID_RE
from ...contracts.lab import records as lab
from ...datasets.imports import write_once
from ...pipelines import training
from ..artifacts.imports import ImportRequest
from ..artifacts.projects import Unsupported

MANUAL = training.MANUAL
SUPPORTED = training.ADVERTISED
LABELS = "infrx.label_export.1"
OBJECTIVE = {"sft.1": "sft", "preference.1": "preference"}
EVIDENCE = "infrx.release_evidence.1"


def _refuse(field: str, code: str, message: str) -> Unsupported:
    return Unsupported([api.FieldError(field=field, code=code, message=message)])


async def export_for_training(store, objects, ledger, *, trainer: str, provider_org_id: str,
                              actor: str, external_run_id: str, dataset_ref: str, config: dict,
                              label_export_id: str, payer_ref: str, limit: str,
                              now: datetime) -> dict:
    """P3's bundle of P1's reviewed-label export, for the one supported trainer."""
    if trainer not in SUPPORTED:
        raise _refuse("trainer", "unsupported", f"supported trainers: {sorted(SUPPORTED)}")
    if not UUID_RE.fullmatch(label_export_id):
        raise errors.InvalidRequest("a label export id is a lowercase UUID")
    stored = await objects.get(f"lab/{provider_org_id}/label-exports/{label_export_id}/export.json")
    if stored is None:
        raise errors.NotFound(f"no reviewed-label export {label_export_id}")
    if OBJECTIVE.get(json.loads(stored)["adapter"]) != config.get("objective"):
        raise _refuse("config.objective", "adapter_mismatch",
                      "the objective is not the reviewed export's adapter")
    return await training.prepare(
        store, objects, ledger, provider_org_id=provider_org_id, actor=actor,
        external_run_id=external_run_id, dataset_ref=dataset_ref, config=config,
        export={"format": LABELS, "export_id": label_export_id}, payer_ref=payer_ref,
        limit=limit, now=now, connector=MANUAL)


async def _eligible(objects, ledger, provider: str, external_run_id: str,
                    checkpoint_id: str) -> tuple[dict, dict]:
    bundle = json.loads(await objects.get(training._key(provider, external_run_id)) or b"null")
    eligible = await ledger.noted(f"eligible:{checkpoint_id}", provider_org_id=provider)
    if bundle is None or eligible is None or \
            eligible["external_run_ref"] != bundle["external_run_ref"]:
        raise errors.StateConflict("not an approved checkpoint of this run")
    return bundle, eligible


async def register_candidate(store, objects, ledger, artifacts, actor: api.Actor, key: str, *,
                             provider_org_id: str, external_run_id: str, checkpoint_id: str,
                             artifact_key: str, project_id: str, source: dict,
                             files: list[dict], secret_ref: str | None = None) -> Any:
    """AP-04's import of an eligible checkpoint's weights; its operation (202's body)."""
    await _eligible(objects, ledger, provider_org_id, external_run_id, checkpoint_id)
    if actor.provider_org_id != provider_org_id:
        raise errors.Forbidden("a candidate is registered in its run's own workspace")
    receipt = await store.checkpoint_receipt(checkpoint_id, provider_org_id=provider_org_id)
    data = await objects.get(artifact_key) \
        if artifact_key.startswith(f"lab/{provider_org_id}/training/{external_run_id}/") else None
    if receipt is None or data is None or \
            "sha256:" + hashlib.sha256(data).hexdigest() != receipt[1]:
        raise errors.Conflict("the descriptor is not the received checkpoint")
    body = ImportRequest.model_validate({"project_id": project_id, "source": source,
                                         "files": files, "secret_ref": secret_ref})
    declared = sorted(json.loads(data).get("files", ()))
    if sorted(f.relative_path for f in body.files) != declared:
        raise _refuse("files", "not_the_checkpoint",
                      "the import declares other files than the checkpoint")
    op = await artifacts.imports.start(actor, body, key)
    await ledger.note(f"candidate:{checkpoint_id}", {
        "operation_id": op.doc.operation_id, "artifact_id": op.doc.resource_id,
        "artifact_digest": receipt[1], "repo": body.source.repo, "commit": body.source.commit,
        "files": declared, "manifest_sha256": body.digest}, provider_org_id=provider_org_id)
    return op


def _evidence_key(provider: str, external_run_id: str, checkpoint_id: str) -> str:
    return f"lab/{provider}/training/{external_run_id}/release-evidence/{checkpoint_id}.json"


async def release_evidence(objects, ledger, artifacts, *, provider_org_id: str,
                           external_run_id: str, checkpoint_id: str, now: datetime) -> dict:
    """The write-once release evidence record of a verified candidate import."""
    key = _evidence_key(provider_org_id, external_run_id, checkpoint_id)
    stored = await objects.get(key)
    if stored is not None:
        return json.loads(stored)
    bundle, eligible = await _eligible(objects, ledger, provider_org_id, external_run_id,
                                       checkpoint_id)
    candidate = await ledger.noted(f"candidate:{checkpoint_id}", provider_org_id=provider_org_id)
    if candidate is None:
        raise errors.StateConflict("no candidate import was registered for this checkpoint")
    op = await artifacts.ops.get(candidate["operation_id"])
    if op.doc.state != "succeeded":
        raise errors.StateConflict(f"the candidate import is {op.doc.state}")
    from ..artifacts.store import Artifact
    artifact = await artifacts.projects.store.get(Artifact, provider_org_id,
                                                  candidate["artifact_id"])
    if artifact is None or (artifact.source, artifact.source_repo, artifact.source_commit,
                            sorted(f.relative_path for f in artifact.files)) != (
            "import", candidate["repo"], candidate["commit"], candidate["files"]):
        raise errors.Conflict("the verified artifact is not the registered checkpoint")
    export = json.loads(await objects.get(
        f"lab/{provider_org_id}/label-exports/{bundle['export']['export_id']}/export.json")
        or b"{}")
    methods = Counter(m for x in export.get("lineage", ()) for m in x["methods"])
    record = {
        "format": EVIDENCE, "provider_org_id": provider_org_id,
        "external_run_id": external_run_id, "external_run_ref": bundle["external_run_ref"],
        "dataset_ref": bundle["dataset_ref"], "export": bundle["export"],
        "label_methods": dict(sorted(methods.items())), "holdout": bundle["holdout"],
        "config": bundle["config"],
        "checkpoint": {"checkpoint_id": checkpoint_id,
                       "artifact_digest": candidate["artifact_digest"]},
        "evaluation": eligible["evaluation"], "approved_by": eligible["approved_by"],
        "artifact": {"artifact_id": artifact.artifact_id,
                     "manifest_sha256": artifact.manifest_sha256,
                     "operation_id": candidate["operation_id"],
                     "repo": candidate["repo"], "commit": candidate["commit"]},
        "qualification": {"state": "pending", "requires": [
            "AP-05 readiness of a deployment of this artifact's revision",
            "the 10d SOP benchmark report of that deployment"]},
        "recorded_at": now.isoformat()}
    await write_once(objects, key, lab.canonical(record))
    return record


__all__ = ["EVIDENCE", "MANUAL", "SUPPORTED", "export_for_training", "register_candidate",
           "release_evidence"]
