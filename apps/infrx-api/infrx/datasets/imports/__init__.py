"""N1: import a provider's benchmark or exported annotations (JSONL + an import spec) as an
immutable Lab dataset, without the provider replacing its pipeline or installing ours.

**The spec** (`infrx.dataset_import.1`, strict) names the provider, an import id, the dataset
id/version it publishes, the modality, the licence and use restrictions, the D7 grant ref the
source registers under, the annotation method/version, and dotted field paths (object keys) into each row
(`content`, and optionally `group`, `split`; finite video also `media`, `start`, `end` in
`clock_unit` ms or s). Only provider-owned data is importable until P-09.

**Streaming and bounds.** The body is read in whatever pieces arrive; a row over
`MAX_ROW_BYTES` is never buffered past the bound (it is quarantined), the whole upload stops
at `MAX_REQUEST_BYTES`, a clip over `MAX_MEDIA_BYTES` is refused from its description,
unread. A media path is a relative key inside this import's own bundle prefix (no `..`, no
scheme, no absolute path, so no other provider's object); an http(s) source goes through M's
SSRF-hardened `MediaFetcher`.

**Quarantine.** A bad row is a `{line, reason, detail}` diagnostic (`REASONS`); a repeated row
is a dedup candidate naming the first line; a declared group in two splits is refused at the
later row. Publication is refused (`ImportRejected`, carrying the report) while any row was
rejected, unless the caller accepts the rejects - never silently omitted.

**Resumable, all-or-nothing.** Every write is write-once under
`lab/<provider>/imports/<import id>/`: the spec, then fixed-size chunks of lines, each
holding its rows' outcomes and the digest of the lines it covers; each accepted row's
content object (`lab/<provider>/samples/<digest>`: the mapped content, the original row, the
annotation method, the span and clip digest) is written before its chunk. A rerun reuses a
chunk whose lines are unchanged and refuses (`Conflict`) one whose lines changed, so a
replayed upload is the same dataset and changed bytes never redefine it. Only after the
last line: the source (the upload's digest, `source_id` = the import id) is registered under
the grant, then the manifest is published in D7's one transaction - a crash anywhere before
that publishes nothing.

The caller's `provider_org_id` is server-derived: `datasets.acting_provider` (the L2 port's
membership check, WR-N-2) at the route; D7 re-checks the grant.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Any, AsyncIterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ...contracts import errors
from ...contracts.lab import records as lab
from ...contracts.limits import DEFAULTS, PilotSettings
from ...media.fetch import ALLOWED_MIME, MediaFetcher, digest_of

FORMAT = "infrx.dataset_import.1"
MAX_ROW_BYTES = 1 << 20
CHUNK_ROWS = 500
SPLITS = ("train", "validation", "holdout")
REASONS = ("too_large", "not_json", "not_object", "missing_field", "bad_content", "bad_group",
           "bad_split", "invalid_span", "bad_media_path", "media_missing", "media_refused",
           "unsupported_type", "duplicate", "split_conflict")
# A bundle key segment cannot start with a dot, so `.` and `..` are not segments.
PATH_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}(/[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}){0,15}")
_MISSING = object()


def bundle_key(provider: str, import_id: str, path: str) -> str:
    return f"lab/{provider}/imports/{import_id}/bundle/{path}"


def sample_key(provider: str, digest: str) -> str:
    return f"lab/{provider}/samples/{digest.removeprefix('sha256:')}"


def media_key(provider: str, digest: str) -> str:
    return f"lab/{provider}/media/{digest.removeprefix('sha256:')}"


def sample_id(dataset_id: str, digest: str) -> str:
    """A UUIDv4-shaped `sha256(dataset id \n content digest)`: one sample per content."""
    sid = hashlib.sha256(f"{dataset_id}\n{digest}".encode()).hexdigest()
    return f"{sid[:8]}-{sid[8:12]}-4{sid[13:16]}-8{sid[17:20]}-{sid[20:32]}"


def spec_key(provider: str, import_id: str) -> str:
    return f"lab/{provider}/imports/{import_id}/spec.json"


async def write_once(objects, key: str, data: bytes, content_type: str = "application/json"
                     ) -> None:
    """The same bytes again are a no-op, other bytes a `Conflict`."""
    if not await objects.put_if_absent(key, data, content_type) and \
            await objects.head(key) != digest_of(data):
        raise errors.Conflict(f"{key} already holds other bytes")


# --- the spec ---------------------------------------------------------------------------------
class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


Path = Annotated[str, Field(min_length=1, max_length=200)]


class Fields(_Model):
    content: Path
    group: Path | None = None
    split: Path | None = None
    media: Path | None = None
    start: Path | None = None
    end: Path | None = None


class Method(_Model):
    method: Literal["human", "synthetic", "imported"]
    method_version: lab.Text
    tool: lab.Text | None = None


class ImportSpec(_Model):
    format: Literal[FORMAT]
    provider_org_id: lab.Uuid
    import_id: lab.Uuid
    dataset_id: lab.Uuid
    version: int = Field(ge=1)
    created_at: lab.Ts
    modality: Literal[lab.MODALITIES]
    ownership: Literal["provider_owned"]           # customer content waits for P-09
    license: lab.Text
    use_restrictions: list[lab.Text]
    grant_ref: lab.RefOf("grant")
    clock_unit: Literal["ms", "s"] | None = None
    annotation: Method
    fields: Fields

    @model_validator(mode="after")
    def _spans_for_video_only(self) -> ImportSpec:
        video = (self.fields.media, self.fields.start, self.fields.end, self.clock_unit)
        if [v is not None for v in video] != [self.modality == "finite_video"] * 4:
            raise ValueError("finite video maps media, start and end in a clock unit; "
                             "nothing else does")
        return self


def parse_spec(payload: Any, *, provider_org_id: str) -> ImportSpec:
    """The spec, or `InvalidRequest`; `Forbidden` unless it is the caller's own provider's
    and names a grant to that provider."""
    try:
        spec = ImportSpec.model_validate(payload)
    except ValidationError as refused:
        raise errors.InvalidRequest(f"import spec: {refused.error_count()} error(s): "
                                    f"{refused.errors()[0]['msg']}") from None
    if spec.provider_org_id != provider_org_id:
        raise errors.Forbidden("a provider imports only for itself")
    if lab.REF_RE.fullmatch(spec.grant_ref).group(2) != spec.provider_org_id:
        raise errors.Forbidden("cross_provider_ref: the grant names another provider")
    return spec


# --- one row ------------------------------------------------------------------------------------
class Rejected(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        assert reason in REASONS, reason
        self.reason, self.detail = reason, detail or reason


def _get(row: Any, path: str) -> Any:
    node = row
    for part in path.split("."):
        if not (isinstance(node, dict) and part in node):
            return _MISSING
        node = node[part]
    return node


def _no_constant(name: str):
    raise ValueError(f"{name} is not a JSON number")


def _ms(value: Any, unit: str) -> int | None:
    """Whole milliseconds, exactly (no float round trip), or None: a fraction, a boolean,
    a non-number, or a non-finite value (`1e999` parses to infinity)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    ms = Decimal(str(value)) * (1000 if unit == "s" else 1)
    return int(ms) if ms.is_finite() and ms == ms.to_integral_value() else None


