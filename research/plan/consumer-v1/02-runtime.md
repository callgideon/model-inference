# Runtime repair briefs — M5, M6, W5, G7, G8

Status: planned. Read [program 22](../22-consumer-v1-implementation.md), [contracts/data](01-contracts-and-data.md) and the assigned row in [tasks.json](../tasks.json). Existing M3/M4/W3/W4/G2/G3/G6B implementations remain the baseline. Reconcile newer evidence first. Slice estimates are 2–8 hours of engineering/review each; split larger work without weakening acceptance. A module is not integrated until its real dependencies pass.

## M5 — Restart-safe upload lifecycle

Own `apps/infrx-api/infrx/media/` upload adapters and corresponding tests. D10 owns SQL; coordinator owns gateway composition. Start from committed F2C and M3; integrate against D10. M6 begins after M5's shared-path changes land.

1. Replace process dictionaries as the authority for upload tickets, destination binding, ownership, digest, byte limits, completion and expiry. Use the F2C repository port and D10 adapter. Preserve the actual create → authenticated same-host PUT → complete → `upload_handle` protocol; E1C owns its client. Never treat a submitted MIME, duration or digest as verified metadata.
2. Make creation and finalization safely retryable with explicit conflict semantics. Handle a lost object-write acknowledgement, completed object with missing finalize acknowledgement, duplicate completion and interrupted PUT. Immutable destinations cannot be reused to mutate media after acceptance. Keep object I/O outside long DB transactions. Bound request bodies and concurrent upload work before buffering.
3. Wire resolution into live admission/preparation through the coordinator. Separate ticket expiry from liveness of a legitimately admitted job. A customer cannot revive an expired ticket by replay, attach another tenant's handle or bypass capability validation through an already uploaded object. Avoid a second cloud upload mechanism in this task.
4. Run tests against actual DB and S3-compatible storage across **different gateway processes**: create in A, PUT in B, complete in C, resolve in D; crash after each durable transition, duplicate/reordered calls, expiry and two tenants. Verify response DTOs at the mounted route, not only direct method calls.

Acceptance: uploaded bytes and their authorized handle remain usable after gateway replacement within the configured lifecycle; malformed/foreign/expired inputs fail with documented errors without new engine work or leaked credits. Memory use remains bounded under many finalized tickets.

Failure oracle: the former in-memory adapter fails the cross-process sequence; a forged ownership/digest or second finalize cannot replace accepted bytes. Include a snapshot of durable rows and object keys before/after recovery, sanitized of customer content.

## M6 — Durable retention, garbage collection and cache bounds

Own media collector/cache implementations and tests, sequentially after M5. Consume D10's durable eligibility/claim/lease/fence operations. I8 owns scheduling and dashboards. **Do not enable the current collector merely by adding a timer**: its process-local liveness view is insufficient.

1. Enumerate candidates with bounded pages from durable state. Protect queued, preparing, running and otherwise live references after a restart. Atomically establish deletion eligibility against attachment/rebinding; external deletes occur outside locks and are idempotent. Use tombstones/object generations so a delayed delete cannot remove a newly created object at the same key. A collector with an expired claim cannot authorize fresh destructive transitions.
2. Cover uploaded source, staged source, prepared video, payload envelopes, generated result objects and content-bearing database fields using the approved policy. Distinguish financial/request metadata and idempotency tombstones from content. Persisted `result_expires_at` is the result boundary; changes to today's config do not extend historical access. A failed deletion stays observable/retryable and cannot make expired content readable.
3. Bound process maps, preparation memoization and local disk caches. Recover the cache index after replacement, validate cache keys against model/processor/preparation versions, and preserve correctness when a cache is absent or full. Define high-water eviction and work admission under disk pressure; do not evict an engine's actively opened/prepared input. No claim that cold cache equals cold GPU.
4. Test two collectors, attach-versus-delete races, claim expiry, DB unavailable, lost object delete acknowledgement, partial multipart upload, retained terminal metadata, late worker completion, clock boundaries and a fully restarted runtime. Use controlled clocks in local tests; never shorten all customer TTLs to accelerate a hosted drill.

Acceptance: no live object is deleted; eligible content is eventually removed within the scheduled cleanup bound after recovery; financial evidence remains correct and content cannot be resurrected. Define and measure maximum collector batch/concurrency and pending-delete age.

Failure oracle: a deliberately local-only liveness view or missing reference lock must fail a real DB race test. An expired result returns the specified expired state even before physical deletion finishes. Run load+cleanup concurrently, not only an idle sweep.

## W5 — Durable execution readiness and bounded worker recovery

Own worker logic/tests. D10 supplies acceptance/readiness primitives; coordinator wires worker entrypoint. Keep the qualified Marlin engine configuration until a separately measured experiment warrants a change.

