"""E6L j01-j04: the data half of the journey on the e6l stack - owned benchmarks imported
through N1 onto D7 and MinIO, N2's versions and split traps, and the rights on every read.

Run only through runner.py (the stack, the verdict); `pytest tests/integration` never
collects `scenarios_*.py`. Every expectation is the fixture's (`fixtures/`), not a value the
code under test produced.
"""
from __future__ import annotations

import collections

import lab_world as lw
import pytest
from lab_world import run


def reasons(report) -> dict[int, str]:
    return {r["line"]: r["reason"] for r in report.rejected}


# ------------------------------------------------------------------------------------ j01
def test_j01_a_benchmark_with_bad_rows_is_refused_until_its_rejects_are_accepted(lab, workdir):
    """The text benchmark's 20 good rows and 5 bad ones: publication is refused while a row is
    rejected (nothing published, the rejects named by line); accepted, the dataset holds the
    20 rows, one sample each, grant- and source-bound, all in train (no declared split)."""
    from infrx.datasets.imports import ImportRejected
    with pytest.raises(ImportRejected) as refused:            # its own import id (10)
        lab.do_import("text", n=10)
    assert refused.value.report.accepted == 20
    assert reasons(refused.value.report) == {21: "not_json", 22: "not_object",
                                             23: "missing_field", 24: "duplicate",
                                             25: "bad_content"}
    assert lab.sql("select count(*) from infrx.lab_sources where source_id = %s",
                   lab.spec("text", n=10)["import_id"]) == [(0,)], "a refused import registered"
    report = lab.do_import("text", n=10, accept=True)
    rows = lab.rows(report.dataset_ref)
    manifest = lab.manifest(report.dataset_ref)
    assert sorted(rows) == sorted([f"L{i:02d}" for i in range(1, 15)] +
                                  [f"M{i:02d}" for i in range(1, 7)])
    assert {s.grant_ref for s in manifest.samples} == {lab.grant}
    assert {s.source_ref for s in manifest.samples} == {report.source_ref}
    assert {r["split"] for r in rows.values()} == {"train"}
    assert rows["L01"]["group"] == "lk01" and rows["L01"]["row"]["answer"] == "Paris"
    lw.save(workdir, "text-import.json", {"dataset_ref": report.dataset_ref,
                                          "source_ref": report.source_ref,
                                          "rejected": report.rejected,
                                          "fixtures": lw.fixture_hashes()})


def test_j01_a_replayed_upload_is_the_same_dataset_and_a_changed_one_conflicts(lab):
    """The same bytes again (another chunking) are the same dataset ref, no second record;
    other bytes under the same import id are a `Conflict`, and nothing changes."""
    from infrx.contracts import errors
    first = lab.dataset("text", 1)
    records = lab.sql("select count(*) from infrx.lab_records")
    again = lab.do_import("text", n=1, accept=True)
    assert (again.dataset_ref, again.source_ref) == (first.dataset_ref, first.source_ref)
    assert lab.sql("select count(*) from infrx.lab_records") == records
    changed = lab.bytes_of("text").replace(b"Paris", b"Lyon")
    with pytest.raises(errors.Conflict):
        lab.do_import("text", n=1, data=changed, accept=True)
    assert lab.sql("select count(*) from infrx.lab_records") == records
    assert lab.rows(first.dataset_ref)["L01"]["row"]["answer"] == "Paris"


# ------------------------------------------------------------------------------------ j02
def test_j02_finite_video_imports_with_its_cap_and_bundle_checks(lab, workdir):
    """Clips uploaded into the import's own bundle on MinIO: 30 s, 30 s of the same clip and
    exactly 82 s import (one media object per clip); 83 s is `invalid_span`, an absent clip
    `media_missing`, a text object `unsupported_type`, a path out of the bundle
    `bad_media_path`."""
    from infrx.datasets.imports import media_key
    from infrx.media.fetch import digest_of
    clips = lw.CLIPS
    report = lab.dataset("video", 2)                     # uploads lw.CLIPS into its bundle
    assert reasons(report) == {4: "invalid_span", 5: "media_missing", 6: "unsupported_type",
                               7: "bad_media_path"}
    manifest = lab.manifest(report.dataset_ref)
    assert sorted(s.duration_ms for s in manifest.samples) == [30_000, 30_000, 82_000]
    assert {s.modality for s in manifest.samples} == {"finite_video"}
    for path in ("clips/a.mp4", "clips/b.mp4"):
        assert run(lab.objects.head(media_key(lab.NEMO, digest_of(clips[path])))) == \
            digest_of(clips[path])
    lw.save(workdir, "video-import.json", {"dataset_ref": report.dataset_ref,
                                           "rejected": report.rejected})


