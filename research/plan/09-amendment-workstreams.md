# Implementation briefs for the platform split

**Current priority (2026-09-22):** [Marlin backend first](18-marlin-backend-first.md). E3B/I2B/I3B/E1B/M4/W4/E4B separate endpoint readiness and measured optimization from the later App browser/deployment gates; G6B provides protected headless operations. Use the updated [fresh-session handoff](16-fresh-session-handoff.md) and manifest for dispatch.

**Current scheduling overlay:** [complete plan](12-complete-build-plan.md), manifest v4 and [fresh-session handoff](16-fresh-session-handoff.md). Marlin App launch is the immediate scope; compatible brief details below remain binding. S2M adds the Marlin profile; Lab M2–M4 are now decomposed separately.

Use with [task manifest v3](tasks.json), [impact mapping](08-platform-split.md), [execution protocol](03-execution-protocol.md) and [test oracles](04-verification.md). The original module briefs supply unchanged detailed algorithms. Each task below inherits its track's ownership restrictions. Start dependencies require reviewed code/fixtures; new F2R/F2P/D1R gates require acceptance evidence; additional integration dependencies may be mocked during development but require real evidence before handback.

The [wave-2 revision handoffs](11-wave3-revision-handoffs.md) add F2R/F2P/D1R/C0/I0/E2R/G1R/V1M/U1R and override older dependencies here. These are bounded deliverables. If the remote implementation makes one larger than a normal 2–8 hour work unit, the coordinator subdivides it in the session record before assignment, preserving its acceptance oracle. No estimate is a completion promise. Several slices share an owner and should not run concurrently on the same files.

## S1 — reconciliation (implemented in the wave-2 audit)

Owner: coordinator. Paths: plan evidence and shared integration wiring only. No dependency.

1. Inspect remote git status, worktree list, recent commits, applied local/test schema and existing handbacks without reading secret values. Capture base SHA and active owner claims.
2. Map all 45 original IDs using the impact table. Preserve completed behavior/tests; identify wrong-unit or wrong-app work explicitly.
3. Assign one F2 contract revision owner, one D migration owner and one C action owner. Record package extraction and UI-move sequencing.
4. Publish an amendment acknowledgment and committed integration base. Do not schedule retired IDs or claim their successors finished without evidence.

Completion record: [10-wave2-platform-audit.md](10-wave2-platform-audit.md); F2R/F2P now own executable contract adoption. Original done criteria: SPLIT-CONTRACT record identifies consumer/provider boundaries, individual grant, CREDIT unit, each active owner and the next eligible tasks. Existing F2 work is reviewed and amended, not blindly restarted.

## A1 — individual signup grant

Owner: D. Start D1R; integrate D5. Paths: D-owned state/RPC implementations and financial tests; all migrations stay with D.

1. Add a verified-identity eligibility operation and immutable user-to-wallet/personal-org binding using the revised schema.
2. Implement one transactional entitlement + CREDIT ledger grant; campaign metadata changes cannot reset eligibility.
3. Reuse the same operation for auth callback retry, first login and existing-user backfill. Keep direct browser financial writes denied.
4. Race retries/backfill, denied/unverified identities, provider membership changes and attempted cross-user wallet access.

Done: CREDIT-GRANT/IDENTITY/UNITS pass against PostgreSQL; exactly 10,000 once per individual, legacy USD unchanged. Publish RPC/API fixture and migration compatibility evidence for A2.

## A2 — consumer signup and onboarding

Owner: A for auth/onboarding views and callback plumbing, C for business server actions. Start F2P; integrate A1/C3A.

1. Add public signup and verification states using current auth conventions; preserve recovery/login redirect validation.
2. After verified identity, invoke the authoritative onboarding/grant action and display persisted state. Show pending/error retry safely; never simulate a balance in client code.
3. Implement onboarding through model selection/key creation/first request; ensure credits are labeled and non-recurring.
4. Document the required auth-provider/site/callback configuration change separately from code. Coordinate live settings through I2A; local work does not toggle production signup.

Done: APP-JOURNEY browser test with new verified identity, callback retry and invalid verification; no duplicate grant or enumeration; desktop/mobile/keyboard states checked. Existing login/recovery still work.

## A3 — catalog and published credit rates

Owner: A views/docs, C repository/action boundary, D schema. Start F2P; integrate D1R/G1R/C0.

1. Use minimal provider/model/serving/deployment/listing/rate records; allow operator-assisted seeds until Lab is ready. D owns their migration, A owns fixtures/content.
2. Show only accessible published deployments, credit rates, capabilities and limits. Coming-soon content cannot supply an apparently live snippet.
3. Generate examples from validated capability fixtures; standard API fields remain standard, credit details explicit extensions.
4. Exercise changed rates/aliases during queued work, private dev exclusion, missing-price rejection and two-model central wallet use.

