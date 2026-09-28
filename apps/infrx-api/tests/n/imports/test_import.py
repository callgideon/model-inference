#!/usr/bin/env python3
"""N1 (13-lab-improvement-handoffs §N1; DATA-IMPORT, DATA-RIGHTS): the streaming JSONL +
spec importer over the M object/media ports, publishing through D7's store - here the fake
world of `world.py` (0029's rules in memory); `test_import_pg.py` reruns the publication
scenarios on the real `PgLabDataStore`. Every case is named by a mutant in `mutants.py`.

    uv run --frozen pytest -q tests/n/imports/test_import.py
"""
from __future__ import annotations

import copy
import hashlib
import json

import httpx
import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.contracts.limits import DEFAULTS
from infrx.datasets import imports
from infrx.media.fetch import MediaFetcher
from infrx.media.store import InMemoryObjectStore

from .world import (NEMO, OTHER, Crash, CrashingObjects, FakeLabStore, chunks, fixture,
                    grant_ref, run)

CLIP_A, CLIP_B = b"\x00\x00\x00\x18ftypmp42 dock-01", b"\x00\x00\x00\x18ftypmp42 yard-07"


def world(objects=None, **kw):
    store = FakeLabStore()
    store.add_grant()
    objects = objects if objects is not None else InMemoryObjectStore()
    return store, objects, imports.Importer(store, objects, **kw)


def go(importer, spec, data, *, piece=7, **kw):
    return run(importer.run(spec, chunks(data, piece), provider_org_id=NEMO, actor="dev@nemo",
                            **kw))


def ok(importer, spec, data, **kw):
    """`go` that must succeed: a refusal is the assertion it stands for (R40)."""
    try:
        return go(importer, spec, data, **kw)
    except errors.DomainError as refused:
        raise AssertionError(f"{type(refused).__name__}: {refused}") from None


def crashes(importer, spec, data, **kw) -> None:
    """`go` that must die by `Crash`; a refusal instead is the assertion (R40)."""
    try:
        go(importer, spec, data, **kw)
    except Crash:
        return
    except errors.DomainError as refused:
        raise AssertionError(f"{type(refused).__name__}: {refused}") from None
    raise AssertionError("the run did not die")


def seed_clips(objects, spec) -> None:
    for path, data in (("clips/dock-01.mp4", CLIP_A), ("clips/yard-07.mp4", CLIP_B)):
        objects.seed(imports.bundle_key(NEMO, spec["import_id"], path), data)


def text_spec(**over) -> dict:
    spec, _ = fixture("benchmark")
    return {**spec, **over}


def rows(*items) -> bytes:
    return b"".join((i if isinstance(i, bytes) else json.dumps(i).encode()) + b"\n"
                    for i in items)


def reasons(report) -> list[tuple[int, str]]:
    return [(r["line"], r["reason"]) for r in report.rejected]


def bodies(objects, manifest) -> list[dict]:
    stored = [run(objects.get(imports.sample_key(NEMO, s.content_digest)))
              for s in manifest.samples]
    assert None not in stored, "a published sample has no content object"
    return [json.loads(b) for b in stored]


# --- N1.a/c: the two provider pipeline export examples ------------------------------------
def test_n1_an_owned_benchmark_is_published_through_d7() -> None:
    """Oracle: every row is a sample of the published manifest, split as the provider
    declared and grouped by episode; the source is the uploaded file's digest registered
    under the spec's grant; each sample's content is stored under its digest with the
    original row and the annotation method; a mapping that dropped the split, the group,
    the original or the method fails."""
    spec, data = fixture("benchmark")
    store, objects, importer = world()
    report = ok(importer, spec, data)
    assert (report.accepted, report.rejected) == (5, []), report
    manifest = run(store.resolve(report.dataset_ref, provider_org_id=NEMO))
    assert report.dataset_ref == records.ref_of(manifest.model_dump(by_alias=True,
                                                                    exclude_none=True))
    digest = hashlib.sha256(data).hexdigest()
    assert report.source_ref == f"lab:source:{NEMO}:{spec['import_id']}@sha256:{digest}"
    assert manifest.derivation == "import" and manifest.parent_refs == []
    assert {(s.source_ref, s.grant_ref) for s in manifest.samples} == \
        {(report.source_ref, grant_ref())}
    got = bodies(objects, manifest)
    for sample in manifest.samples:
        stored = run(objects.get(imports.sample_key(NEMO, sample.content_digest)))
        assert "sha256:" + hashlib.sha256(stored).hexdigest() == sample.content_digest
    originals = [json.loads(line) for line in data.splitlines() if line.strip()]
    assert sorted(json.dumps(b.get("original"), sort_keys=True) for b in got) == \
        sorted(json.dumps(o, sort_keys=True) for o in originals), "the original rows"
    assert all(b.get("annotation") == spec["annotation"] for b in got), got
    by_id = {s.sample_id: (s.group_key, b["original"]) for s, b in zip(manifest.samples, got)}
    split_of = {i: n for n in ("train", "validation", "holdout")
                for i in getattr(manifest.splits, n)}
    assert {i: (g, split_of[i]) for i, (g, _) in by_id.items()} == \
        {i: (o["episode"], o["split"]) for i, (_, o) in by_id.items()}, "groups and splits"
    assert all(b["content"] == b["original"]["question"] for b in got)


