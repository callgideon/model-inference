// node --test "tests/**/*.test.ts"  (nested; Node strips the types, no framework)
//
// The console half of the v2 fixture base (F2P item 1). The fixtures are read from the
// Python package's own directory rather than copied here, so the two halves cannot
// drift apart: a record change regenerates one file and both suites see it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  DATA_CATEGORIES,
  DATA_PURPOSES,
  INITIAL_SIGNUP_ENTITLEMENT,
  INITIAL_SIGNUP_GRANT_CREDIT,
  PROVIDER_CAPABILITIES,
  ROLE_CAPABILITIES,
  availableCredit,
  grantPermits,
  hasSignupEntitlement,
  instantKey,
  mayReadCustomerContent,
  membershipPermits,
  unitOfRow,
  usageTotalsByUnit,
  type AccessGrant,
  type BalanceV2,
  type ProviderMembership,
  type RateCardSnapshot,
  type SignupGrant,
  type UsageRecordV2,
  type WalletRef,
} from "../../../lib/contracts/v2/types.ts";

const FIXTURES = new URL(
  "../../../../infrx-api/infrx/contracts/fixtures/v2/",
  import.meta.url,
);

function fixture<T>(name: string): T {
  return JSON.parse(readFileSync(new URL(name, FIXTURES), "utf8")) as T;
}

const NOW = "2026-09-22T12:00:00Z";
const LATER = "2027-01-02T00:00:00Z";
const PROVIDER_ORG = "b0000001-0000-4000-8000-000000000001";
const RIVAL_ORG = "b0000002-0000-4000-8000-000000000002";
const MODEL = "d0000001-0000-4000-8000-000000000001";

test("the committed wallet fixtures type-check as the console's DTOs", () => {
  const consumer = fixture<WalletRef>("wallet_consumer.json");
  const provider = fixture<WalletRef>("wallet_provider_dev.json");
  assert.equal(consumer.kind, "consumer");
  assert.equal(consumer.unit, "CREDIT");
  assert.equal(hasSignupEntitlement(consumer), true);
  assert.equal(hasSignupEntitlement(provider), false);
  assert.equal(provider.ledger_total, "0.00000000");
  assert.equal(provider.owner_user_id, undefined, "a provider wallet has no individual owner");
  assert.equal(consumer.owner_provider_org_id, undefined);
});

test("available credit is recomputed, not trusted", () => {
  const balance = fixture<BalanceV2>("balance.json");
  assert.equal(availableCredit(balance), balance.available);
  assert.equal(availableCredit(balance), "9989.98560000");
  // a drifted stored counter is visible rather than believed
  assert.notEqual(availableCredit({ ...balance, available: "10000.00000000" as never }), "10000.00000000");
});

test("the legacy USD statement sits beside the CREDIT total, never inside it", () => {
  const balance = fixture<BalanceV2>("balance.json");
  assert.ok(balance.legacy_usd, "the fixture carries a legacy statement");
  assert.equal(balance.legacy_usd.balance, "4.21500000");
  assert.equal(balance.legacy_usd.rollout_hold, true, "a nonzero legacy balance is a hold");
  assert.equal(balance.unit, "CREDIT");
  // and nothing in the DTO offers a combined figure
  assert.equal("total" in balance, false);
});

test("a mixed history totals per unit and refuses a regime/unit mismatch", () => {
  const history = fixture<{ entries: UsageRecordV2[] }>("usage_history_mixed.json");
  assert.deepEqual(usageTotalsByUnit(history.entries), {
    CREDIT: "9.97600000",
    USD: "0.01414000",
  });
  assert.deepEqual(usageTotalsByUnit([]), {}, "an empty history has no total in any unit");
  const legacy = history.entries.find((row) => row.accounting_regime === "legacy_usd")!;
  assert.equal(unitOfRow(legacy), "USD");
  assert.equal(legacy.rate_card_version, undefined, "an old row invents no rate card");
  assert.equal(legacy.serving_version_id, undefined);
  assert.throws(
    () => usageTotalsByUnit([{ ...legacy, unit: "CREDIT" }]),
    /cannot be denominated/,
  );
});

test("the rate card fixture is an approved CREDIT card, labelled provisional", () => {
  const card = fixture<RateCardSnapshot>("rate_card_marlin.json");
  assert.equal(card.unit, "CREDIT");
  assert.equal(card.meter, "tokens-v1");
  assert.equal(card.status, "approved");
  assert.equal(card.hold_rounding, "ceiling_8");
  assert.equal(card.debit_rounding, "half_up_8");
  assert.match(card.approved_by, /provisional/, "P-01 is still pending");
});

test("the signup grant fixture is the exact individual entitlement", () => {
  const grant = fixture<SignupGrant>("signup_grant.json");
  assert.equal(grant.amount, INITIAL_SIGNUP_GRANT_CREDIT);
  assert.equal(grant.amount, "10000.00000000");
  assert.equal(grant.entitlement, INITIAL_SIGNUP_ENTITLEMENT);
  assert.ok(grant.verification_evidence_ref.length > 0);
});

test("no provider role reaches customer content, and roles default deny", () => {
  for (const capabilities of Object.values(ROLE_CAPABILITIES)) {
    assert.equal(capabilities.includes("read_customer_content"), false);
  }
  assert.ok(PROVIDER_CAPABILITIES.includes("read_customer_content"));
  const membership = fixture<ProviderMembership>("provider_membership.json");
  assert.equal(membershipPermits(membership, "manage_dev_deployment", NOW, PROVIDER_ORG), true);
  assert.equal(membershipPermits(membership, "manage_members", NOW, PROVIDER_ORG), false);
  assert.equal(membershipPermits(membership, "read_customer_content", NOW, PROVIDER_ORG), false);
  assert.equal(membershipPermits(membership, "manage_dev_deployment", NOW, RIVAL_ORG), false);
  assert.equal(membershipPermits(null, "read_aggregate_health", NOW, PROVIDER_ORG), false);
  assert.equal(
    membershipPermits({ ...membership, revoked_at: NOW }, "read_aggregate_health", NOW, PROVIDER_ORG),
    false,
  );
});

