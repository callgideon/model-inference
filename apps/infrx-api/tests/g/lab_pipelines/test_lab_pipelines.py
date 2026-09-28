#!/usr/bin/env python3
"""WR-P4-1: `/lab/v1/pipelines` - LAB-ACCESS and PIPELINE-LINEAGE, the route half.

    uv run --frozen pytest -q tests/g/lab_pipelines

P1 and P3 run for real over their own fake world (`tests/p/training/world.py`: D7's store with
checkpoint receipts, the object store, the D8/D6J run ledger, B3's evaluation port, the L2
members) and P1's label log (`tests/p/annotations/world.py`, D8 not merged). The run ledger's
provider listings WR-LAB2-4 asks of lab-sql are the small subclass below. The session
verifier is a minimal fake of `lab_auth.Sessions` (lab-api, batch #4).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.contracts.lab import records
from infrx.contracts.v2 import records as v2
from infrx.gateway.routes import lab_pipelines as lp
from infrx.lab.access import LabAccess
from infrx.pipelines import training as p3

from .. import support
from ...n.imports.world import NEMO, OTHER
from ...n.versions.test_versions import uid
from ...p.annotations.world import (ADMIN, DEV, DEV2, GONE, NOW, RUBRIC, VIEWER,
                                    FakeLabelLog, rows)
from ...p.training import world as p3w
from ...p.training.test_training import World as P3World

P = lp.PIPELINES_PREFIX
OUTSIDER = "d0000009-0000-4000-8000-000000000009"
CONSUMER = "f1000000-0000-4000-8000-0000000000f1"
EXT, IMPORT, EXPORT, CKPT = uid(1, 0xe0), uid(1, 0x1b), uid(1, 0xe7), uid(1, 0xc9)


def token(user: str) -> str:
    return f"eyJ0.{user.replace('-', '')}.c2ln"


class Sessions:
    """`lab_auth.Sessions`: a distinct JWT-shaped token per user."""

    def __init__(self, users) -> None:
        self.users = {token(u): u for u in users}

    async def user_id(self, value: str) -> str:
        if value not in self.users:
            raise errors.InvalidApiKey("not a live session")
        return self.users[value]


class Ledger(p3w.FakeRunLedger):
    """P3's run ledger plus WR-LAB2-4's listings: the provider's runs, and its D7
    checkpoint receipts joined with P3's outcome notes."""

    def __init__(self, store) -> None:
        super().__init__()
        self.store = store

    async def run_rows(self, provider_org_id):
        return [(rid, dict(row)) for (p, rid), row in self.runs.items() if p == provider_org_id]

    async def checkpoint_rows(self, provider_org_id):
        out = []
        for cid, receipt in self.store.receipts.items():
            if receipt["provider_org_id"] == provider_org_id:
                note = self.notes.get((provider_org_id, f"checkpoint:{cid}")) or {}
                out.append({"checkpoint_id": cid, "artifact_digest": receipt["artifact_digest"],
                            "external_run_ref": receipt["external_run_ref"],
                            "state": note.get("state", receipt["state"]),
                            "reason": note.get("reason")})
        return out


