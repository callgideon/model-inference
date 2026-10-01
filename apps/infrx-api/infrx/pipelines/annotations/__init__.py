"""P1: import, review and export annotation records (PIPELINE-LINEAGE, DATA-SPLIT).

**A label is a D7 record, never edited.** Each imported row becomes one `lab.annotation.1`
published through D7 (content-addressed; its id is derived from the dataset and the row, so a
re-import is the same ref). Its `evidence_ref` is the sample's own source: the original
evidence is linked, never copied or overwritten. What the pipeline said about the label -
annotator, model, prompt, confidence, time spans and its own method word - rides inside the
opaque `label` (`{"value", "provenance", "declared_method"}`), under the record's digest.

**Methods stay distinct.** A pipeline row is `human` or `model`; it is published as
`imported` or `synthetic`, never as `human` and never as ground truth. Only a review by a
current developer-or-above member (server-derived `user_id`, the L2 store's clock) can write
a `human` label: a correction (the original is rejected and kept) or an adjudication of a
disagreement (the disputed labels are rejected or superseded and kept). A row that claims
ground truth, or that the mapping cannot read, or whose sample is missing or no longer
readable, is rejected with its row number and reason - never silently dropped.

**Review state is D8's append-only log** (`LabelLog`, lab-sql, not merged): one event per
idempotency key, for ever. A label enters with its record's state; `review:<ref>` moves it
by F3's annotation machine once (a replay is the same event, another decision a conflict);
`assign:` names a reviewer and the rubric version the review must cite; `adjudicate:` is one
per sample. Rights are read at every call: import and select through the access gate
(`provider_sharing`), export through the training gate - both N3's `lineage.permitted`, so a
re-grant never resurrects a tombstoned sample (R193).

**Supersession is adjudication's alone.** A review accepts or rejects; only `adjudicate`
supersedes. Disagreements and adjudication read the same lineage the export reads: every
live label of a sample of this version, logged under this version or an ancestor; a move
on an ancestor's label is logged under that ancestor.

**Exports are train-only.** An example is a train sample of the dataset whose accepted
labels agree, that the training gate allows now, and that is not a holdout sample (by id,
content digest or group key) of any ancestor version. Everything left out is listed with
its reason. The bytes are write-once, so a rerun is byte-identical and a changed rerun a
`Conflict`; lineage (label refs and methods per sample) is in the export record, with
`expires_at` (TTL 1 s..7 days, N2's bound). `read_export` is the only read: `Gone` once
expired, and only the lines whose sample the training gate allows NOW.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from ...contracts import errors
from ...contracts.ids import UUID_RE
from ...contracts.lab import records as lab
from ...contracts.lab import states
from ...contracts.v2.records import ProviderCapability, ProviderMembership
from ...datasets.lineage import permitted
from ...datasets.imports import sample_key, write_once
from ...datasets.versions import MAX_EXPORT_TTL_S

METHODS = {"human": "imported", "model": "synthetic"}      # pipeline word -> lab method
PROVENANCE = ("annotator", "model", "prompt", "confidence", "spans")
ROW_KEYS = frozenset({"sample_id", "method", "method_version", "label", "ground_truth",
                      *PROVENANCE})
ADAPTERS = ("sft.1", "preference.1")
LIVE = ("submitted", "accepted")


class LabelLog(Protocol):
    """D8's append-only label and review log (lab-sql; the fake is `tests/p/annotations`)."""

    async def append(self, event: dict[str, Any], *, provider_org_id: str) -> dict[str, Any]:
        """The stored event. `event["key"]` is unique per provider for ever: the same body
        again is a replay (the stored event), another body `IdempotencyConflict`."""

    async def events(self, dataset_ref: str, *, provider_org_id: str) -> list[dict[str, Any]]:
        """This provider's events under `dataset_ref`, in append order."""


class Members(Protocol):
    """The L2 `AccessStore` subset a review reads (`PgAccessStore` / `FakeAccessStore`)."""

    async def membership(self, provider_org_id: str, user_id: str) -> ProviderMembership | None: ...

    async def db_now(self) -> datetime: ...


@dataclass(frozen=True)
class Imported:
    accepted: list[str] = field(default_factory=list)      # annotation refs, row order
    rejected: list[dict] = field(default_factory=list)     # {"row", "reason"}


