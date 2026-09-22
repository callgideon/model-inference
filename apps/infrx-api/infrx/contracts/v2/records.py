"""The contracts-v2 records, frozen. Additive: nothing in v1 changes.

Every record is a frozen pydantic v2 model with `extra="forbid"` and
`schema_version: int = 2`. `extra="forbid"` is load-bearing here and not only
tidy: it is what makes "a request body can never name a wallet, a price or a
serving revision" a property of the type rather than a check somebody has to
remember (F2P items 3 and 4).

Public OpenAI-style shapes do not change. `NormalizedRequestV2` *contains* the
v1 `NormalizedRequest` rather than restating its fields, so "the public fields
are unchanged" is true by construction and `test_records_v2.py` can assert it.

Money is typed by unit (`money_units`), not by a field name. Timestamps,
UUID strings and the JSON object alias are reused from v1 unchanged.
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (BaseModel, ConfigDict, Field, PlainSerializer,
                      PlainValidator, model_validator)

from .. import money, records
from .money_units import (CREDIT, CREDIT_REGIME, LEGACY_USD_REGIME, PROVIDER_USD,
                          USD, Credit, ProviderUsd, Usd, unit_of)

SCHEMA_VERSION = 2

# `Timestamp`, `UuidStr` and `JsonObject` are the v1 spellings, reused rather than
# redefined: the wire form of a time or an id did not change in this revision.
Timestamp = records.Timestamp
UuidStr = records.UuidStr
JsonObject = records.JsonObject
Usage = records.Usage

Sha256 = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


def _amount(unit_type):
    def validate(value: object):
        return value if isinstance(value, unit_type) else unit_type(value)

    return Annotated[unit_type, PlainValidator(validate),
                     PlainSerializer(str, return_type=str)]


CreditField = _amount(Credit)
UsdField = _amount(Usd)
ProviderUsdField = _amount(ProviderUsd)


class RecordV2(BaseModel):
    """Frozen, closed, and explicitly revision 2."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION

    @model_validator(mode="after")
    def _is_revision_two(self) -> RecordV2:
        # A v1 payload fed to a v2 reader is a *refusal*, never a silent upgrade:
        # `01-contracts.md` — "reject mixed schema/unit payloads explicitly or
        # implement a named compatibility adapter with tests; never guess".
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"contracts v2 records carry schema_version {SCHEMA_VERSION}, "
                             f"not {self.schema_version}; a v1 row is upgraded explicitly "
                             f"(see upgrade_v1_* in this module), never reinterpreted")
        return self


# --- vocabulary (frozen string values) ---------------------------------------
class CredentialAudience(enum.StrEnum):
    """Which product a credential speaks for (`07-api-contracts.md`).

    A consumer key cannot publish a model; a provider control credential cannot
    spend a consumer wallet. The audience is the credential's own property, taken
    from the key row, never from a request header or body.
    """

    consumer = "consumer"
    provider_dev = "provider_dev"
    operator = "operator"


class WalletKind(enum.StrEnum):
    consumer = "consumer"
    provider_dev = "provider_dev"


class AccountingRegime(enum.StrEnum):
    legacy_usd = LEGACY_USD_REGIME
    credit = CREDIT_REGIME


class ProviderRole(enum.StrEnum):
    """`01-architecture.md`: viewer (aggregate health), developer (dev configs and
    evaluations within assigned data access), administrator (members and
    publication proposals). Platform approval of public production changes is
    separate and is not a provider role."""

    viewer = "viewer"
    developer = "developer"
    administrator = "administrator"


class ProviderCapability(enum.StrEnum):
    read_aggregate_health = "read_aggregate_health"
    manage_dev_deployment = "manage_dev_deployment"
    run_evaluation = "run_evaluation"
    propose_publication = "propose_publication"
    manage_members = "manage_members"
    # Deliberately in the vocabulary and in NO role's set: provider ownership
    # alone never yields a customer payload (F2P item 6). It is reachable only
    # through a current `AccessGrant`, checked by `may_read_customer_content`.
    read_customer_content = "read_customer_content"


ROLE_CAPABILITIES: dict[ProviderRole, frozenset[ProviderCapability]] = {
    ProviderRole.viewer: frozenset({ProviderCapability.read_aggregate_health}),
    ProviderRole.developer: frozenset({
        ProviderCapability.read_aggregate_health,
        ProviderCapability.manage_dev_deployment,
        ProviderCapability.run_evaluation,
    }),
    ProviderRole.administrator: frozenset({
        ProviderCapability.read_aggregate_health,
        ProviderCapability.manage_dev_deployment,
        ProviderCapability.run_evaluation,
        ProviderCapability.propose_publication,
        ProviderCapability.manage_members,
    }),
}


