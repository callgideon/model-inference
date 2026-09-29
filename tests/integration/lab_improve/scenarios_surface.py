"""E7L i07 (annotation) and i08: the legs composition-4 bound - the I6 annotation worker as a
real process, and the provider's journey through `/lab/v1/pipelines` as the gateway composes it
(`Lab.surface`: `pilot._lab` with LAB_PIPELINES and LAB_TEACHERS on). Both run on the session's
own datasets and payers, so the other scenarios' numbers are untouched. Stand-ins (named in the
verdict): the session verifier, the checkpoint listing (WR-LAB2-4), B3's suite source and dev
deployer (WR-B3-3) and the teacher rate table. The provider UI over this surface
(`apps/lab/tests/e2e/improve/`, over the gateway's own production suites) is bound below.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import lab_world as lw
from lab_world import DECL, run

TEACH_PAYER = f"lab:payer:{{}}:{lw.uid(7, 0x9a7)}@sha256:{'7' * 64}"


def free_spare() -> int:
    for port in lw._gate().SPARE_PORTS:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError("every spare port of the e7l block is taken")


def until(what: str, done, timeout_s: float = 90.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = done()
        if found:
            return found
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


def annotation_worker(lab, workdir, n: int) -> subprocess.Popen:
    """`python -m infrx.lab.workers annotation` on this world's database, Lab objects (MinIO)
    and teacher fake - the unit's environment, nothing else of the caller's Lab settings."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LAB_", "JUDGE_"))}
    env.update({"LAB_DATABASE_URL": lab.dsn, "LAB_WORKER_HEALTH_PORT": str(free_spare()),
                "LAB_S3_BUCKET": lw.harness.S3_BUCKET, "LAB_S3_PREFIX": lab.prefix,
                "LAB_S3_ENDPOINT": lw.harness.s3_endpoint(), "LAB_TEACHER_URL": lab.teacher.url})
    log = open(workdir / f"annotation-worker-{n}.log", "wb")        # noqa: SIM115 - the child's
    return subprocess.Popen([sys.executable, "-m", "infrx.lab.workers", "annotation"],
                            cwd=lw.API, env=env, stdout=log, stderr=subprocess.STDOUT)


# ------------------------------------------------------------------------------------ i07
def test_i07_the_annotation_worker_process_resumes_a_batch_once(lab, workdir):
    """PIPELINE-BUDGET / TRAIN-RECOVER: a teacher batch planned and approved through the
    composed surface (3 chunks, one teacher job each, on its own payer) is collected by the
    annotation worker PROCESS; SIGKILLed once a chunk has settled and restarted, it finishes
    the rest: still one teacher job per chunk (the worker never submits), each chunk settled
    once at the reported cost with no hold left, every teacher label imported once
    (synthetic), the malformed answer one per-item failure."""
    from infrx.state.lab_consent import PgLabConsentStore
    from infrx.state.jobstore import connector
    ref, payer = lab.import_benchmark(4).dataset_ref, TEACH_PAYER.format(lab.NEMO)
    run(PgLabConsentStore(connector(lab.dsn)).put_budget(
        provider_org_id=lab.NEMO, payer_ref=payer, limit="10.00000000", actor=lab.DEV,
        reason="e7l i07"))
    batch_id = lw.uid(7, 0xb7)
    body = {"batch_id": batch_id, "dataset_ref": ref, "rubric_ref": lab.rubric,
            "teacher_model": lab.j1.JUDGE_MODEL, "prompt_version": "e7l-teach-7",
            "payer_ref": payer, "budget_usd": "5.00000000", "chunk_size": 4}
    planned = lab.call(lab.DEV, "POST", "teacher-batches", body)
    assert planned.status_code == 201, planned.json()
    approved = lab.call(lab.ADMIN, "POST", f"teacher-batches/{batch_id}/approve")
    assert approved.status_code == 200, approved.json()
    chunks = approved.json()["chunks"]
    assert [c["state"] for c in chunks] == ["submitted"] * 3
    runs = [run(lab.judge.run(c["run_id"])) for c in chunks]
    keys = {r.submit_key for r in runs}
    posts = [p for p in lab.teacher.posts if p["submit_key"] in keys]
    assert len(posts) == 3
    lab.answer_posts()

    def states():
        return [run(lab.judge.run(r.run_id)).state for r in runs]
    first = annotation_worker(lab, workdir, 1)
    try:
        killed_at = until("a chunk to settle", lambda: "completed" in states() and states())
    finally:
        first.kill()
        first.wait(30)
    second = annotation_worker(lab, workdir, 2)
    try:
        until("every chunk to settle", lambda: states() == ["completed"] * 3)
    finally:
        second.terminate()
        stopped = second.wait(30)
    assert (first.returncode, stopped) == (-9, 0)
    assert [p for p in lab.teacher.posts if p["submit_key"] in keys] == posts
    assert lab.judge_settled([r.run_id for r in runs]) == "0.03000000"
    assert lab.sql("select reserved::text from infrx.lab_budgets where provider_org_id = %s "
                   "and payer_ref = %s", lab.NEMO, payer)[0][0] == "0.00000000"
    events = [e for e in run(lab.log.events(ref, provider_org_id=lab.NEMO))
              if "annotation_ref" in e]
    assert len(events) == len({e["annotation_ref"] for e in events}), "a label twice"
    row_of = {v: k for k, v in lab.ids(ref).items()}
    sent = sorted(row_of[i["sample_id"]] for p in posts for i in p["items"])
    labelled = sorted(row_of[s] for s, _, record in lab.labels(ref).values()
                      if record.method == "synthetic")
    assert labelled == [r for r in sent if DECL["teacher"].get(r)]
    failures = [f for r in runs for f in run(lab.judge.failures(r.run_id))]
    assert [(row_of[s], why) for s, why in failures] == [("T08", "malformed_label")]
    lw.save(workdir, "i07.json", {"killed_at": killed_at, "runs": [r.run_id for r in runs],
                                  "posts": len(posts), "labels": labelled})


