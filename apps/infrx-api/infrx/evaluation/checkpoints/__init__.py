"""B3: benchmark externally produced checkpoints continuously (CHECKPOINT-IDEM).

**B3.a `receive`.** An external training loop posts a checkpoint event (`EVENT_SCHEMA`):
its provider, the signing key's id, a checkpoint id, the external run it belongs to (D7's
`lab.external_run.1`), its training `step`, and the artifact's URI and sha256 digest, with
the time it was issued. The raw body is signed with HMAC-SHA256 under a key the provider
registered (`sign`; the key directory is a secret port, keys are generated in tests only).
A body whose signature does not verify under the named key, or whose provider is not that
key's (provider binding), is `invalid_api_key`; the external run must be the provider's; an
event issued outside `window_s` of the store clock is refused (a captured event cannot be
replayed later). Inside the window a replay is the same receipt: the ledger keeps the event
once per checkpoint id and D7 (`lab_receive_checkpoint`) the receipt and ONE
`checkpoint_received` outbox event; the same id naming other bytes is `idempotency_conflict`.

**B3.b `on_checkpoint`** (the handler for that outbox event). A received checkpoint is
registered first: its URI's scheme must be a supported registry adapter, the fetched bytes
must hash to the signed digest (bytes changed under the URL are refused) and parse as a
safetensors file; otherwise the checkpoint is `rejected` with the reason and nothing is
deployed. A validated checkpoint is offered to every subscription of its external run
(`subscribe`: a pinned suite - dataset, harness, evaluator spec, seed, max_cases - a CREDIT
limit per run and in total, a concurrency bound and a policy). Per subscription, once:
* `latest_only` skips a checkpoint whose step is below one already received and not
  rejected (`superseded`): arrival order never redefines latest;
* a run that would take the queued runs' limits past the subscription's total is
  `budget`-skipped;
* at `max_active` unfinished runs the event is handed back (`CapacityExhausted`: the Lab
  outbox relay releases it and retries later) - nothing is recorded, so it is decided again;
* otherwise the checkpoint is deployed to a PRIVATE DEV deployment (the `Deployer` port,
  idempotent per checkpoint) and B1's `freeze` queues the run as the subscription's owner,
  whose current membership the L2 port checks again (a revoked owner or grant is a visible
  `forbidden`/`not_found` skip). The run id is derived from (subscription, checkpoint) and
  its `created_at` is the event's, so a redelivery - after a crash anywhere between receipt
  and dispatch - is the same run record, the same ref and the same D7 run: never a second
  paid run.
Every decision is persisted (the ledger), and the checkpoint moves to `evaluated` once a
run is queued. Nothing here publishes or promotes: a checkpoint's deployment is dev-only
and an evaluation is evidence, never a release.

**B3.c lineage**: receipt (D7) -> event and registration (ledger + D7 state) -> decision with
its run id (ledger) -> run, attempts and results (D7). The ledger is lab-sql's to persist
(WR-B3-1); `FakeCheckpointLedger` in the tests keeps its rules.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError

from ...contracts import errors
from ...contracts.lab import records as lab
from ...contracts.v2.money_units import Credit
from ...lab.access import LabAccess
from .. import runner

EVENT_SCHEMA = "infrx.checkpoint_event.1"
WINDOW_S = 300
MAX_HEADER = 100 * 2**20              # a safetensors header past 100 MiB is not a checkpoint


class Artifact(lab.LabModel):
    uri: lab.Text
    digest: lab.Sha256


class CheckpointEvent(lab.LabModel):
    schema_id: Literal["infrx.checkpoint_event.1"] = Field(alias="schema")
    provider_org_id: lab.Uuid
    key_id: lab.Text
    checkpoint_id: lab.Uuid
    external_run_ref: lab.RefOf("external_run")
    step: int = Field(ge=0)
    artifact: Artifact
    issued_at: lab.Ts


class Subscription(lab.LabModel):
    subscription_id: lab.Uuid
    provider_org_id: lab.Uuid
    owner_user_id: lab.Uuid
    external_run_ref: lab.RefOf("external_run")
    dataset_ref: lab.RefOf("dataset")
    harness_ref: lab.RefOf("harness")
    evaluator_ref: lab.RefOf("evaluator")
    evaluator: dict[str, Any]
    seed: int = Field(ge=0)
    max_cases: int = Field(ge=1)
    run_limit: lab.Amount
    limit: lab.Amount
    max_active: int = Field(ge=1)
    policy: Literal["latest_only", "every"]


class CheckpointLedger(Protocol):
    """lab-sql's persistence of B3 (WR-B3-1); every read is provider-scoped."""

    async def db_now(self) -> datetime: ...
    async def record_event(self, event: CheckpointEvent) -> CheckpointEvent:
        """Insert once per checkpoint id; the same id with another body is a conflict."""
    async def event(self, checkpoint_id: str, *, provider_org_id: str) -> CheckpointEvent: ...
    async def events(self, external_run_ref: str, *,
                     provider_org_id: str) -> list[tuple[CheckpointEvent, bool]]:
        """Every event of the external run with whether it was rejected."""
    async def reject(self, checkpoint_id: str, reason: str, *, provider_org_id: str) -> None: ...
    async def add_subscription(self, subscription: Subscription) -> Subscription: ...
    async def subscriptions(self, external_run_ref: str, *,
                            provider_org_id: str) -> list[Subscription]: ...
    async def decisions(self, subscription_id: str) -> dict[str, dict[str, Any]]:
        """checkpoint id -> {state, reason, run_id}: queued or skipped, written once."""
    async def decide(self, subscription_id: str, checkpoint_id: str, *, state: str,
                     reason: str | None, run_id: str | None) -> dict[str, Any]: ...