class DataCategory(enum.StrEnum):
    request_content = "request_content"
    response_content = "response_content"
    media = "media"
    usage_metadata = "usage_metadata"
    feedback = "feedback"


class DataPurpose(enum.StrEnum):
    """Four separate permissions (`01-architecture.md`). Capture implies none of
    the other three, and a role is never consent."""

    capture = "capture"
    provider_sharing = "provider_sharing"
    external_judging = "external_judging"
    training = "training"


class Environment(enum.StrEnum):
    dev = "dev"
    prod = "prod"


class Visibility(enum.StrEnum):
    private = "private"
    public = "public"


class DeploymentState(enum.StrEnum):
    """`07-api-contracts.md`: draft -> validating -> ready_private -> proposed_public
    -> active -> draining -> retired. A failed validation never becomes public."""

    draft = "draft"
    validating = "validating"
    ready_private = "ready_private"
    proposed_public = "proposed_public"
    active = "active"
    draining = "draining"
    retired = "retired"


PUBLIC_DEPLOYMENT_STATES = frozenset({DeploymentState.proposed_public, DeploymentState.active,
                                      DeploymentState.draining})


class LedgerEntryKind(enum.StrEnum):
    """The closed set of CREDIT ledger movements.

    There is **no transfer kind**, and adding one would be a contract revision:
    a provider dev wallet cannot move balance into a consumer wallet because no
    operation expresses it (`02-credits.md`). Consumer credit is minted only by
    `signup_grant` and by an operator-identified `operator_adjustment`; a
    provider dev wallet is funded only by `operator_allocation`.
    """

    signup_grant = "signup_grant"
    operator_allocation = "operator_allocation"
    operator_adjustment = "operator_adjustment"
    inference_debit = "inference_debit"


# The individual promotional grant, exactly (`02-credits.md`).
INITIAL_SIGNUP_ENTITLEMENT = "initial_signup_grant"
INITIAL_SIGNUP_GRANT = Credit("10000.00000000")

# The only meter this revision accepts. A per-second/per-frame/session meter needs
# its own contract, maximum-reservation rule and tests; until then an unknown
# meter token is a refusal, not an arbitrary billing formula.
METER_TOKENS_V1 = "tokens-v1"


# --- wallets and identity (F2P item 3) ---------------------------------------
class WalletRef(RecordV2):
    """A CREDIT wallet as the server knows it. Never built from a request body.

    Ownership is an exclusive-or fixed by `kind` (`06-database-map.md`,
    `wallets`): a consumer wallet is owned by one **user** and billed through
    that user's personal consumer organization; a provider dev wallet is owned by
    a **provider organization** and has no personal-org binding at all.
    """

    wallet_id: UuidStr
    kind: WalletKind
    unit: Literal["CREDIT"] = CREDIT
    owner_user_id: UuidStr | None = None
    owner_provider_org_id: UuidStr | None = None
    # The protected personal-org billing binding of `01-architecture.md`: which
    # consumer organization this individual's wallet funds. Consumer wallets only.
    personal_org_id: UuidStr | None = None
    ledger_total: CreditField = INITIAL_SIGNUP_GRANT.zero()
    reserved_total: CreditField = INITIAL_SIGNUP_GRANT.zero()
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _ownership_matches_kind(self) -> WalletRef:
        if self.kind is WalletKind.consumer:
            if self.owner_user_id is None or self.personal_org_id is None:
                raise ValueError("a consumer wallet is owned by a user and bound to that "
                                 "user's personal consumer organization")
            if self.owner_provider_org_id is not None:
                raise ValueError("a consumer wallet has no provider owner")
        else:
            if self.owner_provider_org_id is None:
                raise ValueError("a provider_dev wallet is owned by a provider organization")
            if self.owner_user_id is not None or self.personal_org_id is not None:
                raise ValueError("a provider_dev wallet has no individual owner and no "
                                 "personal-org binding")
        if self.reserved_total.is_negative or self.ledger_total.is_negative:
            raise ValueError("wallet totals are nonnegative")
        if self.reserved_total > self.ledger_total:
            raise ValueError("reservations never exceed the ledger total")
        return self

    @property
    def available(self) -> Credit:
        """Total minus active reservations. Never negative, by the validator above."""
        return self.ledger_total - self.reserved_total

    @property
    def has_signup_entitlement(self) -> bool:
        """Only an individual's consumer wallet. A provider dev wallet starts at
        zero and is funded solely by an audited operator allocation."""
        return self.kind is WalletKind.consumer


