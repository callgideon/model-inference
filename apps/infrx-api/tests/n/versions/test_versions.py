#!/usr/bin/env python3
"""N2 (13-lab-improvement-handoffs §N2; DATA-IMMUTABLE, DATA-SPLIT, DATA-RIGHTS): derived
versions with deterministic, leakage-trapped splits over a frozen holdout, and bounded,
rights-checked, resumable exports - on N1 imports in the fake world of
`tests/n/imports/world.py` (0029's rules in memory); `test_versions_pg.py` reruns the core
scenarios on the real `PgLabDataStore`. Every case is named by a mutant in `mutants.py`.

    uv run --frozen pytest -q tests/n/versions/test_versions.py
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from infrx.contracts import errors
from infrx.datasets import imports, versions
from infrx.media.store import InMemoryObjectStore

from ..imports.world import (GRANT_ID, NEMO, OTHER, Crash, CrashingObjects, FakeLabStore,
                             chunks, fixture, grant_ref, run)

GRANT_2 = "90000000-0000-4000-8000-000000000002"
NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
POLICY = versions.SplitPolicy(seed=7, train_bp=6000, validation_bp=2000)


def world(objects=None):
    store = FakeLabStore()
    store.add_grant()
    store.add_grant(GRANT_2)
    return store, objects if objects is not None else InMemoryObjectStore()


def uid(n: int, tag: int) -> str:
    return f"{tag:08x}-0000-4000-8000-{n:012x}"


def imported(store, objects, items, n: int, *, grant: str = GRANT_ID, split: bool = False,
             modality: str = "text", **over) -> str:
    """An N1 import of `items` (dicts) as dataset `n`; `split` maps the rows' declared split."""
    spec, _ = fixture("benchmark" if modality == "text" else "sam_export")
    spec = {**spec, "import_id": uid(n, 0x1a), "dataset_id": uid(n, 0xda),
            "grant_ref": grant_ref(grant), **over}
    if modality == "text":
        spec["fields"] = {"content": "q", "group": "g", **({"split": "split"} if split else {})}
    data = b"".join(json.dumps(i).encode() + b"\n" for i in items)
    try:
        return run(imports.Importer(store, objects).run(
            spec, chunks(data, 64), provider_org_id=NEMO, actor="dev@nemo")).dataset_ref
    except errors.DomainError as refused:
        raise AssertionError(f"the import was refused: {refused}") from None


def text(n: int, prefix: str = "q", group: str | None = None, **extra) -> list[dict]:
    return [{"q": f"{prefix} {i}?", "g": group or f"{prefix}-{i}", **extra}
            for i in range(1, n + 1)]


def derive(store, objects, *, version: int = 1, dataset: int = 0xd1, policy=POLICY, **kw):
    try:
        return run(versions.derive(store, objects, provider_org_id=NEMO, actor="dev@nemo",
                                   dataset_id=uid(dataset, 0xda), version=version,
                                   created_at="2026-09-27T13:00:00Z", policy=policy, **kw))
    except errors.DomainError as refused:
        raise AssertionError(f"{type(refused).__name__}: {refused}") from None


def refused_derive(store, objects, **kw) -> errors.DomainError:
    with pytest.raises(errors.DomainError) as refused:
        run(versions.derive(store, objects, provider_org_id=NEMO, actor="dev@nemo",
                            dataset_id=uid(0xd9, 0xda), version=1,
                            created_at="2026-09-27T13:00:00Z", policy=POLICY, **kw))
    return refused.value


def manifest(store, ref):
    return run(store.resolve(ref, provider_org_id=NEMO))


def split_of(m) -> dict[str, str]:
    return {i: n for n in ("train", "validation", "holdout") for i in getattr(m.splits, n)}


def content(objects, m) -> dict[str, dict]:
    return {s.sample_id: json.loads(run(objects.get(imports.sample_key(NEMO, s.content_digest))))
            for s in m.samples}


def export(store, objects, export_id, ref, **kw):
    try:
        return run(versions.export(store, objects, provider_org_id=NEMO, dataset_ref=ref,
                                   export_id=export_id, now=kw.pop("now", NOW),
                                   ttl_s=kw.pop("ttl_s", 3600), **kw))
    except errors.DomainError as refused:
        raise AssertionError(f"{type(refused).__name__}: {refused}") from None


