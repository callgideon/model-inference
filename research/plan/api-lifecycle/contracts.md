# Target API boundary and product contracts

**Proposed additions unless marked existing.** Route names here are an implementation contract to finalize in AP-00; they are not claims that these paths work today. Keep existing public inference wire contracts compatible. Evolve existing Lab paths additively, rather than building a second control service or duplicating its domain logic.

## 1. Ownership

`apps/app` and `apps/lab` own layout, navigation, accessible forms, formatting, local draft state, loading/error presentation and displaying typed API responses. A Next.js server action may remain a thin session-cookie/CSRF forwarding adapter. It must not query product tables, invoke product Supabase RPCs, calculate authoritative balances/eligibility, mint keys, build financial records, choose runtime placement, authorize content, run a judge or reconcile a workflow.

`apps/infrx-api` owns every product operation, including account context, membership, key lifecycle, grant claims, list filtering/cursors, deployment and publication, capture/consent, judge/review and administrative actions. PostgreSQL remains the transactional authority. Existing functions and Python services should be reused behind scoped backend repositories, with the current actor explicitly and safely established; do not replace RLS with an unrestricted backend database client.

The identity provider remains Supabase Auth. Add a narrow FastAPI **auth facade** for app-supported sign-in/signup/sign-out/refresh/recovery/callback operations; use the provider's supported flows, not a custom password database. Next may retain framework cookie handling and redirect/PKCE transport. No signup grant, profile read or membership RPC belongs in this transport layer. Do not leave identity-provider access as an undocumented exception to the user's all-API requirement.

The existing inference and Lab control deployments can remain separate processes/origins sharing Python contracts/domain modules. The data plane must remain available if Lab or the web apps fail. Backend engine credentials, DSNs, judge secrets and object credentials never pass through either frontend. Authenticated responses and one-time secrets use no-store; upstream errors and request bodies are not logged verbatim.

### Required enforcement

- Static boundary check on **both** apps: no product `.from`, `.rpc`, SQL/DB driver, admin Supabase client, business key generation, credit settlement or private object-store client. Inventory nontrivial wrappers as well as literal calls; grep alone is insufficient. Keep any auth transport allowlist small and explicit.
- Generated, versioned TypeScript clients from checked FastAPI schemas; consumer and provider bundles import only their allowed clients. Handwritten view models may format responses but cannot create eligibility or scientific verdicts.
- All DTOs have explicit request and response schemas, named operations, status codes and authentication. Public docs need not be enabled; CI-generated OpenAPI artifacts must represent every mounted route in its enabled composition.
- Product feature availability comes from FastAPI: `configured`, `disabled`, `unavailable`, `unknown`, with safe reason codes and verification time. Capability and feature availability are separate. A failed fetch is never a zero count or a disabled feature.
- Decimal quantities cross the wire as strings with a unit. Backend issues opaque cursors scoped to actor/workspace/filter/sort; UI only passes them back.
- Actor identity comes from a verified session or correctly scoped machine credential. Never accept `actor`, operator status, role or an arbitrary customer identity in a request as authority.

## 2. Shared wire behavior

All mutations accept a stable `Idempotency-Key`, scope it to actor/workspace/action, and store a canonical input hash plus outcome durably. Same key/body replays the same outcome; same key/different body returns 409. Define retention explicitly per operation. Existing inference replay remains 24h; initial new control-operation receipts also remain queryable for at least 24h and retain an audit reference afterward. Resource identity must not depend on an in-process cache.

Long operations return **202 + Location** pointing to a readable operation. Minimum fields:

```json
{
  "operation_id": "uuid",
  "kind": "deployment.create",
  "state": "queued",
  "phase": "capacity",
  "resource_id": "uuid",
  "created_at": "ISO-8601 UTC",
  "updated_at": "ISO-8601 UTC",
  "retry_after_s": 2,
  "error": null
}
```

Operation states: `queued`, `running`, `succeeded`, `failed`, `cancel_requested`, `cancelled`. Domain deployment state is a separate field; do not force existing deployment records into this enum. Phase/progress describes observed work; percentage/ETA may be null. Operations use database leases and fences, survive process restart and have bounded timeout/reconciliation. Cancellation stops new work and reconciles resources already created; it is not a promise of instant physical teardown.