class World(P3World):
    def __init__(self) -> None:
        super().__init__(items=rows(8, splits=("train", "train", "holdout", "validation")))
        self.ledger, self.log = Ledger(self.store), FakeLabelLog()
        self.access.memberships[(OTHER, OUTSIDER)] = v2.ProviderMembership(
            provider_org_id=OTHER, user_id=OUTSIDER, role=v2.ProviderRole.developer,
            granted_by="ops", granted_at=NOW - timedelta(days=1))
        self.access.provider_names = {NEMO: "Nemo", OTHER: "Other"}
        self.sessions = Sessions((ADMIN, DEV, DEV2, VIEWER, GONE, OUTSIDER, CONSUMER))

    def pipelines(self, **absent) -> lp.LabPipelines:
        ports = {"store": self.store, "objects": self.objects, "log": self.log,
                 "ledger": self.ledger, "evals": self.evals}
        return lp.LabPipelines(self.sessions, LabAccess(self.access), **{**ports, **absent})

    def client(self, *, on_runtime=False, **absent) -> TestClient:
        app, rt = FastAPI(), support.runtime()
        x = self.pipelines(**absent)
        if on_runtime:
            rt.lab_pipelines = x
            assert lp.register(app, rt) is x
        else:
            assert lp.register(app, rt, x) is x
        return TestClient(app, raise_server_exceptions=False)

    def train_ids(self) -> list[str]:
        return list(self.manifest().splits.train)

    def rows(self, ids=None) -> str:
        return "\n".join(json.dumps({"sample_id": s, "method": "model",
                                     "method_version": "labeler-3", "label": {"answer": f"a{i}"}})
                         for i, s in enumerate(self.train_ids() if ids is None else ids))

    def importing(self, rows=None, **over) -> dict:
        return {"import_id": IMPORT, "dataset_ref": self.ref, "rubric_ref": RUBRIC,
                "rows": self.rows() if rows is None else rows, **over}

    def exporting(self, **over) -> dict:
        return {"export_id": EXPORT, "dataset_ref": self.ref, "adapter": "sft.1",
                "ttl_s": 3600, **over}

    def preparing(self, **over) -> dict:
        return {"external_run_id": EXT, "dataset_ref": self.ref,
                "export": {"format": "infrx.label_export.1", "export_id": EXPORT},
                "config": {"objective": "sft", "adaptation": "lora", "base_model": "marlin-2b"},
                "payer_ref": p3w.PAYER, "limit": "25.00000000", **over}

    def checkpointing(self, data=None, **over) -> dict:
        data = p3w.descriptor() if data is None else data
        return {"external_run_id": EXT, "checkpoint_id": CKPT,
                "artifact_key": self.put(data), "artifact_digest": p3w.digest(data), **over}

    def labelled(self, c) -> list[str]:
        """Every train sample labelled once and accepted by DEV (assigned by ADMIN)."""
        assert call(c, DEV, "POST", "label-imports", self.importing()).status_code == 201
        refs, listed = [], call(c, DEV, "GET", "labels", query={"dataset_ref": self.ref})
        assert listed.status_code == 200, listed.json()
        for label in listed.json()["data"]:
            assert call(c, ADMIN, "POST", "assignments", {
                "dataset_ref": self.ref, "sample_id": label["sample_id"], "reviewer_id": DEV,
                "rubric_ref": RUBRIC}).status_code == 200
            assert call(c, DEV, "POST", "reviews", {
                "dataset_ref": self.ref, "annotation_ref": label["annotation_ref"],
                "decision": "accepted", "rubric_ref": RUBRIC, "correction": None}
            ).status_code == 200
            refs.append(label["annotation_ref"])
        return refs


def call(c, user, method, path, body=None, *, provider=NEMO, query=None, headers=None,
         raw=None):
    head = {"authorization": f"Bearer {token(user)}"} if user else {}
    head.update(headers or {})
    params = {"provider_org_id": provider, **(query or {})}
    if raw is not None:
        return c.request(method, f"{P}/{path}", params=params, content=raw,
                         headers={"content-type": "application/json", **head})
    return c.request(method, f"{P}/{path}", params=params, json=body, headers=head)


def routes(w):
    q = {"dataset_ref": w.ref}
    return (("GET", "labels", None, q), ("GET", "disagreements", None, q),
            ("GET", "label-imports", None, None), ("GET", "label-exports", None, None),
            ("GET", "training-runs", None, None),
            ("GET", f"training-runs/{EXT}/bundle", None, None),
            ("GET", "checkpoints", None, None),
            ("POST", "label-imports", w.importing(), None),
            ("POST", "assignments", {"dataset_ref": w.ref, "sample_id": w.train_ids()[0],
                                     "reviewer_id": DEV, "rubric_ref": RUBRIC}, None),
            ("POST", "reviews", {"dataset_ref": w.ref, "annotation_ref": RUBRIC.replace(
                "rubric", "annotation"), "decision": "accepted", "rubric_ref": RUBRIC,
                "correction": None}, None),
            ("POST", "adjudications", {"dataset_ref": w.ref, "sample_id": w.train_ids()[0],
                                       "value": "1", "rubric_ref": RUBRIC}, None),
            ("POST", "label-exports", w.exporting(), None),
            ("POST", "training-runs", w.preparing(), None),
            ("POST", f"training-runs/{EXT}/submit", None, None),
            ("POST", f"training-runs/{EXT}/finish", None, None),
            ("POST", f"training-runs/{EXT}/cancel", None, None),
            ("POST", "checkpoints", {"external_run_id": EXT, "checkpoint_id": CKPT,
                                     "artifact_key": f"lab/{NEMO}/training/{EXT}/c",
                                     "artifact_digest": "sha256:" + "0" * 64}, None),
            ("POST", f"checkpoints/{CKPT}/approve", {"external_run_id": EXT}, None))