class AuthContextV2(RecordV2):
    """v1's `AuthContext` plus the audience and the trusted identities behind it.

    It carries **no wallet field**. The wallet is resolved from this context by
    `ports.resolve_wallet`, so there is nowhere for a caller-supplied wallet to
    enter: `extra="forbid"` refuses a payload that tries, and the resolver never
    reads the request.
    """

    audience: CredentialAudience
    org_id: UuidStr
    key_id: UuidStr
    principal: str
    role: records.Role
    entitlement_version: int
    legacy_key: bool = False
    # Consumer credentials: the individual behind the personal consumer org.
    user_id: UuidStr | None = None
    # Provider dev credentials: the provider workspace and the private endpoint
    # the credential is scoped to (`07-api-contracts.md`: Lab-issued preview
    # credentials are environment/endpoint scoped).
    provider_org_id: UuidStr | None = None
    endpoint_id: UuidStr | None = None

    @model_validator(mode="after")
    def _audience_carries_its_own_identity(self) -> AuthContextV2:
        if self.audience is CredentialAudience.consumer:
            if self.user_id is None:
                raise ValueError("a consumer credential resolves an individual user")
            if self.provider_org_id is not None or self.endpoint_id is not None:
                raise ValueError("a consumer credential has no provider or private endpoint "
                                 "scope; it cannot reach a private dev endpoint")
        elif self.audience is CredentialAudience.provider_dev:
            if self.provider_org_id is None or self.endpoint_id is None:
                raise ValueError("a provider_dev credential is scoped to one provider "
                                 "organization and one private endpoint")
        else:
            if self.provider_org_id is not None or self.endpoint_id is not None:
                raise ValueError("an operator credential carries no provider or endpoint scope")
        return self

    @property
    def is_operator(self) -> bool:
        return self.audience is CredentialAudience.operator


# --- registry: serving and deployment revisions (F2P item 4) ------------------
class CapabilityRecord(RecordV2):
    """The minimum capability record of `07-api-contracts.md`.

    Illustrative values are a fixture, never a claim about live Marlin: the real
    context/output/media limits come from the validated runtime contract (S2M).
    """

    api_family: Literal["chat_completions"] = "chat_completions"
    input_modalities: tuple[Literal["text", "video"], ...]
    output_modalities: tuple[Literal["text"], ...]
    stream_output: bool
    stream_input: bool = False
    tools: bool = False
    structured_output: bool = False
    input_schema_ref: str
    output_schema_ref: str
    preprocessing_profile_ref: str
    billing_meter: Literal["tokens-v1"] = METER_TOKENS_V1


class ServingRevision(RecordV2):
    """Everything that changes what the model *does*, pinned by one identifier.

    Optimization creates a new serving revision without pretending the weights
    were retrained: the artifact digests are what distinguish the two cases.
    """

    serving_version_id: UuidStr
    model_id: UuidStr
    model_version_id: UuidStr
    provider_org_id: UuidStr
    weights_digest: Sha256
    adapter_digest: Sha256 | None = None
    tokenizer_digest: Sha256
    prompt_harness_ref: str
    preprocessor_profile_version: str
    runtime_image_digest: Sha256
    engine_options_digest: Sha256
    precision: str
    capability: CapabilityRecord
    created_at: Timestamp


class DeploymentRevision(RecordV2):
    """A stable endpoint name resolved to one immutable serving revision."""

    deployment_revision_id: UuidStr
    endpoint_id: UuidStr
    provider_org_id: UuidStr
    serving_version_id: UuidStr
    environment: Environment
    visibility: Visibility
    state: DeploymentState
    max_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    created_at: Timestamp

    @model_validator(mode="after")
    def _public_means_approved_production(self) -> DeploymentRevision:
        if self.visibility is Visibility.public:
            if self.environment is not Environment.prod:
                raise ValueError("a dev deployment is never public; dev/prod is a deployment "
                                 "property, not a URL convention")
            if self.state not in PUBLIC_DEPLOYMENT_STATES:
                raise ValueError(f"a public deployment is in {sorted(s.value for s in PUBLIC_DEPLOYMENT_STATES)}"
                                 f", not {self.state.value}; a failed validation never becomes public")
        return self


class DataAccessPolicyRef(RecordV2):
    """The policy identity pinned with a job, so a later policy change cannot
    retroactively widen what an already-accepted request allowed."""

    policy_version: str
    consent_version: int
    trace_mode: records.TraceMode
    effective_at: Timestamp