def test_n1_a_sam_style_video_annotation_export_keeps_spans_and_method() -> None:
    """Oracle: nested mappings reach the clip, the session and the span; seconds become
    integer milliseconds (1.5 s -> 1500); the clip bytes are copied content-addressed and
    named by digest; the 82 s clip is accepted (the cap is inclusive); unmapped splits are
    train; the tool's method and version stay on every sample."""
    spec, data = fixture("sam_export")
    store, objects, importer = world()
    seed_clips(objects, spec)
    report = ok(importer, spec, data)
    assert (report.accepted, report.rejected) == (3, []), report
    manifest = run(store.resolve(report.dataset_ref, provider_org_id=NEMO))
    got = bodies(objects, manifest)
    spans = sorted((b.get("span_ms"), s.duration_ms, s.group_key, b.get("media_digest"))
                   for b, s in zip(got, manifest.samples))
    a = "sha256:" + hashlib.sha256(CLIP_A).hexdigest()
    b = "sha256:" + hashlib.sha256(CLIP_B).hexdigest()
    assert spans == [([0, 82000], 82000, "yard-night", b), ([1500, 4250], 2750, "dock-shift-a", a),
                     ([10000, 18500], 8500, "dock-shift-a", a)], spans
    assert run(objects.get(imports.media_key(NEMO, a))) == CLIP_A
    assert len(manifest.splits.train) == 3, manifest.splits
    assert all(body.get("annotation") == spec["annotation"]
               and body["modality"] == "finite_video" for body in got), got


# --- N1.b: quarantine with row diagnostics ----------------------------------------------------
def test_n1_malformed_rows_are_quarantined_and_never_silently_omitted() -> None:
    """Oracle: each bad row is reported with its line and reason; with rejects present the
    publication is refused (ImportRejected carrying the report) unless the caller accepts
    them, and then the report still lists every one; a validator that let one through, or a
    publish that dropped them silently, fails. Structured content is an object or array."""
    spec = text_spec()
    good = {"question": "ok?", "episode": "e9", "split": "train"}
    data = rows(good, b"{not json", b"[1, 2]", {"episode": "e8", "split": "train"},
                {**good, "question": "  "}, {**good, "question": "q", "split": "test"},
                b'{"question": NaN, "episode": "e7", "split": "train"}',
                {**good, "question": "g", "episode": [1]}, {**good, "question": 7})
    store, objects, importer = world()
    with pytest.raises(imports.ImportRejected) as refused:
        go(importer, spec, data)
    expected = [(2, "not_json"), (3, "not_object"), (4, "missing_field"), (5, "bad_content"),
                (6, "bad_split"), (7, "not_json"), (8, "bad_group"), (9, "bad_content")]
    assert reasons(refused.value.report) == expected, refused.value.report
    assert store.published == [], "a dataset was published with its errors omitted"
    report = ok(importer, spec, data, accept_rejects=True)
    assert report.accepted == 1 and reasons(report) == expected, report
    assert len(store.published) == 1
    structured = {**spec, "modality": "structured", "fields": {"content": "answer"},
                  "import_id": "1a000000-0000-4000-8000-0000000000c1",
                  "dataset_id": "da000000-0000-4000-8000-0000000000c1"}
    report = ok(importer, structured, rows({"answer": "a string"}, {"answer": {"x": 1}},
                                           {"answer": [1]}), accept_rejects=True)
    assert (report.accepted, reasons(report)) == (2, [(1, "bad_content")]), report


