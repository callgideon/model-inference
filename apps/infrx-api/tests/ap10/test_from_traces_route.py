#!/usr/bin/env python3
"""AP-10 row 92 (WR-AP10C-4): `POST /lab/v1/providers/{provider}/datasets/from-traces`, the
R270 route over `from_traces.start` - 202 + the 0060 OperationDoc + Location, replay / 409 by
Idempotency-Key, the actor from `rt.actors` scoped to the path's workspace, every refusal an
R270 envelope. Over N3's fake world and 0060's `FakeControlOps` (the operation itself is
`test_from_traces.py`'s; this file proves only the route).

    uv run --frozen pytest -q tests/ap10/test_from_traces_route.py
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import api
from infrx.gateway import control
from infrx.gateway.routes import lab_datasets
from infrx.state.control_ops import FakeControlOps

from ..n.imports.world import NEMO
from ..n.lineage.world import DEV, GRANTOR, MODEL, VIEWER, World

PATH = f"/lab/v1/providers/{NEMO}/datasets/from-traces"
OTHER = "0a000000-0000-4000-8000-0000000000b9"
DATASET = "da000000-0000-4000-8000-0000000000a1"


def session(user: str = DEV, provider: str | None = None) -> api.Actor:
    """A web session names no workspace (AP-01's `SessionActors`); the path does."""
    return api.Actor(audience="session", user_id=user, org_id=user, provider_org_id=provider)


def client(actor: api.Actor | None, *, ops: bool = True) -> tuple[TestClient, World]:
    w = World()
    x = lab_datasets.LabDatasets(sessions=None, access=w.access, store=w.lab,  # type: ignore[arg-type]
                                 objects=w.objects,
                                 ops=FakeControlOps(now=lambda: w.now) if ops else None)
    app = FastAPI()
    lab_datasets.register(app, SimpleNamespace(
        settings=SimpleNamespace(pilot=None), clock=None, lab_datasets=x,
        actors=control.StaticActors(actor) if actor is not None else None))
    return TestClient(app), w


def body(w: World, **kw) -> dict:
    return {"grantor_org_id": GRANTOR, "model_id": MODEL,
            "request_ids": [w.trace(n) for n in range(1, 4)], "dataset_id": DATASET,
            "version": 1, "purpose": "training", "seed": 3, **kw}


def post(c: TestClient, payload: dict, key: str | None = "trace-key-1", path: str = PATH):
    return c.post(path, json=payload, headers={"Idempotency-Key": key} if key else {})


def code(response) -> tuple[int, str | None]:
    error = response.json().get("error") if isinstance(response.json(), dict) else None
    return response.status_code, (error or {}).get("code")


def test_ap10_route_a_session_starts_one_operation_and_a_replay_is_the_same():
    """Oracle: a developer's session on the path's workspace is 202 + the queued
    `dataset.from_traces` OperationDoc (its resource the selection) + Location at the
    operations read + no-store; the same key and body again is the same operation; the same
    key with another body is the R270 409. A 200, a missing Location, a second operation for
    one key or a session not scoped to the path's workspace fails."""
    c, w = client(session())
    payload = body(w)
    first = post(c, payload)
    assert first.status_code == 202, first.text
    doc = api.OperationDoc.model_validate(first.json())
    assert (doc.kind, doc.state, doc.resource_id is not None) == \
        ("dataset.from_traces", "queued", True)
    assert first.headers["location"] == f"/lab/v1/operations/{doc.operation_id}"
    assert first.headers["cache-control"] == "no-store"
    again = post(c, payload)
    assert again.status_code == 202 and again.json()["operation_id"] == doc.operation_id
    assert code(post(c, {**payload, "seed": 4})) == (409, "idempotency_conflict")


def test_ap10_route_refusals_are_r270_envelopes():
    """Oracle: a viewer (no developer role) and a key audience are 403; a session naming
    another workspace than the path's is 404 (never a start in its own); a missing
    Idempotency-Key and a body without a holdout are 422 naming the field; without a composed
    ControlOps (or session actors) it is 503 - never a 500 and never a start."""
    c, w = client(session(VIEWER))
    assert code(post(c, body(w))) == (403, "forbidden")
    key = api.Actor(audience="provider_dev", user_id=DEV, provider_org_id=NEMO)
    c, w = client(key)
    assert code(post(c, body(w))) == (403, "forbidden")
    c, w = client(session(provider=NEMO))
    assert code(post(c, body(w), path=f"/lab/v1/providers/{OTHER}/datasets/from-traces")) \
        == (404, "not_found")
    c, w = client(session())
    missing = post(c, body(w), key=None)
    assert code(missing) == (422, "invalid_request")
    assert "Idempotency-Key" in str(missing.json()["error"]["field_errors"])
    no_holdout = post(c, body(w, train_bp=9000, validation_bp=1000))
    assert no_holdout.status_code == 422, no_holdout.text
    c, w = client(session(), ops=False)
    assert code(post(c, body(w))) == (503, "dependency_unavailable")
    c, w = client(None)
    assert code(post(c, body(w))) == (503, "dependency_unavailable")