# --- rate cards (F2P items 2 and 4) ------------------------------------------
class RateCardSnapshot(RecordV2):
    """v1's `PriceSnapshot` in CREDIT, pinned to a deployment revision.

    The unit and the meter are literals, not free text: an unpriced, wrong-unit or
    unknown-meter card cannot be constructed, so it cannot reach admission.
    """

    rate_card_version: str
    unit: Literal["CREDIT"] = CREDIT
    meter: Literal["tokens-v1"] = METER_TOKENS_V1
    model_id: UuidStr
    deployment_revision_id: UuidStr
    serving_version_id: UuidStr
    input_rate_per_million: CreditField
    output_rate_per_million: CreditField
    effective_at: Timestamp
    approved_by: str
    status: Literal["approved"] = "approved"
    # The two roundings of `02-credits.md`, named so a reader of a stored card can
    # see which one produced a number without consulting the code.
    hold_rounding: Literal["ceiling_8"] = "ceiling_8"
    debit_rounding: Literal["half_up_8"] = "half_up_8"

    @model_validator(mode="after")
    def _rates_are_nonnegative(self) -> RateCardSnapshot:
        # A negative rate turns a debit into a credit at settlement.
        if self.input_rate_per_million.is_negative or self.output_rate_per_million.is_negative:
            raise ValueError("a CREDIT rate is nonnegative")
        if not self.approved_by.strip():
            raise ValueError("an approved rate card names its approver")
        return self

    def _rates(self) -> tuple[Decimal, Decimal]:
        return (self.input_rate_per_million.raw(CREDIT), self.output_rate_per_million.raw(CREDIT))

    def debit(self, prompt_tokens: int, completion_tokens: int) -> Credit:
        """The final charge: computed once, rounded once, half up (`money.debit`)."""
        return Credit(money.debit(prompt_tokens, completion_tokens, *self._rates()))

    def maximum_hold(self, max_input_tokens: int, max_output_tokens: int) -> Credit:
        """The reservation: rounds up, never down (`money.maximum_hold`)."""
        return Credit(money.maximum_hold(max_input_tokens, max_output_tokens, *self._rates()))


# --- admission (F2P item 4) --------------------------------------------------
class AdmissionPins(RecordV2):
    """What `model -> deployment_revision -> serving_version + rate_card_version +
    policy_version` resolved to at acceptance (`07-api-contracts.md`).

    Persisted with the job before dispatch. A later alias move, promotion,
    rollback or rate publication changes future resolution only; retries and
    idempotent replays of *this* request keep these identities.
    """

    model_id: UuidStr
    requested_model: str                # what the caller asked for: an alias or a pin
    deployment_revision_id: UuidStr
    serving_version_id: UuidStr
    rate_card_version: str
    policy_version: str
    accounting_regime: Literal["credit"] = CREDIT_REGIME


class NormalizedRequestV2(RecordV2):
    """The v1 request, unchanged, plus the internal resolution.

    `request` is the v1 `NormalizedRequest` verbatim — the public OpenAI-style
    fields are not restated here, so they cannot drift — and everything the
    platform resolved for it lives beside it. `wallet_id` is written by admission
    from `ports.resolve_wallet`; there is no path from the request body to it.
    """

    request: records.NormalizedRequest
    pins: AdmissionPins
    wallet_id: UuidStr
    policy: DataAccessPolicyRef

    @model_validator(mode="after")
    def _policy_version_matches_the_pin(self) -> NormalizedRequestV2:
        if self.policy.policy_version != self.pins.policy_version:
            raise ValueError("the pinned policy_version and the attached policy disagree")
        return self


class AdmissionV2(RecordV2):
    """v1's `Admission` in the CREDIT regime, carrying its pins and its rate card."""

    request_id: UuidStr
    job_handle: str
    org_id: UuidStr
    wallet_id: UuidStr
    pins: AdmissionPins
    rate_card: RateCardSnapshot
    maximum_hold: CreditField
    accounting_regime: Literal["credit"] = CREDIT_REGIME
    admitted_at: Timestamp
    replayed: bool = False

    @model_validator(mode="after")
    def _the_card_is_the_pinned_card(self) -> AdmissionV2:
        if self.rate_card.rate_card_version != self.pins.rate_card_version:
            raise ValueError("the attached rate card is not the pinned rate_card_version")
        if self.rate_card.deployment_revision_id != self.pins.deployment_revision_id:
            raise ValueError("the rate card prices a different deployment revision")
        if self.rate_card.serving_version_id != self.pins.serving_version_id:
            raise ValueError("the rate card prices a different serving version")
        if self.maximum_hold.is_negative:
            raise ValueError("a maximum hold is nonnegative")
        return self