def test_n1_timestamp_units_are_checked() -> None:
    """Oracle: an unknown clock unit is refused before anything is read; a span in
    fractional milliseconds, backwards, empty, negative, a boolean, past the 82 s cap, or
    out of range (1e999 parses to infinity, a 400-digit integer overflows a float) is
    quarantined - in the import and in the preview, never a crash; seconds with
    sub-millisecond precision are refused, not rounded; a millisecond count above 2**53 is
    exact (a float would merge 2**53 and 2**53 + 1 into an empty span)."""
    spec, _ = fixture("sam_export")
    store, objects, importer = world()
    for unit in ("frames", "MS", None):
        with pytest.raises(errors.InvalidRequest):
            go(importer, {**spec, "clock_unit": unit}, b"")

    def row(start, end):
        return {"video": {"path": "clips/dock-01.mp4"}, "session": {"id": "s"},
                "segment": {"start_s": start, "end_s": end}, "annotation": {}}
    ms = {**spec, "clock_unit": "ms", "import_id": "1a000000-0000-4000-8000-0000000000f2",
          "dataset_id": "da000000-0000-4000-8000-0000000000f2"}
    seed_clips(objects, ms)
    report = ok(importer, ms, rows(row(1500, 4250), row(1.5, 4), row(2 ** 53, 2 ** 53 + 1)),
                accept_rejects=True)
    assert (report.accepted, reasons(report)) == (2, [(2, "invalid_span")]), report
    infinite = json.dumps(row("X", 2)).replace('"X"', "1e999").encode()
    data = rows(row(1, 2), row(2, 1), row(3, 3), row(-1, 2), row(True, 2), row(0, 82.001),
                row(0.0005, 1), row("1", 2), infinite, row(10 ** 400, 2), row(1, 10 ** 400))
    timed = {**spec, "import_id": "1a000000-0000-4000-8000-0000000000f1"}
    seed_clips(objects, timed)
    report = ok(importer, timed, data, accept_rejects=True)
    assert report.accepted == 1, report
    assert reasons(report) == [(n, "invalid_span") for n in range(2, 12)], report
    shown = imports.preview(timed, data, provider_org_id=NEMO)["rows"]
    assert [r.get("reason") for r in shown] == [None] + ["invalid_span"] * 10, shown


def test_n1_oversized_rows_and_uploads_are_refused() -> None:
    """Oracle: a row over MAX_ROW_BYTES is quarantined whether it arrives in pieces (never
    buffered past the bound) or in one piece, and the next row still parses; the last line
    needs no newline; an upload over MAX_REQUEST_BYTES is refused as a whole and publishes
    nothing."""
    big = text_spec()
    huge = rows({"question": "x" * imports.MAX_ROW_BYTES, "episode": "e", "split": "train"})
    fine = rows({"question": "fine", "episode": "f", "split": "train"})
    for piece in (7, len(huge) + len(fine)):
        store, objects, importer = world()
        report = ok(importer, big, huge + fine, piece=piece, accept_rejects=True)
        assert (report.accepted, reasons(report)) == (1, [(1, "too_large")]), (piece, report)

    async def lines(data):
        return [(n, raw) async for n, raw in imports._lines(chunks(data, 7),
                                                             hashlib.sha256(), 1 << 30)]
    assert run(lines(huge + fine.rstrip(b"\n"))) == [(1, None), (2, fine.rstrip(b"\n"))]
    store, objects, importer = world(limits=DEFAULTS.replace(max_request_bytes=40))
    with pytest.raises(errors.RequestTooLarge):
        go(importer, big, rows({"question": "a" * 20, "episode": "e", "split": "train"},
                               {"question": "b" * 20, "episode": "e", "split": "train"}))
    assert store.published == []


def test_n1_an_oversized_clip_is_refused_before_it_is_read() -> None:
    """Oracle: the size check reads the object's description, never its bytes; an import
    that accepts nothing is never published, even with rejects accepted."""
    spec, data = fixture("sam_export")
    objects = CrashingObjects(-1)
    store, objects, importer = world(objects, limits=DEFAULTS.replace(
        max_media_bytes=len(CLIP_A) - 1))
    seed_clips(objects, spec)
    with pytest.raises(errors.DomainError) as refused:
        go(importer, spec, data, accept_rejects=True)
    assert isinstance(refused.value, imports.ImportRejected), refused.value
    report = refused.value.report
    assert (report.accepted, [r for _, r in reasons(report)]) == (0, ["too_large"] * 3), report
    assert not [k for k in objects.reads if "/bundle/" in k], objects.reads


