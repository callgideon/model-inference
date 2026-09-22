# Target persistence map for D1R/F2P

This is the product-v2 target. Existing D1 migrations 0003–0005 implement USD/org wallets and must remain unchanged. D1R adds migrations after F2P freezes revision 2 types. D owns all physical migrations. Read [architecture](../platforms/01-architecture.md) and [credits](../platforms/02-credits.md). Consumer records carry trusted consumer org identity; provider resources carry provider org identity; jobs link both. User-owned wallets retain a protected personal-org billing binding. Never accept ownership from untrusted body fields. Existing organization/profile/key tables remain authoritative. Inspect actual remote schema before generating migrations; keep migration history in its current physical path initially.

| Logical relation | Keys, fields and constraints | Required index / lifecycle |
|---|---|---|
| price_versions / rate_cards | Immutable ID, deployment applicability, unit CREDIT, approved decimal input/output rates, meter rules, author/approver/effective time | Active published lookup; snapshots immutable; USD costs separate |
| wallets | Wallet PK, kind consumer/provider_dev, unit CREDIT, protected owner_user_id or owner_provider_org_id according to kind, ledger_total, reserved_total, revision; decimals20,8 | One initial consumer wallet per user; separate zero-funded provider dev wallet with explicit operator allocations; checked ownership XOR, no client-selected wallet or transfer |
| signup_grants | Unique user_id + initial entitlement, wallet FK, verification ref, amount CREDIT, campaign metadata, unique ledger operation | Entitlement survives callback/backfill retries; campaign version/org change does not reset it |
| credit_ledger (existing USD) | Preserve IDs, denomination and history unchanged | Historical statements/reconciliation; no automatic numerical conversion |
| wallet_ledger (new CREDIT) | Wallet/operation unique, request link, actor/reason/type, amount, unit and timestamps | Append-only, paginated by wallet/time/id; no cross-unit sum |
| credit_holds | Request unique, wallet/consumer org, unit CREDIT, maximum amount, held/settled/released/unknown state, timestamps | Wallet/active, unknown/reconcile_after; no second active hold per request; retain separate old USD regime if already implemented |
| capacity_reservations | request/scope/kind unique, org/key, prep/inference/journal bytes, active/released | Active scope counters updated under deterministic lock order; reconcile rows/counters |
| jobs | UUID request PK, opaque handle unique, org/key, state, immutable input/result refs, model/price/policy versions, absolute deadlines, outcome, terminal timestamps | org/created/id; state/queue/deadline; state checks and immutable tenant identity |
| attempts | job/generation PK, worker, DB lease expiry, publication marker, retry ordinal, timings | Expiring leases; one active generation per job; atomic claim and checked mutations |
| staged_media / uploads | opaque owned ID, digest, bytes, storage ref, profile, state, expires_at; final refs immutable | org/handle; expiry and active-reference-aware GC; no public arbitrary-key resolver |
| idempotency | org/operation/key unique, canonical payload digest, request/feedback/grant ref, expires_at | No active-job expiry; expired tombstone yields explicit response instead of silent re-execution |
| stream_chunks | job/generation/sequence PK, event type, payload, bytes, committed_at, expires_at | Ordered bounded cursor scan; state/lease verified within append transaction |
| usage_events (existing) | UUID request unique for new regime, numeric HTTP status retained, outcome text, usage certainty, rate/serving snapshot, explicit accounting regime, charged_credits; historical cost_usd retained | org/time/id pagination; unique terminal settlement version; never infer unit from numeric amount |
| outbox | stable event ID PK, aggregate ID, kind/version, bounded metadata/ref payload, available_at, claim/ack/retry state | Pending/available index; at-least-once delivery; acknowledgments cannot erase source truth |
| feedback | stable ID, org/request, author/channel/role, rating/correction, calibration membership, idempotency ref | org/request/time; authored provenance server-only; PG acceptance before 201 |
| consent_history | org, version, actor, trace/evaluation settings, effective_at, revoked_at | Current version indexed; immutable audit records; no inferred evaluation consent |
| judge_budgets / reservations | org/budget period PK; limit, reserved/settled; run unique reservation and price snapshot | Transactional budget lock; outstanding and ambiguous reservations included |
| judge_runs / samples | stable run/sample IDs, consent/rubric/model versions, submit state/intent, provider ID, maximum/actual cost, scores | Unique submission intent and run/sample/rubric result; no ambiguous automatic resubmit |
| callback_destinations / deliveries | owned registered HTTPS destination, encrypted signing-key reference/version; event/destination unique, attempt state, next attempt, dead-letter reason | Ready-delivery scan; stable event ID; no inference retry coupled to delivery |
| org_capabilities / provider_memberships | Explicit provider capability, protected membership role, immutable audit trail | Consumer owner cannot self-assign provider/operator role |
| models / model_versions / serving_versions | Provider FK in addition to display label, immutable artifact/adapter/tokenizer/processor/prompt/runtime hashes and schema refs | Provider-scoped queries; registered supported runtimes; versions never edited in place |
| endpoints / deployment_revisions / catalog_listings | Provider-owned dev/prod endpoint, immutable serving/config revision, visibility and publication state, active listing/rate link | Private dev excluded from public catalog; atomic alias change affects future admission only |
| data_access_grants | Grantor/source consumer, recipient provider, resource/model scope, categories, purposes, expiry/revocation, version and audit | Current authorization checked before read/export/egress; copying data does not strip source restrictions |

