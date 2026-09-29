"""E7L i01-i03: two authorized improvement iterations on the e7l stack.

Iteration 1 (i01, i02): the owned benchmark's labels - human rows imported through P1, a P2
teacher batch through J2's one egress path to the local teacher fake (published synthetic) -
reviewed and corrected, exported train-only, bundled for the provider's own training (the
manual connector), and its checkpoints imported through P3 onto D7's receipts and evaluated
by B1/B2 on the frozen holdout before anything is eligible.

Iteration 2 (i03): the grantor's post-deployment traces of candidate 1's serving version,
selected through N3 under its grant (its D6F corrections kept beside the original), derived
over the same frozen holdout, labelled, exported with both iterations' lineage, trained and
evaluated on that holdout again.

Every expectation is `fixtures/improve.json`'s; the synthetic endpoints' "improvement" is the
fixture's declaration, never a model-quality claim (P-07).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import lab_world as lw
import pytest
from infrx.contracts.lab.records import submit_key
from lab_world import DECL, run


def labels1(lab):
    """Iteration 1's labels, once per session."""
    if "labels1" in lab.cache:
        return lab.cache["labels1"]
    from infrx.pipelines.teachers import collect, plan, run_batch
    ref = lab.benchmark
    ids = lab.ids(ref)
    human = [{"sample_id": ids[r], "method": "human", "method_version": "e7l-1", "label": v,
              "annotator": "annotator-1"} for r, v in sorted(DECL["human_labels"].items())]
    forged = {"sample_id": ids[DECL["forged_ground_truth"]], "method": "human",
              "method_version": "e7l-1", "label": "Cairo", "ground_truth": True}
    stray = {"sample_id": lw.uid(99, 0xbad), "method": "human", "method_version": "e7l-1",
             "label": "x"}
    imported = lab.import_labels(ref, human + [forged, stray])
    batch, wiring = lab.batch(ref, 1), lab.wiring()
    planned = run(plan(batch, store=lab.store, objects=lab.objects, rates=wiring.rates,
                       now=run(lab.judge.db_now())))
    report = run(run_batch(batch, wiring=wiring))
    lab.answer_posts()
    collected = [run(collect(batch, r.run_id, wiring=wiring)) for r in report.runs]
    row_of = {v: k for k, v in ids.items()}
    for annotation, (sample, _, record) in sorted(lab.labels(ref).items()):
        row = row_of[sample]
        if record.method == "synthetic" and row in DECL["corrections"]:
            lab.review(ref, annotation, "rejected", correction=DECL["corrections"][row])
        else:
            lab.review(ref, annotation, "accepted")
    lab.cache["labels1"] = SimpleNamespace(
        ref=ref, ids=ids, row_of=row_of, imported=imported, plan=planned, report=report,
        collected=collected, export=lab.export(ref, 1),
        posts=[p for p in lab.teacher.posts
               if p["submit_key"] in {r.submit_key for r in report.runs}])
    return lab.cache["labels1"]


def training1(lab):
    """Iteration 1's training and held-out evaluation, once per session."""
    if "training1" in lab.cache:
        return lab.cache["training1"]
    from infrx.pipelines import training as p3
    one = labels1(lab)
    ext = lw.uid(1, 0xe71)
    bundle = lab.prepare(one.ref, one.export, ext)
    replay = lab.prepare(one.ref, one.export, ext)
    submitted = lab.submit_manual(ext)
    finished = run(p3.finish(lab.runs, lab.directory, provider_org_id=lab.NEMO,
                             user_id=lab.DEV, external_run_id=ext))
    good, bad = lab.checkpoint(ext, 1, serve="cand1"), lab.checkpoint(ext, 2, serve="bad")
    lab.cache["training1"] = SimpleNamespace(
        ext=ext, bundle=bundle, replay=replay, submitted=submitted, finished=finished,
        good=good, bad=bad, eligible=lab.approve(ext, 1),
        cand1=lab.compare("base", "cand1", one.ref), badly=lab.compare("base", "bad", one.ref))
    return lab.cache["training1"]


