#!/usr/bin/env python3
"""P1 (13-lab-improvement-handoffs §P1; PIPELINE-LINEAGE, DATA-SPLIT): import pipeline labels
with their provenance, review/correct/adjudicate them with role checks and rubric versions,
select authorized examples, and export train-only examples - in the fake world of `world.py`.
`test_annotations_pg.py` reruns the core on the real D7 and L2 stores. Every case is named by
a mutant in `mutants.py`.

    uv run --frozen pytest -q tests/p/annotations/test_annotations.py
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pytest
from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.datasets import imports
from infrx.media.store import InMemoryObjectStore
from infrx.pipelines import annotations as p1

from ...n.imports.world import NEMO, grant_ref, run
from ...n.versions.test_versions import derive, imported, uid
from .world import (ADMIN, DEV, DEV2, GONE, GRANT_2, GRANT_3, NOW, RUBRIC, RUBRIC_2, VIEWER,
                    manifest, rows, split_ids, tombstone_regranted, world)


def label_rows(sample_ids, **over) -> list[dict]:
    return [{"sample_id": s, "method": "model", "method_version": "labeler-3",
             "label": {"answer": f"a{i}"}, **over} for i, s in enumerate(sample_ids)]


def load(store, log, ref, items, rubric=RUBRIC, objects=None) -> p1.Imported:
    """P1.a; `objects` holds N3's lineage (none: nothing is tombstoned)."""
    return run(p1.import_labels(store, log, objects=objects or InMemoryObjectStore(), now=NOW,
                                provider_org_id=NEMO, actor="dev@nemo",
                                dataset_ref=ref, rubric_ref=rubric, rows=items))


def assign(log, access, ref, sample, reviewer, by=ADMIN, rubric=RUBRIC):
    return run(p1.assign(log, access, provider_org_id=NEMO, user_id=by, dataset_ref=ref,
                         sample_id=sample, reviewer_id=reviewer, rubric_ref=rubric))


def review(store, log, access, ref, label, decision="accepted", user=DEV, rubric=RUBRIC,
           **kw):
    return run(p1.review(store, log, access, provider_org_id=NEMO, user_id=user,
                         dataset_ref=ref, annotation_ref=label, decision=decision,
                         rubric_ref=rubric, **kw))


def accept_all(store, log, access, ref, labels, user=DEV):
    for label in labels:
        sample = record(store, label).sample_id
        assign(log, access, ref, sample, user)
        review(store, log, access, ref, label, user=user)


def adjudicate(store, log, access, ref, sample, value, user=DEV2):
    return run(p1.adjudicate(store, log, access, provider_org_id=NEMO, user_id=user,
                             dataset_ref=ref, sample_id=sample, value=value, rubric_ref=RUBRIC))


def record(store, ref):
    return run(store.resolve(ref, provider_org_id=NEMO))


def export(store, log, objects, ref, adapter="sft.1", export_id=None, ttl_s=3600):
    return run(p1.export(store, log, objects, provider_org_id=NEMO, dataset_ref=ref,
                         export_id=export_id or uid(1, 0xe7), adapter=adapter, now=NOW,
                         ttl_s=ttl_s))


def lines(objects, rec) -> list[dict]:
    return [json.loads(x) for x in run(objects.get(rec["examples_key"])).splitlines()]


def state(log, ref) -> dict[str, str]:
    return p1._states(run(log.events(ref, provider_org_id=NEMO)))


