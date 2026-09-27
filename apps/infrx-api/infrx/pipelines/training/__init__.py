"""P3: external training and candidate import (TRAIN-RECOVER, PIPELINE-LINEAGE).

**The manual bundle is the real workflow.** `prepare` publishes the `lab.external_run.1`
record (purpose `training`, PROVIDER_USD with a named payer) through D7 and writes one
write-once bundle: the dataset ref, the config (objective `sft`/`preference`; adaptation
`full`/`lora` - LoRA is how the weights adapt, not a competing objective), the environment,
the train and dev ids the training gate allows NOW, the export that carries their content
(N2's or P1's, of this dataset), and a pin of the frozen holdout (its size and the digest of
its ids) - never the holdout itself. The provider trains on its own compute.

**Connectors.** `ManualConnector` is always advertised; an automatic one (`HttpConnector`,
the submit/lookup/status/cancel protocol a test server speaks) is used only when named in
`advertised`, whose default is the manual one alone - automatic submission stays hidden
until P-11 records real integration evidence.

**One paid job per run** (F3's `external_run` machine, R161). `submit` re-reads the rights
(a current developer-or-above; every bundled id still under a training grant), reserves the
budget once per `submit_key`, moves prepared -> submitting, then calls the connector with
the key. A definite refusal is `failed` (the reservation released); any other outcome
(a timeout, a 5xx, a crash) is `ambiguous` and holds the reservation. An ambiguous or stale
`submitting` run is only ever reconciled by `lookup(submit_key)` - found is `submitted`,
not found stays `ambiguous` - so a resume never submits twice. The provider-reported cost
is settled as reported, or as unknown (`None`) - never an estimate.

**Checkpoints come back through B3** (the eval-ops lane; its port is faked until B3
merges) over D7's receipts (merged): a redelivery is the same receipt; a checkpoint of a
run that is not submitted or completed (a late one after cancel), a missing artifact, other
bytes than the declared digest, or a descriptor for another base model or adaptation is
`rejected` and never evaluated. A valid one is queued for evaluation on the bundle's frozen
holdout. `approve` makes a candidate eligible only on a succeeded evaluation of exactly that
holdout - provider training metrics never promote, and nothing here is public.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ...contracts import errors
from ...contracts.lab import records as lab
from ...contracts.v2.records import ProviderCapability
from ...datasets.imports import write_once

MANUAL = "manual-bundle"
ADVERTISED = frozenset({MANUAL})
BUNDLE = "infrx.training_bundle.1"
DESCRIPTOR = "infrx.checkpoint.1"
ACTIVE = ("submitted", "completed")                # states a checkpoint may arrive in


class TrainingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    objective: Literal["sft", "preference"]
    adaptation: Literal["full", "lora"]
    base_model: str = Field(min_length=1)
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, str]


class Rejected(Exception):
    """A connector's definite refusal: nothing was accepted, nothing is owed."""


class Connector(Protocol):
    name: str

    async def submit(self, bundle: dict, *, key: str) -> str: ...

    async def lookup(self, key: str) -> str | None: ...

    async def status(self, job_id: str) -> dict: ...

    async def cancel(self, job_id: str) -> dict: ...


class RunLedger(Protocol):
    """D8's external-run lifecycle and D6J's USD reservation (lab-sql; faked until merged)."""

    async def get(self, external_run_id: str, *, provider_org_id: str) -> dict | None: ...

    async def move(self, external_run_id: str, *, provider_org_id: str, expected: str | None,
                   target: str, **fields: Any) -> dict:
        """Compare-and-set along F3's machine (`expected=None` creates); `StateConflict`."""

    async def reserve(self, key: str, *, provider_org_id: str, payer_ref: str,
                      limit: str) -> dict: ...

    async def settle(self, key: str, *, provider_org_id: str, cost: str | None) -> dict: ...

    async def release(self, key: str, *, provider_org_id: str) -> dict: ...

    async def note(self, key: str, body: dict, *, provider_org_id: str) -> dict:
        """Append-only, one body per key (a replay is the stored body)."""

    async def noted(self, key: str, *, provider_org_id: str) -> dict | None: ...


class Evaluations(Protocol):
    """B3's suite subscription (queues a B1 run) and its result, per checkpoint."""

    async def evaluate(self, *, provider_org_id: str, checkpoint_id: str, dataset_ref: str,
                       split: str, holdout_sha256: str) -> str: ...

    async def evaluation(self, *, provider_org_id: str, checkpoint_id: str) -> dict | None: ...


