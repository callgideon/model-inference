"""E7L i04-i06: the faults of an iteration on the e7l stack - a revocation mid-iteration
(RACER's traces, so nothing else loses its grant), a duplicate and an ambiguous teacher
submit through J2's path to the local teacher fake, and the automatic training connector's
timeout after accept and lost poll against P3's protocol server over TCP.

The i05 batches take ledgers of their own (J2's `FakeJudgeLedger`, the D6J stand-in) so the
session ledger i03 reconciles stays exact; the teacher fake is the session's.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from types import SimpleNamespace

import lab_world as lw
import pytest
from infrx.contracts import errors
from infrx.contracts.lab.records import submit_key
from lab_world import DECL, run


# ------------------------------------------------------------------------------------ i04
def revoked(lab):
    """RACER's traces through a whole iteration's prefix, then the revocation, once."""
    if "revoked" in lab.cache:
        return lab.cache["revoked"]
    from infrx.datasets import versions
    from scenarios_iterate import training1
    requests = [f"7e7e0000-0000-4000-8000-{n:012x}" for n in range(1, 4)]
    for n, (request, trace) in enumerate(zip(requests, DECL["traces"]), 11):
        lab.trace(lab.RACER, request, trace["question"], trace["output"],
                  serving=lab.serving("cand1"), n=n)
    selected = lab.select(lab.RACER, requests, 4)
    ref = selected.dataset_ref
    samples = sorted(s.sample_id for s in lab.manifest(ref).samples)
    rows = [{"sample_id": s, "method": "human", "method_version": "e7l-r", "label": "x"}
            for s in samples]
    lab.import_labels(ref, rows)
    for annotation in sorted(lab.labels(ref)):
        lab.review(ref, annotation, "accepted")
    export = lab.export(ref, 4)
    ext = lw.uid(4, 0xe71)
    bundle = lab.prepare(ref, export, ext)
    dataset_export = run(versions.export(lab.store, lab.objects, provider_org_id=lab.NEMO,
                                         dataset_ref=ref, export_id=lw.uid(4, 0xe9),
                                         now=lab.db_now(), ttl_s=3600))
    before = SimpleNamespace(lines=len(lab.read_export(4)), permitted=lab.permitted(ref))
    training1(lab)                        # iteration 1's world is built before RACER revokes
    lab.revoke(lab.RACER)
    lab.cache["revoked"] = SimpleNamespace(
        ref=ref, samples=samples, rows=rows, export=export, ext=ext, bundle=bundle,
        dataset_export=dataset_export, before=before, reconciled=lab.reconcile())
    return lab.cache["revoked"]


def test_i04_a_revocation_stops_the_queue_the_submit_and_the_export_reads(lab, workdir):
    """DATA-LINEAGE: before the revocation the three trace samples were labelled, exported
    and bundled; after it the annotation queue refuses their rows, the prepared training run
    cannot submit (it stays prepared: nothing left), both exports serve none of them, N3's
    gate is empty, reconcile tombstones each with its reason, the provenance view marks them
    restricted, and the export evidence lists them - delivered, NOT recalled, no unlearning
    claim."""
    from infrx.datasets import lineage, versions
    r = revoked(lab)
    assert r.before.lines == 3 and r.before.permitted == set(r.samples)
    queued = lab.import_labels(r.ref, [dict(row, method_version="e7l-r2") for row in r.rows])
    assert queued.accepted == [] and {x["reason"] for x in queued.rejected} == \
        {"grant_not_current"}
    with pytest.raises(errors.Forbidden):
        lab.submit_manual(r.ext)
    assert run(lab.runs.get(r.ext, provider_org_id=lab.NEMO))["state"] == "prepared"
    assert lab.read_export(4) == []
    part = run(versions.read_part(lab.store, lab.objects, provider_org_id=lab.NEMO,
                                  export_id=lw.uid(4, 0xe9), part=0, now=lab.db_now()))
    assert part.strip() == b""
    assert lab.permitted(r.ref) == set()
    assert sorted(r.reconciled["tombstoned"], key=lambda x: x["sample_id"]) == \
        [{"sample_id": s, "reason": "grant_not_current"} for s in r.samples]
    view = run(lineage.status(lab.store, lab.objects, r.ref, provider_org_id=lab.NEMO,
                              now=lab.db_now()))
    assert {s["restricted"] for s in view["samples"]} == {"grant_not_current"}
    evidence = run(lineage.export_evidence(lab.objects, provider_org_id=lab.NEMO,
                                           export_id=lw.uid(4, 0xe9), now=lab.db_now()))
    assert evidence["recalled"] is False and evidence["note"] == lineage.NOT_RECALLED
    assert [a["sample_id"] for a in evidence["affected"]] == r.samples
    lw.save(workdir, "revocation.json", {"reconciled": r.reconciled, "evidence": evidence,
                                         "view": view})


def regranted(lab):
    """RACER grants again (after `revoked`); the caller revokes once done."""
    r = revoked(lab)
    lab.put_trace_grant(lab.RACER)
    return r


def test_i04_a_regrant_leaves_the_n3_gate_closed(lab):
    """DATA-LINEAGE: D7 reads the samples again under the re-grant, but N3's gate - the
    tombstones are permanent - stays closed, and a derivation omits them."""
    from infrx.datasets import versions
    r = regranted(lab)
    try:
        assert sorted(run(lab.store.accessible_samples(
            r.ref, provider_org_id=lab.NEMO, purpose="training"))) == r.samples
        assert lab.permitted(r.ref) == set(), "a re-grant resurrected a tombstoned sample"
        with pytest.raises(errors.InvalidRequest):
            run(versions.derive(lab.store, lab.objects, provider_org_id=lab.NEMO,
                                actor="dev@nemo", dataset_id=lw.uid(9, 0xda8), version=1,
                                created_at="2026-09-28T13:00:00Z",
                                policy=versions.SplitPolicy(seed=7), add=[r.ref],
                                now=lab.db_now()))
    finally:
        lab.revoke(lab.RACER)


def test_i04_a_regrant_resurrects_no_tombstoned_sample_into_training(lab, workdir):
    """DATA-LINEAGE (transitive revocation): after the re-grant every training path must
    still refuse the tombstoned samples - a new P1 label export and the prepared P3 run's
    submit (N3: `permitted` is the one gate every export and external submission
    re-checks). Today P1/P3 read D7's `accessible_samples` only: 0-E7L-1."""
    r = regranted(lab)
    try:
        leaks = []
        export = lab.export(r.ref, 5)
        shipped = sorted({x["sample_id"] for x in export["lineage"]} & set(r.samples))
        if shipped:
            leaks.append(f"P1 export ships {len(shipped)} tombstoned samples")
        try:
            submitted = lab.submit_manual(r.ext)
            leaks.append(f"P3 submit of the prepared bundle -> {submitted['state']}")
        except errors.Forbidden:
            pass
        lw.save(workdir, "regrant.json", {"leaks": leaks, "export": export})
        assert not leaks, "0-E7L-1 missing transitive revocation: " + "; ".join(leaks)
    finally:
        lab.revoke(lab.RACER)