def _uuid(*parts: Any) -> str:
    return str(uuid.UUID(hashlib.sha256(lab.canonical(list(parts))).hexdigest()[:32], version=4))


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _refusal(row: Any, samples: dict, readable: set[str]) -> str | None:
    if not isinstance(row, dict) or row.get("sample_id") not in samples:
        return "missing_evidence"
    if row["sample_id"] not in readable:
        return "grant_not_current"
    if row.get("ground_truth", False) is not False:
        return "forged_ground_truth"
    spans = row.get("spans", [])
    confidence = row.get("confidence", 0)
    if (set(row) - ROW_KEYS or row.get("method") not in METHODS or "label" not in row
            or not isinstance(row.get("method_version"), str) or not row["method_version"]
            or not (_number(confidence) and 0 <= confidence <= 1)
            or not isinstance(spans, list)
            or not all(isinstance(s, list) and len(s) == 2 and all(map(_number, s))
                       and 0 <= s[0] < s[1] for s in spans)):
        return "bad_mapping"
    return None


def _annotation(provider: str, annotation_id: str, dataset_ref: str, sample, *, method: str,
                method_version: str, rubric_ref: str, label: dict, state: str,
                reviewer_id: str | None = None) -> dict:
    return {"schema": "lab.annotation.1", "provider_org_id": provider,
            "annotation_id": annotation_id, "dataset_ref": dataset_ref,
            "sample_id": sample.sample_id, "method": method, "method_version": method_version,
            **({"reviewer_id": reviewer_id} if reviewer_id else {}), "rubric_ref": rubric_ref,
            "evidence_ref": sample.source_ref, "label": label,
            "ground_truth": method == "human", "state": state}


async def import_labels(store, log: LabelLog, *, objects, now: datetime, provider_org_id: str,
                        actor: str, dataset_ref: str, rubric_ref: str, rows) -> Imported:
    """P1.a: every row published as an `imported`/`synthetic` label, or rejected."""
    manifest = await store.resolve(dataset_ref, provider_org_id=provider_org_id)
    samples = {s.sample_id: s for s in manifest.samples}
    readable = await permitted(store, objects, dataset_ref, now=now,
                               provider_org_id=provider_org_id, purpose="provider_sharing")
    done = Imported()
    for n, row in enumerate(rows, 1):
        reason = _refusal(row, samples, readable)
        if reason:
            done.rejected.append({"row": n, "reason": reason})
            continue
        label = {"value": row["label"], "declared_method": row["method"],
                 "provenance": {k: row[k] for k in PROVENANCE if k in row}}
        try:
            ref = await store.publish(_annotation(
                provider_org_id, _uuid("import", dataset_ref, row), dataset_ref,
                samples[row["sample_id"]], method=METHODS[row["method"]],
                method_version=row["method_version"], rubric_ref=rubric_ref, label=label,
                state="submitted"), provider_org_id=provider_org_id, actor=actor)
        except lab.LabRejected:              # e.g. a number the two halves read differently
            done.rejected.append({"row": n, "reason": "bad_mapping"})
            continue
        await log.append({"key": f"label:{ref}", "kind": "label", "dataset_ref": dataset_ref,
                          "sample_id": row["sample_id"], "annotation_ref": ref, "actor": actor},
                         provider_org_id=provider_org_id)
        done.accepted.append(ref)
    return done


# --- P1.b: review, correction, disagreement and adjudication ---------------------------------
async def _may_review(members: Members, provider: str, user_id: str,
                      capability=ProviderCapability.run_evaluation) -> None:
    membership = await members.membership(provider, user_id)
    now = await members.db_now()
    if membership is None or not membership.permits(capability, now, provider):
        raise errors.Forbidden(f"this needs a current member holding {capability}")


def _states(events: list[dict]) -> dict[str, str]:
    """annotation ref -> its state: the record's, then each review move in log order."""
    state: dict[str, str] = {}
    for event in events:
        if event["kind"] == "label":
            state[event["annotation_ref"]] = event.get("state", "submitted")
        elif event["kind"] == "review":
            state[event["annotation_ref"]] = event["decision"]
    return state


def _assigned(events: list[dict], sample_id: str, user_id: str, rubric_ref: str) -> None:
    if not any(e["kind"] == "assign" and e["sample_id"] == sample_id
               and e["reviewer_id"] == user_id and e["rubric_ref"] == rubric_ref
               for e in events):
        raise errors.Forbidden("not assigned to review this sample under this rubric version")


async def assign(log: LabelLog, members: Members, *, provider_org_id: str, user_id: str,
                 dataset_ref: str, sample_id: str, reviewer_id: str, rubric_ref: str) -> dict:
    """An administrator assigns a sample to a reviewer who may review, under one rubric."""
    await _may_review(members, provider_org_id, user_id, ProviderCapability.manage_members)
    await _may_review(members, provider_org_id, reviewer_id)
    return await log.append({
        "key": f"assign:{dataset_ref}:{sample_id}:{reviewer_id}", "kind": "assign",
        "dataset_ref": dataset_ref, "sample_id": sample_id, "reviewer_id": reviewer_id,
        "rubric_ref": rubric_ref, "actor": user_id}, provider_org_id=provider_org_id)


