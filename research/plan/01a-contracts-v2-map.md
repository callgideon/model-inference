# Contracts v2 — the v1 → v2 field map (appendix to `01-contracts.md`)

Appendix to [shared contracts](01-contracts.md), written by F2P. It records the
executable encoding of revision 2 that now exists in
`apps/infrx-api/infrx/contracts/v2/` and `apps/app/lib/contracts/v2/`, and the
rulings F2P had to make to get there.

**Status.** This is the *additive* half of F2P: v1 is untouched, nothing imports v2
yet, and no adapter, migration or UI consumes it. The committed artefacts are the
types, the fixture base and the conformance cases D1R/D2–D5, A1, G1R/G6B and
M/Q/W implement against. The wire-in phase (F2P item 7) is listed in
[§7](#7-what-the-wire-in-phase-must-touch) and is not done.

The machine-readable form of this map is
`apps/infrx-api/infrx/contracts/fixtures/v2/map.json`, generated from the records
themselves and checked field-by-field against both revisions' live `model_fields`
by `tests/contracts/v2/test_fixtures_v2.py`. Read that file for the exact lists;
this document explains the *why*, which a generator cannot.

One reviewed identifier covers the whole changed consumer/provider surface:
**`contracts-v2.0`**, declared once in `infrx/contracts/v2/__init__.py`
(`SURFACE_VERSION`) and once in `apps/app/lib/contracts/v2/money-units.ts`, with
`tests/contracts/v2/test_parity_v2.py` failing if the two drift.

## 1. What revision 2 changes, in one table

| v1 record | v2 record | The change |
|---|---|---|
| `AuthContext` | `AuthContextV2` | adds `audience` (consumer / provider_dev / operator) and the trusted identities behind it (`user_id`, `provider_org_id`, `endpoint_id`). Carries **no wallet field**. |
| `NormalizedRequest` | `NormalizedRequestV2` | **nests the v1 record verbatim** as `request`, and adds `pins`, `wallet_id`, `policy`. Every public OpenAI-style field is therefore unchanged by construction. |
| `Admission` | `AdmissionV2` | `price_snapshot` → `rate_card` (CREDIT), plus `pins` and `wallet_id`. Capacity reservations, budgets, outbox and the deadline set stay on the v1 `Admission` that D persists. |
| `Work` | `WorkV2` | nests `NormalizedRequestV2`; the worker receives the **admitted** rate card, not a re-resolvable alias. |
| `PriceSnapshot` | `RateCardSnapshot` | renamed and re-denominated: `unit: CREDIT`, `meter: tokens-v1`, rates per million in CREDIT, `effective_at`, `approved_by`, `status`, and both roundings named. `currency` is **dropped, not renamed**. |
| `TerminalOutcome` | `SettlementV2` | the money half only, in CREDIT, at the admitted card. State, cause and result ref stay on v1's `TerminalOutcome`. |
| — | `WalletRef` | new. Server-derived; `kind` fixes ownership as an exclusive-or plus the protected personal-org binding. |
| — | `SignupGrant`, `CreditLedgerEntry` | new. The individual entitlement, and the closed set of CREDIT movements. |
| — | `LegacyUsdStatement`, `ProviderBudget` | new. Historical USD, and external budgets in `PROVIDER_USD`. |
| — | `UsageRecordV2`, `UsageHistory`, `BalanceV2` | new DTOs. Explicit regime and unit; totals per unit; derived available. |
| — | `ServingRevision`, `DeploymentRevision`, `CapabilityRecord`, `AdmissionPins`, `DataAccessPolicyRef` | new. The registry identities admission pins. |
| — | `ProviderMembership`, `AccessGrant` | new. Provider roles, and source-purpose grants. |

Internal v2 records carry `schema_version: 2`, and a payload with any other value
is **refused** rather than upgraded. Reused v1 records nested inside a v2 record
(`Usage`, `MediaRef`, `ConsentSnapshot`, `Budgets`, `NormalizedRequest`) keep
`schema_version: 1`, because they are v1 records and pretending otherwise would
hide which revision produced a row.

## 2. Money: shared arithmetic, three denominations

`infrx/contracts/money.py` is still the only money arithmetic in the tree and was
not forked. `contracts/v2/money_units.py` adds the unit that v1 did not have:

- `Credit` — the consumer product unit. Not dollars.
- `Usd` — the historical `credit_ledger.delta_usd` / `usage_events.cost_usd` regime.
- `ProviderUsd` — external provider/teacher/judge budgets.

They are **siblings, not aliases and not subclasses of one another**, so no
`isinstance` check can be satisfied by the wrong unit, and every binary operation
returns `NotImplemented` across units — `Credit("1") + Usd("1")` is a `TypeError`
from the interpreter rather than a plausible number. The console half is branded
string types over the existing `money.ts` BigInt arithmetic; mixed-unit calls fail
`tsc --noEmit`, and `parseAmount(value, unit)` re-checks the unit at runtime where
the brand no longer exists.

**There is no conversion function anywhere in the tree, and that is deliberate.**
[`02-credits.md`](../platforms/02-credits.md) chooses no exchange rate, so a
function producing one would be inventing product policy. `Credit(Usd("1"))`
raises because `money.parse` accepts only a string, an int or a `Decimal`; the one
unwrap, `raw(unit)`, refuses unless the caller names the unit it already expects.

An amount is always already rounded to 1e-8 (`money.parse` refuses finer), so an
unrounded per-token cost cannot be held in one of these types at all. "Computed
once, rounded once, at the end" is therefore a property of the types.

## 3. Identity and the wallet

A credential's audience is its own property, read from the key row. The wallet is
resolved from it:

| Audience | Resolves | Notes |
|---|---|---|
| `consumer` | the wallet owned by the credential's own `user_id`, whose `personal_org_id` equals the authenticating org | Anything else is `Forbidden`, never a fallback |
| `provider_dev` | the provider organization's zero-initialised dev wallet, and only the private endpoint the credential is scoped to | No signup entitlement; cannot reach a consumer wallet |
| `operator` | nothing | Operator work is audited grants and adjustments, not inference on a balance |

`ports.resolve_wallet(auth, wallet)` takes **no request**: the signature is the
contract. `AuthContextV2` has no wallet field and `extra="forbid"` refuses a
payload that adds one, so there is no path from a request body to a wallet.

`LedgerEntryKind` is closed — `signup_grant`, `operator_allocation`,
`operator_adjustment`, `inference_debit` — and contains no transfer. A provider dev
wallet cannot move balance into a consumer wallet because **no operation expresses
it**; adding one is a contract revision.

## 4. Admission pins

At acceptance, `model → deployment_revision → serving_version + rate_card_version
+ policy_version` is resolved and frozen in `AdmissionPins`. The records cannot
state the alternative:

- `AdmissionV2` and `WorkV2` refuse a rate card whose version, deployment revision
  or serving version is not the pinned one.
- `settle(admission, usage, settled_at)` takes no catalog, clock or rate. A rate
  published, an alias moved or a deployment rolled back after acceptance cannot
  reach the settlement, and an idempotent replay settles identically.
- The public request shape has no price, rate card, serving, deployment, wallet or
  hold field (R45 extended), and `pin_admission` takes every input as a named
  trusted row.

Refusals do not confirm what exists: unknown, private-to-others and retired are
`not_found`; unpriced, unresolvable serving and a mismatched card are
`invalid_request`, because **unpriced is unserveable, not free**. Unknown usage is
never settled, and usage beyond the reserved envelope is a platform incident.

r1 R62 holds: the consumer-facing `model_revision` keeps its v1 form
`<public_model_id>@<revision>` (`nemostation/marlin-2b@2026-09-01`, byte-identical
to the v1 fixtures) and is **not** an artifact identity. `ServingRevision` is where
the artifact is pinned.

## 5. The individual grant, and the legacy regime

`SignupGrant.key` is `(user_id, initial_signup_grant)` and nothing else. A campaign
bump, another organization, a provider membership or different verification
evidence all produce the **same** key, so the unique index still refuses a second
grant. Only a different user is a different key. The amount is exactly
`+10000.00000000` CREDIT, validated on both the grant and its ledger entry, and a
blank verification reference is refused.

Legacy USD stays legacy. `LegacyUsdStatement` requires a nonzero balance to be
flagged `rollout_hold`, because no conversion policy exists.
`upgrade_v1_price_snapshot_is_refused` is a **named refusal** placed where someone
would look for a converter. `project_v1_usage` reads a pre-cutover row with the v2
fields absent rather than back-filled, and refuses a row that is already CREDIT.

`UsageHistory.totals()` answers one figure per unit and there is no combined
figure; `BalanceV2` carries the legacy statement beside the CREDIT total and
adding the two raises.

## 6. Provider roles and source-purpose grants

Roles: `viewer` (aggregate health), `developer` (dev deployment, evaluation),
`administrator` (adds publication proposals and membership). Platform approval of
public production changes is not a provider role.

`read_customer_content` is in the capability vocabulary and in **no role's set**:
provider ownership of a model yields no customer payload. It is reachable only
through a current `AccessGrant`, and `may_read_customer_content` requires both a
current membership and a current grant.

Capture, provider sharing, external judging and training are four separate
permissions. A grant names grantor, recipient provider, model scope, categories,
purposes, retention (1–90 days), version, effective/expiry/revocation, and is
checked **current** at every use — `ProviderDirectory.current_grant` has no "as of"
parameter, so a snapshot cannot be used as authorization. An empty scope tuple
means nothing in scope, never everything.

## 7. What the wire-in phase must touch

F2P item 7 is **not done**. The additive phase deliberately added no import of v2
from any v1 module, so nothing in the tree consumes revision 2 yet. The wire-in
phase, on the coordinator's go-ahead and rebased on F2R's accepted commit, must
edit exactly these existing files:

| File | Change |
|---|---|
| `apps/infrx-api/infrx/contracts/__init__.py` | add `v2` to `_SUBMODULES` so `contracts.v2` resolves by attribute access like every other submodule |
| `apps/infrx-api/infrx/contracts/conformance/__init__.py` | export `run_v2_conformance` and add the v2 suite to `SUITES` (or a `V2_SUITES` sibling, coordinator's call) |
| `apps/infrx-api/infrx/contracts/fakes/state.py`, `factories.py` | v2 admission/settlement path in the fake `JobStore`, and a `V2Harness` factory beside the v1 ones |
| `apps/infrx-api/infrx/contracts/ports.py` | `JobStore.admit`/`complete` v2 signatures (or v2 siblings); the v1 signatures stay until D2–D5 cut over |
| `apps/infrx-api/infrx/config.py` | the v2 config names: accounting regime, the active rate-card version, and the provider-dev wallet allocation ceiling |
| `apps/infrx-api/tests/contracts/mutants.py` | fold `tests/contracts/v2/mutants_v2.py` into the consolidated list, and parameterise `run_mutant` with the test path so the duplicated classifier in `mutants_v2.py` can be deleted |
| `apps/infrx-api/tests/contracts/test_fixtures.py` | the v1 fixture guard must also know about `fixtures/v2/` (or state that it deliberately does not) |
| `apps/infrx-api/tests/contracts/test_parity_console.py` | add the v2 vocabulary comparison, or record that `tests/contracts/v2/test_parity_v2.py` owns it |
| `apps/app/lib/contracts/types.ts` | re-export or reference the v2 vocabulary so a console module cannot import a v1 enum where a v2 one is meant |
| `apps/app/lib/contracts/services.ts`, `fake-services.ts`, `conformance.ts` | `WalletBalance`/`LedgerEntry`/`UsageRow` become the v2 DTOs; the v1 read projection test goes here |
| `apps/app/tests/contracts/mutants.json`, `run-mutants.mjs` | the v2 mutant entries and the entry point that runs the v2 conformance module |
| `Makefile` | `api-mutants` and `console-mutants` gain the v2 mutant lists |
| `research/plan/01-contracts.md`, `06-database-map.md`, `08-contracts-v1-encoding.md` | link this appendix and `06a-database-map-v2.md`; fold §8's proposed rulings into 08 §10 with coordinator-assigned numbers |

Two constraints the wire-in must respect (no change was made for them now):

- `NormalizedRequestV2.wallet_id` must be written only from `ports.resolve_wallet`;
  no adapter may populate it from any other source.
- The console's grant and membership checks compare ISO timestamps as strings
  (`types.ts` `membershipPermits`/`grantPermits`), which is correct only for the
  canonical `Z` form the contracts serialize. A DTO reaching the console with
  `+00:00` or fractional-second variants must be normalized first (C accepts both
  forms per R59 (9), so the normalization belongs there).

A v1 read projection/upgrade test is part of that phase and is **not** satisfied by
`project_v1_usage` alone: that function proves the shape, while the projection test
must run against real pre-cutover rows produced by 0001–0005 (D1R's fixtures).

## 8. Rulings this encoding needed (proposed for `08` §10)

Numbered by the coordinator at the additive merge (2026-09-22): V1–V15 are **R64–R78** in
`08-contracts-v1-encoding.md` §10, in this order.

| # | Ruling |
|---|---|
| V1 | **A unit is a type, not a field.** `Credit`, `Usd` and `ProviderUsd` are sibling runtime classes on the Python side and disjoint brands on the TS side. A unit is never inferred from a field name, a magnitude or a regime label alone; a row states its regime and its unit, and the pair must agree. |
| V2 | **No conversion exists.** No function in either language maps one denomination to another, and none may be added without a product decision recording an exchange rate. The only unwrap is `raw(unit)`, which refuses a foreign unit. A v1 `PriceSnapshot` does not upgrade to a `RateCardSnapshot`; `upgrade_v1_price_snapshot_is_refused` is the named refusal. |
| V3 | **The wallet is resolved, never named.** `AuthContextV2` has no wallet field, `resolve_wallet` takes no request, and a wallet not owned by the credential's identity, and of the credential's audience's wallet kind, is `Forbidden` rather than a fallback. The kind is checked explicitly and before ownership: record validators do not protect a `model_copy(update=)`/`model_construct` object. An operator credential resolves no wallet at all. |
| V4 | **The ledger vocabulary is closed and has no transfer.** Provider-dev credit reaching a consumer wallet is prevented by the absence of an operation, not by a check. |
| V5 | **`settle` cannot see the present.** It takes the admission, the usage and a time — no directory, no clock, no rate. This is how "every request settles at its admitted rate and serving revision" is structural. |
| V6 | **Unpriced is unserveable.** A deployment with no approved active CREDIT card is `invalid_request`, never free inference. A card that prices another deployment or serving revision is refused rather than pinned. |
| V7 | **A private dev deployment is `not_found` to everyone else**, including a provider credential scoped to a different endpoint or a member of a different provider. A 403 would confirm the artifact exists. |
| V8 | **The grant key is the identity.** `(user_id, initial_signup_grant)`; campaign metadata is audit only and can never re-open eligibility. |
| V9 | **A nonzero legacy USD balance is a rollout hold**, expressed as a required field on the record. It is neither converted nor discarded. |
| V10 | **Totals are per unit.** No DTO, function or view offers a combined "total spend" across CREDIT and USD, and an empty history answers `{}` rather than zero in an unstated unit. |
| V11 | **Authorization is against the current grant.** No lookup takes an "as of" time, so a snapshot taken at capture cannot authorize a later read. Provider ownership never yields customer payload; `read_customer_content` is in no role's capability set. |
| V12 | **An empty scope fails closed** — the same reading v1's `OrgEntitlements.model_ids == ()` already has. |
| V13 | **Digest provenance is recorded, not assumed.** `ServingRevision.digest_source` distinguishes served bytes from a registry oid, and `runtime_image_digest` may be absent while the runtime is a moving tag. Without this, "measured on the pilot box" would later read as "confirmed upstream". |
| V14 | **The fixture base is generated.** `fixtures/v2/*.json` comes from `fixtures.py --write`; a hand-edited fixture fails `test_fixtures_v2.py`. The console reads those same files rather than keeping byte-identical copies, so there is nothing to diff at integration. |
| V15 | **Pins are immutable after acceptance.** `AdmissionPins` and `AdmissionV2` are frozen and every identity field is required. `model_copy(update=)` and `model_construct` are not permitted on admitted `AdmissionPins`/`AdmissionV2`: both bypass the validators that tie a card to its pins, so the wire-in phase and D2–D5 enforce the prohibition at the persistence boundary (the pin columns on `jobs` are never updated). |

## 9. Pending inputs this encoding depends on

| Id | What is pending | How the encoding copes |
|---|---|---|
| P-01 | The operator-approved Marlin CREDIT rate, rounding and failed-execution disclosure | `rate_card_marlin.json` carries `approved_by: "provisional - P-01 pending"`, so any run that used it is identifiable. Rates are 400 / 1,200 CREDIT per million: the 1:3 input:output ratio is carried unchanged from the v1 USD fixture (0.20 / 0.60) so this is a *unit* change and not a silent repricing, and the scale is set against the S2M video operating point — 23,500 input + 512 output tokens is 10.0144 CREDIT, so the 10,000 CREDIT grant buys ≈998 two-minute clips. The cost floor it must clear is `research/models/marlin2b/README.md`'s B300 interactive $0.0278–0.0565 per 1M output tokens (from `cloud-pricing.md`'s p6-b300 on-demand $17.802/GPU-hr). **No exchange rate is implied or implementable.** |
| — (W3/I2B) | Registry `.lfs.oid` equality for the Marlin artifacts; the vLLM runtime image digest | `digest_source: served_bytes` and an absent `runtime_image_digest`; `ServingRevision.image_is_pinned` is False until W3 pulls by digest. |

## Verification log

- 2026-09-22: Written by F2P's additive phase. Items 1–6 of the F2P brief are
  encoded and tested; item 7 (wire-in) is listed in §7 and is not done. Commands,
  counts and mutant results are in `research/plan/evidence/f/`.
- 2026-09-22: F2P independent review (fix_required at `c42b213`). B1: the three
  guards the additive phase deleted as redundant are restored — record validators
  do not run under `model_copy(update=)`/`model_construct`, so the wallet-kind and
  audience checks are load-bearing. V3 amended accordingly; V15 added. Wire-in
  notes on `wallet_id` provenance and canonical-`Z` string comparison added to §7.