def part(store, objects, export_id, n=0, now=NOW) -> list[dict]:
    data = run(versions.read_part(store, objects, provider_org_id=NEMO, export_id=export_id,
                                  part=n, now=now))
    return [json.loads(line) for line in data.splitlines()]


# --- N2.a: content-addressed versions and deterministic splits -------------------------------
def test_n2_the_same_inputs_policy_and_seed_give_the_same_version_and_split_digest() -> None:
    """Oracle (DATA-IMMUTABLE): two worlds deriving from the same import with the same
    policy and seed publish the same ref and split digest; another seed moves the split
    digest; the policy's proportions are honoured (none of 40 lands outside its split's
    band); an import is `import`, a derivation names its parents."""
    got = []
    for seed in (7, 7, 8):
        store, objects = world()
        parent = imported(store, objects, text(40), 1)
        derived = derive(store, objects, add=[parent],
                         policy=versions.SplitPolicy(seed=seed, train_bp=6000,
                                                     validation_bp=2000))
        m = manifest(store, derived.dataset_ref)
        assert (m.derivation, m.parent_refs) == ("derive", [parent]), m
        sizes = [len(m.splits.train), len(m.splits.validation), len(m.splits.holdout)]
        assert 14 <= sizes[0] <= 34 and 2 <= sizes[1] <= 14 and 2 <= sizes[2] <= 14, sizes
        got.append((derived.dataset_ref, derived.split_digest))
    assert got[0] == got[1], "the same inputs, policy and seed gave two versions"
    assert got[0][1] != got[2][1], "the seed does not move the split"


def test_n2_related_clips_and_near_duplicates_never_cross_splits() -> None:
    """Oracle (DATA-SPLIT): clips of one source video (other sessions, other spans) and
    texts equal up to case and spacing are one group for splitting, whatever the seed, and
    each such cross-group family is flagged for review; unrelated rows are not."""
    store, objects = world()
    spec, _ = fixture("sam_export")
    for path, data in (("a.mp4", b"clip-a"), ("b.mp4", b"clip-b")):
        objects.seed(imports.bundle_key(NEMO, uid(2, 0x1a), path), data)
    clips = [{"video": {"path": p}, "session": {"id": s}, "segment": {"start_s": a, "end_s": b},
              "annotation": {"n": i}}
             for i, (p, s, a, b) in enumerate([("a.mp4", "s1", 0, 5), ("a.mp4", "s2", 5, 9),
                                                ("a.mp4", "s3", 9, 12), ("b.mp4", "s4", 0, 3)])]
    video = imported(store, objects, clips, 2, modality="finite_video")
    texts = imported(store, objects, [{"q": "Hello  World", "g": "t1"},
                                      {"q": "hello world", "g": "t2"},
                                      {"q": "unrelated", "g": "t3"}], 3)
    for seed in range(12):
        derived = derive(store, objects, add=[video, texts], version=seed + 1,
                         policy=versions.SplitPolicy(seed=seed, train_bp=3400,
                                                     validation_bp=3300))
        m = manifest(store, derived.dataset_ref)
        where, body = split_of(m), content(objects, m)
        by_clip = {}
        for sid, b in body.items():
            key = b.get("media_digest") or " ".join(b["content"].lower().split())
            by_clip.setdefault(key, set()).add(where[sid])
        assert all(len(s) == 1 for s in by_clip.values()), (seed, by_clip)
        flagged = [sorted(body[i].get("media_digest") or body[i]["content"] for i in family)
                   for family in derived.review]
        assert len(flagged) == 2 and ["Hello  World", "hello world"] in flagged, flagged


def test_n2_a_leaked_split_is_rejected_with_the_leak_visible() -> None:
    """Oracle (DATA-SPLIT): a frozen base whose declared splits put near-duplicates in train
    and holdout cannot be derived from; the refusal names both samples and both splits."""
    store, objects = world()
    base = imported(store, objects, [{"q": "What is X?", "g": "a", "split": "train"},
                                     {"q": "what is  x?", "g": "b", "split": "holdout"},
                                     {"q": "other", "g": "c", "split": "train"}], 4, split=True)
    refused = refused_derive(store, objects, base=base)
    assert isinstance(refused, versions.LeakRefused), refused
    assert [sorted(leak["splits"]) for leak in refused.leaks] == [["holdout", "train"]], \
        refused.leaks
    assert len(refused.leaks[0]["samples"]) == 2, refused.leaks