async def _move(log: LabelLog, provider: str, events: list[dict], dataset_ref: str,
                ref: str, sample_id: str, decision: str, actor: str) -> None:
    key = f"{'supersede' if decision == 'superseded' else 'review'}:{ref}"
    if not any(e["key"] == key for e in events):
        states.transition("annotation", _states(events)[ref], decision)
    await log.append({"key": key, "kind": "review", "dataset_ref": dataset_ref,
                      "sample_id": sample_id, "annotation_ref": ref, "decision": decision,
                      "actor": actor}, provider_org_id=provider)


async def _human(store, log: LabelLog, provider: str, dataset_ref: str, record, *,
                 annotation_id: str, value: Any, provenance: dict, user_id: str,
                 rubric_ref: str) -> str:
    """A reviewer's own label: `human`, ground truth, accepted, on the same evidence."""
    manifest = await store.resolve(dataset_ref, provider_org_id=provider)
    sample = next(s for s in manifest.samples if s.sample_id == record.sample_id)
    ref = await store.publish(_annotation(
        provider, annotation_id, dataset_ref, sample, method="human", method_version="review",
        rubric_ref=rubric_ref, label={"value": value, "provenance": provenance},
        state="accepted", reviewer_id=user_id), provider_org_id=provider, actor=user_id)
    await log.append({"key": f"label:{ref}", "kind": "label", "dataset_ref": dataset_ref,
                      "sample_id": record.sample_id, "annotation_ref": ref, "state": "accepted",
                      "actor": user_id}, provider_org_id=provider)
    return ref


async def review(store, log: LabelLog, members: Members, *, provider_org_id: str,
                 user_id: str, dataset_ref: str, annotation_ref: str, decision: str,
                 rubric_ref: str, correction: Any = None) -> str | None:
    """Accept or reject a submitted label once. A correction rejects it and writes the
    reviewer's `human` label (its ref is returned); the original is kept."""
    await _may_review(members, provider_org_id, user_id)
    record = await store.resolve(annotation_ref, provider_org_id=provider_org_id)
    events = await log.events(dataset_ref, provider_org_id=provider_org_id)
    if annotation_ref not in _states(events):
        raise errors.NotFound("no such label in this dataset")
    _assigned(events, record.sample_id, user_id, rubric_ref)
    if decision not in ("accepted", "rejected"):
        raise errors.InvalidRequest("a review accepts or rejects; only an adjudication supersedes")
    if correction is not None and decision != "rejected":
        raise errors.InvalidRequest("a correction rejects the label it corrects")
    await _move(log, provider_org_id, events, dataset_ref, annotation_ref, record.sample_id,
                decision, user_id)
    if correction is None:
        return None
    return await _human(store, log, provider_org_id, dataset_ref, record,
                        annotation_id=_uuid("correct", annotation_ref), value=correction,
                        provenance={"corrects": annotation_ref}, user_id=user_id,
                        rubric_ref=rubric_ref)


async def _values(store, provider: str, refs) -> dict[str, Any]:
    return {ref: (await store.resolve(ref, provider_org_id=provider)).label["value"]
            for ref in refs}


async def _labels(store, log: LabelLog, provider: str, dataset_ref: str
                  ) -> tuple[list[dict], dict[str, tuple[str, str, str]]]:
    """(every event of this version and its ancestors, annotation ref -> (the version it is
    logged under, its sample, its state)) for the labels of this version's samples."""
    versions, _ = await _lineage(store, provider, dataset_ref)
    here = {s.sample_id for s in (await store.resolve(dataset_ref, provider_org_id=provider)
                                  ).samples}
    everything, labels = [], {}
    for version in versions:
        events = await log.events(version, provider_org_id=provider)
        everything += events
        sample = {e["annotation_ref"]: e["sample_id"] for e in events if "annotation_ref" in e}
        for ref, state in _states(events).items():
            if sample[ref] in here:
                labels[ref] = (version, sample[ref], state)
    return everything, labels


async def disagreements(store, log: LabelLog, *, provider_org_id: str,
                        dataset_ref: str) -> dict[str, list[str]]:
    """sample id -> its live (submitted or accepted) labels, where their values differ."""
    _, labels = await _labels(store, log, provider_org_id, dataset_ref)
    live: dict[str, list[str]] = {}
    for ref, (_, sample, state) in labels.items():
        if state in LIVE:
            live.setdefault(sample, []).append(ref)
    out = {}
    for sample, refs in live.items():
        values = await _values(store, provider_org_id, refs)
        if len({json.dumps(v, sort_keys=True) for v in values.values()}) > 1:
            out[sample] = sorted(refs)
    return out


