"""The v2 boundaries: trusted directories, and the two resolutions that read them.

v1's eight ports are unchanged and stay in `contracts/ports.py`. This module adds
only what revision 2 introduced — the trusted lookups behind a credential, a
catalog resolution and a permission check — as Protocols, plus the pure functions
that combine them.

The functions are the contract. `resolve_wallet` and `pin_admission` take an
`AuthContextV2` and a directory and **never a request body**, so there is no
parameter through which a caller could name a wallet, a price, a serving revision
or another organization. That is F2P items 3 and 4 expressed as a signature
rather than as a validation somebody has to remember to call.

These are synchronous because they are pure resolutions over already-fetched
trusted rows; the directories that fetch those rows are async, like every other
port. Errors are the v1 `errors.DomainError` subclasses — no new error code is
introduced by this revision.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .. import errors
from .records import (AccessGrant, AdmissionPins, AuthContextV2, CredentialAudience,
                      DataAccessPolicyRef, DataCategory, DataPurpose, DeploymentRevision,
                      DeploymentState, ProviderMembership, RateCardSnapshot, ServingRevision,
                      Visibility, WalletKind, WalletRef, may_read_customer_content)


@runtime_checkable
class WalletDirectory(Protocol):
    """D. The trusted wallet lookup. Keyed by identity, never by a request field."""

    async def consumer_wallet_for_user(self, user_id: str) -> WalletRef | None:
        """The individual's own CREDIT wallet, with its personal-org binding."""

    async def provider_dev_wallet(self, provider_org_id: str) -> WalletRef | None:
        """The provider workspace's operator-funded dev wallet. Starts at zero."""


@runtime_checkable
class CatalogDirectory(Protocol):
    """D/A3. Resolution of a consumer-visible model name to immutable revisions.

    `resolve` answers what is published **now**; the answer is then pinned. A
    later alias move or rollback changes this lookup, never an admitted job.
    """

    async def resolve(self, requested_model: str, *, audience: CredentialAudience,
                      endpoint_id: str | None) -> DeploymentRevision | None:
        """The deployment revision a caller of this audience may reach, or None."""

    async def serving_revision(self, serving_version_id: str) -> ServingRevision | None: ...

    async def active_rate_card(self, deployment_revision_id: str) -> RateCardSnapshot | None:
        """The approved, active CREDIT card. An unpriced deployment answers None."""

    async def data_access_policy(self, deployment_revision_id: str) -> DataAccessPolicyRef | None: ...


@runtime_checkable
class ProviderDirectory(Protocol):
    """L/C. Provider membership and source-purpose grants, read at use time.

    `current_grant` deliberately has no "as of" parameter: revocation must block
    new access immediately, so a caller cannot ask for the grant as it stood when
    the data was captured and use that as authorization (F2P item 6).
    """

    async def membership(self, provider_org_id: str, user_id: str) -> ProviderMembership | None: ...

    async def current_grant(self, grantor_org_id: str,
                            provider_org_id: str) -> AccessGrant | None: ...


def resolve_wallet(auth: AuthContextV2, wallet: WalletRef | None) -> WalletRef:
    """The wallet this credential spends, checked against the credential's audience.

    The caller fetches the candidate from a `WalletDirectory` keyed by the
    *identity in the auth context*; this function is the part that must not be
    got wrong, and it takes no request at all:

    * a consumer credential spends the wallet owned by its own `user_id`, bound
      to that user's personal consumer organization;
    * a provider dev credential spends its provider organization's dev wallet,
      which has no signup entitlement;
    * an operator credential spends nothing — operator actions are audited grants
      and adjustments, not inference on someone's balance.

    A wallet that does not belong to the credential's identity is `Forbidden`,
    not a silent fallback to "some wallet".
    """
    if auth.audience is CredentialAudience.operator:
        raise errors.Forbidden("an operator credential does not spend a wallet")
    if wallet is None:
        raise errors.NotFound("no wallet is provisioned for this credential")
    if auth.audience is CredentialAudience.consumer:
        if wallet.kind is not WalletKind.consumer:
            raise errors.Forbidden("a consumer credential spends a consumer wallet")
        if wallet.owner_user_id != auth.user_id:
            raise errors.Forbidden("a credential spends only its own user's wallet")
        if wallet.personal_org_id != auth.org_id:
            raise errors.Forbidden("the wallet's personal-org binding does not match the "
                                   "organization this key authenticates")
        return wallet
    if wallet.kind is not WalletKind.provider_dev:
        raise errors.Forbidden("a provider_dev credential spends the provider dev wallet, "
                               "never a consumer wallet")
    if wallet.owner_provider_org_id != auth.provider_org_id:
        raise errors.Forbidden("a provider credential spends only its own provider's wallet")
    return wallet