class Deployer(Protocol):
    """L3: a private dev deployment of a validated checkpoint, idempotent per checkpoint id,
    pulled by its digest; answers its serving ref. Never a public or production target."""

    async def deploy(self, *, provider_org_id: str, checkpoint_id: str, uri: str,
                     digest: str) -> str: ...


def sign(body: bytes, secret: bytes) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


async def receive(body: bytes, signature: str, *, keys, ledger: CheckpointLedger, store,
                  window_s: float = WINDOW_S) -> dict[str, Any]:
    """B3.a: an authenticated, provider-bound, fresh event -> the D7 receipt (once)."""
    try:
        claimed = json.loads(body)
        key_id = claimed["key_id"]
    except (ValueError, TypeError, KeyError):
        raise errors.InvalidApiKey("the checkpoint event is not signed") from None
    key = keys(key_id) if isinstance(key_id, str) else None
    if key is None or not hmac.compare_digest(sign(body, key[1]), signature):
        raise errors.InvalidApiKey("the checkpoint event signature does not verify")
    try:
        event = CheckpointEvent.model_validate(claimed)
    except ValidationError as refused:
        raise errors.InvalidRequest(f"checkpoint event: {refused}") from None
    if {event.provider_org_id, lab.REF_RE.fullmatch(event.external_run_ref).group(2)} != \
            {key[0]}:
        raise errors.InvalidApiKey("the signing key is not this provider's")
    if abs((await ledger.db_now() - _ts(event.issued_at)).total_seconds()) > window_s:
        raise errors.InvalidRequest("the checkpoint event is outside the replay window")
    await store.resolve(event.external_run_ref, provider_org_id=event.provider_org_id)
    await ledger.record_event(event)
    return await store.receive_checkpoint(
        provider_org_id=event.provider_org_id, checkpoint_id=event.checkpoint_id,
        external_run_ref=event.external_run_ref, artifact_digest=event.artifact.digest)