New control errors use `{error:{code,message,request_id,retryable,field_errors}}`, with sanitized messages and optional operation/resource reference. Preserve existing Lab `{refusal:reason}` and other old formats during client migration via explicit adapters. Keep OpenAI-compatible errors on `/v1/*`. Standard outcomes: 401 authentication; 403 permitted disclosure of denied action; 404 absent or inaccessible resource; 409 stale version/idempotency/state conflict; 410 expired authorized result; 422 invalid input; 429 limit/budget admission with retry policy; 503 required service unavailable. Never return 200 empty when a required read failed.

## 3. Complete screen-to-API map

Auth facade routes to define explicitly: POST `/auth/v1/sign-in`, `/auth/v1/sign-up`, `/auth/v1/refresh`, `/auth/v1/sign-out`, `/auth/v1/recovery`, `/auth/v1/password`; GET `/auth/v1/callback` and `/auth/v1/availability`. Sign-in accepts email/password; signup additionally accepts the configured CAPTCHA token; recovery accepts email/CAPTCHA and only an allowlisted redirect. Callback handles the IdP code/state/PKCE exchange. Password update requires the appropriate authenticated recovery/current session. Session tokens use the established secure channel and never enter URLs, caches or logs. Verify CSRF/session rotation, generic errors and closed signup before replacing forms. Framework callback transport may remain thin while the exchange and product context are API-owned.

Paths without `existing` below are target contracts. List APIs return `{data,next_cursor}` with a server maximum page size (default 25, maximum 100 unless an existing stricter limit applies). Detail IDs remain opaque; UUIDs are not access controls.

| App surface / action | FastAPI operation(s) | Authority / important response |
|---|---|---|
| Login, signup, verification/callback, refresh, logout, recovery/password update | `/auth/v1/*` action-specific typed routes; GET auth availability | Existing IdP; signup availability/CAPTCHA; generic account-enumeration-safe outcomes; no raw password persistence |
| App account shell, welcome, settings | GET `/console/v1/me`; GET `/console/v1/capabilities` | Verified individual, active account, suspension, allowed actions; feature availability |
| Welcome grant/retry | POST `/console/v1/signup-grant/claim` | Server verifies individual, campaign and email; exactly one 10,000 CREDIT grant across accounts/retries |
| API keys | GET/POST `/console/v1/keys`; DELETE `/console/v1/keys/{id}` | Own consumer keys only; secret returned only at successful creation; revocation allowed when suspended |
| Models and Docs | Existing GET `/v1/models`; GET `/console/v1/capabilities` | Published capabilities, actual model IDs, prices, retention and availability; static prose/code formatting can remain local |
| Usage list and detail | GET `/console/v1/requests`; GET `/console/v1/requests/{id}`; GET `.../{id}/result` | Own history; API filters/cursors; execution and settlement states separate; unavailable/expired outcomes |
| Request feedback | POST `/console/v1/requests/{id}/feedback`; existing machine POST `/v1/feedback` | Own request; durable feedback identity; no browser-created API key to impersonate a user |
| Credits | GET `/console/v1/credits`; GET `/console/v1/credit-ledger`; GET `/console/v1/legacy-statement` | Exact spendable/reserved/settled fields, CREDIT vs legacy USD kept separate |
| Teams/current account information | GET `/console/v1/account/members` | Existing individual-account model; no invented shared credit ownership or unimplemented team invitations |
| Privacy/settings | GET `/console/v1/data-use`; PUT `/console/v1/keys/{id}/capture`; POST/GET `/console/v1/data-grants`; DELETE `/console/v1/data-grants/{id}` | Grantor-controlled capture/purposes/scope/expiry; no retroactive trace creation |
| Dedicated page | GET `/console/v1/capabilities` | Informational/unavailable until hosting product exists; no inert “Deploy” purchase button |
| Operator admin | GET `/operator/v1/accounts`, `/wallet-drift`, `/unknown-usage`, `/audit`; POST `/credit-adjustments`, `/suspensions`, `/key-revocations` | Operator authority from server; mandatory reason/idempotency; reuse audited domain operations |
| Lab sign-in/shell/workspace/settings | Auth facade; GET `/lab/v1/workspaces`; GET `/lab/v1/capabilities?provider_org_id=`; GET `/lab/v1/workspaces/{id}/members` | Current membership and capabilities; content permission separate |
| Provider onboarding/member changes | POST `/operator/v1/providers`; POST/DELETE `/lab/v1/workspaces/{id}/members[/user]` | Operator creates approved initial provider; existing admin member policy, no self-elevation |
| Lab overview | Existing control aggregates/models/deployments + capabilities | Section-specific reads, no health inferred from zero requests |
| Models and import | GET/POST `/lab/v1/control/model-projects`; artifact operations in §4; GET/POST `.../model-projects/{id}/revisions` | Provider ownership, artifact and schema/profile pins; first model supported |
| Deployments | Existing GET control deployments; POST control deployments; GET detail/operations/readiness; developer key routes in §5 | Actual allocation/check/endpoint receipts and current allowed actions |
| Publication | Existing POST control proposals; operator approval/rejection/rollback in §6 | Provider admin proposes; operator decides; expected listing version |
| Requests | Existing GET `/lab/v1/traces[/{id}]`; extend projected metadata/filters only when backed | Scope, current grants, available content/evidence; no raw private storage URL |
| Request review | GET `/lab/v1/traces/{id}/feedback`; POST `/lab/v1/traces/{id}/reviews` | Existing review domain; separate immutable human label/provenance |
| Judge | Configs/budgets/runs/calibration APIs in §8 | Grant/payer/rubric/model pins, no-media/abstain and spend receipts |
| Datasets/import/detail/export | Existing `/lab/v1/providers/{provider}/datasets/*` | Preserve immutable version, preview/import/requeue/derive/export/part contracts; enable objects/worker |
| Evaluation/checkpoints/experiment detail | Existing `/lab/v1/evaluations/{catalog,runs,experiments,subscriptions}`, launch/cancel; add scoped experiment/run detail/report reads if existing listings cannot supply them | Listings backed by actual database; workload pins; exactly-once accounting; no inferred comparison |
| Annotations/training | Existing `/lab/v1/pipelines/*` labels/reviews/adjudications/imports/exports/training/checkpoints/teacher batches | Existing HTTP workflows; real worker/object/config dependencies; native hosting of trainers remains absent |
| Releases/optimizations | Existing `/lab/v1/releases`, `/lab/v1/optimizations`, release proposals | Frozen qualified evidence and operator decision; no provider traffic override |