def untouched(w) -> bool:
    written = asyncio.run(w.objects.keys(f"lab/{NEMO}/"))
    return (w.log.rows, w.ledger.runs, w.ledger.notes, w.store.receipts) == ([], {}, {}, {}) \
        and not [k for k in written if "/label-" in k or "/training/" in k]


# --- mounting ------------------------------------------------------------------------------
def test_lab_pipelines__nothing_is_mounted_without_the_switch():
    """Oracle: no `rt.lab_pipelines` (LAB_PIPELINES off), no route - a 404, never a stand-in."""
    app = FastAPI()
    assert lp.register(app, support.runtime()) is None
    assert not [r for r in app.routes if getattr(r, "path", "").startswith(P)]
    w = World()
    assert call(w.client(on_runtime=True), DEV, "GET", "training-runs").status_code == 200


# --- LAB-ACCESS -----------------------------------------------------------------------------
def test_lab_pipelines__every_route_needs_the_session_before_anything_else():
    """Oracle: no token, or an API key, is a 401 on every route and nothing is asked - not even
    the body, so an invalid one is still a 401."""
    w = World()
    c = w.client()
    for method, path, body, query in routes(w):
        for headers in ({}, {"authorization": f"Bearer {support.TOKEN}"}):
            for sent in (body, {"forged": True}):
                answer = call(c, None, method, path, sent, query=query, headers=headers)
                assert (answer.status_code, answer.json()) \
                    == (401, {"refusal": "unauthenticated"}), path
    assert untouched(w)


def test_lab_pipelines__a_consumer_only_user_is_denied_and_another_provider_is_not_found():
    """Oracle (LAB-ACCESS): a user with no provider membership is a 403 on every route; a
    member of OTHER naming NEMO a 404 on every route; nothing is asked of P1 or P3."""
    w = World()
    c = w.client()
    for method, path, body, query in routes(w):
        answer = call(c, CONSUMER, method, path, body, query=query)
        assert (answer.status_code, answer.json()) == (403, {"refusal": "denied"}), path
        answer = call(c, OUTSIDER, method, path, body, query=query)
        assert (answer.status_code, answer.json()) == (404, {"refusal": "not_found"}), path
    assert untouched(w)


def test_lab_pipelines__a_viewer_reads_nothing_and_only_an_administrator_assigns():
    """Oracle (LAB-ACCESS): label values are content - a viewer is a 403 on every route, reads
    included; a developer is a 403 on assignments only; a revoked member is a 403."""
    w = World()
    c = w.client()
    for method, path, body, query in routes(w):
        answer = call(c, VIEWER, method, path, body, query=query)
        assert (answer.status_code, answer.json()) == (403, {"refusal": "denied"}), path
        assert call(c, GONE, method, path, body, query=query).status_code == 403, path
    assert untouched(w)
    assign = routes(w)[8]
    assert call(c, DEV, *assign[:3]).status_code == 403
    answer = call(c, DEV, "POST", "assignments", {"forged": True})    # refused before the body
    assert (answer.status_code, answer.json()) == (403, {"refusal": "denied"})
    assert call(c, ADMIN, *assign[:3]).status_code == 200
    assert call(c, DEV, "GET", "training-runs").status_code == 200


