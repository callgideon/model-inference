# Consumer App completion — existing tasks, refined acceptance

Status: planned. These briefs refine C0/C3A/A2/A3/U1R/U2/U3/I2A/I3/E3A/E4 and add U4; they do not create a second App. [The manifest](../tasks.json) is authoritative. **Dispatch feature work after accepted E4C BACKEND-READY**, per [program 22](../22-consumer-v1-implementation.md). Reuse existing pages/components/auth/SDK/contracts. Read [App spec](../../platforms/03-app-spec.md) and [credit policy](../../platforms/02-credits.md). Each numbered deliverable should be a reviewable 2–8 hour slice or split further.

## Shared consumer boundary

Launch journey: discover Marlin → sign up/verify → receive the one-time individual 10,000 CREDIT grant → create key → submit finite video through documented API → retrieve output/status → inspect usage/credits → revoke key. The App is a control console; uploading a large robotics corpus through a new browser playground is not a v1 dependency. The resumable dataset CLI is covered separately.

Navigation exposes Models, API Keys, Usage, Credits, Docs and supported Settings; request detail is reached from Usage. Keep provider trace exploration, evaluation/training, team pooling, paid billing and dedicated hosting out of consumer navigation and protected routes. Do not merely hide a link to an unguarded provider route. Preserve legacy trace code for the later V1M/Lab move, without requiring Lab implementation to launch. Unsupported controls are unavailable with truthful copy, not fake toggles that save only locally.

Production reads must never fall back to fixture account data. Network/auth/config failure is an explicit unavailable/retry state. Browser bundles receive no privileged credentials, DB service keys or hosting secrets. Log only safe identifiers/status; never API-key plaintext, result content, prompts or signed media URLs.

## C0 — Real consumer context and read port

Own server query adapters and trusted consumer context; coordinator owns shared layout wiring. Start F2C; integrate D10/G8 and existing D1R. A/U lanes consume this port.

1. Resolve a verified individual and their intended personal consumer account server-side. Do not select the first membership or infer consumer ownership from an operator/provider org. Preserve account regime and exact CREDIT units; keep legacy USD explicitly separate. A signed-in user without completed onboarding receives a typed state.
2. Replace `consoleContext = null`/fixture projections with the real Supabase/PostgREST query path for model availability, balances/holds, paginated usage, key metadata and owned requests/results. Only expose required columns. Result access uses persisted expiry and D10 authorization through a trusted query/service port, never a browser-held customer API key.
3. Test real RLS/RPC grants for anonymous, unverified, individual A/B, revoked/suspended, provider and operator contexts. Include empty/large histories, boundary pagination, same timestamps and currency precision. Use indexed bounded queries; no full ledger scan per page.

Acceptance: sign-in identifies the correct account, production usage/credits come from actual durable records, and another tenant's identifiers return no data. Query errors cannot display a plausible fake balance or a transient zero as confirmed funds.

## C3A — Trusted consumer actions

Own shared server actions/API adapter; C0 precedes shared-path work. Integrate D10/G8. Keep mutations out of individual pages.

1. Key create/revoke, onboarding/grant retry, supported privacy settings and operator actions delegate to approved narrow ports. Authorize each action with current context/role; protect cookie/session mutations against cross-site requests. Never trust client-supplied org, role, amount or audience.
2. Idempotency and conflict responses cover double-click, connection loss and concurrent callbacks. Key plaintext is returned once; a lost response requires revoke/rotate, not plaintext storage or redisplay. Refresh read models from committed state, without optimistic financial success before acknowledgement.
3. Exercise actual DB role boundaries and callback retries. Privileged reasoned operator actions are distinct from consumer actions, with immutable audit identity/idempotency. Do not duplicate grant SQL in the browser/server framework.

## A2 — Public verified onboarding and recovery

Own auth screens/callback/recovery UX and focused browser tests; coordinate shared callback implementation with C3A. Existing invitation-only flow is the starting point.

1. Implement signup, verify-email, resend, sign-in, password recovery and expired-link states against the actual configured auth service. Avoid enumeration in error messages. Apply target-environment abuse/rate limits and callback/origin allowlists (P-05).
2. Issue the promotion through the trusted idempotent individual entitlement after verification. Reopened callbacks, multiple sessions and new org memberships never multiply grants. Unverified users cannot issue a funded consumer key or bypass verification by calling the action directly.
3. After verification, show actual balance and a clear key/docs next step. Handle incomplete onboarding recoverably. Keyboard/focus/form-label/error states and mobile layouts must work. Test a fresh email identity in staging; local fake mail alone cannot close E4.

## A3 — Models, pricing and working API documentation

Own consumer Models/Docs pages and examples. Consume G7's shared approved projection, not a second model/rate constant. Reuse existing design/components.

1. Present the served Marlin version, actual finite-video capabilities, accepted transport/limits and approved CREDIT price/rounding/failure policy. Public model ID, aliases and the SDK request resolve identically. Keep legacy USD accounting out of public credit examples. Do not publish provisional rates as approved.
2. Provide copyable authenticated text, upload/video, SSE output, async poll/result, resume/idempotency and revoke examples that are executed in tests. Show exact supported OpenAI-style fields; do not imply every OpenAI feature works. Distinguish streamed text output from native live-video input.
3. Document duration/geometry/byte constraints, retry/backpressure, request IDs, persisted result lifetime, content retention and support path honestly. Replace stale 120-second and “never stores content”/ZDR copy. Capture-off is not deletion of serving payloads.

