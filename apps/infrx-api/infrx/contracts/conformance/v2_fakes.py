"""In-memory v2 directories, seeded from the committed v2 fixture base.

The reference implementation of `contracts.v2.ports`' three Protocols and nothing
more: dictionaries keyed exactly the way the real relations are keyed
(`research/plan/06a-database-map-v2.md`). D1R/G1R run the same exported cases
against their psycopg adapters; passing here means *implemented*, never integrated.

Deliberately no clock, no storage and no crash simulation: these directories only
answer lookups, so anything else would be a hook a real adapter has to pretend to
have.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

from ..v2 import fixtures as v2fix, ports as v2ports, records as v2

IDS = v2fix.IDS

# The one clock the v2 cases use. After the fixtures' `effective_at`, inside the
# access grant's window, so a case that needs "expired" or "revoked" states it.
NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=v2fix.T0.tzinfo)
LATER = NOW + timedelta(days=90)


@dataclasses.dataclass
class FakeWalletDirectory:
    """`wallets`, keyed by owner. There is no lookup by wallet id on purpose: a
    caller that knows a wallet id still cannot ask for it."""

    by_user: dict[str, v2.WalletRef] = dataclasses.field(default_factory=dict)
    by_provider: dict[str, v2.WalletRef] = dataclasses.field(default_factory=dict)

    async def consumer_wallet_for_user(self, user_id: str) -> v2.WalletRef | None:
        return self.by_user.get(user_id)

    async def provider_dev_wallet(self, provider_org_id: str) -> v2.WalletRef | None:
        return self.by_provider.get(provider_org_id)


@dataclasses.dataclass
class FakeCatalogDirectory:
    """`endpoints`/`deployment_revisions`/`catalog_listings`/`rate_cards`.

    `resolve` applies the visibility rule of `07-api-contracts.md` — a private dev
    deployment is not in the public catalog at all — and `active_rate_card` answers
    the card published *now*, which is what makes the CREDIT-RATE case meaningful.
    """

    aliases: dict[str, str] = dataclasses.field(default_factory=dict)
    deployments: dict[str, v2.DeploymentRevision] = dataclasses.field(default_factory=dict)
    servings: dict[str, v2.ServingRevision] = dataclasses.field(default_factory=dict)
    rate_cards: dict[str, v2.RateCardSnapshot] = dataclasses.field(default_factory=dict)
    policies: dict[str, v2.DataAccessPolicyRef] = dataclasses.field(default_factory=dict)

    async def resolve(self, requested_model: str, *, audience: v2.CredentialAudience,
                      endpoint_id: str | None) -> v2.DeploymentRevision | None:
        deployment = self.deployments.get(self.aliases.get(requested_model, ""))
        if deployment is None:
            return None
        if deployment.visibility is v2.Visibility.private:
            # Not visible to a public catalog reader, and only to the very
            # credential scoped to this endpoint.
            if audience is not v2.CredentialAudience.provider_dev:
                return None
            if endpoint_id != deployment.endpoint_id:
                return None
        return deployment

    async def serving_revision(self, serving_version_id: str) -> v2.ServingRevision | None:
        return self.servings.get(serving_version_id)

    async def active_rate_card(self, deployment_revision_id: str) -> v2.RateCardSnapshot | None:
        return self.rate_cards.get(deployment_revision_id)

    async def data_access_policy(self,
                                 deployment_revision_id: str) -> v2.DataAccessPolicyRef | None:
        return self.policies.get(deployment_revision_id)

    def publish(self, card: v2.RateCardSnapshot) -> None:
        """An operator publishing a new approved card. Future admissions only."""
        self.rate_cards[card.deployment_revision_id] = card

    def move_alias(self, requested_model: str, deployment_revision_id: str) -> None:
        self.aliases[requested_model] = deployment_revision_id


@dataclasses.dataclass
class FakeProviderDirectory:
    """`provider_memberships` and `data_access_grants`, read at use time."""

    memberships: dict[tuple[str, str], v2.ProviderMembership] = dataclasses.field(
        default_factory=dict)
    grants: dict[tuple[str, str], v2.AccessGrant] = dataclasses.field(default_factory=dict)

    async def membership(self, provider_org_id: str,
                         user_id: str) -> v2.ProviderMembership | None:
        return self.memberships.get((provider_org_id, user_id))

    async def current_grant(self, grantor_org_id: str,
                            provider_org_id: str) -> v2.AccessGrant | None:
        return self.grants.get((grantor_org_id, provider_org_id))

    def revoke(self, grantor_org_id: str, provider_org_id: str, at: datetime) -> None:
        key = (grantor_org_id, provider_org_id)
        grant = self.grants[key]
        self.grants[key] = grant.model_copy(update={"revoked_at": at,
                                                    "version": grant.version + 1})


@dataclasses.dataclass(frozen=True)
class V2Harness:
    """What a v2 conformance case is handed. An adapter supplies the same shape."""

    wallets: v2ports.WalletDirectory
    catalog: v2ports.CatalogDirectory
    providers: v2ports.ProviderDirectory
    now: datetime


def fake_v2_harness() -> V2Harness:
    """A fresh harness seeded from the committed fixtures. Never shared between cases."""
    built = {name: builder() for name, builder in v2fix.BUILDERS.items()}
    prod = built["deployment_revision_public.json"]
    dev = built["deployment_revision_private_dev.json"]
    serving = built["serving_revision.json"]
    card = built["rate_card_marlin.json"]
    policy = built["data_access_policy.json"]
    wallets = FakeWalletDirectory(
        by_user={IDS.consumer_user: built["wallet_consumer.json"]},
        by_provider={IDS.provider_org: built["wallet_provider_dev.json"]})
    catalog = FakeCatalogDirectory(
        aliases={v2fix.REQUESTED_MODEL: prod.deployment_revision_id,
                 v2fix.DEV_REQUESTED_MODEL: dev.deployment_revision_id},
        deployments={prod.deployment_revision_id: prod, dev.deployment_revision_id: dev},
        servings={serving.serving_version_id: serving},
        # The dev deployment is deliberately *unpriced*: an operator-funded preview
        # still needs an approved internal card, and the fixture proves the refusal.
        rate_cards={prod.deployment_revision_id: card},
        policies={prod.deployment_revision_id: policy, dev.deployment_revision_id: policy})
    membership = built["provider_membership.json"]
    grant = built["access_grant.json"]
    providers = FakeProviderDirectory(
        memberships={(membership.provider_org_id, membership.user_id): membership},
        grants={(grant.grantor_org_id, grant.recipient_provider_org_id): grant})
    return V2Harness(wallets=wallets, catalog=catalog, providers=providers, now=NOW)