# --- PIPELINE-LINEAGE: labels -> export -> run -> checkpoint -> eligible --------------------
def test_lab_pipelines__lineage_runs_from_labels_to_an_eligible_checkpoint():
    """Oracle (PIPELINE-LINEAGE): imported labels are `synthetic`, never ground truth; the
    export lists each train sample's label refs and methods and omits the rest; the run is
    the export's (id and digest), the config's and the frozen holdout's (a pin, never the
    ids); a checkpoint is queued for evaluation on exactly that holdout and becomes eligible
    only once that evaluation succeeded."""
    w = World()
    c = w.client()
    refs = w.labelled(c)
    labels = call(c, DEV, "GET", "labels", query={"dataset_ref": w.ref}).json()["data"]
    assert {(x["method"], x["ground_truth"], x["state"]) for x in labels} \
        == {("synthetic", False, "accepted")}
    exported = call(c, DEV, "POST", "label-exports", w.exporting())
    assert exported.status_code == 201
    record = exported.json()
    assert [x["sample_id"] for x in record["lineage"]] == sorted(w.train_ids())
    assert sorted(r for x in record["lineage"] for r in x["label_refs"]) == sorted(refs)
    assert {m for x in record["lineage"] for m in x["methods"]} == {"synthetic"}
    assert call(c, DEV, "GET", "label-exports").json() == {"data": [record]}
    prepared = call(c, DEV, "POST", "training-runs", w.preparing())
    assert prepared.status_code == 201
    run = prepared.json()
    holdout = sorted(w.manifest().splits.holdout)
    assert run["export"] == {"format": "infrx.label_export.1", "export_id": EXPORT,
                             "sha256": record["sha256"]}
    assert (run["dataset_ref"], run["config"], run["state"], run["train"], run["dev"],
            run["holdout"]) == (
        w.ref, w.preparing()["config"], "prepared", len(w.train_ids()),
        len(w.manifest().splits.validation),
        {"size": len(holdout), "sha256": hashlib.sha256(records.canonical(holdout)).hexdigest()})
    bundle = call(c, DEV, "GET", f"training-runs/{EXT}/bundle").json()
    assert bundle["train"] == w.train_ids()
    assert not [h for h in holdout if h in json.dumps(bundle)]
    for op, state in (("submit", "submitted"), ("finish", "completed")):
        answer = call(c, DEV, "POST", f"training-runs/{EXT}/{op}")
        assert (answer.status_code, answer.json()["state"]) == (200, state), op
    assert call(c, DEV, "GET", "training-runs").json() == {"data": [answer.json()]}
    imported = call(c, DEV, "POST", "checkpoints", w.checkpointing())
    assert imported.status_code == 201
    checkpoint = imported.json()
    assert (checkpoint["state"], checkpoint["external_run_id"], checkpoint["eligible"]) \
        == ("validated", EXT, False)
    evaluation = checkpoint["evaluation"] or {}
    assert (evaluation.get("split"), evaluation.get("holdout_sha256"), evaluation.get("state")) \
        == ("holdout", run["holdout"]["sha256"], "queued")
    approve = call(c, DEV, "POST", f"checkpoints/{CKPT}/approve", {"external_run_id": EXT})
    assert (approve.status_code, approve.json()) == (409, {"refusal": "conflict"})
    w.evals.runs[CKPT]["state"] = "succeeded"
    approve = call(c, DEV, "POST", f"checkpoints/{CKPT}/approve", {"external_run_id": EXT})
    assert approve.status_code == 200 and approve.json()["eligible"] is True
    assert call(c, DEV, "GET", "checkpoints").json() == {"data": [approve.json()]}


def test_lab_pipelines__a_rejected_checkpoint_is_never_evaluated():
    """Oracle (PIPELINE-LINEAGE): bytes other than the declared digest are `rejected` with
    the reason and no evaluation; its redelivery is the same outcome."""
    w = World()
    c = w.client()
    w.labelled(c)
    call(c, DEV, "POST", "label-exports", w.exporting())
    call(c, DEV, "POST", "training-runs", w.preparing())
    call(c, DEV, "POST", f"training-runs/{EXT}/submit")
    body = w.checkpointing(artifact_digest="sha256:" + "0" * 64)
    for _ in range(2):
        answer = call(c, DEV, "POST", "checkpoints", body)
        assert answer.status_code == 201
        assert (answer.json()["state"], answer.json()["reason"], answer.json()["evaluation"]) \
            == ("rejected", "digest_mismatch", None)
    assert w.evals.calls == 0


def test_lab_pipelines__a_label_import_is_write_once_with_every_rejected_row():
    """Oracle: every refused row is in the receipt with its number and P1's reason, an
    unreadable line as `missing_evidence`; the same import again is the stored receipt (no
    second label); another body under its id is a 409; the receipts are listed."""
    w = World()
    c = w.client()
    ids = w.train_ids()
    lines = "\n".join([w.rows(ids[:1]), "", "not json",
                      json.dumps({"sample_id": ids[1], "method": "human", "method_version": "1",
                                  "label": 1, "ground_truth": True}), ""])
    answer = call(c, DEV, "POST", "label-imports", w.importing(lines))
    expected = {"import_id": IMPORT, "dataset_ref": w.ref, "accepted": 1,
                "rejected": [{"row": 2, "reason": "missing_evidence"},
                             {"row": 3, "reason": "forged_ground_truth"}]}
    assert (answer.status_code, answer.json()) == (201, expected)
    events = list(w.log.rows)
    again = call(c, DEV, "POST", "label-imports", w.importing(lines))
    assert (again.status_code, again.json(), w.log.rows) == (201, expected, events)
    other = call(c, DEV, "POST", "label-imports", w.importing(w.rows(ids[1:2])))
    assert (other.status_code, other.json()) == (409, {"refusal": "conflict"})
    assert w.log.rows == events
    assert call(c, DEV, "GET", "label-imports").json() == {"data": [expected]}


