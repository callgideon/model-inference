"""N3: datasets derived from permitted traces, lineage per sample, and tombstones that reach
every derived version and export when a source is revoked, deleted or expires.

**Selection (N3.a) goes through three ports only.** L2 (`LabAccess`): the caller acts for the
provider (`acting_provider`) and the grantor's CURRENT grant allows provider_sharing of
request and response content of the model (and of feedback, or no feedback is joined). T3
(`Retention`): the request is projected, not deleted, and its content inside its bound - a
request T3 does not project is omitted (`missing_projection`) and C2 is never asked for it,
so a missing projection can never stand in for permission. Every projected row must be of
the granted model (`model_of`, the row's serving version -> the grant's model id; WR-N3-4),
else the request is omitted (`model_not_granted`) before C2 is asked. C2 (the content port,
`Content`): a short-lived ref signed for this provider, the request, the grant and the
model, read once; a ref C2
refuses is omitted (`content_denied`), a C2 outage publishes nothing. Each sample's content
object keeps the original request/output and the trace's time span; the grantor's D6F
feedback (0028's durable rows) is appended as separate `corrections` records, never merged
into the original and without author identity. At most `MAX_SELECT` requests per call.

**Lineage (N3.b).** Source -> dataset -> parents is D7's (immutable manifests: each sample
names its source and grant ref; a derived version names its parents and copies its samples'
ids), and a run or artifact names its dataset. What D7 does not hold is the trace behind a
sample: `lab/<provider>/lineage/samples/<sample id>.json` (grantor, request, grant, model,
digest, `content_until` = T3's content bound) and a per-request marker
`lab/<provider>/lineage/traces/<grantor>/<request>/<sample id>`, all write-once; the bound
is also D7's (0041 `lab_sample_bounds`, written before the version is published).

**Denial (N3.c).** `permitted` is the one gate every dataset read, derivation, export, part
read, queued job and external submission re-checks: D7's samples under a grant current NOW
for the purpose, minus tombstoned samples, minus trace samples past `content_until` (so
expiry is denied the instant it passes, with no job in between) - 0041's
`lab_permitted_samples` on D7's clock, less the bounds the caller's `now` has passed. A
tombstone (0041 `lab_sample_tombstones`, WR-N3-5: reason and D7's time, never content;
`restrictions` is `PgSampleRestrictions`) is permanent - a later re-grant does not resurrect
the sample. The object tombstones of before WR-N3-5 move once with `backfill`. `tombstone` is the push (a T3
deletion or an L2 revocation hook, wiring); `reconcile` is the pull that finds the rest
(grant no longer current, a newer version of it dropping the entry's model or one of its
categories (`grant_narrowed`), request deleted, content expired) and, only for deletion and
expiry, then deletes the sample copies: logical denial first, physical purge after
retention. Nothing is claimed about data already delivered: `export_evidence` lists an
export's items now denied and says they were not recalled, and no trained model is
unlearned.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol, Sequence

from ...contracts import errors
from ...contracts.lab import records as lab
from ...contracts.v2.records import DataCategory, DataPurpose
from ...media.fetch import digest_of
from ...state.lab_content import PgSampleRestrictions
from ...state.lab_data import grant_ref
from .. import acting_provider
from ..imports import sample_id, sample_key, write_once

MAX_SELECT = 500
PAGE = 100
TRACE_FORMAT = "infrx.trace_sample.1"
PURGED_BY_RETENTION = ("deleted", "content_expired")
NOT_RECALLED = ("items already read from this export were delivered and are not recalled; "
                "no model trained on them is unlearned; future reads exclude them")


class Content(Protocol):
    """C2's content port (the content lane; WR-N3-1): refs bound to grant, recipient and
    expiry; `read` fails closed with NotFound / Forbidden / Gone, even for an issued ref."""

    async def sign(self, *, provider_org_id: str, grantor_org_id: str, request_id: str,
                   grant_id: str, model_id: str) -> str: ...

    async def read(self, ref: str, *, provider_org_id: str) -> dict: ...


Feedback = Callable[[str, str], Awaitable[Sequence[Any]]]   # (org, request) -> D6F rows
ModelOf = Callable[[Any], Awaitable[str | None]]   # T3 row -> the grant's model id (WR-N3-4)


@dataclass(frozen=True)
class Selected:
    dataset_ref: str
    source_ref: str
    samples: int
    omitted: list[dict] = field(default_factory=list)


def _base(provider: str) -> str:
    return f"lab/{provider}/lineage"


def _id(key: str) -> str:
    return key.rsplit("/", 1)[1].removesuffix(".json")


def restrictions_of(store):
    """0041's tombstones and bounds beside D7's rows: a store that carries them (the fakes),
    else the real store's own connection (WR-DS5-1: `PgLabDataStore.restrictions`)."""
    found = getattr(store, "restrictions", None)
    return found if found is not None else PgSampleRestrictions(store._connect)