# --- N2.b: frozen holdout, new versions, access -----------------------------------------------
def test_n2_adding_samples_is_a_new_version_over_a_frozen_holdout() -> None:
    """Oracle (DATA-IMMUTABLE, DATA-SPLIT): v2 = v1 + a new import keeps every v1 sample in
    its v1 split, puts no new sample in the holdout, moves a new sample of a v1 train group
    into train, and omits (visibly) a new sample of a v1 holdout group; v1 is untouched; the
    same version with other bytes is a conflict."""
    store, objects = world()
    first = imported(store, objects, text(30), 5)
    v1 = derive(store, objects, add=[first])
    m1 = manifest(store, v1.dataset_ref)
    s1 = split_of(m1)
    group_of = {s.sample_id: s.group_key for s in m1.samples}
    held = next(group_of[i] for i in m1.splits.holdout)
    trained = next(group_of[i] for i in m1.splits.train)
    more = text(20, "new") + [{"q": "joins holdout", "g": held}, {"q": "joins train", "g": trained}]
    second = imported(store, objects, more, 6)
    v2 = derive(store, objects, base=v1.dataset_ref, add=[second], version=2)
    m2 = manifest(store, v2.dataset_ref)
    s2 = split_of(m2)
    assert {i: s2.get(i) for i in s1} == s1, "a v1 sample moved"
    assert m2.splits.holdout == m1.splits.holdout, "the holdout changed"
    assert m2.parent_refs == [v1.dataset_ref, second], m2.parent_refs
    body = content(objects, m2)
    joined = [i for i, b in body.items() if b["content"] == "joins train"]
    assert [s2[i] for i in joined] == ["train"], joined
    assert [o["reason"] for o in v2.omitted] == ["related_to_holdout"], v2.omitted
    assert len(m2.samples) == len(m1.samples) + 21
    assert manifest(store, v1.dataset_ref) == m1
    with pytest.raises(errors.StateConflict):
        run(versions.derive(store, objects, provider_org_id=NEMO, actor="dev@nemo",
                            dataset_id=uid(0xd1, 0xda), version=2,
                            created_at="2026-09-28T13:00:00Z", policy=POLICY,
                            base=v1.dataset_ref, add=[second]))


def test_n2_a_duplicate_import_cannot_change_the_holdout() -> None:
    """Oracle (DATA-SPLIT): the same rows imported again (another import, another dataset)
    add nothing to a version derived over them: every row is omitted as a duplicate and the
    splits - holdout included - are digest-identical."""
    store, objects = world()
    rows = text(25)
    v1 = derive(store, objects, add=[imported(store, objects, rows, 7)])
    again = imported(store, objects, rows, 8)
    v2 = derive(store, objects, base=v1.dataset_ref, add=[again], version=2)
    assert [o["reason"] for o in v2.omitted] == ["duplicate"] * 25, v2.omitted
    assert v2.split_digest == v1.split_digest


def test_n2_a_revoked_source_is_excluded_from_new_versions() -> None:
    """Oracle (DATA-RIGHTS): derivation reads through the access gate (provider_sharing)
    now - a revoked source's samples are omitted from the new version with the reason
    visible, while its old manifest still resolves as metadata; the other source's samples
    stay (its grant permits access though not training)."""
    store, objects = world()
    a = imported(store, objects, text(6), 9)
    b = imported(store, objects, text(4, "b"), 10, grant=GRANT_2)
    v1 = derive(store, objects, add=[a, b])
    store.revoke(grant_ref())
    store.grants[grant_ref(GRANT_2)]["purposes"] = {"provider_sharing"}    # access, not export
    v2 = derive(store, objects, base=v1.dataset_ref, add=[], version=2)
    assert [o["reason"] for o in v2.omitted] == ["grant_not_current"] * 6, v2.omitted
    m2 = manifest(store, v2.dataset_ref)
    assert {s.grant_ref for s in m2.samples} == {grant_ref(GRANT_2)}
    assert len(manifest(store, a).samples) == 6
    # An unreadable base sample still holds its family's split: new rows related to a v1
    # holdout sample (by group, or by text up to case) stay out after v1's grant loses
    # provider_sharing, so no v2 train row descends from v1's holdout.
    store, objects = world()
    v1 = derive(store, objects, add=[imported(store, objects, text(30), 14)])
    m1 = manifest(store, v1.dataset_ref)
    held = sorted(s.group_key for s in m1.samples if s.sample_id in m1.splits.holdout)
    near = [f"  {b['content'].upper()} " for i, b in content(objects, m1).items()
            if i in m1.splits.holdout]
    assert held and len(near) == len(held), m1.splits
    new = imported(store, objects, [{"q": f"near copy of {g}", "g": g} for g in held] +
                   [{"q": t, "g": f"other-{n}"} for n, t in enumerate(near)] + text(5, "fresh"),
                   15, grant=GRANT_2)
    store.grants[grant_ref()]["purposes"] = {"training"}       # access lapses, not training
    v2 = derive(store, objects, base=v1.dataset_ref, add=[new], version=2)
    reasons = sorted(o["reason"] for o in v2.omitted)
    assert reasons == ["grant_not_current"] * 30 + ["related_to_holdout"] * (2 * len(held)), \
        reasons
    m2 = manifest(store, v2.dataset_ref)
    assert sorted(b["content"] for b in content(objects, m2).values()) == \
        [f"fresh {i}?" for i in range(1, 6)], m2.samples
    assert m2.splits.holdout == [], m2.splits