def iteration2(lab):
    """Iteration 2 over candidate 1's post-deployment traces, once per session."""
    if "iteration2" in lab.cache:
        return lab.cache["iteration2"]
    from infrx.datasets import versions
    from infrx.pipelines import training as p3
    from tests.d import checks_admission as ca
    one, first = labels1(lab), training1(lab)
    requests = []
    for n, trace in enumerate(DECL["traces"], 1):
        request = lab.admitted_request(lab.TRACER, ca.C2_KEY, f"e7l-trace-{n}")
        lab.feedback(lab.TRACER, request, trace["correction"], f"e7l{n}")
        lab.trace(lab.TRACER, request, trace["question"], trace["output"],
                  serving=lab.serving("cand1"), n=n)
        requests.append(request)
    selected = lab.select(lab.TRACER, requests, 2)
    derived = run(versions.derive(
        lab.store, lab.objects, provider_org_id=lab.NEMO, actor="dev@nemo",
        dataset_id=lw.uid(1, 0xda7), version=2, created_at="2026-09-28T12:30:00Z",
        policy=versions.SplitPolicy(seed=7), base=one.ref, add=[selected.dataset_ref],
        now=lab.db_now()))
    ref = derived.dataset_ref
    traced = {s.sample_id: lab.body(s) for s in lab.manifest(selected.dataset_ref).samples}
    rows = [{"sample_id": sid, "method": "human", "method_version": "e7l-2",
             "label": next((c["value"] for c in body["corrections"]), None),
             "annotator": "annotator-2"}
            for sid, body in sorted(traced.items())]
    imported = lab.import_labels(ref, rows)
    for annotation, (sample, state, _) in sorted(lab.labels(ref).items()):
        if sample in traced and state == "submitted":
            lab.review(ref, annotation, "accepted")
    export = lab.export(ref, 2)
    ext = lw.uid(2, 0xe71)
    bundle = lab.prepare(ref, export, ext)
    lab.submit_manual(ext)
    run(p3.finish(lab.runs, lab.directory, provider_org_id=lab.NEMO, user_id=lab.DEV,
                  external_run_id=ext))
    checkpoint = lab.checkpoint(ext, 5, serve="cand2")
    lab.cache["iteration2"] = SimpleNamespace(
        requests=requests, selected=selected, derived=derived, ref=ref, traced=traced,
        imported=imported, export=export, ext=ext, bundle=bundle, checkpoint=checkpoint,
        eligible=lab.approve(ext, 5), report=lab.compare("cand1", "cand2", ref),
        first=first)
    return lab.cache["iteration2"]


def by_row(one, sample_ids) -> list[str]:
    return sorted(one.row_of[s] for s in sample_ids if s in one.row_of)


# ------------------------------------------------------------------------------------ i01
def test_i01_labels_keep_their_method_evidence_and_review(lab, workdir):
    """PIPELINE-LINEAGE: the forged ground truth and the stray row are refused by row; every
    label keeps its method (human rows `imported`, the teacher's `synthetic`), its sample's
    own evidence and its provenance; the teacher's wrong label is kept rejected beside the
    reviewer's `human` correction; the original evidence is never overwritten; the export is
    exactly the fixture's train lines, lineage and omissions - the holdout never ships."""
    one = labels1(lab)
    assert one.imported.rejected == [{"row": 6, "reason": "forged_ground_truth"},
                                     {"row": 7, "reason": "missing_evidence"}]
    manifest = {s.sample_id: s for s in lab.manifest(one.ref).samples}
    got = {}
    for _, (sample, state, record) in lab.labels(one.ref).items():
        row = one.row_of[sample]
        assert record.evidence_ref == manifest[sample].source_ref, row
        assert record.ground_truth == (record.method == "human"), (row, record.method)
        assert lab.body(manifest[sample])["original"]["answer"] == \
            next(r["answer"] for r in lw.ROWS if r["id"] == row), "the evidence was edited"
        got.setdefault(row, []).append((record.method, state, record.label["value"]))
    assert sorted(got["T07"]) == [("human", "accepted", "Santiago"),
                                  ("synthetic", "rejected", "Valparaiso")]
    assert sorted(got["T01"]) == [("imported", "accepted", "Paris"),
                                  ("synthetic", "accepted", "Paris")]
    assert got["H01"] == [("imported", "accepted", "Berlin")]
    assert "T08" not in got, "a malformed teacher answer became a label"
    expected = DECL["export_1"]
    lineage = {one.row_of[x["sample_id"]]: x["methods"] for x in one.export["lineage"]}
    assert sorted(lineage) == expected["lines"] and lineage == expected["methods"]
    assert {one.row_of[o["sample_id"]]: o["reason"] for o in one.export["omitted"]} == \
        expected["omitted"]
    lines = lab.read_export(1)
    assert [line["completion"] for line in lines] == [
        DECL["corrections"].get(r) or DECL["human_labels"].get(r) or DECL["teacher"][r]
        for r in (one.row_of[x["sample_id"]] for x in one.export["lineage"])]
    lw.save(workdir, "export-1.json", one.export)