async def select(*, access, retention, content: Content, feedback: Feedback,
                 model_of: ModelOf, store, objects,
                 user_id: str, provider_org_id: str, grantor_org_id: str, model_id: str,
                 selection_id: str, dataset_id: str, version: int, created_at: str,
                 request_ids: list[str], actor: str) -> Selected:
    """Publish the permitted requests as dataset `dataset_id`@`version` (every sample in
    train: `versions.derive` places them)."""
    if not 1 <= len(request_ids) <= MAX_SELECT or len(set(request_ids)) != len(request_ids):
        raise errors.InvalidRequest(f"a selection is 1..{MAX_SELECT} distinct requests")
    await acting_provider(access, user_id, provider_org_id)
    gate = dict(user_id=user_id, provider_org_id=provider_org_id, grantor_org_id=grantor_org_id,
                model_id=model_id, purpose=DataPurpose.provider_sharing)
    grant = await access.authorize_content(category=DataCategory.request_content, **gate)
    await access.authorize_content(category=DataCategory.response_content, **gate)
    try:
        await access.authorize_content(category=DataCategory.feedback, **gate)
        joined = True
    except errors.Forbidden:
        joined = False
    base, now = _base(provider_org_id), retention.clock()
    samples, omitted, bounds = [], [], {}
    for request in sorted(request_ids):
        rows = await retention.find_traces(grantor_org_id, request)
        if not rows:
            omitted.append({"request_id": request, "reason": "missing_projection"})
            continue
        if {await model_of(r) for r in rows} != {model_id}:
            omitted.append({"request_id": request, "reason": "model_not_granted"})
            continue
        started = min(r.started_at for r in rows)
        if not retention.content_live(started, now):
            omitted.append({"request_id": request, "reason": "content_expired"})
            continue
        try:
            ref = await content.sign(provider_org_id=provider_org_id, request_id=request,
                                     grantor_org_id=grantor_org_id, grant_id=grant.grant_id,
                                     model_id=model_id)
            document = await content.read(ref, provider_org_id=provider_org_id)
        except (errors.NotFound, errors.Forbidden, errors.Gone):
            omitted.append({"request_id": request, "reason": "content_denied"})
            continue
        ended = [r.completed_at for r in rows if r.completed_at is not None]
        corrections = [{k: v for k, v in f.model_dump(mode="json").items()
                        if k in ("feedback_id", "name", "value", "comment", "author_role",
                                 "channel", "created_at")}
                       for f in await feedback(grantor_org_id, request)] if joined else []
        data = lab.canonical({
            "modality": "structured", "content": document, "original": document,
            "annotation": {"method": "imported", "method_version": TRACE_FORMAT},
            "trace": {"grantor_org_id": grantor_org_id, "request_id": request,
                      "started_at": started.isoformat(),
                      "completed_at": max(ended).isoformat() if ended else None},
            "corrections": corrections})
        digest = digest_of(data)
        sid = sample_id(dataset_id, digest)
        bounds[sid] = started + timedelta(days=retention.content_days)
        await write_once(objects, sample_key(provider_org_id, digest), data)
        await write_once(objects, f"{base}/samples/{sid}.json", lab.canonical({
            "sample_id": sid, "selection_id": selection_id, "grantor_org_id": grantor_org_id,
            "request_id": request, "grant_id": grant.grant_id, "model_id": model_id,
            "categories": ["request_content", "response_content"]
            + (["feedback"] if corrections else []), "content_digest": digest,
            "content_until": bounds[sid].isoformat()}))
        await write_once(objects, f"{base}/traces/{grantor_org_id}/{request}/{sid}", b"{}")
        samples.append({"sample_id": sid, "content_digest": digest, "group_key": request})
    if not samples:
        raise errors.InvalidRequest("no selected request is permitted: nothing to publish")
    await restrictions_of(store).bound(bounds, provider_org_id=provider_org_id)
    whole = hashlib.sha256(lab.canonical(sorted(
        [s["group_key"], s["content_digest"]] for s in samples))).hexdigest()
    source_ref = await store.register_source(
        provider_org_id=provider_org_id, source_id=selection_id, actor=actor,
        content_digest=f"sha256:{whole}", grant_ref=grant_ref(grant))
    samples.sort(key=lambda s: s["sample_id"])
    ref = await store.publish({
        "schema": "lab.dataset_manifest.1", "provider_org_id": provider_org_id,
        "dataset_id": dataset_id, "version": version, "created_at": created_at,
        "derivation": "import", "parent_refs": [],
        "samples": [{"modality": "structured", "source_ref": source_ref,
                     "grant_ref": grant_ref(grant), **s} for s in samples],
        "splits": {"train": [s["sample_id"] for s in samples], "validation": [], "holdout": []}},
        provider_org_id=provider_org_id, actor=actor)
    return Selected(dataset_ref=ref, source_ref=source_ref, samples=len(samples),
                    omitted=omitted)