# --- P1.a: import with provenance -------------------------------------------------------------
def test_p1_import_keeps_provenance_and_links_the_original_evidence() -> None:
    """Oracle (PIPELINE-LINEAGE): a model row is `synthetic`, a human row `imported`; neither
    is ground truth; the record links the sample's own source as evidence and carries the
    annotator, model, prompt, confidence, spans and declared method; the sample's content
    object is untouched; a re-import is the same refs and no new log events."""
    store, log, objects, _, ref = world()
    m = manifest(store, ref)
    before = run(objects.get(imports.sample_key(NEMO, m.samples[0].content_digest)))
    items = [{"sample_id": m.samples[0].sample_id, "method": "model", "method_version": "v3",
              "label": {"answer": "yes"}, "annotator": "labeler", "model": "m-7b",
              "prompt": "Is it?", "confidence": 0.75, "spans": [[0, 1500], [2000, 2600]]},
             {"sample_id": m.samples[1].sample_id, "method": "human", "method_version": "tool-2",
              "label": "no", "annotator": "ann-17"}]
    got = load(store, log, ref, items)
    assert got.rejected == [] and len(got.accepted) == 2, got
    synth, human = (record(store, r) for r in got.accepted)
    assert (synth.method, human.method) == ("synthetic", "imported")
    assert not synth.ground_truth and not human.ground_truth and human.reviewer_id is None
    assert synth.evidence_ref == m.samples[0].source_ref and synth.state == "submitted"
    assert synth.label == {"value": {"answer": "yes"}, "declared_method": "model",
                           "provenance": {"annotator": "labeler", "model": "m-7b",
                                          "prompt": "Is it?", "confidence": 0.75,
                                          "spans": [[0, 1500], [2000, 2600]]}}
    assert human.label["declared_method"] == "human" and synth.rubric_ref == RUBRIC
    assert run(objects.get(imports.sample_key(NEMO, m.samples[0].content_digest))) == before
    events = len(log.rows)
    assert load(store, log, ref, items).accepted == got.accepted and len(log.rows) == events


def test_p1_rejected_rows_are_reported_with_their_reason() -> None:
    """Oracle: a row naming no sample of the dataset is missing evidence, a ground-truth
    claim is forged, and an unreadable mapping (unknown method, no version, no label, an
    extra key, confidence outside 0..1, an empty, reversed or non-list span, a non-finite
    number) is
    `bad_mapping` - each reported by row number, none published."""
    store, log, _, _, ref = world()
    s = manifest(store, ref).samples[0].sample_id
    ok = label_rows([s])[0]
    bad = [{**ok, "sample_id": uid(9, 0x99)}, {**ok, "ground_truth": True},
           {**ok, "method": "oracle"}, {k: v for k, v in ok.items() if k != "method_version"},
           {k: v for k, v in ok.items() if k != "label"}, {**ok, "extra": 1},
           {**ok, "confidence": 1.5}, {**ok, "confidence": True}, {**ok, "spans": [[5, 5]]},
           {**ok, "spans": [[9, 3]]}, {**ok, "spans": 5}, {**ok, "label": float("nan")}, ok]
    got = load(store, log, ref, bad)
    assert got.rejected == [{"row": 1, "reason": "missing_evidence"},
                            {"row": 2, "reason": "forged_ground_truth"},
                            *({"row": n, "reason": "bad_mapping"} for n in range(3, 13))], got
    assert len(got.accepted) == 1 and len(log.rows) == 1


def test_p1_each_step_reads_its_own_gate_now() -> None:
    """Oracle (DATA-RIGHTS): labels import through the access gate (provider_sharing) and
    export through the training gate: a sharing-only source imports and reviews but exports
    nothing (`grant_not_current`); after a grant is revoked, its samples' labels are
    `grant_not_current` and nothing is published."""
    store, log, objects, access, ref = world(grant=GRANT_3)
    labels = load(store, log, ref, label_rows(split_ids(store, ref)["train"])).accepted
    accept_all(store, log, access, ref, labels)
    rec = export(store, log, objects, ref)
    assert rec["items"] == 0 and {o["reason"] for o in rec["omitted"]} == {"grant_not_current"}
    store.revoke(grant_ref(GRANT_3))
    events = len(log.rows)
    got = load(store, log, ref, label_rows([s.sample_id for s in manifest(store, ref).samples]))
    assert {r["reason"] for r in got.rejected} == {"grant_not_current"} and not got.accepted
    assert len(log.rows) == events


# --- P1.b: review ----------------------------------------------------------------------------
def test_p1_a_forged_or_unassigned_reviewer_is_denied() -> None:
    """Oracle (PIPELINE-LINEAGE): only an administrator assigns, only a current
    developer-or-above is assigned or reviews, a review needs its own assignment under the
    rubric version it cites; every refusal leaves the log unchanged."""
    store, log, _, access, ref = world()
    (label,) = load(store, log, ref, label_rows([manifest(store, ref).samples[0].sample_id])
                    ).accepted
    sample = record(store, label).sample_id
    for by, who in ((DEV, DEV2), (ADMIN, VIEWER), (ADMIN, GONE)):
        with pytest.raises(errors.Forbidden):
            assign(log, access, ref, sample, who, by=by)
    assign(log, access, ref, sample, DEV)
    before = list(log.rows)
    for user, rubric in ((VIEWER, RUBRIC), (GONE, RUBRIC), (DEV2, RUBRIC), (DEV, RUBRIC_2)):
        with pytest.raises(errors.Forbidden):
            review(store, log, access, ref, label, user=user, rubric=rubric)
    assert log.rows == before and state(log, ref)[label] == "submitted"
    access.memberships[(NEMO, DEV)] = access.memberships[(NEMO, DEV)].model_copy(
        update={"revoked_at": NOW})                      # revoked after its assignment
    with pytest.raises(errors.Forbidden):
        review(store, log, access, ref, label)
    assert log.rows == before