def test_i01_the_teacher_never_sees_the_holdout_and_its_labels_stay_synthetic(lab, workdir):
    """PIPELINE-LINEAGE / R57: the teacher batch plans and sends the train and validation
    samples only, each once, through J2's path; its labels are `synthetic`, never ground
    truth, pinned to the teacher model and prompt - even once a reviewer accepts them; a
    malformed answer is a per-item failure; every chunk settles once at the reported cost."""
    one = labels1(lab)
    splits = lab.splits(one.ref)
    sent = [item["sample_id"] for post in one.posts for item in post["items"]]
    assert sorted(sent) == sorted(s for s, n in splits.items() if n != "holdout")
    assert len(sent) == len(set(sent)) and len(one.posts) == len(one.report.runs) == 3
    assert {o["sample_id"] for o in one.plan.omitted} == \
        {s for s, n in splits.items() if n == "holdout"}
    assert [f for c in one.collected for f in c.failures] == [(one.ids["T08"], "malformed_label")]
    assert {c.run.state for c in one.collected} == {"completed"}
    for _, (sample, state, record) in lab.labels(one.ref).items():
        if record.method == "synthetic":
            assert record.ground_truth is False and record.reviewer_id is None
            assert record.label["provenance"] == {"model": lab.j1.JUDGE_MODEL,
                                                  "prompt": "e7l-teach-1"}
    lw.save(workdir, "teacher.json", {"posts": one.posts, "plan": one.plan.omitted})


# ------------------------------------------------------------------------------------ i02
def test_i02_the_bundle_trains_on_the_train_export_and_pins_the_holdout(lab, workdir):
    """TRAIN-RECOVER / PIPELINE-LINEAGE: the manual bundle names the train and dev ids, the
    export by its digest and the frozen holdout by its size and digest - no holdout id
    anywhere in it; a replay is the same bundle; the manual connector reserves nothing (the
    provider's own compute) and the provider declares it done."""
    one, first = labels1(lab), training1(lab)
    splits = lab.splits(one.ref)
    holdout = sorted(s for s, n in splits.items() if n == "holdout")
    bundle = first.bundle
    assert bundle == first.replay
    assert sorted(bundle["train"]) == sorted(s for s, n in splits.items() if n == "train")
    assert sorted(bundle["dev"]) == sorted(s for s, n in splits.items() if n == "validation")
    assert bundle["holdout"] == {"size": 12, "sha256": lw.pin_of(holdout)}
    assert bundle["export"] == {"format": "infrx.label_export.1",
                                "export_id": one.export["export_id"],
                                "sha256": one.export["sha256"]}
    assert not [h for h in holdout if h in json.dumps(bundle)], "the bundle carries the holdout"
    assert first.submitted["state"] == "submitted" and first.finished["state"] == "completed"
    assert lab.reservation(submit_key(first.ext)) is None, "the manual bundle reserves nothing"
    lw.save(workdir, "bundle-1.json", bundle)


def test_i02_a_candidate_is_eligible_only_after_its_holdout_evaluation(lab, workdir):
    """TRAIN-RECOVER: a validated checkpoint whose holdout run has not succeeded is never
    eligible; candidate 1, evaluated by B1 on exactly the bundle's frozen holdout, is
    eligible and B2 accepts it as the fixture declares; a redelivered checkpoint is the
    same outcome and evaluates nothing again."""
    from infrx.contracts import errors
    first = training1(lab)
    held = lab.checkpoint(first.ext, 3, serve="cand1", held=True)
    assert held["state"] == "validated"
    with pytest.raises(errors.StateConflict):
        lab.approve(first.ext, 3)
    assert first.good["state"] == "validated"
    evaluation = run(lab.evals.evaluation(provider_org_id=lab.NEMO,
                                          checkpoint_id=first.good["checkpoint_id"]))
    assert (evaluation["state"], evaluation["split"], evaluation["holdout_sha256"]) == \
        ("succeeded", "holdout", first.bundle["holdout"]["sha256"])
    assert first.eligible["evaluation"] == evaluation["run_ref"]
    calls = lab.evals.calls
    assert lab.checkpoint(first.ext, 1, serve="cand1") == first.good and lab.evals.calls == calls
    outcome, improved = DECL["decisions"]["cand1_vs_base"]
    assert (first.cand1["decision"]["outcome"],
            first.cand1["estimates"]["overall"]["improved"]) == (outcome, improved)
    lw.save(workdir, "report-base-cand1.json", first.cand1)