Done: CREDIT-RATE/SPLIT-CONTRACT and catalog portion of APP-JOURNEY pass. Production rates remain a release input; test fixture rates are labeled synthetic.

## D6F and D6J — split feedback from judge coordination

Owner: D; reference the original [durable-state brief](handoffs/D-durable-state.md).

**D6F:** after D5, implement the original durable feedback transaction/outbox and provenance checks. Request ownership comes from durable records even when no trace projection exists. FEEDBACK-ACK and DUR-RLS are its complete integration oracles; no judge dependency.

**D6J:** after D5, integrate L2 purpose grants and implement the original consent/budget/submit-intent protocol. Budget is explicitly Lab/provider USD with a named payer; never implicitly a consumer CREDIT wallet. Consent snapshot AND current permission required before egress. Preserve ambiguous-submit quarantine. JUDGE-BUDGET and LAB-ACCESS must pass with real DB contention.

Schema additions for both are D-owned and may be additive; use separate feature enablement so feedback does not require judge configuration.

## G4U, G4F and G4T — split route adapters

Owner: G; reference the original [gateway brief](handoffs/G-gateway.md). All start after G1.

- **G4U:** integrate M3 only. Create/complete owned uploads and return bounded safe envelopes. MEDIA-SEC/DUR-RLS prove auth, ownership and immutability. No trace/judge dependency.
- **G4F:** integrate D6F. Validate feedback/idempotency, pass server-derived author identity, acknowledge only durable acceptance. FEEDBACK-ACK proves projection independence and spoof rejection.
- **G4T:** integrate T2I/T3/C2. Export only owned, unexpired trace data through the content-access service; do not expose an unrestricted provider query route here. TRACE-TENANT/LAB-ACCESS prove cross-org denial and revoked/expired references.

Each adapter can be handed back independently, with its routes enabled only when its dependency service exists.

## T2I and T2F — split trace and feedback projections

Owner: T; reference the original [trace brief](handoffs/T-traces.md).

**T2I:** after T1, integrate D5. Ship inference envelopes/content to existing planned projection stores, deduplicate stable identities, carry serving/rate/source-policy versions and loss state. Test fsync crash recovery, duplicate shipping and tenant/content isolation. No D6J dependency.

**T2F:** after T2I, integrate D6F. Project feedback outbox independently, preserving author/method/role and calibration authority; late feedback joins by durable request identity. Test retry/loss/duplicate effects and projection lag. Do not require or invent a judge result.

T3 then applies retention to both sources. Their deployment can wait for Lab; enabling capture earlier still requires all applicable bounds/retention gates.

## C3A, C3F and C3L — split server actions

Owner: C; all start after C0; reference the original [console-service brief](handoffs/C-console-services.md). App and Lab views never reimplement financial or permission rules.

- **C3A:** integrate D5. Consumer keys/settings and protected operator grants/suspension stay in App. Wire verified onboarding to A1 when available without adding A1 as a dependency for unrelated actions. Verify member/operator boundaries, restricted columns, idempotent grant adjustments and no provider mint authority.
- **C3F:** integrate D6F/L2. Consumer own-feedback adapter remains narrow; Lab review actions additionally check provider purpose grants. Server-derived provenance prevents customer labels masquerading as calibration truth. Test both audiences and revoked sharing.
- **C3L:** integrate D6J/J2/L2. Lab-only judge/budget/calibration operations, current permission checks and bounded pagination. Test forged provider/org/model IDs, expired data grants and duplicate submit actions.

Extract shared internals only via a coordinator-owned package change. Importing one app's server action into the other is not a shared service boundary.

## L1 — Lab shell and deployment boundary

Owner: L with coordinator-owned workspace wiring. Start F2P; integrate L2.

1. Create the actual Next.js app using established UI conventions and explicit dev port/origin. The README scaffold is not implementation.
2. Add provider-session access guard, provider workspace selection for authorized memberships and a minimal shell; consumer onboarding stays in App.
3. Consume shared contracts/UI via controlled extraction. Add independent test/lint/build targets and secret/cookie/callback configuration; don't broaden cookie scope to make auth appear to work.
4. Test consumer-only user denial, correct provider membership selection and direct-route/server-action access.

Done: Lab builds independently, App still builds, LAB-ACCESS passes, no provider functionality relies on an App route being mounted.

## L2 — provider roles and data-access policy

Owner: L policy/repository module, D migrations. Start F2P; integrate D1R.

1. Add provider capability/membership authorization without reusing the consumer owner or operator bit. D authors constraints/RLS/RPCs.
2. Implement named provider operations and grant evaluation for model, data category, purpose, recipient and expiry/revocation.
3. Return redacted own-deployment operational aggregates by default. Individual trace/content/export/evaluation/training access requires its applicable grant.
4. Create two-consumer/two-provider fixtures, with a user belonging to both products. Exercise direct DB roles and privileged server entry points.