def test_p1_a_review_moves_a_label_once() -> None:
    """Oracle: accept moves submitted -> accepted by F3's machine; the same review again is
    a replay (no event), another decision on it a conflict; an accepted synthetic label stays
    synthetic and not ground truth; a label is reviewed only under its own dataset."""
    store, log, objects, access, ref = world()
    (label,) = load(store, log, ref, label_rows([manifest(store, ref).samples[0].sample_id])
                    ).accepted
    assign(log, access, ref, record(store, label).sample_id, DEV)
    review(store, log, access, ref, label)
    events = len(log.rows)
    review(store, log, access, ref, label)
    assert len(log.rows) == events and state(log, ref)[label] == "accepted"
    with pytest.raises(errors.IdempotencyConflict):
        review(store, log, access, ref, label, "rejected")
    assert (record(store, label).method, record(store, label).ground_truth) == ("synthetic",
                                                                                False)
    other = imported(store, objects, rows(2, "o"), 3, split=True)
    with pytest.raises(errors.NotFound):             # a label of another dataset
        review(store, log, access, other, label)


def test_p1_a_human_correction_round_trips_with_provenance() -> None:
    """Oracle (PIPELINE-LINEAGE): a correction rejects the pipeline label, which still
    resolves unchanged, and writes the reviewer's `human` ground-truth label on the same
    evidence citing what it corrects; replaying it is the same label; the training export
    carries the correction with its lineage."""
    store, log, objects, access, ref = world()
    train = split_ids(store, ref)["train"][0]
    (label,) = load(store, log, ref, label_rows([train])).accepted
    original = record(store, label)
    assign(log, access, ref, train, DEV)
    with pytest.raises(errors.InvalidRequest):
        review(store, log, access, ref, label, "accepted", correction={"answer": "fixed"})
    fixed = review(store, log, access, ref, label, "rejected", correction={"answer": "fixed"})
    human = record(store, fixed)
    assert (human.method, human.reviewer_id, human.ground_truth, human.state) == (
        "human", DEV, True, "accepted")
    assert human.evidence_ref == original.evidence_ref
    assert human.label == {"value": {"answer": "fixed"}, "provenance": {"corrects": label}}
    assert record(store, label) == original and state(log, ref)[label] == "rejected"
    with pytest.raises(errors.StateConflict):            # accepted -> rejected is undeclared
        review(store, log, access, ref, fixed, "rejected")
    events = len(log.rows)
    assert review(store, log, access, ref, label, "rejected",
                  correction={"answer": "fixed"}) == fixed and len(log.rows) == events
    rec = export(store, log, objects, ref)
    assert rec["lineage"] == [{"sample_id": train, "label_refs": [fixed], "methods": ["human"]}]
    assert lines(objects, rec)[0]["completion"] == {"answer": "fixed"}


def test_p1_only_an_adjudication_supersedes() -> None:
    """Oracle (0-P1-SUPERSEDE-BYPASS): a review accepts or rejects and nothing else - another
    assigned reviewer cannot retire a human ground-truth correction by reviewing it
    `superseded` (or back to `submitted`); the log is unchanged and the correction still
    exports."""
    store, log, objects, access, ref = world()
    train = split_ids(store, ref)["train"][0]
    (label,) = load(store, log, ref, label_rows([train])).accepted
    assign(log, access, ref, train, DEV)
    fixed = review(store, log, access, ref, label, "rejected", correction={"answer": "fixed"})
    assign(log, access, ref, train, DEV2)
    before = list(log.rows)
    for decision in ("superseded", "submitted"):
        with pytest.raises(errors.InvalidRequest):
            review(store, log, access, ref, fixed, decision, user=DEV2)
    assert log.rows == before and state(log, ref)[fixed] == "accepted"
    assert export(store, log, objects, ref)["lineage"] == [
        {"sample_id": train, "label_refs": [fixed], "methods": ["human"]}]


