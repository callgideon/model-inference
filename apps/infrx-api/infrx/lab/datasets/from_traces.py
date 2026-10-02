"""AP-10 10c: a rights-preserving trace -> dataset operation, through ControlOps (0060).

New here is only the operation around existing parts; it replaces nothing. The work itself is
N3's `lineage.select` (permitted traces -> an immutable D7 version, lineage per sample,
content bounds) and N2's `versions.derive` (a new immutable version with a deterministic,
leakage-trapped train/validation/holdout split over it).

* **Selection** (`start`, the route's half): the actor is a provider workspace session; it
  must be a current developer+ member (`acting_provider`) and the grantor's CURRENT AP-07 grant
  (`infrx.lab_access_grants`, read through L2) must permit request and response content of the
  model for `provider_sharing` (reading content) and `training` (the purpose: annotation is
  `training`, as `console.data_use` says). The selection - body, user, the grant id and VERSION,
  its instant - is written once to the Lab objects under a selection id derived from the
  provider and the Idempotency-Key, then `ControlOps.start` (`dataset.from_traces`, scoped,
  hashed: replay / 409 / 410). The selection is written first so a worker never leases an
  operation without its selection; a retry under the same key with another body is a 409 even
  when the first attempt died between the two writes.
* **Materialisation** (`work`, the datasets worker's pass): lease, then the same gate again -
  and the grant must still be the selected VERSION. A revocation, an expiry or any new version
  between selection and materialisation refuses (`failed`, code `forbidden`, field `grant`);
  nothing is published. Then `select` (which checks `provider_sharing` again per request and
  omits expired content) publishes `<dataset>@<version>`, and `derive` publishes
  `<dataset>@<version + 1>` with its holdout recorded in the manifest; the outcome (both refs,
  the split digest, the holdout, every omission) is written once beside the selection. A
  crash resumes under a new fence and republishes the same bytes (D7 and the objects are
  write-once on identical bytes). `cancel_requested` finishes `cancelled`. A 5xx/429 or an
  unexpected failure finishes nothing: the lease lapses and the operation is leased again.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from collections.abc import Callable

from pydantic import Field, model_validator

from ...contracts import api, errors
from ...contracts.lab import records as lab
from ...contracts.v2.records import AccessGrant, DataCategory, DataPurpose
from ...datasets import acting_provider, lineage, versions
from ...datasets.imports import write_once
from ...state.control_ops import ControlOps, Operation, Started, input_hash
from ...state.lab_data import grant_ref
from ..time import iso_z

KIND = "dataset.from_traces"
LEASE_S = 600
PURPOSES = (DataPurpose.provider_sharing, DataPurpose.training)
CATEGORIES = (DataCategory.request_content, DataCategory.response_content)
GRANT_REFUSED = api.FieldError(field="grant", code="grant_not_current",
                               message="the selected grant no longer permits this dataset")
log = logging.getLogger(__name__)


class TraceDataset(api.Wire):
    """POST body. The provider and the user are the actor's, never a field."""

    grantor_org_id: uuid.UUID
    model_id: str = Field(min_length=1, max_length=200)
    request_ids: tuple[str, ...] = Field(min_length=1, max_length=lineage.MAX_SELECT)
    dataset_id: uuid.UUID
    version: int = Field(ge=1)                 # the selection; the split is version + 1
    purpose: Literal["training", "annotation"]
    seed: int
    train_bp: int = Field(default=8000, ge=0)
    validation_bp: int = Field(default=1000, ge=0)

    @model_validator(mode="after")
    def _holdout(self) -> TraceDataset:
        if self.train_bp + self.validation_bp >= 10_000:
            raise ValueError("a trace dataset keeps a held-out evaluation split")
        return self


def _key(provider: str, selection_id: str, suffix: str = "") -> str:
    return f"lab/{provider}/lineage/selections/{selection_id}{suffix}.json"


def selection_id(provider: str, idempotency_key: str) -> str:
    """Stable per provider and key; v4-shaped, as every Lab record id (`ids.UUID_RE`)."""
    digest = hashlib.sha256(f"dataset.from_traces\n{provider}\n{idempotency_key}".encode())
    return str(uuid.UUID(bytes=digest.digest()[:16], version=4))