def test_lab_pipelines__a_label_export_id_answers_the_export_already_made():
    """Oracle: the same export again - later, on the store's clock - is the stored record;
    another adapter or lifetime under its id is a 409."""
    w = World()
    c = w.client()
    w.labelled(c)
    first = call(c, DEV, "POST", "label-exports", w.exporting()).json()
    w.access.now += timedelta(minutes=10)
    again = call(c, DEV, "POST", "label-exports", w.exporting())
    assert (again.status_code, again.json()) == (201, first)
    for over in ({"ttl_s": 60}, {"adapter": "preference.1"}):
        answer = call(c, DEV, "POST", "label-exports", w.exporting(**over))
        assert (answer.status_code, answer.json()) == (409, {"refusal": "conflict"}), over


def test_lab_pipelines__review_corrections_and_adjudications_are_human_ground_truth():
    """Oracle (PIPELINE-LINEAGE): a correction (JSON text) rejects the label and adds the
    reviewer's `human` ground-truth label; two imported labels that disagree are listed as a
    disagreement, and its adjudication (another assigned reviewer) rejects both and adds a
    `human` label; a value that is not JSON is a 422 and nothing moves."""
    w = World()
    c = w.client()
    first, second = w.train_ids()[:2]

    def labels():
        answer = call(c, DEV, "GET", "labels", query={"dataset_ref": w.ref})
        assert answer.status_code == 200, answer.json()
        return {x["annotation_ref"]: x for x in answer.json()["data"]}
    call(c, DEV, "POST", "label-imports", w.importing(w.rows([first, second])))
    call(c, DEV, "POST", "label-imports", w.importing(
        w.rows([second]).replace('"a0"', '"other"'), import_id=uid(2, 0x1b)))
    (label,) = [x for x in labels().values() if x["sample_id"] == first]
    for sample, reviewer in ((first, DEV), (second, DEV2)):
        call(c, ADMIN, "POST", "assignments", {"dataset_ref": w.ref, "sample_id": sample,
                                               "reviewer_id": reviewer, "rubric_ref": RUBRIC})
    review = {"dataset_ref": w.ref, "annotation_ref": label["annotation_ref"],
              "decision": "rejected", "rubric_ref": RUBRIC}
    bad = call(c, DEV, "POST", "reviews", {**review, "correction": "{answer"})
    assert (bad.status_code, bad.json()) == (422, {"refusal": "invalid"})
    answer = call(c, DEV, "POST", "reviews", {**review, "correction": '{"answer": "fixed"}'})
    assert answer.status_code == 200
    rows = labels()
    human = rows[answer.json()["annotation_ref"]]
    assert (rows[label["annotation_ref"]]["state"], human["method"], human["ground_truth"],
            human["value"], human["reviewer_id"]) \
        == ("rejected", "human", True, '{"answer":"fixed"}', DEV)
    disputed = sorted(ref for ref, x in rows.items() if x["sample_id"] == second)
    assert call(c, DEV, "GET", "disagreements", query={"dataset_ref": w.ref}).json() \
        == {"data": [{"sample_id": second, "annotation_refs": disputed}]}
    adjudication = {"dataset_ref": w.ref, "sample_id": second, "rubric_ref": RUBRIC}
    bad = call(c, DEV2, "POST", "adjudications", {**adjudication, "value": "{answer"})
    assert (bad.status_code, bad.json()) == (422, {"refusal": "invalid"})
    answer = call(c, DEV2, "POST", "adjudications", {**adjudication, "value": '"settled"'})
    assert answer.status_code == 200
    rows = labels()
    assert [rows[ref]["state"] for ref in disputed] == ["rejected", "rejected"]
    judged = rows[answer.json()["annotation_ref"]]
    assert (judged["method"], judged["ground_truth"], judged["value"], judged["state"]) \
        == ("human", True, '"settled"', "accepted")
    assert call(c, DEV, "GET", "disagreements", query={"dataset_ref": w.ref}).json() \
        == {"data": []}