# ------------------------------------------------------------------------------------ j03
def test_j03_a_foreign_dataset_is_refused_at_import_resolve_and_schedule(lab):
    """Another provider's spec, a grant to another provider, another provider's dataset read,
    and a run naming another provider's dataset: each refused before anything is written."""
    from infrx.contracts import errors
    from infrx.contracts.lab import records
    records_before = lab.sql("select count(*) from infrx.lab_records")
    with pytest.raises(errors.Forbidden):                          # a spec for OTHER
        lab.do_import("text", n=31, provider=lab.OTHER, accept=True)
    with pytest.raises(errors.Forbidden, match="cross_provider_ref"):
        lab.do_import("text", n=32, grant=lab.other_grant, accept=True)
    mine = lab.dataset("text", 1).dataset_ref
    with pytest.raises(errors.NotFound):
        lab.manifest(mine, provider=lab.OTHER)
    assert run(lab.store.accessible_samples(mine, provider_org_id=lab.OTHER,
                                            purpose="provider_sharing")) == []
    theirs = lab.publish({
        "schema": "lab.dataset_manifest.1", "provider_org_id": lab.OTHER,
        "dataset_id": lw.uid(33, 0xda6), "version": 1, "created_at": "2026-09-28T10:00:00Z",
        "derivation": "import", "parent_refs": [],
        "samples": [{"sample_id": lw.uid(1, 0x5a6), "modality": "text",
                     "source_ref": lab.other_source, "grant_ref": lab.other_grant,
                     "content_digest": "sha256:" + "e" * 64, "group_key": "o1"}],
        "splits": {"train": [lw.uid(1, 0x5a6)], "validation": [], "holdout": []}},
        provider=lab.OTHER)
    harness_ref = lab.harness_ref(30)
    runs_before = lab.sql("select count(*) from infrx.lab_eval_runs")
    with pytest.raises((records.LabRejected, errors.DomainError)):
        lab.freeze(lab.run_payload(30, theirs, harness_ref, lab.serving("baseline")))
    assert lab.sql("select count(*) from infrx.lab_eval_runs") == runs_before
    assert lab.sql("select count(*) from infrx.lab_records") == [
        (records_before[0][0] + 2,)], "only OTHER's own manifest and NEMO's harness published"


def test_j03_a_revoked_grant_stops_derivation_and_scheduling(lab):
    """After CONSUMER_1 revokes NEMO's grant: no sample is readable, a derivation omits every
    one (`grant_not_current`), scheduling is `Forbidden`, an export ships nothing; restored
    (a new grant version), all of it reads again."""
    from infrx.contracts import errors
    from infrx.datasets import versions
    from datetime import datetime, timezone
    ref = lab.dataset("text", 1).dataset_ref
    harness_ref = lab.harness_ref(31)
    payload = lab.run_payload(31, ref, harness_ref, lab.serving("baseline"), max_cases=2)
    lab.revoke()
    try:
        assert run(lab.store.accessible_samples(ref, provider_org_id=lab.NEMO,
                                                purpose="provider_sharing")) == []
        with pytest.raises(errors.DomainError):
            run(versions.derive(lab.store, lab.objects, provider_org_id=lab.NEMO, actor="dev",
                                dataset_id=lw.uid(31, 0xda6), version=1,
                                created_at="2026-09-28T11:00:00Z",
                                policy=versions.SplitPolicy(seed=3), add=[ref]))
        with pytest.raises(errors.Forbidden):
            lab.freeze(payload)
        record = run(versions.export(lab.store, lab.objects, provider_org_id=lab.NEMO,
                                     dataset_ref=ref, export_id=lw.uid(31, 0xe96),
                                     now=datetime.now(timezone.utc), ttl_s=600))
        assert record["parts"] == [] and \
            {o["reason"] for o in record["omitted"]} == {"grant_not_current"}
    finally:
        lab.restore()
    assert len(run(lab.store.accessible_samples(ref, provider_org_id=lab.NEMO,
                                                purpose="provider_sharing"))) == 20
    assert lab.freeze(payload).cases


# ------------------------------------------------------------------------------------ j04
def placement(rows: dict[str, dict], key: str) -> dict[str, set[str]]:
    families = collections.defaultdict(set)
    for row in rows.values():
        families[row["row"][key]].add(row["split"])
    return families