async def gate(access, *, user_id: str, provider_org_id: str, grantor_org_id: str,
               model_id: str) -> AccessGrant:
    """The current grant permitting both categories for both purposes, or the refusal. (A
    version written between the reads is caught by the materialisation's version check.)"""
    await acting_provider(access, user_id, provider_org_id)
    for p in PURPOSES:
        for c in CATEGORIES:
            grant = await access.authorize_content(
                user_id=user_id, provider_org_id=provider_org_id, grantor_org_id=grantor_org_id,
                model_id=model_id, category=c, purpose=p)
    return grant


async def start(ops: ControlOps, access, objects, actor: api.Actor, idempotency_key: str,
                body: TraceDataset) -> Started:
    provider, user = actor.provider_org_id, actor.user_id
    if actor.audience != "session" or provider is None or user is None:
        raise errors.Forbidden("a trace dataset is selected in a provider workspace session")
    grant = await gate(access, user_id=user, provider_org_id=provider,
                       grantor_org_id=str(body.grantor_org_id), model_id=body.model_id)
    digest = input_hash(body.model_dump(mode="json"))
    sid = selection_id(provider, idempotency_key)
    stored = await objects.get(_key(provider, sid))
    if stored is None:
        await write_once(objects, _key(provider, sid), lab.canonical({
            "selection_id": sid, "input_hash": digest, "body": body.model_dump(mode="json"),
            "user_id": user, "grant_id": grant.grant_id, "grant_version": grant.version,
            "selected_at": iso_z(await access.store.db_now())}))
    elif json.loads(stored)["input_hash"] != digest:
        raise errors.IdempotencyConflict("this Idempotency-Key was used with another request")
    return await ops.start(KIND, actor, idempotency_key, digest, resource_kind="dataset_selection",
                           resource_id=sid)


async def outcome(objects, *, provider_org_id: str, selection_id: str) -> dict | None:
    """A succeeded operation's outcome (its `resource_id` is the selection id), else None."""
    data = await objects.get(_key(provider_org_id, selection_id, ".outcome"))
    return None if data is None else json.loads(data)


@dataclass(frozen=True)
class Ports:
    access: Any                       # LabAccess (L2)
    retention: Any                    # T3's Retention
    content: Callable[[str, AccessGrant], lineage.Content]   # (user, selected grant) -> C2
    feedback: lineage.Feedback
    model_of: lineage.ModelOf
    store: Any                        # D7
    objects: Any                      # the Lab objects


class C2Content:
    """`lineage.Content` over C2 (`content.ContentAccess`) for one operation: every ref is
    issued to the operation's user, bound to the SELECTED grant version, for `training`."""

    def __init__(self, c2, user_id: str, grant: AccessGrant) -> None:
        self.c2, self.user_id, self.grant = c2, user_id, grant

    async def sign(self, *, provider_org_id: str, grantor_org_id: str, request_id: str,
                   grant_id: str, model_id: str) -> str:
        handle, _ = await self.c2.issue(user_id=self.user_id, provider_org_id=provider_org_id,
                                        grant_ref=grant_ref(self.grant), request_id=request_id,
                                        purpose=DataPurpose.training)
        return handle

    async def read(self, ref: str, *, provider_org_id: str) -> dict:
        got = await self.c2.read(handle=ref, user_id=self.user_id,
                                 provider_org_id=provider_org_id)
        if got.content is None:
            raise errors.Gone(f"the content is {got.state}")
        return got.content.model_dump(mode="json")


