"""N2: reproducible dataset versions - deterministic, leakage-trapped splits over a frozen
holdout - and bounded, rights-checked, resumable exports.

**Versions are D7 records.** A version is a `lab.dataset_manifest.1` published through D7:
content-addressed (the ref is the digest of its canonical bytes), one set of bytes per
(dataset, version), never edited. `derive` publishes a NEW version from a frozen `base`
version plus the samples of `add`ed datasets (N1 imports or other versions); it never
changes a parent.

**Splits.** Samples are related when they share a group key (episode/session/customer, set
at import), a clip (the same media digest, whatever the span) or a text equal up to case and
spacing; related samples form one family, and a family is placed whole. A base sample keeps
its split; a new sample joins its family's base split, except the holdout, which is frozen:
a new sample related to a holdout sample is omitted (`related_to_holdout`) and an unrelated
one is placed by `SplitPolicy` over train/validation only. Without a base a family is placed
by `sha256(seed "\\n" its least group key)` over the policy's train/validation/holdout
basis points - the same inputs, policy and seed give the same version and split digest.
A base whose declared splits already put one family in two splits is refused
(`LeakRefused`, naming every leak). A family spanning several group keys is returned for
near-duplicate review. A repeated content digest is omitted (`duplicate`), so a re-import
never changes the holdout.

**Rights at every read (R160).** Derivation reads through the access gate (the grant's
current version for `provider_sharing`) and omits what it may no longer read
(`grant_not_current`) - yet a base sample it may no longer read still anchors its family's
base split, so a new relative of an unreadable holdout sample stays out; an export reads
through the export gate (`training`) and never ships the holdout. A published manifest confers nothing: `read_part` re-reads the gate, so an
export made before a revocation stops serving the revoked items.

**Exports** live at `lab/<provider>/exports/<export id>/`: write-once parts of `part_items`
JSONL items (sample id, split, digests, source and grant refs, the redacted content record),
then `export.json` naming the manifest hash, schema, purpose, parts and their digests, every
omitted sample with its reason, each source's licence/restrictions/grant (per-source
lineage), the redacted keys, and `expires_at` (TTL 1 s..7 days). A redacted key is a
dotted path in the import spec's syntax, removed from the original row and from the content
re-read at the spec's content path; a redaction that would remove the content itself is
refused. A rerun of an unfinished
export reuses finished parts and refuses (`Conflict`) parts its inputs no longer produce;
a finished one replays. `cancel` stops it for good; reads after expiry or cancellation are
`Gone`.

The caller's `provider_org_id` is server-derived: `datasets.acting_provider` (the L2
membership check, WR-N-2) at the route.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ...contracts import errors
from ...contracts.ids import UUID_RE
from ...contracts.lab import records as lab
from ..imports import _MISSING, SPLITS, _get, sample_key, spec_key, write_once

MAX_EXPORT_TTL_S = 7 * 86_400
PART_ITEMS = 1000


@dataclass(frozen=True)
class SplitPolicy:
    seed: int
    train_bp: int = 8000
    validation_bp: int = 1000                  # the holdout is the rest of 10000

    def __post_init__(self) -> None:
        if min(self.train_bp, self.validation_bp) < 0 or \
                self.train_bp + self.validation_bp > 10_000:
            raise errors.InvalidRequest("split basis points are 0..10000 in all")

    def place(self, key: str, *, frozen_holdout: bool) -> str:
        bucket = int(hashlib.sha256(f"{self.seed}\n{key}".encode()).hexdigest()[:8], 16) % 10_000
        if frozen_holdout:
            bucket %= self.train_bp + self.validation_bp
        return "train" if bucket < self.train_bp else \
            "validation" if bucket < self.train_bp + self.validation_bp else "holdout"


@dataclass(frozen=True)
class Derived:
    dataset_ref: str
    split_digest: str
    omitted: list[dict] = field(default_factory=list)
    review: list[list[str]] = field(default_factory=list)


class LeakRefused(errors.Conflict):
    def __init__(self, leaks: list[dict]) -> None:
        self.leaks = leaks
        super().__init__(f"{len(leaks)} related famil(ies) span two splits")


def near_key(body: dict) -> str | None:
    """What makes two samples the same material: one clip, or one text up to case/spacing."""
    if body.get("media_digest"):
        return "media:" + body["media_digest"]
    if isinstance(body.get("content"), str):
        return "text:" + " ".join(body["content"].casefold().split())
    return None


def split_digest(splits: dict) -> str:
    return "sha256:" + hashlib.sha256(lab.canonical(splits)).hexdigest()


async def _body(objects, provider: str, digest: str) -> dict:
    data = await objects.get(sample_key(provider, digest))
    if data is None:
        raise errors.NotFound(f"no content object for {digest}")
    return json.loads(data)


async def derive(store, objects, *, provider_org_id: str, actor: str, dataset_id: str,
                 version: int, created_at: str, policy: SplitPolicy, base: str | None = None,
                 add=()) -> Derived:
    parents = list(dict.fromkeys(([base] if base else []) + list(add)))   # none: F3 refuses
    if base and not policy.train_bp + policy.validation_bp:
        raise errors.InvalidRequest("over a frozen holdout, new samples need train/validation")
    omitted, kept, placed, digests = [], [], {}, set()   # placed: base sample id -> its split
    anchors = []            # base samples no longer readable: left out, their splits still bind
    for ref in parents:
        manifest = await store.resolve(ref, provider_org_id=provider_org_id)
        readable = set(await store.accessible_samples(ref, provider_org_id=provider_org_id,
                                                      purpose="provider_sharing"))
        where = {i: n for n in SPLITS for i in getattr(manifest.splits, n)}
        for sample in sorted(manifest.samples, key=lambda s: (s.content_digest, s.sample_id)):
            if sample.sample_id not in readable:
                omitted.append({"sample_id": sample.sample_id, "reason": "grant_not_current"})
                if ref == base:
                    anchors.append(sample)
            elif sample.content_digest in digests:
                omitted.append({"sample_id": sample.sample_id, "reason": "duplicate"})
            else:
                kept.append(sample)
                digests.add(sample.content_digest)
            if ref == base:
                placed[sample.sample_id] = where[sample.sample_id]
    # families: union of samples sharing a group key or a near key
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = x = parent[parent[x]]         # path halving: near-linear families
        return x
    # an anchor's content is read only to relate new samples to it (so its holdout stays
    # frozen); it is never published
    for sample in kept + anchors:
        near = near_key(await _body(objects, provider_org_id, sample.content_digest))
        for label in ("g:" + sample.group_key, *(("n:" + near,) if near else ())):
            parent[find(label)] = find(sample.sample_id)
    families: dict[str, list] = {}
    for sample in kept + anchors:
        families.setdefault(find(sample.sample_id), []).append(sample)
    leaks, review, split = [], [], {}
    for members in sorted(families.values(), key=lambda f: min(s.sample_id for s in f)):
        ids = sorted(s.sample_id for s in members)
        fixed = {placed[i] for i in ids if i in placed}
        if len(fixed) > 1:
            leaks.append({"samples": ids, "splits": sorted(fixed)})
            continue
        if len({s.group_key for s in members}) > 1:
            review.append(ids)
        target = fixed.pop() if fixed else policy.place(
            min(s.group_key for s in members), frozen_holdout=base is not None)
        for i in ids:
            if target == "holdout" and base is not None and i not in placed:
                omitted.append({"sample_id": i, "reason": "related_to_holdout"})
            else:
                split[i] = target
    if leaks:
        raise LeakRefused(leaks)
    samples = sorted((s for s in kept if s.sample_id in split), key=lambda s: s.sample_id)
    splits = {n: [s.sample_id for s in samples if split[s.sample_id] == n] for n in SPLITS}
    ref = await store.publish({
        "schema": "lab.dataset_manifest.1", "provider_org_id": provider_org_id,
        "dataset_id": dataset_id, "version": version, "created_at": created_at,
        "derivation": "derive", "parent_refs": parents,
        "samples": [s.model_dump(exclude_none=True) for s in samples], "splits": splits},
        provider_org_id=provider_org_id, actor=actor)
    return Derived(dataset_ref=ref, split_digest=split_digest(splits), omitted=omitted,
                   review=review)


# --- exports -----------------------------------------------------------------------------------
def _base(provider: str, export_id: str) -> str:
    if not isinstance(export_id, str) or not UUID_RE.fullmatch(export_id):
        raise errors.InvalidRequest("an export id is a lowercase UUID")
    return f"lab/{provider}/exports/{export_id}"


async def _live(objects, base: str, export_id: str) -> None:
    if await objects.head(f"{base}/cancelled") is not None:
        raise errors.Gone(f"export {export_id} was cancelled")


def _drop(node, path: list[str]):
    """`node` without the value at `path` (a no-op where the path is absent)."""
    if not isinstance(node, dict) or path[0] not in node:
        return node
    if len(path) == 1:
        return {k: v for k, v in node.items() if k != path[0]}
    return {**node, path[0]: _drop(node[path[0]], path[1:])}


def _redacted(body: dict, keys: list[str], content_path: str | None) -> dict:
    """Each key is a dotted path in the import spec's syntax, removed from the original
    row; the content is re-read from what is left at the spec's content path, so a
    structured content loses it too. Removing the content itself is refused."""
    if not keys:
        return body
    original = body["original"]
    for key in keys:
        original = _drop(original, key.split("."))
    content = _get(original, content_path) if content_path else _MISSING
    if content is _MISSING:
        raise errors.InvalidRequest("a redaction cannot remove a sample's content")
    return {**body, "original": original, "content": content}


async def export(store, objects, *, provider_org_id: str, dataset_ref: str, export_id: str,
                 now: datetime, ttl_s: int, redact=(), part_items: int = PART_ITEMS) -> dict:
    """The export record (finished, or replayed); `Gone` once cancelled."""
    base = _base(provider_org_id, export_id)
    if not 1 <= ttl_s <= MAX_EXPORT_TTL_S:
        raise errors.InvalidRequest(f"an export lives 1..{MAX_EXPORT_TTL_S} s")
    await _live(objects, base, export_id)
    done = await objects.get(f"{base}/export.json")
    if done is not None:
        record = json.loads(done)
        if record["dataset_ref"] != dataset_ref:
            raise errors.Conflict(f"export {export_id} is of another dataset")
        return record
    manifest = await store.resolve(dataset_ref, provider_org_id=provider_org_id)
    allowed = set(await store.accessible_samples(dataset_ref, provider_org_id=provider_org_id,
                                                 purpose="training"))
    where = {i: n for n in SPLITS for i in getattr(manifest.splits, n)}
    keys = sorted(set(redact))
    items, omitted, sources, paths = [], [], {}, {}      # paths: source -> its content path
    for sample in sorted(manifest.samples, key=lambda s: s.sample_id):
        reason = "holdout" if where[sample.sample_id] == "holdout" else \
            None if sample.sample_id in allowed else "grant_not_current"
        if reason:
            omitted.append({"sample_id": sample.sample_id, "reason": reason})
            continue
        if sample.source_ref not in sources:
            spec = await objects.get(spec_key(provider_org_id,
                                              lab.REF_RE.fullmatch(sample.source_ref).group(3)))
            spec = json.loads(spec) if spec is not None else {}
            paths[sample.source_ref] = spec.get("fields", {}).get("content")
            sources[sample.source_ref] = {
                "grant_ref": sample.grant_ref, "license": spec.get("license"),
                "use_restrictions": spec.get("use_restrictions"),
                "ownership": spec.get("ownership")}
        body = await _body(objects, provider_org_id, sample.content_digest)
        items.append(lab.canonical({
            "sample_id": sample.sample_id, "split": where[sample.sample_id],
            "content_digest": sample.content_digest, "source_ref": sample.source_ref,
            "grant_ref": sample.grant_ref,
            "record": _redacted(body, keys, paths[sample.source_ref])}))
    parts = []
    for n, start in enumerate(range(0, len(items), part_items)):
        data = b"".join(item + b"\n" for item in items[start:start + part_items])
        key = f"{base}/part-{n:06d}.jsonl"
        await write_once(objects, key, data, "application/x-ndjson")
        parts.append({"key": key, "sha256": hashlib.sha256(data).hexdigest(),
                      "items": len(items[start:start + part_items])})
    record = {
        "format": "infrx.dataset_export.1", "export_id": export_id, "dataset_ref": dataset_ref,
        "manifest_sha256": dataset_ref.split("@sha256:")[1], "schema": manifest.schema_id,
        "purpose": "training", "redacted": keys, "parts": parts,
        "content_sha256": hashlib.sha256(lab.canonical([p["sha256"] for p in parts])).hexdigest(),
        "omitted": omitted, "sources": sources, "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_s)).isoformat()}
    await objects.put_if_absent(f"{base}/export.json", lab.canonical(record), "application/json")
    return json.loads(await objects.get(f"{base}/export.json"))


async def cancel(objects, *, provider_org_id: str, export_id: str) -> None:
    await objects.put_if_absent(f"{_base(provider_org_id, export_id)}/cancelled", b"cancelled",
                                "text/plain")


async def read_part(store, objects, *, provider_org_id: str, export_id: str, part: int,
                    now: datetime) -> bytes:
    """One part's items whose grant still permits training NOW."""
    base = _base(provider_org_id, export_id)
    await _live(objects, base, export_id)
    done = await objects.get(f"{base}/export.json")
    if done is None:
        raise errors.NotFound(f"no finished export {export_id}")
    record = json.loads(done)
    if now >= datetime.fromisoformat(record["expires_at"]):
        raise errors.Gone(f"export {export_id} expired")
    if not 0 <= part < len(record["parts"]):
        raise errors.NotFound(f"export {export_id} has no part {part}")
    data = await objects.get(record["parts"][part]["key"]) or b""
    allowed = set(await store.accessible_samples(record["dataset_ref"],
                                                 provider_org_id=provider_org_id,
                                                 purpose="training"))
    return b"".join(line + b"\n" for line in data.splitlines()
                    if json.loads(line)["sample_id"] in allowed)
