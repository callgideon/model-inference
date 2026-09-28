#!/usr/bin/env python3
"""N3 (13-lab-improvement-handoffs §N3; DATA-RIGHTS, DATA-LINEAGE): datasets derived from
permitted traces through L2, T3 and C2 only, with D6F feedback appended as separate
corrections, lineage per sample, and revocation / deletion / expiry tombstones that reach
every derived version and export - in the fake world of `world.py`; `test_lineage_pg.py`
reruns the rights scenarios on the real L2/D7/D6F PostgreSQL. Every case is named by a
mutant in `mutants.py`.

    uv run --frozen pytest -q tests/n/lineage/test_lineage.py
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from infrx.contracts import errors
from infrx.datasets import imports, lineage, versions

from ..imports.world import chunks, fixture, grant_ref, run
from .world import (CATEGORIES, CONSUMER, DEV, GRANTOR, GRANTOR_2, MODEL, NEMO, OTHER, VIEWER,
                    World, rid)

SELECTION, DATASET = "5e000000-0000-4000-8000-000000000001", "da000000-0000-4000-8000-0000000000e1"
POLICY = versions.SplitPolicy(seed=3, train_bp=5000, validation_bp=5000)


def select(w: World, requests, *, user=DEV, grantor=GRANTOR, selection=SELECTION,
           dataset=DATASET, version=1):
    return run(lineage.select(
        access=w.access, retention=w.retention, content=w.content, feedback=w.feedback,
        model_of=w.model_of, store=w.lab, objects=w.objects, user_id=user, provider_org_id=NEMO,
        grantor_org_id=grantor, model_id=MODEL, selection_id=selection, dataset_id=dataset,
        version=version, created_at="2026-09-27T12:30:00Z", request_ids=list(requests),
        actor="dev@nemo"))


def ok(w: World, requests, **kw) -> lineage.Selected:
    try:
        return select(w, requests, **kw)
    except errors.DomainError as refused:
        raise AssertionError(f"{type(refused).__name__}: {refused}") from None


def refused(w: World, requests, **kw) -> str:
    with pytest.raises(errors.DomainError) as caught:
        select(w, requests, **kw)
    return type(caught.value).__name__


def body(w: World, sample) -> dict:
    return json.loads(run(w.objects.get(imports.sample_key(NEMO, sample.content_digest))))


def permitted(w: World, ref: str, purpose: str = "provider_sharing") -> set[str]:
    return run(lineage.permitted(w.lab, w.objects, ref, provider_org_id=NEMO, purpose=purpose,
                                 now=w.now))


def owned_import(w: World) -> str:
    """A provider-owned N1 import (no trace behind it), under its own grant."""
    w.lab.add_grant()
    spec, data = fixture("benchmark")
    return run(imports.Importer(w.lab, w.objects).run(
        {**spec, "grant_ref": grant_ref()}, chunks(data), provider_org_id=NEMO,
        actor="dev@nemo")).dataset_ref


# --- N3.a: selection ---------------------------------------------------------------------
def test_n3_permitted_traces_become_a_dataset_with_lineage_per_sample() -> None:
    """Oracle: each selected trace is one sample whose content object keeps the original
    request and output and the trace's time span, with the grantor's feedback appended as
    separate correction records (the original untouched, no author identity); the source
    is registered under the current grant's ref; every sample has a lineage entry naming
    its grantor, request, grant and content bound. Dropping the corrections, merging them
    into the content, a wrong span, or a missing lineage entry fails."""
    w = World()
    a = w.trace(1, feedback=(("rating", 2), ("thumb", False)))
    b = w.trace(2)
    got = ok(w, [b, a])
    assert (got.samples, got.omitted) == (2, []), got
    manifest = run(w.lab.resolve(got.dataset_ref, provider_org_id=NEMO))
    grant = w.directory.grants[(GRANTOR, NEMO)]
    assert {(s.source_ref, s.grant_ref) for s in manifest.samples} == \
        {(got.source_ref, grant_ref(grant.grant_id, NEMO, grant.version))}
    assert got.source_ref.startswith(f"lab:source:{NEMO}:{SELECTION}@sha256:")
    bodies = {body(w, s)["trace"]["request_id"]: (s, body(w, s)) for s in manifest.samples}
    assert set(bodies) == {a, b}
    sample, first = bodies[a]
    assert first["original"] == first["content"] == {
        "request": {"messages": [{"role": "user", "content": "question 1"}]},
        "output": {"content": "answer 1"}}
    row = w.traces.rows[0]
    assert first["trace"] == {"grantor_org_id": GRANTOR, "request_id": a,
                              "started_at": row.started_at.isoformat(),
                              "completed_at": row.completed_at.isoformat()}
    assert [(c["name"], c["value"], c["author_role"]) for c in first["corrections"]] == \
        [("rating", 2, "customer"), ("thumb", False, "customer")]
    assert all("author_principal" not in c for c in first["corrections"])
    assert bodies[b][1]["corrections"] == []
    entry = run(lineage.trace_of(w.objects, provider_org_id=NEMO, sample_id=sample.sample_id))
    assert entry == {"sample_id": sample.sample_id, "selection_id": SELECTION,
                     "grantor_org_id": GRANTOR, "request_id": a, "grant_id": grant.grant_id,
                     "model_id": MODEL, "content_digest": sample.content_digest,
                     "categories": ["request_content", "response_content", "feedback"],
                     "content_until": (row.started_at + timedelta(days=90)).isoformat()}
    assert sample.group_key == a
    assert permitted(w, got.dataset_ref) == {s.sample_id for s in manifest.samples}


def test_n3_feedback_joins_only_under_a_feedback_grant() -> None:
    """Oracle (independent purposes and categories): GRANTOR_2's grant does not name the
    feedback category, so its traces are selected without their feedback rows; a join that
    ignored the category fails."""
    w = World()
    got = ok(w, [w.trace(3, GRANTOR_2, feedback=(("rating", 5),))], grantor=GRANTOR_2)
    manifest = run(w.lab.resolve(got.dataset_ref, provider_org_id=NEMO))
    assert body(w, manifest.samples[0])["corrections"] == []


def test_n3_selection_goes_through_l2_t3_and_c2_only() -> None:
    """Oracle (DATA-RIGHTS): a consumer-only user, a viewer and a grant without the response
    category are refused before T3 or C2 is asked; a request T3 does not project (another
    grantor's, deleted, or never captured) is omitted as `missing_projection` without asking
    C2 - a missing projection never substitutes for permission; expired content is omitted
    before C2; a ref C2 refuses is omitted as `content_denied`; a C2 outage publishes
    nothing; nothing permitted publishes nothing; another provider cannot read the result."""
    w = World()
    live, other, gone, never, old, lost = (
        w.trace(1), w.trace(2, GRANTOR_2), w.trace(3), rid(4),
        w.trace(5, started=w.now - timedelta(days=90)), w.trace(6))
    run(w.delete(GRANTOR, gone))
    assert refused(w, [live], user=CONSUMER) == "NotFound"
    assert refused(w, [live], user=VIEWER) == "Forbidden"
    grant = w.directory.grants[(GRANTOR, NEMO)]
    w.directory.grants[(GRANTOR, NEMO)] = grant.model_copy(
        update={"categories": (grant.categories[0],)})
    assert refused(w, [live]) == "Forbidden"
    w.directory.grants[(GRANTOR, NEMO)] = grant
    assert w.content.calls == []
    run(w.trace_objects.delete(w.traces.rows[-1].content_key))
    got = ok(w, [live, other, gone, never, old, lost])
    assert sorted((o["request_id"], o["reason"]) for o in got.omitted) == sorted([
        (other, "missing_projection"), (gone, "missing_projection"),
        (never, "missing_projection"), (old, "content_expired"), (lost, "content_denied")])
    assert sorted(w.content.calls) == sorted([live, lost]), w.content.calls
    assert got.samples == 1
    w.content.down = True
    assert refused(w, [live], selection=rid(0x51), dataset=rid(0xd2)) == \
        "DependencyUnavailable"
    w.content.down = False
    assert refused(w, [gone, never], selection=rid(0x52), dataset=rid(0xd3)) == \
        "InvalidRequest"
    assert len(w.lab.published) == 1 and set(w.lab.sources) == {SELECTION}
    with pytest.raises(errors.NotFound):
        run(lineage.status(w.lab, w.objects, got.dataset_ref, provider_org_id=OTHER, now=w.now))


def test_n3_a_trace_of_another_model_is_omitted_before_c2() -> None:
    """Oracle (0-DS4-B1, DATA-RIGHTS): the grant names MODEL only; a request of the same
    grantor served by another model - its id known from the provider's own request metadata -
    is omitted as `model_not_granted` and C2 is never asked for it; a ref C2 signed for a
    model the grant does not name is refused even when issued."""
    w = World()
    mine, theirs = w.trace(1), w.trace(9, model="kimi-k3")
    got = ok(w, [mine, theirs])
    assert (got.samples, got.omitted) == (1, [{"request_id": theirs,
                                               "reason": "model_not_granted"}])
    assert w.content.calls == [mine]
    manifest = run(w.lab.resolve(got.dataset_ref, provider_org_id=NEMO))
    assert [body(w, s)["trace"]["request_id"] for s in manifest.samples] == [mine]
    assert refused(w, [theirs], selection=rid(0x53), dataset=rid(0xd4)) == "InvalidRequest"
    grant = w.directory.grants[(GRANTOR, NEMO)]
    ref = run(w.content.sign(provider_org_id=NEMO, grantor_org_id=GRANTOR, request_id=theirs,
                             grant_id=grant.grant_id, model_id="kimi-k3"))
    with pytest.raises(errors.Forbidden):
        run(w.content.read(ref, provider_org_id=NEMO))


def test_n3_the_fan_out_is_bounded() -> None:
    """Oracle: more than `MAX_SELECT` requests, none, or a repeated request id is refused
    before any port is asked; reconcile and push tombstones work in pages of `limit`."""
    w = World()
    requests = [w.trace(n) for n in range(1, 4)]
    many = [rid(n) for n in range(lineage.MAX_SELECT + 1)]
    assert refused(w, many) == "InvalidRequest"
    assert refused(w, []) == "InvalidRequest"
    assert refused(w, [rid(1), rid(1)]) == "InvalidRequest"
    assert w.content.calls == [] and w.lab.published == []
    ok(w, requests)
    page = run(lineage.reconcile(w.directory, w.retention, w.objects, provider_org_id=NEMO,
                                 limit=2))
    assert page["checked"] == 2 and page["next"] is not None
    rest = run(lineage.reconcile(w.directory, w.retention, w.objects, provider_org_id=NEMO,
                                 after=page["next"], limit=2))
    assert rest["checked"] == 1 and rest["next"] is None
    first = run(lineage.tombstone(w.objects, provider_org_id=NEMO, grantor_org_id=GRANTOR,
                                  reason="grant_revoked", at=w.now, limit=2))
    assert first == {"tombstoned": 2, "more": True}
    second = run(lineage.tombstone(w.objects, provider_org_id=NEMO, grantor_org_id=GRANTOR,
                                   reason="grant_revoked", at=w.now, limit=2))
    assert second == {"tombstoned": 1, "more": False}


# --- N3.b/c: revocation, deletion and expiry reach every derived version and export -------
def test_n3_revocation_tombstones_every_derived_version_and_export() -> None:
    """Oracle (revoke after selection, during a queue wait, before external submission): a
    derived version and an export made before the revocation stop serving the trace samples
    at once - the gate a queued job re-checks before it submits returns none of them, a new
    derivation omits them, a part read drops them; reconcile then tombstones them for good
    (a later re-grant does not bring them back) with evidence that holds no content and
    says nothing was recalled or unlearned."""
    w = World()
    requests = [w.trace(n) for n in range(1, 5)]
    traced = ok(w, requests)
    owned = owned_import(w)
    derived = run(versions.derive(w.lab, w.objects, provider_org_id=NEMO, actor="dev@nemo",
                                  dataset_id=rid(0xd5), version=1, now=w.now,
                                  created_at="2026-09-27T13:00:00Z", policy=POLICY,
                                  add=[traced.dataset_ref, owned])).dataset_ref
    manifest = run(w.lab.resolve(derived, provider_org_id=NEMO))
    trace_ids = {s.sample_id for s in manifest.samples if s.source_ref == traced.source_ref}
    assert len(trace_ids) == 4
    queued = permitted(w, derived, "training")         # what a queued job saw
    assert trace_ids <= queued
    export = run(versions.export(w.lab, w.objects, provider_org_id=NEMO, dataset_ref=derived,
                                 export_id=rid(0xe1), now=w.now, ttl_s=3600))
    w.revoke(GRANTOR)
    assert permitted(w, derived, "training") & trace_ids == set()   # before submission
    assert permitted(w, traced.dataset_ref) == set()
    view = run(lineage.status(w.lab, w.objects, traced.dataset_ref, provider_org_id=NEMO,
                              now=w.now))
    assert {s["restricted"] for s in view["samples"]} == {"grant_not_current"}
    part = run(versions.read_part(w.lab, w.objects, provider_org_id=NEMO, export_id=rid(0xe1),
                                  part=0, now=w.now))
    assert {json.loads(line)["sample_id"] for line in part.splitlines()} & trace_ids == set()
    report = run(lineage.reconcile(w.directory, w.retention, w.objects, provider_org_id=NEMO))
    assert sorted(t["reason"] for t in report["tombstoned"]) == ["grant_not_current"] * 4
    assert report["purged"] == 0                       # logical now; physical after retention
    w.grant(GRANTOR, 0x91, *w.directory.grants[(GRANTOR, NEMO)].categories[2:])
    assert permitted(w, derived, "training") & trace_ids == set(), "a re-grant resurrected"
    part = run(versions.read_part(w.lab, w.objects, provider_org_id=NEMO, export_id=rid(0xe1),
                                  part=0, now=w.now))
    assert {json.loads(line)["sample_id"] for line in part.splitlines()} & trace_ids == set()
    later = run(versions.export(w.lab, w.objects, provider_org_id=NEMO, dataset_ref=derived,
                                export_id=rid(0xe2), now=w.now, ttl_s=3600))
    assert {o["sample_id"] for o in later["omitted"]} == trace_ids
    again = run(versions.derive(w.lab, w.objects, provider_org_id=NEMO, actor="dev@nemo",
                                dataset_id=rid(0xd6), version=1, now=w.now,
                                created_at="2026-09-27T14:00:00Z", policy=POLICY,
                                add=[derived]))
    assert {o["sample_id"] for o in again.omitted} == trace_ids
    evidence = run(lineage.export_evidence(w.objects, provider_org_id=NEMO,
                                           export_id=rid(0xe1), now=w.now))
    assert (evidence["dataset_ref"], evidence["recalled"]) == (derived, False)
    assert sorted(a["sample_id"] for a in evidence["affected"]) == sorted(trace_ids)
    assert export["created_at"] == evidence["delivered_from"]
    stones = [run(w.objects.get(k)) for k in run(w.objects.keys(f"lab/{NEMO}/lineage/tomb"))]
    retained = b"".join(stones) + json.dumps(evidence).encode()
    assert b"question" not in retained and b"answer" not in retained


def test_n3_a_narrowed_grant_version_tombstones_its_trace_samples() -> None:
    """Oracle (0-DS4-M1, independent categories): a new current version of the same grant
    that drops the response category, or the model, tombstones the samples it no longer
    covers as `grant_narrowed` at reconcile and every gate drops them; dropping feedback
    reaches only samples that carry corrections; a new version that narrows nothing a
    sample uses leaves it readable."""
    w = World()
    rated, plain = w.trace(1, feedback=(("rating", 2),)), w.trace(2)
    other = w.trace(3, GRANTOR_2)
    got = ok(w, [rated, plain])
    got_2 = ok(w, [other], grantor=GRANTOR_2, selection=rid(0x54), dataset=rid(0xd7))
    ids = {body(w, s)["trace"]["request_id"]: s.sample_id for ref in
           (got.dataset_ref, got_2.dataset_ref)
           for s in run(w.lab.resolve(ref, provider_org_id=NEMO)).samples}

    def reconcile():
        return {t["sample_id"]: t["reason"] for t in run(lineage.reconcile(
            w.directory, w.retention, w.objects, provider_org_id=NEMO))["tombstoned"]}
    w.narrow(GRANTOR_2, retention_days=7)               # narrows nothing a sample uses
    w.narrow(GRANTOR, categories=CATEGORIES)            # drops feedback only
    assert reconcile() == {ids[rated]: "grant_narrowed"}
    assert permitted(w, got.dataset_ref, "training") == {ids[plain]}
    w.narrow(GRANTOR, categories=(CATEGORIES[0],))      # drops the response category
    w.narrow(GRANTOR_2, model_ids=("some-other-model",))
    assert reconcile() == {ids[plain]: "grant_narrowed", ids[other]: "grant_narrowed"}
    assert permitted(w, got.dataset_ref, "training") == set()
    assert permitted(w, got_2.dataset_ref, "training") == set()


def test_n3_deletion_and_expiry_deny_at_once_and_purge_after_retention() -> None:
    """Oracle: content past its T3 bound is out of every gate the moment the bound passes,
    before any reconcile; a T3 deletion is out at the push tombstone; reconcile records both
    (`deleted`, `content_expired`) and only then deletes the sample copies; the status view
    explains each restricted sample and keeps its lineage."""
    w = World()
    old = w.trace(1, started=w.now - timedelta(days=89, hours=23))
    kept, doomed = w.trace(2), w.trace(3)
    got = ok(w, [old, kept, doomed])
    ids = {body(w, s)["trace"]["request_id"]: s for s in
           run(w.lab.resolve(got.dataset_ref, provider_org_id=NEMO)).samples}
    w.advance(hours=2)
    assert permitted(w, got.dataset_ref) == {ids[kept].sample_id, ids[doomed].sample_id}
    run(w.delete(GRANTOR, doomed))
    assert run(lineage.tombstone(w.objects, provider_org_id=NEMO, grantor_org_id=GRANTOR,
                                 request_id=doomed, reason="deleted", at=w.now)) == \
        {"tombstoned": 1, "more": False}
    assert permitted(w, got.dataset_ref) == {ids[kept].sample_id}
    report = run(lineage.reconcile(w.directory, w.retention, w.objects, provider_org_id=NEMO))
    assert sorted(t["reason"] for t in report["tombstoned"]) == ["content_expired"]
    assert report["purged"] == 2
    for request, present in ((old, False), (doomed, False), (kept, True)):
        stored = run(w.objects.get(imports.sample_key(NEMO, ids[request].content_digest)))
        assert (stored is not None) == present, request
    view = run(lineage.status(w.lab, w.objects, got.dataset_ref, provider_org_id=NEMO,
                              now=w.now))
    assert {s["trace"]["request_id"]: s["restricted"] for s in view["samples"]} == \
        {old: "content_expired", kept: None, doomed: "deleted"}