class WorkV2(RecordV2):
    """What a lease holder executes, carrying the pins it was admitted with.

    v1's `Work` handed the worker a `PriceSnapshot`; v2 hands it the admitted
    `RateCardSnapshot` and the resolution, so a worker cannot re-resolve an alias
    or re-read a rate while the job is in flight.
    """

    request: NormalizedRequestV2
    media_refs: tuple[records.MediaRef, ...] = ()
    prepared_refs: tuple[records.MediaRef, ...] = ()
    rate_card: RateCardSnapshot
    budgets: records.Budgets

    @model_validator(mode="after")
    def _the_work_carries_its_own_pins(self) -> WorkV2:
        if self.rate_card.rate_card_version != self.request.pins.rate_card_version:
            raise ValueError("the work's rate card is not the request's pinned card")
        if self.rate_card.serving_version_id != self.request.pins.serving_version_id:
            raise ValueError("the work's rate card prices a different serving version")
        return self


class SettlementV2(RecordV2):
    """One terminal settlement, in the unit and at the rates the job was admitted at."""

    request_id: UuidStr
    wallet_id: UuidStr
    usage: Usage
    charged: CreditField
    rate_card_version: str
    serving_version_id: str
    deployment_revision_id: str
    accounting_regime: Literal["credit"] = CREDIT_REGIME
    settled_at: Timestamp

    @model_validator(mode="after")
    def _charge_is_nonnegative(self) -> SettlementV2:
        if self.charged.is_negative:
            raise ValueError("a settlement charge is nonnegative")
        return self


def settle(admission: AdmissionV2, usage: Usage, settled_at: datetime) -> SettlementV2:
    """Settle at the **admitted** card, never at the currently published one.

    This function takes no directory and no clock-dependent rate: that is the
    CREDIT-RATE invariant in code. A rate published while the job waited, an alias
    that moved, a rolled-back deployment — none of them can reach this call.
    """
    if usage.certainty is not records.UsageCertainty.authoritative:
        raise ValueError("only authoritative engine usage settles a debit; unknown usage is "
                         "quarantined and reconciled, never debited later")
    charged = admission.rate_card.debit(usage.prompt_tokens, usage.completion_tokens)
    if charged > admission.maximum_hold:
        # `01-contracts.md`: engine usage beyond the reserved envelope is a platform
        # incident requiring reconciliation, not an unreserved customer debit.
        raise ValueError(f"settlement {charged} exceeds the admitted maximum hold "
                         f"{admission.maximum_hold}; this is a platform incident")
    return SettlementV2(
        request_id=admission.request_id, wallet_id=admission.wallet_id, usage=usage,
        charged=charged, rate_card_version=admission.pins.rate_card_version,
        serving_version_id=admission.pins.serving_version_id,
        deployment_revision_id=admission.pins.deployment_revision_id, settled_at=settled_at)


# --- ledger, grant and legacy USD (F2P item 5) -------------------------------
class CreditLedgerEntry(RecordV2):
    """One append-only CREDIT movement. `operation_id` is the idempotency key."""

    entry_id: UuidStr
    wallet_id: UuidStr
    wallet_kind: WalletKind
    kind: LedgerEntryKind
    amount: CreditField
    unit: Literal["CREDIT"] = CREDIT
    operation_id: UuidStr
    request_id: UuidStr | None = None
    actor: str
    reason: str = ""
    created_at: Timestamp

    @model_validator(mode="after")
    def _kind_fixes_sign_and_wallet(self) -> CreditLedgerEntry:
        if self.kind is LedgerEntryKind.signup_grant:
            if self.wallet_kind is not WalletKind.consumer:
                raise ValueError("only an individual's consumer wallet receives a signup grant; "
                                 "a provider_dev wallet has no signup entitlement")
            if self.amount != INITIAL_SIGNUP_GRANT:
                raise ValueError(f"the initial signup grant is exactly {INITIAL_SIGNUP_GRANT} CREDIT")
        elif self.kind is LedgerEntryKind.operator_allocation:
            if self.wallet_kind is not WalletKind.provider_dev:
                raise ValueError("an operator allocation funds a provider_dev wallet; consumer "
                                 "credit is not minted this way")
            if self.amount.is_negative or self.amount.is_zero:
                raise ValueError("an operator allocation is positive")
        elif self.kind is LedgerEntryKind.inference_debit:
            if not self.amount.is_negative:
                raise ValueError("an inference debit is negative")
            if self.request_id is None:
                raise ValueError("an inference debit names the request it settles")
        if self.kind is not LedgerEntryKind.inference_debit and self.request_id is not None:
            raise ValueError(f"a {self.kind.value} entry is not a request settlement")
        if not self.actor.strip():
            raise ValueError("every ledger entry names the actor that produced it")
        return self


