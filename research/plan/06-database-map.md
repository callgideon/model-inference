# Persistence map for D1/F2

This map is a proposed migration design, not existing schema. D1 owns physical migrations; F2 freezes serialized types first. All tenant records carry `org_id`; server-derived identity is never accepted from untrusted body fields. Existing organization/profile/key tables remain authoritative. Read actual schema before generating migrations.

| Logical relation | Keys, fields and constraints | Required index / lifecycle |
|---|---|---|
| price_versions | Immutable ID, model revision, decimal input/output rates, token rules, effective time | Model/effective-time lookup; referenced snapshots immutable |
| wallets | org PK, ledger_total, reserved_total, revision; available derived; decimals20, 8 | Row lock serializes all monetary mutations; reconcile with ledger/holds |
| credit_ledger (existing) | Preserve IDs/history; add unique operation ID, request link, operator/reason/type metadata | org/time/id pagination; append-only; historical rows not recharged |
| credit_holds | request unique, org, maximum amount, held/settled/released/unknown state, timestamps | org/active, unknown/reconcile_after; no second active hold per request |
| capacity_reservations | request/scope/kind unique, org/key, prep/inference/journal bytes, active/released | Active scope counters updated under deterministic lock order; reconcile rows/counters |
| jobs | UUID request PK, opaque handle unique, org/key, state, immutable input/result refs, model/price/policy versions, absolute deadlines, outcome, terminal timestamps | org/created/id; state/queue/deadline; state checks and immutable tenant identity |
| attempts | job/generation PK, worker, DB lease expiry, publication marker, retry ordinal, timings | Expiring leases; one active generation per job; atomic claim and checked mutations |
| staged_media / uploads | opaque owned ID, digest, bytes, storage ref, profile, state, expires_at; final refs immutable | org/handle; expiry and active-reference-aware GC; no public arbitrary-key resolver |
| idempotency | org/operation/key unique, canonical payload digest, request/feedback/grant ref, expires_at | No active-job expiry; expired tombstone yields explicit response instead of silent re-execution |
| stream_chunks | job/generation/sequence PK, event type, payload, bytes, committed_at, expires_at | Ordered bounded cursor scan; state/lease verified within append transaction |
| usage_events (existing) | UUID request unique for new regime, numeric HTTP status retained, outcome text, usage certainty, price snapshot, exact cost | org/time/id pagination; unique terminal settlement version; historical provenance |
| outbox | stable event ID PK, aggregate ID, kind/version, bounded metadata/ref payload, available_at, claim/ack/retry state | Pending/available index; at-least-once delivery; acknowledgments cannot erase source truth |
| feedback | stable ID, org/request, author/channel/role, rating/correction, calibration membership, idempotency ref | org/request/time; authored provenance server-only; PG acceptance before 201 |
| consent_history | org, version, actor, trace/evaluation settings, effective_at, revoked_at | Current version indexed; immutable audit records; no inferred evaluation consent |
| judge_budgets / reservations | org/budget period PK; limit, reserved/settled; run unique reservation and price snapshot | Transactional budget lock; outstanding and ambiguous reservations included |
| judge_runs / samples | stable run/sample IDs, consent/rubric/model versions, submit state/intent, provider ID, maximum/actual cost, scores | Unique submission intent and run/sample/rubric result; no ambiguous automatic resubmit |
| callback_destinations / deliveries | owned registered HTTPS destination, encrypted signing-key reference/version; event/destination unique, attempt state, next attempt, dead-letter reason | Ready-delivery scan; stable event ID; no inference retry coupled to delivery |

## Mutation boundaries

Expose narrow service/RPC operations for admit, prepare, claim, heartbeat, append, terminalize, cancel, grant, accept_feedback, reserve_judge and record_submission. A transaction may call internal helpers but no public caller can independently manipulate holds or terminal rows. Use prepared parameters and fixed search paths for privileged functions; restrict execution grants explicitly. Grant operators permission to authorized actions, not unrestricted table updates.

Browser/member access is read-only to allowed tenant views plus narrowly authorized nonfinancial settings/key actions. Platform operator role is derived from protected profile state. Service clients still verify tenant/principal at the entry boundary; possession of a service credential does not justify accepting caller org IDs. Test both SQL-role/RLS behavior and server-side authorization.

## Migration acceptance

Seed upgrade fixtures with existing organizations, members, keys, positive/negative ledger history and usage. Assert identical historical totals and values after upgrade; new wallets zero; no duplicate `models.limits`; old numeric status still readable. Add entitlements/suspension/limit fields with restricted column grants. Exercise migration in a fresh DB and from current schema. Record compatibility window and rollback procedure; no destructive downgrade of accepted job/ledger data.

Store bounded canonical refs rather than large trace/video payloads in PG. Keep accounting records according to the existing audit policy; trace retention13mo does not authorize deletion of ledger/usage/idempotency evidence needed for reconciliation. D1 must document relation retention and storage sizing in its migration evidence before deployment.

## Verification log

- 2026-09-20: Proposed relations, keys and role boundaries mapped to D1–D6. No migrations generated or applied by the documentation task.