async def subscribe(ledger: CheckpointLedger, store, payload: dict[str, Any], *,
                    access: LabAccess, user_id: str) -> Subscription:
    """A pinned suite on one external run, by a member allowed to run evaluations."""
    try:
        sub = Subscription.model_validate({**payload, "owner_user_id": user_id})
    except ValidationError as refused:
        raise errors.InvalidRequest(f"subscription: {refused}") from None
    if {sub.run_limit.unit, sub.limit.unit} != {"CREDIT"} or \
            sub.run_limit.amount > sub.limit.amount:
        raise errors.InvalidRequest("a subscription spends provider_dev CREDIT, a run's limit "
                                    "within the total")
    evaluator_id = lab.REF_RE.fullmatch(sub.evaluator_ref).group(3)
    if runner.evaluator_ref(sub.evaluator, provider_org_id=sub.provider_org_id,
                            evaluator_id=evaluator_id) != sub.evaluator_ref:
        raise errors.InvalidRequest("the evaluator spec is not the one its ref names")
    await runner.may_schedule(access, user_id=user_id, provider_org_id=sub.provider_org_id)
    await store.resolve(sub.external_run_ref, provider_org_id=sub.provider_org_id)
    return await ledger.add_subscription(sub)


def _valid_safetensors(data: bytes) -> bool:
    """An 8-byte little-endian header length, a JSON header, every tensor inside the file."""
    if len(data) < 8:
        return False
    size = int.from_bytes(data[:8], "little")
    if size > min(MAX_HEADER, len(data) - 8):
        return False
    try:
        header = json.loads(data[8:8 + size])
        spans = [t["data_offsets"] for name, t in header.items() if name != "__metadata__"]
        return bool(spans) and all(
            0 <= a <= b <= len(data) - 8 - size for a, b in spans)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


async def _register(event: CheckpointEvent, registries) -> str | None:
    """None if the artifact is the signed, well-formed checkpoint, else the reason."""
    fetch = registries.get(event.artifact.uri.split(":", 1)[0])
    if fetch is None:
        return "unsupported_registry"
    # ponytail: the whole artifact in memory; stream the hash when a real adapter lands.
    data = await fetch(event.artifact.uri)
    if "sha256:" + hashlib.sha256(data).hexdigest() != event.artifact.digest:
        return "digest_mismatch"
    return None if _valid_safetensors(data) else "malformed"


def run_id_of(subscription_id: str, checkpoint_id: str) -> str:
    """One run per subscription and checkpoint (a v4-shaped id, derived)."""
    digest = hashlib.sha256(f"{subscription_id}:{checkpoint_id}".encode()).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))


def _run_payload(sub: Subscription, event: CheckpointEvent, serving_ref: str) -> dict:
    run_id = run_id_of(sub.subscription_id, event.checkpoint_id)
    return {"schema": "lab.eval_run.1", "provider_org_id": sub.provider_org_id,
            "run_id": run_id, "created_at": event.issued_at, "dataset_ref": sub.dataset_ref,
            "harness_ref": sub.harness_ref, "serving_ref": serving_ref,
            "evaluator_ref": sub.evaluator_ref, "seed": sub.seed, "environment": "dev",
            "max_cases": sub.max_cases, "state": "queued",
            "idempotency_key": lab.run_key(run_id),
            "budgets": [{"limit": sub.run_limit.model_dump(),
                         "reserved": {"unit": "CREDIT", "value": "0.00000000"}}]}