# ------------------------------------------------------------------------------------ i05
def own_ledger(lab, budget):
    from tests.j.submit import fakes as j2
    return j2.FakeJudgeLedger({lab.payer: budget}, now=lab.judge.now)


def posts_for(lab, runs) -> list[dict]:
    keys = {r.submit_key for r in runs if r.submit_key}
    return [p for p in lab.teacher.posts if p["submit_key"] in keys]


def wc(lab, samples: int):
    from infrx.contracts.v2.money_units import ProviderUsd
    from infrx.judge import worst_case
    batch = lab.batch(lab.benchmark, 0)
    return ProviderUsd(worst_case(lab.j1.TEST_RATE, batch.ceilings, samples))


def test_i05_a_duplicate_teacher_submit_is_one_paid_job(lab, workdir):
    """PIPELINE-BUDGET: the same batch submitted twice at once, then resumed, is one
    reservation and one teacher job per chunk - the exact worst case, on the named payer."""
    from infrx.pipelines.teachers import run_batch
    ledger = own_ledger(lab, wc(lab, 6) + wc(lab, 6) + wc(lab, 6))
    batch, wiring = lab.batch(lab.benchmark, 5, chunk_size=6), lab.wiring(ledger=ledger)

    async def twice():
        return await asyncio.gather(run_batch(batch, wiring=wiring),
                                    run_batch(batch, wiring=wiring))
    first, second = run(twice())
    again = run(run_batch(batch, wiring=wiring))
    runs = list(ledger.runs.values())
    assert len(runs) == 2 and {r.state for r in again.runs} == {"submitted"}
    assert len(posts_for(lab, runs)) == 2, "a duplicate submit paid a second teacher job"
    assert ledger.committed(lab.payer) == wc(lab, 6) + wc(lab, 6)
    assert {r.payer_ref for r in runs} == {lab.payer}
    lw.save(workdir, "duplicate.json", {"runs": [r.run_id for r in runs],
                                        "states": [[r.state for r in x.runs]
                                                   for x in (first, second, again)]})


def test_i05_an_ambiguous_teacher_submit_is_held_and_never_resubmitted(lab, workdir):
    """PIPELINE-BUDGET: the teacher accepts a chunk and answers 500: the run is ambiguous and
    its hold stays in the cap; a resumed batch sends nothing; reconcile adopts the teacher's
    record of the key; collection settles it once at the reported cost."""
    from infrx.contracts.v2.money_units import ProviderUsd
    from infrx.judge.submit import reconcile
    from infrx.pipelines.teachers import collect, run_batch
    ledger = own_ledger(lab, wc(lab, 12))
    batch, wiring = lab.batch(lab.benchmark, 6, chunk_size=12), lab.wiring(ledger=ledger)
    lab.teacher.mode = "error"
    try:
        report = run(run_batch(batch, wiring=wiring))
    finally:
        lab.teacher.mode = "ok"
    [held] = report.runs
    assert held.state == "ambiguous" and ledger.committed(lab.payer) == wc(lab, 12)
    run(run_batch(batch, wiring=wiring))
    assert len(posts_for(lab, [held])) == 1, "an ambiguous chunk was resubmitted"
    adopted = run(reconcile(held.run_id, wiring=wiring))
    assert adopted.state == "submitted"
    assert adopted.external_id == lab.teacher.batches[held.submit_key]
    lab.answer_posts()
    done = run(collect(batch, held.run_id, wiring=wiring))
    assert done.run.state == "completed"
    assert ledger.spent == {lab.payer: ProviderUsd(lab.teacher.cost)}
    assert ledger.committed(lab.payer) == ProviderUsd(lab.teacher.cost)
    lw.save(workdir, "ambiguous.json", {"run": held.run_id, "audit": ledger.audit})