def test_p1_a_disagreement_across_versions_is_adjudicated() -> None:
    """Oracle (0-P1-CROSS-VERSION-DISAGREEMENT-STUCK): a label accepted on the parent and a
    differing one accepted on a child that keeps the sample in train are one disagreement of
    the child - the export omits it and `disagreements` and `adjudicate` see it; the
    adjudication supersedes the parent's label under the parent's log and the child's under
    the child's, and the child then exports the adjudicated label alone; the adjudicator
    reviewed neither label, on either version; a parent-only sample is not the child's."""
    store, log, objects, access, parent = world()
    t = next(s for s in manifest(store, parent).samples
             if s.sample_id == split_ids(store, parent)["train"][0])
    child = run(store.publish({
        "schema": "lab.dataset_manifest.1", "provider_org_id": NEMO,
        "dataset_id": uid(9, 0xda), "version": 1, "created_at": "2026-09-27T15:00:00Z",
        "derivation": "derive", "parent_refs": [parent], "samples": [t.model_dump()],
        "splits": {"train": [t.sample_id], "validation": [], "holdout": []}},
        provider_org_id=NEMO, actor="dev@nemo"))
    (old,) = load(store, log, parent, label_rows([t.sample_id])).accepted
    (new,) = load(store, log, child, [{**label_rows([t.sample_id])[0],
                                       "label": {"answer": "other"}}]).accepted
    elsewhere = split_ids(store, parent)["train"][1]     # the parent's own disagreement
    load(store, log, parent, [label_rows([elsewhere])[0],
                              {**label_rows([elsewhere])[0], "label": 2}])
    accept_all(store, log, access, parent, [old], user=DEV2)
    accept_all(store, log, access, child, [new])
    assert export(store, log, objects, child)["omitted"] == [
        {"sample_id": t.sample_id, "reason": "disagreement"}]
    assert run(p1.disagreements(store, log, provider_org_id=NEMO, dataset_ref=child)) == {
        t.sample_id: sorted([old, new])}
    assign(log, access, child, t.sample_id, DEV2)
    with pytest.raises(errors.Forbidden):             # DEV2 reviewed the parent's label
        adjudicate(store, log, access, child, t.sample_id, {"answer": "x"})
    assign(log, access, child, t.sample_id, ADMIN)
    final = adjudicate(store, log, access, child, t.sample_id, {"answer": "x"}, user=ADMIN)
    assert (state(log, parent)[old], state(log, child)[new]) == ("superseded", "superseded")
    rec = export(store, log, objects, child, export_id=uid(2, 0xe7))
    assert rec["lineage"] == [{"sample_id": t.sample_id, "label_refs": [final],
                               "methods": ["human"]}] and rec["omitted"] == []


def test_p1_a_disagreement_is_adjudicated_by_an_independent_reviewer() -> None:
    """Oracle: two accepted labels that differ are a disagreement the export refuses; the
    reviewer who accepted them cannot adjudicate; an independent assigned reviewer writes one
    `human` label, the disputed ones are superseded and kept; a replay is the same, another
    value a conflict; a sample without a disagreement cannot be adjudicated."""
    store, log, objects, access, ref = world()
    train = split_ids(store, ref)["train"]
    a, b = load(store, log, ref, [label_rows([train[0]])[0],
                                  {**label_rows([train[0]])[0], "method": "human",
                                   "label": {"answer": "other"}}]).accepted
    same = load(store, log, ref, label_rows([train[1]])).accepted
    accept_all(store, log, access, ref, [a, b, *same])
    assert run(p1.disagreements(store, log, provider_org_id=NEMO, dataset_ref=ref)) == {
        train[0]: sorted([a, b])}
    assert {o["reason"] for o in export(store, log, objects, ref)["omitted"]
            if o["sample_id"] == train[0]} == {"disagreement"}
    with pytest.raises(errors.Forbidden):
        adjudicate(store, log, access, ref, train[0], {"answer": "x"}, user=DEV)
    with pytest.raises(errors.Forbidden):             # not assigned to this sample
        adjudicate(store, log, access, ref, train[0], {"answer": "x"})
    assign(log, access, ref, train[0], DEV2)
    assign(log, access, ref, train[1], DEV2)
    with pytest.raises(errors.StateConflict):        # one accepted label: nothing to settle
        adjudicate(store, log, access, ref, train[1], {"answer": "x"})
    final = adjudicate(store, log, access, ref, train[0], {"answer": "x"})
    human = record(store, final)
    assert (human.method, human.reviewer_id, human.ground_truth) == ("human", DEV2, True)
    assert human.label["provenance"] == {"adjudicates": sorted([a, b])}
    assert state(log, ref)[a] == state(log, ref)[b] == "superseded"
    assert run(p1.disagreements(store, log, provider_org_id=NEMO, dataset_ref=ref)) == {}
    events = len(log.rows)
    assert adjudicate(store, log, access, ref, train[0], {"answer": "x"}) == final
    assert len(log.rows) == events
    with pytest.raises(errors.IdempotencyConflict):
        adjudicate(store, log, access, ref, train[0], {"answer": "y"})
    rec = export(store, log, objects, ref, export_id=uid(2, 0xe7))
    assert [x["label_refs"] for x in rec["lineage"] if x["sample_id"] == train[0]] == [[final]]