Acceptance: a new external developer can use only these docs and their key to complete a real in-cap video request and retrieve its result. CI compares examples/capabilities against route conformance; E4 repeats the hosted path.

## U1R — CREDIT usage and balance views

Own usage/credit pages and shared money display helpers. Integrate C0/C3A/D5/D10; land helpers before U2/U3/U4 consume them.

1. Display exact CREDIT available, reserved and spent with the agreed precision; show separately labelled legacy USD history where present. Never turn decimal strings into floating-point financial totals. The promotional grant is once per individual, with no invented monthly refill/expiry.
2. Usage lists correlate request status, model/version, timestamps, units and settlement. Pending/unknown usage is explicit; zero charge is not assumed on uncertainty. Pagination/filter/date boundaries produce consistent totals and remain indexed for large histories.
3. Cover low/zero funds, active reservations, canceled/failed/expired requests, adjusted ledger entries and delayed settlement. Provide a request-detail link and useful empty/unavailable states. Verify UI totals against durable ledger queries under concurrent requests.

## U2 — Keys and supported settings

Own UI for key issue/copy/revoke, metadata and supported settings; use C3A actions. Clearly expose one-time plaintext and lost-key rotation behavior. A revoked key must fail actual inference admission, including across caches/replicas within the documented revocation bound. Privacy wording reflects real serving retention. Do not enable trace-sharing/annotation consent until its complete permission and deletion path exists. Test keyboard/mobile access and secret redaction in logs, URL, analytics and error reporting.

## U3 — Minimal operator controls

Own authorized operator views, separate routes and tests. Keep minimal approved rate publication, reasoned credit adjustment/grant, suspension and health/reconciliation visibility. Use audited idempotent operations; no direct arbitrary balance editing. Consumers/provider users cannot reach actions via crafted URLs or RPC. Require reason and show committed outcome/conflict; prevent duplicate changes on retry. This is not the provider Lab.

## U4 — Owned request detail and result lifecycle

Own consumer request detail page/components and tests, plus route integration requested from coordinator. Start from F2C/U1R; integrate C0/C3A/G7.

1. Show safe request ID/model/time/status, waiting/running/terminal phases, exact usage/charge state and sanitized actionable failure. Expose owned output within persisted expiry, with copy/download only when authorized. Do not build a provider trace tree or display other tenants' media.
2. Poll with bounded backoff and cancellation on navigation; handle auth loss, backend outage, not-ready, expired and unknown usage distinctly. A retry button must not silently submit a second paid inference. Reuse request/idempotency guidance for a deliberate retry; content expiry retains permitted metadata but removes content access.
3. Test two tenants, shared/guessed links, role changes, expiry during an open page, a changed retention configuration, reconnect and page reload. Avoid client cache resurrection of expired content; server/CDN responses use appropriate private/no-store behavior. Do not persist raw results in browser analytics/local storage.

Acceptance: a customer can understand and retrieve their actual request result and charge, including failure/expiry; cross-tenant and expired-content probes fail. UI and API agree on authoritative expiry and state.

## E3A, I2A, I3 and E4 — Integrated browser delivery

**E3A:** extend the existing App suite to run browser + actual auth/DB/object/runtime adapters, with controlled engine locally. Required journey: fresh individual verify → one grant despite repeated callback → create key → external text/video sync/SSE/async → request detail/usage/balance → low funds → revoke; repeat isolation checks with a second user. Exercise refresh, retry, expired result, rate rejection, accounting uncertainty and provider-route denial. A mocked C0 port cannot pass APP-LOCAL. Reuse E3C backend evidence and rerun affected cases on the merged SHA.

**I2A:** deploy the App against the accepted backend, with explicit environment separation, configured domains/auth callbacks/email, secret isolation, private result caching policy and reproducible release identity. Do not duplicate the GPU deployment. Verify public docs point to the correct endpoint and preview branches cannot accidentally use production write credentials. Reuse I8 artifact/rollback discipline.

**I3:** run combined operational checks, auth/credit cutover, browser error monitoring, alert delivery and rollback of App plus backend-compatible contracts. Maintain the approved content-retention and resource budgets; no payment stack is added.

**E4:** repeat the complete hosted journey with real verified email and actual Marlin hardware from an external client/browser. Confirm published limits/rates, tenant denial, once-only grant, exact reconciliation, revocation, expiry, monitoring and rollback. Record staged launch population, owner, decision, support path, abort/rollback criteria and actual release identity. Reuse backend capacity proof only when unchanged; repeat the capacity cells affected by App/DB load. Public launch requires all enabled-path inputs and evidence, not only a successful page build.

## Handoff

Every lane supplies test commands/results, real adapter wiring, screenshots only where useful for product verification, unresolved input and reviewed commits. Coordinator merges shared layout/navigation/actions once. Keep the old App suite and accessibility checks green; add tests at new authorization/accounting/result seams rather than duplicating implementation details.
