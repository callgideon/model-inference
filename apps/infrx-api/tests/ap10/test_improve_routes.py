#!/usr/bin/env python3
"""AP-10 10e: the R270 routes over `infrx.lab.improve.release` (`routes/lab_improve.py`),
`/lab/v1/providers/{provider}/training-runs/{run_id}/...` - the reviewed-label export (202 +
the `training.export` OperationDoc), the candidate registration (202 + AP-04's import
operation) and the write-once release evidence read. Over `test_release.py`'s rig (P3's fake
world and the recording AP-04 stand-in), P1's members as L2 and 0060's `FakeControlOps`; the
composed AP-04 world is `test_improve_composed_pg.py`'s.

    uv run --frozen pytest -q tests/ap10/test_improve_routes.py
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import api, errors
from infrx.gateway import control
from infrx.gateway.routes import lab_improve
from infrx.lab.access import LabAccess
from infrx.lab.improve import release
from infrx.state.control_ops import FakeControlOps

from ..n.imports.world import NEMO, run
from ..p.annotations.world import DEV, NOW, VIEWER
from ..p.training.test_training import CONFIG, EXT
from ..p.training.world import PAYER
from .test_release import CKPT, COMMIT, FILES, PROJECT, Rig, entry

OTHER = "0a000000-0000-4000-8000-0000000000b9"
RUN = f"/lab/v1/providers/{NEMO}/training-runs/{EXT}"
CANDIDATE = f"{RUN}/checkpoints/{CKPT}/candidate"
EVIDENCE = f"{RUN}/checkpoints/{CKPT}/release-evidence"


def session(user: str = DEV, provider: str | None = None) -> api.Actor:
    return api.Actor(audience="session", user_id=user, org_id=user, provider_org_id=provider)


def client(r: Rig, actor: api.Actor | None, *, composed: bool = True) -> TestClient:
    r.w.access.provider_names.setdefault(NEMO, "NemoStation")      # L2's workspace read
    access = LabAccess(r.w.access)
    if not hasattr(r, "ops"):
        r.clock = [NOW]                                     # type: ignore[attr-defined]
        r.ops = FakeControlOps(now=lambda: r.clock[0])      # type: ignore[attr-defined]
    x = lab_improve.LabImprove(access=access, store=r.w.store, objects=r.w.objects,
                               ledger=r.w.ledger, ops=r.ops, artifacts=r.a)  # type: ignore[arg-type]
    app = FastAPI()
    lab_improve.register(app, SimpleNamespace(
        lab_improve=x if composed else None,
        actors=control.StaticActors(actor) if actor is not None else None))
    return TestClient(app)


def export_body(r: Rig, **kw) -> dict:
    return {"trainer": release.MANUAL, "dataset_ref": r.w.ref, "label_export_id": r.label["export_id"],
            "config": CONFIG, "payer_ref": PAYER, "limit": "25.00000000", **kw}


def code(response) -> tuple[int, str | None]:
    error = response.json().get("error") or {}
    return response.status_code, error.get("code")


def fields(response) -> list[tuple[str, str]]:
    return [(e["field"], e["code"]) for e in response.json()["error"]["field_errors"]]


def test_ap10_improve_the_export_is_one_finished_operation_per_key():
    """Oracle: a developer's session on the path's workspace exports the run's bundle as one
    `training.export` operation: 202 + Location at the operations read + no-store, finished
    `succeeded`, its resource the run, and P3's bundle written; the same key and body is the
    same operation (nothing re-run), the same key with another body the R270 409. A 200, an
    unfinished operation, a second operation for one key or no bundle fails."""
    r = Rig()
    c = client(r, session())
    first = c.post(f"{RUN}/export", json=export_body(r), headers={"Idempotency-Key": "exp-1-key"})
    assert first.status_code == 202, first.text
    doc = api.OperationDoc.model_validate(first.json())
    assert (doc.kind, doc.state, doc.resource_id) == ("training.export", "succeeded", EXT)
    assert first.headers["location"] == f"/lab/v1/operations/{doc.operation_id}"
    assert first.headers["cache-control"] == "no-store"
    assert r.w.run_state()["state"] == "prepared"
    assert run(r.ops.get(doc.operation_id, session(provider=NEMO))).state == "succeeded", \
        "the workspace reads it at its Location"
    again = c.post(f"{RUN}/export", json=export_body(r), headers={"Idempotency-Key": "exp-1-key"})
    assert again.status_code == 202 and again.json() == first.json()
    other = c.post(f"{RUN}/export", json=export_body(r, limit="26.00000000"),
                   headers={"Idempotency-Key": "exp-1-key"})
    assert code(other) == (409, "idempotency_conflict")
    run2 = RUN.replace(EXT, OTHER)
    assert code(c.post(f"{run2}/export", json=export_body(r),
                       headers={"Idempotency-Key": "exp-1-key"})) == (409, "idempotency_conflict")


def test_ap10_improve_a_transient_failure_finishes_nothing_and_the_key_runs_it_again():
    """Oracle: a store that fails (503) mid-export leaves the operation `running` under its
    lease - never `failed` - and the response is the retryable 503; while the lease is live
    the same key answers the running operation, after it lapses the same key runs the export
    and finishes it `succeeded`."""
    r = Rig()
    c = client(r, session())
    get, calls = r.w.objects.get, []

    async def flaky(key):
        if not calls:
            calls.append(key)
            raise errors.DependencyUnavailable("the Lab objects did not answer")
        return await get(key)
    r.w.objects.get = flaky
    down = c.post(f"{RUN}/export", json=export_body(r), headers={"Idempotency-Key": "exp-f-key"})
    assert code(down) == (503, "dependency_unavailable") and down.json()["error"]["retryable"]
    held = c.post(f"{RUN}/export", json=export_body(r), headers={"Idempotency-Key": "exp-f-key"})
    assert held.status_code == 202 and held.json()["state"] == "running"
    r.clock[0] += timedelta(seconds=lab_improve.LEASE_S + 1)
    done = c.post(f"{RUN}/export", json=export_body(r), headers={"Idempotency-Key": "exp-f-key"})
    assert (done.status_code, done.json()["state"]) == (202, "succeeded")
    assert done.json()["operation_id"] == held.json()["operation_id"]
    assert r.w.run_state()["state"] == "prepared"


def test_ap10_improve_export_refusals_are_r270_envelopes():
    """Oracle: an unsupported trainer is 422 `unsupported` on `trainer` (nothing bundled) and
    its operation is finished `failed` with that reason (the replay answers it); an objective
    that is not the reviewed export's adapter is 422 on `config.objective`; another
    provider's payer and a viewer are 403; a key without a user, or a credential of another
    workspace than the path's, is 404; a run id that is not a UUID
    is 422 before anything is read; uncomposed actors are 503 - never a 500."""
    r = Rig()
    c = client(r, session())
    unsupported = c.post(f"{RUN}/export", json=export_body(r, trainer="hosted-tinker"),
                         headers={"Idempotency-Key": "exp-t-key"})
    assert code(unsupported) == (422, "invalid_request")
    assert fields(unsupported) == [("trainer", "unsupported")]
    assert r.w.run_state() is None, "nothing bundled"
    replay = c.post(f"{RUN}/export", json=export_body(r, trainer="hosted-tinker"),
                    headers={"Idempotency-Key": "exp-t-key"})
    assert replay.status_code == 202 and replay.json()["state"] == "failed"
    assert [(e["field"], e["code"]) for e in replay.json()["error"]["field_errors"]] == \
        [("trainer", "unsupported")]
    mismatch = c.post(f"{RUN}/export", json=export_body(
        r, config={**CONFIG, "objective": "preference"}), headers={"Idempotency-Key": "exp-o-key"})
    assert fields(mismatch) == [("config.objective", "adapter_mismatch")]
    payer = PAYER.replace(NEMO, OTHER)
    assert code(c.post(f"{RUN}/export", json=export_body(r, payer_ref=payer),
                       headers={"Idempotency-Key": "exp-p-key"})) == (403, "forbidden")
    not_an_id = c.post(f"/lab/v1/providers/{NEMO}/training-runs/not-a-uuid/export",
                       json=export_body(r), headers={"Idempotency-Key": "exp-u-key"})
    assert fields(not_an_id) == [("path.run_id", "string_pattern_mismatch")]
    assert code(client(r, session(VIEWER)).post(
        f"{RUN}/export", json=export_body(r), headers={"Idempotency-Key": "exp-v-key"})) == \
        (403, "forbidden")
    key = api.Actor(audience="provider_dev", provider_org_id=NEMO)
    assert code(client(r, key).post(f"{RUN}/export", json=export_body(r),
                                    headers={"Idempotency-Key": "exp-k-key"})) == (404, "not_found")
    assert code(client(r, session(provider=OTHER)).post(
        f"{RUN}/export", json=export_body(r), headers={"Idempotency-Key": "exp-w-key"})) == \
        (404, "not_found")
    assert code(client(r, None).post(f"{RUN}/export", json=export_body(r),
                                     headers={"Idempotency-Key": "exp-n-key"})) == \
        (503, "dependency_unavailable")
    assert r.w.run_state() is None


def candidate_body(r: Rig, files=FILES) -> dict:
    return {"artifact_key": r.ckpt_key, "project_id": PROJECT,
            "source": {"host": "huggingface.co", "repo": "nemo/marlin-2b-sop-lora",
                       "commit": COMMIT}, "files": [entry(f) for f in files]}


def test_ap10_improve_the_candidate_is_ap04s_import_and_the_evidence_follows_it():
    """Oracle: an approved checkpoint's registration is 202 + AP-04's import operation with
    Location at the operations read (the import body is AP-04's, the actor the scoped
    session); other files are 422 `not_the_checkpoint` and start nothing. The release
    evidence read is 409 until AP-04 verified the import, then 200 with the record
    (`qualification: pending`) - the same record on every read; a viewer reads it, a session
    of no member is 404, and the registration is refused to a viewer (403)."""
    r = Rig()
    r.ckpt_key = r.eligible()
    c = client(r, session())
    wrong = c.post(CANDIDATE, json=candidate_body(r, files=FILES[:1]),
                   headers={"Idempotency-Key": "cand-0-key"})
    assert fields(wrong) == [("files", "not_the_checkpoint")] and r.a.started == {}
    assert code(client(r, session(VIEWER)).post(
        CANDIDATE, json=candidate_body(r), headers={"Idempotency-Key": "cand-v-key"})) == \
        (403, "forbidden")
    started = c.post(CANDIDATE, json=candidate_body(r), headers={"Idempotency-Key": "cand-1-key"})
    assert started.status_code == 202, started.text
    assert started.headers["location"] == f"/lab/v1/operations/{started.json()['operation_id']}"
    actor, body = r.a.started["cand-1-key"]
    assert actor.provider_org_id == NEMO and actor.user_id == DEV
    assert sorted(f.relative_path for f in body.files) == sorted(FILES)
    assert code(c.get(EVIDENCE)) == (409, "state_conflict")
    r.a.verify("cand-1-key")
    got = c.get(EVIDENCE)
    assert got.status_code == 200, got.text
    record = got.json()
    assert record["artifact"]["artifact_id"] == r.a.docs["cand-1-key"].resource_id
    assert record["qualification"]["state"] == "pending"
    assert client(r, session(VIEWER)).get(EVIDENCE).json() == record
    assert code(client(r, session("f1000000-0000-4000-8000-0000000000f1")).get(EVIDENCE)) == \
        (404, "not_found")
    assert client(r, session(), composed=False).get(EVIDENCE).status_code == 404  # unmounted