def test_p1_an_adjudication_rejects_submitted_labels() -> None:
    """Oracle: a disputed label nobody reviewed yet is rejected (not superseded) by the
    adjudication, and the corrector of a label may not adjudicate against it."""
    store, log, _, access, ref = world()
    s = split_ids(store, ref)["train"][0]
    a, b = load(store, log, ref, [label_rows([s])[0], {**label_rows([s])[0], "label": 2}]
                ).accepted
    assign(log, access, ref, s, DEV)
    fixed = review(store, log, access, ref, a, "rejected", correction=3)
    with pytest.raises(errors.Forbidden):
        adjudicate(store, log, access, ref, s, 3, user=DEV)
    assign(log, access, ref, s, DEV2)
    adjudicate(store, log, access, ref, s, 3)
    assert state(log, ref)[b] == "rejected" and state(log, ref)[fixed] == "superseded"


def test_p1_select_keeps_splits_and_only_authorized_samples() -> None:
    """Oracle (DATA-SPLIT, DATA-RIGHTS): a selection is a new derived version of the chosen
    samples the access gate allows now, each in its original split; an unknown id adds
    nothing and a revoked source's samples are left out."""
    store, log, objects, _, a = world()
    b = imported(store, objects, rows(4, "b"), 2, grant=GRANT_2, split=True)
    both = derive(store, objects, add=[a, b]).dataset_ref
    store.revoke(grant_ref(GRANT_2))
    chosen = [s.sample_id for s in manifest(store, both).samples] + [uid(9, 0x99)]
    new = run(p1.select(store, objects=objects, now=NOW, provider_org_id=NEMO, actor="dev@nemo",
                        dataset_ref=both,
                        sample_ids=chosen, dataset_id=uid(7, 0xda), version=1,
                        created_at="2026-09-27T14:00:00Z"))
    m, parent = manifest(store, new), split_ids(store, both)
    assert m.parent_refs == [both] and m.derivation == "derive"
    assert {s.sample_id for s in m.samples} == {s.sample_id for s in manifest(store, a).samples}
    for split, ids in split_ids(store, new).items():
        assert set(ids) <= set(parent[split]), split