Done: LAB-ACCESS proves cross-provider denial, no customer identity leakage in default aggregates, purpose separation and immediate denial on revocation. Grant history and source identifiers are available to C/J/T adapters.

## L3 — model versions and endpoint lifecycle

Owner: L control services; D schemas, G/W runtime adapter changes remain separately owned. Start L2; integrate G1R/W2/A3.

1. Wrap A3's operator-managed registry with authorized provider registration/configuration operations. Validate supported artifact/schema/runtime, immutable digests and ownership.
2. Create private dev deployment revisions with endpoint-scoped credentials and a distinct operator-funded provider_dev CREDIT wallet/internal rate card. No individual signup grant applies to it; no arbitrary uploaded code or unmetered preview bypass.
3. Implement validation/publication proposal/promotion/rollback state transitions and audit. Reuse shared model/rate resolution; don't create a parallel catalog.
4. Prove alias switch during a queued job leaves its serving/rate pins unchanged; reject cross-provider mutation and dev visibility leaks.

Done: LAB-PUBLISH/LAB-ACCESS pass with a real registry and fake engine, followed by staged actual deployment evidence before release. Authenticated control operations are separate from consumer `/v1` credentials.

## L4 — provider model/deployment workflows

Owner: L UI; server services L/C. Start L1; integrate L3.

1. Implement Overview, Models, Deployments and Access/Settings with usable empty/error states.
2. Show pinned model/runtime/schema/rate identities and dev/prod visibility. Let providers propose public changes and operators approve through their authorized action boundary.
3. Provide health and redacted aggregate performance; do not build customer trace drilldowns before the access-controlled V work.
4. Exercise assisted model registration → dev smoke → publication → App discovery → rollback, and unauthorized variants.

Done: LAB-PUBLISH/ACCESS plus CONSOLE-FLOWS pass. Successful visual state is derived from actual deployment/control records, not button-local optimistic claims.

## I2A and I2L — independent deployments

Owner: I. Start I1/F2P; full integration dependencies in manifest. Original [infrastructure brief](handoffs/I-infrastructure.md) still supplies runtime recovery requirements.

**I2A:** deploy reproducible consumer app and runtime with verified signup configuration, actual credit rates, migrated accounting and metered endpoints. Default optional capture/judge off if their gates are incomplete. Test new-user journey, secret isolation, auth callbacks and runtime readiness. Record legacy-account transition. No Lab UI dependency.

**I2L:** separately configure Lab build/origin/auth allowlist and protected provider control services. Exercise provider denial, dev/prod publication, health and rollback. Lab outage must not interrupt App inference. Before enabling LAB-M1, extend this deployment evidence to trace/judge workers, storage grants, egress budgets and retention alarms.

Local plans/tests do not automatically authorize provisioning resources, public publication or paid external jobs. Use the session's actual environment authorization and preserve accepted jobs during rollback.

## E3A, E3L and E5L — closed-loop verification

Owner: E; test harness after E2, actual dependencies per manifest. Record raw logs, pinned revisions and failure injection, not only screenshots.

**E3A:** run the full consumer journey against real PostgreSQL and a controlled engine, concurrent individual grant/holds, unit migration, media/security, retry/journal/cancellation and key revocation. Disable Lab/optional analytics and prove it still completes. Enabled optional features bring their own suites. Hand back APP-LOCAL; E4 adds real GPU/operations evidence for APP-PILOT.

**E3L:** run two-provider permissions, private dev exclusion, registry validation, production publication/rate snapshots, App discovery and rollback. Deny provider controls to consumer keys and consumer-data access without grants. Hand back LAB-OPERATE integration evidence; I2L adds staging deployment evidence.

**E5L:** after E3L, run authorized trace capture/search/review/evaluation through all actual services. Revoke grant mid-queue, expire content, drop projection/capture and time out judge submission. Prove no unconsented egress, duplicate paid submit, customer-wallet charge or consumer outage. Hand back LAB-OBSERVE, distinguishing dry-run from any separately assigned live judge run.

## Handback format

Use [evidence template](evidence/README.md). Include old/new task mapping, commit/base SHA, changed paths, contract/schema version, fixtures and exact test output, integration dependencies still missing, rollout/rollback effects, and ownership for follow-up. Do not mark mock-only UI/backend work integrated. Larger Lab roadmap packages require new briefs before sessions claim them as implementation tasks.

## Backend-first extraction and later reuse (2026-09-22)

[Eight new backend briefs](18-marlin-backend-first.md) remove the UI dependency from endpoint readiness. A1 is shared D-owned entitlement and G6B is the protected headless adapter. E3B owns backend service/security/durability proof; E3A adds the later browser/signup journey. I2B owns runtime deployment; I2A adds App deployment/auth callbacks. I3B owns backend recovery/restore; I3 adds consumer-flow recovery. E4B owns final model/runtime/load evidence; E4 adds consumer launch acceptance and reruns backend checks affected by subsequent changes. Reuse artifacts and common scripts, not parallel implementations.