def pin_admission(*, auth: AuthContextV2, requested_model: str,
                  deployment: DeploymentRevision | None, serving: ServingRevision | None,
                  rate_card: RateCardSnapshot | None,
                  policy: DataAccessPolicyRef | None) -> tuple[AdmissionPins, RateCardSnapshot]:
    """Freeze `model -> deployment -> serving + rate card + policy` for one request.

    Every input is a trusted row the catalog answered with; there is no parameter
    for a price, a serving version or an organization, so a caller-supplied one
    cannot be honoured (R45 extended to v2). Refusals:

    * unknown / unpublished model, or a private dev deployment reached by a
      consumer credential, or a credential reaching an endpoint it is not scoped
      to -> `NotFound` (never a 403 that confirms the artifact exists);
    * a deployment with no approved active CREDIT rate card -> `InvalidRequest`:
      an unpriced model is not free, it is unserveable;
    * a card that prices a different deployment or serving revision -> refused,
      because that pairing would settle at a price nobody approved for it.
    """
    if deployment is None:
        raise errors.NotFound(f"no published deployment for model {requested_model!r}")
    if deployment.state is DeploymentState.retired:
        raise errors.NotFound(f"model {requested_model!r} is retired")
    if deployment.visibility is Visibility.private:
        # A private dev endpoint is reachable only by a provider_dev credential
        # scoped to that very endpoint. A consumer key never is.
        if auth.audience is not CredentialAudience.provider_dev:
            raise errors.NotFound(f"no published deployment for model {requested_model!r}")
        if auth.endpoint_id != deployment.endpoint_id:
            raise errors.NotFound(f"no published deployment for model {requested_model!r}")
        if auth.provider_org_id != deployment.provider_org_id:
            raise errors.NotFound(f"no published deployment for model {requested_model!r}")
    if serving is None or serving.serving_version_id != deployment.serving_version_id:
        raise errors.InvalidRequest("the deployment's serving revision is not resolvable")
    if rate_card is None:
        raise errors.InvalidRequest(f"model {requested_model!r} has no approved CREDIT rate card")
    if (rate_card.deployment_revision_id != deployment.deployment_revision_id
            or rate_card.serving_version_id != deployment.serving_version_id):
        raise errors.InvalidRequest("the active rate card prices a different revision")
    if policy is None:
        raise errors.InvalidRequest("the deployment has no data-access policy version")
    pins = AdmissionPins(model_id=serving.model_id, requested_model=requested_model,
                         deployment_revision_id=deployment.deployment_revision_id,
                         serving_version_id=serving.serving_version_id,
                         rate_card_version=rate_card.rate_card_version,
                         policy_version=policy.policy_version)
    return (pins, rate_card)


def authorize_content_read(*, membership: ProviderMembership | None, grant: AccessGrant | None,
                           now: datetime, provider_org_id: str, model_id: str,
                           category: DataCategory, purpose: DataPurpose) -> None:
    """Raise `Forbidden` unless a current membership **and** a current grant allow it."""
    if not may_read_customer_content(membership=membership, grant=grant, now=now,
                                     provider_org_id=provider_org_id, model_id=model_id,
                                     category=category, purpose=purpose):
        raise errors.Forbidden("no current access grant for this provider, source, category "
                               "and purpose")