Dataset/evaluation/training lineage relations are planned in F3/D7/D8 below and rollout in D9. They are follow-on Lab milestones; do not create those tables as an App release requirement. D authors initial provider/registry additions needed by A3/L2/L3 in coordinated migrations.

## Mutation boundaries

Expose narrow service/RPC operations for admit, prepare, claim, heartbeat, append, terminalize, cancel, grant, accept_feedback, reserve_judge and record_submission. A transaction may call internal helpers but no public caller can independently manipulate holds or terminal rows. Use prepared parameters and fixed search paths for privileged functions; restrict execution grants explicitly. Grant operators permission to authorized actions, not unrestricted table updates.

Browser/member access is read-only to allowed tenant views plus narrowly authorized nonfinancial settings/key actions. Platform operator role is derived from protected profile state. Service clients still verify tenant/principal at the entry boundary; possession of a service credential does not justify accepting caller org IDs. Test both SQL-role/RLS behavior and server-side authorization.

## Migration acceptance

Seed upgrade fixtures with existing organizations, members, keys, positive/negative USD ledger history and usage. Assert identical historical USD totals/values; separate CREDIT wallets and one initial 10,000 grant per eligible verified user, even under backfill/callback races; no duplicate `models.limits`; old numeric status still readable. Add entitlements/suspension/limit/provider fields with restricted column grants. Exercise fresh DB, baseline upgrade and any remotely implemented schema upgrade. Record compatibility window, old-job drain and rollback procedure; no destructive downgrade of accepted job/ledger data. Nonzero legacy balances require a named transition policy before affected-account cutover.

Store bounded canonical refs rather than large trace/video payloads in PG. Keep accounting records according to the existing audit policy; trace retention13mo does not authorize deletion of ledger/usage/idempotency evidence needed for reconciliation. D1 must document relation retention and storage sizing in its migration evidence before deployment.

## Verification log

- 2026-09-20: Proposed relations, keys and role boundaries mapped to D1–D6. No migrations generated or applied by the documentation task.

- 2026-09-21: Amended for separate consumer App/provider Lab, individual signup credits and independent release gates; see the platform-split review. Implementation evidence on the other system remains unverified here.

## D7–D9 follow-on Lab persistence map

These are planned follow-on migrations under the same D ownership, not changes required for the App pilot. [F3 and detailed briefs](13-lab-improvement-handoffs.md) provide the contract and task acceptance. Keep original migration history unchanged; reserve future sequence numbers at integration. D8 and D9 can design concurrently but cannot publish competing migration numbers or skip merged-tree upgrade checks.

| Owner | Relations / keys | Required database proof |
|---|---|---|
| D7 | sources/source-purpose links; dataset_versions/manifests/samples/membership/split assignments; harness revisions | Provider-scoped keys, immutable published manifests, unique version/content references, grouped split constraints and current source authorization |
| D7 | evaluation_runs/cases/attempts/results; checkpoint_receipts/subscriptions; durable outbox | Unique run/case/evaluator result, lease-generation checks, exactly-once logical acceptance under replay, explicit partial/cancel/restricted state, receipt/subscription dedup |
| D8 | annotations/reviews; annotation_batches/items; external_pipeline_runs/intents/receipts/checkpoints; shared budget links | Append-only provenance; protected reviewer fields; one budget reservation per logical operation including unknown submissions; current purpose enforcement |
| D9 | release_policy_versions/decisions; experiment assignments/cohort references | Protected public policy activation, compare-and-swap transitions, stable declared assignment, job serving/rate pins never rewritten by rollback |

Never store large video/trace/training bundles inline or copy source rights into an unrevocable boolean. Dataset content may become inaccessible while bounded audit identity remains; retention for evidence and actual payload deletion are separate. Cross-provider joins and signed exports must be tested through both actual RLS roles and service authorization. Use a representative large dataset/query fixture to inspect plans and prove scoped pagination rather than only singleton-row tests.