def test_n2_derivation_reads_only_the_callers_datasets_and_a_sane_policy() -> None:
    """Oracle (DATA-RIGHTS, DATA-IMMUTABLE): another provider's dataset is not found; a
    derivation needs a parent; proportions over 10000 bp, a negative one, or a frozen base
    with nowhere for new samples to go are refused."""
    store, objects = world()
    a = imported(store, objects, text(3), 11)
    foreign = a.replace(f"lab:dataset:{NEMO}", f"lab:dataset:{OTHER}")
    assert isinstance(refused_derive(store, objects, add=[foreign]), errors.NotFound)
    assert isinstance(refused_derive(store, objects), errors.InvalidRequest)
    for bp in ((9000, 1001), (-1, 0)):
        with pytest.raises(errors.InvalidRequest):
            versions.SplitPolicy(seed=1, train_bp=bp[0], validation_bp=bp[1])
    v1 = derive(store, objects, add=[a])
    closed = versions.SplitPolicy(seed=1, train_bp=0, validation_bp=0)
    with pytest.raises(errors.InvalidRequest):
        run(versions.derive(store, objects, provider_org_id=NEMO, actor="dev@nemo",
                            dataset_id=uid(0xd1, 0xda), version=2,
                            created_at="2026-09-27T13:00:00Z", policy=closed,
                            base=v1.dataset_ref, add=[]))


# --- N2.c: exports ---------------------------------------------------------------------------
def exported_world():
    """Two sources (the benchmark-shaped A under grant 1, B under grant 2) derived into one
    version with some of each in the holdout."""
    store, objects = world()
    a = imported(store, objects, text(12, extra="secret"), 12)
    b = imported(store, objects, text(8, "b"), 13, grant=GRANT_2, license="CC0",
                 use_restrictions=["training"])
    v1 = derive(store, objects, add=[a, b])
    return store, objects, v1.dataset_ref


def test_n2_a_reexport_is_byte_identical_and_carries_schema_rights_and_omissions() -> None:
    """Oracle (DATA-IMMUTABLE, DATA-RIGHTS): two exports of one version are the same parts,
    byte for byte; the record names the manifest hash, the schema, the purpose, each
    source's licence, restrictions and grant, every omitted sample with its reason (the
    holdout never leaves), and the redacted keys, which are gone from every item."""
    store, objects, ref = exported_world()
    m = manifest(store, ref)
    one = export(store, objects, uid(1, 0xe0), ref, redact=["extra"], part_items=5)
    two = export(store, objects, uid(2, 0xe0), ref, redact=["extra"], part_items=5)
    bytes_of = [[run(objects.get(p["key"])) for p in r["parts"]] for r in (one, two)]
    assert bytes_of[0] == bytes_of[1] and one["content_sha256"] == two["content_sha256"]
    assert (one["manifest_sha256"], one["schema"], one["purpose"], one["redacted"]) == \
        (ref.split("@sha256:")[1], "lab.dataset_manifest.1", "training", ["extra"]), one
    held = sorted(m.splits.holdout)
    assert sorted(o["sample_id"] for o in one["omitted"]) == held and \
        {o["reason"] for o in one["omitted"]} == {"holdout"}, one["omitted"]
    items = [i for n in range(len(one["parts"])) for i in part(store, objects, uid(1, 0xe0), n)]
    assert sorted(i["sample_id"] for i in items) == sorted(set(split_of(m)) - set(held))
    assert [p["items"] for p in one["parts"]] == [5] * (len(items) // 5) + (
        [len(items) % 5] if len(items) % 5 else []), one["parts"]
    assert all("extra" not in i["record"]["original"] and i["record"]["original"]["q"]
               for i in items), items[0]
    licences = sorted((s["license"], s["grant_ref"]) for s in one["sources"].values())
    assert licences == sorted([("CC-BY-4.0", grant_ref()), ("CC0", grant_ref(GRANT_2))]), \
        one["sources"]
    assert {i["source_ref"] for i in items} <= set(one["sources"])