async def adjudicate(store, log: LabelLog, members: Members, *, provider_org_id: str,
                     user_id: str, dataset_ref: str, sample_id: str, value: Any,
                     rubric_ref: str) -> str:
    """One adjudication per sample, by an assigned reviewer who reviewed none of the
    disputed labels: a `human` label; the disputed ones rejected or superseded, kept."""
    await _may_review(members, provider_org_id, user_id)
    events = await log.events(dataset_ref, provider_org_id=provider_org_id)
    _assigned(events, sample_id, user_id, rubric_ref)
    key = f"adjudicate:{dataset_ref}:{sample_id}"
    prior = next((e for e in events if e["key"] == key), None)
    everything, labels = await _labels(store, log, provider_org_id, dataset_ref)
    if prior is None:
        disputed = (await disagreements(store, log, provider_org_id=provider_org_id,
                                        dataset_ref=dataset_ref)).get(sample_id)
        if not disputed:
            raise errors.StateConflict("this sample has no disagreement to adjudicate")
        authors = {(await store.resolve(r, provider_org_id=provider_org_id)).reviewer_id
                   for r in disputed}
        if user_id in authors or any(e["kind"] == "review" and e["actor"] == user_id
                                     and e["annotation_ref"] in disputed for e in everything):
            raise errors.Forbidden("an adjudicator wrote or reviewed none of the disputed labels")
        moves = {r: "superseded" if labels[r][2] == "accepted" else "rejected"
                 for r in disputed}
    else:
        disputed, moves = prior["disputed"], prior["moves"]
    await log.append({"key": key, "kind": "adjudicate", "dataset_ref": dataset_ref,
                      "sample_id": sample_id, "disputed": disputed, "moves": moves,
                      "value": value, "actor": user_id}, provider_org_id=provider_org_id)
    record = await store.resolve(disputed[0], provider_org_id=provider_org_id)
    ref = await _human(store, log, provider_org_id, dataset_ref, record,
                       annotation_id=_uuid("adjudicate", key), value=value,
                       provenance={"adjudicates": disputed}, user_id=user_id,
                       rubric_ref=rubric_ref)
    for old in disputed:                       # logged under the version the label lives on
        version = labels[old][0]
        events = await log.events(version, provider_org_id=provider_org_id)
        await _move(log, provider_org_id, events, version, old, sample_id, moves[old], user_id)
    return ref


async def select(store, *, objects, now: datetime, provider_org_id: str, actor: str,
                 dataset_ref: str, sample_ids, dataset_id: str, version: int,
                 created_at: str) -> str:
    """A new version of the chosen samples that the access gate allows now, each keeping
    its split (so a holdout sample stays holdout)."""
    manifest = await store.resolve(dataset_ref, provider_org_id=provider_org_id)
    readable = await permitted(store, objects, dataset_ref, now=now,
                               provider_org_id=provider_org_id, purpose="provider_sharing")
    keep = set(sample_ids) & readable
    samples = [s for s in manifest.samples if s.sample_id in keep]
    return await store.publish({
        "schema": "lab.dataset_manifest.1", "provider_org_id": provider_org_id,
        "dataset_id": dataset_id, "version": version, "created_at": created_at,
        "derivation": "derive", "parent_refs": [dataset_ref],
        "samples": [s.model_dump(exclude_none=True) for s in samples],
        "splits": {n: [i for i in getattr(manifest.splits, n) if i in keep]
                   for n in ("train", "validation", "holdout")}},
        provider_org_id=provider_org_id, actor=actor)


# --- P1.c: train-only exports -----------------------------------------------------------------
async def _lineage(store, provider: str, dataset_ref: str) -> tuple[list[str], set[str]]:
    """(this version and every ancestor, their holdout sample ids, digests and group keys)."""
    seen, queue, holdout = [], [dataset_ref], set()
    while queue:
        ref = queue.pop(0)
        if ref in seen:
            continue
        seen.append(ref)
        manifest = await store.resolve(ref, provider_org_id=provider)
        queue.extend(manifest.parent_refs)
        ids = set(manifest.splits.holdout)
        for s in manifest.samples:
            if s.sample_id in ids:
                holdout |= {s.sample_id, s.content_digest, "g:" + s.group_key}
    return seen, holdout