# --- P1.c: exports ---------------------------------------------------------------------------
def test_p1_a_training_export_is_train_only_and_byte_identical() -> None:
    """Oracle (DATA-SPLIT, PIPELINE-LINEAGE): accepted labels on train samples are examples;
    validation and holdout labels are omitted by split, unlabelled samples are not examples;
    the lines are the adapter's schema; a rerun is the same record and bytes, and bytes an
    earlier attempt left under the id are never replaced; after the
    grant stops allowing training, a new export omits them and the same export id with other
    bytes is a conflict; unknown adapters and non-UUID ids are refused."""
    store, log, objects, access, ref = world()
    ids = split_ids(store, ref)
    labels = load(store, log, ref, label_rows(ids["train"] + ids["validation"] + ids["holdout"])
                  ).accepted
    accept_all(store, log, access, ref, labels)
    pending = load(store, log, ref, label_rows(ids["train"][:1], method="human")).accepted
    rec = export(store, log, objects, ref)
    assert [x["sample_id"] for x in rec["lineage"]] == sorted(ids["train"])
    assert pending[0] not in {r for x in rec["lineage"] for r in x["label_refs"]}
    assert {o["reason"] for o in rec["omitted"]} == {"validation", "holdout"}
    assert len(rec["omitted"]) == len(ids["validation"] + ids["holdout"])
    assert rec["items"] == len(ids["train"]) and rec["split"] == "train"
    assert all(set(x) == {"prompt", "completion"} for x in lines(objects, rec))
    assert rec["lineage"][0]["methods"] == ["synthetic"]
    assert export(store, log, objects, ref) == rec
    assert rec["sha256"] == hashlib.sha256(
        run(objects.get(rec["examples_key"]))).hexdigest()
    stale = uid(5, 0xe7)                   # an earlier attempt died after other bytes landed
    objects.seed(f"lab/{NEMO}/label-exports/{stale}/examples.jsonl", b"other\n")
    with pytest.raises(errors.Conflict):
        export(store, log, objects, ref, export_id=stale)
    store.revoke(grant_ref())
    later = export(store, log, objects, ref, export_id=uid(3, 0xe7))
    assert later["items"] == 0 and "grant_not_current" in {o["reason"] for o in later["omitted"]}
    with pytest.raises(errors.Conflict):
        export(store, log, objects, ref)
    for adapter, eid in (("rlhf.1", uid(4, 0xe7)), ("sft.1", "../x")):
        with pytest.raises(errors.InvalidRequest):
            export(store, log, objects, ref, adapter=adapter, export_id=eid)


def test_p1_a_label_export_expires_and_rereads_the_training_gate() -> None:
    """Oracle (1-PIPE-R2, DATA-RIGHTS): a label export lives 1 s..7 days (N2's bound) and
    records its expiry; `read_export` serves its lines while the training grant stands, drops
    a sample's line once the grant is revoked, and is `Gone` at expiry."""
    store, log, objects, access, ref = world()
    labels = load(store, log, ref, label_rows(split_ids(store, ref)["train"])).accepted
    accept_all(store, log, access, ref, labels)
    for ttl in (0, 7 * 86_400 + 1):
        with pytest.raises(errors.InvalidRequest):
            export(store, log, objects, ref, ttl_s=ttl)
    rec = export(store, log, objects, ref)
    assert rec["expires_at"] == (NOW + timedelta(seconds=3600)).isoformat()

    def read(now=NOW):
        return run(p1.read_export(store, objects, provider_org_id=NEMO,
                                  export_id=rec["export_id"], now=now))
    assert read() == run(objects.get(rec["examples_key"])) and rec["items"] > 0
    with pytest.raises(errors.Gone):
        read(NOW + timedelta(seconds=3600))
    store.revoke(grant_ref())
    assert read() == b""


def test_p1_preference_pairs_map_or_are_reported() -> None:
    """Oracle: the preference adapter emits {prompt, chosen, rejected} from a label holding
    exactly a chosen and a rejected answer; any other label is `bad_mapping`, not dropped."""
    store, log, objects, access, ref = world()
    t = split_ids(store, ref)["train"]
    labels = load(store, log, ref, [
        {**label_rows([t[0]])[0], "label": {"chosen": "A", "rejected": "B"}},
        {**label_rows([t[1]])[0], "label": {"chosen": "A"}}]).accepted
    accept_all(store, log, access, ref, labels)
    rec = export(store, log, objects, ref, adapter="preference.1")
    assert [(x["chosen"], x["rejected"]) for x in lines(objects, rec)] == [("A", "B")]
    assert set(lines(objects, rec)[0]) == {"prompt", "chosen", "rejected"}
    assert rec["omitted"] == [{"sample_id": t[1], "reason": "bad_mapping"}]


