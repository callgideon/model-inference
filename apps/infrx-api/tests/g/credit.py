"""D2's CREDIT admission (`PgJobStore.admit_credit`, migration 0011), in memory, for the
ingress cases - a stand-in, not a second specification.

F2P's wire-in adds v2 admission to `contracts/fakes/state.py`; these cases move onto it,
and onto D2's real store at integration. What this keeps from 0011 is exactly what the
ingress relies on:

* one call is one transaction: every check runs before any write, so a refusal writes
  no job, no hold, no idempotency record and no journal reservation;
* an idempotent replay is answered first, with the admission as it was, pins and all;
* the key is re-read by id (revoked after the ingress cached it), the wallet resolved
  from the key's own identity (R66, `resolve_wallet`), and the requested name
  re-resolved and pinned **now** (the ingress's resolution is never trusted);
* the hold comes from the pinned card; capacity and available CREDIT are checked last.
"""
from __future__ import annotations

import dataclasses

from fastapi.responses import JSONResponse

from infrx.auth.context import auth_context
from infrx.contracts import errors, wire
from infrx.contracts.conformance.v2_fakes import NOW, FakeCatalogDirectory, FakeWalletDirectory
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.v2 import ports as v2ports, records as v2
from infrx.gateway.routes import catalog as resolution

Audience = v2.CredentialAudience


@dataclasses.dataclass
class CreditStore:
    catalog: FakeCatalogDirectory
    wallets: FakeWalletDirectory
    keys: dict                                   # key id -> 0009 `api_keys` row
    max_active: int = DEFAULTS.max_active_jobs
    jobs: dict = dataclasses.field(default_factory=dict)      # request id -> AdmissionV2
    holds: dict = dataclasses.field(default_factory=dict)     # request id -> (wallet, hold)
    replays: dict = dataclasses.field(default_factory=dict)   # idem scope -> (hash, request)
    journal: dict = dataclasses.field(default_factory=dict)   # request id -> reserved bytes
    done: set = dataclasses.field(default_factory=set)

    def side_effects(self) -> tuple:
        return (dict(self.jobs), dict(self.holds), dict(self.replays), dict(self.journal))

    def finish(self, request_id: str) -> None:
        """The job went terminal: its capacity is free (settlement is D5's)."""
        self.done.add(request_id)

    def available(self, wallet: v2.WalletRef):
        held = (hold for owner, hold in self.holds.values() if owner == wallet.wallet_id)
        return wallet.available - sum(held, wallet.available.zero())

    async def admit_credit(self, request, idem) -> v2.AdmissionV2:
        scope = (idem.org_id, idem.operation, idem.key)
        if idem.key is not None and scope in self.replays:
            payload_hash, request_id = self.replays[scope]
            if payload_hash != idem.payload_hash:
                raise errors.IdempotencyConflict("the key was used for another payload")
            # A new record, not `model_copy`: R78 forbids editing an admitted one.
            return v2.AdmissionV2.model_validate({**self.jobs[request_id].model_dump(),
                                                  "replayed": True})
        row = self.keys.get(request.key_id)
        if row is None or row["revoked_at"] or row["org_id"] != request.org_id:
            raise errors.InvalidApiKey("the key is revoked or unknown")
        auth = auth_context(audience=row["audience"], org_id=row["org_id"], key_id=row["id"],
                            user_id=row.get("user_id") or row.get("created_by"),
                            provider_org_id=row.get("provider_org_id"),
                            endpoint_id=row.get("endpoint_id"))
        wallet = (await self.wallets.consumer_wallet_for_user(auth.user_id)
                  if auth.audience is Audience.consumer else
                  await self.wallets.provider_dev_wallet(auth.provider_org_id)
                  if auth.audience is Audience.provider_dev else None)
        wallet = v2ports.resolve_wallet(auth, wallet)
        resolved = await resolution.resolve(self.catalog, auth, request.model_revision)
        if len(self.jobs) - len(self.done) >= self.max_active:
            raise errors.CapacityExhausted("total active job limit reached", retry_after_s=5)
        hold = resolved.rate_card.maximum_hold(request.max_input_tokens,
                                               request.max_output_tokens)
        if hold > self.available(wallet):
            raise errors.InsufficientCredit("the hold exceeds the wallet's available CREDIT")
        admission = v2.AdmissionV2(
            request_id=request.request_id, job_handle="job_" + request.request_id.replace("-", ""),
            org_id=request.org_id, wallet_id=wallet.wallet_id, pins=resolved.pins,
            rate_card=resolved.rate_card, maximum_hold=hold, admitted_at=NOW)
        self.jobs[request.request_id] = admission
        self.holds[request.request_id] = (wallet.wallet_id, hold)
        self.journal[request.request_id] = DEFAULTS.journal_job_reserve_bytes
        if idem.key is not None:
            self.replays[scope] = (idem.payload_hash, request.request_id)
        return admission


def acceptor(store: CreditStore, before=None):
    """G2's `accept` reduced to the admission: `before` is whatever happens between the
    ingress's validation and the store's transaction (the race)."""
    async def accept(auth, request, idem):
        if before is not None:
            before()
        admission = await store.admit_credit(request, idem)
        headers = {wire.HEADER_IDEMPOTENCY_REPLAYED: "true"} if admission.replayed else {}
        return JSONResponse(admission.model_dump(mode="json"), status_code=202, headers=headers)

    return accept
