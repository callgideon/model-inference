# Persistence additions for contracts v2 (appendix to `06-database-map.md`)

Appendix to the [persistence map](06-database-map.md), written by F2P so D1R can
write migrations without inventing field names. **F2P owns no SQL**: nothing here
is a migration, a filename or a reservation of a sequence number — D alone writes
those. What this document does is bind each v2 record to the relation it persists
in and name the constraint that carries each contract invariant, so the type and
the table cannot disagree.

Read with [`01a-contracts-v2-map.md`](01a-contracts-v2-map.md) and the generated
`apps/infrx-api/infrx/contracts/fixtures/v2/map.json`, which is the authority for
field names and is checked against the live records.

Existing migrations `0001`–`0005` remain unchanged, and the existing USD relations
keep their values. Everything below is **additive**.

## 1. Record → relation

| v2 record | Relation | Keys and the constraint that carries the invariant |
|---|---|---|
| `WalletRef` | `wallets` | PK wallet id; `kind` in (`consumer`,`provider_dev`); `unit = 'CREDIT'` CHECK; ownership CHECK as an exclusive-or by kind — consumer requires `owner_user_id` **and** `personal_org_id` and forbids `owner_provider_org_id`, provider_dev the reverse; UNIQUE `(owner_user_id)` where kind = consumer, so one initial wallet per individual; UNIQUE `(owner_provider_org_id)` where kind = provider_dev; CHECK `reserved_total <= ledger_total` and both `>= 0`; `numeric(20,8)` |
| `CreditLedgerEntry` | `wallet_ledger` | PK entry id; UNIQUE `operation_id` (the idempotency key); `unit = 'CREDIT'`; CHECK by kind: `signup_grant` requires a consumer wallet and amount `= 10000.00000000`, `operator_allocation` requires a provider_dev wallet and amount `> 0`, `inference_debit` requires amount `< 0` **and** a non-null request link; every other kind forbids the request link; `actor` NOT NULL and non-blank. Append-only: no UPDATE/DELETE grant, TRUNCATE refused by trigger |
| — | wallet total | The total moves **only** by an AFTER INSERT trigger on `wallet_ledger`, in the same transaction, for every writer (R59 (7) applied to the CREDIT ledger). Reconciliation stays the detector, not the writer |
| `SignupGrant` | `signup_grants` | **UNIQUE `(user_id, entitlement)`** and nothing else in the key — campaign metadata is an ordinary column, so bumping it cannot re-open eligibility; FK to `wallets`; `verification_evidence_ref` NOT NULL and non-blank; UNIQUE `ledger_operation_id` FK to `wallet_ledger.operation_id`, so the grant and its money are one operation |
| `LegacyUsdStatement` | derived from existing `credit_ledger` (USD) | A read projection, not a new table. No new column on the historical rows; CHECK belongs in the service: a nonzero balance must be reported with `rollout_hold` true |
| `ProviderBudget` | `provider_budgets` | PK budget id; `unit = 'PROVIDER_USD'` CHECK; never joined to `wallets`, and no view sums it with a CREDIT total |
| `RateCardSnapshot` | `rate_cards` | PK `rate_card_version`; `unit = 'CREDIT'` and `meter = 'tokens-v1'` CHECKs; rates `>= 0`; FK to `deployment_revisions` **and** `serving_versions`; `status`, `approved_by` NOT NULL; immutable after insert (UPDATE refused). A new rate is a new row: "changing a rate affects newly admitted requests, never prior holds" is the absence of an UPDATE path |
| `ServingRevision` | `serving_versions` | PK serving version id; FK model/model_version/provider org; `model_commit` 40 hex CHECK; weight shard digests as an ordered array, at least one; tokenizer and chat-template digests; `digest_source` enum; `runtime_image_digest` NULLABLE **on purpose** (a moving tag has none — W3 fills it) and `runtime_image_ref` NOT NULL; immutable after insert |
| `DeploymentRevision` | `deployment_revisions` | PK revision id; FK endpoint/provider/serving version; CHECK a `public` revision is `prod` and in (`proposed_public`,`active`,`draining`) — a dev or failed-validation revision can never be public; immutable after insert. Alias movement is a row in the listing/alias relation, never an UPDATE of a revision |
| `CapabilityRecord` | column of `serving_versions` | Bounded JSON with the fields of `07-api-contracts.md`; validated by the service against the record, not by a free-form JSON column |
| `AdmissionPins` | columns of `jobs` | `model_id`, `requested_model`, `deployment_revision_id`, `serving_version_id`, `rate_card_version`, `policy_version`, `accounting_regime`; all NOT NULL for a CREDIT-regime job and **never updated** after acceptance. This is where "in-flight work never changes revision" lives in SQL |
| `AdmissionV2` / `SettlementV2` | `jobs` + `usage_events` + `credit_holds` | one active hold per request (partial UNIQUE on request where state = held); at most one settlement per request; the settlement's `rate_card_version` must equal the job's pinned one (composite FK or CHECK against the job row), so a settlement at a different rate cannot be written at all |
| `UsageRecordV2` | `usage_events` | `accounting_regime` NOT NULL; CHECK the regime and unit agree (`legacy_usd`↔USD, `credit`↔CREDIT); CHECK a `credit` row has `rate_card_version` and `serving_version_id` and no `price_version`, and a `legacy_usd` row the reverse. Historical `cost_usd` is retained unchanged; `charged_credits` is a separate column |
| `AuthContextV2` | existing key/org relations + `audience` | `audience` on the key row, NOT NULL, defaulted to `consumer` for existing keys at cutover; a `provider_dev` key requires a provider org **and** an endpoint scope (CHECK). No request-supplied column anywhere in this path |
| `ProviderMembership` | `provider_memberships` | UNIQUE `(provider_org_id, user_id)` for the current row; `role` enum; `granted_by` NOT NULL; `revoked_at` NULLABLE with the one-way NULL→value transition of R59 (8); a consumer owner cannot self-assign it (column grant, not application check) |
| `AccessGrant` | `data_access_grants` | PK grant id; `version` monotone per `(grantor_org_id, recipient_provider_org_id)`; categories and purposes as enum arrays; `retention_days` CHECK 1..90; `effective_at` NOT NULL; `expires_at`/`revoked_at` NULLABLE with the one-way revocation transition; queried by the **current** predicate at every read/export — no "as of" parameter exists in the contract, so none should exist in the RPC |
| `DataAccessPolicyRef` | `consent_history` + policy version column on `jobs` | The pinned `policy_version` is a job column and is never updated |

