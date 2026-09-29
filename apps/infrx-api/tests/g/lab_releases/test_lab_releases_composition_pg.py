#!/usr/bin/env python3
"""WR-R4-2 on real PostgreSQL: `/lab/v1/releases` as `LAB_RELEASES` composes it
(`pilot.lab_releases`) - D9's listing (0048) and decisions (0053), D7's policy revision, the
plan the release launcher stored beside it (`rollout launch`, WR-C5-PLAN), 0043's proposals
and D9 as the route's store, L2's
`LabAccess` - and the operator's decision of a proposal through D9's CAS
(`python -m infrx.lab.workers rollout decide`, with L3's serving control over the seed's real
alias rows). Only the session verifier (a token per user) and the objects (in memory; the
unit's are the Lab bucket) are stand-ins.

    INFRX_D_TASK=r2 uv run --frozen pytest -q tests/g/lab_releases/test_lab_releases_composition_pg.py
"""
# ruff: noqa: F811 - the pytest fixture imported from its world module is the parameter
from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from infrx.gateway import pilot
from infrx.gateway.routes import lab_releases as lr
from infrx.lab.access import LabAccess
from infrx.lab.control.operations import serving_ref
from infrx.lab.workers import __main__ as lab_workers
from infrx.media.store import InMemoryObjectStore
from infrx.rollouts import control as r2
from infrx.state.catalog import PgCatalogDirectory
from infrx.state.jobstore import connector
from infrx.state.lab_access import PgAccessStore
from infrx.state.lab_control import PgControlStore
from infrx.state.lab_rollout import PgReleaseStore

from .. import support
from ...d import checks_credit as cc
from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_d9_rollout as d9
from ...d import test_l2sql_access as l2
from ...d.test_code_mutants_live import job
from ...r.control.test_control import plan
from ...r.control.test_control_pg import DB, world  # noqa: F401
from .test_lab_releases import Sessions, token

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "r2" else \
    "PostgreSQL only on the r2 task-local key (INFRX_D_TASK=r2)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
OPERATOR = "00000091-0000-4000-8000-000000000091"
PROPOSE = lr.RELEASES_PATH + "/proposals"


def run(coro):
    return asyncio.run(coro)