# --- connectors ---------------------------------------------------------------------------------
class ManualConnector:
    """The provider downloads the bundle and trains itself: no platform-paid job."""

    name = MANUAL

    async def submit(self, bundle: dict, *, key: str) -> str:
        return f"manual:{key}"

    async def lookup(self, key: str) -> str | None:
        return f"manual:{key}"

    async def status(self, job_id: str) -> dict:
        return {"state": "running"}

    async def cancel(self, job_id: str) -> dict:
        return {"state": "cancelled"}


class HttpConnector:
    """The automatic protocol: POST /jobs (Idempotency-Key), GET /jobs?key=, GET /jobs/<id>,
    POST /jobs/<id>/cancel. A 4xx on submit is a definite refusal; anything else unknown."""

    def __init__(self, client: httpx.AsyncClient, name: str) -> None:
        self.client, self.name = client, name

    async def submit(self, bundle: dict, *, key: str) -> str:
        response = await self.client.post("/jobs", json=bundle, headers={"Idempotency-Key": key})
        if 400 <= response.status_code < 500:
            raise Rejected(f"{response.status_code}: {response.text[:200]}")
        return response.json()["job_id"]            # a 5xx has no job id: unknown, ambiguous

    async def lookup(self, key: str) -> str | None:
        response = await self.client.get("/jobs", params={"key": key})
        if response.status_code == 404:
            return None
        return response.json()["job_id"]

    async def status(self, job_id: str) -> dict:
        return (await self.client.get(f"/jobs/{job_id}")).json()

    async def cancel(self, job_id: str) -> dict:
        response = await self.client.post(f"/jobs/{job_id}/cancel")
        response.raise_for_status()
        return response.json()


# --- P3.a: the bundle ---------------------------------------------------------------------------
def _key(provider: str, external_run_id: str) -> str:
    return f"lab/{provider}/training/{external_run_id}/bundle.json"


async def prepare(store, objects, ledger: RunLedger, *, provider_org_id: str, actor: str,
                  external_run_id: str, dataset_ref: str, config: dict, export: dict,
                  payer_ref: str, limit: str, connector: str = MANUAL) -> dict:
    """The bundle (write-once; a replay is the same bytes, changed inputs a `Conflict`)."""
    try:
        config = TrainingConfig.model_validate(config).model_dump()
    except ValidationError as refused:
        raise errors.InvalidRequest(f"training config: {refused}") from None
    if export.get("dataset_ref") != dataset_ref:
        raise errors.InvalidRequest("the export is of another dataset")
    manifest = await store.resolve(dataset_ref, provider_org_id=provider_org_id)
    allowed = set(await store.accessible_samples(dataset_ref, provider_org_id=provider_org_id,
                                                 purpose="training"))
    train = [i for i in manifest.splits.train if i in allowed]
    dev = [i for i in manifest.splits.validation if i in allowed]
    if not train:
        raise errors.Forbidden("no train sample is under a current training grant")
    run_ref = await store.publish({
        "schema": "lab.external_run.1", "provider_org_id": provider_org_id,
        "external_run_id": external_run_id, "purpose": "training", "connector": connector,
        "dataset_ref": dataset_ref, "submit_key": lab.submit_key(external_run_id),
        "state": "prepared",
        "budget": {"limit": {"unit": "PROVIDER_USD", "value": limit},
                   "reserved": {"unit": "PROVIDER_USD", "value": "0.00000000"},
                   "payer_ref": payer_ref}}, provider_org_id=provider_org_id, actor=actor)
    holdout = sorted(manifest.splits.holdout)
    bundle = {
        "format": BUNDLE, "external_run_ref": run_ref, "dataset_ref": dataset_ref,
        "config": config, "train": train, "dev": dev,
        "omitted": sorted(set(manifest.splits.train + manifest.splits.validation) - allowed),
        "export": {"format": export["format"], "export_id": export["export_id"],
                   "sha256": export.get("sha256") or export["content_sha256"]},
        "holdout": {"size": len(holdout),
                    "sha256": hashlib.sha256(lab.canonical(holdout)).hexdigest()}}
    await write_once(objects, _key(provider_org_id, external_run_id), lab.canonical(bundle))
    await ledger.move(external_run_id, provider_org_id=provider_org_id, expected=None,
                      target="prepared", run_ref=run_ref, connector=connector,
                      payer_ref=payer_ref, limit=limit)
    return bundle


async def _bundle(objects, provider: str, external_run_id: str) -> dict:
    data = await objects.get(_key(provider, external_run_id))
    if data is None:
        raise errors.NotFound(f"no bundle for external run {external_run_id}")
    return json.loads(data)


async def _run(ledger: RunLedger, provider: str, external_run_id: str) -> dict:
    run = await ledger.get(external_run_id, provider_org_id=provider)
    if run is None:
        raise errors.NotFound(f"no external run {external_run_id}")
    return run