async def on_checkpoint(checkpoint_id: str, *, provider_org_id: str, ledger: CheckpointLedger,
                        store, registries, deployer: Deployer,
                        access: LabAccess) -> dict[str, dict[str, Any]]:
    """B3.b: register the checkpoint, then decide it once per subscription; answers
    subscription id -> decision. Raises `CapacityExhausted` to be retried later."""
    event = await ledger.event(checkpoint_id, provider_org_id=provider_org_id)
    receipt = await _registered(event, store, ledger, registries)
    subs = await ledger.subscriptions(event.external_run_ref, provider_org_id=provider_org_id)
    if receipt["state"] == "rejected":
        return {s.subscription_id: await _once(ledger, s, event, "skipped", "rejected")
                for s in subs}
    superseded = None                 # a newer step counts only once it registered valid
    serving, out, busy = None, {}, False
    for sub in subs:
        mine = await ledger.decisions(sub.subscription_id)
        done = mine.get(checkpoint_id)
        if done is not None and done["state"] != "queued":
            out[sub.subscription_id] = done
            continue
        if done is None:
            if sub.policy == "latest_only" and superseded is None:
                superseded = False
                for e, _ in await ledger.events(event.external_run_ref,
                                                provider_org_id=provider_org_id):
                    if e.step > event.step and (await _registered(
                            e, store, ledger, registries))["state"] != "rejected":
                        superseded = True
                        break
            if sub.policy == "latest_only" and superseded:
                out[sub.subscription_id] = await _once(ledger, sub, event, "skipped",
                                                       "superseded")
                continue
            queued = [d for d in mine.values() if d["state"] == "queued"]
            spend = sum([sub.run_limit.amount] * (len(queued) + 1), Credit("0"))
            if spend > sub.limit.amount:
                out[sub.subscription_id] = await _once(ledger, sub, event, "skipped", "budget")
                continue
            active = [d for d in queued if (await store.run_status(
                d["run_id"], provider_org_id=provider_org_id))["state"] in ("queued", "running")]
            if len(active) >= sub.max_active:
                busy = True
                continue
        try:                          # the L2 half of R160 before any GPU is spent
            await runner.may_schedule(access, user_id=sub.owner_user_id,
                                      provider_org_id=provider_org_id)
        except (errors.Forbidden, errors.NotFound) as refused:
            out[sub.subscription_id] = await _once(ledger, sub, event, "skipped", refused.code)
            continue
        serving = serving or await deployer.deploy(
            provider_org_id=provider_org_id, checkpoint_id=checkpoint_id,
            uri=event.artifact.uri, digest=event.artifact.digest)
        try:
            frozen = await runner.freeze(store, _run_payload(sub, event, serving),
                                         evaluator=sub.evaluator, access=access,
                                         user_id=sub.owner_user_id,
                                         provider_org_id=provider_org_id)
        except (errors.Forbidden, errors.NotFound) as refused:
            out[sub.subscription_id] = await _once(ledger, sub, event, "skipped", refused.code)
            continue
        out[sub.subscription_id] = done or await ledger.decide(
            sub.subscription_id, checkpoint_id, state="queued", reason=None,
            run_id=frozen.run.run_id)
    if receipt["state"] == "validated" and any(d["state"] == "queued" for d in out.values()):
        await store.transition_checkpoint(checkpoint_id, "evaluated",
                                          provider_org_id=provider_org_id)
    if busy:
        raise errors.CapacityExhausted("a subscription is at its concurrent-run bound")
    return out


async def _registered(event: CheckpointEvent, store, ledger: CheckpointLedger,
                      registries) -> dict[str, Any]:
    """The D7 receipt of a received event, registered (validated or rejected) once."""
    receipt = await store.receive_checkpoint(         # a redelivery: the current receipt
        provider_org_id=event.provider_org_id, checkpoint_id=event.checkpoint_id,
        external_run_ref=event.external_run_ref, artifact_digest=event.artifact.digest)
    if receipt["state"] != "received":
        return receipt
    reason = await _register(event, registries)
    if reason:
        await ledger.reject(event.checkpoint_id, reason, provider_org_id=event.provider_org_id)
    return await store.transition_checkpoint(
        event.checkpoint_id, "rejected" if reason else "validated",
        provider_org_id=event.provider_org_id)


async def _once(ledger: CheckpointLedger, sub: Subscription, event: CheckpointEvent,
                state: str, reason: str) -> dict[str, Any]:
    return (await ledger.decisions(sub.subscription_id)).get(event.checkpoint_id) or \
        await ledger.decide(sub.subscription_id, event.checkpoint_id, state=state,
                            reason=reason, run_id=None)


