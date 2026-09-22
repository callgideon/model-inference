/**
 * Console contract vocabulary and DTOs — contracts v2 (F2P item 1).
 *
 * The mirror of `infrx/contracts/v2/records.py`: every union is a frozen
 * `as const` list with the type derived from it, so a runtime guard and the
 * compiler share one source, and `tests/contracts/v2/test_parity_v2.py` compares
 * each list against the Python enum it mirrors. A vocabulary that drifts in one
 * language fails that test rather than surfacing as a mystery in a UI track.
 *
 * Amounts are branded by unit (`./money-units.ts`) and carry their unit token
 * beside them, because the brand does not exist at runtime. There is no
 * conversion, in either language.
 *
 * Owned by the coordinator: a change here is a contract revision.
 */

import {
  type AccountingRegime,
  type Credit,
  type MoneyUnit,
  type ProviderUsd,
  type Usd,
  parseAmount,
  parseCredit,
  subCredit,
  totalCredit,
  totalUsd,
  unitOfRegime,
} from "./money-units.ts";

// ---------------------------------------------------------------------------
// Vocabulary — mirrors of the v2 StrEnums, string values frozen.
// ---------------------------------------------------------------------------

/** Which product a credential speaks for. A consumer key cannot publish a model. */
export const CREDENTIAL_AUDIENCES = ["consumer", "provider_dev", "operator"] as const;
export type CredentialAudience = (typeof CREDENTIAL_AUDIENCES)[number];

export const WALLET_KINDS = ["consumer", "provider_dev"] as const;
export type WalletKind = (typeof WALLET_KINDS)[number];

/** Closed: there is no transfer or conversion movement, in either language. */
export const LEDGER_ENTRY_KINDS_V2 = [
  "signup_grant",
  "operator_allocation",
  "operator_adjustment",
  "inference_debit",
] as const;
export type LedgerEntryKindV2 = (typeof LEDGER_ENTRY_KINDS_V2)[number];

export const PROVIDER_ROLES = ["viewer", "developer", "administrator"] as const;
export type ProviderRole = (typeof PROVIDER_ROLES)[number];

export const PROVIDER_CAPABILITIES = [
  "read_aggregate_health",
  "manage_dev_deployment",
  "run_evaluation",
  "propose_publication",
  "manage_members",
  "read_customer_content",
] as const;
export type ProviderCapability = (typeof PROVIDER_CAPABILITIES)[number];

/**
 * `read_customer_content` is deliberately absent from every set: provider
 * ownership yields no customer payload, and only a current access grant reaches it.
 */
export const ROLE_CAPABILITIES: Readonly<Record<ProviderRole, readonly ProviderCapability[]>> =
  Object.freeze({
    viewer: ["read_aggregate_health"],
    developer: ["read_aggregate_health", "manage_dev_deployment", "run_evaluation"],
    administrator: [
      "read_aggregate_health",
      "manage_dev_deployment",
      "run_evaluation",
      "propose_publication",
      "manage_members",
    ],
  });

export const DATA_CATEGORIES = [
  "request_content",
  "response_content",
  "media",
  "usage_metadata",
  "feedback",
] as const;
export type DataCategory = (typeof DATA_CATEGORIES)[number];

/** Four separate permissions. Capture implies none of the other three. */
export const DATA_PURPOSES = [
  "capture",
  "provider_sharing",
  "external_judging",
  "training",
] as const;
export type DataPurpose = (typeof DATA_PURPOSES)[number];

export const ENVIRONMENTS = ["dev", "prod"] as const;
export type Environment = (typeof ENVIRONMENTS)[number];

export const VISIBILITIES = ["private", "public"] as const;
export type Visibility = (typeof VISIBILITIES)[number];

export const DEPLOYMENT_STATES = [
  "draft",
  "validating",
  "ready_private",
  "proposed_public",
  "active",
  "draining",
  "retired",
] as const;
export type DeploymentState = (typeof DEPLOYMENT_STATES)[number];

export const DIGEST_SOURCES = [
  "served_bytes",
  "registry_oid",
  "registry_oid_confirmed",
] as const;
export type DigestSource = (typeof DIGEST_SOURCES)[number];

/** The only meter this revision accepts; an unknown one is a refusal. */
export const BILLING_METERS = ["tokens-v1"] as const;
export type BillingMeter = (typeof BILLING_METERS)[number];

/** The individual promotional grant, exactly. Never described as a request count. */
export const INITIAL_SIGNUP_GRANT_CREDIT = "10000.00000000" as Credit;
export const INITIAL_SIGNUP_ENTITLEMENT = "initial_signup_grant";

// ---------------------------------------------------------------------------
// DTOs the console reads. Server-derived; none of them is ever built from a form.
// ---------------------------------------------------------------------------

export type WalletRef = {
  schema_version: 2;
  wallet_id: string;
  kind: WalletKind;
  unit: "CREDIT";
  owner_user_id?: string;
  owner_provider_org_id?: string;
  personal_org_id?: string;
  ledger_total: Credit;
  reserved_total: Credit;
  revision: number;
};

/** The legacy USD balance, shown separately and labelled. Never spendable here. */
export type LegacyUsdStatement = {
  schema_version: 2;
  org_id: string;
  balance: Usd;
  entry_count: number;
  as_of: string;
  rollout_hold: boolean;
};

export type BalanceV2 = {
  schema_version: 2;
  wallet_id: string;
  kind: WalletKind;
  unit: "CREDIT";
  ledger_total: Credit;
  reserved_total: Credit;
  available: Credit;
  legacy_usd?: LegacyUsdStatement;
};