## 2. Where each oracle becomes a database fact

| Oracle | The database fact |
|---|---|
| CREDIT-GRANT | UNIQUE `(user_id, entitlement)` plus the single transaction that creates/locks the wallet, inserts the entitlement, appends the ledger row and lets the trigger move the total. A replay returns the existing row; a race loses on the unique index rather than inserting twice |
| CREDIT-IDENTITY | the ownership exclusive-or, the per-user wallet uniqueness, and the absence of any transfer operation in the ledger's kind CHECK |
| CREDIT-UNITS | the regime/unit CHECK on `usage_events`, the unchanged historical USD columns, and the absence of any view or RPC that sums across units |
| CREDIT-RATE | immutable `rate_cards` rows, never-updated pin columns on `jobs`, and the settlement's rate-card equality against the job row |
| CREDIT-SPEND | one active hold per request, `reserved_total <= ledger_total`, at most one settlement per request, wallet-level locking for grant/admit/settle |
| LAB-ACCESS | `provider_memberships` role column with restricted grants, `data_access_grants` queried by the current predicate, and no unconditional customer-content read path for a provider role |
| SPLIT-CONTRACT | `audience` on the key row, the dev/public CHECK on `deployment_revisions`, and separate consumer/provider read surfaces |

## 3. What D1R still has to decide

F2P deliberately does not:

- reserve migration filenames or sequence numbers after `0005`;
- choose whether `charged_credits` is a new column on `usage_events` or a new
  relation joined to it (both satisfy the contract; the CHECK moves with it);
- choose the physical spelling of the ordered weight-shard digests
  (`text[]` versus a child relation);
- decide the cutover default for `audience` on existing keys beyond "it must be
  explicit and it must be `consumer`";
- say anything about RLS role names, which R59 (4) already governs.

Existing accounts with a nonzero legacy USD balance remain a **rollout hold** for
that account; the contract records the hold, and the migration must not resolve it
by picking a rate.

## Verification log

- 2026-09-22: Written by F2P's additive phase, from the committed v2 records and
  fixtures. No SQL generated, no filename reserved, nothing applied.