test("a grant is checked current, and every dimension must match", () => {
  const grant = fixture<AccessGrant>("access_grant.json");
  const base = {
    now: NOW,
    providerOrgId: PROVIDER_ORG,
    modelId: MODEL,
    category: "request_content" as const,
    purpose: "provider_sharing" as const,
  };
  assert.equal(grantPermits(grant, base), true);
  assert.equal(grantPermits(grant, { ...base, now: LATER }), false, "expired");
  assert.equal(grantPermits({ ...grant, revoked_at: NOW }, base), false, "revoked");
  assert.equal(grantPermits(grant, { ...base, providerOrgId: RIVAL_ORG }), false);
  assert.equal(grantPermits(grant, { ...base, modelId: "other" }), false);
  assert.equal(grantPermits(grant, { ...base, category: "media" }), false);
  for (const purpose of DATA_PURPOSES.filter((p) => p !== "provider_sharing")) {
    assert.equal(grantPermits(grant, { ...base, purpose }), false, purpose);
  }
  assert.equal(grantPermits({ ...grant, model_ids: [] }, base), false, "empty scope denies");
  assert.equal(grantPermits(null, base), false);
  assert.ok(DATA_CATEGORIES.length === 5);
});

test("content access needs a current membership and a current grant", () => {
  const membership = fixture<ProviderMembership>("provider_membership.json");
  const grant = fixture<AccessGrant>("access_grant.json");
  const base = {
    now: NOW,
    providerOrgId: PROVIDER_ORG,
    modelId: MODEL,
    category: "request_content" as const,
    purpose: "provider_sharing" as const,
  };
  assert.equal(mayReadCustomerContent(membership, grant, base), true);
  assert.equal(mayReadCustomerContent(null, grant, base), false);
  assert.equal(mayReadCustomerContent(membership, null, base), false);
  assert.equal(
    mayReadCustomerContent({ ...membership, role: "viewer" }, grant, base),
    false,
    "a viewer has a membership and still cannot read content",
  );
  assert.equal(mayReadCustomerContent(membership, { ...grant, revoked_at: NOW }, base), false);
});

test("the v1 contract module carries the v2 revision as a namespace, not a shadow", async () => {
  // F2P wire-in item 9: `v2.X` from lib/contracts/types.ts is the v2 module itself, and the one
  // name both revisions declare differently stays distinct in each.
  const v1 = await import("../../../lib/contracts/types.ts");
  assert.equal(v1.v2.availableCredit, availableCredit, "v2 is not the v2 DTO module");
  assert.equal(v1.v2.SURFACE_VERSION, "contracts-v2.0", "the v2 namespace lost the unit module");
  assert.deepEqual(v1.v2.ACCOUNTING_REGIMES, ["legacy_usd", "credit"]);
  assert.deepEqual(v1.ACCOUNTING_REGIMES, ["legacy_usd", "pilot"]);
  assert.deepEqual(v1.v2.CREDENTIAL_AUDIENCES, ["consumer", "provider_dev", "operator"]);
});

test("grant and membership checks compare instants, never mixed spellings", () => {
  // F2P wire-in (01a §7): a revocation at 12:00:00.5Z is in force at 12:00:00.600Z although, as
  // raw text, "12:00:00.600Z" < "12:00:00.5Z" is false only by accident of padding and
  // "12:00:00Z" > "12:00:00.5Z" is simply wrong. Offsets are refused, not compared.
  assert.equal(instantKey("2026-09-22T12:00:00Z"), "2026-09-22T12:00:00.000000Z");
  assert.equal(instantKey("2026-09-22T12:00:00.5Z"), "2026-09-22T12:00:00.500000Z");
  for (const bad of ["2026-09-22T12:00:00+00:00", "2026-09-22T12:00:00z", "2026-09-22T12:00:00",
                     "2026-09-22T12:00:00.1234567Z", "2026-09-22 12:00:00Z"]) {
    assert.throws(() => instantKey(bad), TypeError, bad);
  }
  const grant = fixture<AccessGrant>("access_grant.json");
  const base = {
    providerOrgId: grant.recipient_provider_org_id,
    modelId: grant.model_ids[0],
    category: grant.categories[0],
    purpose: grant.purposes[0],
  };
  const revokedAt = grant.effective_at.replace("Z", ".5Z");
  const revoked = { ...grant, revoked_at: revokedAt };
  // Before the revocation (same second, whole-second spelling): current.
  assert.equal(grantPermits(revoked, { ...base, now: grant.effective_at }), true);
  // After it (fraction longer than the revocation's): revoked.
  assert.equal(grantPermits(revoked, { ...base, now: grant.effective_at.replace("Z", ".600Z") }), false);
  assert.throws(() => grantPermits(grant, { ...base, now: grant.effective_at.replace("Z", "+00:00") }), TypeError);
  const membership = fixture<ProviderMembership>("provider_membership.json");
  const later = membership.granted_at.replace("Z", ".5Z");
  assert.equal(
    membershipPermits({ ...membership, revoked_at: later }, "read_aggregate_health", membership.granted_at, membership.provider_org_id),
    true,
  );
  assert.equal(
    membershipPermits({ ...membership, revoked_at: later }, "read_aggregate_health", later.replace(".5Z", ".6Z"), membership.provider_org_id),
    false,
  );
});