def test_i02_an_evaluation_on_another_holdout_never_makes_a_candidate_eligible(lab):
    """TRAIN-RECOVER (holdout leakage): a validated checkpoint whose succeeded evaluation ran
    on a holdout other than the bundle's frozen one - another pin, or another dataset
    version's - is never eligible; the same answer on the frozen holdout is."""
    from infrx.contracts import errors
    first = training1(lab)
    cid = first.good["checkpoint_id"]
    kept = lab.evals.done[cid]
    try:
        for other in ({"holdout_sha256": "sha256:" + "0" * 64},
                      {"dataset_ref": lw.uid(9, 0xda8), "holdout_sha256": "sha256:" + "1" * 64}):
            lab.evals.done[cid] = {**kept, **other}
            with pytest.raises(errors.StateConflict):
                lab.approve(first.ext, 1)
    finally:
        lab.evals.done[cid] = kept
    assert lab.approve(first.ext, 1)["evaluation"] == first.eligible["evaluation"]


def test_i02_the_bad_checkpoint_with_the_better_training_loss_is_rejected(lab, workdir):
    """TRAIN-RECOVER: the seeded bad checkpoint reports the lower training loss and scores
    better than the baseline on the train cases, yet B2 over the frozen holdout rejects it:
    training metrics never decide."""
    first = training1(lab)
    losses = DECL["checkpoints"]
    assert losses["bad"]["train_loss"] < losses["cand1"]["train_loss"]
    one = labels1(lab)
    train = {s for s, n in lab.splits(one.ref).items() if n == "train"}

    def train_score(name):
        from infrx.evaluation import reports
        frozen = run(lab.evaluate(name, one.ref))
        rows = run(lab.store.run_results(frozen.run.run_id, provider_org_id=lab.NEMO))
        return sum(r.get("score") or 0 for r in run(reports.case_records(
            frozen, rows, lab.objects)) if r["case_id"] in train)
    assert train_score("bad") > train_score("base")
    outcome, improved = DECL["decisions"]["bad_vs_base"]
    assert first.bad["state"] == "validated"
    assert (first.badly["decision"]["outcome"],
            first.badly["estimates"]["overall"]["improved"]) == (outcome, improved)
    assert first.badly["decision"]["reasons"] == ["overall inferior"]
    lw.save(workdir, "report-base-bad.json", first.badly)


def test_i02_missing_and_late_checkpoints_are_rejected_and_never_evaluated(lab):
    """TRAIN-RECOVER: a checkpoint without its artifact, one whose bytes are not the declared
    digest, and one arriving after its run was cancelled are rejected receipts in D7 and
    queue no evaluation."""
    from infrx.pipelines import training as p3
    one, first = labels1(lab), training1(lab)
    calls = lab.evals.calls
    missing = lab.checkpoint(first.ext, 6, serve="cand1", data=b"",
                             declared="sha256:" + "0" * 64)
    tampered = lab.checkpoint(first.ext, 7, serve="cand1", data=b"tampered",
                              declared="sha256:" + "1" * 64)
    ext = lw.uid(3, 0xe71)
    lab.prepare(one.ref, one.export, ext)
    lab.submit_manual(ext)
    run(p3.cancel(lab.runs, p3.ManualConnector(), provider_org_id=lab.NEMO,
                  external_run_id=ext))
    late = lab.checkpoint(ext, 8, serve="cand1")
    assert (missing.get("reason"), tampered.get("reason"), late.get("reason")) == \
        ("missing_artifact", "digest_mismatch", "run_cancelled")
    assert lab.sql("select array_agg(state order by checkpoint_id) from "
                   "infrx.lab_checkpoint_receipts where checkpoint_id = any(%s)",
                   [lw.uid(n, 0xc7e) for n in (6, 7, 8)]) == [(["rejected"] * 3,)]
    assert lab.evals.calls == calls