class SignupGrant(RecordV2):
    """The one-time individual promotional grant (`02-credits.md`).

    The uniqueness key is `(user_id, entitlement)` and **nothing else**: campaign
    metadata is audit only, so bumping a campaign version, creating another
    organization, joining a provider workspace or reconnecting an identity cannot
    make the same human eligible again.
    """

    user_id: UuidStr
    entitlement: Literal["initial_signup_grant"] = INITIAL_SIGNUP_ENTITLEMENT
    wallet_id: UuidStr
    amount: CreditField = INITIAL_SIGNUP_GRANT
    verification_evidence_ref: str
    ledger_operation_id: UuidStr
    campaign_version: str = ""          # audit metadata; never part of the key
    granted_at: Timestamp

    @model_validator(mode="after")
    def _exactly_the_promotional_amount(self) -> SignupGrant:
        if self.amount != INITIAL_SIGNUP_GRANT:
            raise ValueError(f"the initial signup grant is exactly {INITIAL_SIGNUP_GRANT} CREDIT")
        if not self.verification_evidence_ref.strip():
            raise ValueError("the grant records the verification evidence it was issued against; "
                             "an unverified user cannot mint credit")
        return self

    @property
    def key(self) -> tuple[str, str]:
        """What the unique index is on. Campaign and organization are not in it."""
        return (self.user_id, self.entitlement)


def issue_signup_grant(*, wallet: WalletRef, user_id: str, verification_evidence_ref: str,
                       ledger_operation_id: str, granted_at: datetime,
                       campaign_version: str = "") -> tuple[SignupGrant, CreditLedgerEntry]:
    """The grant and its ledger entry, or a refusal. One operation, never two halves.

    Refuses a provider dev wallet and a wallet that is not the individual's own,
    which is the CREDIT-IDENTITY property: no membership, organization or
    campaign change can point an individual's grant at another wallet.
    """
    if not wallet.has_signup_entitlement:
        raise ValueError("a provider_dev wallet has no signup entitlement")
    if wallet.owner_user_id != user_id:
        raise ValueError("the initial grant lands in the individual's own wallet only")
    grant = SignupGrant(user_id=user_id, wallet_id=wallet.wallet_id,
                        verification_evidence_ref=verification_evidence_ref,
                        ledger_operation_id=ledger_operation_id, granted_at=granted_at,
                        campaign_version=campaign_version)
    entry = CreditLedgerEntry(
        entry_id=ledger_operation_id, wallet_id=wallet.wallet_id, wallet_kind=wallet.kind,
        kind=LedgerEntryKind.signup_grant, amount=INITIAL_SIGNUP_GRANT,
        operation_id=ledger_operation_id, actor=records.PLATFORM_ACTOR,
        reason=INITIAL_SIGNUP_ENTITLEMENT, created_at=granted_at)
    return (grant, entry)


class LegacyUsdStatement(RecordV2):
    """The historical USD balance, shown separately and labelled as legacy.

    It is a *statement*, not a balance that can be spent, and it is never summed
    with a CREDIT total. `02-credits.md` chooses no conversion rate; an unresolved
    nonzero legacy balance is a rollout hold for that account.
    """

    org_id: UuidStr
    balance: UsdField
    entry_count: int = Field(ge=0)
    as_of: Timestamp
    # True when a product decision is still owed for this account before cutover.
    rollout_hold: bool = False

    @model_validator(mode="after")
    def _a_nonzero_balance_is_a_hold(self) -> LegacyUsdStatement:
        if not self.balance.is_zero and not self.rollout_hold:
            raise ValueError("a nonzero legacy USD balance is a rollout hold for that account "
                             "until a conversion policy exists; it is never silently discarded")
        return self


class ProviderBudget(RecordV2):
    """An external judge/teacher/training budget. Dollars, and never a wallet."""

    budget_id: UuidStr
    provider_org_id: UuidStr
    unit: Literal["PROVIDER_USD"] = PROVIDER_USD
    limit: ProviderUsdField
    reserved: ProviderUsdField
    period: str