def test_n1_traversal_ssrf_and_foreign_objects_are_refused() -> None:
    """Oracle: a media path that climbs, is absolute, names a scheme or another provider's
    prefix never reaches another key; an http(s) source goes through M's SSRF-hardened
    fetcher (a metadata address is refused, a public one is fetched); a non-video object is
    refused."""
    spec, _ = fixture("sam_export")
    served = []

    async def resolve(host):
        return {"metadata.internal": ["169.254.169.254"]}.get(host, ["93.184.216.34"])

    def handler(request):
        served.append(str(request.url))
        return httpx.Response(200, stream=httpx.ByteStream(CLIP_B),
                              headers={"content-type": "video/mp4"})
    fetcher = MediaFetcher(resolve=resolve, transport=httpx.MockTransport(handler))
    store, objects, importer = world(fetcher=fetcher)
    seed_clips(objects, spec)
    foreign = f"lab/{OTHER}/imports/{spec['import_id']}/bundle/clips/secret.mp4"
    objects.seed(foreign, CLIP_A)
    objects.seed(imports.bundle_key(NEMO, spec["import_id"], "notes.txt"), b"hi", "text/plain")

    def row(path):
        return {"video": {"path": path}, "session": {"id": str(path)},
                "segment": {"start_s": 0, "end_s": 1}, "annotation": {}}
    paths = ["../clips/dock-01.mp4", "/clips/dock-01.mp4", "clips/../../x.mp4",
             "file:///etc/passwd", "s3://bucket/key.mp4", f"../../{OTHER}/clips/secret.mp4",
             "clips\\dock-01.mp4", "http://metadata.internal/latest/meta-data", foreign,
             "notes.txt", "https://cdn.example.com/clip.mp4", 7]
    report = ok(importer, spec, rows(*map(row, paths)), accept_rejects=True)
    assert reasons(report) == [(1, "bad_media_path"), (2, "bad_media_path"),
                               (3, "bad_media_path"), (4, "bad_media_path"),
                               (5, "bad_media_path"), (6, "bad_media_path"),
                               (7, "bad_media_path"), (8, "media_refused"),
                               (9, "media_missing"), (10, "unsupported_type"),
                               (12, "bad_media_path")], report
    assert report.accepted == 1 and len(served) == 1 and "93.184.216.34" in served[0], served


def test_n1_a_spec_for_another_provider_or_grant_is_refused() -> None:
    """Oracle (DATA-RIGHTS): the caller's server-derived provider must be the spec's, the
    grant ref must name that provider, and the store must know it as a current grant TO
    that provider; a revoked grant registers nothing; only provider-owned data is
    importable until P-09."""
    spec, data = fixture("benchmark")
    store, objects, importer = world()
    with pytest.raises(errors.Forbidden):
        run(importer.run(spec, chunks(data), provider_org_id=OTHER, actor="dev@other"))
    with pytest.raises(errors.Forbidden):
        go(importer, {**spec, "grant_ref": grant_ref(provider=OTHER)}, data)
    unknown = grant_ref("90000000-0000-4000-8000-0000000000ff")
    with pytest.raises(errors.NotFound):
        go(importer, {**spec, "grant_ref": unknown, "import_id":
                      "1a000000-0000-4000-8000-0000000000a1"}, data)
    with pytest.raises(errors.InvalidRequest):
        go(importer, {**spec, "ownership": "customer"}, data)
    store.revoke(grant_ref())
    with pytest.raises(errors.Forbidden):
        go(importer, {**spec, "import_id": "1a000000-0000-4000-8000-0000000000a2"}, data)
    assert store.published == [] and store.sources == {}


# --- N1.b/c: crash, resume and replay ----------------------------------------------------------
def staged(keys) -> list[str]:
    return sorted(k for k in keys if "/staged/" in k)


def test_n1_a_crash_mid_staging_publishes_nothing_and_resumes_to_the_same_dataset() -> None:
    """Oracle: a process dying after some staged writes leaves no dataset; rerunning over
    the same objects resumes - a staged chunk is reused, never staged again - and publishes
    exactly the dataset an uninterrupted run publishes, from one chunk per two lines."""
    spec, data = fixture("benchmark")
    _, _, clean = world(chunk_rows=2)
    expected = ok(clean, spec, data).dataset_ref
    for puts in range(1, 9):
        objects = CrashingObjects(puts)
        store, objects, importer = world(objects, chunk_rows=2)
        crashes(importer, spec, data)
        assert store.published == [], f"crash after {puts} writes published"
        before, objects.writes, objects.left = set(objects.objects), [], -1
        report = ok(importer, spec, data)
        assert report.dataset_ref == expected and store.published == [expected]
        assert not set(staged(objects.writes)) & before, f"restaged after {puts} writes"
        assert len(staged(objects.objects)) == 3, staged(objects.objects)


