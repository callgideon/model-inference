"""AP-06's worlds: L3's LAB-PUBLISH world (`tests/l/control/worlds.py`: NemoStation's Marlin
listed at version 1, provider B empty, ADMIN/DEV/VIEWER members, a platform operator) behind
the new routes, as a FastAPI app built the way R270 says a lane tests before it is mounted:
`register(app, rt)` over a `SimpleNamespace` runtime and `control.StaticActors`.

`FakeReadiness` is AP-05's readiness by protocol (`Readiness.receipt`) until it merges;
`FakeCredentials` is SR-AP06-1's dev key / dev wallet reads over the fake store's own rows.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import api, errors
from infrx.gateway import control
from infrx.gateway.routes import lab_control, operator_publication as op
from infrx.lab.control.operations import Operations
from infrx.state.control_ops import FakeControlOps

from tests.l.control.test_control import ready_dev
from tests.l.control.worlds import ALIAS, IDS, OPERATOR, pin

__all__ = ["ALIAS", "IDS", "OPERATOR", "pin", "ready_dev"]


def run(coro):
    return asyncio.run(coro)


class Clock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 2, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


class FakeReadiness:
    """deployment_revision_id -> receipt; `down` makes every read an outage."""

    def __init__(self) -> None:
        self.receipts: dict[str, op.ReadinessReceipt] = {}
        self.down = False

    def ready(self, deployment, *, serving_version_id=None, ready=True) -> None:
        self.receipts[deployment.deployment_revision_id] = op.ReadinessReceipt(
            deployment_revision_id=deployment.deployment_revision_id,
            serving_version_id=serving_version_id or deployment.serving_version_id,
            ready=ready, checked_at="2026-10-02T00:00:00Z")

    async def receipt(self, deployment_revision_id):
        if self.down:
            raise errors.DependencyUnavailable("readiness store down")
        return self.receipts.get(deployment_revision_id)


class FakeCredentials:
    """SR-AP06-1's reads over `FakeControl`'s key rows and dev wallets."""

    def __init__(self, store) -> None:
        self.store = store

    def _key(self, key_id, row) -> op.DevKey:
        return op.DevKey(key_id=key_id, endpoint_id=row["endpoint_id"], name=row["name"],
                         prefix=row["prefix"], created_at="2026-10-02T00:00:00Z",
                         revoked_at=row.get("revoked_at"))

    async def keys(self, provider_org_id, endpoint_id):
        return [self._key(k, r) for k, r in self.store.keys.items()
                if (r["provider_org_id"], r["endpoint_id"]) == (provider_org_id, endpoint_id)]

    async def revoke(self, provider_org_id, endpoint_id, key_id, *, actor, idempotency_key):
        row = self.store.keys.get(key_id)
        if row is None or (row["provider_org_id"], row["endpoint_id"]) != (provider_org_id,
                                                                           endpoint_id):
            raise errors.NotFound("no such dev key")
        row.setdefault("revoked_at", "2026-10-02T00:00:01Z")
        return self._key(key_id, row)

    async def wallet(self, provider_org_id):
        wallet = self.store.wallets.get(provider_org_id)
        total = wallet.ledger_total if wallet else "0.00000000"
        return op.DevWallet(provider_org_id=provider_org_id, opened=wallet is not None,
                            balance=api.Money(amount=str(total), unit="CREDIT"))


def session(user_id: str, *, operator: bool = False, audience: str = "session") -> api.Actor:
    return api.Actor(audience=audience, user_id=user_id, operator=operator)  # type: ignore[arg-type]


def operator_actor(w) -> api.Actor:
    return session(w.OPS_USER, operator=True)


class Api:
    """One API instance over world `w`: `as_(actor)` swaps who is calling."""

    def __init__(self, w, *, readiness: object = "fake", credentials=None, ops=None,
                 mount=True) -> None:
        self.w = w
        self.clock = Clock()
        self.readiness = FakeReadiness() if readiness == "fake" else readiness
        self.ops = ops if ops is not None else FakeControlOps(now=self.clock)
        self.pub = op.Publication(Operations(w.control, w.control_store), self.ops,
                                  self.readiness, credentials)
        self.actors = control.StaticActors(operator_actor(w))
        rt = SimpleNamespace(actors=self, **({"lab_publication": self.pub} if mount else {}))
        app = FastAPI()
        op.register(app, rt)
        lab_control.register(app, rt)
        self.client = TestClient(app)

    async def actor(self, request):
        return await self.actors.actor(request)

    def as_(self, actor: api.Actor | None) -> Api:
        self.actors = control.StaticActors(actor)
        return self

    def post(self, path, body=None, key="key-00000001", **params):
        headers = {"Idempotency-Key": key} if key else {}
        return self.client.post(path, json=body, headers=headers, params=params)

    def get(self, path, **params):
        return self.client.get(path, params=params)


def proposed(w, label="2026-10-01"):
    """A's validated dev revision proposed by its administrator: (serving, source, proposal)."""
    s, source = ready_dev(w, label, endpoint=f"preview-{label}")
    proposal = run(w.control.propose(w.ADMIN_A, w.A, source.deployment_revision_id,
                                     endpoint_name="marlin-2b"))
    return s, source, proposal


def approval(expected_version=1, rate="rc_2026_10_01", reason="launch") -> dict:
    return {"expected_version": expected_version, "rate_card_version": rate,
            "input_rate": "300", "output_rate": "900", "reason": reason}