async def trace_of(objects, *, provider_org_id: str, sample_id: str) -> dict | None:
    """The trace lineage entry of a sample, or None for a sample no trace is behind."""
    data = await objects.get(f"{_base(provider_org_id)}/samples/{sample_id}.json")
    return None if data is None else json.loads(data)


# --- denial --------------------------------------------------------------------------------
async def _expired(objects, provider: str, sample_ids, now: datetime) -> dict:
    """The trace samples among `sample_ids` whose content bound the caller's `now` passed.
    ponytail: lists the provider's trace samples per call; a bounds read in 0041 when
    datasets outgrow a listing per read."""
    traced = {_id(k) for k in await objects.keys(f"{_base(provider)}/samples/")} & set(sample_ids)
    out = {}
    for sid in sorted(traced):
        entry = await trace_of(objects, provider_org_id=provider, sample_id=sid)
        if now >= datetime.fromisoformat(entry["content_until"]):
            out[sid] = "content_expired"
    return out


async def blocked(objects, *, provider_org_id: str, sample_ids, now: datetime, restrictions,
                  dataset_ref: str) -> dict:
    """sample id -> why it is denied now: 0041's answer for the dataset (the tombstone's
    reason, or `content_expired` on D7's clock), else `content_expired` at the caller's `now`."""
    wanted = set(sample_ids)
    out = {s: r for s, r in (await restrictions.blocked(
        dataset_ref, provider_org_id=provider_org_id)).items() if s in wanted}
    return {**await _expired(objects, provider_org_id, wanted - set(out), now), **out}


async def permitted(store, objects, dataset_ref: str, *, provider_org_id: str, purpose: str,
                    now: datetime) -> set[str]:
    """The gate: D7's samples under a grant current now for `purpose`, less the denied."""
    allowed = set(await restrictions_of(store).permitted(
        dataset_ref, provider_org_id=provider_org_id, purpose=purpose))
    return allowed - set(await _expired(objects, provider_org_id, allowed, now))


async def tombstone(objects, *, provider_org_id: str, grantor_org_id: str,
                    request_id: str | None = None, reason: str, at: datetime,
                    limit: int = PAGE, restrictions) -> dict:
    """The push: tombstone up to `limit` not-yet-tombstoned samples of the grantor (or of
    one request); call again while `more`. 0041 stamps its own time (R7), so `at` is not
    stored."""
    base = _base(provider_org_id)
    prefix = f"{base}/traces/{grantor_org_id}/" + (f"{request_id}/" if request_id else "")
    ids = sorted(_id(k) for k in await objects.keys(prefix))
    for start in range(0, len(ids), limit):
        new = await restrictions.tombstone(ids[start:start + limit],
                                           provider_org_id=provider_org_id, reason=reason)
        if new:
            return {"tombstoned": len(new), "more": start + limit < len(ids)}
    return {"tombstoned": 0, "more": False}


async def reconcile(directory, retention, objects, *, provider_org_id: str,
                    after: str | None = None, limit: int = PAGE, restrictions) -> dict:
    """The pull over `limit` trace samples after the cursor: tombstone a sample whose grant
    is no longer current (L2, on the store clock) or whose grant's current version no longer
    names its model or one of its categories, whose request T3 no longer projects, or
    whose content passed its bound; then, for deletion and expiry only, delete the copy.
    `next` is the cursor of the following page (None at the end)."""
    keys = [k for k in await objects.keys(f"{_base(provider_org_id)}/samples/")
            if after is None or k > after]
    page, report = keys[:limit], {"checked": 0, "tombstoned": [], "purged": 0}
    now, clock = await directory.db_now(), retention.clock()
    for key in page:
        entry = json.loads(await objects.get(key))
        rows = await retention.find_traces(entry["grantor_org_id"], entry["request_id"])
        grant = await directory.current_grant(entry["grantor_org_id"], provider_org_id)
        reason = "deleted" if not rows else "content_expired" if clock >= \
            datetime.fromisoformat(entry["content_until"]) else "grant_not_current" if (
                grant is None or grant.grant_id != entry["grant_id"]
                or not grant.is_current(now)) else "grant_narrowed" if (
                    entry["model_id"] not in grant.model_ids
                    or not set(entry["categories"]) <= set(grant.categories)) else None
        if reason and await restrictions.tombstone([entry["sample_id"]],
                                                   provider_org_id=provider_org_id, reason=reason):
            report["tombstoned"].append({"sample_id": entry["sample_id"], "reason": reason})
        copy = sample_key(provider_org_id, entry["content_digest"])
        if reason in PURGED_BY_RETENTION and await objects.head(copy) is not None:
            await objects.delete(copy)
            report["purged"] += 1
        report["checked"] += 1
    report["next"] = page[-1] if len(keys) > limit else None
    return report