Account settings not implemented today should remain read-only unless a separately specified mutation exists. Every rendered **action** must map to one documented operation. Static headings, examples and local navigation do not require artificial database APIs.

## 4. Artifact and model-project onboarding

Use these operations under `/lab/v1`, with verified `provider_org_id` context:

| Operation | Request | Response / checks |
|---|---|---|
| POST `/control/model-projects` | `name`, stable `slug`, task/use-case description | 201 model project ID; provider-scoped uniqueness; no serving claim |
| POST `/artifacts/uploads` | Model project, file manifest (`relative_path`, bytes, SHA-256, media type), card metadata | Upload ID, bounded expiry, chunk/multipart instructions. Storage keys are allocated by backend |
| Upload parts | API-issued scoped object PUT URLs or bounded API upload route | Backend grants byte transport; frontend holds no storage credentials. Presigned object PUT is the intentional data-transport exception, not a business-logic bypass |
| POST `/artifacts/uploads/{id}/complete` | Received part/ETag list, declared manifest hash | 202 verification operation; server rehashes stored bytes, checks all files/limits, rejects missing/mismatched/extra paths |
| POST `/artifacts/imports` | Model project, allowlisted source repository + immutable commit, optional **secret reference**, manifest | 202 import operation; credentials never in card/response. No arbitrary server-side URL fetching |
| GET `/artifacts/{id}` and GET `/operations/{id}` | — | Verified manifest/status, safe validation errors, compatibility report and audit provenance |
| POST `/control/model-projects/{id}/revisions` | Verified artifact ID, harness ref/digest, input/output schema refs, approved preprocessor/runtime profile | Immutable serving revision or actionable unsupported outcome; no engine allocation yet |

Verify **all** weight shards plus tokenizer, config, generation config, processor, preprocessor and template identities. Card descriptions are untrusted metadata, not executable instructions. Initial supported format/profile is the measured Marlin model; reject unsupported serialized code/remote-code execution rather than silently enabling it. Credentials, path traversal, decompression limits, source network policy and interrupted-upload cleanup require explicit tests. Use source commit and verified file hashes, not mutable `main` or an unverified single shard digest as artifact identity.

For the already hosted Marlin artifact, add an **operator API adoption operation** that records verified existing bytes and their provenance into the approved owner workspace. Do not copy the public model into another workspace or alter its owner to satisfy a test. A new internal test project may import the same licensed public artifact under its own distinct identity. Adoption is not proof that upload/import works; acceptance must exercise at least one byte/source import path independently.

