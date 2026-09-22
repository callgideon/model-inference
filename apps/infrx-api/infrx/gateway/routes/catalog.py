"""A requested model name -> what this credential may call, from the trusted catalog (G1R).

The catalog is `contracts.v2.ports.CatalogDirectory`: operator-seeded rows (D1R's
`seed_marlin_provisional.sql` is the F2P fixtures verbatim) answer it before Lab exists.
Every refusal is the contract's own (`pin_admission`, R69/R70), so an unknown, retired,
unpublished or other-endpoint model is one byte-identical `not_found` and an unpriced
one is `invalid_request` - never free inference and never a 403 that confirms a private
artifact exists.

This is validation, not admission. D2's `admit_credit` resolves the name again inside
its own transaction and pins what it finds there; the ingress hands it the name the
caller asked for, never what was resolved here, so an alias moved or a rate published
in between is decided by admission alone (CREDIT-RATE).
"""
from __future__ import annotations

import dataclasses
import logging

from ...contracts import errors
from ...contracts.records import ExecutionMode
from ...contracts.v2 import ports as v2ports
from ...contracts.v2.money_units import CREDIT, Credit
from ...contracts.v2.records import (AdmissionPins, AuthContextV2, CredentialAudience,
                                     DeploymentRevision, DeploymentState, RateCardSnapshot,
                                     ServingRevision, Visibility)

log = logging.getLogger("infrx.gateway")

# What each audience may call. A consumer key: a public deployment that is active - not
# proposed, draining or retired (D1R's `resolve_admission_pins` reads the same rule). A
# provider dev credential: its own private endpoint once validation made it ready
# (07-api-contracts.md: preview credentials are endpoint scoped), and never production,
# which would spend the provider's dev wallet on a consumer listing (D2 refuses it too).
# An operator credential is absent on purpose: it spends no wallet (R66), so it runs no
# inference.
CALLABLE = {CredentialAudience.consumer: (Visibility.public, DeploymentState.active),
            CredentialAudience.provider_dev: (Visibility.private, DeploymentState.ready_private)}
# A request part type -> the input modality it needs (S2M §2.3: text and video only).
MODALITY = {"text": "text", "video_url": "video"}


@dataclasses.dataclass(frozen=True)
class Resolution:
    deployment: DeploymentRevision
    serving: ServingRevision
    pins: AdmissionPins
    rate_card: RateCardSnapshot


def priced_in_credit(card: object) -> bool:
    """R64, a unit is a type: a card whose rates are not `Credit` - a v1 USD snapshot, a
    record assembled with `model_construct` - prices nothing this ingress may admit."""
    return (isinstance(card, RateCardSnapshot) and card.unit == CREDIT
            and isinstance(card.input_rate_per_million, Credit)
            and isinstance(card.output_rate_per_million, Credit))


async def resolve(catalog, auth: AuthContextV2, requested_model: str) -> Resolution:
    """The deployment, serving revision and CREDIT card `requested_model` names for this
    credential now, or the contract's refusal. Reads trusted rows only."""
    callable_ = CALLABLE.get(auth.audience)
    if callable_ is None:
        raise errors.Forbidden("an operator credential does not run inference")
    serving = card = policy = None
    try:
        deployment = await catalog.resolve(requested_model, audience=auth.audience,
                                           endpoint_id=auth.endpoint_id)
        if deployment is not None and (deployment.visibility, deployment.state) != callable_:
            deployment = None                        # the same answer as an unknown model
        if deployment is not None:
            serving = await catalog.serving_revision(deployment.serving_version_id)
            card = await catalog.active_rate_card(deployment.deployment_revision_id)
            policy = await catalog.data_access_policy(deployment.deployment_revision_id)
    except errors.DomainError:
        raise
    except Exception:
        # A lookup that failed is not a model that does not exist: retryable, and the
        # driver's text (a DSN, a host) stays in the log.
        log.exception("model catalog lookup failed")
        raise errors.DependencyUnavailable("the model catalog is unreachable") from None
    pins, card = v2ports.pin_admission(
        auth=auth, requested_model=requested_model, deployment=deployment, serving=serving,
        rate_card=card if priced_in_credit(card) else None, policy=policy)
    return Resolution(deployment=deployment, serving=serving, pins=pins, rate_card=card)


def check_capability(serving: ServingRevision, messages, mode: ExecutionMode) -> None:
    """Only what the serving revision declares (07's capability record). `messages` are
    already R58-validated, so every part has one of `MODALITY`'s types."""
    needed = set()
    for message in messages:
        content = message["content"]
        needed.update(["text"] if isinstance(content, str)
                      else (MODALITY[part["type"]] for part in content))
    capability = serving.capability
    if not needed <= set(capability.input_modalities):
        raise errors.UnsupportedMedia("the model does not accept this input modality",
                                      param="messages")
    if mode is ExecutionMode.stream and not capability.stream_output:
        raise errors.UnsupportedParameter("the model does not stream its output",
                                          param="stream")
