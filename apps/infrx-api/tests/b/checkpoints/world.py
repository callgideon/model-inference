"""B3's fake world: B1's D7 fake plus 0029's checkpoint receipts and outbox, the ledger
B3 asks lab-sql to persist (WR-B3-1), a registry of artifact bytes, a dev deployer and the
L2 port. Keys are generated here, per world, and never written anywhere.

`FakeCheckpointLedger` keeps the rules the handler relies on: one event per checkpoint id
(another body under the same id is a conflict), provider-scoped reads, one decision per
(subscription, checkpoint), written once. `receive_checkpoint`/`transition_checkpoint` keep
0029's: one receipt and ONE `checkpoint_received` outbox event per id, the checkpoint state
machine, provider scoping.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

from infrx.contracts import errors
from infrx.contracts.lab import records, states
from infrx.datasets.imports import sample_key
from infrx.evaluation import checkpoints
from infrx.evaluation.runner import evaluator_ref
from infrx.media.store import InMemoryObjectStore

from ..runner.world import (DEV, EVALUATOR_ID, NEMO, OTHER, SPEC, FakeEvalStore, access,
                            content, digest_of, harness, manifest, uid)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
PAYER = f"lab:payer:{NEMO}:{uid(1, 0xfa)}@sha256:" + "0" * 64


def ts(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def safetensors(seed: int = 0) -> bytes:
    """A minimal well-formed safetensors file: one F32 tensor of 2 values."""
    header = json.dumps({"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}).encode()
    return len(header).to_bytes(8, "little") + header + seed.to_bytes(8, "little")


def sha(data: bytes) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(data).hexdigest()


class CheckpointStore(FakeEvalStore):
    def __init__(self) -> None:
        super().__init__()
        self.receipts: dict[str, dict] = {}
        self.outbox: list[dict] = []

    async def receive_checkpoint(self, *, provider_org_id, checkpoint_id, external_run_ref,
                                 artifact_digest):
        self.catalog.resolve(external_run_ref, provider_org_id=provider_org_id)
        mine = {"provider": provider_org_id, "external_run_ref": external_run_ref,
                "artifact_digest": artifact_digest}
        have = self.receipts.get(checkpoint_id)
        if have is None:
            self.receipts[checkpoint_id] = have = {**mine, "state": "received"}
            self.outbox.append({"kind": "checkpoint_received", "provider_org_id": provider_org_id,
                                "payload": {"checkpoint_id": checkpoint_id}})
        elif {k: have[k] for k in mine} != mine:
            raise errors.IdempotencyConflict("the checkpoint id names another artifact")
        return {"checkpoint_id": checkpoint_id, "state": have["state"], **mine}

    async def transition_checkpoint(self, checkpoint_id, state, *, provider_org_id):
        have = self.receipts.get(checkpoint_id)
        if have is None or have["provider"] != provider_org_id:
            raise errors.NotFound("no such checkpoint for this provider")
        have["state"] = states.transition("checkpoint", have["state"], state)
        return {"checkpoint_id": checkpoint_id, **have}


class FakeCheckpointLedger:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.by_id: dict[str, checkpoints.CheckpointEvent] = {}
        self.rejected: dict[str, str] = {}
        self.subs: dict[str, checkpoints.Subscription] = {}
        self.decided: dict[tuple[str, str], dict] = {}
        self.crash_on_decide = 0

    async def db_now(self) -> datetime:
        return self.now

    async def record_event(self, event):
        have = self.by_id.setdefault(event.checkpoint_id, event)
        if have != event:
            raise errors.IdempotencyConflict("the checkpoint id names another event")
        return have

    async def event(self, checkpoint_id, *, provider_org_id):
        event = self.by_id.get(checkpoint_id)
        if event is None or event.provider_org_id != provider_org_id:
            raise errors.NotFound("no such checkpoint event for this provider")
        return event

    async def events(self, external_run_ref, *, provider_org_id):
        return [(e, e.checkpoint_id in self.rejected) for e in self.by_id.values()
                if e.external_run_ref == external_run_ref
                and e.provider_org_id == provider_org_id]

    async def reject(self, checkpoint_id, reason, *, provider_org_id):
        self.rejected.setdefault(checkpoint_id, reason)

    async def add_subscription(self, sub):
        return self.subs.setdefault(sub.subscription_id, sub)

    async def subscriptions(self, external_run_ref, *, provider_org_id):
        return [s for s in self.subs.values() if s.external_run_ref == external_run_ref
                and s.provider_org_id == provider_org_id]

    async def decisions(self, subscription_id):
        return {c: d for (s, c), d in self.decided.items() if s == subscription_id}

    async def decide(self, subscription_id, checkpoint_id, *, state, reason, run_id):
        if self.crash_on_decide:
            self.crash_on_decide -= 1
            raise Crash("died before recording the decision")
        return self.decided.setdefault((subscription_id, checkpoint_id),
                                       {"state": state, "reason": reason, "run_id": run_id})


class Crash(BaseException):
    """A worker process dying."""


class Registry:
    """`mem://<name>` artifacts; `fetched` counts reads."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.fetched: list[str] = []

    async def fetch(self, uri: str) -> bytes:
        self.fetched.append(uri)
        return self.blobs[uri]


