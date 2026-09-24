# Consumer v1 — contracts and durable data

Tasks **F2C, D10**. Status: planned. Parent: [program 22](../22-consumer-v1-implementation.md). Read the implemented F2P/D2–D5/A1 adapters, migrations 0001–0018 and audit RV-02/03/05/11 before designing. This is an additive correction to the current ports, not a new parallel state system.

## F2C — freeze the corrective contracts

**Start:** S3 and F2P. **Owner:** F, with coordinator approval of shared changes. **Paths:** `apps/infrx-api/infrx/contracts/`, `apps/app/lib/contracts/`, both contract/conformance suites and the corresponding contract docs. No SQL or feature-route implementation in this lane.

Deliver four reviewable slices, approximately 2–8 hours each; split further if the existing contract surface requires it:

1. **F2C.a — lifecycle records.** Encode durable upload constraints/state/expiry/finalized reference, immutable attachment completion including an empty set, and content-lifecycle/tombstone semantics. Name the authority for each timestamp and version. Keep opaque handles and tenant-safe not-found behavior. Specify narrow upload/liveness/deletion repository ports; no raw SQL or object keys to browsers.
2. **F2C.b — terminal/read consistency.** Carry persisted `result_expires_at` through Python/TS terminal/status DTOs and adapter fixtures. Define every outcome: pending, succeeded with result, terminal failure, unknown usage/held reservation, expired or unavailable result. Check old client decoding and previous-runtime migration compatibility; optional-to-required rollout must be explicit.
3. **F2C.c — public capability and accounting identity.** Define a reusable published-model projection containing canonical ID/aliases, immutable serving revision, actual modality/limits/supported parameters, output streaming vs video-input support, rate version/unit and serving-data retention. Distinguish availability from declared capability. Define alias resolution and pricing once: CREDIT pins the resolved deployment/card; retained USD resolves through its own USD price identity, never by converting the CREDIT card. Record an amendment for P-22 with a compatibility test for each existing alias.
4. **F2C.d — versioned acceptance fixtures.** Add representative multi-process/restart/expiry fixtures and failure cases for each port. Publish a changed-field/consumer matrix for D/M/W/G/C/E, a migration rollout contract and fixture hash before feature branches use it. Preserve v1 history and compatibility needed by existing callers.

**Acceptance:** both languages serialize the same exact units, opaque IDs, timestamps and refusal vocabulary; every existing implementation consumer of a changed contract is listed. An unexpected/missing required field fails deterministically. The public projection cannot advertise a limit/capability that the serving profile refuses.

**Failure oracles:** missing/empty attachment completion confused; expired result rendered available; caller-selected tenant/rate/expiry; USD parsed as CREDIT; published private/deactivated model; unknown video/tool feature silently advertised; old terminal records decoded with an invented expiry. Use semantic tests, not snapshots alone.

**Handback:** commit with F2C fixture/port revisions, compatibility notes and each owner's wiring requirement. No broad shared-package move is necessary for this release.

## D10 — durable lifecycle, read authority and operational seams

**Start:** F2C, D5. **Integration:** E2C supported PG harness. **Owner:** D alone. **Paths:** `apps/app/supabase/migrations/`, `apps/infrx-api/infrx/state/`, D adapter/race/permission tests. Migration numbers are allocated by the coordinator from the latest applied main; **0001–0018 are immutable**. Do not assume 0019 is still free in a later session. D7–D9 are task IDs for later Lab work, not an ordering constraint on this migration.

### D10.a — atomic execution eligibility and uploads

- Persist upload ticket constraints, original owner, timestamps, destination identity, digest/size, finalization state and immutable source reference. Reload after any process restart. One `(owner, handle)` means the same bytes; same-content retry is idempotent, changed bytes/constraints conflict.
- Make acceptance checks and attachment-completion authority durable for **zero and nonzero media**. Preferred design: the admission transaction validates the trusted serving/card policy and records the exact normalized source manifest plus completion marker atomically with job/hold/outbox. Preparation claims require that committed marker. The marker proves the accepted source manifest and admission checks are complete, not that expensive media decoding already happened. Keep fetching/probing outside database locks and expensive preparation after bounded durable admission; do not move unpaid/unbounded downloads into the admission transaction or its preflight.
- If the reviewed design retains two phases, it must persist recoverable inputs, prevent preparation before the second phase, and implement bounded recovery/cancellation for a crash in the gap. A boolean marker without a recovery path is insufficient. Publish the choice in the contract amendment before M/G/W integration.
- Do not use local `by_job` or `payloads` dictionaries as authority. Retrying after a gateway dies must find the same job and either finish a safe attachment or release/fence it according to a declared terminal outcome.
- Lock order must remain compatible with existing wallet/job/capacity ordering. Pin trusted configured serving/card expectations, not fields supplied in the public request.