async def _member(members, provider: str, user_id: str) -> None:
    membership = await members.membership(provider, user_id)
    now = await members.db_now()
    if membership is None or not membership.permits(ProviderCapability.run_evaluation, now,
                                                    provider):
        raise errors.Forbidden("this needs a current developer or administrator")


# --- P3.b: submit, reconcile, poll, cancel ------------------------------------------------------
async def submit(store, objects, ledger: RunLedger, connector: Connector, members, *,
                 provider_org_id: str, user_id: str, external_run_id: str,
                 advertised=ADVERTISED) -> dict:
    """The run after at most one submission for its key, ever."""
    run = await _run(ledger, provider_org_id, external_run_id)
    key = lab.submit_key(external_run_id)
    if run["connector"] != connector.name or connector.name not in advertised:
        raise errors.Forbidden(f"connector {connector.name!r} is not available for this run")
    if run["state"] in ("submitting", "ambiguous"):
        return await reconcile(ledger, connector, provider_org_id=provider_org_id,
                               external_run_id=external_run_id)
    if run["state"] != "prepared":
        return run
    await _member(members, provider_org_id, user_id)
    bundle = await _bundle(objects, provider_org_id, external_run_id)
    allowed = set(await store.accessible_samples(bundle["dataset_ref"],
                                                 provider_org_id=provider_org_id,
                                                 purpose="training"))
    if set(bundle["train"] + bundle["dev"]) - allowed:
        raise errors.Forbidden("a bundled sample's training grant is no longer current")
    await ledger.reserve(key, provider_org_id=provider_org_id, payer_ref=run["payer_ref"],
                         limit=run["limit"])
    await ledger.move(external_run_id, provider_org_id=provider_org_id, expected="prepared",
                      target="submitting")
    try:
        job_id = await connector.submit(bundle, key=key)
    except Rejected as refused:
        await ledger.release(key, provider_org_id=provider_org_id)
        return await ledger.move(external_run_id, provider_org_id=provider_org_id,
                                 expected="submitting", target="failed", reason=str(refused))
    except Exception:                                   # noqa: BLE001 - the outcome is unknown
        return await ledger.move(external_run_id, provider_org_id=provider_org_id,
                                 expected="submitting", target="ambiguous")
    return await ledger.move(external_run_id, provider_org_id=provider_org_id,
                             expected="submitting", target="submitted", job_id=job_id)


async def reconcile(ledger: RunLedger, connector: Connector, *, provider_org_id: str,
                    external_run_id: str) -> dict:
    """Settle an unknown submission by lookup only: never back to `submitting`."""
    run = await _run(ledger, provider_org_id, external_run_id)
    if run["state"] == "submitting":           # a submit that died between the move and its end
        run = await ledger.move(external_run_id, provider_org_id=provider_org_id,
                                expected="submitting", target="ambiguous")
    if run["state"] != "ambiguous":
        return run
    job_id = await connector.lookup(lab.submit_key(external_run_id))
    if job_id is None:
        return run
    return await ledger.move(external_run_id, provider_org_id=provider_org_id,
                             expected="ambiguous", target="submitted", job_id=job_id)


def _cost(value: Any) -> str | None:
    """A provider-reported PROVIDER_USD amount, or None (unknown) - never an estimate."""
    try:
        return lab.Amount(unit="PROVIDER_USD", value=value).value
    except ValidationError:
        return None


async def poll(ledger: RunLedger, connector: Connector, *, provider_org_id: str,
               external_run_id: str) -> dict:
    run = await _run(ledger, provider_org_id, external_run_id)
    if run["state"] != "submitted":
        return run
    status = await connector.status(run["job_id"])
    if status.get("state") not in ("completed", "failed", "cancelled"):
        return run
    cost = _cost(status.get("cost"))
    await ledger.settle(lab.submit_key(external_run_id), provider_org_id=provider_org_id,
                        cost=cost)
    return await ledger.move(external_run_id, provider_org_id=provider_org_id,
                             expected="submitted", target=status["state"], cost=cost)


async def cancel(ledger: RunLedger, connector: Connector, *, provider_org_id: str,
                 external_run_id: str) -> dict:
    run = await _run(ledger, provider_org_id, external_run_id)
    if run["state"] == "prepared":
        return await ledger.move(external_run_id, provider_org_id=provider_org_id,
                                 expected="prepared", target="cancelled")
    if run["state"] != "submitted":
        raise errors.StateConflict(f"a {run['state']} run cannot be cancelled")
    cost = _cost((await connector.cancel(run["job_id"])).get("cost"))
    await ledger.settle(lab.submit_key(external_run_id), provider_org_id=provider_org_id,
                        cost=cost)
    return await ledger.move(external_run_id, provider_org_id=provider_org_id,
                             expected="submitted", target="cancelled", cost=cost)