def test_j04_related_sources_are_placed_whole_and_deterministically(lab, workdir):
    """Three spellings of one question (three episodes), two questions of one episode and two
    spans of one clip: each family lands in ONE split; the same inputs, policy and seed are the
    same version and split digest; the cross-episode family is returned for review. The trap
    bites: under seed 11, placed on their own, f1/f2/f3 and va/vb would land in different
    splits."""
    from infrx.datasets import versions
    fam = lab.dataset("families", 4).dataset_ref
    video = lab.dataset("video", 2).dataset_ref
    policy = versions.SplitPolicy(seed=11, train_bp=4000, validation_bp=3000)

    def derive(n: int, add):
        return run(versions.derive(lab.store, lab.objects, provider_org_id=lab.NEMO,
                                   actor="dev", dataset_id=lw.uid(n, 0xda6), version=1,
                                   created_at="2026-09-28T11:00:00Z", policy=policy, add=add))
    first, again = derive(40, [fam]), derive(40, [fam])
    assert (first.dataset_ref, first.split_digest) == (again.dataset_ref, again.split_digest)
    rows = lab.rows(first.dataset_ref)
    spread = {family: splits for family, splits in placement(rows, "family").items()
              if len(splits) > 1}
    assert spread == {}, f"a family was split: {spread}"
    moons = sorted(rows[i]["sample_id"] for i in ("F1a", "F1b", "F1c"))
    assert moons in [sorted(r) for r in first.review]
    clips = derive(41, [video])
    by_clip = collections.defaultdict(set)
    for row in lab.rows(clips.dataset_ref).values():
        by_clip[row["row"]["clip"]].add(row["split"])
    assert all(len(s) == 1 for s in by_clip.values()), dict(by_clip)
    lw.save(workdir, "families.json", {"ref": first.dataset_ref, "split": first.split_digest,
                                       "review": first.review,
                                       "placement": {k: rows[k]["split"] for k in sorted(rows)},
                                       "clips": {k: sorted(v) for k, v in by_clip.items()}})


def test_j04_a_leaking_base_is_refused_and_the_frozen_holdout_admits_no_relative(lab, workdir):
    """Import traps: one episode declared in two splits is `split_conflict` at the later row.
    A base whose declared train and holdout hold two spellings of one question is refused
    (`LeakRefused`, naming the family). Over a clean frozen base, a new spelling of a holdout
    question is omitted (`related_to_holdout`), a new question of a train episode joins train,
    an unrelated one is placed outside the holdout, and the holdout is exactly the base's."""
    from infrx.datasets import versions
    leaky = lab.dataset("split_leaky", 5)
    assert reasons(leaky) == {4: "split_conflict"}
    leaky_rows = lab.rows(leaky.dataset_ref)
    policy = versions.SplitPolicy(seed=5)
    with pytest.raises(versions.LeakRefused) as refused:
        run(versions.derive(lab.store, lab.objects, provider_org_id=lab.NEMO, actor="dev",
                            dataset_id=lw.uid(50, 0xda6), version=2,
                            created_at="2026-09-28T11:00:00Z", policy=policy,
                            base=leaky.dataset_ref))
    assert refused.value.leaks == [{"samples": sorted(leaky_rows[i]["sample_id"]
                                                      for i in ("K1", "K2")),
                                    "splits": ["holdout", "train"]}]
    base = lab.dataset("split_base", 6)
    additions = lab.dataset("split_additions", 7)
    derived = run(versions.derive(
        lab.store, lab.objects, provider_org_id=lab.NEMO, actor="dev",
        dataset_id=lab.spec("split_base", n=6)["dataset_id"], version=2,
        created_at="2026-09-28T11:00:00Z", policy=policy, base=base.dataset_ref,
        add=[additions.dataset_ref]))
    old, new = lab.rows(base.dataset_ref), lab.rows(derived.dataset_ref)
    add_rows = lab.rows(additions.dataset_ref)
    assert {"sample_id": add_rows["A1"]["sample_id"], "reason": "related_to_holdout"} in \
        derived.omitted
    assert "A1" not in new
    assert new["A3"]["split"] == "train"
    assert new["A2"]["split"] in ("train", "validation")
    holdout = sorted(r["sample_id"] for r in new.values() if r["split"] == "holdout")
    assert holdout == sorted(r["sample_id"] for r in old.values() if r["split"] == "holdout")
    assert {k: old[k]["split"] for k in old} == {k: new[k]["split"] for k in old}
    lw.save(workdir, "frozen-holdout.json", {"base": base.dataset_ref,
                                             "derived": derived.dataset_ref,
                                             "split": derived.split_digest,
                                             "omitted": derived.omitted})
