"""The serialized contracts-v2 base: the bytes every track and both languages agree on.

Each file under `../fixtures/v2/` is canonical JSON (`codec.canonical_bytes`):
sorted keys, two-space indent, trailing newline, no nulls. The files are
**generated from `build()`**, never hand-edited, and
`tests/contracts/v2/test_fixtures_v2.py` regenerates them in memory and compares
bytes — so a fixture cannot drift away from the record that defines it, and a
record change that nobody reflected in the fixtures fails the suite.

    uv run --frozen python -m infrx.contracts.v2.fixtures --write

`map.json` is the machine-readable v1 -> v2 field map (F2P item 1); the prose
appendix is `research/plan/01a-contracts-v2-map.md`. It is checked against the
live `model_fields` of both revisions, so it cannot describe a field that does
not exist or omit one that does.

`money_unit_cases.json` is the cross-language parity table: the console half must
accept and reject exactly the same amounts under exactly the same units.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from . import SURFACE_VERSION
from .. import codec, records as v1
from . import records as v2
from . import lifecycle as lc
from . import money_units as mu
from .money_units import CREDIT, PROVIDER_USD, USD

DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "v2"


# --- the fixture identities -------------------------------------------------
# One readable scheme so a case, a fake and a migration test all name the same
# rows: `a…` consumer, `b…` provider, `c…` endpoint/deployment, `d…` registry,
# `e…` grants and ledger operations, `f…` requests.
class IDS:
    consumer_user = "a0000001-0000-4000-8000-000000000001"
    consumer_org = "a0000002-0000-4000-8000-000000000002"
    consumer_key = "a0000003-0000-4000-8000-000000000003"
    consumer_wallet = "a0000004-0000-4000-8000-000000000004"
    other_user = "a0000005-0000-4000-8000-000000000005"
    other_org = "a0000006-0000-4000-8000-000000000006"
    other_wallet = "a0000007-0000-4000-8000-000000000007"

    provider_org = "b0000001-0000-4000-8000-000000000001"
    rival_provider_org = "b0000002-0000-4000-8000-000000000002"
    provider_dev_wallet = "b0000003-0000-4000-8000-000000000003"
    provider_dev_key = "b0000004-0000-4000-8000-000000000004"
    provider_member = "b0000005-0000-4000-8000-000000000005"

    dev_endpoint = "c0000001-0000-4000-8000-000000000001"
    prod_endpoint = "c0000002-0000-4000-8000-000000000002"
    dev_deployment = "c0000003-0000-4000-8000-000000000003"
    prod_deployment = "c0000004-0000-4000-8000-000000000004"

    model = "d0000001-0000-4000-8000-000000000001"
    model_version = "d0000002-0000-4000-8000-000000000002"
    serving_version = "d0000003-0000-4000-8000-000000000003"

    access_grant = "e0000001-0000-4000-8000-000000000001"
    signup_operation = "e0000002-0000-4000-8000-000000000002"
    provider_budget = "e0000003-0000-4000-8000-000000000003"

    request = "f0000001-0000-4000-8000-000000000001"
    legacy_request = "f0000002-0000-4000-8000-000000000002"


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)
# --- the Marlin artifact identity (S2M, research/workloads/marlin-sop.md) -----
# Real measured values, not placeholders: the served-bytes sha256 of each weight
# shard and of `tokenizer.json`, hashed read-only on the pilot box 2026-09-22
# (§1.3), plus the chat template's sha256 (§1.3, needs no gate) and the HF commit
# (§2.6). `digest_source` records that these are SERVED BYTES: the repository is
# gated, so the registry `.lfs.oid` equality is still ⚠️ and W3/I2B confirms it on
# the serving host. The runtime image and the engine options are W3's measured pins
# (`models/marlin2b/serving-version.json`: `runtime_image.ref`, `engine_options_digest`;
# `tests/g/ops/test_publication.py` holds them equal to that file). `runtime_image_digest`
# stays unset on this revision: recording it is a new serving version's (R76).
MODEL_REPO = "NemoStation/Marlin-2B"
MODEL_COMMIT = "fd111fca4fc7897876fb0d7e9df22ca5ac8ab965"
SHARD_DIGESTS = (
    "sha256:5d78fa4dbd856dc89c01b99ffa92072fe31b8a1e6b31e87893734c80304983b7",
    "sha256:01d40ec9ccf4c2ad8e755604468dd6ee4a5c6551553e5739a03beb4c0673d0db",
)
TOKENIZER_DIGEST = "sha256:06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523"
CHAT_TEMPLATE_DIGEST = \
    "sha256:273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80"
RUNTIME_IMAGE_REF = \
    "vllm/vllm-openai@sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42"
# The validated engine options of the profile: sha256 of serve.sh's flags as W3 launches
# them (serving-version.json `engine_options_digest`).
ENGINE_OPTIONS_DIGEST = \
    "sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147"
PAYLOAD_DIGEST = "sha256:" + "55" * 32

# r1 R62: the consumer-facing model identifier keeps its v1 form
# `<public_model_id>@<revision>` — byte-identical to `fixtures/v1/*.json`. The
# artifact is pinned by the serving revision, not by this string.
PUBLIC_MODEL_ID = "nemostation/marlin-2b"
REVISION_LABEL = "2026-09-01"
REQUESTED_MODEL = f"{PUBLIC_MODEL_ID}@{REVISION_LABEL}"
DEV_REQUESTED_MODEL = f"{PUBLIC_MODEL_ID}-dev@{REVISION_LABEL}"
RATE_CARD_VERSION = "rc_marlin2b_2026_09_provisional"
POLICY_VERSION = "dap_2026_09_01"

# --- the provisional Marlin rate (P-01 pending) ------------------------------
# NOT a price. `research/plan/15-pending-inputs.md` P-01 is the operator-approved
# rate, and it is not decided. This fixture exists so every track can be built and
# tested against a *shaped* card, and it is labelled as provisional in the record
# itself (`approved_by`) so a run that used it is identifiable afterwards.
#
# How it was picked, so a reviewer can argue with the arithmetic rather than the
# number (`research/plan/evidence/f/F2P-*.md` repeats this):
#
#   1. Cost floor. The only measured-cost input we have for this model is
#      `research/models/marlin2b/README.md`'s comparison table, whose B300
#      interactive column is $0.0278-0.0565 per 1M output tokens, derived from
#      `research/cross-cutting/cloud-pricing.md`'s p6-b300 on-demand
#      $17.802/GPU-hr. Credits are not dollars, so this fixes no exchange rate; it
#      only says the grant must not be priced below cost by orders of magnitude.
#   2. Shape. The 1:3 input:output ratio is carried over unchanged from the v1
#      USD fixture (`fixtures/v1/price_snapshot.json`: 0.20 / 0.60), so this
#      fixture is a *unit* change, not a silent repricing.
#   3. Scale. The grant is sized against the S2M video operating point: a
#      two-minute clip is ~23,500 input tokens (`models/marlin2b/tokens.py`), with
#      512 output tokens. At 400 / 1,200 CREDIT per million that is
#      23,500x400/1e6 + 512x1,200/1e6 = 9.40 + 0.6144 = 10.0144 CREDIT, so the
#      10,000 CREDIT grant buys ~998 such requests — a defensible free tier, and
#      emphatically not "10,000 requests" (`02-credits.md`).
INPUT_RATE_PER_MILLION = "400.00000000"
OUTPUT_RATE_PER_MILLION = "1200.00000000"
RATE_CARD_APPROVER = "provisional - P-01 pending"


def _capability() -> v2.CapabilityRecord:
    """S2M §2.6's actual values, not 07-api-contracts.md's illustrative ones."""
    return v2.CapabilityRecord(
        input_modalities=("text", "video"), output_modalities=("text",), stream_output=True,
        input_schema_ref="infrx.request.chat.v1", output_schema_ref="infrx.response.chat.v1",
        preprocessing_profile_ref="marlin2b.video.v1")


def _serving() -> v2.ServingRevision:
    return v2.ServingRevision(
        serving_version_id=IDS.serving_version, model_id=IDS.model,
        model_version_id=IDS.model_version, provider_org_id=IDS.provider_org,
        public_model_id=PUBLIC_MODEL_ID, revision_label=REVISION_LABEL,
        model_repo=MODEL_REPO, model_commit=MODEL_COMMIT,
        weight_shard_digests=SHARD_DIGESTS, tokenizer_digest=TOKENIZER_DIGEST,
        chat_template_digest=CHAT_TEMPLATE_DIGEST,
        digest_source=v2.DigestSource.served_bytes, prompt_harness_ref="marlin2b.chat.v1",
        preprocessor_profile_version="marlin2b.video.v1",
        runtime_image_ref=RUNTIME_IMAGE_REF, engine_options_digest=ENGINE_OPTIONS_DIGEST,
        precision="bfloat16", capability=_capability(), created_at=T0)


def _prod_deployment() -> v2.DeploymentRevision:
    return v2.DeploymentRevision(
        deployment_revision_id=IDS.prod_deployment, endpoint_id=IDS.prod_endpoint,
        provider_org_id=IDS.provider_org, serving_version_id=IDS.serving_version,
        environment=v2.Environment.prod, visibility=v2.Visibility.public,
        state=v2.DeploymentState.active, max_input_tokens=30720, max_output_tokens=2048,
        created_at=T0)


def _dev_deployment() -> v2.DeploymentRevision:
    return v2.DeploymentRevision(
        deployment_revision_id=IDS.dev_deployment, endpoint_id=IDS.dev_endpoint,
        provider_org_id=IDS.provider_org, serving_version_id=IDS.serving_version,
        environment=v2.Environment.dev, visibility=v2.Visibility.private,
        state=v2.DeploymentState.ready_private, max_input_tokens=30720,
        max_output_tokens=2048, created_at=T0)


def _rate_card() -> v2.RateCardSnapshot:
    return v2.RateCardSnapshot(
        rate_card_version=RATE_CARD_VERSION, model_id=IDS.model,
        deployment_revision_id=IDS.prod_deployment, serving_version_id=IDS.serving_version,
        input_rate_per_million=INPUT_RATE_PER_MILLION,
        output_rate_per_million=OUTPUT_RATE_PER_MILLION, effective_at=T0,
        approved_by=RATE_CARD_APPROVER)


def _policy() -> v2.DataAccessPolicyRef:
    return v2.DataAccessPolicyRef(policy_version=POLICY_VERSION, consent_version=1,
                                  trace_mode=v1.TraceMode.off, effective_at=T0)


def _consumer_wallet() -> v2.WalletRef:
    return v2.WalletRef(wallet_id=IDS.consumer_wallet, kind=v2.WalletKind.consumer,
                        owner_user_id=IDS.consumer_user, personal_org_id=IDS.consumer_org,
                        ledger_total="10000.00000000", reserved_total="10.01440000")


def _provider_dev_wallet() -> v2.WalletRef:
    """Zero-initialised, as `02-credits.md` requires: no signup grant, ever."""
    return v2.WalletRef(wallet_id=IDS.provider_dev_wallet, kind=v2.WalletKind.provider_dev,
                        owner_provider_org_id=IDS.provider_org)


def _consumer_auth() -> v2.AuthContextV2:
    return v2.AuthContextV2(
        audience=v2.CredentialAudience.consumer, org_id=IDS.consumer_org,
        key_id=IDS.consumer_key, principal=IDS.consumer_key, role=v1.Role.owner,
        entitlement_version=1, user_id=IDS.consumer_user)


def _provider_dev_auth() -> v2.AuthContextV2:
    return v2.AuthContextV2(
        audience=v2.CredentialAudience.provider_dev, org_id=IDS.provider_org,
        key_id=IDS.provider_dev_key, principal=IDS.provider_dev_key, role=v1.Role.member,
        entitlement_version=1, user_id=IDS.provider_member, provider_org_id=IDS.provider_org,
        endpoint_id=IDS.dev_endpoint)


def _pins() -> v2.AdmissionPins:
    return v2.AdmissionPins(
        model_id=IDS.model, requested_model=REQUESTED_MODEL,
        deployment_revision_id=IDS.prod_deployment, serving_version_id=IDS.serving_version,
        rate_card_version=RATE_CARD_VERSION, policy_version=POLICY_VERSION)


def _v1_request() -> v1.NormalizedRequest:
    """The v1 public shape, byte-for-byte what v1 already froze."""
    return v1.NormalizedRequest(
        request_id=IDS.request, org_id=IDS.consumer_org, key_id=IDS.consumer_key,
        model_revision=REQUESTED_MODEL, messages=({"role": "user", "content": "hello"},),
        payload_ref="payloads/2026/09/22/request", payload_digest=PAYLOAD_DIGEST,
        execution_mode=v1.ExecutionMode.sync, max_input_tokens=23500, max_output_tokens=512,
        created_at=T1, deadline_at=datetime(2026, 9, 22, 12, 5, tzinfo=timezone.utc),
        trace_policy=v1.ConsentSnapshot(
            org_id=IDS.consumer_org, consent_version=1, trace_mode=v1.TraceMode.off,
            content_retention_days=30, evaluation_consent=False, effective_at=T0))


def _request() -> v2.NormalizedRequestV2:
    return v2.NormalizedRequestV2(request=_v1_request(), pins=_pins(),
                                  wallet_id=IDS.consumer_wallet, policy=_policy())


def _admission() -> v2.AdmissionV2:
    card = _rate_card()
    return v2.AdmissionV2(
        request_id=IDS.request, job_handle="job_fixture0admission0handle00000000001",
        org_id=IDS.consumer_org, wallet_id=IDS.consumer_wallet, pins=_pins(), rate_card=card,
        maximum_hold=card.maximum_hold(23500, 512), admitted_at=T1)


def _settlement() -> v2.SettlementV2:
    return v2.settle(_admission(), v1.Usage.of(23500, 480),
                     datetime(2026, 9, 22, 12, 0, 30, tzinfo=timezone.utc))


def _signup_grant() -> v2.SignupGrant:
    grant, _entry = v2.issue_signup_grant(
        wallet=_consumer_wallet(), user_id=IDS.consumer_user,
        verification_evidence_ref="email_verification/2026-09-22/a0000001",
        ledger_operation_id=IDS.signup_operation, granted_at=T1,
        campaign_version="launch_2026_09")
    return grant


def _signup_ledger_entry() -> v2.CreditLedgerEntry:
    _grant, entry = v2.issue_signup_grant(
        wallet=_consumer_wallet(), user_id=IDS.consumer_user,
        verification_evidence_ref="email_verification/2026-09-22/a0000001",
        ledger_operation_id=IDS.signup_operation, granted_at=T1,
        campaign_version="launch_2026_09")
    return entry


def _legacy_statement() -> v2.LegacyUsdStatement:
    """A real pre-cutover balance: nonzero, and therefore a rollout hold."""
    return v2.LegacyUsdStatement(org_id=IDS.consumer_org, balance="4.21500000",
                                 entry_count=37, as_of=T1, rollout_hold=True)


def _usage_credit() -> v2.UsageRecordV2:
    settlement = _settlement()
    return v2.UsageRecordV2(
        request_id=IDS.request, org_id=IDS.consumer_org,
        accounting_regime=v2.AccountingRegime.credit, unit=CREDIT,
        charged_amount=str(settlement.charged), usage=settlement.usage,
        outcome=v1.SettlementState.settled, rate_card_version=RATE_CARD_VERSION,
        serving_version_id=IDS.serving_version, deployment_revision_id=IDS.prod_deployment,
        settled_at=settlement.settled_at)


def _usage_legacy() -> v2.UsageRecordV2:
    """An old D1 row read through the v2 projection: no rate card, no serving id."""
    return v2.project_v1_usage({
        "request_id": IDS.legacy_request, "cost_usd": "0.01414000",
        "prompt_tokens": 23500, "completion_tokens": 480,
        "price_version": "pv_2026_09_01", "settled_at": T0,
    }, org_id=IDS.consumer_org)


def _usage_history() -> v2.UsageHistory:
    return v2.UsageHistory(org_id=IDS.consumer_org, entries=(_usage_legacy(), _usage_credit()))


def _balance() -> v2.BalanceV2:
    return v2.BalanceV2.of(_consumer_wallet(), legacy_usd=_legacy_statement())


def _membership() -> v2.ProviderMembership:
    return v2.ProviderMembership(provider_org_id=IDS.provider_org, user_id=IDS.provider_member,
                                 role=v2.ProviderRole.developer, granted_by="platform",
                                 granted_at=T0)


def _access_grant() -> v2.AccessGrant:
    return v2.AccessGrant(
        grant_id=IDS.access_grant, version=1, grantor_org_id=IDS.consumer_org,
        recipient_provider_org_id=IDS.provider_org, model_ids=(IDS.model,),
        categories=(v2.DataCategory.request_content, v2.DataCategory.response_content),
        purposes=(v2.DataPurpose.provider_sharing,), retention_days=30, effective_at=T0,
        expires_at=datetime(2026, 12, 1, tzinfo=timezone.utc))


def _provider_budget() -> v2.ProviderBudget:
    return v2.ProviderBudget(budget_id=IDS.provider_budget, provider_org_id=IDS.provider_org,
                             limit="500.00000000", reserved="12.50000000", period="2026-09")


def _work() -> v2.WorkV2:
    return v2.WorkV2(request=_request(), rate_card=_rate_card(),
                     budgets=v1.Budgets(preparation_s=120.0, queue_wait_s=10.0,
                                        generation_s=300.0, first_token_s=60.0, stall_s=20.0))


BUILDERS: dict[str, Any] = {
    "access_grant.json": _access_grant,
    "admission.json": _admission,
    "admission_pins.json": _pins,
    "auth_context_consumer.json": _consumer_auth,
    "auth_context_provider_dev.json": _provider_dev_auth,
    "balance.json": _balance,
    "capability.json": _capability,
    "credit_ledger_signup.json": _signup_ledger_entry,
    "data_access_policy.json": _policy,
    "deployment_revision_private_dev.json": _dev_deployment,
    "deployment_revision_public.json": _prod_deployment,
    "legacy_usd_statement.json": _legacy_statement,
    "normalized_request.json": _request,
    "provider_budget.json": _provider_budget,
    "provider_membership.json": _membership,
    "rate_card_marlin.json": _rate_card,
    "serving_revision.json": _serving,
    "settlement.json": _settlement,
    "signup_grant.json": _signup_grant,
    "usage_credit.json": _usage_credit,
    "usage_history_mixed.json": _usage_history,
    "usage_legacy_usd.json": _usage_legacy,
    "wallet_consumer.json": _consumer_wallet,
    "wallet_provider_dev.json": _provider_dev_wallet,
    "work.json": _work,
}

MODELS: dict[str, type[BaseModel]] = {
    "access_grant.json": v2.AccessGrant,
    "admission.json": v2.AdmissionV2,
    "admission_pins.json": v2.AdmissionPins,
    "auth_context_consumer.json": v2.AuthContextV2,
    "auth_context_provider_dev.json": v2.AuthContextV2,
    "balance.json": v2.BalanceV2,
    "capability.json": v2.CapabilityRecord,
    "credit_ledger_signup.json": v2.CreditLedgerEntry,
    "data_access_policy.json": v2.DataAccessPolicyRef,
    "deployment_revision_private_dev.json": v2.DeploymentRevision,
    "deployment_revision_public.json": v2.DeploymentRevision,
    "legacy_usd_statement.json": v2.LegacyUsdStatement,
    "normalized_request.json": v2.NormalizedRequestV2,
    "provider_budget.json": v2.ProviderBudget,
    "provider_membership.json": v2.ProviderMembership,
    "rate_card_marlin.json": v2.RateCardSnapshot,
    "serving_revision.json": v2.ServingRevision,
    "settlement.json": v2.SettlementV2,
    "signup_grant.json": v2.SignupGrant,
    "usage_credit.json": v2.UsageRecordV2,
    "usage_history_mixed.json": v2.UsageHistory,
    "usage_legacy_usd.json": v2.UsageRecordV2,
    "wallet_consumer.json": v2.WalletRef,
    "wallet_provider_dev.json": v2.WalletRef,
    "work.json": v2.WorkV2,
}

# Tables rather than single records; checked by their own tests.
TABLES = ("map.json", "money_unit_cases.json")


# --- F2C.a lifecycle (`lifecycle.py`) ----------------------------------------------------
# `7...` ids are lifecycle content rows. The upload window below is today's MediaUploads
# window (PROCESSING_CACHE_TTL_S, 7 days) as an example only; the store configures its own.
class LIFECYCLE_IDS:
    upload_handle = "upl_fixture0lifecycle0upload0000000001"
    source_content = "70000001-0000-4000-8000-000000000001"
    payload_content = "70000002-0000-4000-8000-000000000002"


UPLOAD_DIGEST = "sha256:" + "a1" * 32
UPLOAD_BYTES = 4_194_304
T_UPLOAD_PUT = datetime(2026, 9, 22, 12, 0, 5, tzinfo=timezone.utc)
T_UPLOAD_DONE = datetime(2026, 9, 22, 12, 0, 7, tzinfo=timezone.utc)
T_UPLOAD_END = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


def _upload_created() -> lc.UploadTicket:
    handle = LIFECYCLE_IDS.upload_handle
    return lc.UploadTicket(
        upload_handle=handle, org_id=IDS.consumer_org, destination_ref=lc.DESTINATION_SCHEME + handle,
        constraints=lc.UploadConstraints(max_bytes=67_108_864, bytes=UPLOAD_BYTES,
                                         accepted_mime=("video/mp4", "video/webm"),
                                         digest=UPLOAD_DIGEST),
        state=v1.UploadState.created, created_at=T1, expires_at=T_UPLOAD_END)


def _upload_finalized() -> lc.UploadTicket:
    return lc.UploadTicket.model_validate({
        **_upload_created().model_dump(), "state": v1.UploadState.finalized,
        "received": lc.UploadReceipt(bytes=UPLOAD_BYTES, digest=UPLOAD_DIGEST,
                                     received_at=T_UPLOAD_PUT),
        "finalized": lc.FinalizedSource(
            content_id=LIFECYCLE_IDS.source_content, generation=1, digest=UPLOAD_DIGEST,
            bytes=UPLOAD_BYTES, mime="video/mp4", profile_version="v1", duration_s=12.5,
            finalized_at=T_UPLOAD_DONE)})


def _upload_source_ref() -> v1.MediaRef:
    return v1.MediaRef(org_id=IDS.consumer_org, handle=LIFECYCLE_IDS.upload_handle,
                       kind=v1.MediaKind.upload, digest=UPLOAD_DIGEST, bytes=UPLOAD_BYTES,
                       mime="video/mp4", duration_s=12.5,
                       storage_ref=f"media/{IDS.consumer_org}/v1/{'a1' * 8}/source")


def _readiness_text_only() -> lc.ExecutionReadiness:
    """RV-05: the COMPLETED empty manifest - `sources: []` present, never omitted."""
    return lc.ExecutionReadiness(job_id=IDS.request, org_id=IDS.consumer_org, sources=(),
                                 ready_at=T1)


def _readiness_media() -> lc.ExecutionReadiness:
    return lc.ExecutionReadiness(job_id=IDS.request, org_id=IDS.consumer_org, ready_at=T1,
                                 sources=(lc.ManifestSource(
                                     content_id=LIFECYCLE_IDS.source_content, generation=1,
                                     ref=_upload_source_ref()),))


def _source_identity() -> lc.ContentIdentity:
    ref = _upload_source_ref()
    return lc.ContentIdentity(org_id=ref.org_id, kind=lc.ContentKind.source,
                              location=lc.ContentLocation.object_store,
                              object_key=ref.storage_ref, digest=ref.digest, bytes=ref.bytes,
                              origin=lc.ContentOrigin.written)


def _deletion_claim() -> lc.DeletionClaim:
    return lc.DeletionClaim(content_id=LIFECYCLE_IDS.source_content, generation=1, fence=2,
                            holder="collector-a", claimed_at=T_UPLOAD_END,
                            expires_at=datetime(2026, 9, 29, 12, 1, tzinfo=timezone.utc))


def _content_live() -> lc.ContentObject:
    return lc.ContentObject(content_id=LIFECYCLE_IDS.source_content, generation=1,
                            identity=_source_identity(), state=lc.LifecycleState.live,
                            registered_at=T_UPLOAD_PUT,
                            eligible_at=datetime(2026, 9, 29, 12, 0, 5, tzinfo=timezone.utc))


def _content_tombstoned() -> lc.ContentObject:
    return lc.ContentObject.model_validate({
        **_content_live().model_dump(), "state": lc.LifecycleState.tombstoned,
        "claim": _deletion_claim(), "tombstoned_at": T_UPLOAD_END})


def _tombstone() -> lc.Tombstone:
    identity = _source_identity()
    return lc.Tombstone(content_id=LIFECYCLE_IDS.source_content, generation=1, fence=2,
                        location=identity.location, object_key=identity.object_key,
                        tombstoned_at=T_UPLOAD_END)


def _content_reference() -> lc.ContentReference:
    return lc.ContentReference(content_id=LIFECYCLE_IDS.source_content, generation=1,
                               job_id=IDS.request, org_id=IDS.consumer_org, referenced_at=T1,
                               retain_until=datetime(2026, 9, 29, 12, 0, 30, tzinfo=timezone.utc))


LIFECYCLE_BUILDERS: dict[str, Any] = {
    "lifecycle_content_live.json": _content_live,
    "lifecycle_content_reference.json": _content_reference,
    "lifecycle_content_tombstoned.json": _content_tombstoned,
    "lifecycle_deletion_claim.json": _deletion_claim,
    "lifecycle_readiness_media.json": _readiness_media,
    "lifecycle_readiness_text_only.json": _readiness_text_only,
    "lifecycle_readiness_view_not_ready.json":
        lambda: lc.readiness_view(IDS.legacy_request, None),
    "lifecycle_readiness_view_ready_empty.json": lambda: _readiness_text_only().view(),
    "lifecycle_tombstone.json": _tombstone,
    "lifecycle_upload_created.json": _upload_created,
    "lifecycle_upload_finalized.json": _upload_finalized,
}
LIFECYCLE_MODELS: dict[str, type[BaseModel]] = {
    "lifecycle_content_live.json": lc.ContentObject,
    "lifecycle_content_reference.json": lc.ContentReference,
    "lifecycle_content_tombstoned.json": lc.ContentObject,
    "lifecycle_deletion_claim.json": lc.DeletionClaim,
    "lifecycle_readiness_media.json": lc.ExecutionReadiness,
    "lifecycle_readiness_text_only.json": lc.ExecutionReadiness,
    "lifecycle_readiness_view_not_ready.json": lc.ReadinessView,
    "lifecycle_readiness_view_ready_empty.json": lc.ReadinessView,
    "lifecycle_tombstone.json": lc.Tombstone,
    "lifecycle_upload_created.json": lc.UploadTicket,
    "lifecycle_upload_finalized.json": lc.UploadTicket,
}
BUILDERS.update(LIFECYCLE_BUILDERS)
MODELS.update(LIFECYCLE_MODELS)
TABLES = (*TABLES, "lifecycle_refusals.json", "result_read_cases.json")


# --- the v1 -> v2 field map (F2P item 1) -------------------------------------
# `v1` names the v1 record this one revises (null for a record v2 introduces).
# `same_name` are field NAMES v2 keeps from v1 (a kept name may carry a revised
# type — `note` says so where it does), `added` are new names and `dropped` are v1
# names this record does not carry. `schema_version` is excluded from all three
# and stated once in the header: every v2 record carries 2.
# `test_fixtures_v2.py` checks each list against the live `model_fields` of both
# revisions, so the map cannot describe a field that does not exist, or miss one.
def _map_entry(v2_model, v1_model, *, note: str, nests: str | None = None) -> dict[str, Any]:
    v2_fields = set(v2_model.model_fields) - {"schema_version"}
    v1_fields = (set(v1_model.model_fields) - {"schema_version"}
                 if v1_model is not None else set())
    return {
        "record": v2_model.__name__,
        "v1": v1_model.__name__ if v1_model is not None else None,
        "nests_v1_as": nests,
        "same_name": sorted(v2_fields & v1_fields),
        "added": sorted(v2_fields - v1_fields),
        "dropped": sorted(v1_fields - v2_fields),
        "note": note,
    }


def field_map() -> dict[str, Any]:
    return {
        "surface_version": SURFACE_VERSION,
        "schema_version": v2.SCHEMA_VERSION,
        "schema_version_note": "every internal v2 record carries schema_version: 2; it is "
                               "excluded from the per-record field lists below",
        "appendix": "research/plan/01a-contracts-v2-map.md",
        "database_map": "research/plan/06a-database-map-v2.md",
        "records": [
            _map_entry(v2.AuthContextV2, v1.AuthContext,
                       note="adds the credential audience and the trusted identities behind "
                            "it (user, provider org, private endpoint). No wallet field: the "
                            "wallet is resolved by ports.resolve_wallet from these."),
            _map_entry(v2.NormalizedRequestV2, v1.NormalizedRequest, nests="request",
                       note="the v1 request is nested verbatim, so every public "
                            "OpenAI-style field is unchanged; v2 adds only the internal "
                            "resolution (pins, wallet, policy)."),
            _map_entry(v2.AdmissionV2, v1.Admission,
                       note="the price snapshot becomes a CREDIT rate card and the resolved "
                            "identities are pinned. Capacity reservations, budgets, outbox "
                            "and deadlines stay in the v1 Admission that D persists."),
            _map_entry(v2.WorkV2, v1.Work, nests="request",
                       note="the lease holder receives the admitted rate card and pins, "
                            "never a re-resolvable alias."),
            _map_entry(v2.RateCardSnapshot, v1.PriceSnapshot,
                       note="PriceSnapshot renamed and re-denominated: unit CREDIT, meter "
                            "tokens-v1, rates per million as CREDIT, effective_at, approver "
                            "and both rounding rules named. currency/USD is gone; there is "
                            "no conversion from a v1 snapshot."),
            _map_entry(v2.SettlementV2, v1.TerminalOutcome,
                       note="the money half of the terminal outcome, in CREDIT, at the "
                            "admitted card. State, cause and result ref stay on v1's "
                            "TerminalOutcome."),
            _map_entry(v2.WalletRef, None,
                       note="new: the server-derived wallet. kind fixes ownership "
                            "(user XOR provider org) and the personal-org billing binding."),
            _map_entry(v2.SignupGrant, None,
                       note="new: the one-time individual grant, keyed by "
                            "(user_id, initial_signup_grant) alone."),
            _map_entry(v2.CreditLedgerEntry, None,
                       note="new: append-only CREDIT movements. The closed kind set contains "
                            "no transfer, so provider dev credit cannot reach a consumer."),
            _map_entry(v2.LegacyUsdStatement, None,
                       note="new: existing delta_usd history, preserved and labelled. A "
                            "nonzero balance is a rollout hold, never a converted total."),
            _map_entry(v2.ProviderBudget, None,
                       note="new: external judge/teacher budgets in PROVIDER_USD, never "
                            "summed with a consumer balance."),
            _map_entry(v2.UsageRecordV2, None,
                       note="new DTO: one settled request with its explicit accounting "
                            "regime and unit beside the amount."),
            _map_entry(v2.UsageHistory, None,
                       note="new DTO: a mixed legacy/CREDIT history whose totals() answers "
                            "per unit and never once."),
            _map_entry(v2.BalanceV2, None,
                       note="new DTO: CREDIT total, reserved and derived available, with the "
                            "legacy USD statement alongside rather than added in."),
            _map_entry(v2.ServingRevision, None,
                       note="new: immutable repository/commit/per-shard weight/adapter/"
                            "tokenizer/chat-template/prompt/preprocessor/engine/runtime "
                            "identity. r1 R62: the v1 model_revision string "
                            "<public_model_id>@<revision> is unchanged and is NOT an "
                            "artifact identity; this record is. digest_source records "
                            "that the Marlin digests are served bytes, with registry-oid "
                            "equality still pending (W3), and runtime_image_digest is "
                            "absent while serve.sh pins a moving tag."),
            _map_entry(v2.DeploymentRevision, None,
                       note="new: endpoint -> serving revision, with environment, visibility "
                            "and lifecycle state. Dev is never public."),
            _map_entry(v2.CapabilityRecord, None,
                       note="new: the minimum capability record of "
                            "research/platforms/07-api-contracts.md."),
            _map_entry(v2.AdmissionPins, None,
                       note="new: model -> deployment revision -> serving version + rate "
                            "card version + policy version, frozen at acceptance."),
            _map_entry(v2.DataAccessPolicyRef, None,
                       note="new: the data-access policy identity pinned with the job."),
            _map_entry(v2.ProviderMembership, None,
                       note="new: explicit provider membership and role. Consumer org "
                            "ownership grants none of it."),
            _map_entry(v2.AccessGrant, None,
                       note="new: a source owner's grant to one provider for named "
                            "categories and purposes, checked current at every use."),
        ],
    }


# --- loading ----------------------------------------------------------------
def names() -> tuple[str, ...]:
    return tuple(sorted(p.name for p in DIR.glob("*.json")))


def load_bytes(name: str) -> bytes:
    return (DIR / name).read_bytes()


def load(name: str) -> Any:
    return json.loads(load_bytes(name))


def model(name: str) -> BaseModel:
    """The fixture as its validated model (single-model fixtures only)."""
    return MODELS[name].model_validate(load(name))


def build() -> dict[str, bytes]:
    """The canonical bytes of every fixture, from the records themselves."""
    built = {name: codec.canonical_bytes(builder()) for name, builder in BUILDERS.items()}
    built["map.json"] = codec.canonical_bytes(field_map())
    built["money_unit_cases.json"] = codec.canonical_bytes(list(mu.PARITY_CASES))
    built["lifecycle_refusals.json"] = codec.canonical_bytes(lc.refusal_table())
    built["result_read_cases.json"] = codec.canonical_bytes(lc.result_case_table())
    return built


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="regenerate the contracts-v2 fixture base")
    parser.add_argument("--write", action="store_true", help="write the files to disk")
    args = parser.parse_args()
    DIR.mkdir(parents=True, exist_ok=True)
    changed = []
    for name, payload in build().items():
        path = DIR / name
        if not path.exists() or path.read_bytes() != payload:
            changed.append(name)
            if args.write:
                path.write_bytes(payload)
    print(("wrote " if args.write else "would change ") + (", ".join(changed) or "nothing"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