# --- P3.c: checkpoints through B3, held-out evaluation before eligibility -----------------------
async def _refusal(objects, run: dict, bundle: dict, artifact_key: str, digest: str) -> str | None:
    if run["state"] not in ACTIVE:
        return f"run_{run['state']}"
    data = await objects.get(artifact_key)
    if data is None:
        return "missing_artifact"
    if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
        return "digest_mismatch"
    try:
        descriptor = json.loads(data)
    except ValueError:
        return "incompatible"
    config = bundle["config"]
    if not isinstance(descriptor, dict) or descriptor.get("format") != DESCRIPTOR or (
            descriptor.get("base_model"), descriptor.get("adaptation")) != (
            config["base_model"], config["adaptation"]):
        return "incompatible"
    return None


async def import_checkpoint(store, objects, ledger: RunLedger, evals: Evaluations, *,
                            provider_org_id: str, external_run_id: str, checkpoint_id: str,
                            artifact_key: str, artifact_digest: str) -> dict:
    """The receipt's outcome: `rejected` with its reason, or `validated` and queued for
    evaluation on the frozen holdout. A redelivery changes nothing."""
    run = await _run(ledger, provider_org_id, external_run_id)
    if not artifact_key.startswith(f"lab/{provider_org_id}/training/{external_run_id}/"):
        raise errors.InvalidRequest("a checkpoint artifact lives under its run's prefix")
    receipt = await store.receive_checkpoint(
        provider_org_id=provider_org_id, checkpoint_id=checkpoint_id,
        external_run_ref=run["run_ref"], artifact_digest=artifact_digest)
    prior = await ledger.noted(f"checkpoint:{checkpoint_id}", provider_org_id=provider_org_id)
    if prior is not None:                       # a redelivery: the first outcome stands
        return prior
    bundle = await _bundle(objects, provider_org_id, external_run_id)
    if receipt["state"] == "received":
        reason = await _refusal(objects, run, bundle, artifact_key, artifact_digest)
        await store.transition_checkpoint(checkpoint_id, "rejected" if reason else "validated",
                                          provider_org_id=provider_org_id)
    else:             # moved before a crash lost the outcome: the receipt's state stands
        reason = "rejected" if receipt["state"] == "rejected" else None
    if reason:
        outcome = {"checkpoint_id": checkpoint_id, "external_run_ref": run["run_ref"],
                   "state": "rejected", "reason": reason}
    else:
        evaluation = await evals.evaluate(
            provider_org_id=provider_org_id, checkpoint_id=checkpoint_id,
            dataset_ref=bundle["dataset_ref"], split="holdout",
            holdout_sha256=bundle["holdout"]["sha256"])
        outcome = {"checkpoint_id": checkpoint_id, "external_run_ref": run["run_ref"],
                   "state": "validated", "evaluation": evaluation}
    return await ledger.note(f"checkpoint:{checkpoint_id}", outcome,
                             provider_org_id=provider_org_id)


async def approve(objects, ledger: RunLedger, evals: Evaluations, members, *,
                  provider_org_id: str, user_id: str, external_run_id: str,
                  checkpoint_id: str) -> dict:
    """An eligible candidate (not public, not promoted): a validated checkpoint of this run
    whose evaluation on exactly the bundle's frozen holdout succeeded."""
    await _member(members, provider_org_id, user_id)
    bundle = await _bundle(objects, provider_org_id, external_run_id)
    outcome = await ledger.noted(f"checkpoint:{checkpoint_id}",
                                 provider_org_id=provider_org_id) or {}
    result = await evals.evaluation(provider_org_id=provider_org_id,
                                    checkpoint_id=checkpoint_id) or {}
    if (outcome.get("state"), outcome.get("external_run_ref")) != (
            "validated", bundle["external_run_ref"]) or (
            result.get("state"), result.get("dataset_ref"), result.get("split"),
            result.get("holdout_sha256")) != ("succeeded", bundle["dataset_ref"], "holdout",
                                              bundle["holdout"]["sha256"]):
        raise errors.StateConflict("no succeeded evaluation on this run's frozen holdout")
    return await ledger.note(f"eligible:{checkpoint_id}", {
        "checkpoint_id": checkpoint_id, "external_run_ref": bundle["external_run_ref"],
        "evaluation": result["run_ref"], "approved_by": user_id},
        provider_org_id=provider_org_id)
