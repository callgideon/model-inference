"""One in-memory registry database for L3 and the lanes that consume it (L4, I2L, E3L).

`FakeControl` is lab-sql's L3-SQL seam (`ControlStore`) AND the A3 relations it moves, so it
also answers A3's `Registry.put`, `CatalogDirectory` (the resolution rules of
`infrx/state/catalog.py`, restated over dicts) and `WalletDirectory.provider_dev_wallet`: one
fake database, never a second catalog in production code. It enforces what L3 leaves to the
store - compare-and-set, 0007's state graph, the proposal source, the listing rules, the
wallet's opening balance and allocation replay - and leaves out the ownership foreign keys the
service checks first (so a service defect stays visible here; PostgreSQL has both).
"""
from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from ...contracts import errors
from ...contracts.v2 import records as v2
from . import ControlEvent, Listing

S = v2.DeploymentState
# 0007 `deployment_revisions_guard`, verbatim.
MOVES = {S.draft: {S.validating, S.retired}, S.validating: {S.ready_private, S.retired},
         S.ready_private: {S.retired}, S.proposed_public: {S.active, S.retired},
         S.active: {S.draining, S.retired}, S.draining: {S.retired}}


@dataclasses.dataclass
class FakeControl:
    """`clock` is any object with a `now` (L2's `FakeAccessStore`): one clock (R7)."""

    clock: Any
    slugs: dict[str, str]                                   # provider -> slug
    models: dict[str, tuple[str, str]]                      # model_uuid -> (alias, provider)
    policy: v2.DataAccessPolicyRef
    endpoints: dict[str, tuple[str, str, v2.Environment]] = dataclasses.field(
        default_factory=dict)                               # id -> (provider, name, env)
    servings: dict[str, v2.ServingRevision] = dataclasses.field(default_factory=dict)
    deployments: dict[str, v2.DeploymentRevision] = dataclasses.field(default_factory=dict)
    cards: dict[str, v2.RateCardSnapshot] = dataclasses.field(default_factory=dict)
    listings: dict[str, list[Listing]] = dataclasses.field(default_factory=dict)
    keys: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)
    wallets: dict[str, v2.WalletRef] = dataclasses.field(default_factory=dict)
    ledger: list[v2.CreditLedgerEntry] = dataclasses.field(default_factory=list)
    audit: list[ControlEvent] = dataclasses.field(default_factory=list)
    # R195/LSQ5-m1: every listing ever published, in the order it was (global, across every
    # alias - `listings` alone only orders one alias's own versions). `endpoint_alias` walks
    # this backwards for the newest listing naming a deployment on an endpoint, the fake's
    # analogue of the SQL read's `order by created_at desc`.
    _listing_log: list[Listing] = dataclasses.field(default_factory=list, repr=False, compare=False)

    # --- seeding (an operator seed; synchronous) -----------------------------------
    def _table(self, row):
        if isinstance(row, v2.ServingRevision):
            return self.servings, row.serving_version_id
        if isinstance(row, v2.DeploymentRevision):
            return self.deployments, row.deployment_revision_id
        return self.cards, row.rate_card_version

    def put_now(self, row) -> bool:
        table, key = self._table(row)
        if key in table:
            if table[key] != row:
                raise errors.Conflict("registry rows are immutable: a different row holds "
                                      "this id")
            return False
        table[key] = row
        return True

    def list_now(self, alias: str, deployment_revision_id: str, card: str) -> Listing:
        versions = self.listings.setdefault(alias, [])
        listing = Listing(public_model_id=alias, version=len(versions) + 1,
                          deployment_revision_id=deployment_revision_id, rate_card_version=card)
        versions.append(listing)
        self._listing_log.append(listing)
        return listing

    def _event(self, action, actor, provider, subject, after, before=None) -> None:
        self.audit.append(ControlEvent(action=action, actor=actor, provider_org_id=provider,
                                       subject=subject, before=before, after=after,
                                       at=self.clock.now))

    # --- A3: Registry.put, CatalogDirectory, WalletDirectory -----------------------------
    async def put(self, row) -> bool:
        return self.put_now(row)

    async def resolve(self, requested_model: str, *, audience: v2.CredentialAudience,
                      endpoint_id: str | None) -> v2.DeploymentRevision | None:
        """`PgCatalogDirectory.resolve`: the highest listing version whose deployment is
        public and active (and serves `@label`); else, for a provider_dev credential, the
        newest VALIDATED (`ready_private`) private revision on ITS endpoint named
        `<slug>/<name>-<env>` - a later draft never shadows it (WR-L3-5, catalog.py's
        `_PRIVATE`)."""
        alias, _, label = requested_model.partition("@")

        def labelled(d):
            return not label or self.servings[d.serving_version_id].revision_label == label
        for listing in reversed(self.listings.get(alias, [])):
            d = self.deployments[listing.deployment_revision_id]
            if d.visibility is v2.Visibility.public and d.state is S.active and labelled(d):
                return d
        if audience is not v2.CredentialAudience.provider_dev or not endpoint_id:
            return None
        provider, name, env = self.endpoints.get(endpoint_id, (None, None, None))
        if provider is None or alias != f"{self.slugs[provider]}/{name}-{env}":
            return None
        private = [d for d in self.deployments.values() if d.endpoint_id == endpoint_id
                   and d.visibility is v2.Visibility.private and d.state is S.ready_private
                   and labelled(d)]
        return max(private, key=lambda d: (d.created_at, d.deployment_revision_id),
                   default=None)

    async def serving_revision(self, serving_version_id: str) -> v2.ServingRevision | None:
        return self.servings.get(serving_version_id)

    async def active_rate_card(self, deployment_revision_id: str) -> v2.RateCardSnapshot | None:
        """The card of the newest listing naming the deployment, else (never listed) its
        newest effective card."""
        named = [lst for lists in self.listings.values() for lst in lists
                 if lst.deployment_revision_id == deployment_revision_id]
        if named:
            return self.cards[max(named, key=lambda lst: lst.version).rate_card_version]
        own = [c for c in self.cards.values() if c.deployment_revision_id
               == deployment_revision_id and c.effective_at <= self.clock.now]
        return max(own, key=lambda c: c.effective_at, default=None)

    async def data_access_policy(self, deployment_revision_id: str):
        return self.policy if deployment_revision_id in self.deployments else None

    async def provider_dev_wallet(self, provider_org_id: str) -> v2.WalletRef | None:
        return self.wallets.get(provider_org_id)

    # --- L3-SQL: ControlStore ---------------------------------------------------------
    async def db_now(self):
        return self.clock.now

    async def model_provider(self, model_id: str) -> str | None:
        return self.models.get(model_id, (None, None))[1]

    async def deployment(self, deployment_revision_id: str) -> v2.DeploymentRevision | None:
        return self.deployments.get(deployment_revision_id)

    async def endpoint(self, provider_org_id, name, environment, actor) -> str:
        for endpoint_id, row in self.endpoints.items():
            if row == (provider_org_id, name, environment):
                return endpoint_id
        endpoint_id = str(uuid.uuid4())
        self.endpoints[endpoint_id] = (provider_org_id, name, environment)
        return endpoint_id

    async def transition(self, deployment_revision_id, provider_org_id, *, expected, to,
                         actor, reason) -> v2.DeploymentRevision:
        d = self.deployments.get(deployment_revision_id)
        if d is None or d.provider_org_id != provider_org_id:
            raise errors.NotFound("no such deployment revision")
        if d.state is not expected or to not in MOVES.get(expected, ()):
            raise errors.StateConflict(f"{d.state} -> {to} is not this revision's move")
        moved = self.deployments[deployment_revision_id] = d.model_copy(update={"state": to})
        self._event("lab_transition", actor, provider_org_id, deployment_revision_id,
                    {"state": to.value, "reason": reason}, {"state": expected.value})
        return moved

    async def propose(self, proposal, *, source_revision_id, actor) -> v2.DeploymentRevision:
        source = self.deployments.get(source_revision_id)
        if source is None or source.provider_org_id != proposal.provider_org_id:
            raise errors.NotFound("no such dev revision")
        if (source.state is not S.ready_private
                or source.serving_version_id != proposal.serving_version_id):
            raise errors.StateConflict("only a validated dev revision's serving is proposed")
        if proposal.state is not S.proposed_public:
            raise errors.InvalidRequest("a proposal is a proposed_public revision")
        self.put_now(proposal)
        self._event("lab_propose", actor, proposal.provider_org_id,
                    proposal.deployment_revision_id, {"source": source_revision_id})
        return proposal

    async def issue_dev_key(self, *, provider_org_id, endpoint_id, user_id, key_hash, prefix,
                            name) -> str:
        if self.endpoints.get(endpoint_id, (None, None, None))[::2] != (
                provider_org_id, v2.Environment.dev):
            raise errors.NotFound("no such dev endpoint")
        key_id = str(uuid.uuid4())
        self.keys[key_id] = {"provider_org_id": provider_org_id, "endpoint_id": endpoint_id,
                             "user_id": user_id, "key_hash": key_hash, "prefix": prefix,
                             "name": name}
        self._event("lab_dev_key", user_id, provider_org_id, key_id,
                    {"endpoint_id": endpoint_id, "prefix": prefix})
        return key_id

    def _current(self, alias, expected_version) -> Listing | None:
        current = (self.listings.get(alias) or [None])[-1]
        if (current.version if current else None) != expected_version:
            raise errors.StateConflict(f"{alias} is no longer at version {expected_version}")
        return current

    async def publish(self, public_model_id, card, *, expected_version, actor,
                      reason) -> Listing:
        current = self._current(public_model_id, expected_version)
        d = self.deployments.get(card.deployment_revision_id)
        if d is None:
            raise errors.NotFound("no such deployment revision")
        if d.state is not S.proposed_public:
            raise errors.StateConflict("only a proposed public revision is published")
        if self.models.get(card.model_id, (None,))[0] != public_model_id \
                or card.serving_version_id != d.serving_version_id:
            raise errors.InvalidRequest("the card prices another model or revision")
        self.put_now(card)
        self.deployments[d.deployment_revision_id] = d.model_copy(update={"state": S.active})
        listing = self.list_now(public_model_id, d.deployment_revision_id,
                                card.rate_card_version)
        self._event("lab_publish", actor, d.provider_org_id, public_model_id,
                    listing.model_dump() | {"reason": reason},
                    current.model_dump() if current else None)
        return listing

    async def rollback(self, public_model_id, *, to_version, expected_version, actor,
                       reason) -> Listing:
        current = self._current(public_model_id, expected_version)
        if not 1 <= to_version < current.version:
            raise errors.NotFound(f"{public_model_id} has no earlier version {to_version}")
        target = self.listings[public_model_id][to_version - 1]
        d = self.deployments[target.deployment_revision_id]
        if d.state is not S.active:
            raise errors.StateConflict("the target revision is no longer active")
        listing = self.list_now(public_model_id, target.deployment_revision_id,
                                target.rate_card_version)
        self._event("lab_rollback", actor, d.provider_org_id, public_model_id,
                    listing.model_dump() | {"reason": reason}, current.model_dump())
        return listing

    async def fund_dev_wallet(self, provider_org_id, amount, *, operation_id, actor,
                              reason) -> v2.CreditLedgerEntry:
        prior = next((e for e in self.ledger if e.operation_id == operation_id), None)
        if prior is not None:
            return prior
        if amount.is_negative or amount.is_zero:              # grant_credit's refusal
            raise errors.InvalidRequest("an operator allocation is positive")
        wallet = self.wallets.get(provider_org_id) or v2.WalletRef(
            wallet_id=str(uuid.uuid4()), kind=v2.WalletKind.provider_dev,
            owner_provider_org_id=provider_org_id)
        entry = v2.CreditLedgerEntry(
            entry_id=str(uuid.uuid4()), wallet_id=wallet.wallet_id,
            wallet_kind=v2.WalletKind.provider_dev, kind=v2.LedgerEntryKind.operator_allocation,
            amount=amount, operation_id=operation_id, actor=actor, reason=reason,
            created_at=self.clock.now)
        self.wallets[provider_org_id] = wallet.model_copy(update={
            "ledger_total": wallet.ledger_total + amount,
            "revision": wallet.revision + 1})
        self.ledger.append(entry)
        self._event("lab_fund", actor, provider_org_id, wallet.wallet_id,
                    {"entry_id": entry.entry_id, "amount": str(amount)})
        return entry

    async def events(self, provider_org_id: str) -> list[ControlEvent]:
        return [e for e in self.audit if e.provider_org_id == provider_org_id]

    # --- WR-LSQ-9: ControlReads (operations.py) -------------------------------------------
    async def provider_servings(self, provider_org_id: str) -> list[v2.ServingRevision]:
        return [s for s in self.servings.values() if s.provider_org_id == provider_org_id]

    async def provider_deployments(self, provider_org_id: str) -> list[v2.DeploymentRevision]:
        return [d for d in self.deployments.values() if d.provider_org_id == provider_org_id]

    async def endpoint_alias(self, endpoint_id: str) -> str | None:
        for listing in reversed(self._listing_log):
            if self.deployments[listing.deployment_revision_id].endpoint_id == endpoint_id:
                return listing.public_model_id
        return None

    async def listing_versions(self, public_model_id: str) -> list[Listing]:
        return list(self.listings.get(public_model_id, ()))


@dataclasses.dataclass
class FakeEngine:
    """The engine's dev smoke: `passes` decides, `calls` records which revisions ran."""

    passes: bool = True
    calls: list[str] = dataclasses.field(default_factory=list)

    async def smoke(self, serving, deployment) -> bool:
        self.calls.append(deployment.deployment_revision_id)
        return self.passes