# --- views ---------------------------------------------------------------------------------
async def status(store, objects, dataset_ref: str, *, provider_org_id: str,
                 now: datetime) -> dict:
    """The provenance view of one version: each sample's split, source and grant, the trace
    behind it, and why it is restricted now (None when it is readable)."""
    manifest = await store.resolve(dataset_ref, provider_org_id=provider_org_id)
    ids = [s.sample_id for s in manifest.samples]
    readable = set(await store.accessible_samples(dataset_ref, provider_org_id=provider_org_id,
                                                  purpose="provider_sharing"))
    denied = await blocked(objects, provider_org_id=provider_org_id, sample_ids=ids, now=now,
                           restrictions=restrictions_of(store), dataset_ref=dataset_ref)
    split = {i: n for n in ("train", "validation", "holdout") for i in getattr(manifest.splits, n)}
    samples = []
    for s in manifest.samples:
        trace = await trace_of(objects, provider_org_id=provider_org_id, sample_id=s.sample_id)
        samples.append({
            "sample_id": s.sample_id, "split": split[s.sample_id], "source_ref": s.source_ref,
            "grant_ref": s.grant_ref, "trace": trace and {k: trace[k] for k in (
                "grantor_org_id", "request_id", "content_until")},
            "restricted": denied.get(s.sample_id) or (
                None if s.sample_id in readable else "grant_not_current")})
    return {"dataset_ref": dataset_ref, "parent_refs": manifest.parent_refs,
            "samples": samples}


async def export_evidence(objects, *, provider_org_id: str, export_id: str,
                          now: datetime, restrictions) -> dict:
    """Reconciliation evidence for an export already made: its items denied now. Sample
    ids and reasons only - never content - and an honest `recalled: False`."""
    record = json.loads(await objects.get(f"lab/{provider_org_id}/exports/{export_id}/"
                                          f"export.json") or b"null")
    if record is None:
        raise errors.NotFound(f"no finished export {export_id}")
    shipped = []
    for part in record["parts"]:
        shipped += [json.loads(line)["sample_id"]
                    for line in (await objects.get(part["key"]) or b"").splitlines()]
    denied = await blocked(objects, provider_org_id=provider_org_id, sample_ids=shipped,
                           now=now, restrictions=restrictions, dataset_ref=record["dataset_ref"])
    return {"export_id": export_id, "dataset_ref": record["dataset_ref"],
            "delivered_from": record["created_at"], "recalled": False, "note": NOT_RECALLED,
            "affected": [{"sample_id": s, "reason": r} for s, r in sorted(denied.items())]}


# --- WR-N3-5 -------------------------------------------------------------------------------
async def backfill(objects, *, provider_org_id: str, restrictions) -> dict:
    """The one-shot move of the object-era restrictions into 0041: each
    `tombstones/<id>.json` (its reason; 0041 stamps the time) and each trace entry's
    `content_until`. Idempotent: a tombstone keeps its first reason, the same bound replays
    (another is `IdempotencyConflict`). ponytail: one call per reason and one for every
    bound; pages when a provider's trace samples outgrow one request."""
    base, reasons = _base(provider_org_id), {}
    for key in await objects.keys(f"{base}/tombstones/"):
        reasons.setdefault(json.loads(await objects.get(key))["reason"], []).append(_id(key))
    moved = [s for reason, ids in sorted(reasons.items()) for s in await restrictions.tombstone(
        ids, provider_org_id=provider_org_id, reason=reason)]
    bounds = {_id(k): datetime.fromisoformat(json.loads(await objects.get(k))["content_until"])
              for k in await objects.keys(f"{base}/samples/")}
    await restrictions.bound(bounds, provider_org_id=provider_org_id)
    return {"stones": sum(map(len, reasons.values())), "tombstoned": len(moved),
            "bounded": len(bounds)}