# --- P3's Evaluations port (WR-E7L-1 / WR-B3-EVALS) -----------------------------------------
class Evaluations:
    """P3's `Evaluations` over B3 and B1, for a checkpoint P3 validated (the manual bundle's).

    `suites(provider_org_id=, checkpoint_id=)` is B3's side: the pinned suite that evaluates
    the checkpoint (a `Subscription`: harness, evaluator, seed, cases, CREDIT run limit, owner)
    and the checkpoint's private dev serving (L3's deployer). Without it (not composable yet:
    the checkpoint's run is not readable from D7 and no dev deployer exists, WR-B3-3) nothing
    is frozen: a typed 503. With it, `evaluate` is ONE B1 run on the dataset P3 asks (the
    bundle's), its id derived from (subscription, checkpoint, dataset) and resumed from D7 when
    it exists, so a repeat or a crash before the record is the same run; the Lab eval worker
    works it. The record (write-once, `lab/<p>/checkpoints/<id>/evaluation.json`) pins the
    holdout digest of what B1 froze, never the ask's; `evaluation()` adds D7's run state."""

    def __init__(self, store, objects, access: LabAccess, suites=None) -> None:
        self.store, self.objects, self.access, self.suites = store, objects, access, suites

    @staticmethod
    def _key(provider_org_id: str, checkpoint_id: str) -> str:
        return f"lab/{provider_org_id}/checkpoints/{checkpoint_id}/evaluation.json"

    async def evaluate(self, *, provider_org_id: str, checkpoint_id: str, dataset_ref: str,
                       split: str, holdout_sha256: str) -> str:
        from ...datasets.imports import write_once
        if split != "holdout":
            raise errors.InvalidRequest("a checkpoint is evaluated on its frozen holdout")
        key = self._key(provider_org_id, checkpoint_id)
        found = await self.objects.get(key)
        if found is None:
            if self.suites is None:
                raise errors.DependencyUnavailable("no B3 suite or dev deployer serves this "
                                                   "checkpoint yet (WR-B3-3)")
            sub, serving = await self.suites(provider_org_id=provider_org_id,
                                             checkpoint_id=checkpoint_id)
            run_id = run_id_of(sub.subscription_id, f"{checkpoint_id}:{dataset_ref}")
            try:
                frozen = await runner.resume(self.store, run_id, evaluator=sub.evaluator,
                                             provider_org_id=provider_org_id)
            except errors.NotFound:
                now = (await self.access.store.db_now()).strftime("%Y-%m-%dT%H:%M:%SZ")
                event = CheckpointEvent.model_construct(checkpoint_id=checkpoint_id,
                                                        issued_at=now)
                payload = {**_run_payload(sub, event, serving), "run_id": run_id,
                           "idempotency_key": lab.run_key(run_id), "dataset_ref": dataset_ref}
                frozen = await runner.freeze(self.store, payload, evaluator=sub.evaluator,
                                             access=self.access, user_id=sub.owner_user_id,
                                             provider_org_id=provider_org_id)
            await write_once(self.objects, key, lab.canonical({
                "run_ref": frozen.run_ref, "run_id": run_id,
                "dataset_ref": frozen.run.dataset_ref, "split": split,
                "holdout_sha256": hashlib.sha256(
                    lab.canonical(sorted(frozen.holdout))).hexdigest()}))
            found = await self.objects.get(key)
        return json.loads(found)["run_ref"]

    async def evaluation(self, *, provider_org_id: str, checkpoint_id: str) -> dict | None:
        found = await self.objects.get(self._key(provider_org_id, checkpoint_id))
        if found is None:
            return None
        record = json.loads(found)
        status = await self.store.run_status(record["run_id"], provider_org_id=provider_org_id)
        return {**record, "state": status["state"]}