def test_n1_a_crash_between_source_and_manifest_resumes_once() -> None:
    """Oracle: a source registered without its manifest is harmless; the rerun registers
    the same source (a replay) and publishes once."""
    spec, data = fixture("benchmark")
    store, objects, importer = world()
    publish = store.publish

    async def dies(*a, **k):
        raise Crash("publish")
    store.publish = dies
    crashes(importer, spec, data)
    assert len(store.sources) == 1 and store.published == []
    store.publish = publish
    report = ok(importer, spec, data)
    assert store.published == [report.dataset_ref]


def test_n1_a_replayed_upload_is_one_dataset_and_changed_bytes_conflict() -> None:
    """Oracle (DATA-IMMUTABLE): the same spec and bytes again answer the same report and
    publish nothing new; other bytes, or another spec, under the same import id are a
    conflict, never a silently redefined dataset - also when the first run died before its
    source was registered."""
    spec, data = fixture("benchmark")
    store, objects, importer = world(chunk_rows=2)
    first = ok(importer, spec, data)
    assert ok(importer, spec, data) == first and store.published == [first.dataset_ref]
    changed = data.replace(b"forklift", b"tractor!")
    with pytest.raises(errors.Conflict):
        go(importer, spec, changed)
    with pytest.raises(errors.Conflict):
        go(importer, {**spec, "license": "CC0"}, data)
    appended = data + rows({"question": "extra", "episode": "e9", "split": "train"})
    with pytest.raises(errors.Conflict):
        go(importer, spec, appended)
    assert store.published == [first.dataset_ref]
    other = {**spec, "import_id": "1a000000-0000-4000-8000-0000000000d1",
             "dataset_id": "da000000-0000-4000-8000-0000000000d1"}
    register = store.register_source

    async def dies(**k):
        raise Crash("register")
    store.register_source = dies
    crashes(importer, other, data)
    store.register_source = register
    with pytest.raises(errors.Conflict):
        go(importer, other, changed)
    assert store.published == [first.dataset_ref]


def resumed(spec, data, puts):
    """(the report of an uninterrupted run, the report of a run that died after `puts`
    writes and resumed), two lines per chunk."""
    _, _, clean = world(chunk_rows=2)
    first = ok(clean, spec, data, accept_rejects=True)
    store, objects, importer = world(CrashingObjects(puts), chunk_rows=2)
    crashes(importer, spec, data, accept_rejects=True)
    objects.left = -1
    return first, ok(importer, spec, data, accept_rejects=True)


def test_n1_duplicate_rows_are_dedup_candidates() -> None:
    """Oracle: a row whose content repeats an earlier one - within a chunk, across a chunk
    boundary, or across a crash and resume - is quarantined as a duplicate naming the first
    line."""
    row = {"question": "same?", "episode": "e1", "split": "train"}
    data = rows(row, {**row, "question": "other"}, row, row)
    for report in resumed(text_spec(), data, puts=4):
        assert reasons(report) == [(3, "duplicate"), (4, "duplicate")], report
        assert all("line 1" in r["detail"] for r in report.rejected), report


def test_n1_a_group_split_across_splits_is_quarantined() -> None:
    """Oracle (DATA-SPLIT at import): rows of one declared group in two splits would leak;
    the later row is quarantined, across chunks and across a resume; without a group
    mapping every row is its own group."""
    data = rows({"question": "a", "episode": "e1", "split": "train"},
                {"question": "b", "episode": "e2", "split": "holdout"},
                {"question": "c", "episode": "e1", "split": "holdout"})
    for report in resumed(text_spec(), data, puts=4):
        assert reasons(report) == [(3, "split_conflict")], report
    ungrouped = text_spec(fields={"content": "question", "split": "split"})
    store, objects, importer = world()
    report = ok(importer, ungrouped, data)
    assert (report.accepted, report.rejected) == (3, []), report