1. Consume durable execution eligibility for **both zero-media/text and media jobs**. Preparation/claim cannot start between admission commit and late capability/rate/attachment checks. Prefer completing required eligibility in the atomic admission transaction; if F2C selects staged finalization, implement its recovery/timeout protocol. Empty attachments are a completed empty manifest, not missing work.
2. Exercise every crash boundary: accepted-but-not-ready, ready before queue wakeup, preparing before object commit, engine submitted before journal cursor commit, cancellation/terminal races. Use existing durable fences and reconciliation; Valkey remains a wakeup/cache. Do not introduce an exactly-once engine promise that cannot be enforced; accounting/output identity remains once-only even when recovery needs a retry.
3. Separate temporary dependency failures from permanent unsupported/over-cap media. Permanent rejection must terminate and release/reconcile its hold once, rather than cycling through preparation leases forever. Bound waits/retries and expose actionable sanitized states. Test maximum supported input geometry/FPS/token budget, not duration alone.
4. Preserve startup warmup/readiness distinctions. Warmup is explicit operator work, cannot consume a customer's grant, and does not prove end-to-end readiness. Record model load, processor warmup and first valid customer request separately. Cache optimization changes need parity and resource evidence; exercise a cache miss in acceptance.

Acceptance: every accepted request reaches its correct terminal or explicitly bounded recoverable state, without executing before durable eligibility, publishing conflicting output or settling twice. Text-only canaries must fail under a deliberately removed readiness barrier.

## G7 — Truthful endpoint contract and persistent result expiry

Own gateway route/public discovery projection and route tests. D10 owns state; G8 owns operations; coordinator owns app composition/shared configuration. Start from F2C/G3 and integrate D10/M5.

1. Replace stale static public claims with one approved model capability/rate projection consumed by API discovery and A3. Enforce the same allowlist/limits that are advertised: 82-second current ceiling, bytes, validated media profile, output modes, unsupported parameters and configured admission concurrency. Do not advertise native input streaming, universal tool support, 120-second clips, concurrency 16 or ZDR based on stale JSON. Retain a safe internal default only if startup validates it against the approved release profile.
2. Resolve the canonical public ID and historical alias/rate mismatch explicitly (P-22). Ensure SDK examples, `/v1/models`, admission and accounting resolve the same served version and rate card. Preserve historical request identity and USD pricing records; no destructive alias rewrite. A disabled/unpriced public model fails closed before hold/engine work.
3. Carry persisted result expiry through terminal DTOs, polling, replay, recovery and browser query ports. Use one authority rather than recomputing expiry from current settings. Document not-ready/expired/not-owned/not-found states without exposing another tenant's existence. Metadata visibility and content availability have separate lifetimes.
4. Prove bounded overload/body drain, 429/503 and retry guidance, cancellation and client disconnect behavior for sync/SSE/async. Do not regress completed large-body drain repairs: reuse the valid E1B cells and repeat on affected code. Same-host authenticated uploads must match M5/E1C fixtures; SSRF and tenant controls remain enforced.

Acceptance: an external client can discover only the qualified model profile, submit by documented modes and retrieve/replay results exactly within the persisted lifecycle. Compare rendered docs/discovery against admission tests in CI. Unsupported capability claims are a test failure, not copy-only review feedback.

## G8 — Headless CREDIT activation and account operations

Own `infrx/operations/`, CLI/operations tests and headless examples. Coordinate any shared CLI edit; D10 owns SQL, A1 remains the grant primitive, G6B is reused. This package proves the customer account path without requiring the App.

1. Expose repeatable trusted operations for verified-individual onboarding, personal consumer account resolution, API key issue/revoke, approved card publication, suspension and reasoned/audited reconciliation. Validate exact CREDIT DTOs and personal identity, not arbitrary first membership. The one-time 10,000 CREDIT entitlement is per verified individual across callbacks and org changes; provider development allocation remains separately zero-initialized.
2. Provide an idempotent bounded pilot transition procedure: inventory current USD requests/holds, stop new affected admission if required, drain/reconcile, pin the new card and regime, then exercise grant→key→request→hold→settle→usage→revoke. Never convert USD balances into credits implicitly. Historical replay settles under its original policy and cannot switch units when the active regime changes.
3. Require operator-supplied **approved** launch rates (P-01). Current 400/1200 CREDIT values are provisional, and the USD test grant is not the public promotion. Allow explicitly labelled fixture rates only in local tests. A live public activation command must fail on an unapproved/missing card or unresolved transition, with a useful dry-run report.
4. Test simultaneous signup grants, lost callback acknowledgement, revoked/suspended keys, zero/insufficient funds, timeout, cancel, unknown usage and repeated reconciliation. Return typed operation conflicts; do not catch every exception and silently retry financial changes. Hide plaintext keys after their single issue response; lost issue response requires an explicit rotate/revoke workflow, not a stored plaintext recovery feature.

Acceptance: a fresh verified individual can perform actual metered inference and view exact available/reserved/spent credit state through trusted headless queries, then revoke the key and receive denial. Two identities cannot cross-read/mutate or multiply one entitlement. Before/after ledger/hold sums reconcile under concurrency.

## Handback for every runtime lane

Include base/head SHA, exact paths, compatibility and wiring requests, new migration/port contracts, focused commands/results, real-service evidence and a failed-then-passed regression. List outstanding tests plainly. Integration owner repeats the complete request on the merged SHA; a lane's fake-backed test pass does not replace E3C/E4C.