# --- usage and balance DTOs (F2P item 1) -------------------------------------
class UsageRecordV2(RecordV2):
    """One settled request, in its own explicit regime.

    The amount crosses JSON as a decimal **string with its unit beside it**; the
    unit is never inferred from the field name or the magnitude. A legacy row
    keeps its `price_version` and its USD amount for ever; a new row carries the
    rate card and serving revision it was admitted at.
    """

    request_id: UuidStr
    org_id: UuidStr
    accounting_regime: AccountingRegime
    unit: Literal["CREDIT", "USD"]
    charged_amount: str
    usage: Usage
    outcome: records.SettlementState
    rate_card_version: str | None = None
    serving_version_id: UuidStr | None = None
    deployment_revision_id: UuidStr | None = None
    price_version: str | None = None    # legacy rows only
    settled_at: Timestamp

    @model_validator(mode="after")
    def _regime_fixes_unit_and_provenance(self) -> UsageRecordV2:
        expected = unit_of(self.accounting_regime.value)
        if self.unit != expected:
            raise ValueError(f"a {self.accounting_regime.value} row is denominated in "
                             f"{expected}, not {self.unit}")
        # Parsing under the declared unit is the trust boundary: an amount that is
        # not an exact numeric(20, 8) decimal string never becomes a DTO. The
        # canonical eight-digit spelling is required too, so the same amount is the
        # same bytes in a fixture, on the wire and in the database.
        from .money_units import parse_amount
        if str(parse_amount(self.charged_amount, self.unit)) != self.charged_amount:
            raise ValueError(f"an amount crosses JSON in its canonical eight-digit form, "
                             f"not {self.charged_amount!r}")
        if self.accounting_regime is AccountingRegime.credit:
            if self.rate_card_version is None or self.serving_version_id is None:
                raise ValueError("a CREDIT row names the rate card and serving revision it "
                                 "was admitted at")
            if self.price_version is not None:
                raise ValueError("price_version is the legacy regime's field")
        else:
            if self.rate_card_version is not None or self.serving_version_id is not None:
                raise ValueError("a legacy_usd row predates rate cards and serving revisions; "
                                 "it is preserved, not back-filled")
        return self

    def amount(self):
        """The typed amount. `Credit` or `Usd` — and the caller cannot get both."""
        from .money_units import parse_amount
        return parse_amount(self.charged_amount, self.unit)


class UsageHistory(RecordV2):
    """A mixed legacy/CREDIT history, kept intelligible by never adding it up.

    `totals()` returns one figure **per unit**. There is no "total spend" here,
    because there is no rate at which the two could be combined (CREDIT-UNITS).
    """

    org_id: UuidStr
    entries: tuple[UsageRecordV2, ...] = ()

    def totals(self) -> dict[str, str]:
        """`{unit: canonical decimal string}`, one entry per unit present."""
        from .money_units import UNIT_TYPES, total
        by_unit: dict[str, list] = {}
        for entry in self.entries:
            by_unit.setdefault(entry.unit, []).append(entry.amount())
        return {unit: str(total(amounts, UNIT_TYPES[unit]))
                for unit, amounts in sorted(by_unit.items())}


class BalanceV2(RecordV2):
    """What the console shows: a CREDIT wallet, plus the legacy statement beside it."""

    wallet_id: UuidStr
    kind: WalletKind
    unit: Literal["CREDIT"] = CREDIT
    ledger_total: CreditField
    reserved_total: CreditField
    available: CreditField
    legacy_usd: LegacyUsdStatement | None = None

    @model_validator(mode="after")
    def _available_is_derived(self) -> BalanceV2:
        if self.available != self.ledger_total - self.reserved_total:
            raise ValueError("available is ledger_total minus reserved_total, not a stored "
                             "counter that can drift")
        if self.available.is_negative:
            raise ValueError("available credit is never negative")
        return self

    @classmethod
    def of(cls, wallet: WalletRef,
           legacy_usd: LegacyUsdStatement | None = None) -> BalanceV2:
        return cls(wallet_id=wallet.wallet_id, kind=wallet.kind,
                   ledger_total=wallet.ledger_total, reserved_total=wallet.reserved_total,
                   available=wallet.available, legacy_usd=legacy_usd)