def test_lab_pipelines__the_connector_is_never_the_callers():
    """Oracle (P-11): a body naming a connector is a 422 and no run exists; the run is the
    manual bundle, and its submit reserves nothing (no USD held for provider compute)."""
    w = World()
    c = w.client()
    w.labelled(c)
    call(c, DEV, "POST", "label-exports", w.exporting())
    for connector in ("protocol-test", p3.MANUAL):
        answer = call(c, DEV, "POST", "training-runs", w.preparing(connector=connector))
        assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"})
    assert w.ledger.runs == {}
    run = call(c, DEV, "POST", "training-runs", w.preparing()).json()
    assert (run["connector"], run["reserved_usd"], run["limit_usd"], run["payer_ref"]) \
        == (p3.MANUAL, "0.00000000", "25.00000000", p3w.PAYER)
    submitted = call(c, DEV, "POST", f"training-runs/{EXT}/submit").json()
    assert (submitted["state"], submitted["settled"], submitted["cost_usd"]) \
        == ("submitted", False, None)
    assert w.ledger.reservations == {}


def test_lab_pipelines__an_expired_export_is_gone():
    """Oracle: a run prepared from an export past its lifetime is `410 gone`, not a 503."""
    w = World()
    c = w.client()
    w.labelled(c)
    call(c, DEV, "POST", "label-exports", w.exporting(ttl_s=60))
    w.access.now += timedelta(minutes=2)
    answer = call(c, DEV, "POST", "training-runs", w.preparing())
    assert (answer.status_code, answer.json()) == (410, {"refusal": "gone"})
    assert answer.headers.get("cache-control") == "no-store"


# --- the ports not merged yet, and the body -------------------------------------------------
@pytest.mark.parametrize("absent", ("store", "objects", "log", "ledger", "evals"))
def test_lab_pipelines__an_unwired_port_is_unavailable_after_the_access_checks(absent, caplog):
    """Oracle: LAB_PIPELINES on before a table merges answers 503 on the reads that need the
    port - an expected state, not a bug to log - and still 404 for another provider."""
    w = World()
    c = w.client(**{absent: None})
    caplog.set_level("ERROR")
    needs = {"store": ("labels", "disagreements"), "objects": ("label-imports",
                                                              "label-exports"),
             "log": ("labels", "disagreements"), "ledger": ("training-runs", "checkpoints"),
             "evals": ()}[absent]
    for method, path, body, query in routes(w)[:7]:
        answer = call(c, DEV, method, path, body, query=query)
        if path in needs:
            assert (answer.status_code, answer.json()) == (503, {"refusal": "unavailable"})
        assert call(c, OUTSIDER, method, path, body, query=query).status_code == 404
    if absent == "evals":
        w.ledger.notes[(NEMO, f"checkpoint:{CKPT}")] = {}
        w.store.receipts[CKPT] = {"provider_org_id": NEMO, "artifact_digest": "sha256:0",
                                  "external_run_ref": w.ref, "state": "received"}
        assert call(c, DEV, "GET", "checkpoints").status_code == 503
    assert caplog.records == []


def test_lab_pipelines__a_body_is_json_bounded_and_valid_before_p1_and_p3():
    """Oracle: a non-JSON content type, an oversized body, an extra key or a limit that is not
    an exact USD amount is a 422 and nothing is asked."""
    w = World()
    c = w.client()
    text = json.dumps(w.importing())
    for raw, content_type in ((text, "text/plain"), (text + " " * lp.MAX_BODY_BYTES, None)):
        answer = call(c, DEV, "POST", "label-imports", raw=raw,
                      headers={"content-type": content_type} if content_type else None)
        assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"})
    for path, body in (("label-imports", w.importing(provider_org_id=OTHER)),
                       ("training-runs", w.preparing(limit="25")),
                       ("training-runs", w.preparing(limit="25.00000000 CREDIT"))):
        answer = call(c, DEV, "POST", path, body)
        assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"}), body
    assert untouched(w)
    padded = text + " " * 20_000                  # over the chat cap, under the import cap
    assert call(c, DEV, "POST", "label-imports", raw=padded).status_code == 201