class Deployer:
    """L3's dev deployment, idempotent per checkpoint; records every call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.deployed: dict[str, dict] = {}

    async def deploy(self, *, provider_org_id, checkpoint_id, uri, digest) -> str:
        self.calls.append({"checkpoint_id": checkpoint_id, "digest": digest})
        self.deployed.setdefault(checkpoint_id, {"environment": "dev", "digest": digest})
        return f"lab:serving:{provider_org_id}:{checkpoint_id}@sha256:{digest[7:]}"


class World:
    """One provider (NEMO), one external training run, a 3-sample dataset and a harness."""

    def __init__(self) -> None:
        self.store, self.ledger = CheckpointStore(), FakeCheckpointLedger()
        self.registry, self.deployer, self.access = Registry(), Deployer(), access()
        self.objects = InMemoryObjectStore()
        self.key_id, self.secret = "key-nemo", secrets.token_bytes(32)
        self.other_secret = secrets.token_bytes(32)
        self.keys = {self.key_id: (NEMO, self.secret), "key-other": (OTHER, self.other_secret)}
        self.grant = self.store.add_grant()
        source = self._run(self.store.register_source(
            provider_org_id=NEMO, source_id="5a000000-0000-4000-8000-0000000000b3",
            content_digest="sha256:" + "3" * 64, grant_ref=self.grant, actor=DEV))
        self.manifest = manifest(3, self.grant, source, dataset=3)
        for i, sample in enumerate(self.manifest["samples"], 1):
            self._run(self.objects.put_if_absent(sample_key(NEMO, sample["content_digest"]),
                                                 content(i), "application/json"))
        self.dataset = self._run(self.store.publish(self.manifest, provider_org_id=NEMO,
                                                    actor=DEV))
        self.harness = self._run(self.store.publish(harness(), provider_org_id=NEMO, actor=DEV))
        self.external = self._run(self.store.publish(
            self.external_run(uid(1, 0xe3)), provider_org_id=NEMO, actor=DEV))

    @staticmethod
    def _run(coro):
        import asyncio
        return asyncio.run(coro)

    def external_run(self, external_run_id: str, provider: str = NEMO) -> dict:
        return {"schema": "lab.external_run.1", "provider_org_id": provider,
                "external_run_id": external_run_id, "purpose": "training",
                "connector": "manual-bundle", "dataset_ref": self.dataset,
                "submit_key": records.submit_key(external_run_id), "state": "prepared",
                "budget": {"limit": {"unit": "PROVIDER_USD", "value": "40.00000000"},
                           "reserved": {"unit": "PROVIDER_USD", "value": "0.00000000"},
                           "payer_ref": PAYER}}

    def subscription(self, n: int, *, policy: str = "latest_only", run_limit: str = "10",
                     limit: str = "100", max_active: int = 5, **over) -> dict:
        return {"subscription_id": uid(n, 0x5b), "provider_org_id": NEMO,
                "external_run_ref": self.external, "dataset_ref": self.dataset,
                "harness_ref": self.harness,
                "evaluator_ref": evaluator_ref(SPEC, provider_org_id=NEMO,
                                               evaluator_id=EVALUATOR_ID),
                "evaluator": SPEC, "seed": 7, "max_cases": 3,
                "run_limit": {"unit": "CREDIT", "value": f"{run_limit}.00000000"},
                "limit": {"unit": "CREDIT", "value": f"{limit}.00000000"},
                "max_active": max_active, "policy": policy, **over}

    def event(self, n: int, *, step: int | None = None, data: bytes | None = None,
              uri: str | None = None, issued: datetime | None = None, **over) -> dict:
        data = safetensors(n) if data is None else data
        uri = uri or f"mem://ckpt-{n}"
        self.registry.blobs.setdefault(uri, data)
        return {"schema": checkpoints.EVENT_SCHEMA, "provider_org_id": NEMO,
                "key_id": self.key_id, "checkpoint_id": uid(n, 0xc3),
                "external_run_ref": self.external, "step": n if step is None else step,
                "artifact": {"uri": uri, "digest": sha(data)},
                "issued_at": ts(issued or NOW), **over}

    def body(self, event: dict, secret: bytes | None = None) -> tuple[bytes, str]:
        raw = json.dumps(event).encode()
        return raw, checkpoints.sign(raw, self.secret if secret is None else secret)


__all__ = ["DEV", "NEMO", "OTHER", "SPEC", "NOW", "Crash", "World", "digest_of", "sha",
           "safetensors", "ts", "timedelta"]
