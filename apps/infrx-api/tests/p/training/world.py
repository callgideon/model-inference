"""P3's fake world: P1's store (every Lab record kind) plus D7's checkpoint receipts; D8/D6J's
run ledger (`FakeRunLedger`) and B3's evaluation subscription (`FakeEvaluations`) in memory -
the ports lab-sql and eval-ops implement - and the automatic-connector protocol test server
(`protocol_app`), in process through `httpx.ASGITransport` or on p3's port 57531.
"""
from __future__ import annotations

import hashlib
import json

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from infrx.contracts import errors
from infrx.contracts.lab import states

from ...n.imports.world import NEMO
from ...n.versions.test_versions import uid
from ..annotations.world import Store

PAYER = f"lab:payer:{NEMO}:{uid(1, 0x9a)}@sha256:{'a' * 64}"


class CheckpointStore(Store):
    """D7's `lab_receive_checkpoint` / `lab_checkpoint_transition` rules in memory."""

    def __init__(self) -> None:
        super().__init__()
        self.receipts: dict[str, dict] = {}

    async def receive_checkpoint(self, *, provider_org_id, checkpoint_id, external_run_ref,
                                 artifact_digest) -> dict:
        record = self.catalog.resolve(external_run_ref, provider_org_id=provider_org_id)
        if record.schema_id != "lab.external_run.1":
            raise errors.NotFound("no such external run")
        row = self.receipts.setdefault(checkpoint_id, {
            "checkpoint_id": checkpoint_id, "provider_org_id": provider_org_id,
            "external_run_ref": external_run_ref, "artifact_digest": artifact_digest,
            "state": "received"})
        if (row["provider_org_id"], row["external_run_ref"], row["artifact_digest"]) != (
                provider_org_id, external_run_ref, artifact_digest):
            raise errors.IdempotencyConflict("the checkpoint id names another artifact")
        return dict(row)

    async def transition_checkpoint(self, checkpoint_id, state, *, provider_org_id) -> dict:
        row = self.receipts.get(checkpoint_id)
        if row is None or row["provider_org_id"] != provider_org_id:
            raise errors.NotFound("no such checkpoint")
        row["state"] = states.transition("checkpoint", row["state"], state)
        return dict(row)


class FakeRunLedger:
    """D8's external-run rows (CAS along F3's machine), D6J's one reservation per key with a
    single settlement, and append-only notes."""

    def __init__(self) -> None:
        self.runs: dict[tuple[str, str], dict] = {}
        self.reservations: dict[tuple[str, str], dict] = {}
        self.notes: dict[tuple[str, str], dict] = {}

    async def get(self, external_run_id, *, provider_org_id):
        row = self.runs.get((provider_org_id, external_run_id))
        return dict(row) if row else None

    async def move(self, external_run_id, *, provider_org_id, expected, target, **fields):
        key = (provider_org_id, external_run_id)
        row = self.runs.get(key)
        if expected is None:
            new = {"state": target, **fields}
            if row is not None and row != new:
                raise errors.StateConflict("this external run already exists otherwise")
            self.runs[key] = row = row or new
            return dict(row)
        if row is None or row["state"] != expected:
            raise errors.StateConflict(f"expected {expected}, found {row and row['state']}")
        self.runs[key] = {**row, **fields, "state": states.transition("external_run", expected,
                                                                      target)}
        return dict(self.runs[key])

    async def reserve(self, key, *, provider_org_id, payer_ref, limit):
        row = self.reservations.setdefault((provider_org_id, key), {
            "payer_ref": payer_ref, "limit": limit, "state": "held", "cost": None})
        if (row["payer_ref"], row["limit"]) != (payer_ref, limit):
            raise errors.IdempotencyConflict("this key reserved another budget")
        return dict(row)

    async def settle(self, key, *, provider_org_id, cost):
        row = self.reservations[(provider_org_id, key)]
        if row["state"] == "settled" and row["cost"] != cost:
            raise errors.IdempotencyConflict("already settled at another cost")
        if row["state"] == "released":
            raise errors.StateConflict("a released reservation is not settled")
        row.update(state="settled", cost=cost)
        return dict(row)

    async def release(self, key, *, provider_org_id):
        row = self.reservations[(provider_org_id, key)]
        if row["state"] == "settled":
            raise errors.StateConflict("a settled reservation is not released")
        row["state"] = "released"
        return dict(row)

    async def note(self, key, body, *, provider_org_id):
        stored = self.notes.setdefault((provider_org_id, key), dict(body))
        if stored != body:
            raise errors.IdempotencyConflict(f"{key} holds another note")
        return dict(stored)

    async def noted(self, key, *, provider_org_id):
        stored = self.notes.get((provider_org_id, key))
        return dict(stored) if stored is not None else None


class FakeEvaluations:
    """B3: one evaluation per checkpoint (a redelivery queues nothing new)."""

    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}
        self.calls = 0

    async def evaluate(self, *, provider_org_id, checkpoint_id, dataset_ref, split,
                       holdout_sha256) -> str:
        self.calls += 1
        n = len(self.runs) + 1
        return self.runs.setdefault(checkpoint_id, {
            "run_ref": f"lab:run:{provider_org_id}:{uid(n, 0xb1)}@sha256:{'b' * 64}",
            "dataset_ref": dataset_ref, "split": split, "holdout_sha256": holdout_sha256,
            "state": "queued"})["run_ref"]

    async def evaluation(self, *, provider_org_id, checkpoint_id):
        found = self.runs.get(checkpoint_id)
        return dict(found) if found else None


def protocol_app(mode: str = "ok", delay_s: float = 0.0) -> FastAPI:
    """The automatic-connector protocol, with a failure mode: `reject` (422), `accept_503`
    (accept, then answer 503), `accept_sleep` (accept, then answer after `delay_s`)."""
    import asyncio
    api = FastAPI()
    api.state.mode, api.state.posts, api.state.jobs, api.state.keys = mode, 0, {}, {}

    @api.post("/jobs")
    async def create(request: Request):
        api.state.posts += 1
        key = request.headers.get("Idempotency-Key")
        if api.state.mode == "reject" or not key:
            return JSONResponse({"error": "refused"}, status_code=422)
        body = await request.json()
        job_id = api.state.keys.setdefault(key, f"job-{len(api.state.keys) + 1}")
        api.state.jobs.setdefault(job_id, {"state": "running", "bundle": body})
        if api.state.mode == "accept_503":
            return JSONResponse({"error": "busy"}, status_code=503)
        if api.state.mode == "accept_sleep":
            await asyncio.sleep(delay_s)
        return {"job_id": job_id}

    @api.get("/jobs")
    async def lookup(key: str):
        if key not in api.state.keys:
            return JSONResponse({"error": "no such job"}, status_code=404)
        return {"job_id": api.state.keys[key]}

    @api.get("/jobs/{job_id}")
    async def status(job_id: str):
        job = api.state.jobs[job_id]
        return {k: v for k, v in job.items() if k != "bundle"}

    @api.post("/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        if api.state.jobs[job_id].get("uncancellable"):
            return JSONResponse({"error": "too late to cancel"}, status_code=409)
        api.state.jobs[job_id]["state"] = "cancelled"
        return await status(job_id)

    return api


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p3")


def descriptor(base_model: str = "marlin-2b", adaptation: str = "lora") -> bytes:
    return json.dumps({"format": "infrx.checkpoint.1", "base_model": base_model,
                       "adaptation": adaptation, "files": ["adapter.safetensors"]}).encode()


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()