# --- provider roles and source-purpose grants (F2P item 6) -------------------
class ProviderMembership(RecordV2):
    """Explicit membership. Owning a consumer organization grants none of this."""

    provider_org_id: UuidStr
    user_id: UuidStr
    role: ProviderRole
    granted_by: str
    granted_at: Timestamp
    revoked_at: Timestamp | None = None

    def is_current(self, now: datetime) -> bool:
        return self.granted_at <= now and (self.revoked_at is None or now < self.revoked_at)

    def permits(self, capability: ProviderCapability, now: datetime,
                provider_org_id: str) -> bool:
        """Default deny: unknown capability, wrong provider or revoked -> False."""
        if provider_org_id != self.provider_org_id or not self.is_current(now):
            return False
        return capability in ROLE_CAPABILITIES[self.role]


class AccessGrant(RecordV2):
    """A source owner's grant of their data to one provider, for named purposes.

    Four purposes are four permissions; a grant for `capture` says nothing about
    training. Authorization is checked against the **current** grant at every read,
    export and submission, so revocation blocks new access immediately — a
    snapshot taken at capture time is audit evidence, not a standing permission.
    """

    grant_id: UuidStr
    version: int = Field(ge=1)
    grantor_org_id: UuidStr
    recipient_provider_org_id: UuidStr
    model_ids: tuple[str, ...] = ()     # empty = no model in scope (fail closed)
    categories: tuple[DataCategory, ...] = ()
    purposes: tuple[DataPurpose, ...] = ()
    retention_days: int = Field(ge=1, le=90)
    effective_at: Timestamp
    expires_at: Timestamp | None = None
    revoked_at: Timestamp | None = None

    def is_current(self, now: datetime) -> bool:
        if now < self.effective_at:
            return False
        if self.revoked_at is not None and now >= self.revoked_at:
            return False
        return self.expires_at is None or now < self.expires_at

    def permits(self, *, now: datetime, provider_org_id: str, model_id: str,
                category: DataCategory, purpose: DataPurpose) -> bool:
        """Every dimension must be named. An empty scope permits nothing."""
        return (self.is_current(now)
                and provider_org_id == self.recipient_provider_org_id
                and model_id in self.model_ids
                and category in self.categories
                and purpose in self.purposes)


def may_read_customer_content(*, membership: ProviderMembership | None,
                              grant: AccessGrant | None, now: datetime, provider_org_id: str,
                              model_id: str, category: DataCategory,
                              purpose: DataPurpose) -> bool:
    """Both halves, every time: a current membership **and** a current grant.

    Provider ownership of the model is not one of the halves. Default deny — a
    missing membership or a missing grant answers False rather than falling
    through to "the provider owns it, so it is fine".
    """
    if membership is None or grant is None:
        return False
    if not membership.permits(ProviderCapability.manage_dev_deployment, now, provider_org_id):
        # viewer sees aggregate health only; content needs at least developer.
        return False
    return grant.permits(now=now, provider_org_id=provider_org_id, model_id=model_id,
                         category=category, purpose=purpose)


# --- v1 -> v2 read projection (F2P item 7's honest half) ---------------------
def upgrade_v1_price_snapshot_is_refused(snapshot: records.PriceSnapshot) -> None:
    """There is no upgrade. A v1 USD price snapshot is not a CREDIT rate card.

    Kept as a named function so the refusal is discoverable at the place someone
    would look for a converter, rather than being an absence they work around.
    """
    raise ValueError(f"v1 PriceSnapshot {snapshot.price_version} is denominated in "
                     f"{snapshot.currency}; there is no conversion to CREDIT "
                     f"(research/platforms/02-credits.md chooses no rate). Issue a new "
                     f"approved CREDIT rate card instead.")


def project_v1_usage(row: dict, *, org_id: str) -> UsageRecordV2:
    """Read an old D1 usage row as a v2 DTO **without inventing the new fields**.

    Old rows have no rate card, no serving revision and no unit column: they are
    `legacy_usd`, their `cost_usd` stays exactly the number it was, and the
    fields v2 added read back as `None` rather than as a plausible default. That
    is the whole point of the projection — the upgrade must not make history look
    like it was always v2.
    """
    if "charged_credits" in row or row.get("accounting_regime") == CREDIT_REGIME:
        raise ValueError("this row is already a CREDIT-regime row; project_v1_usage reads "
                         "pre-cutover history only")
    return UsageRecordV2(
        request_id=row["request_id"], org_id=org_id,
        accounting_regime=AccountingRegime.legacy_usd, unit=USD,
        charged_amount=str(Usd(row["cost_usd"])),
        usage=Usage.of(int(row["prompt_tokens"]), int(row["completion_tokens"]),
                       records.UsageCertainty(row.get("usage_certainty", "authoritative"))),
        outcome=records.SettlementState(row.get("settlement_state", "settled")),
        price_version=row.get("price_version"), settled_at=row["settled_at"])