def test_n2_redaction_removes_nested_paths_and_never_the_content_itself() -> None:
    """Oracle (DATA-RIGHTS): a redacted key is a dotted path in the import spec's syntax,
    removed from the original row at any depth and from structured content that holds it,
    so no listed key ships; a redaction that would remove the mapped content itself (the
    text field, or the whole structured content) is refused before anything is written."""
    store, objects = world()
    a = imported(store, objects, [{"q": f"email me? {i}", "g": f"g{i}",
                                   "meta": {"email": f"user{i}@x.com", "lang": "en"}}
                                  for i in range(12)], 16)
    for eid, redact in ((uid(12, 0xe0), ["q"]), (uid(13, 0xe0), ["meta.email", "q"])):
        with pytest.raises(errors.InvalidRequest):
            run(versions.export(store, objects, provider_org_id=NEMO, dataset_ref=a,
                                export_id=eid, now=NOW, ttl_s=60, redact=redact))
    assert not any(uid(n, 0xe0) in key for n in (12, 13) for key in objects.objects), \
        "a refused export wrote something"
    record = export(store, objects, uid(14, 0xe0), a, redact=["meta.email", "q.x"])
    items = part(store, objects, uid(14, 0xe0))
    assert record["redacted"] == ["meta.email", "q.x"] and len(items) == 12, record
    assert all(i["record"]["original"]["meta"] == {"lang": "en"} and
               i["record"]["content"] == i["record"]["original"]["q"] for i in items), items[0]
    spec, _ = fixture("sam_export")
    objects.seed(imports.bundle_key(NEMO, uid(17, 0x1a), "a.mp4"), b"clip-a")
    clips = [{"video": {"path": "a.mp4"}, "session": {"id": f"s{i}"},
              "segment": {"start_s": i, "end_s": i + 1},
              "annotation": {"caption": f"worker {i}", "operator": {"badge": i, "shift": "a"}}}
             for i in range(6)]
    video = imported(store, objects, clips, 17, modality="finite_video")
    record = export(store, objects, uid(15, 0xe0), video,
                    redact=["annotation.operator.badge", "session"])
    items = part(store, objects, uid(15, 0xe0))
    assert items and record["redacted"] == ["annotation.operator.badge", "session"], record
    for i in items:
        original, got = i["record"]["original"], i["record"]["content"]
        assert "session" not in original and original["annotation"] == got, original
        assert got["operator"] == {"shift": "a"} and got["caption"].startswith("worker"), got
    with pytest.raises(errors.InvalidRequest):
        run(versions.export(store, objects, provider_org_id=NEMO, dataset_ref=video,
                            export_id=uid(16, 0xe0), now=NOW, ttl_s=60,
                            redact=["annotation"]))


def test_n2_an_export_omits_revoked_and_untrained_sources() -> None:
    """Oracle (DATA-RIGHTS): the export gate is `training`, read now: a revoked source's
    samples are omitted with the reason; a read of an existing export drops items whose
    grant has since been revoked or lost the training purpose; a grant without the training
    purpose exports nothing of its source."""
    store, objects, ref = exported_world()
    first = export(store, objects, uid(3, 0xe0), ref)
    store.revoke(grant_ref(GRANT_2))
    record = export(store, objects, uid(4, 0xe0), ref)
    reasons = {o["reason"] for o in record["omitted"]}
    assert reasons == {"holdout", "grant_not_current"}, record["omitted"]
    kept = part(store, objects, uid(4, 0xe0))
    assert kept and {i["grant_ref"] for i in kept} == {grant_ref()}, kept
    assert {i["grant_ref"] for i in part(store, objects, uid(3, 0xe0))} == {grant_ref()}
    assert len(first["omitted"]) < len(record["omitted"])
    store.grants[grant_ref()]["purposes"] = {"provider_sharing"}
    assert part(store, objects, uid(3, 0xe0)) == []
    record = export(store, objects, uid(11, 0xe0), ref)
    assert record["parts"] == [] and {o["reason"] for o in record["omitted"]} == \
        {"holdout", "grant_not_current"}, record