def _row(spec: ImportSpec, raw: bytes | None) -> tuple[dict, dict]:
    """(the row, its checked mapped values) or `Rejected`. No I/O."""
    if raw is None or len(raw) > MAX_ROW_BYTES:
        raise Rejected("too_large", f"a row is at most {MAX_ROW_BYTES} bytes")
    try:
        row = json.loads(raw, parse_constant=_no_constant)
    except (ValueError, RecursionError):
        raise Rejected("not_json") from None
    if not isinstance(row, dict):
        raise Rejected("not_object")
    got = {name: _get(row, path) for name, path in spec.fields if path is not None}
    missing = [name for name, value in got.items() if value is _MISSING]
    if missing:
        raise Rejected("missing_field", f"no {getattr(spec.fields, missing[0])!r}")
    content = got["content"]
    if not (isinstance(content, str) and content.strip() if spec.modality == "text"
            else isinstance(content, (dict, list))):
        raise Rejected("bad_content", f"{spec.modality} content")
    checked = {"content": content}
    if "group" in got:
        group = got["group"]
        if isinstance(group, bool) or not isinstance(group, (str, int)) or group == "":
            raise Rejected("bad_group", "a group is a non-empty string or an integer")
        checked["group"] = str(group)
    if "split" in got:
        if got["split"] not in SPLITS:
            raise Rejected("bad_split", f"a split is one of {SPLITS}")
        checked["split"] = got["split"]
    if spec.modality == "finite_video":
        start, end = _ms(got["start"], spec.clock_unit), _ms(got["end"], spec.clock_unit)
        if start is None or end is None or not 0 <= start < end <= start + lab.MAX_VIDEO_MS:
            raise Rejected("invalid_span", f"whole milliseconds, 0 <= start < end, at most "
                                           f"{lab.MAX_VIDEO_MS} ms long")
        media = got["media"]
        if not isinstance(media, str) or not (media.startswith(("http://", "https://"))
                                              or PATH_RE.fullmatch(media)):
            raise Rejected("bad_media_path", "a relative bundle key or an http(s) URL")
        checked["span_ms"], checked["media"] = [start, end], media
    return row, checked