def test_n1_the_schema_preview_shows_fields_mapping_and_row_errors() -> None:
    """Oracle: the preview lists every top-level field with its JSON types, the mapped
    values of the first `rows` rows (blank lines are not rows) and each row's refusal - an
    oversized one included - without writing anything."""
    spec, data = fixture("benchmark")
    head = b"[1]\n" + data + b"x" * (imports.MAX_ROW_BYTES + 1) + b"\n[2]\n"
    got = imports.preview(spec, head, provider_org_id=NEMO, rows=7)
    assert got["fields"] == {"answer": ["boolean", "number", "object", "string"],
                             "episode": ["string"], "id": ["string"], "question": ["string"],
                             "split": ["string"]}, got["fields"]
    assert [r["line"] for r in got["rows"]] == [1, 2, 3, 4, 5, 7, 8], got["rows"]
    assert got["rows"][0] == {"line": 1, "reason": "not_object"}, got["rows"]
    assert got["rows"][1] == {"line": 2, "mapped": {
        "content": "What is shown in the first frame?", "group": "e1", "split": "holdout"}}
    assert got["rows"][-1] == {"line": 8, "reason": "too_large"}, got["rows"][-1]
    with pytest.raises(errors.Forbidden):
        imports.preview(spec, data, provider_org_id=OTHER)


def test_n1_the_spec_is_strict() -> None:
    """Oracle: an unknown key, a mutable grant ref, another format, a video spec without a
    media mapping or a text spec with a clock unit is refused."""
    spec, _ = fixture("benchmark")
    video, _ = fixture("sam_export")
    bad = [{**spec, "extra": 1}, {**spec, "grant_ref": grant_ref().split("@")[0] + "@latest"},
           {**spec, "format": "infrx.dataset_import.2"}]
    no_media = copy.deepcopy(video)
    del no_media["fields"]["media"]
    bad += [no_media, {**spec, "clock_unit": "ms"}]
    for payload in bad:
        with pytest.raises(errors.InvalidRequest):
            imports.parse_spec(payload, provider_org_id=NEMO)


# --- WR-N-2: the acting provider comes from the L2 port -----------------------------------
def test_wrn2_only_a_current_developer_member_acts_for_the_provider() -> None:
    """Oracle (DATA-RIGHTS, LAB-ACCESS): a dataset call (import, preview, derive, export,
    trace selection) acts for `provider_org_id` only when L2 says the user is a current
    developer-or-above member of it; a consumer-only user, a revoked member and another
    provider's developer are `NotFound` (a foreign workspace id confirms nothing), a viewer
    is `Forbidden` - each before the importer writes any object. A check that accepted any
    membership, a revoked one, or another provider's, fails."""
    from datetime import UTC, datetime, timedelta

    from infrx.contracts.v2 import records as v2
    from infrx.datasets import acting_provider
    from infrx.lab.access import LabAccess
    from infrx.lab.access.fakes import FakeAccessStore

    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    store = FakeAccessStore(now=now, provider_names={NEMO: "Nemo", OTHER: "Other"})
    users = {}
    for n, (provider, role, revoked) in enumerate((
            (NEMO, "developer", False), (NEMO, "administrator", False), (NEMO, "viewer", False),
            (NEMO, "developer", True), (OTHER, "developer", False))):
        users[(provider, role, revoked)] = user = f"a0000000-0000-4000-8000-{n + 1:012x}"
        store.memberships[(provider, user)] = v2.ProviderMembership(
            provider_org_id=provider, user_id=user, role=role, granted_by="ops",
            granted_at=now - timedelta(days=1),
            revoked_at=now - timedelta(seconds=1) if revoked else None)
    access = LabAccess(store)
    spec, data = fixture("benchmark")

    def route(user: str) -> str:        # the composed caller: L2 first, then the importer
        _, objects, importer = world()

        async def call():
            provider = await acting_provider(access, user, NEMO)
            await importer.run(spec, chunks(data), provider_org_id=provider, actor=user)
            return provider
        try:
            return run(call())
        except errors.DomainError as refused:
            assert run(objects.keys("")) == [], "an object was written before the refusal"
            return type(refused).__name__
    assert route(users[(NEMO, "developer", False)]) == NEMO
    assert route(users[(NEMO, "administrator", False)]) == NEMO
    assert route(users[(NEMO, "viewer", False)]) == "Forbidden"
    assert route(users[(NEMO, "developer", True)]) == "NotFound"
    assert route(users[(OTHER, "developer", False)]) == "NotFound"
    assert route("f1000000-0000-4000-8000-0000000000f1") == "NotFound"    # consumer-only