### Lab UX amendment

Replace the empty-workspace first action with **Add model**: (1) model project/card, (2) pinned repository or upload, (3) validation/progress, (4) supported serving setup. Show “Artifact verified” only after server confirmation. The existing four-field Register form is only **New revision of an imported model** and stays unavailable when prerequisites are absent. It must never serve as the first-model upload wizard.

## 5. Deployment, private inference and operations

- GET `/lab/v1/hosting-profiles`: supported runtime/image/architecture/precision/GPU shape, limits, modes, eligibility and evidence. Initially one approved Marlin + L40S + pinned vLLM profile. “Recommended” needs measured evidence; no invented automatic vLLM/SGLang comparison.
- POST `/lab/v1/control/deployments`: serving revision, hosting profile, dev environment, input/output limits, warm policy, `max_replicas=1` initially, explicit idle/expiry policy, approved provider resource budget. Return 202 operation and deployment IDs.
- GET `/lab/v1/control/deployments/{id}` and `.../{id}/readiness`: actual domain state, phase, desired/observed revisions, engine image/options/model identity, allocation state, safe endpoint URL, last checks and reason codes. Ready requires checks matching this exact deployment and serving revision.
- POST `.../{id}/smoke`: replace the synchronous stand-in with a real bounded operation. Probe the actual private route with the declared modalities, assert served pins, then record a receipt. Exceptions/timeouts must end or reconcile into an actionable state; never remain indefinitely validating.
- POST/GET `/lab/v1/control/endpoints/{id}/keys`; DELETE `.../keys/{key_id}`: endpoint-scoped provider-dev credentials. Consumer keys cannot call private endpoints and dev keys cannot spend a consumer wallet on public endpoints.
- GET `/lab/v1/control/dev-wallet`; operator POST `/operator/v1/dev-wallet-grants` and `/operator/v1/deployments/{id}/dev-rate`: use existing private **CREDIT** accounting and approved cards. This is separate from provider judge/training spending in **PROVIDER_USD**.
- POST `/lab/v1/operations/{id}/cancel`; POST `/lab/v1/control/deployments/{id}/retire`: durable cancellation/drain/cleanup. Show existing operations on refresh; do not regenerate a deployment after a browser timeout.

An out-of-process controller holds privileged compute access. The API commits desired state and an outbox/operation; it does not shell out to arbitrary commands or wait for image pulls inside an HTTP request. Fence resource creation, reconcile uncertain allocations by stable provider resource tags, verify capacity again before admission, and clean up only resources owned by this operation. Capture artifact/image/driver/runtime/options identities in readiness evidence.

The first controller may manage a specifically allocated existing host/pool. A provider cannot name an arbitrary production container to adopt, restart or replace. Candidate deployment must be isolated from the serving Marlin instance. No spare capacity means `capacity_unavailable`, not silent eviction of consumer traffic. Freeze the tested configuration; optimizations create a new serving revision and require new checks.

## 6. Publication and discovery

Reuse provider POST `/lab/v1/control/proposals` for `kind=publish`; deprecate its unsupported rollback option explicitly. Operator APIs:

- GET `/operator/v1/publication-proposals[/{id}]` including exact candidate, readiness, benchmark policy, rate and consumer contract diffs.
- POST `/operator/v1/publication-proposals/{id}/approve` with expected listing version, approved rate version/decimal rates and reason; POST `.../reject` with reason. The existing Lab reject route can forward to the same domain during migration.
- POST `/operator/v1/listings/{model_id}/rollback` with immutable target version, expected current version and reason. Encode identifiers consistently; do not ambiguously split `provider/model` across path segments.

Approval atomically checks candidate/owner/state/readiness/limits/card and updates listing/route eligibility. Provision infrastructure **before** that transaction; never hold a database transaction over a GPU launch. Use an outbox/reconciler for cache/route propagation, and fail unavailable if a route cannot serve its pinned identity. GET `/v1/models` derives supported published listings from this registry, rather than returning any arbitrary row or keeping owner/profile constants as the only model selector.

Keep a measured approved profile per serving revision. No new model becomes public merely because it has a deployment record, a price, or a successful generic text response. A private candidate should not appear in consumer discovery, including via guessed aliases. Already admitted jobs keep their listing/serving/rate pins after promotion or rollback.

## 7. Consumer data permission and trace visualization