# --- the import ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class ImportReport:
    accepted: int
    rejected: list[dict] = field(default_factory=list)
    dataset_ref: str | None = None
    source_ref: str | None = None


class ImportRejected(errors.InvalidRequest):
    """Rows were rejected (or none accepted) and the caller did not accept that."""

    def __init__(self, report: ImportReport) -> None:
        self.report = report
        super().__init__(f"{len(report.rejected)} row(s) rejected, {report.accepted} accepted")


async def _lines(pieces: AsyncIterable[bytes], whole, limit: int):
    """(line number, its bytes), streaming: a line never buffers past MAX_ROW_BYTES (it is
    None from there on; one that ends inside the piece it overflows in is `_row`'s to refuse);
    `whole` digests everything read; more than `limit` bytes is `RequestTooLarge`."""
    buf, over, number, total = bytearray(), False, 0, 0
    async for piece in pieces:
        total += len(piece)
        if total > limit:
            raise errors.RequestTooLarge(f"an import is at most {limit} bytes")
        whole.update(piece)
        start = 0
        while (end := piece.find(b"\n", start)) >= 0:
            number += 1
            yield number, None if over else bytes(buf + piece[start:end])
            buf, over, start = bytearray(), False, end + 1
        if not over:
            buf += piece[start:]
            if len(buf) > MAX_ROW_BYTES:
                buf, over = bytearray(), True
    if buf or over:
        yield number + 1, None if over else bytes(buf)