def test_p1_holdout_descendants_never_reach_a_training_export() -> None:
    """Oracle (DATA-SPLIT): a child version that moves an ancestor's holdout sample into
    train (even re-described), or adds a train sample with a holdout sample's content or
    group, cannot export it (`holdout_descendant`); a train sample unrelated to the holdout,
    labelled on the parent version, is exported from the child."""
    store, log, objects, access, parent = world()
    m = manifest(store, parent)
    ids = split_ids(store, parent)
    (h,) = [s for s in m.samples if s.sample_id == ids["holdout"][0]]
    t = next(s for s in m.samples if s.sample_id == ids["train"][0])
    moved = {**h.model_dump(), "content_digest": t.content_digest, "group_key": "moved"}
    copy = {**h.model_dump(), "sample_id": uid(1, 0xc0), "group_key": "copy"}
    kin = {**t.model_dump(), "sample_id": uid(2, 0xc0), "group_key": h.group_key}
    child = run(store.publish({
        "schema": "lab.dataset_manifest.1", "provider_org_id": NEMO,
        "dataset_id": uid(8, 0xda), "version": 1, "created_at": "2026-09-27T15:00:00Z",
        "derivation": "derive", "parent_refs": [parent],
        "samples": [moved, t.model_dump(), copy, kin],
        "splits": {"train": [h.sample_id, t.sample_id, copy["sample_id"], kin["sample_id"]],
                   "validation": [], "holdout": []}}, provider_org_id=NEMO, actor="dev@nemo"))
    labels = load(store, log, child, label_rows([h.sample_id, copy["sample_id"],
                                                 kin["sample_id"]])).accepted
    accept_all(store, log, access, child, labels)
    accept_all(store, log, access, parent, load(store, log, parent, label_rows([t.sample_id])
                                                ).accepted)          # labelled on the parent
    rec = export(store, log, objects, child)
    assert [x["sample_id"] for x in rec["lineage"]] == [t.sample_id]
    assert sorted(o["sample_id"] for o in rec["omitted"]) == sorted(
        [h.sample_id, copy["sample_id"], kin["sample_id"]])
    assert {o["reason"] for o in rec["omitted"]} == {"holdout_descendant"}
    assert records.REF_RE.fullmatch(child)


# --- R193 (0-E7L-1): a re-grant never resurrects a tombstoned sample --------------------------
def regranted_world():
    """Labels accepted on every train sample and an export made; then the grant is revoked,
    N3 tombstones the first train sample, and the grant comes back (D7 reads it again)."""
    store, log, objects, access, ref = world()
    train = sorted(split_ids(store, ref)["train"])
    accept_all(store, log, access, ref, load(store, log, ref, label_rows(train)).accepted)
    rec = export(store, log, objects, ref)
    tombstone_regranted(store, objects, train[0])
    assert train[0] in run(store.accessible_samples(ref, provider_org_id=NEMO,
                                                    purpose="training"))
    return store, log, objects, ref, train, rec


def test_p1_a_regrant_imports_no_label_for_a_tombstoned_sample() -> None:
    """Oracle (R193, DATA-LINEAGE): after the re-grant a label row for the tombstoned
    sample is refused `grant_not_current` and nothing is logged; its neighbours import."""
    store, log, objects, ref, train, _ = regranted_world()
    events = len(log.rows)
    got = load(store, log, ref, label_rows(train, method="human"), objects=objects)
    assert got.rejected == [{"row": 1, "reason": "grant_not_current"}], got
    assert len(got.accepted) == len(train) - 1 and len(log.rows) == events + len(train) - 1


def test_p1_a_regrant_selects_no_tombstoned_sample() -> None:
    """Oracle (R193): a selection after the re-grant leaves the tombstoned sample out."""
    store, _, objects, ref, train, _ = regranted_world()
    new = run(p1.select(store, objects=objects, now=NOW, provider_org_id=NEMO,
                        actor="dev@nemo", dataset_ref=ref, sample_ids=train,
                        dataset_id=uid(8, 0xda), version=1, created_at="2026-09-27T14:00:00Z"))
    assert {s.sample_id for s in manifest(store, new).samples} == set(train[1:])


def test_p1_a_regrant_exports_no_tombstoned_sample() -> None:
    """Oracle (R193): a new training export after the re-grant omits the tombstoned sample
    (`grant_not_current`) and ships its neighbours."""
    store, log, objects, ref, train, _ = regranted_world()
    later = export(store, log, objects, ref, export_id=uid(6, 0xe7))
    assert [x["sample_id"] for x in later["lineage"]] == train[1:]
    assert {"sample_id": train[0], "reason": "grant_not_current"} in later["omitted"]


def test_p1_a_regrant_rereads_no_tombstoned_line() -> None:
    """Oracle (R193): an export made before the tombstone serves, after the re-grant, every
    line but the tombstoned sample's."""
    store, _, objects, _, train, rec = regranted_world()
    whole = run(objects.get(rec["examples_key"])).splitlines(keepends=True)
    assert [x["sample_id"] for x in rec["lineage"]] == train
    assert run(p1.read_export(store, objects, provider_org_id=NEMO,
                              export_id=rec["export_id"], now=NOW)) == b"".join(whole[1:])