def test_lab_releases_composition_pg__the_page_proposes_and_the_operator_decides_on_d9(
        world, monkeypatch, tmp_path):
    """k10's port half: the release page lists D9's release with the stored plan and D7's
    policy (no progress while nothing is assigned, then D9's Live once a job is - WR-LIVE-PAGE;
    no verdict before a decision); a
    developer cannot propose, an expansion without an expand verdict is a 409, a rollback
    proposal is stored once (a second is a 409: 0043's one-pending index); the operator's
    approval is ONE D9 decision at the proposal's fence (the alias already on the baseline),
    after which the page shows the release rolled back with that decision, the proposal
    approved, and a second decision of it fails; a rejected proposal moves nothing; an
    approval at a stale fence (WR-C6-F4) is refused by D9's CAS and the proposal stays
    pending."""
    dsn = pgharness.dsn(DB)
    connect = connector(dsn)
    public = run(PgControlStore(connect).deployment(cc.PUBLIC_DEPLOYMENT))
    baseline = serving_ref(public, run(PgCatalogDirectory(connect).serving_revision(
        public.serving_version_id)))
    objects = InMemoryObjectStore()
    monkeypatch.setattr(lab_workers, "lab_objects", lambda mode, env: objects)
    stored_plan = tmp_path / "plan.json"
    stored_plan.write_text(plan().model_dump_json())
    launched = []
    for tag, endpoint in ((31, public.endpoint_id), (32, d9.uid(32, 0xe0))):
        payload = {**d9.policy(d9.uid(tag, 0xb0), weights=(1_000,), endpoint=endpoint),
                   "baseline_ref": baseline}
        ref = d7.publish(world, payload)
        assert lab_workers.main([                       # WR-C5-PLAN: the release launcher
            "rollout", "launch", "--policy-ref", ref, "--plan", str(stored_plan), "--reason",
            "canary 10%"], env={"LAB_DATABASE_URL": dsn, "LAB_S3_BUCKET": "lab",
                                "LAB_OPERATOR_ID": OPERATOR}) == 0
        launched.append((ref, payload))
    (ref, payload), (quiet, _) = launched
    x = pilot.lab_releases(connect, Sessions((l2.ADMIN, l2.DEV, l2.VIEWER)),
                           LabAccess(PgAccessStore(connect)), objects)
    app = FastAPI()
    lr.register(app, support.runtime(), x)
    c = TestClient(app, raise_server_exceptions=False)

    def page(user=l2.VIEWER):
        answer = c.get(lr.RELEASES_PATH, params={"provider_org_id": d9.NEMO},
                       headers={"authorization": f"Bearer {token(user)}"})
        assert answer.status_code == 200, answer.text
        return answer.json()["data"]

    def propose(user, kind, policy_ref=ref, fence=1):
        return c.post(PROPOSE, params={"provider_org_id": d9.NEMO},
                      json={"kind": kind, "policy_ref": policy_ref, "fence": fence},
                      headers={"authorization": f"Bearer {token(user)}"})

    def decide(proposal_id, verb, policy_ref=ref):
        env = {"LAB_DATABASE_URL": dsn, "LAB_OPERATOR_ID": OPERATOR}
        return lab_workers.main(["rollout", "decide", "--policy-ref", policy_ref,
                                 "--proposal-id", proposal_id, f"--{verb}", "--reason",
                                 "pager"], env=env)

    shown = {r["policy_ref"]: r for r in page()["releases"]}
    row = shown[ref]
    assert (row["state"], row["fence"], row["progress"], row["verdict"]) == \
        ("running", 1, None, None)
    assert (row["endpoint_id"], row["baseline_ref"], row["candidates"]) == \
        (public.endpoint_id, baseline, payload["candidates"])
    assert row["plan"]["budget"] == {"amount": plan().budget.value, "unit": plan().budget.unit}
    assert row["plan_digest"] == r2.plan_digest(plan()) and row["started_at"].endswith("Z")
    assert [d for d in page()["decisions"] if d["policy_ref"] in (ref, quiet)] == []
    # WR-LIVE-PAGE (R244): one failed candidate job assigned - progress is D9's Live (0054)
    job(world, ref, payload, d9.uid(1, 0x31), payload["candidates"][0]["serving_ref"],
        "failed", ms=40)
    shown = {r["policy_ref"]: r for r in page()["releases"]}
    live = shown[ref]["progress"]
    assert shown[quiet]["progress"] is None, "nothing assigned to it: null"
    assert (live["candidate"], live["baseline"]["requests"], live["quality_covered"],
            live["spent"], live["assignments"]) == (
        {"requests": 1, "errors": 1, "p99_ms": 40}, 0, 0,
        {"amount": "0.00000000", "unit": "CREDIT"},
        [{"serving_ref": payload["candidates"][0]["serving_ref"], "pinned_by": "cohort",
          "requests": 1}]), live                              # WR-C7-TALLY (0058)
    assert live["observed_until"].endswith("Z") and live["candidate_healthy"] is False

    assert propose(l2.DEV, "rollback").status_code == 403
    assert propose(l2.ADMIN, "expand").status_code == 409         # no expand verdict
    made = propose(l2.ADMIN, "rollback")
    assert made.status_code == 201, made.text
    proposal = made.json()
    assert (proposal["kind"], proposal["fence"], proposal["state"], proposal["decided_at"]) \
        == ("rollback", 1, "proposed", None)
    assert propose(l2.ADMIN, "rollback").status_code == 409       # one pending per revision
    assert [p for p in page()["proposals"] if p["policy_ref"] == ref] == [proposal]

    assert decide(proposal["proposal_id"], "approve") == 0
    after = {r["policy_ref"]: r for r in page()["releases"]}[ref]
    assert (after["state"], after["fence"]) == ("rolled_back", 2)
    reasons = ["operator:pager", f"proposal:{proposal['proposal_id']}"]
    assert (after["verdict"]["action"], after["verdict"]["reasons"]) == ("rollback", reasons)
    [decision] = [d for d in page()["decisions"] if d["policy_ref"] == ref]
    assert (decision["decision"], decision["reasons"], decision["decided_by"]) == \
        ("rollback", reasons, OPERATOR)
    [approved] = [p for p in page()["proposals"] if p["policy_ref"] == ref]
    assert approved["state"] == "approved" and approved["decided_at"].endswith("Z")
    assert decide(proposal["proposal_id"], "approve") == 1         # decided once
    assert len([d for d in page()["decisions"] if d["policy_ref"] == ref]) == 1
    serving = lab_workers.compose("rollout", {
        "LAB_DATABASE_URL": dsn, "LAB_WORKER_HEALTH_PORT": "9", "LAB_S3_BUCKET": "unused",
        "LAB_OPERATOR_ID": OPERATOR}, objects=objects).wiring._serving
    assert run(serving.serving(public.endpoint_id))[0] == baseline

    other = propose(l2.ADMIN, "rollback", quiet).json()
    assert decide(other["proposal_id"], "reject", quiet) == 0
    still = {r["policy_ref"]: r for r in page()["releases"]}[quiet]
    assert (still["state"], still["fence"], still["verdict"]) == ("running", 1, None)
    assert {p["state"] for p in page()["proposals"] if p["policy_ref"] == quiet} == {"rejected"}

    # WR-C6-F4: a stale fence refuses the approval on D9 and leaves the proposal pending
    stale = propose(l2.ADMIN, "rollback", quiet).json()
    run(PgReleaseStore(connect).transition(quiet, fence=1, to="rolled_back", decision={
        "schema": "lab.rollout_decision.1", "provider_org_id": d9.NEMO, "policy_ref": quiet,
        "decision": "rollback", "evidence_refs": [], "decided_by": OPERATOR,
        "decided_at": "2026-09-29T00:00:00Z"}, reasons=("operator:elsewhere",)))
    assert decide(stale["proposal_id"], "approve", quiet) == 1
    [pending] = [p for p in page()["proposals"] if p["proposal_id"] == stale["proposal_id"]]
    assert pending["state"] == "proposed" and pending["decided_at"] is None
    assert [d["reasons"] for d in page()["decisions"] if d["policy_ref"] == quiet] == \
        [["operator:elsewhere"]]