def test_n2_an_interrupted_export_resumes_and_refuses_changed_inputs() -> None:
    """Oracle (DATA-IMMUTABLE): an export that died after some parts resumes to the same
    record (its finished parts are write-once); a resume after a revocation changed the
    parts is a conflict, not a silently different bundle; a finished export replays - even
    after that revocation - and is never an export of another dataset."""
    store, objects, ref = exported_world()
    clean = export(store, objects, uid(5, 0xe0), ref, part_items=3)
    crashing = CrashingObjects(2)
    crashing.objects = dict(objects.objects)
    with pytest.raises(Crash):
        run(versions.export(store, crashing, provider_org_id=NEMO, dataset_ref=ref,
                            export_id=uid(6, 0xe0), now=NOW, ttl_s=3600, part_items=3))
    crashing.left = -1
    resumed = export(store, crashing, uid(6, 0xe0), ref, part_items=3)
    assert resumed["parts"] == [{**p, "key": p["key"].replace(uid(5, 0xe0), uid(6, 0xe0))}
                                for p in clean["parts"]]
    assert export(store, crashing, uid(6, 0xe0), ref, part_items=3) == resumed
    with pytest.raises(errors.Conflict):
        run(versions.export(store, crashing, provider_org_id=NEMO, export_id=uid(6, 0xe0),
                            dataset_ref=manifest(store, ref).parent_refs[0], now=NOW,
                            ttl_s=3600))
    crashing.left = 1
    with pytest.raises(Crash):
        run(versions.export(store, crashing, provider_org_id=NEMO, dataset_ref=ref,
                            export_id=uid(7, 0xe0), now=NOW, ttl_s=3600, part_items=3))
    crashing.left = -1
    store.revoke(grant_ref())
    with pytest.raises(errors.Conflict):
        run(versions.export(store, crashing, provider_org_id=NEMO, dataset_ref=ref,
                            export_id=uid(7, 0xe0), now=NOW, ttl_s=3600, part_items=3))
    assert export(store, crashing, uid(6, 0xe0), ref, part_items=3) == resumed, "no replay"


def test_n2_an_export_is_cancelled_or_expires() -> None:
    """Oracle: a cancelled export cannot be read or resumed; an export past its TTL cannot
    be read; a TTL outside 1 s..7 days, a malformed export id or a part that does not exist
    is refused."""
    store, objects, ref = exported_world()
    record = export(store, objects, uid(8, 0xe0), ref, ttl_s=60)
    assert record["expires_at"] == (NOW + timedelta(seconds=60)).isoformat()
    assert part(store, objects, uid(8, 0xe0), now=NOW + timedelta(seconds=59))
    with pytest.raises(errors.Gone):
        part(store, objects, uid(8, 0xe0), now=NOW + timedelta(seconds=60))
    run(versions.cancel(objects, provider_org_id=NEMO, export_id=uid(8, 0xe0)))
    with pytest.raises(errors.Gone):
        part(store, objects, uid(8, 0xe0))
    with pytest.raises(errors.Gone):
        run(versions.export(store, objects, provider_org_id=NEMO, dataset_ref=ref,
                            export_id=uid(8, 0xe0), now=NOW, ttl_s=60))
    with pytest.raises(errors.NotFound):
        part(store, objects, uid(9, 0xe0))
    export(store, objects, uid(9, 0xe0), ref)
    with pytest.raises(errors.NotFound):
        part(store, objects, uid(9, 0xe0), n=99)
    for ttl, eid in ((0, uid(10, 0xe0)), (7 * 86400 + 1, uid(10, 0xe0)), (60, "../x")):
        with pytest.raises(errors.InvalidRequest):
            run(versions.export(store, objects, provider_org_id=NEMO, dataset_ref=ref,
                                export_id=eid, now=NOW, ttl_s=ttl))