# ------------------------------------------------------------------------------------ i08
def test_i08_the_pipeline_surface_drives_labels_to_an_eligible_candidate(lab, workdir):
    """PIPELINE-LINEAGE through `/lab/v1/pipelines` as composed: human labels imported and
    reviewed, a train-only export, a manual bundle pinning the frozen holdout, submitted and
    finished; the provider's checkpoint validated and evaluated by P3's port over B3/B1 on the
    bundle's dataset (queued, the holdout digest what B1 froze); not eligible until the Lab
    eval worker's run succeeds, then eligible on exactly that holdout - never public."""
    lab.benchmark  # WR-LE2E-2: the WR-B3-3 suite stand-in reads it inside the route's event loop
    ref = lab.import_benchmark(3).dataset_ref
    ids, rubric, dev = lab.ids(ref), lab.rubric, lab.DEV
    rows = "\n".join(json.dumps({"sample_id": ids[r], "method": "human",
                                 "method_version": "e7l-8", "label": v,
                                 "annotator": "annotator-8"})
                     for r, v in sorted(DECL["human_labels"].items()))
    imported = lab.call(dev, "POST", "label-imports", {
        "import_id": lw.uid(8, 0x1b8), "dataset_ref": ref, "rubric_ref": rubric, "rows": rows})
    assert (imported.status_code, imported.json()["accepted"]) == (201, 5), imported.json()
    labels = lab.call(dev, "GET", "labels", dataset_ref=ref).json()["data"]
    for label in labels:
        assigned = lab.call(lab.ADMIN, "POST", "assignments", {
            "dataset_ref": ref, "sample_id": label["sample_id"], "reviewer_id": dev,
            "rubric_ref": rubric})
        assert assigned.status_code == 200, assigned.json()
        reviewed = lab.call(dev, "POST", "reviews", {
            "dataset_ref": ref, "annotation_ref": label["annotation_ref"],
            "decision": "accepted", "rubric_ref": rubric, "correction": None})
        assert reviewed.status_code == 200, reviewed.json()
    assert {x["state"] for x in lab.call(dev, "GET", "labels", dataset_ref=ref).json()[
        "data"]} == {"accepted"}
    export_id, ext, cid = lw.uid(8, 0xe88), lw.uid(8, 0xe78), lw.uid(1, 0xc78)
    export = lab.call(dev, "POST", "label-exports", {
        "export_id": export_id, "dataset_ref": ref, "adapter": "sft.1", "ttl_s": 3600})
    assert export.status_code == 201, export.json()
    prepared = lab.call(dev, "POST", "training-runs", {
        "external_run_id": ext, "dataset_ref": ref,
        "export": {"format": export.json()["format"], "export_id": export_id},
        "config": {k: lw.CONFIG[k] for k in ("objective", "adaptation", "base_model")},
        "payer_ref": lab.payer, "limit": "25.00000000"})
    assert prepared.status_code == 201, prepared.json()
    pin = prepared.json()["holdout"]["sha256"]
    for op, state in (("submit", "submitted"), ("finish", "completed")):
        moved = lab.call(dev, "POST", f"training-runs/{ext}/{op}")
        assert (moved.status_code, moved.json().get("state")) == (200, state), moved.json()
    from infrx.pipelines import training as p3
    key = f"lab/{lab.NEMO}/training/{ext}/checkpoints/1.json"
    data = json.dumps({"format": p3.DESCRIPTOR, "base_model": lw.CONFIG["base_model"],
                       "adaptation": lw.CONFIG["adaptation"], "files": ["adapter.safetensors"],
                       "metrics": DECL["checkpoints"]["cand1"]}).encode()
    run(lab.objects.put_if_absent(key, data, "application/json"))
    lab.cache.setdefault("suites", {})[cid] = (prepared.json()["run_ref"], "cand1")
    import hashlib
    checkpoint = lab.call(dev, "POST", "checkpoints", {
        "external_run_id": ext, "checkpoint_id": cid, "artifact_key": key,
        "artifact_digest": "sha256:" + hashlib.sha256(data).hexdigest()})
    assert checkpoint.status_code == 201, checkpoint.json()
    got = checkpoint.json()
    assert (got["state"], got["eligible"]) == ("validated", False), got
    evaluation = got["evaluation"]
    assert (evaluation["state"], evaluation["split"], evaluation["holdout_sha256"]) == \
        ("queued", "holdout", pin), evaluation
    early = lab.call(dev, "POST", f"checkpoints/{cid}/approve", {"external_run_id": ext})
    assert early.status_code == 409, early.json()
    report = lab.work(evaluation["run_ref"], "cand1")
    assert report["state"] == "succeeded", report
    approved = lab.call(dev, "POST", f"checkpoints/{cid}/approve", {"external_run_id": ext})
    assert approved.status_code == 200, approved.json()
    assert approved.json()["eligible"] is True
    assert approved.json()["evaluation"]["state"] == "succeeded"
    lw.save(workdir, "i08.json", {"prepared": prepared.json(), "checkpoint": got,
                                  "approved": approved.json(), "report": report})