async def _materialise(op: Operation, ops: ControlOps, p: Ports) -> str:
    provider, sid = op.actor.provider_org_id or "", op.resource_id or ""
    stored = await p.objects.get(_key(provider, sid))
    if stored is None:
        raise errors.NotFound("the selection was not recorded")
    sel = json.loads(stored)
    body, user = TraceDataset.model_validate(sel["body"]), sel["user_id"]
    grantor = str(body.grantor_org_id)
    grant = await gate(p.access, user_id=user, provider_org_id=provider,
                       grantor_org_id=grantor, model_id=body.model_id)
    if (grant.grant_id, grant.version) != (sel["grant_id"], sel["grant_version"]):
        raise errors.Forbidden("the grant changed since the selection")
    if (await ops.advance(op.operation_id, op.fence, "selecting")).state == "cancel_requested":
        return "cancelled"
    dataset_id, at = str(body.dataset_id), sel["selected_at"]
    selected = await lineage.select(
        access=p.access, retention=p.retention, content=p.content(user, grant),
        feedback=p.feedback, model_of=p.model_of, store=p.store, objects=p.objects,
        user_id=user, provider_org_id=provider, grantor_org_id=grantor, model_id=body.model_id,
        selection_id=sid, dataset_id=dataset_id, version=body.version, created_at=at,
        request_ids=list(body.request_ids), actor=user)
    if (await ops.advance(op.operation_id, op.fence, "splitting")).state == "cancel_requested":
        return "cancelled"
    derived = await versions.derive(
        p.store, p.objects, provider_org_id=provider, actor=user, dataset_id=dataset_id,
        version=body.version + 1, created_at=at, add=[selected.dataset_ref],
        policy=versions.SplitPolicy(seed=body.seed, train_bp=body.train_bp,
                                    validation_bp=body.validation_bp),
        now=p.retention.clock())
    manifest = await p.store.resolve(derived.dataset_ref, provider_org_id=provider)
    await write_once(p.objects, _key(provider, sid, ".outcome"), lab.canonical({
        "selection_id": sid, "purpose": body.purpose, "grant_id": grant.grant_id,
        "grant_version": grant.version, "selected_ref": selected.dataset_ref,
        "dataset_ref": derived.dataset_ref, "split_digest": derived.split_digest,
        "holdout": list(manifest.splits.holdout),
        "omitted": selected.omitted + derived.omitted, "review": derived.review}))
    return "succeeded"


async def work(ops: ControlOps, ports: Ports, *, worker_id: str, limit: int = 1,
               lease_s: int = LEASE_S) -> dict[str, int]:
    """The datasets worker's pass over pending `dataset.from_traces` operations."""
    done = {"succeeded": 0, "failed": 0, "cancelled": 0, "retry": 0}
    for operation_id in await ops.pending([KIND], limit):
        try:
            op = await ops.lease(operation_id, worker_id, lease_s)
        except errors.Conflict:
            done["retry"] += 1                # another worker leased it first
            continue
        body = None
        try:
            state = await _materialise(op, ops, ports)   # a cancel shows at its first phase
        except (errors.ServerError, errors.RateLimitError):
            log.warning("trace dataset %s: transient refusal", operation_id, exc_info=True)
            done["retry"] += 1                # the lease lapses; leased again
            continue
        except errors.DomainError as refused:
            state, body = "failed", api.envelope(refused, operation_id, operation_id=operation_id,
                                                 resource_id=op.resource_id).error
            if isinstance(refused, errors.Forbidden):    # the rights refused, by name
                body = body.model_copy(update={"field_errors": (GRANT_REFUSED,)})
        except Exception:                     # noqa: BLE001 - the lease lapses: leased again
            log.exception("trace dataset %s did not finish", operation_id)
            done["retry"] += 1
            continue
        try:
            await ops.finish(operation_id, op.fence, state, body)
        except errors.Conflict:
            done["retry"] += 1                # the lease lapsed first: the new holder finishes
            continue
        done[state] += 1
    return done


def pg_ports(connect, objects, retention) -> Ports:
    """The datasets role's ports on its own login (`work`'s production composition)."""
    from types import SimpleNamespace

    from ...content import ContentAccess
    from ...state.feedback import PgFeedbackService
    from ...state.lab_access import PgAccessStore
    from ...state.lab_content import PgContentRefs
    from ...state.lab_data import PgLabDataStore
    from ...traces.ship.pins import pg_rows
    from ..access import LabAccess
    c2, feedback = ContentAccess(PgContentRefs(connect), retention), PgFeedbackService(connect)

    async def rows(org: str, request: str):
        owner: Any = SimpleNamespace(org_id=org, is_operator=False)   # the grantor's own rows
        return await feedback.list_owned(owner, request)

    async def model_of(row) -> str | None:
        """WR-N3-4: the row's serving version -> its model (0007)."""
        # ponytail: one lookup per row (<= MAX_SELECT); a batched read if selections grow
        found = await pg_rows(connect, "select model_id::text from infrx.serving_versions "
                              "where serving_version_id::text = %s",
                              (row.serving_version_id,)) if row.serving_version_id else []
        return found[0][0] if isinstance(found, list) and found else None
    return Ports(access=LabAccess(PgAccessStore(connect)), retention=retention,
                 content=lambda user, grant: C2Content(c2, user, grant), feedback=rows,
                 model_of=model_of, store=PgLabDataStore(connect), objects=objects)


__all__ = ["KIND", "C2Content", "Ports", "TraceDataset", "gate", "outcome", "pg_ports",
           "selection_id", "start", "work"]