def test_i05_the_budget_stops_the_batch_before_the_chunk_it_cannot_cover(lab):
    """PIPELINE-BUDGET: a payer budget covering two chunks' worst case stops the three-chunk
    batch before the third: it is never reserved nor sent."""
    from infrx.pipelines.teachers import run_batch
    ledger = own_ledger(lab, wc(lab, 4) + wc(lab, 4) + wc(lab, 1))
    report = run(run_batch(lab.batch(lab.benchmark, 7), wiring=lab.wiring(ledger=ledger)))
    assert report.stopped == "budget" and len(report.unsent) == 1
    assert len(ledger.runs) == 2 and report.unsent[0] not in ledger.runs
    assert len(posts_for(lab, ledger.runs.values())) == 2
    assert ledger.committed(lab.payer) == wc(lab, 4) + wc(lab, 4)


# ------------------------------------------------------------------------------------ i06
@contextlib.contextmanager
def protocol_server(app):
    """P3's protocol test server over TCP on the e7l block's protocol port (else a spare)."""
    import errno

    import uvicorn

    def start(port):
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="critical"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started or not thread.is_alive():
                break
            time.sleep(0.05)
        if not server.started:
            server.should_exit = True
            thread.join(timeout=10)
            raise OSError(errno.EADDRINUSE, f"address already in use: {port}")
        return server, thread
    (server, thread), port = lw.bound(start, lw.SERVICE_PORTS["protocol"])
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_i06_a_timeout_after_accept_and_a_lost_poll_are_one_paid_job(lab, workdir):
    """TRAIN-RECOVER / PIPELINE-BUDGET (R184): the automatic connector is used only when
    advertised; the server accepts and answers after the client's timeout - `ambiguous`,
    the reservation held; the resume looks the key up and adopts the one job; a poll lost
    with the server leaves the run submitted and the hold in place; the completed job
    settles once at its reported cost and nothing is ever submitted again."""
    import httpx
    from infrx.pipelines import training as p3
    from scenarios_iterate import labels1
    from tests.p.training.world import protocol_app
    one = labels1(lab)
    ext, name = lw.uid(6, 0xe71), "protocol-test"
    lab.prepare(one.ref, one.export, ext, connector=name)
    app = protocol_app("accept_sleep", delay_s=1.0)

    def call(fn, url, **kw):
        async def go():
            async with httpx.AsyncClient(base_url=url, timeout=0.3) as client:
                return await fn(p3.HttpConnector(client, name), **kw)
        return run(go())

    def submit(connector, advertised=frozenset({p3.MANUAL, name})):
        return p3.submit(lab.store, lab.objects, lab.runs, connector, lab.directory,
                         provider_org_id=lab.NEMO, user_id=lab.DEV, external_run_id=ext,
                         advertised=advertised)

    def poll(connector):
        return p3.poll(lab.runs, connector, provider_org_id=lab.NEMO, external_run_id=ext)
    held = (lab.NEMO, submit_key(ext))
    with protocol_server(app) as url:
        with pytest.raises(errors.Forbidden):
            call(lambda c: submit(c, p3.ADVERTISED), url)       # hidden until P-11
        assert call(submit, url)["state"] == "ambiguous"
        assert lab.runs.reservations[held]["state"] == "held"
        time.sleep(1.2)                                       # the answer is long gone
        resumed = call(submit, url)
        assert (resumed["state"], resumed["job_id"]) == ("submitted", "job-1")
    with pytest.raises(httpx.TransportError):
        call(poll, url)                                       # the poll is lost
    assert run(lab.runs.get(ext, provider_org_id=lab.NEMO))["state"] == "submitted"
    assert lab.runs.reservations[held]["state"] == "held"
    with protocol_server(app) as url:
        assert call(poll, url)["state"] == "submitted"        # still running
        app.state.jobs["job-1"].update(state="completed", cost="12.50000000")
        done = call(poll, url)
        again = call(poll, url)
        after = call(submit, url)
    assert done["state"] == again["state"] == after["state"] == "completed"
    assert lab.runs.reservations[held] == {"payer_ref": lab.payer, "limit": "25.00000000",
                                           "state": "settled", "cost": "12.50000000"}
    assert (app.state.posts, len(app.state.jobs)) == (1, 1)
    lw.save(workdir, "connector.json", {"run": done, "reservation": lab.runs.reservations[held]})