class Importer:
    def __init__(self, store, objects, *, fetcher: MediaFetcher | None = None,
                 limits: PilotSettings = DEFAULTS, chunk_rows: int = CHUNK_ROWS) -> None:
        self.store, self.objects = store, objects       # D7's catalog, M's object port
        self.fetcher = fetcher or MediaFetcher(limits)
        self.limits, self.chunk_rows = limits, chunk_rows

    async def _once(self, key: str, data: bytes) -> None:
        await write_once(self.objects, key, data)

    async def run(self, payload: Any, pieces: AsyncIterable[bytes], *, provider_org_id: str,
                  actor: str, accept_rejects: bool = False) -> ImportReport:
        spec = parse_spec(payload, provider_org_id=provider_org_id)
        base = f"lab/{provider_org_id}/imports/{spec.import_id}"
        await self._once(spec_key(provider_org_id, spec.import_id),
                         lab.canonical(spec.model_dump(mode="json", exclude_none=True)))
        whole, seen, groups = hashlib.sha256(), {}, {}
        accepted, rejected, block = [], [], []

        async def flush() -> None:
            key = f"{base}/staged/{block[0][0] // self.chunk_rows:08d}.json"
            lines = hashlib.sha256(lab.canonical([[n, (raw or b"").hex()] for n, raw in block]))
            stored = await self.objects.get(key)
            if stored is None:
                chunk = await self._stage(spec, block, seen, groups)
                chunk["lines"] = lines.hexdigest()
                await self._once(key, lab.canonical(chunk))
            else:
                chunk = json.loads(stored)
                if chunk["lines"] != lines.hexdigest():
                    raise errors.Conflict(f"import {spec.import_id}: the upload changed "
                                          f"since it was staged")
                for sample in chunk["accepted"]:
                    seen.setdefault(sample["content_digest"], sample["line"])
                    groups.setdefault(sample["group_key"], sample["split"])
            accepted.extend(chunk["accepted"])
            rejected.extend(chunk["rejected"])
            block.clear()

        async for number, raw in _lines(pieces, whole, self.limits.max_request_bytes):
            if block and (number - 1) // self.chunk_rows != block[0][0] // self.chunk_rows:
                await flush()
            block.append((number - 1, raw))
        if block:
            await flush()
        report = ImportReport(accepted=len(accepted), rejected=rejected)
        if not accepted or (rejected and not accept_rejects):
            raise ImportRejected(report)
        source_ref = await self.store.register_source(
            provider_org_id=provider_org_id, source_id=spec.import_id,
            content_digest="sha256:" + whole.hexdigest(), grant_ref=spec.grant_ref, actor=actor)
        samples = sorted(accepted, key=lambda s: s["sample_id"])
        manifest = {
            "schema": "lab.dataset_manifest.1", "provider_org_id": provider_org_id,
            "dataset_id": spec.dataset_id, "version": spec.version,
            "created_at": spec.created_at, "derivation": "import", "parent_refs": [],
            "samples": [{"sample_id": s["sample_id"], "modality": spec.modality,
                         "source_ref": source_ref, "grant_ref": spec.grant_ref,
                         "content_digest": s["content_digest"], "group_key": s["group_key"],
                         **({"duration_ms": s["duration_ms"]} if "duration_ms" in s else {})}
                        for s in samples],
            "splits": {name: [s["sample_id"] for s in samples if s["split"] == name]
                       for name in SPLITS}}
        ref = await self.store.publish(manifest, provider_org_id=provider_org_id, actor=actor)
        return ImportReport(accepted=len(accepted), rejected=rejected, dataset_ref=ref,
                            source_ref=source_ref)

    async def _stage(self, spec: ImportSpec, block, seen: dict, groups: dict) -> dict:
        """One chunk's outcomes; each accepted row's content object is written first."""
        accepted, rejected = [], []
        for index, raw in block:
            line = index + 1
            if raw is not None and not raw.strip():
                continue                                   # a blank line is not a row
            try:
                row, checked = _row(spec, raw)
                body = {"modality": spec.modality, "content": checked["content"], "original": row,
                        "annotation": spec.annotation.model_dump(exclude_none=True)}
                if "media" in checked:
                    body["media_digest"] = await self._media(spec, checked["media"])
                    body["span_ms"] = checked["span_ms"]
                data = lab.canonical(body)
                digest = digest_of(data)
                if digest in seen:
                    raise Rejected("duplicate", f"the same content as line {seen[digest]}")
                group = checked.get("group", digest)
                split = checked.get("split", "train")
                if groups.get(group, split) != split:
                    raise Rejected("split_conflict", f"group {group!r} is in {groups[group]}")
            except Rejected as bad:
                rejected.append({"line": line, "reason": bad.reason, "detail": bad.detail})
                continue
            await self._once(sample_key(spec.provider_org_id, digest), data)
            seen[digest], groups[group] = line, split
            sample = {"line": line, "content_digest": digest, "group_key": group, "split": split,
                      "sample_id": sample_id(spec.dataset_id, digest)}
            if "span_ms" in checked:
                sample["duration_ms"] = checked["span_ms"][1] - checked["span_ms"][0]
            accepted.append(sample)
        return {"accepted": accepted, "rejected": rejected}

    async def _media(self, spec: ImportSpec, source: str) -> str:
        """The clip's digest, its bytes copied to `media_key`; `Rejected` otherwise."""
        if source.startswith(("http://", "https://")):
            try:
                fetched = await self.fetcher.fetch(source)
            except errors.DomainError as refused:
                raise Rejected("media_refused", getattr(refused, "reason", "refused")) from None
            data, mime = fetched.data, fetched.mime
        else:
            key = bundle_key(spec.provider_org_id, spec.import_id, source)
            described = await self.objects.describe(key)
            if described is None:
                raise Rejected("media_missing", f"no {source!r} in this import's bundle")
            size, mime = described
            if size > self.limits.max_media_bytes:
                raise Rejected("too_large", f"a clip is at most {self.limits.max_media_bytes} "
                                            f"bytes")
            if mime not in ALLOWED_MIME:
                raise Rejected("unsupported_type", f"{mime!r} is not a video type")
            data = await self.objects.get(key)
            if data is None:
                raise Rejected("media_missing", f"no {source!r} in this import's bundle")
        digest = digest_of(data)
        await self.objects.put_if_absent(media_key(spec.provider_org_id, digest), data, mime)
        return digest


def _type(value: Any) -> str:
    return {bool: "boolean", int: "number", float: "number", str: "string", dict: "object",
            list: "array"}.get(type(value), "null")


def preview(payload: Any, head: bytes, *, provider_org_id: str, rows: int = 20) -> dict:
    """The schema preview of an upload's first rows: every top-level field with its JSON
    types, and each row's checked mapped values or refusal. Reads nothing, writes nothing
    (media are not fetched)."""
    spec = parse_spec(payload, provider_org_id=provider_org_id)
    types: dict[str, set[str]] = {}
    shown = []
    for number, raw in enumerate(head.split(b"\n"), 1):
        if len(shown) == rows:
            break
        if not raw.strip():
            continue
        try:
            row, checked = _row(spec, raw)
        except Rejected as bad:
            shown.append({"line": number, "reason": bad.reason})
            continue
        for name, value in row.items():
            types.setdefault(name, set()).add(_type(value))
        shown.append({"line": number, "mapped": checked})
    return {"fields": {name: sorted(t) for name, t in sorted(types.items())}, "rows": shown}
