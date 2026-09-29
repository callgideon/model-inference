"""L3-SQL: the L3 service's `ControlStore` (WR-L3-1, `infrx.lab.control`) over the named RPCs
of `0032_lab_control.sql` (LAB-PUBLISH, the SQL half). No parallel catalog: revisions are
`deployment_revisions`, discovery is `catalog_listings` (read by `catalog.py`), and serving
revisions, dev revisions and internal cards are written by A3's `operations.PgRegistry`.

Each write is one RPC that compare-and-sets and writes its `lab_control_events` row in the
same transaction; refusals are typed (`not_found` for another provider's or an unknown row,
`state_conflict` for a stale compare-and-set). `provider_org_id`, `user_id` and `actor` are
the server's (the L2 membership, the operator session), never request fields. `Listing` and
`ControlEvent` carry the fields of L3's models of the same names. Nothing composes this store
yet (L3 does).

WR-LSQ-9: the `ControlReads` of L3's `Operations`/`Serving` are plain selects of 0007's rows
(`provider_servings`, `provider_deployments`, `endpoint_alias`, `listing_versions`), readable
by the control service's own login `infrx_lab_control` (0043; the listings since 0044).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..contracts.v2.money_units import Credit
from ..contracts.v2.records import (CreditLedgerEntry, DeploymentRevision, DeploymentState,
                                    Environment, RateCardSnapshot, ServingRevision)
from .jobstore import Connect
from .lab_data import PgLabDataStore
from .operations import (_DEPLOYMENT, _DEPLOYMENT_FIELDS, _SERVING, _SERVING_FIELDS, _Db,
                         _entry, _record)

# A3's selects, keyed by provider instead of id (ties in one transaction: by id)
_PROVIDER_SERVINGS = (_SERVING.rpartition("where")[0] + "where s.provider_org_id = %s "
                      "order by s.created_at, s.serving_version_id")
_PROVIDER_DEPLOYMENTS = (_DEPLOYMENT.rpartition("where")[0] + "where provider_org_id = %s "
                         "order by created_at, deployment_revision_id")
# R195/LSQ5-m1: an endpoint's alias is the newest LISTING that names a deployment on it.
# `version` is scoped to one alias (0007 lets two different aliases' deployments sit on the
# same endpoint over time), so ordering by it across aliases is not "newest" - order by
# `created_at` instead, tie-broken like WR-LSQ-9's other reads (rows one transaction wrote
# together share `created_at`).
# 0-F1/LSQ5-m2: a listing row is never deleted or moved (0007 keeps it as history), so an
# alias that republishes to a NEW deployment on a DIFFERENT endpoint leaves its OLD listing
# still "naming a deployment" on the endpoint it moved off. Ordering by `created_at` alone
# (m1's fix) still lets that stale listing win when nothing else has since claimed the old
# endpoint. A listing only counts when it is ALSO its own alias's current listing (the
# highest `version` under its `public_model_id`) - never a listing an alias has superseded.
_ENDPOINT_ALIAS = ("select l.public_model_id from infrx.catalog_listings l "
                   "join infrx.deployment_revisions d using (deployment_revision_id) "
                   "where d.endpoint_id = %s "
                   "and l.version = (select max(v.version) "
                   "from infrx.catalog_listings v "
                   "where v.public_model_id = l.public_model_id) "
                   "order by l.created_at desc, l.public_model_id desc, l.version desc limit 1")
_LISTINGS = ("select public_model_id, version, deployment_revision_id::text, rate_card_version "
             "from infrx.catalog_listings where public_model_id = %s order by version")


@dataclass(frozen=True)
class Listing:
    """One catalog listing version: the alias's current target."""

    public_model_id: str
    version: int
    deployment_revision_id: str
    rate_card_version: str


@dataclass(frozen=True)
class ControlEvent:
    action: str
    actor: str
    provider_org_id: str
    subject: str
    after: dict[str, Any]
    at: datetime
    before: dict[str, Any] | None = None


def _uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _deployment(doc: dict[str, Any]) -> DeploymentRevision:
    return _record(DeploymentRevision, _DEPLOYMENT_FIELDS,
                   [doc[k] for k in _DEPLOYMENT_FIELDS])


def _listing(doc: dict[str, Any]) -> Listing:
    return Listing(**{**doc, "deployment_revision_id": str(doc["deployment_revision_id"])})