def _e2e():
    """LAB-E2E's gate half (`apps/lab/tests/e2e/gate.py`), by path under this package's name
    (R213); a mutant copy has no apps/lab, so INFRX_LAB_DIR names the checkout's."""
    import importlib.util
    from pathlib import Path
    lab = Path(os.environ.get("INFRX_LAB_DIR") or lw.REPO / "apps" / "lab")
    spec = importlib.util.spec_from_file_location("lab_improve.lab_e2e_gate",
                                                  lab / "tests" / "e2e" / "gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_i08_the_provider_ui_drives_labels_to_a_checkpoint(workdir):
    """i08's provider-UI half (WR-C4-UI, LAB-E2E): `apps/lab/tests/e2e/improve` - the Lab's
    annotations and training pages built and served, signed in through their own form, over
    `/lab/v1/pipelines` as the gateway composes it with LAB_PIPELINES on (D8's label log and
    run ledger, D7, L2) on the l4 key: labels imported (a forged ground truth and a foreign
    sample refused with their reasons), assigned and reviewed, a train-only export, the manual
    bundle prepared, submitted and finished, a checkpoint returned and never eligible without
    its held-out evaluation, and the unsafe variants. The run and checkpoint listings are
    WR-LAB2-4's stand-in, as the API half's (`Listing`); a red suite fails this case. With
    `checkpoints.production_suites` composed (composition-5) the suite's backend seeds D8's
    subscription and L3's dev revision and the page shows the held-out evaluation queued; a
    green suite without them composed is NOT RUN (WR-B3-3)."""
    e2e = _e2e()
    got = e2e.run("improve", workdir)
    lw.save(workdir, "i08-ui.json", {k: v for k, v in got.items() if k != "tail"})
    absent = [port for port in e2e.missing(got) if port != "listings"]
    if absent:
        lw.not_run("i08", "WR-B3-3", why=f"{e2e.command('improve')} passed ({got['pass']} "
                   f"cases) through the returned checkpoint, but the gateway's own "
                   f"LAB_PIPELINES composition lacks {absent}: the held-out evaluation and the "
                   f"approval to an eligible candidate are not reachable from the page")