export type UsageRecordV2 = {
  schema_version: 2;
  request_id: string;
  org_id: string;
  accounting_regime: AccountingRegime;
  unit: "CREDIT" | "USD";
  charged_amount: string;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  outcome: string;
  rate_card_version?: string;
  serving_version_id?: string;
  deployment_revision_id?: string;
  price_version?: string;
  settled_at: string;
};

export type RateCardSnapshot = {
  schema_version: 2;
  rate_card_version: string;
  unit: "CREDIT";
  meter: BillingMeter;
  model_id: string;
  deployment_revision_id: string;
  serving_version_id: string;
  input_rate_per_million: Credit;
  output_rate_per_million: Credit;
  effective_at: string;
  approved_by: string;
  status: "approved";
  hold_rounding: "ceiling_8";
  debit_rounding: "half_up_8";
};

export type SignupGrant = {
  schema_version: 2;
  user_id: string;
  entitlement: "initial_signup_grant";
  wallet_id: string;
  amount: Credit;
  verification_evidence_ref: string;
  ledger_operation_id: string;
  campaign_version: string;
  granted_at: string;
};

export type ProviderMembership = {
  schema_version: 2;
  provider_org_id: string;
  user_id: string;
  role: ProviderRole;
  granted_by: string;
  granted_at: string;
  revoked_at?: string;
};

export type AccessGrant = {
  schema_version: 2;
  grant_id: string;
  version: number;
  grantor_org_id: string;
  recipient_provider_org_id: string;
  model_ids: readonly string[];
  categories: readonly DataCategory[];
  purposes: readonly DataPurpose[];
  retention_days: number;
  effective_at: string;
  expires_at?: string;
  revoked_at?: string;
};

export type ProviderBudget = {
  schema_version: 2;
  budget_id: string;
  provider_org_id: string;
  unit: "PROVIDER_USD";
  limit: ProviderUsd;
  reserved: ProviderUsd;
  period: string;
};

// ---------------------------------------------------------------------------
// The few derivations the console must not get wrong.
// ---------------------------------------------------------------------------

/**
 * Available credit, recomputed rather than trusted: a stored counter can drift,
 * and a drifted "available" is either an over-spend or a refused valid request.
 */
export function availableCredit(balance: BalanceV2): Credit {
  return subCredit(parseCredit(balance.ledger_total), parseCredit(balance.reserved_total));
}

/**
 * Totals of a mixed legacy/CREDIT history, **one per unit**. There is no combined
 * figure and there must never be one: the plan fixes no rate at which the two
 * could be added (`research/platforms/02-credits.md`).
 */
export function usageTotalsByUnit(rows: readonly UsageRecordV2[]): Record<string, string> {
  const credits: Credit[] = [];
  const dollars: Usd[] = [];
  for (const row of rows) {
    if (row.unit !== unitOfRegime(row.accounting_regime)) {
      throw new TypeError(
        `a ${row.accounting_regime} row cannot be denominated in ${row.unit}`,
      );
    }
    const amount = parseAmount(row.charged_amount, row.unit);
    if (row.unit === "CREDIT") credits.push(amount as Credit);
    else dollars.push(amount as Usd);
  }
  const totals: Record<string, string> = {};
  if (credits.length > 0) totals.CREDIT = totalCredit(credits);
  if (dollars.length > 0) totals.USD = totalUsd(dollars);
  return totals;
}

/** The unit a row must carry, from its declared regime. Never from its magnitude. */
export function unitOfRow(row: Pick<UsageRecordV2, "accounting_regime">): MoneyUnit {
  return unitOfRegime(row.accounting_regime);
}

/** Default deny: an unknown capability, another provider or a revoked membership. */
export function membershipPermits(
  membership: ProviderMembership | null,
  capability: ProviderCapability,
  now: string,
  providerOrgId: string,
): boolean {
  if (membership === null) return false;
  if (membership.provider_org_id !== providerOrgId) return false;
  if (membership.granted_at > now) return false;
  if (membership.revoked_at !== undefined && membership.revoked_at <= now) return false;
  return ROLE_CAPABILITIES[membership.role].includes(capability);
}

/** Current, not snapshot: revoked or expired denies, and an empty scope denies. */
export function grantPermits(
  grant: AccessGrant | null,
  options: {
    now: string;
    providerOrgId: string;
    modelId: string;
    category: DataCategory;
    purpose: DataPurpose;
  },
): boolean {
  if (grant === null) return false;
  if (options.now < grant.effective_at) return false;
  if (grant.revoked_at !== undefined && options.now >= grant.revoked_at) return false;
  if (grant.expires_at !== undefined && options.now >= grant.expires_at) return false;
  return (
    grant.recipient_provider_org_id === options.providerOrgId &&
    grant.model_ids.includes(options.modelId) &&
    grant.categories.includes(options.category) &&
    grant.purposes.includes(options.purpose)
  );
}

/**
 * Both halves, every time. A developer membership AND a current grant for that
 * provider, source, category and purpose. Provider ownership is not one of them.
 */
export function mayReadCustomerContent(
  membership: ProviderMembership | null,
  grant: AccessGrant | null,
  options: Parameters<typeof grantPermits>[1],
): boolean {
  return (
    membershipPermits(membership, "manage_dev_deployment", options.now, options.providerOrgId) &&
    grantPermits(grant, options)
  );
}

/** A consumer wallet is the only one with a signup entitlement. */
export function hasSignupEntitlement(wallet: WalletRef): boolean {
  return wallet.kind === "consumer";
}