### D10.b — safe cleanup authority and content minimization

Create or extend a **durable, paginated lifecycle repository** over uploads, attached source/prepared objects, staged payloads, result records and cache eligibility. Required semantics, regardless of table names:

| Operation | Durable invariant |
|---|---|
| Enumerate candidates | Expired/unreferenced candidates come from persisted state; restart does not turn an unknown object into a deletable object |
| Claim deletion | A bounded leased/fenced claim with immutable object identity; concurrent sweepers do not race ownership |
| Recheck/reference | Admission/attach and delete are mutually safe at the same serialization boundary; a referenced object cannot be deleted underneath a newly accepted job |
| Mark tombstone | Once deletion is committed, new use is refused or re-staged to a new object identity; delayed delete must never remove newly recreated bytes at the same key |
| Delete/reconcile | External delete is idempotent; crash before/after delete is recoverable; retention failure does not delete billing/audit history |
| Expire result | Persisted expiry controls logical access independently of object deletion lag |

Cover content-bearing columns too: normalized request text, request/result bodies and staged request JSON cannot be exempted just because they are in PostgreSQL rather than S3. Separate retained metadata/digests/accounting/idempotency tombstones from expired content. Keep enough information to return a known terminal/expired outcome without regenerating inference. The design must respect existing replay guarantees and approved retention rather than deleting the whole job row.

Conservative orphan discovery must persist first-seen/eligibility or otherwise prove a safe grace window across processes and restarts. Object listing age or an empty process dictionary is not proof of no durable reference. Never delete outside the environment's exact bucket/prefix. An unavailable authority, ambiguous ownership or live/renewable lease means **retain and report**.

### D10.c — result/consumer reads and pricing seams

- Return persisted expiry and versions in real `TerminalOutcome`/owned-read adapters. Apply logical access rules consistently across explicit result fetch, status and idempotent sync recovery; metadata remains readable when content expires.
- Add narrow, cursor-paginated consumer queries needed by C0/U4: trusted individual/personal-wallet context, own jobs/usage/holds, settled debit and result availability. No ClickHouse or optional trace dependency.
- Complete the reviewed P-22 resolved USD price mapping without changing prior rows or converting balances. Preserve the original requested model name alongside immutable resolved identity.
- Make concurrent replay of the same reconcile operation id return its original audited result or a typed conflict; raw `23505` is not the public outcome. Test two connections, not sequential calls.
- Supply I8 the dedicated-runtime-role privilege list. Browser roles retain column-scoped key permissions; adding columns must not broaden their writable audience/scope.

### D10.d — migration and concurrency evidence

On real PostgreSQL and the supported Supabase/PostgREST image:

1. Apply the migration to a copy of the latest schema with both USD history and CREDIT jobs/holds. Reapply if the migration convention requires it; compare counts, exact sums, identity bindings and permissions before/after.
2. Exercise previous and new runtime adapters during expand/contract transition. If compatibility is impossible, declare the maintenance/drain requirement and prohibit old-runtime rollback after the boundary.
3. Run two-connection races at admission/attachment, finalize/finalize, attach/delete, complete/cancel, expire/read and reconcile/reconcile. Include test-clock equality boundaries.
4. Verify role matrix and denial paths with actual roles/JWTs, not service-role-only testing.
5. Crash after DB commit and before response; repeat with a fresh adapter/process; compare job/hold/outbox/ledger identities.

**Acceptance:** no accepted executable job lacks the durable ready manifest; zero-media jobs obey the same barrier; every valid upload step survives restart; only one finalization/settlement/audited operation wins; deletion cannot invalidate accepted live work; persisted expiry wins over later configuration. Financial metadata is unchanged by content scrubbing.

**Failure oracles:** remove ready predicate; omit an upload constraint; bypass tenant join; skip live-reference check; delete a renewed claim; re-create a tombstoned object with the same identity; infer expiry from current config; let a browser write audience/provider scope; duplicate reconcile audit. Each must produce an observable failing invariant in a deterministic local test.

**Evidence:** new migration IDs/hashes, target schema/image, applied/rollback boundaries, role matrix, race schedules, reference/deletion state before/after, exact financial reconciliation and real adapter conformance. D10 does not deploy or activate CREDIT alone.

## Shared decisions that must be explicit before integration

- Where admission/ready completion becomes atomic and how old in-flight jobs are recovered.
- Which persisted reference protects each content object and how attach/delete races are serialized.
- What survives content expiry for accounting and idempotency, and what the caller sees on late retry.
- Exact UTC/time authority, expiry boundary and physical deletion lag objective; no silent default changes to existing jobs.
- Backfill cost/locking and rolling runtime compatibility. Source object size can be large; database transactions must remain bounded.

Implementers can resolve routine table/port naming within these constraints. Product retention commitments, public prices and rollout limits come from the recorded inputs; do not invent them to unblock a test.