# ------------------------------------------------------------------------------------ i03
def test_i03_permitted_traces_become_a_derived_version_over_the_frozen_holdout(lab, workdir):
    """DATA-LINEAGE: candidate 1's traces become samples under the grantor's current grant,
    each with its trace lineage (grantor, request, grant, model) and the grantor's D6F
    correction beside - never merged into - the original output; the derived version keeps
    iteration 1's holdout exactly and places the traces in train/validation only."""
    from infrx.datasets import lineage
    one, two = labels1(lab), iteration2(lab)
    assert (two.selected.samples, two.selected.omitted) == (4, [])
    grantor = lab.org(lab.TRACER)
    for sid, body in two.traced.items():
        trace = run(lineage.trace_of(lab.objects, provider_org_id=lab.NEMO, sample_id=sid))
        assert (trace["grantor_org_id"], trace["model_id"]) == (grantor, lab.cc.MODEL)
        assert trace["request_id"] in two.requests
        declared = next(t for t in DECL["traces"]
                        if t["question"] == body["original"]["request"]["messages"][0]["content"])
        assert body["original"]["output"]["content"] == declared["output"]
        assert [(c["name"], c["value"], c["author_role"]) for c in body["corrections"]] == \
            [("correction", declared["correction"], "customer")]
    v1, v2 = lab.splits(one.ref), lab.splits(two.ref)
    assert sorted(s for s, n in v2.items() if n == "holdout") == \
        sorted(s for s, n in v1.items() if n == "holdout")
    assert {v2[s] for s in two.traced} <= {"train", "validation"}
    assert lab.manifest(two.ref).parent_refs == [one.ref, two.selected.dataset_ref]
    assert two.derived.omitted == []
    lw.save(workdir, "derived.json", {"ref": two.ref, "splits": v2, "omitted": two.derived.omitted})


def test_i03_the_second_iteration_improves_on_the_same_holdout_with_its_lineage(lab, workdir):
    """DATA-LINEAGE / PIPELINE-LINEAGE: iteration 2's export carries iteration 1's accepted
    labels (logged under the parent version) and the trace labels of the train traces, no
    holdout sample; its bundle pins the SAME holdout as iteration 1's; candidate 2 is
    evaluated on it, eligible, and B2 decides as the fixture declares against candidate 1."""
    one, two = labels1(lab), iteration2(lab)
    splits = lab.splits(two.ref)
    shipped = {x["sample_id"]: x["methods"] for x in two.export["lineage"]}
    first = {x["sample_id"] for x in one.export["lineage"]}
    assert first <= set(shipped)
    assert {s for s in shipped if s not in first} == \
        {s for s in two.traced if splits[s] == "train"}
    assert {tuple(shipped[s]) for s in two.traced if s in shipped} <= {("imported",)}
    assert not [s for s in shipped if splits[s] == "holdout"]
    assert two.bundle["holdout"] == two.first.bundle["holdout"]
    assert two.checkpoint["state"] == "validated"
    evaluation = run(lab.evals.evaluation(provider_org_id=lab.NEMO,
                                          checkpoint_id=two.checkpoint["checkpoint_id"]))
    assert (evaluation["state"], evaluation["dataset_ref"], evaluation["holdout_sha256"]) == \
        ("succeeded", two.ref, two.bundle["holdout"]["sha256"])
    assert two.eligible["evaluation"] == evaluation["run_ref"]
    outcome, improved = DECL["decisions"]["cand2_vs_cand1"]
    assert (two.report["decision"]["outcome"],
            two.report["estimates"]["overall"]["improved"]) == (outcome, improved)
    lw.save(workdir, "export-2.json", two.export)
    lw.save(workdir, "report-cand1-cand2.json", two.report)


def test_i03_budgets_reconcile_per_unit_across_both_iterations(lab, workdir):
    """PIPELINE-BUDGET: after both iterations the teacher's PROVIDER_USD, on the provider's
    one named payer, is exactly the reported cost of each settled chunk with no hold left;
    the manual training runs reserved nothing; evaluation spent CREDIT on the provider_dev
    wallet only, the D7 attempts' costs in CREDIT and nothing else."""
    from infrx.contracts.v2.money_units import ProviderUsd
    one = labels1(lab)
    iteration2(lab)
    runs = [c.run for c in one.collected]
    assert {r.payer_ref for r in runs} == {lab.payer}
    reported = ProviderUsd(lab.teacher.cost)
    total = ProviderUsd.zero()
    for r in runs:
        assert r.actual == reported
        total = total + r.actual
    assert lab.judge_settled([r.run_id for r in runs]) == str(total), \
        "the teacher's PROVIDER_USD is exactly the reported cost of each settled chunk"
    assert lab.budget()["reserved"] == "0.00000000", "no hold left on the payer's budget"
    assert all(lab.reservation(submit_key(lw.uid(n, 0xe71))) is None for n in (1, 2)), \
        "the manual training runs reserved nothing"
    units = {row[0] for row in lab.sql(
        "select distinct cost_unit from infrx.lab_eval_attempts where cost_unit is not null")}
    assert units == {"CREDIT"}
    assert all(str(w.debited) != "0" for _, w in lab.wallets)
    lw.save(workdir, "budget.json", {"teacher_spent": str(total), "eval_units": sorted(units),
                                     "wallets": {n: str(w.debited) for n, w in lab.wallets}})