class PgControlStore:
    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect
        self._db = _Db(connect)

    async def db_now(self) -> datetime:
        return (await self._db.one("select infrx.now()"))[0]

    async def model_provider(self, model_id: str) -> str | None:
        if not _uuid(model_id):
            return None
        row = await self._db.one("select provider_org_id::text from public.models "
                                 "where model_uuid = %s", (model_id,))
        return row[0] if row else None

    async def deployment(self, deployment_revision_id: str) -> DeploymentRevision | None:
        if not _uuid(deployment_revision_id):
            return None
        row = await self._db.one(_DEPLOYMENT, (deployment_revision_id,))
        return None if row is None else _record(DeploymentRevision, _DEPLOYMENT_FIELDS, row)

    async def endpoint(self, provider_org_id: str, name: str, environment: Environment,
                       actor: str) -> str:
        return (await self._call("lab_control_endpoint", {
            "provider_org_id": provider_org_id, "name": name,
            "environment": Environment(environment).value, "actor": actor}))["endpoint_id"]

    async def transition(self, deployment_revision_id: str, provider_org_id: str, *,
                         expected: DeploymentState, to: DeploymentState, actor: str,
                         reason: str) -> DeploymentRevision:
        return _deployment(await self._call("lab_control_transition", {
            "deployment_revision_id": deployment_revision_id,
            "provider_org_id": provider_org_id, "expected": DeploymentState(expected).value,
            "to": DeploymentState(to).value, "actor": actor, "reason": reason}))

    async def propose(self, proposal: DeploymentRevision, *, source_revision_id: str,
                      actor: str) -> DeploymentRevision:
        return _deployment(await self._call("lab_control_propose", {
            "proposal": proposal.model_dump(mode="json"),
            "source_revision_id": source_revision_id, "actor": actor}))

    async def issue_dev_key(self, *, provider_org_id: str, endpoint_id: str, user_id: str,
                            key_hash: str, prefix: str, name: str) -> str:
        return (await self._call("lab_control_dev_key", {
            "provider_org_id": provider_org_id, "endpoint_id": endpoint_id,
            "user_id": user_id, "key_hash": key_hash, "prefix": prefix,
            "name": name}))["key_id"]

    async def publish(self, public_model_id: str, card: RateCardSnapshot, *,
                      expected_version: int | None, actor: str, reason: str) -> Listing:
        return _listing(await self._call("lab_control_publish", {
            "public_model_id": public_model_id, "expected_version": expected_version,
            "actor": actor, "reason": reason, "card": {
                "rate_card_version": card.rate_card_version, "model_id": card.model_id,
                "deployment_revision_id": card.deployment_revision_id,
                "serving_version_id": card.serving_version_id,
                "input_rate_per_million": str(card.input_rate_per_million.raw("CREDIT")),
                "output_rate_per_million": str(card.output_rate_per_million.raw("CREDIT")),
                "effective_at": card.effective_at.isoformat(),
                "approved_by": card.approved_by}}))

    async def rollback(self, public_model_id: str, *, to_version: int, expected_version: int,
                       actor: str, reason: str) -> Listing:
        return _listing(await self._call("lab_control_rollback", {
            "public_model_id": public_model_id, "to_version": to_version,
            "expected_version": expected_version, "actor": actor, "reason": reason}))

    async def fund_dev_wallet(self, provider_org_id: str, amount: Credit, *, operation_id: str,
                              actor: str, reason: str) -> CreditLedgerEntry:
        return _entry((await self._call("lab_control_fund", {
            "provider_org_id": provider_org_id, "amount": str(amount),
            "operation_id": operation_id, "actor": actor, "reason": reason}))["entry"])

    async def events(self, provider_org_id: str) -> list[ControlEvent]:
        return [ControlEvent(**{**e, "provider_org_id": str(e["provider_org_id"]),
                                "at": datetime.fromisoformat(e["at"])})
                for e in await self._call("lab_control_events",
                                          {"provider_org_id": provider_org_id})]

    # --- WR-LSQ-9: L3's ControlReads (infrx.lab.control.operations) ------------------------
    async def provider_servings(self, provider_org_id: str) -> list[ServingRevision]:
        return [_record(ServingRevision, _SERVING_FIELDS, row)
                for row in await self._db.rows(_PROVIDER_SERVINGS, (provider_org_id,))]

    async def provider_deployments(self, provider_org_id: str) -> list[DeploymentRevision]:
        return [_record(DeploymentRevision, _DEPLOYMENT_FIELDS, row)
                for row in await self._db.rows(_PROVIDER_DEPLOYMENTS, (provider_org_id,))]

    async def endpoint_alias(self, endpoint_id: str) -> str | None:
        if not _uuid(endpoint_id):
            return None
        row = await self._db.one(_ENDPOINT_ALIAS, (endpoint_id,))
        return row[0] if row else None

    async def listing_versions(self, public_model_id: str) -> list[Listing]:
        return [Listing(*row) for row in await self._db.rows(_LISTINGS, (public_model_id,))]