def _line(adapter: str, prompt: Any, value: Any) -> dict | None:
    if adapter == "sft.1":
        return {"prompt": prompt, "completion": value}
    if isinstance(value, dict) and set(value) == {"chosen", "rejected"}:
        return {"prompt": prompt, **value}
    return None


async def export(store, log: LabelLog, objects, *, provider_org_id: str, dataset_ref: str,
                 export_id: str, adapter: str, now: datetime, ttl_s: int) -> dict:
    """The export record; its examples at `examples_key`, one JSONL line per sample."""
    if not 1 <= ttl_s <= MAX_EXPORT_TTL_S:
        raise errors.InvalidRequest(f"an export lives 1..{MAX_EXPORT_TTL_S} s")
    if adapter not in ADAPTERS:
        raise errors.InvalidRequest(f"adapter is one of {ADAPTERS}")
    if not isinstance(export_id, str) or not UUID_RE.fullmatch(export_id):
        raise errors.InvalidRequest("an export id is a lowercase UUID")
    manifest = await store.resolve(dataset_ref, provider_org_id=provider_org_id)
    allowed = await permitted(store, objects, dataset_ref, now=now,
                              provider_org_id=provider_org_id, purpose="training")
    _, holdout = await _lineage(store, provider_org_id, dataset_ref)
    _, labels = await _labels(store, log, provider_org_id, dataset_ref)
    accepted: dict[str, list[str]] = {}
    for label, (_, sample, state) in labels.items():
        if state == "accepted":
            accepted.setdefault(sample, []).append(label)
    where = {i: n for n in ("train", "validation", "holdout") for i in getattr(manifest.splits, n)}
    lines, lineage, omitted = [], [], []
    for sample in sorted(manifest.samples, key=lambda s: s.sample_id):
        refs = sorted(accepted.get(sample.sample_id, ()))
        if not refs:
            continue
        records = {r: await store.resolve(r, provider_org_id=provider_org_id) for r in refs}
        values = {json.dumps(r.label["value"], sort_keys=True) for r in records.values()}
        line = None
        if where[sample.sample_id] != "train":
            reason = where[sample.sample_id]
        elif {sample.sample_id, sample.content_digest, "g:" + sample.group_key} & holdout:
            reason = "holdout_descendant"
        elif sample.sample_id not in allowed:
            reason = "grant_not_current"
        elif len(values) > 1:
            reason = "disagreement"
        else:
            body = json.loads(await objects.get(sample_key(provider_org_id,
                                                           sample.content_digest)))
            line = _line(adapter, body["content"], json.loads(values.pop()))
            reason = None if line else "bad_mapping"
        if reason:
            omitted.append({"sample_id": sample.sample_id, "reason": reason})
            continue
        lines.append(lab.canonical(line) + b"\n")
        lineage.append({"sample_id": sample.sample_id, "label_refs": refs,
                        "methods": sorted({r.method for r in records.values()})})
    data = b"".join(lines)
    base = f"lab/{provider_org_id}/label-exports/{export_id}"
    # ponytail: one object per export; N2-style parts when exports outgrow a request.
    await write_once(objects, f"{base}/examples.jsonl", data, "application/x-ndjson")
    record = {"format": "infrx.label_export.1", "export_id": export_id,
              "dataset_ref": dataset_ref, "adapter": adapter, "split": "train",
              "examples_key": f"{base}/examples.jsonl",
              "sha256": hashlib.sha256(data).hexdigest(), "items": len(lines),
              "lineage": lineage, "omitted": omitted, "created_at": now.isoformat(),
              "expires_at": (now + timedelta(seconds=ttl_s)).isoformat()}
    await write_once(objects, f"{base}/export.json", lab.canonical(record))
    return record


async def read_export(store, objects, *, provider_org_id: str, export_id: str,
                      now: datetime) -> bytes:
    """The export's lines whose sample the training gate allows NOW; `Gone` once expired."""
    if not isinstance(export_id, str) or not UUID_RE.fullmatch(export_id):
        raise errors.InvalidRequest("an export id is a lowercase UUID")
    done = await objects.get(f"lab/{provider_org_id}/label-exports/{export_id}/export.json")
    if done is None:
        raise errors.NotFound(f"no label export {export_id}")
    record = json.loads(done)
    if now >= datetime.fromisoformat(record["expires_at"]):
        raise errors.Gone(f"label export {export_id} expired")
    allowed = await permitted(store, objects, record["dataset_ref"], now=now,
                              provider_org_id=provider_org_id, purpose="training")
    data = await objects.get(record["examples_key"]) or b""
    return b"".join(line + b"\n" for line, x in zip(data.splitlines(), record["lineage"])
                    if x["sample_id"] in allowed)