Capture, provider sharing, external judging, annotation/training and export are separate choices tied to the existing purpose model. Define a typed grant request identifying the authorized grantor-owned data/key/project scope, recipient provider, purposes, expiry and consent version. The backend resolves ownership; provider admin status cannot grant use of customer data. Do not conflate capture enabled with external-judge permission.

Each captured request carries consumer request ID, job/attempt IDs, serving/deployment/listing/rate/profile/harness pins, trace schema version, capture disposition and timing fields with documented measurement boundaries. Latency breakdown must label unavailable components, not fill them with zero. Preserve raw versus user-visible output identity where the existing pipeline requires it.

FastAPI trace reads return:

- Operational summary: request state, model revision, observed timing, token usage, settlement state, admitted rate/version. Provider aggregates never disclose customer identity without authority.
- Access state: `metadata`, `content`, `not_captured`, `expired`, `revoked`, `partial` (as applicable to the existing contract; map deliberately, do not overload one enum with unrelated dimensions).
- Content/evidence: authorized request/result/media references only after current grant/retention checks; bounded media access tokens/URLs and range reads where supported. Never expose internal object keys, credentials or permanent raw S3 links.
- Trace availability and backlog: server-confirmed delivery status/watermark; dropped/partial traces surfaced independently of inference success.

Lab screen order: request summary → execution timeline → input/video and response if authorized → model/harness/pricing pins → judge results → human review/feedback. Filters must run server-side and be reflected in scoped cursor identity. Clicking Run judge submits the exact selected trace set or a declared server-side sampling rule, not whichever rows happen to be on the page.

Grant revocation/expiry is rechecked before content retrieval, export and external egress, including queued tasks. Existing serving retention remains documented; revocation prevents future permitted use but cannot claim to retract a request already delivered to an external processor. Report physical deletion as unknown until measured.

## 8. Judge and human-review contracts

Under `/lab/v1/judge`, with provider context:

| Operation | Required semantics |
|---|---|
| GET `/models`, GET/POST `/rubrics` | Approved judge models/modalities/regions; immutable rubric/version; criterion evidence requirements and output schema |
| GET/POST `/configs`, GET `/configs/{id}` | Exact source scope, model/rubric pins, sampling seed/count, purpose and media policy; no secret configuration |
| GET `/budgets`, PUT `/budgets/{payer_id}` | Existing payer eligibility/admin authority; PROVIDER_USD decimal amount and budget version; concurrent reservations bounded |
| POST `/estimates` | Bounded eligible sample count and conservative spend estimate; not a reservation or authority to egress |
| POST `/runs` | Stable run identity, config, payer, expected budget version, max spend, dry_run/live; freeze eligible candidates and reserve before work |
| GET `/runs`, GET `/runs/{id}`, GET `/runs/{id}/results` | Lifecycle, selected/excluded/abstained counts, failures, per-criterion scores/evidence, latency, exact judge pins and reserved/settled cost |
| POST `/runs/{id}/cancel` | Stop future sends, reconcile reserved/in-flight/ambiguous work; do not retry an uncertain external submission blindly |
| GET `/calibration` | Current reviewed label set and agreement/confidence; `uncalibrated`/insufficient data preserved |

Online judge RPCs (`lab_judge_configure`, `lab_judge_set_budget`, `lab_judge_request_run`, `lab_judge_runs`, `lab_judge_calibration`) are implementation assets behind the new HTTP contract. Preserve their authority, run identity and money rules; add missing reads/estimates as owned work instead of rendering invented data. Judge worker composition must match these operations and stay dry-run when no live adapter is configured.

For SOP verification, version the SOP steps and rubric; require source-video evidence for visual criteria, return timestamps/citations when actually available, separate instruction following/output validity from task correctness, and report no-media/insufficient-evidence as abstention. Use a small human-reviewed gold set to measure judge agreement before showing a calibrated quality score. Teacher-generated labels and human ground truth are distinct provenance classes.

Provider judge/training costs never debit the consumer's inference CREDIT wallet. A repeated run request or killed worker cannot reserve/charge twice. Ambiguous provider submissions remain quarantined until reconciled. Rate/cost changes mid-run cannot silently expand the frozen budget.

Review routes produce immutable human review records linked to trace/dataset/rubric versions. Dataset construction preserves consent, media lineage, held-out evaluation splits and licensing. Existing dataset/evaluation/pipeline/release APIs remain the foundation; fix their runtime composition and accounting before enabling “Improve this model.”
